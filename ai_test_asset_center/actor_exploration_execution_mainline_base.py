"""Execution-safe helpers for runtime actor exploration.

These helpers keep actor switching and retry policy deterministic.  They do
not perform transport and can therefore be tested without a target system.
"""
from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any

from .behavior_ir_core import (
    _infer_operation_effect,
    _operation_has_semantic_marker,
)


_ACTOR_SCALAR_KEYS = frozenset({
    "actor_ref",
    "owner_actor_ref",
    "viewer_actor_ref",
    "control_actor_ref",
    "treatment_actor_ref",
    "fixture_owner_actor_ref",
    "resolver_actor_ref",
    "source_actor_ref",
    "cleanup_actor_ref",
    "created_by_actor_ref",
})
_ACTOR_LIST_KEYS = frozenset({"required_actors", "actor_refs"})
_IMMUTABLE_ACTOR_CONTRACT_PATHS = frozenset({
    "actor_execution_plan",
    "actor_selection_contract",
})
_SAFE_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_STATE_TRANSITION_PATTERNS = frozenset({
    "approve", "submit", "cancel", "enable", "disable", "activate",
    "deactivate", "publish", "freeze", "status", "transition", "state",
})
_DESTRUCTIVE_PATTERNS = frozenset({
    "delete", "refund", "payment", "pay", "transfer", "ship", "ban",
    "close", "revoke", "destroy", "permanent",
})
_RESIDUE_CLEANUP_ACTIONS = frozenset({"accepted_residue"})
_RESIDUE_CLEANUP_MODES = frozenset({"accepted_residue_no_cleanup"})
_RESIDUE_CLEANUP_AUTHORITIES = frozenset({"accepted_residue"})
_ACTIVE_EXPLORATION_EFFECT: ContextVar[str] = ContextVar(
    "qualibug_actor_exploration_operation_effect",
    default="",
)


@dataclass(frozen=True)
class HttpAttemptEvidence:
    status_code: int
    actor_ref: str
    operation_ref: str
    phase: str
    source: str
    business_layer_reached: bool = False


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _experiment_property(experiment: dict[str, Any]) -> dict[str, Any]:
    """Return semantic property without assuming a non-canonical top-level copy."""

    direct = _dict(_dict(experiment).get("property"))
    if direct:
        return direct
    for assertion in _list(_dict(experiment).get("assertions")):
        if not isinstance(assertion, dict):
            continue
        prop = _dict(assertion.get("property"))
        if prop:
            return prop
    return {}


