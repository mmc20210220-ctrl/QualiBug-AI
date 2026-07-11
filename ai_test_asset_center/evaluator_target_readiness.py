"""Industry-neutral evaluator target readiness and serial-admission receipts."""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from .target_policy import WRITE_EXECUTION_MODE, build_target_policy_decision


READINESS_SCHEMA = "qualibug.evaluator-target-readiness.v1"
ADMISSION_SCHEMA = "qualibug.evaluator-target-admission.v1"
TARGET_STATES = frozenset({
    "ASSET_VALID",
    "DEPLOYABLE",
    "RUNTIME_READY",
    "EVALUATOR_READY",
    "STOPPED_CLEAN",
    "BLOCKED",
    "FAILED_SAFE",
})
TRANSITIONS = {
    "NOT_STARTED": frozenset({"ASSET_VALID", "BLOCKED", "FAILED_SAFE"}),
    "ASSET_VALID": frozenset({"DEPLOYABLE", "BLOCKED", "FAILED_SAFE"}),
    "DEPLOYABLE": frozenset({"RUNTIME_READY", "BLOCKED", "FAILED_SAFE", "STOPPED_CLEAN"}),
    "RUNTIME_READY": frozenset({"EVALUATOR_READY", "STOPPED_CLEAN", "BLOCKED", "FAILED_SAFE"}),
    "EVALUATOR_READY": frozenset({"STOPPED_CLEAN", "BLOCKED", "FAILED_SAFE"}),
    "BLOCKED": frozenset({"STOPPED_CLEAN"}),
    "FAILED_SAFE": frozenset({"STOPPED_CLEAN"}),
    "STOPPED_CLEAN": frozenset({"ASSET_VALID"}),
}
RUNTIME_REQUIRED_CHECKS = frozenset({
    "health",
    "login",
    "api",
    "database_observation",
    "fixture_prepare",
    "fixture_cleanup",
})
EVALUATOR_REQUIRED_CHECKS = RUNTIME_REQUIRED_CHECKS | frozenset({
    "reset",
    "evaluator_private_manifest",
    "ground_truth_or_clean_audit",
})
STOPPED_REQUIRED_CHECKS = frozenset({"target_stopped", "ports_released"})
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class EvaluatorTargetReadinessError(ValueError):
    """Raised when a target state or receipt would overstate readiness."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _state(value: Any) -> str:
    state = _text(value).upper()
    if state not in TARGET_STATES:
        raise EvaluatorTargetReadinessError(f"unsupported target state: {state!r}")
    return state


def _previous_state(value: Any) -> str:
    state = _text(value).upper()
    if state != "NOT_STARTED" and state not in TARGET_STATES:
        raise EvaluatorTargetReadinessError(f"unsupported previous target state: {state!r}")
    return state


def validate_target_transition(previous_state: str, new_state: str) -> None:
    previous = _previous_state(previous_state)
    current = _state(new_state)
    if current not in TRANSITIONS[previous]:
        raise EvaluatorTargetReadinessError(
            f"invalid target transition: {previous} -> {current}"
        )


def assess_serial_target_admission(
    receipts: list[dict[str, Any]],
    requested_target_id: str,
) -> dict[str, Any]:
    requested = _text(requested_target_id)
    if not requested:
        raise EvaluatorTargetReadinessError("requested target id is required")
    latest: dict[str, str] = {}
    for index, receipt in enumerate(receipts):
        if not isinstance(receipt, dict):
            raise EvaluatorTargetReadinessError(f"receipt[{index}] must be an object")
        target_id = _text(receipt.get("target_id"))
        if not target_id:
            raise EvaluatorTargetReadinessError(f"receipt[{index}] target_id is required")
        latest[target_id] = _state(receipt.get("state"))
    active = sorted(
        target_id
        for target_id, state in latest.items()
        if target_id != requested and state != "STOPPED_CLEAN"
    )
    blocking = ["BLOCKED_ANOTHER_TARGET_ACTIVE"] if active else []
    canonical = {
        "requested_target_id": requested,
        "allowed": not active,
        "active_target_ids": active,
        "latest_states": dict(sorted(latest.items())),
        "blocking_codes": blocking,
    }
    decision_id = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": ADMISSION_SCHEMA,
        "decision_id": f"sha256:{decision_id}",
        **canonical,
    }


def _required_checks(state: str) -> frozenset[str]:
    if state == "RUNTIME_READY":
        return RUNTIME_REQUIRED_CHECKS
    if state == "EVALUATOR_READY":
        return EVALUATOR_REQUIRED_CHECKS
    if state == "STOPPED_CLEAN":
        return STOPPED_REQUIRED_CHECKS
    return frozenset()


def build_target_readiness_receipt(
    *,
    target_id: str,
    target_role: str,
    state: str,
    previous_state: str,
    environment_type: str,
    environment_ref: str,
    requested_base_url: str,
    approved_base_url: str,
    checks: dict[str, str],
    fingerprints: dict[str, str],
    blocking_codes: list[str] | None = None,
    operator_action: str = "",
) -> dict[str, Any]:
    target = _text(target_id)
    role = _text(target_role)
    if not target or not role:
        raise EvaluatorTargetReadinessError("target id and role are required")
    current = _state(state)
    previous = _previous_state(previous_state)
    validate_target_transition(previous, current)
    normalized_checks = {
        _text(name): _text(result).lower()
        for name, result in checks.items()
        if _text(name)
    }
    required = _required_checks(current)
    missing = sorted(
        name for name in required if normalized_checks.get(name) != "passed"
    )
    if missing:
        raise EvaluatorTargetReadinessError(
            f"missing required checks for {current}: {', '.join(missing)}"
        )
    normalized_fingerprints: dict[str, str] = {}
    for name, value in fingerprints.items():
        key = _text(name)
        digest = _text(value).lower().removeprefix("sha256:")
        if not key or not SHA256_RE.fullmatch(digest):
            raise EvaluatorTargetReadinessError(
                f"malformed SHA-256 fingerprint: {key!r}"
            )
        normalized_fingerprints[key] = f"sha256:{digest}"
    blockers = sorted({_text(code) for code in (blocking_codes or []) if _text(code)})
    if current in {"BLOCKED", "FAILED_SAFE"} and not blockers:
        raise EvaluatorTargetReadinessError(f"{current} receipt requires a blocking code")
    policy = build_target_policy_decision(
        requested_base_url=requested_base_url,
        approved_base_url=approved_base_url,
        environment_type=environment_type,
        environment_ref=environment_ref,
        execution_mode=WRITE_EXECUTION_MODE,
        runtime_status="approved",
    )
    canonical = {
        "schema_version": READINESS_SCHEMA,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target_id": target,
        "target_role": role,
        "state": current,
        "previous_state": previous,
        "environment_type": _text(environment_type).lower(),
        "environment_ref": _text(environment_ref),
        "target_policy_decision": policy,
        "checks": dict(sorted(normalized_checks.items())),
        "blocking_codes": blockers,
        "operator_action": _text(operator_action),
        "fingerprints": dict(sorted(normalized_fingerprints.items())),
        "measurement_status": "NOT_MEASURED",
        "commercial_promotion_evidence": False,
        "gate_d_unlocked": False,
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {**canonical, "receipt_fingerprint": f"sha256:{digest}"}
