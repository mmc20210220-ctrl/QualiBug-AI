"""
Phase77: Semantic State Verifier — Main Phase77 Verifier Kernel

Wires together all Phase77 modules to verify business hypotheses and multi-step
flows.  Compiles hypotheses into proof obligations, captures before/after state
snapshots, evaluates business invariants deterministically, builds evidence
graphs, and returns structured verdicts.

Design principles
-----------------
- Each non-CONFIRMED verdict MUST carry: reason_code, actionable_next_step,
  missing_capability, affected_entity, retryability.
- INCONCLUSIVE is a last-resort fallback only when no other verdict type applies.
- Zero LLM calls in the evaluation path — purely deterministic rule-based checks.
"""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ── Existing Phase77 modules ──────────────────────────────────────────────
from .state_observer_registry import (
    CanonicalStateSnapshot,
    StateObserver,
    snapshot_diff,
)
from .state_projection_engine import StateProjectionEngine

# Two ProofObligation flavours exist in the codebase:
#   1. proof_obligation_compiler.ProofObligation — the *compiled* form
#      (has hypothesis_ref, entity_alias, assertion_config, …)
#   2. business_invariant_evaluator.ProofObligation — the *evaluation* form
#      (has fields, allowed_transitions, expected_delta, expression, …)
# We alias both to avoid collisions.
from .proof_obligation_compiler import (
    ProofObligation as CompiledObligation,
    compile_from_hypothesis,
    compile_from_flow,
)
from .business_invariant_evaluator import (
    BusinessInvariantEvaluator,
    InvariantResult,
)
from .business_invariant_evaluator import (
    ProofObligation as EvalObligation,
)

# ── Structured verdict constants ──────────────────────────────────────────
VERDICT_CONFIRMED = "CONFIRMED"
VERDICT_FALSIFIED = "FALSIFIED"
VERDICT_EVIDENCE_CAPTURED = "EVIDENCE_CAPTURED"
VERDICT_OBSERVATION_PENDING = "OBSERVATION_PENDING"
VERDICT_ENTITY_BINDING_MISSING = "ENTITY_BINDING_MISSING"
VERDICT_INSUFFICIENT_INSTRUMENTATION = "INSUFFICIENT_INSTRUMENTATION"
VERDICT_ASYNC_WINDOW_PENDING = "ASYNC_WINDOW_PENDING"
VERDICT_ASYNC_TIMEOUT = "ASYNC_TIMEOUT"
VERDICT_SOURCE_CONFLICT = "SOURCE_CONFLICT"
VERDICT_BLOCKED_BY_SAFETY = "BLOCKED_BY_SAFETY"
VERDICT_BLOCKED_BY_FIXTURE = "BLOCKED_BY_FIXTURE"
VERDICT_CONFIGURATION_INVALID = "CONFIGURATION_INVALID"
VERDICT_EXECUTION_ERROR = "EXECUTION_ERROR"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"  # last resort only

_VERDICTS = {
    "CONFIRMED", "FALSIFIED", "EVIDENCE_CAPTURED", "OBSERVATION_PENDING",
    "ENTITY_BINDING_MISSING", "INSUFFICIENT_INSTRUMENTATION",
    "ASYNC_WINDOW_PENDING", "ASYNC_TIMEOUT", "SOURCE_CONFLICT",
    "BLOCKED_BY_SAFETY", "BLOCKED_BY_FIXTURE", "CONFIGURATION_INVALID",
    "EXECUTION_ERROR", "INCONCLUSIVE",
}


# ── Minimal Evidence Graph (evidence_graph_builder.py not yet in repo) ────

@dataclass
class EvidenceGraph:
    """Directed acyclic graph recording verification nodes and edges.

    Nodes represent snapshots, obligations, and results.
    Edges represent derivation / dependency relationships.
    """
    graph_id: str = ""
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.graph_id:
            self.graph_id = f"evg_{uuid.uuid4().hex[:12]}"


