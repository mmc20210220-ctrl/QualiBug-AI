from __future__ import annotations

"""
Proof Obligation Compiler — Hypothesis → ProofObligation

Compiles hypotheses and flow configurations into structured ProofObligations
that the verification pipeline can execute methodically.  Uses keyword-driven
inference from hypothesis titles / expected behaviors to classify obligations
into one of eight canonical kinds, each with a distinct assertion strategy.
"""

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ProofObligation:
    """A single testable obligation derived from a hypothesis or flow step."""

    obligation_id: str
    kind: str  # see KIND_KEYWORDS below
    hypothesis_ref: str
    severity: str
    entity_alias: str
    description: str
    required_observers: list[str] = field(default_factory=list)
    assertion_config: dict = field(default_factory=dict)
    status: str = "PENDING"

    # Optional provenance
    compiled_at: str = ""
    source_engine: str = ""

    def __post_init__(self):
        if not self.compiled_at:
            self.compiled_at = datetime.now(timezone.utc).isoformat()
        if not self.obligation_id:
            self.obligation_id = f"obl_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Keyword → kind mapping (Chinese + English)
# ---------------------------------------------------------------------------

KIND_KEYWORDS: list[tuple[str, str, str]] = [
    # (keyword_or_regex, kind, label)
    # Order matters: earlier matches win (more specific first).
    ("reject|拒绝|驳回|拒收|rejected|rejection",  "state_unchanged_after_rejection",
     "Rejection must leave entity state unchanged"),
    ("idempotent|幂等|idempotency|重放|replay",   "idempotency_replay",
     "Repeat of same operation must produce identical outcome"),
    ("conservation|守恒|不变|invariant|总量不变|原子", "conservation",
     "Aggregate quantities must be conserved across operations"),
    ("lifecycle|生命周期|状态流转|状态迁移|状态机|transition", "lifecycle_transition",
     "Entity must follow the defined lifecycle state machine"),
    ("cross[-_]?view|不一致|数据不一致|cross[-_]?model|视图|reconciliation", "cross_view_equal",
     "Multiple views of the same entity must agree"),
    ("eventual|异步|最终一致性|eventually|delayed|延迟", "eventually",
     "A property must hold after a bounded delay"),
    ("unauthorized|越权|未授权|权限|authz|authorization|illegal access|role[-_]?based|伪造", "authorization_non_mutation",
     "Unauthorized operations must not mutate system state"),
    ("delta|增量|差值|变化量|amount|quantity|numeric|数值|差额", "numeric_delta",
     "State change must equal a calculated delta"),
]

# Severity → fallback assertion defaults
SEVERITY_DEFAULTS: dict[str, dict] = {
    "P0": {"max_retries": 3, "strict": True,  "block_release": True},
    "P1": {"max_retries": 3, "strict": True,  "block_release": True},
    "P2": {"max_retries": 2, "strict": False, "block_release": False},
    "P3": {"max_retries": 1, "strict": False, "block_release": False},
}


# ---------------------------------------------------------------------------
# Kind inference
# ---------------------------------------------------------------------------

def _infer_kind(title: str = "", expected: str = "") -> str:
    """Return the canonical kind string for a hypothesis based on its title/expected."""
    text = f"{title} {expected}".lower()
    for pattern, kind, _label in KIND_KEYWORDS:
        if re.search(pattern, text):
            return kind
    # Fallback to generic lifecycle_transition (most common default)
    return "lifecycle_transition"


def _infer_entity(hypothesis: dict) -> str:
    """Extract the best entity alias from a hypothesis dict."""
    for key in ("entity", "source_entity", "target_entity", "entity_alias"):
        val = hypothesis.get(key, "")
        if val:
            return str(val)
    # Fallback: guess from title
    title = hypothesis.get("title", "")
    # Common entity patterns in MES domain
    for cand in ("生产订单", "production order", "工单", "work order", "质检",
                 "BOM", "物料", "material", "库存", "inventory",
                 "报工", "work report", "设备", "equipment", "追溯", "traceability"):
        if cand.lower() in title.lower():
            return cand
    return "primary"


def _generate_description(hypothesis: dict, kind: str) -> str:
    """Build a human- and machine-readable obligation description."""
    title = hypothesis.get("title", "Untitled Hypothesis")
    expected = hypothesis.get("expected_behavior", hypothesis.get("expected", ""))
    desc = hypothesis.get("description", "")
    parts = [title]
    if expected:
        parts.append(f"[Expected] {expected}")
    if desc and desc != title:
        parts.append(desc)
    parts.append(f"[Kind] {kind}")
    return " | ".join(parts)


