"""Explicit Cleanup Execution Receipt — SPEC v1.1.1 §6.

Every experiment that executes a primary governed write MUST emit an explicit
cleanup execution receipt. This receipt records what actually happened during
cleanup transport — it does NOT judge whether business state was restored
(that is the equivalence engine's job).

PROHIBITED: inferring success from ``cleanup_failures == 0``.
Only an explicit receipt with ``attempted=true`` and a real status code is valid.

Output schema: qualibug.cleanup-execution-receipt.v1
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha256_short(obj: Any) -> str:
    """Stable SHA256 prefix of a JSON-serializable object."""
    if obj is None:
        return ""
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ─── Receipt Status Constants ─────────────────────────────────────────────────

STATUS_NOT_REQUIRED = "NOT_REQUIRED"
STATUS_NOT_ATTEMPTED = "NOT_ATTEMPTED"
STATUS_BLOCKED = "BLOCKED"
STATUS_TRANSPORT_FAILED = "TRANSPORT_FAILED"
STATUS_REJECTED = "REJECTED"
STATUS_ACCEPTED = "ACCEPTED"


def build_cleanup_execution_receipt(
    *,
    experiment_id: str,
    proof_id: str,
    cleanup_plan: list[dict[str, Any]],
    steps_out: list[dict[str, Any]],
    cleanup_failures: int,
    cleanup_status: str,
    proof: dict[str, Any],
) -> dict[str, Any]:
    """Build the explicit cleanup execution receipt from actual execution evidence.

    This function examines the cleanup-phase steps in ``steps_out`` to determine
    what actually happened. It NEVER infers success from absence of failures.

    Args:
        experiment_id: The experiment identity.
        proof_id: The WriteReversibilityProof identity.
        cleanup_plan: The declared cleanup plan from the experiment.
        steps_out: All execution steps (including cleanup phase).
        cleanup_failures: Count of cleanup failures (diagnostic only).
        cleanup_status: The observed cleanup status string.
        proof: The full WriteReversibilityProof dict.

    Returns:
        A qualibug.cleanup-execution-receipt.v1 dict.
    """
    cleanup_authority = _text(
        _dict(proof.get("cleanup_authority")).get("mode")
    )
    cleanup_operation_ref = _text(
        _dict(proof.get("cleanup_authority")).get("cleanup_operation_ref")
    )

    # Extract cleanup-phase steps
    cleanup_steps = [
        step for step in steps_out
        if isinstance(step, dict) and _text(step.get("phase")) == "cleanup"
    ]

    # No cleanup plan declared
    if not cleanup_plan:
        return _build_receipt(
            experiment_id=experiment_id,
            proof_id=proof_id,
            cleanup_operation_ref=cleanup_operation_ref,
            cleanup_authority=cleanup_authority,
            attempted=False,
            transport_reached=False,
            method="",
            path_template="",
            materialized_path="",
            request_body_fingerprint="",
            identity_bindings={},
            status_code=0,
            response_body_fingerprint="",
            succeeded=False,
            status=STATUS_NOT_REQUIRED,
            reason_code="NO_CLEANUP_PLAN",
            detail="no_cleanup_plan_declared",
        )

    # Cleanup plan exists but no cleanup steps executed
    if not cleanup_steps:
        # Determine why: blocked or not attempted
        if cleanup_status == "blocked":
            status = STATUS_BLOCKED
            reason = "CLEANUP_BLOCKED_BEFORE_TRANSPORT"
            detail = "cleanup_blocked_before_transport"
        else:
            status = STATUS_NOT_ATTEMPTED
            reason = "CLEANUP_NOT_ATTEMPTED"
            detail = f"cleanup_status={cleanup_status or 'empty'}"
        return _build_receipt(
            experiment_id=experiment_id,
            proof_id=proof_id,
            cleanup_operation_ref=cleanup_operation_ref,
            cleanup_authority=cleanup_authority,
            attempted=False,
            transport_reached=False,
            method=_first_cleanup_method(cleanup_plan),
            path_template=_first_cleanup_path(cleanup_plan),
            materialized_path="",
            request_body_fingerprint="",
            identity_bindings={},
            status_code=0,
            response_body_fingerprint="",
            succeeded=False,
            status=status,
            reason_code=reason,
            detail=detail,
        )

    # Aggregate cleanup steps
    last_step = cleanup_steps[-1]
    last_status_code = int(last_step.get("status_code") or 0)
    last_governance = _dict(last_step.get("governance_receipt"))
    transport_reached = last_status_code > 0
    accepted = last_governance.get("accepted") is True

    # Determine receipt status from actual evidence
    if not transport_reached:
        status = STATUS_TRANSPORT_FAILED
        reason = "CLEANUP_TRANSPORT_NOT_REACHED"
        detail = _text(last_step.get("error")) or "status_code_zero"
        succeeded = False
    elif accepted and 200 <= last_status_code < 300:
        status = STATUS_ACCEPTED
        reason = ""
        detail = ""
        succeeded = True
    elif 200 <= last_status_code < 300:
        # 2xx but not formally accepted by governance
        status = STATUS_ACCEPTED
        reason = ""
        detail = "accepted_by_status_code"
        succeeded = True
    elif last_status_code in {401, 403}:
        status = STATUS_REJECTED
        reason = "CLEANUP_REJECTED_BY_TARGET"
        detail = f"status={last_status_code}"
        succeeded = False
    else:
        status = STATUS_TRANSPORT_FAILED
        reason = "CLEANUP_TRANSPORT_FAILED"
        detail = f"status={last_status_code}"
        succeeded = False

    # Extract identity bindings from the cleanup step
    identity_bindings: dict[str, Any] = {}
    gov_before = _dict(last_governance.get("before"))
    gov_write = _dict(last_governance.get("write"))
    if gov_write.get("body") and isinstance(gov_write.get("body"), dict):
        for key in ("id", "uuid", "resource_id"):
            if key in gov_write["body"]:
                identity_bindings[key] = gov_write["body"][key]

    return _build_receipt(
        experiment_id=experiment_id,
        proof_id=proof_id,
        cleanup_operation_ref=cleanup_operation_ref or _text(last_step.get("operation_ref")),
        cleanup_authority=cleanup_authority,
        attempted=True,
        transport_reached=transport_reached,
        method=_text(last_step.get("method")),
        path_template=_first_cleanup_path(cleanup_plan),
        materialized_path=_text(last_step.get("path")),
        request_body_fingerprint=_sha256_short(gov_write.get("body")),
        identity_bindings=identity_bindings,
        status_code=last_status_code,
        response_body_fingerprint=_sha256_short(last_step.get("body")),
        succeeded=succeeded,
        status=status,
        reason_code=reason,
        detail=detail,
    )


# ─── Internal Helpers ─────────────────────────────────────────────────────────


def _first_cleanup_method(cleanup_plan: list[dict[str, Any]]) -> str:
    """Get the method from the first cleanup plan entry."""
    for item in cleanup_plan:
        if isinstance(item, dict):
            m = _text(item.get("method"))
            if m:
                return m.upper()
    return ""


def _first_cleanup_path(cleanup_plan: list[dict[str, Any]]) -> str:
    """Get the path template from the first cleanup plan entry."""
    for item in cleanup_plan:
        if isinstance(item, dict):
            p = _text(item.get("path"))
            if p:
                return p
    return ""


def _build_receipt(
    *,
    experiment_id: str,
    proof_id: str,
    cleanup_operation_ref: str,
    cleanup_authority: str,
    attempted: bool,
    transport_reached: bool,
    method: str,
    path_template: str,
    materialized_path: str,
    request_body_fingerprint: str,
    identity_bindings: dict[str, Any],
    status_code: int,
    response_body_fingerprint: str,
    succeeded: bool,
    status: str,
    reason_code: str,
    detail: str,
) -> dict[str, Any]:
    """Assemble the final receipt with content-addressed fingerprint."""
    receipt_id = f"cleanup_exec_{_sha256_short(experiment_id + proof_id + method + materialized_path)}"
    receipt = {
        "schema_version": "qualibug.cleanup-execution-receipt.v1",
        "experiment_id": experiment_id,
        "proof_id": proof_id,
        "cleanup_operation_ref": cleanup_operation_ref,
        "cleanup_authority": cleanup_authority,
        "attempted": attempted,
        "transport_reached": transport_reached,
        "method": method,
        "path_template": path_template,
        "materialized_path": materialized_path,
        "request_body_fingerprint": request_body_fingerprint,
        "identity_bindings": identity_bindings,
        "status_code": status_code,
        "response_body_fingerprint": response_body_fingerprint,
        "succeeded": succeeded,
        "status": status,
        "reason_code": reason_code,
        "detail": detail,
        "receipt_id": receipt_id,
        "fingerprint": "",
    }
    receipt["fingerprint"] = _sha256_short({
        "experiment_id": experiment_id,
        "proof_id": proof_id,
        "attempted": attempted,
        "transport_reached": transport_reached,
        "status_code": status_code,
        "succeeded": succeeded,
        "status": status,
    })
    return receipt