class EvidenceGraphBuilder:
    """Builds an EvidenceGraph from verification pipeline artifacts."""

    def __init__(self) -> None:
        self._nodes: list[dict] = []
        self._edges: list[dict] = []
        self._node_ids: set[str] = set()

    def add_obligation_node(self, obligation: CompiledObligation) -> str:
        nid = obligation.obligation_id
        if nid not in self._node_ids:
            self._node_ids.add(nid)
            self._nodes.append({
                "node_id": nid,
                "type": "obligation",
                "kind": obligation.kind,
                "entity_alias": obligation.entity_alias,
                "description": obligation.description,
                "severity": obligation.severity,
            })
        return nid

    def add_snapshot_node(self, snapshot: CanonicalStateSnapshot, role: str = "") -> str:
        nid = snapshot.snapshot_id
        if nid not in self._node_ids:
            self._node_ids.add(nid)
            self._nodes.append({
                "node_id": nid,
                "type": "snapshot",
                "role": role,
                "entity_id": snapshot.entity_id,
                "entity_type": snapshot.entity_type,
                "source": snapshot.source,
                "observed_at": snapshot.observed_at,
                "lifecycle_state": snapshot.projection.get("lifecycle_state"),
            })
        return nid

    def add_result_node(self, result: dict, obl_id: str) -> str:
        nid = f"result_{obl_id}"
        if nid not in self._node_ids:
            self._node_ids.add(nid)
            self._nodes.append({
                "node_id": nid,
                "type": "result",
                "obligation_id": obl_id,
                "verdict": result.get("verdict", ""),
                "passed": result.get("passed", False),
                "detail": result.get("detail", ""),
                "failed_fields": result.get("failed_fields", []),
            })
        return nid

    def add_edge(self, from_id: str, to_id: str, edge_type: str = "derives_from") -> None:
        self._edges.append({
            "from": from_id,
            "to": to_id,
            "type": edge_type,
        })

    def build(self, metadata: dict | None = None) -> EvidenceGraph:
        return EvidenceGraph(
            nodes=list(self._nodes),
            edges=list(self._edges),
            metadata=metadata or {},
        )


# ── ProofObligationCompiler (adapter over module-level functions) ─────────

class ProofObligationCompiler:
    """Wraps the module-level compile_* functions from proof_obligation_compiler.

    Provides a uniform class-based API for the verifier to call.
    """

    @staticmethod
    def compile_hypothesis(hypothesis: dict) -> list[CompiledObligation]:
        """Compile a hypothesis dict into ProofObligations."""
        return compile_from_hypothesis(hypothesis)

    @staticmethod
    def compile_flow(flow_config: dict) -> list[CompiledObligation]:
        """Compile a flow configuration into ProofObligations."""
        return compile_from_flow(flow_config)


# ── Helper: convert CompiledObligation → EvalObligation ───────────────────

def _to_eval_obligation(compiled: CompiledObligation) -> EvalObligation:
    """Convert a compiled obligation (from proof_obligation_compiler) into the
    evaluator-compatible ProofObligation (from business_invariant_evaluator)."""

    cfg = compiled.assertion_config or {}
    kind = compiled.kind

    # Map kind-specific fields from assertion_config
    allowed_transitions: dict[str, set[str]] = {}
    at_raw = cfg.get("allowed_transitions", {})
    if isinstance(at_raw, dict):
        allowed_transitions = {str(k): set(v) if isinstance(v, list) else {str(v)}
                               for k, v in at_raw.items()}

    return EvalObligation(
        obligation_id=compiled.obligation_id,
        kind=kind,
        title=cfg.get("hypothesis_title", compiled.description),
        severity=compiled.severity,
        fields=cfg.get("comparison_fields", cfg.get("tolerance_fields", [])),
        allowed_transitions=allowed_transitions,
        expected_delta=float(cfg.get("expected_delta", 0.0)),
        tolerance=float(cfg.get("tolerance", 1e-6)),
        expression=cfg.get("expression", cfg.get("conservation_expression", "")),
        cross_view_before_field=cfg.get("cross_view_before_field", ""),
        cross_view_after_field=cfg.get("cross_view_after_field", ""),
        eventually_timeout=float(
            cfg.get("max_poll_attempts", 15) * cfg.get("poll_interval_seconds", 2)
        ),
        eventually_poll_interval=float(cfg.get("poll_interval_seconds", 1.0)),
        eventually_field=cfg.get("delta_field", ""),
        eventually_predicate=cfg.get("expected_behavior", ""),
        extra={
            "assert_type": cfg.get("assert_type", ""),
            "hypothesis_ref": compiled.hypothesis_ref,
            "source_engine": compiled.source_engine,
        },
    )


