"""Executable experiment contract schema."""
from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any


SCHEMA_VERSION = "qualibug.experiment.v1"
LIFECYCLE_STATES = frozenset(
    {
        "PLANNED",
        "BINDING_READY",
        "EXECUTABLE",
        "EXECUTED",
        "VERIFIED",
        "DELIVERABLE",
        "REJECTED",
        "BLOCKED",
        "DEFERRED",
        "HARNESS_FAILED",
    }
)
TERMINAL_STATES = frozenset(
    {"DELIVERABLE", "REJECTED", "BLOCKED", "DEFERRED", "HARNESS_FAILED"}
)
_ALLOWED_TRANSITIONS = {
    "PLANNED": {"BINDING_READY", "BLOCKED", "DEFERRED", "HARNESS_FAILED"},
    "BINDING_READY": {"EXECUTABLE", "BLOCKED", "DEFERRED", "HARNESS_FAILED"},
    "EXECUTABLE": {"EXECUTED", "BLOCKED", "DEFERRED", "HARNESS_FAILED"},
    "EXECUTED": {"VERIFIED", "REJECTED", "HARNESS_FAILED"},
    "VERIFIED": {"DELIVERABLE", "REJECTED", "HARNESS_FAILED"},
}

BLOCK_REASONS = (
    "BLOCKED_MISSING_OPERATION",
    "BLOCKED_MISSING_ACTOR",
    "BLOCKED_MISSING_FIXTURE",
    "BLOCKED_MISSING_BINDING",
    "BLOCKED_MISSING_OBSERVER",
    "BLOCKED_CONTROL_ARM_NOT_PROVEN",
    "BLOCKED_OBSERVER_RECEIPT_INDETERMINATE",
    "BLOCKED_ORACLE_INPUT_INCOMPLETE",
    "BLOCKED_TARGET_POLICY",
    "BLOCKED_NON_REVERSIBLE_WRITE",
    "BLOCKED_CONFLICTING_SOURCE",
    "BLOCKED_UNSUPPORTED_ADAPTER",
    "BLOCKED_MISSING_IR_RELATION",
    "BLOCKED_AMBIGUOUS_IR_RELATION",
)


class ExperimentLifecycleError(ValueError):
    """Raised when an experiment attempts an invalid or unsafe transition."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def stable_experiment_id(obligation_id: str, *parts: Any) -> str:
    raw = "|".join([_text(obligation_id), *(_text(p) for p in parts if _text(p))])
    return f"exp_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def make_experiment(
    *,
    obligation_id: str,
    policy_version: str = "",
    control_plan: list[dict[str, Any]] | None = None,
    treatment_plan: list[dict[str, Any]] | None = None,
    binding_plan: list[dict[str, Any]] | None = None,
    setup_plan: list[dict[str, Any]] | None = None,
    assertions: list[dict[str, Any]] | None = None,
    observers: list[dict[str, Any]] | None = None,
    async_observation_policy: dict[str, Any] | None = None,
    cleanup_plan: list[dict[str, Any]] | None = None,
    safety_contract: dict[str, Any] | None = None,
    source_refs: list[dict[str, Any]] | None = None,
    compile_receipt: dict[str, Any] | None = None,
    lifecycle_state: str | None = None,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    eid = _text(experiment_id) or stable_experiment_id(obligation_id, "v1")
    receipt = dict(compile_receipt or {"status": "COMPILED"})
    initial_state = _text(lifecycle_state).upper()
    if not initial_state:
        initial_state = "BLOCKED" if _text(receipt.get("status")).upper() == "BLOCKED" else "PLANNED"
    if initial_state not in LIFECYCLE_STATES:
        raise ExperimentLifecycleError(f"unsupported lifecycle state: {initial_state}")
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": eid,
        "obligation_id": _text(obligation_id),
        "policy_version": _text(policy_version),
        "control_plan": list(control_plan or []),
        "treatment_plan": list(treatment_plan or []),
        "binding_plan": list(binding_plan or []),
        "setup_plan": list(setup_plan or []),
        "assertions": list(assertions or []),
        "observers": list(observers or []),
        "async_observation_policy": dict(async_observation_policy or {"mode": "bounded_backoff"}),
        "cleanup_plan": list(cleanup_plan or []),
        "safety_contract": dict(safety_contract or {"environment": "non_production_required"}),
        "source_refs": list(source_refs or []),
        "compile_receipt": receipt,
        "lifecycle_state": initial_state,
        "lifecycle_history": [
            {
                "from_state": "",
                "to_state": initial_state,
                "reason_code": _text(receipt.get("reason_code")),
            }
        ],
    }


def transition_experiment_state(
    experiment: dict[str, Any],
    to_state: str,
    *,
    reason_code: str = "",
) -> dict[str, Any]:
    """Move an experiment through the strict lifecycle without skipping gates."""
    current = _text(experiment.get("lifecycle_state")).upper()
    target = _text(to_state).upper()
    if current not in LIFECYCLE_STATES or target not in LIFECYCLE_STATES:
        raise ExperimentLifecycleError(f"unsupported lifecycle transition {current}->{target}")
    if current in TERMINAL_STATES or target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise ExperimentLifecycleError(f"invalid lifecycle transition {current}->{target}")
    if target == "EXECUTABLE":
        unresolved = [
            _text(item.get("slot_name") or item.get("target"))
            for item in experiment.get("binding_plan") or []
            if isinstance(item, dict)
            and _text(item.get("status")).lower() not in {"resolved", "bound"}
        ]
        if unresolved:
            raise ExperimentLifecycleError(
                "EXECUTABLE has unresolved binding slots: " + ",".join(unresolved)
            )
    updated = deepcopy(experiment)
    updated["lifecycle_state"] = target
    history = list(updated.get("lifecycle_history") or [])
    history.append(
        {
            "from_state": current,
            "to_state": target,
            "reason_code": _text(reason_code),
        }
    )
    updated["lifecycle_history"] = history
    return updated


def blocked_experiment(obligation_id: str, reason_code: str, detail: str = "") -> dict[str, Any]:
    code = reason_code if reason_code in BLOCK_REASONS else "BLOCKED_UNSUPPORTED_ADAPTER"
    return make_experiment(
        obligation_id=obligation_id,
        compile_receipt={
            "status": "BLOCKED",
            "reason_code": code,
            "detail": _text(detail),
        },
        lifecycle_state="BLOCKED",
        experiment_id=stable_experiment_id(obligation_id, code),
    )