def _build_assertion_config(hypothesis: dict, kind: str) -> dict:
    """Construct the assertion_config payload for a ProofObligation."""
    severity = hypothesis.get("severity", "P1")
    base = dict(SEVERITY_DEFAULTS.get(severity, SEVERITY_DEFAULTS["P2"]))
    base["kind"] = kind
    base["verification_method"] = hypothesis.get("verification_method", {})
    base["expected_behavior"] = hypothesis.get("expected_behavior", "")
    base["hypothesis_title"] = hypothesis.get("title", "")

    # Kind-specific overrides
    if kind == "state_unchanged_after_rejection":
        base["assert_type"] = "snapshot_before_after_reject"
        base["tolerance_fields"] = ["updated_at", "timestamp"]
    elif kind == "idempotency_replay":
        base["assert_type"] = "identical_response"
        base["replay_count"] = 2
    elif kind == "conservation":
        base["assert_type"] = "sum_aggregate"
        base["tolerance"] = 0.001
    elif kind == "lifecycle_transition":
        base["assert_type"] = "state_machine"
        base["allowed_transitions"] = []
        base["forbidden_transitions"] = []
    elif kind == "cross_view_equal":
        base["assert_type"] = "semantic_diff_zero"
        base["comparison_fields"] = []
    elif kind == "eventually":
        base["assert_type"] = "polling_condition"
        base["poll_interval_seconds"] = 2
        base["max_poll_attempts"] = 15
    elif kind == "authorization_non_mutation":
        base["assert_type"] = "mutation_detector"
        base["compare_roles"] = ["admin", "viewer"]
    elif kind == "numeric_delta":
        base["assert_type"] = "delta_equation"
        base["delta_field"] = ""
        base["expected_delta"] = 0.0

    return base


def _required_observers(kind: str, hypothesis: dict) -> list[str]:
    """Determine which observer types are needed for this obligation."""
    entity = _infer_entity(hypothesis)
    observers: list[str] = []

    if kind == "state_unchanged_after_rejection":
        observers = [f"http_snapshot_{entity}", f"http_snapshot_{entity}_post_reject"]
    elif kind == "idempotency_replay":
        observers = [f"http_snapshot_{entity}_call1", f"http_snapshot_{entity}_call2"]
    elif kind == "conservation":
        observers = [f"http_snapshot_{entity}_before", f"http_snapshot_{entity}_after"]
    elif kind == "lifecycle_transition":
        observers = [f"http_snapshot_{entity}_initial", f"http_snapshot_{entity}_post_transition"]
    elif kind == "cross_view_equal":
        observers = [f"http_snapshot_view1_{entity}", f"http_snapshot_view2_{entity}"]
    elif kind == "eventually":
        observers = [f"http_snapshot_{entity}_poll_window"]
    elif kind == "authorization_non_mutation":
        observers = [f"http_snapshot_{entity}_auth", f"http_snapshot_{entity}_unauth"]
    elif kind == "numeric_delta":
        observers = [f"http_snapshot_{entity}_before", f"http_snapshot_{entity}_after"]
    else:
        observers = [f"http_snapshot_{entity}"]

    return observers


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compile_from_hypothesis(hypothesis: dict) -> list[ProofObligation]:
    """Compile a single hypothesis dict into one or more ProofObligations.

    Parameters
    ----------
    hypothesis : dict
        Must contain at least ``title``.  Typical fields from the Reasoner:
        hypothesis_id, title, severity, expected_behavior, description,
        entity, verification_method, source_entity.

    Returns
    -------
    list[ProofObligation]
        One obligation per inferred path (usually 1, but may be >1 for
        composite hypotheses).
    """
    if not hypothesis or not isinstance(hypothesis, dict):
        return []

    title = hypothesis.get("title", "")
    expected = hypothesis.get("expected_behavior", hypothesis.get("expected", ""))
    kind = _infer_kind(title, expected)
    entity = _infer_entity(hypothesis)
    severity = hypothesis.get("severity", "P1")
    hyp_ref = hypothesis.get("hypothesis_id", "unknown")

    description = _generate_description(hypothesis, kind)
    assertion_config = _build_assertion_config(hypothesis, kind)
    observers = _required_observers(kind, hypothesis)

    obl = ProofObligation(
        obligation_id="",
        kind=kind,
        hypothesis_ref=hyp_ref,
        severity=severity,
        entity_alias=entity,
        description=description,
        required_observers=observers,
        assertion_config=assertion_config,
        status="PENDING",
        source_engine=hypothesis.get("engine", hypothesis.get("source_engine", "")),
    )

    # If the hypothesis looks composite (e.g. mentions multiple entities or
    # cross-cutting concerns), emit additional obligations from the
    # verification_method substeps.
    obligations = [obl]
    vm = hypothesis.get("verification_method", {})
    if isinstance(vm, list) and len(vm) > 1:
        for i, substep in enumerate(vm[1:], start=2):
            sub_desc = f"{description} [Step {i}]"
            sub_obl = ProofObligation(
                obligation_id="",
                kind=kind,
                hypothesis_ref=hyp_ref,
                severity=severity,
                entity_alias=entity,
                description=sub_desc,
                required_observers=observers,
                assertion_config={**assertion_config, "substep_index": i},
                status="PENDING",
                source_engine=obl.source_engine,
            )
            obligations.append(sub_obl)

    return obligations