# ── Helpers: entity binding ───────────────────────────────────────────────

def _bind_entity(
    hypothesis: dict,
    flow_context: dict | None,
    obligation: CompiledObligation,
) -> dict:
    """Attempt to extract an entity_id from available context.

    Returns {"bound": True, "entity_id": str, "entity_alias": str} on success,
    or {"bound": False, "reason": str} on failure.
    """
    # 1. Explicit entity_id in flow context
    if flow_context:
        eid = flow_context.get("entity_id", "")
        if eid:
            return {"bound": True, "entity_id": str(eid),
                    "entity_alias": obligation.entity_alias}

    # 2. Explicit entity_id in hypothesis
    eid = hypothesis.get("entity_id", "")
    if eid:
        return {"bound": True, "entity_id": str(eid),
                "entity_alias": obligation.entity_alias}

    # 3. Try entity field (might be a lookup key)
    entity_field = hypothesis.get("entity", "")
    if entity_field and isinstance(entity_field, (str, int)):
        return {"bound": True, "entity_id": str(entity_field),
                "entity_alias": obligation.entity_alias}

    # 4. Check if the hypothesis supplies a source_entity ID
    for key in ("source_entity_id", "target_entity_id"):
        val = hypothesis.get(key, "")
        if val:
            return {"bound": True, "entity_id": str(val),
                    "entity_alias": obligation.entity_alias}

    return {
        "bound": False,
        "reason": (
            f"No entity_id found in hypothesis ({list(hypothesis.keys())}) "
            f"or flow_context ({list(flow_context.keys()) if flow_context else 'None'})"
        ),
        "entity_alias": obligation.entity_alias,
    }


# ── Helpers: build a structured verdict ───────────────────────────────────

def _make_verdict_response(
    verdict: str,
    verdict_reason: str,
    evidence_graph: EvidenceGraph | None = None,
    obligations: list[dict] | None = None,
    results: list[dict] | None = None,
    actionable_next_step: str = "",
    missing_capability: str = "",
    affected_entity: str = "",
    retryable: bool = False,
    extra: dict | None = None,
) -> dict:
    """Build the standard verifier response dict."""
    resp: dict[str, Any] = {
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "evidence_graph": evidence_graph,
        "obligations": obligations or [],
        "results": results or [],
        "actionable_next_step": actionable_next_step,
        "missing_capability": missing_capability,
        "affected_entity": affected_entity,
        "retryability": retryable,
    }
    if extra:
        resp.update(extra)
    return resp


# ═══════════════════════════════════════════════════════════════════════════
#  SemanticStateVerifier
# ═══════════════════════════════════════════════════════════════════════════

