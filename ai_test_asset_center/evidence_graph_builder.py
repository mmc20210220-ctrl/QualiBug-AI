from __future__ import annotations

"""
Evidence Graph Builder — Structured Evidence Graphs from Verification Results

Builds directed acyclic evidence graphs that trace every verification step
from hypothesis through proof obligations, before/after snapshots, invariant
evaluation results, and final verdict.  Each graph is self-contained and
auditable, with edges that make the logical chain of reasoning explicit.

The to_ledger_entry() method produces a redacted, persistent-storage-safe
dict that strips sensitive data (tokens, passwords, authorization headers,
cookies) while preserving entity references, snapshot hashes, and timestamps
for human review traceability.

Design goals
------------
- Every node in the verification chain gets a stable node_id.
- Edges capture dependency / supports / refutes / constrains relationships.
- Redaction is deep-recursive and covers nested dicts, lists, headers, and cookies.
- Entity bindings, snapshot hashes, and timestamps are NEVER redacted — they are
  essential for traceability.
"""

import hashlib
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

VALID_NODE_TYPES: frozenset[str] = frozenset({
    "hypothesis",
    "proof_obligation",
    "entity_binding",
    "before_snapshot",
    "action",
    "after_snapshot",
    "invariant_result",
    "verdict",
    "safety_gate",
    "human_review_reference",
})

VALID_EDGE_TYPES: frozenset[str] = frozenset({
    "supports",
    "refutes",
    "derives_from",
    "depends_on",
    "constrains",
    "evaluates",
    "observes_before",
    "observes_after",
    "produces",
    "references",
    "gates",
})

# Keys whose values must be redacted in ledger entries.
# Matching is case-insensitive and partial (substring).
SENSITIVE_KEY_PATTERNS: tuple[str, ...] = (
    "token",
    "password",
    "passwd",
    "secret",
    "authorization",
    "cookie",
    "set-cookie",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "client_secret",
    "jwt",
    "credential",
    "private_key",
    "x-api-key",
)

# Header keys that must be redacted even if their name doesn't match SENSITIVE_KEY_PATTERNS.
SENSITIVE_HEADER_NAMES: frozenset[str] = frozenset({
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "proxy-authorization",
})

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class EvidenceNode:
    """A single node in the evidence graph.

    ``node_type`` must be one of the canonical types — hypothesis, proof_obligation,
    entity_binding, before_snapshot, action, after_snapshot, invariant_result,
    verdict, safety_gate, or human_review_reference.
    """

    node_id: str
    node_type: str  # one of VALID_NODE_TYPES
    label: str
    data: dict = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if self.node_type not in VALID_NODE_TYPES:
            raise ValueError(
                f"Invalid node_type: {self.node_type!r}. Must be one of {sorted(VALID_NODE_TYPES)}"
            )
        if not self.node_id:
            self.node_id = f"node_{uuid.uuid4().hex[:12]}"
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class EvidenceEdge:
    """A directed edge connecting two evidence nodes."""

    from_node: str   # node_id of the source node
    to_node: str     # node_id of the target node
    edge_type: str   # one of VALID_EDGE_TYPES

    def __post_init__(self) -> None:
        if self.edge_type not in VALID_EDGE_TYPES:
            raise ValueError(
                f"Invalid edge_type: {self.edge_type!r}. Must be one of {sorted(VALID_EDGE_TYPES)}"
            )


@dataclass
class EvidenceGraph:
    """A complete evidence graph representing a single verification run."""

    graph_id: str
    nodes: list[EvidenceNode] = field(default_factory=list)
    edges: list[EvidenceEdge] = field(default_factory=list)
    verdict: str = "UNDETERMINED"
    verdict_reason: str = ""
    generated_at: str = ""

    def __post_init__(self) -> None:
        if not self.graph_id:
            self.graph_id = f"evg_{uuid.uuid4().hex[:16]}"
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def add_node(
        self,
        node_type: str,
        label: str,
        data: dict | None = None,
        node_id: str = "",
        timestamp: str = "",
    ) -> EvidenceNode:
        """Create and append a node, returning it for edge-building."""
        node = EvidenceNode(
            node_id=node_id or f"node_{uuid.uuid4().hex[:12]}",
            node_type=node_type,
            label=label,
            data=data or {},
            timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
        )
        self.nodes.append(node)
        return node

    def add_edge(self, from_node: str, to_node: str, edge_type: str) -> EvidenceEdge:
        """Create and append an edge."""
        edge = EvidenceEdge(from_node=from_node, to_node=to_node, edge_type=edge_type)
        self.edges.append(edge)
        return edge


# ---------------------------------------------------------------------------
# EvidenceGraphBuilder
# ---------------------------------------------------------------------------