def _status_code(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    if isinstance(value, float) and value.is_integer():
        return int(value) if value > 0 else 0
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else 0
    return 0


def _operation_effect(operation: dict[str, Any], method: str = "") -> str:
    """Resolve read/write semantics through the Behavior IR single authority."""

    row = _dict(operation)
    resolved_method = _text(method or row.get("method")).upper()
    return _infer_operation_effect(row, resolved_method)


def apply_actor_execution_overlay(
    experiment: dict[str, Any],
    candidate_actor_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebind every executable reference to one compiler-sealed candidate.

    Only references equal to the compiler-selected source actor are replaced;
    unrelated fixture/dependency actors remain unchanged. The immutable actor
    execution/selection contracts are never rewritten by an attempt overlay.
    """
    candidate = _text(candidate_actor_id)
    if not candidate:
        raise ValueError("candidate_actor_id_required")

    governed = deepcopy(_dict(experiment))
    plan = _dict(governed.get("actor_execution_plan"))
    prop = _experiment_property(governed)
    required = [
        _text(value)
        for value in _list(governed.get("required_actors"))
        if _text(value)
    ]
    planned_actor_refs = [
        _text(step.get("actor_ref"))
        for step in [
            *_list(governed.get("control_plan")),
            *_list(governed.get("treatment_plan")),
        ]
        if isinstance(step, dict) and _text(step.get("actor_ref"))
    ]
    source_actor = (
        _text(plan.get("source_actor_id"))
        or _text(prop.get("actor_ref"))
        or (required[0] if required else "")
        or (planned_actor_refs[0] if planned_actor_refs else "")
    )
    if not source_actor:
        raise ValueError("exploration_source_actor_missing")
    sealed_candidates = {
        _text(value)
        for value in _list(plan.get("candidate_ids"))
        if _text(value)
    }
    if sealed_candidates and candidate not in sealed_candidates:
        raise ValueError(f"candidate_actor_not_in_compiled_plan:{candidate}")

    changed_paths: list[str] = []

    def visit(value: Any, path: str) -> Any:
        if path in _IMMUTABLE_ACTOR_CONTRACT_PATHS:
            return deepcopy(value)
        if isinstance(value, dict):
            output: dict[str, Any] = {}
            for key, raw in value.items():
                child_path = f"{path}.{key}" if path else key
                if key in _ACTOR_SCALAR_KEYS and _text(raw) == source_actor:
                    output[key] = candidate
                    changed_paths.append(child_path)
                elif key in _ACTOR_LIST_KEYS and isinstance(raw, list):
                    replaced = [
                        candidate if _text(item) == source_actor else item
                        for item in raw
                    ]
                    if replaced != raw:
                        changed_paths.append(child_path)
                    output[key] = replaced
                else:
                    output[key] = visit(raw, child_path)
            return output
        if isinstance(value, list):
            return [visit(item, f"{path}[{index}]") for index, item in enumerate(value)]
        return value

    governed = visit(governed, "")
    if "property" in governed:
        governed_prop = dict(_dict(governed.get("property")))
        governed_prop["actor_ref"] = candidate
        governed["property"] = governed_prop
    if required:
        governed["required_actors"] = [
            candidate if actor_id == source_actor else actor_id
            for actor_id in required
        ]

    stale_paths: list[str] = []
    effective_actor_refs: set[str] = set()

    def inspect(value: Any, path: str) -> None:
        if path in _IMMUTABLE_ACTOR_CONTRACT_PATHS:
            return
        if isinstance(value, dict):
            for key, raw in value.items():
                child_path = f"{path}.{key}" if path else key
                if key in _ACTOR_SCALAR_KEYS and _text(raw):
                    effective_actor_refs.add(_text(raw))
                    if _text(raw) == source_actor and source_actor != candidate:
                        stale_paths.append(child_path)
                elif key in _ACTOR_LIST_KEYS and isinstance(raw, list):
                    for index, item in enumerate(raw):
                        actor_id = _text(item)
                        if actor_id:
                            effective_actor_refs.add(actor_id)
                        if actor_id == source_actor and source_actor != candidate:
                            stale_paths.append(f"{child_path}[{index}]")
                else:
                    inspect(raw, child_path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                inspect(item, f"{path}[{index}]")

    inspect(governed, "")
    if stale_paths:
        raise ValueError(
            "actor_overlay_incomplete:" + ",".join(stale_paths[:8])
        )

    receipt = {
        "source_actor_id": source_actor,
        "source_actor_basis": (
            "actor_execution_plan"
            if _text(plan.get("source_actor_id"))
            else "semantic_property"
            if _text(prop.get("actor_ref"))
            else "required_actors"
            if required
            else "compiled_plan_step"
        ),
        "candidate_actor_id": candidate,
        "compiled_plan_hash": _text(plan.get("plan_hash")),
        "changed_paths": changed_paths,
        "effective_actor_refs": sorted(effective_actor_refs),
        "status": "APPLIED",
    }
    return governed, receipt


def extract_primary_http_attempt_evidence(
    result: dict[str, Any],
    primary_operation_id: str,
) -> HttpAttemptEvidence:
    """Extract the target operation's real HTTP status from step evidence.

    Lifecycle strings such as ``EXECUTED`` or ``BLOCKED`` are never parsed as
    HTTP status codes.
    """
    row = _dict(result)
    primary = _text(primary_operation_id)
    steps = [item for item in _list(row.get("steps")) if isinstance(item, dict)]

    candidates: list[tuple[int, dict[str, Any]]] = []
    for step in steps:
        status = _status_code(step.get("status_code"))
        if not status:
            status = _status_code(_dict(step.get("response")).get("status_code"))
        if not status:
            continue
        operation_ref = _text(step.get("operation_ref") or step.get("operation_id"))
        phase = _text(step.get("phase")).lower()
        score = 0
        if primary and operation_ref == primary:
            score += 100
        if phase == "treatment":
            score += 20
        elif phase == "control":
            score += 10
        candidates.append((score, step))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        selected = candidates[0][1]
        status = _status_code(selected.get("status_code")) or _status_code(
            _dict(selected.get("response")).get("status_code")
        )
        return HttpAttemptEvidence(
            status_code=status,
            actor_ref=_text(selected.get("actor_ref")),
            operation_ref=_text(
                selected.get("operation_ref") or selected.get("operation_id")
            ),
            phase=_text(selected.get("phase")),
            source="steps",
            business_layer_reached=(
                selected.get("business_layer_reached") is True
                or bool(selected.get("business_error_code"))
                or bool(selected.get("validation_errors"))
            ),
        )

    for key in ("treatment_observation", "control_observation"):
        observation = _dict(row.get(key))
        status = _status_code(observation.get("status_code"))
        if status:
            return HttpAttemptEvidence(
                status_code=status,
                actor_ref=_text(observation.get("actor_ref")),
                operation_ref=_text(
                    observation.get("operation_ref") or primary
                ),
                phase="treatment" if key.startswith("treatment") else "control",
                source=key,
                business_layer_reached=(
                    observation.get("business_layer_reached") is True
                    or bool(observation.get("business_error_code"))
                    or bool(observation.get("validation_errors"))
                ),
            )

    execution_receipt = _dict(row.get("execution_receipt"))
    status = _status_code(execution_receipt.get("status_code"))
    if status:
        return HttpAttemptEvidence(
            status_code=status,
            actor_ref=_text(execution_receipt.get("actor_ref")),
            operation_ref=_text(execution_receipt.get("operation_ref") or primary),
            phase=_text(execution_receipt.get("phase")),
            source="execution_receipt.status_code",
            business_layer_reached=(
                execution_receipt.get("business_layer_reached") is True
            ),
        )

    return HttpAttemptEvidence(
        status_code=0,
        actor_ref="",
        operation_ref=primary,
        phase="",
        source="missing_http_evidence",
        business_layer_reached=False,
    )


def _write_reversibility_gate(
    experiment: dict[str, Any],
) -> tuple[bool, str]:
    """Require a real reversible cleanup authority for write exploration.

    ``accepted_residue`` is a deliberate non-production coverage tradeoff: it
    records that no cleanup will run.  It may be a valid experiment policy, but
    it is never permission to repeat the same unknown write under several
    actors.  Multi-candidate exploration therefore requires a compiled PROVEN
    WriteReversibilityProof whose authority is not residue.
    """

    exp = _dict(experiment)
    cleanup_plan = [
        row for row in _list(exp.get("cleanup_plan"))
        if isinstance(row, dict)
    ]
    if not cleanup_plan:
        return False, "write_without_cleanup_proof"

    residue_plan = any(
        _text(row.get("action")).lower() in _RESIDUE_CLEANUP_ACTIONS
        or _text(row.get("mode")).lower() in _RESIDUE_CLEANUP_MODES
        or row.get("residue") is True
        for row in cleanup_plan
    )
    if residue_plan:
        return False, "accepted_residue_is_not_reversible"

    proof = _dict(exp.get("write_reversibility_proof"))
    if not proof:
        return False, "write_reversibility_proof_missing"
    if _text(proof.get("proof_status")).upper() != "PROVEN":
        return False, "write_reversibility_not_proven"

    proof_kind = _text(proof.get("proof_kind")).lower()
    authority_kind = _text(
        _dict(proof.get("cleanup_authority")).get("kind")
    ).lower()
    reversibility = _text(proof.get("reversibility")).lower()
    if (
        proof_kind in _RESIDUE_CLEANUP_AUTHORITIES
        or authority_kind in _RESIDUE_CLEANUP_AUTHORITIES
        or reversibility == "none"
    ):
        return False, "accepted_residue_is_not_reversible"
    return True, ""


def exploration_execution_policy(
    *,
    operation: dict[str, Any],
    experiment: dict[str, Any],
    requested_max_attempts: int,
) -> tuple[bool, int, str]:
    """Apply the final runtime safety gate before any actor is attempted.

    The Behavior IR effect classifier is the single read/write authority.  This
    keeps declared read-like POST operations aligned with compile semantics and
    ensures an explicit write declaration overrides a query-looking path.

    Risky verbs are evaluated only after a real WriteReversibilityProof exists.
    This aligns runtime with the compiler without weakening safety: residue is
    rejected, ambiguous write outcomes never retry, and DELETE stays blocked.
    """
    method = _text(operation.get("method")).upper()
    effect = _operation_effect(operation, method)
    _ACTIVE_EXPLORATION_EFFECT.set(effect)
    if effect == "read":
        return True, max(1, int(requested_max_attempts or 1)), "safe_read"

    if method == "DELETE":
        return False, 0, "destructive_operation"

    reversible, reversibility_reason = _write_reversibility_gate(experiment)
    if not reversible:
        # Anonymous-executable operations (explicitly declared empty security
        # scheme) carry no identity: exploration yields a single
        # unauthenticated candidate, so the accepted-residue concern
        # (repeating an unknown write under several actors) cannot arise —
        # the residue belongs to no actor and matches the source-declared
        # anonymous contract. A missing security field is not anonymity.
        if (
            reversibility_reason == "accepted_residue_is_not_reversible"
            and _dict(operation).get("security") is not None
            and not _list(_dict(operation).get("security"))
        ):
            return True, 1, "anonymous_accepted_residue_write"
        return False, 0, reversibility_reason

    if _operation_has_semantic_marker(operation, _STATE_TRANSITION_PATTERNS):
        prop = _experiment_property(_dict(experiment))
        owner_evidence = any(
            _text(prop.get(key))
            for key in (
                "owner_actor_ref",
                "control_actor_ref",
                "resource_creator_actor_ref",
                "previous_step_actor_ref",
            )
        )
        if not owner_evidence:
            owner_evidence = any(
                _text(row.get("fixture_owner_actor_ref") or row.get("owner_actor_ref"))
                for row in _list(_dict(experiment).get("binding_plan"))
                if isinstance(row, dict)
            )
        if not owner_evidence:
            return False, 0, "state_transition_owner_unproven"
        return True, 1, "state_transition_owner_proven"

    max_attempts = min(max(1, int(requested_max_attempts or 1)), 2)
    if _operation_has_semantic_marker(operation, _DESTRUCTIVE_PATTERNS):
        return True, max_attempts, "compensated_destructive_write"
    return True, max_attempts, "compensated_write"


def should_continue_actor_exploration(
    *,
    method: str,
    outcome: str,
    status_code: int,
    operation: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Return whether a different actor may be attempted safely.

    Direct callers may pass the operation.  The executor uses the semantic
    effect established by ``exploration_execution_policy`` in the same context,
    so retry behavior cannot drift from the runtime admission decision.
    """
    normalized_method = _text(method).upper()
    normalized_outcome = _text(outcome).lower()
    status = _status_code(status_code)
    effect = (
        _operation_effect(_dict(operation), normalized_method)
        if isinstance(operation, dict)
        else _ACTIVE_EXPLORATION_EFFECT.get()
    )
    safe_read = effect == "read" or (
        not effect and normalized_method in _SAFE_READ_METHODS
    )

    if status == 429:
        return False, "rate_limited"

    if safe_read:
        if normalized_outcome in {
            "authentication_failed",
            "permission_denied",
            "resource_not_visible",
            "infrastructure_failed",
            "inconclusive",
        }:
            return True, "safe_read_retryable"
        return False, "safe_read_terminal"

    if normalized_outcome in {
        "authentication_failed",
        "permission_denied",
        "resource_not_visible",
    }:
        return True, "write_rejected_before_effect"

    if normalized_outcome in {"infrastructure_failed", "inconclusive"}:
        return False, "write_side_effect_unknown"

    return False, "write_terminal"


def exploration_receipt(
    *,
    attempt_index: int,
    planned_actor_id: str,
    overlay_receipt: dict[str, Any],
    evidence: HttpAttemptEvidence,
    outcome: str,
    continued: bool,
    continue_reason: str,
) -> dict[str, Any]:
    return {
        "attempt_index": int(attempt_index),
        "planned_actor_id": _text(planned_actor_id),
        "effective_step_actor_id": evidence.actor_ref,
        "operation_ref": evidence.operation_ref,
        "phase": evidence.phase,
        "status_code": evidence.status_code,
        "classification": _text(outcome),
        "continued": bool(continued),
        "continue_reason": _text(continue_reason),
        "http_evidence_source": evidence.source,
        "business_layer_reached": evidence.business_layer_reached,
        "actor_overlay": dict(overlay_receipt),
    }


def evidence_as_dict(evidence: HttpAttemptEvidence) -> dict[str, Any]:
    return asdict(evidence)