def compile_from_flow(flow_config: dict) -> list[ProofObligation]:
    """Compile a flow/scenario configuration into ProofObligations.

    A flow_config is a dict describing a multi-step business process with
    expected state transitions and invariants at each step.  Typical shape::

        {
            "flow_name": "Production Order Lifecycle",
            "entity": "production_order",
            "steps": [
                {"action": "create",  "expected_state": "DRAFT",
                 "invariants": ["idempotent create"]},
                {"action": "release", "expected_state": "RELEASED",
                 "invariants": ["quantity conserved"]},
                {"action": "reject",  "expected_state": "DRAFT",
                 "invariants": ["state unchanged after rejection"]},
                ...
            ],
            "severity": "P1"
        }

    Parameters
    ----------
    flow_config : dict
        Flow definition (see shape above).

    Returns
    -------
    list[ProofObligation]
    """
    if not flow_config or not isinstance(flow_config, dict):
        return []

    obligations: list[ProofObligation] = []
    flow_name = flow_config.get("flow_name", "unnamed_flow")
    entity = flow_config.get("entity", flow_config.get("entity_alias", "primary"))
    severity = flow_config.get("severity", "P1")
    steps = flow_config.get("steps", flow_config.get("flow_steps", []))

    if not steps:
        # Minimal flow: treat the whole config as one obligation
        kind = _infer_kind(flow_name)
        observers = _required_observers(kind, {"title": flow_name, "entity": entity})
        obl = ProofObligation(
            obligation_id="",
            kind=kind,
            hypothesis_ref=flow_name,
            severity=severity,
            entity_alias=entity,
            description=f"Flow invariant from {flow_name} [{kind}]",
            required_observers=observers,
            assertion_config=_build_assertion_config(
                {"title": flow_name, "severity": severity, "verification_method": {}}, kind
            ),
            status="PENDING",
        )
        obligations.append(obl)
        return obligations

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        step_name = step.get("action", f"step_{i}")
        expected_state = step.get("expected_state", "")
        invariants = step.get("invariants", [])

        # Derive a synthetic hypothesis from the step
        synthetic_title = f"{flow_name} :: {step_name} → {expected_state}"
        synthetic_expected = " ; ".join(invariants) if invariants else f"Reach state {expected_state}"

        hypothesis = {
            "title": synthetic_title,
            "expected_behavior": synthetic_expected,
            "severity": severity,
            "entity": entity,
            "hypothesis_id": f"flow_{flow_name}_step_{i}",
            "verification_method": step.get("verification_method", {}),
        }

        kind = _infer_kind(synthetic_title, synthetic_expected)

        # If invariants explicitly mention a kind keyword, let it override
        for inv in invariants:
            k = _infer_kind(inv)
            if k != "lifecycle_transition":
                kind = k
                break

        description = _generate_description(hypothesis, kind)
        assertion_config = _build_assertion_config(hypothesis, kind)
        observers = _required_observers(kind, hypothesis)

        # Add step-specific context
        assertion_config["step_index"] = i
        assertion_config["action"] = step_name
        assertion_config["expected_state"] = expected_state

        obl = ProofObligation(
            obligation_id="",
            kind=kind,
            hypothesis_ref=f"flow_{flow_name}_step_{i}",
            severity=severity,
            entity_alias=entity,
            description=description,
            required_observers=observers,
            assertion_config=assertion_config,
            status="PENDING",
        )
        obligations.append(obl)

    # If the flow has a global invariant, emit one more top-level obligation
    global_invariant = flow_config.get("invariant", flow_config.get("global_invariant", ""))
    if global_invariant:
        kind = _infer_kind(global_invariant)
        observers = _required_observers(kind, {"title": global_invariant, "entity": entity})
        obl = ProofObligation(
            obligation_id="",
            kind=kind,
            hypothesis_ref=flow_name,
            severity=severity,
            entity_alias=entity,
            description=f"Flow global invariant: {global_invariant} [{kind}]",
            required_observers=observers,
            assertion_config=_build_assertion_config(
                {"title": global_invariant, "severity": severity, "verification_method": {}}, kind
            ),
            status="PENDING",
        )
        obligations.append(obl)

    return obligations


# ---------------------------------------------------------------------------
# Batch convenience
# ---------------------------------------------------------------------------

def compile_batch(hypotheses: list[dict]) -> list[ProofObligation]:
    """Compile a list of hypotheses into a flat list of ProofObligations."""
    result: list[ProofObligation] = []
    for h in hypotheses:
        result.extend(compile_from_hypothesis(h))
    return result