class EvidenceGraphBuilder:
    """Builds structured evidence graphs from verification pipeline results.

    The ``build_from_verification`` method accepts the full set of inputs from
    the verification pipeline (hypothesis, obligations, snapshots, results,
    verdict, and optional safety-gate result) and constructs a complete
    EvidenceGraph that traces every logical step.

    ``to_ledger_entry`` produces a redacted dict suitable for persistent storage
    and human review.
    """

    REDACTED_PLACEHOLDER: str = "[REDACTED]"

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def build_from_verification(
        self,
        hypothesis: dict,
        obligations: list[dict],
        before_snapshot: dict,
        after_snapshot: dict,
        results: list[dict],
        verdict: str,
        safety_gate_result: dict | None = None,
    ) -> EvidenceGraph:
        """Build a complete EvidenceGraph from verification pipeline inputs.

        Parameters
        ----------
        hypothesis : dict
            The hypothesis under test (title, description, entity_refs, etc.).
        obligations : list[dict]
            Proof obligations derived from the hypothesis.
        before_snapshot : dict
            Canonical state snapshot captured before the action.
        after_snapshot : dict
            Canonical state snapshot captured after the action.
        results : list[dict]
            Invariant evaluation results (one per obligation).
        verdict : str
            Overall verification verdict (PASSED / FAILED / UNDETERMINED).
        safety_gate_result : dict, optional
            Safety gate evaluation result, if one was performed.

        Returns
        -------
        EvidenceGraph
            The fully constructed evidence graph.
        """
        graph = EvidenceGraph(graph_id="")
        now = datetime.now(timezone.utc).isoformat()

        # ── 1. Hypothesis node ──────────────────────────────────────────
        hyp_title = hypothesis.get("title", hypothesis.get("description", "Unnamed Hypothesis"))
        hyp_node = graph.add_node(
            node_type="hypothesis",
            label=f"Hypothesis: {hyp_title}",
            data={
                "title": hypothesis.get("title", ""),
                "description": hypothesis.get("description", ""),
                "entity_refs": hypothesis.get("entity_refs", []),
                "severity": hypothesis.get("severity", "P1"),
            },
        )
        hyp_id = hyp_node.node_id

        # ── 2. Entity binding nodes ─────────────────────────────────────
        entity_binding_ids: list[str] = []
        for i, entity_ref in enumerate(hypothesis.get("entity_refs", [])):
            eb_node = graph.add_node(
                node_type="entity_binding",
                label=f"Entity: {entity_ref.get('alias', f'entity_{i}')}",
                data={
                    "entity_alias": entity_ref.get("alias", f"entity_{i}"),
                    "entity_type": entity_ref.get("type", "unknown"),
                    "entity_id": entity_ref.get("id", ""),
                    "binding_source": entity_ref.get("source", "flow_context"),
                },
            )
            entity_binding_ids.append(eb_node.node_id)
            graph.add_edge(hyp_id, eb_node.node_id, "constrains")

        # ── 3. Before snapshot node ─────────────────────────────────────
        before_snap_id = before_snapshot.get("snapshot_id", f"snap_before_{uuid.uuid4().hex[:8]}")
        before_node = graph.add_node(
            node_type="before_snapshot",
            label=f"Before: {before_snapshot.get('entity_alias', 'state')}",
            data={
                "snapshot_id": before_snap_id,
                "entity_alias": before_snapshot.get("entity_alias", "primary"),
                "entity_type": before_snapshot.get("entity_type", ""),
                "raw_payload_hash": before_snapshot.get("raw_payload_hash", ""),
                "observed_at": before_snapshot.get("observed_at", now),
                "entity_id": before_snapshot.get("entity_id", ""),
                "correlation_id": before_snapshot.get("correlation_id", ""),
                "raw_status_code": before_snapshot.get("raw_status_code", 0),
            },
        )

        # ── 4. Action node ──────────────────────────────────────────────
        action_node = graph.add_node(
            node_type="action",
            label=f"Action: {hypothesis.get('action', 'verify')}",
            data={
                "action": hypothesis.get("action", "verify"),
                "endpoint": hypothesis.get("endpoint", ""),
                "method": hypothesis.get("method", "GET"),
                "actor_role": hypothesis.get("actor_role", ""),
            },
        )

        # ── 5. After snapshot node ──────────────────────────────────────
        after_snap_id = after_snapshot.get("snapshot_id", f"snap_after_{uuid.uuid4().hex[:8]}")
        after_node = graph.add_node(
            node_type="after_snapshot",
            label=f"After: {after_snapshot.get('entity_alias', 'state')}",
            data={
                "snapshot_id": after_snap_id,
                "entity_alias": after_snapshot.get("entity_alias", "primary"),
                "entity_type": after_snapshot.get("entity_type", ""),
                "raw_payload_hash": after_snapshot.get("raw_payload_hash", ""),
                "observed_at": after_snapshot.get("observed_at", now),
                "entity_id": after_snapshot.get("entity_id", ""),
                "correlation_id": after_snapshot.get("correlation_id", ""),
                "raw_status_code": after_snapshot.get("raw_status_code", 0),
            },
        )

        # Chain: hypothesis → action → before_snapshot → action → after_snapshot
        graph.add_edge(hyp_id, action_node.node_id, "derives_from")
        graph.add_edge(action_node.node_id, before_node.node_id, "observes_before")
        graph.add_edge(action_node.node_id, after_node.node_id, "observes_after")

        # ── 6. Obligation nodes and result nodes ────────────────────────
        result_by_obl_id: dict[str, dict] = {
            r.get("obligation_id", ""): r for r in results
        }
        result_node_ids: list[str] = []
        for i, obl in enumerate(obligations):
            obl_id = obl.get("obligation_id", f"obl_{i}")
            obl_kind = obl.get("kind", "unknown")
            obl_title = obl.get("title", obl.get("description", f"Obligation {i+1}"))

            obl_node = graph.add_node(
                node_type="proof_obligation",
                label=f"Obligation: {obl_title}",
                data={
                    "obligation_id": obl_id,
                    "kind": obl_kind,
                    "severity": obl.get("severity", "P1"),
                    "description": obl.get("description", ""),
                    "fields": obl.get("fields", []),
                },
            )
            graph.add_edge(hyp_id, obl_node.node_id, "derives_from")
            for eb_id in entity_binding_ids:
                graph.add_edge(eb_id, obl_node.node_id, "constrains")
            graph.add_edge(before_node.node_id, obl_node.node_id, "depends_on")
            graph.add_edge(after_node.node_id, obl_node.node_id, "depends_on")

            # Result for this obligation (if present)
            result_data = result_by_obl_id.get(obl_id)
            if result_data:
                res_node = graph.add_node(
                    node_type="invariant_result",
                    label=f"Result: {result_data.get('verdict', 'UNDETERMINED')}",
                    data={
                        "obligation_id": obl_id,
                        "kind": result_data.get("kind", obl_kind),
                        "verdict": result_data.get("verdict", "UNDETERMINED"),
                        "passed": result_data.get("passed", False),
                        "detail": result_data.get("detail", ""),
                        "failed_fields": result_data.get("failed_fields", []),
                        "computed": result_data.get("computed", {}),
                    },
                )
                result_node_ids.append(res_node.node_id)
                graph.add_edge(obl_node.node_id, res_node.node_id, "evaluates")
                edge_kind = "supports" if result_data.get("passed") else "refutes"
                graph.add_edge(res_node.node_id, hyp_id, edge_kind)

        # ── 7. Verdict node ─────────────────────────────────────────────
        verdict_reason = self._derive_verdict_reason(results, verdict)
        verdict_node = graph.add_node(
            node_type="verdict",
            label=f"Verdict: {verdict}",
            data={
                "verdict": verdict,
                "reason": verdict_reason,
                "total_obligations": len(obligations),
                "total_results": len(results),
                "passed_count": sum(1 for r in results if r.get("passed")),
                "failed_count": sum(1 for r in results if not r.get("passed")),
            },
        )
        for rid in result_node_ids:
            graph.add_edge(rid, verdict_node.node_id, "produces")

        # ── 8. Safety gate node (optional) ───────────────────────────────
        if safety_gate_result is not None:
            sg_allowed = safety_gate_result.get("allowed", True)
            sg_label = "Safety Gate: ALLOWED" if sg_allowed else "Safety Gate: BLOCKED"
            sg_node = graph.add_node(
                node_type="safety_gate",
                label=sg_label,
                data={
                    "allowed": sg_allowed,
                    "reason": safety_gate_result.get("reason", ""),
                    "environment": safety_gate_result.get("environment", ""),
                    "checks": safety_gate_result.get("checks", []),
                    "checked_at": safety_gate_result.get("checked_at", now),
                },
            )
            graph.add_edge(sg_node.node_id, hyp_id, "gates")
            graph.add_edge(sg_node.node_id, verdict_node.node_id, "gates")

        # ── 9. Human review reference node ───────────────────────────────
        # Provides traceability for auditors: what to look at, timestamps,
        # and hashes without exposing raw payloads.
        graph.add_node(
            node_type="human_review_reference",
            label="Review Reference",
            data={
                "hypothesis_title": hypothesis.get("title", ""),
                "before_snapshot_id": before_snap_id,
                "after_snapshot_id": after_snap_id,
                "before_snapshot_hash": before_snapshot.get("raw_payload_hash", ""),
                "after_snapshot_hash": after_snapshot.get("raw_payload_hash", ""),
                "before_observed_at": before_snapshot.get("observed_at", ""),
                "after_observed_at": after_snapshot.get("observed_at", ""),
                "verdict": verdict,
                "verdict_reason": verdict_reason,
                "graph_id": graph.graph_id,
                "generated_at": graph.generated_at,
            },
        )

        # ── Finalize ────────────────────────────────────────────────────
        graph.verdict = verdict
        graph.verdict_reason = verdict_reason
        return graph

    # ------------------------------------------------------------------
    # Ledger entry (redacted, safe for persistent storage)
    # ------------------------------------------------------------------

    def to_ledger_entry(self, graph: EvidenceGraph) -> dict:
        """Produce a redacted dict safe for persistent storage.

        Sensitive values (tokens, passwords, authorization headers, cookies)
        are replaced with ``[REDACTED]``.  Entity references, snapshot hashes,
        and timestamps are preserved for human review traceability.

        Parameters
        ----------
        graph : EvidenceGraph
            The evidence graph to convert.

        Returns
        -------
        dict
            Redacted ledger entry.
        """
        nodes_redacted = []
        for node in graph.nodes:
            redacted_data = self._deep_redact(deepcopy(node.data))
            nodes_redacted.append({
                "node_id": node.node_id,
                "node_type": node.node_type,
                "label": node.label,
                "data": redacted_data,
                "timestamp": node.timestamp,
            })

        edges_serialised = [
            {
                "from_node": e.from_node,
                "to_node": e.to_node,
                "edge_type": e.edge_type,
            }
            for e in graph.edges
        ]

        return {
            "graph_id": graph.graph_id,
            "nodes": nodes_redacted,
            "edges": edges_serialised,
            "verdict": graph.verdict,
            "verdict_reason": graph.verdict_reason,
            "generated_at": graph.generated_at,
            "redacted": True,
        }

    # ------------------------------------------------------------------
    # Redaction helpers
    # ------------------------------------------------------------------

    @classmethod
    def _is_sensitive_key(cls, key: str) -> bool:
        """Check whether a dictionary key indicates sensitive data."""
        key_lower = key.lower().replace("_", "").replace("-", "")
        for pattern in SENSITIVE_KEY_PATTERNS:
            pattern_clean = pattern.lower().replace("_", "").replace("-", "")
            if pattern_clean in key_lower:
                return True
        return False

    @classmethod
    def _deep_redact(cls, obj: Any) -> Any:
        """Recursively redact sensitive values in dicts, lists, and strings.

        Preserves entity references (entity_alias, entity_type, entity_id,
        correlation_id, tenant_id), snapshot hashes (raw_payload_hash,
        snapshot_id), and timestamps (observed_at, generated_at, checked_at,
        compiled_at).
        """
        if isinstance(obj, dict):
            redacted: dict[str, Any] = {}
            for k, v in obj.items():
                if cls._is_sensitive_key(k) and isinstance(v, str) and v:
                    redacted[k] = cls.REDACTED_PLACEHOLDER
                else:
                    redacted[k] = cls._deep_redact(v)
            # Special handling: if this dict looks like HTTP headers (all keys
            # are typically header names), redact sensitive header values.
            redacted = cls._redact_headers_in_dict(redacted)
            return redacted
        if isinstance(obj, list):
            return [cls._deep_redact(item) for item in obj]
        return obj

    @classmethod
    def _redact_headers_in_dict(cls, d: dict[str, Any]) -> dict[str, Any]:
        """Redact sensitive HTTP header values within a dict.

        Headers with names in SENSITIVE_HEADER_NAMES are always redacted.
        Additionally, any key matching _is_sensitive_key gets redacted.
        """
        result: dict[str, Any] = {}
        for k, v in d.items():
            if k.lower() in SENSITIVE_HEADER_NAMES:
                result[k] = cls.REDACTED_PLACEHOLDER
            elif cls._is_sensitive_key(k) and isinstance(v, str) and v:
                result[k] = cls.REDACTED_PLACEHOLDER
            else:
                result[k] = v
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_verdict_reason(results: list[dict], overall_verdict: str) -> str:
        """Derive a human-readable reason string from the result set."""
        if not results:
            return "No invariant results were produced."

        passed = [r for r in results if r.get("passed")]
        failed = [r for r in results if not r.get("passed")]
        parts: list[str] = []

        if passed:
            parts.append(f"{len(passed)} obligation(s) PASSED")
        if failed:
            failed_details = []
            for r in failed:
                obl_id = r.get("obligation_id", "?")
                kind = r.get("kind", "?")
                detail = r.get("detail", "")
                failed_details.append(f"{obl_id} ({kind}): {detail}" if detail else f"{obl_id} ({kind})")
            parts.append(f"{len(failed)} obligation(s) FAILED: {'; '.join(failed_details)}")
        if not passed and not failed:
            parts.append("All results UNDETERMINED")

        reason = ". ".join(parts) + "."
        return reason