class SemanticStateVerifier:
    """Main Phase77 Verifier kernel.

    Wires together:
      - ProofObligationCompiler  (hypothesis → obligations)
      - StateObserver            (before/after snapshots)
      - BusinessInvariantEvaluator (invariant evaluation)
      - EvidenceGraphBuilder     (evidence graph construction)

    Usage::

        verifier = SemanticStateVerifier(project_id="mes_pilot")
        result = verifier.verify_hypothesis(
            hypothesis={"title": "Reject must not mutate state", ...},
            http_client=my_client,
            flow_context={"entity_id": "PO-12345"},
        )
        print(result["verdict"])  # CONFIRMED | FALSIFIED | …
    """

    def __init__(
        self,
        project_id: str = "",
        base_url: str = "",
        redact_sensitive: bool = True,
    ) -> None:
        self.project_id = project_id
        self.base_url = base_url
        self.redact_sensitive = redact_sensitive

        # Subsystem instances
        self._observer = StateObserver(redact_sensitive=redact_sensitive)
        self._compiler = ProofObligationCompiler()
        self._evaluator = BusinessInvariantEvaluator()
        self._projection_engine = StateProjectionEngine()

    # ── Public API ─────────────────────────────────────────────────────────

    def verify_hypothesis(
        self,
        hypothesis: dict,
        http_client: Any = None,
        flow_context: dict | None = None,
    ) -> dict:
        """Full pipeline: hypothesis → obligations → before→action→after →
        evaluate → evidence → verdict.

        Parameters
        ----------
        hypothesis : dict
            Must contain at least ``title``.  Optional: severity, entity,
            expected_behavior, verification_method, entity_id, etc.
        http_client : optional
            Any object with a ``request(method, url, **kwargs)`` signature
            that returns a ``(status_code, response_body)`` tuple.
            If None, actions cannot be executed and OBSERVATION_PENDING is
            returned.
        flow_context : dict | None
            Flow-level context with entity_id, correlation_id, tenant_id, etc.

        Returns
        -------
        dict
            Standard verifier response with keys: verdict, verdict_reason,
            evidence_graph, obligations, results, actionable_next_step,
            missing_capability, affected_entity, retryability.
        """
        ctx = flow_context or {}

        # 1. Compile hypothesis → obligations
        try:
            compiled = self._compiler.compile_hypothesis(hypothesis)
        except Exception as exc:
            return _make_verdict_response(
                verdict=VERDICT_EXECUTION_ERROR,
                verdict_reason=f"Compilation error: {exc}",
                actionable_next_step="Check hypothesis structure; ensure 'title' field is present.",
                missing_capability="hypothesis_compilation",
                retryable=True,
            )

        if not compiled:
            return _make_verdict_response(
                verdict=VERDICT_CONFIGURATION_INVALID,
                verdict_reason="Hypothesis compiled to zero obligations (empty or malformed).",
                actionable_next_step="Add a 'title' field to the hypothesis and re-submit.",
                missing_capability="hypothesis_structure",
                retryable=False,
            )

        # 2. For each obligation: bind entity, capture snapshots, execute, evaluate
        graph_builder = EvidenceGraphBuilder()
        obligation_dicts: list[dict] = []
        result_dicts: list[dict] = []
        all_passed = True
        any_failed = False
        first_non_confirmed: dict | None = None

        for obl in compiled:
            graph_builder.add_obligation_node(obl)
            obl_dict = {
                "obligation_id": obl.obligation_id,
                "kind": obl.kind,
                "entity_alias": obl.entity_alias,
                "description": obl.description,
                "severity": obl.severity,
                "status": obl.status,
            }

            # 2a. Bind entity
            binding = _bind_entity(hypothesis, ctx, obl)
            if not binding["bound"]:
                obl_dict["status"] = "ENTITY_BINDING_FAILED"
                obligation_dicts.append(obl_dict)
                all_passed = False
                first_non_confirmed = first_non_confirmed or _make_verdict_response(
                    verdict=VERDICT_ENTITY_BINDING_MISSING,
                    verdict_reason=binding["reason"],
                    actionable_next_step=(
                        "Supply entity_id in hypothesis or flow_context, "
                        "or ensure the hypothesis includes an 'entity' field."
                    ),
                    missing_capability="entity_resolution",
                    affected_entity=binding.get("entity_alias", "unknown"),
                    retryable=True,
                )
                continue

            entity_id = binding["entity_id"]
            entity_alias = binding["entity_alias"]

            # 2b. Capture before snapshot
            before_snap = self._capture_snapshot(
                http_client, ctx, entity_id, entity_alias, role="before"
            )
            if before_snap is None:
                obl_dict["status"] = "BEFORE_SNAPSHOT_FAILED"
                obligation_dicts.append(obl_dict)
                all_passed = False
                first_non_confirmed = first_non_confirmed or _make_verdict_response(
                    verdict=VERDICT_OBSERVATION_PENDING,
                    verdict_reason=(
                        "Cannot capture before-snapshot: no http_client provided "
                        "and no fixture/flow_context data available."
                    ),
                    actionable_next_step=(
                        "Provide an http_client for live API observation, "
                        "or pre-populate flow_context with fixture data."
                    ),
                    missing_capability="state_observation",
                    affected_entity=entity_alias,
                    retryable=True,
                )
                continue

            graph_builder.add_snapshot_node(before_snap, role="before")

            # 2c. Execute action (if possible)
            action_result = self._execute_action(
                http_client, hypothesis, ctx, entity_id
            )

            # 2d. Capture after snapshot
            after_snap = self._capture_snapshot(
                http_client, ctx, entity_id, entity_alias, role="after",
                response_body=action_result.get("response_body"),
                status_code=action_result.get("status_code", 0),
            )
            if after_snap is None:
                obl_dict["status"] = "AFTER_SNAPSHOT_FAILED"
                obligation_dicts.append(obl_dict)
                all_passed = False
                first_non_confirmed = first_non_confirmed or _make_verdict_response(
                    verdict=VERDICT_OBSERVATION_PENDING,
                    verdict_reason="Cannot capture after-snapshot (no API response or fixture).",
                    actionable_next_step="Provide http_client or fixture data for post-action observation.",
                    missing_capability="state_observation",
                    affected_entity=entity_alias,
                    retryable=True,
                )
                continue

            graph_builder.add_snapshot_node(after_snap, role="after")

            # 2e. Evaluate invariant
            eval_obl = _to_eval_obligation(obl)
            inv_result: InvariantResult = self._evaluator.evaluate(
                eval_obl, before_snap, after_snap,
            )

            # 2f. Record result
            result_dict = {
                "obligation_id": obl.obligation_id,
                "kind": obl.kind,
                "verdict": inv_result.verdict,
                "passed": inv_result.passed,
                "detail": inv_result.detail,
                "failed_fields": inv_result.failed_fields,
                "computed": inv_result.computed,
                "before_snapshot_id": before_snap.snapshot_id,
                "after_snapshot_id": after_snap.snapshot_id,
            }
            result_dicts.append(result_dict)

            graph_builder.add_result_node(result_dict, obl.obligation_id)
            graph_builder.add_edge(obl.obligation_id, before_snap.snapshot_id, "observed_before")
            graph_builder.add_edge(obl.obligation_id, after_snap.snapshot_id, "observed_after")
            graph_builder.add_edge(
                f"result_{obl.obligation_id}", obl.obligation_id, "evaluates"
            )

            if not inv_result.passed:
                all_passed = False
                any_failed = True

            obl_dict["status"] = inv_result.verdict
            obligation_dicts.append(obl_dict)

        # 3. Build evidence graph
        evidence_graph = graph_builder.build(metadata={
            "project_id": self.project_id,
            "hypothesis_title": hypothesis.get("title", ""),
            "compiled_at": datetime.now(timezone.utc).isoformat(),
        })

        # 4. Return structured verdict
        if first_non_confirmed:
            resp = first_non_confirmed
            resp["evidence_graph"] = evidence_graph
            resp["obligations"] = obligation_dicts
            resp["results"] = result_dicts
            return resp

        if all_passed and result_dicts:
            return _make_verdict_response(
                verdict=VERDICT_CONFIRMED,
                verdict_reason=f"All {len(result_dicts)} obligation(s) passed.",
                evidence_graph=evidence_graph,
                obligations=obligation_dicts,
                results=result_dicts,
                actionable_next_step="",
                missing_capability="",
                retryable=False,
            )

        if any_failed:
            failed_count = sum(1 for r in result_dicts if not r["passed"])
            return _make_verdict_response(
                verdict=VERDICT_FALSIFIED,
                verdict_reason=f"{failed_count}/{len(result_dicts)} obligation(s) failed.",
                evidence_graph=evidence_graph,
                obligations=obligation_dicts,
                results=result_dicts,
                actionable_next_step="Review failed obligations and inspect failed_fields for root cause.",
                missing_capability="",
                retryable=True,
            )

        # Fallback: no results, no obligations
        return _make_verdict_response(
            verdict=VERDICT_INCONCLUSIVE,
            verdict_reason="No obligations were evaluated. Pipeline produced zero results.",
            evidence_graph=evidence_graph,
            obligations=obligation_dicts,
            results=result_dicts,
            actionable_next_step="Review hypothesis structure and re-submit.",
            missing_capability="hypothesis_actionability",
            retryable=True,
        )

    def verify_flow(
        self,
        flow_config: dict,
        http_client: Any = None,
    ) -> dict:
        """Verify a multi-step business flow.

        Parameters
        ----------
        flow_config : dict
            Flow definition with flow_name, entity, steps[], and optional
            invariants.  See proof_obligation_compiler docstring for shape.
        http_client : optional
            HTTP client for live API observation (same contract as
            verify_hypothesis).

        Returns
        -------
        dict
            Standard verifier response (same shape as verify_hypothesis).
        """
        # 1. Compile flow → obligations
        try:
            compiled = self._compiler.compile_flow(flow_config)
        except Exception as exc:
            return _make_verdict_response(
                verdict=VERDICT_EXECUTION_ERROR,
                verdict_reason=f"Flow compilation error: {exc}",
                actionable_next_step="Check flow_config structure.",
                missing_capability="flow_compilation",
                retryable=True,
            )

        if not compiled:
            return _make_verdict_response(
                verdict=VERDICT_CONFIGURATION_INVALID,
                verdict_reason="Flow compiled to zero obligations (empty steps or malformed).",
                actionable_next_step=(
                    "Add at least one step with an 'action' field to flow_config."
                ),
                missing_capability="flow_definition",
                retryable=False,
            )

        # 2. Build a synthetic hypothesis from the flow for entity binding
        entity = flow_config.get("entity", flow_config.get("entity_alias", "primary"))
        synthetic_hypothesis = {
            "title": flow_config.get("flow_name", "flow"),
            "entity": entity,
            "severity": flow_config.get("severity", "P1"),
        }

        # Reuse the shared flow_context from flow_config
        flow_context: dict = flow_config.get("context", flow_config.get("flow_context", {}))
        if entity and "entity" not in (flow_context or {}):
            flow_context = (flow_context or {}) | {"entity_alias": entity}

        # 3. Evaluate each obligation in sequence (flows are ordered)
        graph_builder = EvidenceGraphBuilder()
        obligation_dicts: list[dict] = []
        result_dicts: list[dict] = []
        all_passed = True
        any_failed = False
        first_non_confirmed: dict | None = None

        for obl in compiled:
            graph_builder.add_obligation_node(obl)
            obl_dict = {
                "obligation_id": obl.obligation_id,
                "kind": obl.kind,
                "entity_alias": obl.entity_alias,
                "description": obl.description,
                "severity": obl.severity,
                "status": obl.status,
            }

            # Bind entity (use flow entity and any step-specific context)
            binding = _bind_entity(synthetic_hypothesis, flow_context, obl)
            if not binding["bound"]:
                obl_dict["status"] = "ENTITY_BINDING_FAILED"
                obligation_dicts.append(obl_dict)
                all_passed = False
                first_non_confirmed = first_non_confirmed or _make_verdict_response(
                    verdict=VERDICT_ENTITY_BINDING_MISSING,
                    verdict_reason=binding["reason"],
                    actionable_next_step="Supply entity_id in flow_config or flow_context.",
                    missing_capability="entity_resolution",
                    affected_entity=binding.get("entity_alias", "unknown"),
                    retryable=True,
                )
                continue

            entity_id = binding["entity_id"]
            entity_alias = binding["entity_alias"]

            # Capture before snapshot
            before_snap = self._capture_snapshot(
                http_client, flow_context, entity_id, entity_alias, role="before"
            )
            if before_snap is None:
                obl_dict["status"] = "BEFORE_SNAPSHOT_FAILED"
                obligation_dicts.append(obl_dict)
                all_passed = False
                first_non_confirmed = first_non_confirmed or _make_verdict_response(
                    verdict=VERDICT_OBSERVATION_PENDING,
                    verdict_reason="Cannot capture before-snapshot for flow step.",
                    actionable_next_step="Provide http_client or fixture data.",
                    missing_capability="state_observation",
                    affected_entity=entity_alias,
                    retryable=True,
                )
                continue

            graph_builder.add_snapshot_node(before_snap, role="before")

            # Execute flow step action
            step_action = obl.assertion_config.get("action", "")
            action_result = self._execute_action(
                http_client, synthetic_hypothesis, flow_context, entity_id,
                step_action=step_action,
            )

            # Capture after snapshot
            after_snap = self._capture_snapshot(
                http_client, flow_context, entity_id, entity_alias, role="after",
                response_body=action_result.get("response_body"),
                status_code=action_result.get("status_code", 0),
            )
            if after_snap is None:
                obl_dict["status"] = "AFTER_SNAPSHOT_FAILED"
                obligation_dicts.append(obl_dict)
                all_passed = False
                first_non_confirmed = first_non_confirmed or _make_verdict_response(
                    verdict=VERDICT_OBSERVATION_PENDING,
                    verdict_reason="Cannot capture after-snapshot for flow step.",
                    actionable_next_step="Provide http_client or fixture data.",
                    missing_capability="state_observation",
                    affected_entity=entity_alias,
                    retryable=True,
                )
                continue

            graph_builder.add_snapshot_node(after_snap, role="after")

            # Evaluate invariant
            eval_obl = _to_eval_obligation(obl)
            inv_result = self._evaluator.evaluate(eval_obl, before_snap, after_snap)

            result_dict = {
                "obligation_id": obl.obligation_id,
                "kind": obl.kind,
                "verdict": inv_result.verdict,
                "passed": inv_result.passed,
                "detail": inv_result.detail,
                "failed_fields": inv_result.failed_fields,
                "computed": inv_result.computed,
                "before_snapshot_id": before_snap.snapshot_id,
                "after_snapshot_id": after_snap.snapshot_id,
                "step_index": obl.assertion_config.get("step_index"),
                "step_action": step_action,
            }
            result_dicts.append(result_dict)

            graph_builder.add_result_node(result_dict, obl.obligation_id)
            graph_builder.add_edge(obl.obligation_id, before_snap.snapshot_id, "observed_before")
            graph_builder.add_edge(obl.obligation_id, after_snap.snapshot_id, "observed_after")
            graph_builder.add_edge(
                f"result_{obl.obligation_id}", obl.obligation_id, "evaluates"
            )

            if not inv_result.passed:
                all_passed = False
                any_failed = True

            obl_dict["status"] = inv_result.verdict
            obligation_dicts.append(obl_dict)

        evidence_graph = graph_builder.build(metadata={
            "project_id": self.project_id,
            "flow_name": flow_config.get("flow_name", ""),
            "compiled_at": datetime.now(timezone.utc).isoformat(),
        })

        if first_non_confirmed:
            resp = first_non_confirmed
            resp["evidence_graph"] = evidence_graph
            resp["obligations"] = obligation_dicts
            resp["results"] = result_dicts
            return resp

        if all_passed and result_dicts:
            return _make_verdict_response(
                verdict=VERDICT_CONFIRMED,
                verdict_reason=f"Flow verified: all {len(result_dicts)} step(s) passed.",
                evidence_graph=evidence_graph,
                obligations=obligation_dicts,
                results=result_dicts,
                actionable_next_step="",
                missing_capability="",
                retryable=False,
            )

        if any_failed:
            failed_count = sum(1 for r in result_dicts if not r["passed"])
            return _make_verdict_response(
                verdict=VERDICT_FALSIFIED,
                verdict_reason=f"Flow verification failed: {failed_count}/{len(result_dicts)} step(s) failed.",
                evidence_graph=evidence_graph,
                obligations=obligation_dicts,
                results=result_dicts,
                actionable_next_step="Review failed flow steps and inspect failed_fields.",
                missing_capability="",
                retryable=True,
            )

        return _make_verdict_response(
            verdict=VERDICT_INCONCLUSIVE,
            verdict_reason="Flow verification produced zero results.",
            evidence_graph=evidence_graph,
            obligations=obligation_dicts,
            results=result_dicts,
            actionable_next_step="Review flow_config and re-submit.",
            missing_capability="flow_actionability",
            retryable=True,
        )

    # ── Internal helpers ───────────────────────────────────────────────────

    def _capture_snapshot(
        self,
        http_client: Any,
        context: dict | None,
        entity_id: str,
        entity_alias: str,
        role: str = "before",
        response_body: dict | None = None,
        status_code: int = 0,
    ) -> CanonicalStateSnapshot | None:
        """Capture a CanonicalStateSnapshot from the best available source."""

        ctx = context or {}

        # 1. If we have a direct response_body from an action execution
        if response_body is not None:
            endpoint = ctx.get("endpoint", ctx.get(f"{role}_endpoint", self.base_url))
            return self._observer.observe_from_http(
                response_body=response_body,
                status_code=status_code or 200,
                endpoint=str(endpoint),
                observer_id=f"verifier_{role}",
                entity_alias=entity_alias,
                entity_id=entity_id,
                correlation_id=str(ctx.get("correlation_id", "")),
                tenant_id=str(ctx.get("tenant_id", "")),
                version_hint=str(ctx.get("version", "")),
            )

        # 2. If http_client is available, make a GET call (read-only observe)
        if http_client is not None:
            endpoint = ctx.get("observe_endpoint", ctx.get("endpoint", ""))
            if endpoint:
                try:
                    status, body = http_client.request("GET", endpoint)
                    return self._observer.observe_from_http(
                        response_body=body if isinstance(body, dict) else {},
                        status_code=status,
                        endpoint=endpoint,
                        observer_id=f"verifier_{role}",
                        entity_alias=entity_alias,
                        entity_id=entity_id,
                        correlation_id=str(ctx.get("correlation_id", "")),
                        tenant_id=str(ctx.get("tenant_id", "")),
                        version_hint=str(ctx.get("version", "")),
                    )
                except Exception:
                    pass  # fall through to fixture

        # 3. Fall back to fixture data from context
        fixture = ctx.get("fixture", ctx.get("fixture_data", {}))
        if fixture:
            return self._observer.observe_from_fixture(
                fixture_data=fixture,
                entity_alias=entity_alias,
                fixture_name=ctx.get("fixture_name", f"{role}_fixture"),
            )

        # 4. Fall back to flow_context as a static snapshot
        if ctx:
            snap = self._observer.observe_from_flow_context(
                flow_context=ctx,
                entity_alias=entity_alias,
            )
            # Override entity_id if we have a stronger binding
            if entity_id:
                snap.entity_id = entity_id
            return snap

        return None

    def _execute_action(
        self,
        http_client: Any,
        hypothesis: dict,
        context: dict | None,
        entity_id: str,
        step_action: str = "",
    ) -> dict:
        """Execute the verification action via http_client, returning the result.

        Returns dict with keys: status_code, response_body, executed.
        """
        ctx = context or {}

        if http_client is None:
            return {"status_code": 0, "response_body": None, "executed": False}

        # Determine the HTTP method and endpoint
        method = hypothesis.get("method", ctx.get("method", "GET")).upper()
        action = step_action or hypothesis.get("action", "")

        # Action-to-method mapping
        action_method_map = {
            "create": "POST", "release": "POST", "approve": "POST",
            "reject": "POST", "cancel": "POST", "update": "PUT",
            "delete": "DELETE", "patch": "PATCH", "submit": "POST",
            "complete": "POST", "start": "POST", "pause": "POST",
        }
        if action.lower() in action_method_map and method == "GET":
            method = action_method_map[action.lower()]

        # Build URL
        endpoint = hypothesis.get("endpoint", ctx.get("action_endpoint", ctx.get("endpoint", "")))
        if not endpoint:
            return {"status_code": 0, "response_body": None, "executed": False,
                    "error": "No endpoint configured"}

        # Substitute entity_id in URL template
        url = str(endpoint).replace("{entity_id}", entity_id).replace("{id}", entity_id)

        # Build payload
        payload = hypothesis.get("payload", ctx.get("payload", {}))
        if action and not payload:
            payload = {"action": action}

        try:
            if method in ("POST", "PUT", "PATCH"):
                status, body = http_client.request(method, url, json=payload)
            else:
                status, body = http_client.request(method, url)
            return {
                "status_code": status,
                "response_body": body if isinstance(body, dict) else {},
                "executed": True,
            }
        except Exception as exc:
            return {
                "status_code": 0,
                "response_body": None,
                "executed": False,
                "error": str(exc),
            }
