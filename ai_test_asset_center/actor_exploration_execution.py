"""Execution-safe helpers for runtime actor exploration.

These helpers keep actor switching and retry policy deterministic.  They do
not perform transport and can therefore be tested without a target system.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any


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
_SAFE_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_STATE_TRANSITION_PATTERNS = frozenset({
    "approve", "submit", "cancel", "enable", "disable", "activate",
    "deactivate", "publish", "freeze", "status", "transition", "state",
})
_DESTRUCTIVE_PATTERNS = frozenset({
    "delete", "refund", "payment", "pay", "transfer", "ship", "ban",
    "close", "revoke", "destroy", "permanent",
})


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


def apply_actor_execution_overlay(
    experiment: dict[str, Any],
    candidate_actor_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebind every compiled reference to the selected exploration actor.

    Only references equal to the compiler-selected source actor are replaced;
    unrelated fixture/dependency actors remain unchanged.
    """
    candidate = _text(candidate_actor_id)
    if not candidate:
        raise ValueError("candidate_actor_id_required")

    governed = deepcopy(_dict(experiment))
    prop = _dict(governed.get("property"))
    required = [_text(v) for v in _list(governed.get("required_actors")) if _text(v)]
    source_actor = _text(prop.get("actor_ref")) or (required[0] if required else "")
    if not source_actor:
        raise ValueError("exploration_source_actor_missing")

    changed_paths: list[str] = []

    def visit(value: Any, path: str) -> Any:
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
    governed_prop = _dict(governed.get("property"))
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
        "candidate_actor_id": candidate,
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


def exploration_execution_policy(
    *,
    operation: dict[str, Any],
    experiment: dict[str, Any],
    requested_max_attempts: int,
) -> tuple[bool, int, str]:
    """Apply the final runtime safety gate before any actor is attempted."""
    method = _text(operation.get("method")).upper()
    if method in _SAFE_READ_METHODS:
        return True, max(1, int(requested_max_attempts or 1)), "safe_read"

    combined = " ".join(
        _text(operation.get(key)).lower()
        for key in ("path", "raw_path", "name", "operation_id", "summary")
    )
    if method == "DELETE" or any(token in combined for token in _DESTRUCTIVE_PATTERNS):
        return False, 0, "destructive_operation"

    cleanup_plan = [
        row for row in _list(_dict(experiment).get("cleanup_plan"))
        if isinstance(row, dict)
    ]
    if not cleanup_plan:
        return False, 0, "write_without_cleanup_proof"

    if any(token in combined for token in _STATE_TRANSITION_PATTERNS):
        prop = _dict(_dict(experiment).get("property"))
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

    return True, min(max(1, int(requested_max_attempts or 1)), 2), "compensated_write"


def should_continue_actor_exploration(
    *,
    method: str,
    outcome: str,
    status_code: int,
) -> tuple[bool, str]:
    """Return whether a different actor may be attempted safely."""
    normalized_method = _text(method).upper()
    normalized_outcome = _text(outcome).lower()
    status = _status_code(status_code)

    if status == 429:
        return False, "rate_limited"

    if normalized_method in _SAFE_READ_METHODS:
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
