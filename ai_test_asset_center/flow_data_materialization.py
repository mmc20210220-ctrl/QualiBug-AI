"""Runtime proof for the frozen FlowDataRequirement.

Validation occurs after the existing fixture materializer returns READY and
before any precondition or measured business step. It never creates values. A
missing, unreceipted, or drifted binding blocks measurement while leaving the
existing fixture cleanup context intact.
"""
from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "qualibug.flow-data-materialization-receipt.v1"
STATUS_VALID = "VALID"
STATUS_BLOCKED = "BLOCKED"
BLOCKED_FLOW_DATA_REQUIREMENT_MISSING = "BLOCKED_FLOW_DATA_REQUIREMENT_MISSING"
BLOCKED_FLOW_DATA_REQUIREMENT_DRIFT = "BLOCKED_FLOW_DATA_REQUIREMENT_DRIFT"
BLOCKED_FLOW_DATA_MATERIALIZATION_INCOMPLETE = (
    "BLOCKED_FLOW_DATA_MATERIALIZATION_INCOMPLETE"
)

_VALID_BINDING_STATUSES = frozenset(
    {"BOUND", "RESOLVED", "READY", "COMPLETED", "OBSERVED"}
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _receipt_targets(state: dict[str, Any]) -> set[str]:
    targets: set[str] = set()
    for raw in [
        *_list(state.get("binding_materialization_receipts")),
        *_list(state.get("fixture_receipts")),
    ]:
        row = _dict(raw)
        target = _text(
            row.get("target")
            or row.get("binding_target")
            or row.get("name")
        )
        status = _text(row.get("status")).upper()
        if target and status in _VALID_BINDING_STATUSES:
            targets.add(target)
    return targets


def validate_flow_data_materialization(
    experiment: dict[str, Any],
    materializer_state: dict[str, Any],
) -> dict[str, Any]:
    """Prove runtime bindings match the compile-frozen data requirement."""
    exp = _dict(experiment)
    state = _dict(materializer_state)
    compile_receipt = _dict(exp.get("compile_receipt"))
    requirement = _dict(exp.get("flow_data_requirement"))
    expected_id = _text(compile_receipt.get("flow_data_requirement_id"))
    expected_fingerprint = _text(
        compile_receipt.get("flow_data_requirement_fingerprint")
    )

    # Stored experiments compiled before the FlowDataRequirement migration keep
    # their original lifecycle. Newly frozen experiments must carry the contract.
    newly_frozen = _text(compile_receipt.get("compile_freeze_status")) == "FROZEN"
    if not requirement:
        if not newly_frozen or not expected_id:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": STATUS_VALID,
                "legacy_experiment": True,
                "requirement_id": "",
                "required_target_count": 0,
                "materialized_target_count": 0,
                "missing_targets": [],
                "unreceipted_targets": [],
            }
        return {
            "schema_version": SCHEMA_VERSION,
            "status": STATUS_BLOCKED,
            "reason_code": BLOCKED_FLOW_DATA_REQUIREMENT_MISSING,
            "detail": "compile_receipt_declares_requirement_but_payload_missing",
            "requirement_id": expected_id,
        }

    requirement_id = _text(requirement.get("requirement_id"))
    requirement_fingerprint = _text(
        requirement.get("requirement_fingerprint")
    )
    authority = _dict(requirement.get("materialization_authority"))
    drift_reasons: list[str] = []
    if expected_id and expected_id != requirement_id:
        drift_reasons.append("requirement_id_mismatch")
    if expected_fingerprint and expected_fingerprint != requirement_fingerprint:
        drift_reasons.append("requirement_fingerprint_mismatch")
    if _text(requirement.get("status")) != "FROZEN":
        drift_reasons.append("requirement_not_frozen")
    if _text(authority.get("executor")) != "experiment_fixture_materializer_core":
        drift_reasons.append("materialization_executor_mismatch")
    if drift_reasons:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": STATUS_BLOCKED,
            "reason_code": BLOCKED_FLOW_DATA_REQUIREMENT_DRIFT,
            "detail": ";".join(drift_reasons),
            "requirement_id": requirement_id,
            "requirement_fingerprint": requirement_fingerprint,
        }

    required_targets = [
        _text(value)
        for value in _list(
            requirement.get("materialized_before_measurement_targets")
        )
        if _text(value)
    ]
    runtime_bindings = _dict(state.get("runtime_bindings"))
    materialized_targets = {
        target
        for target in required_targets
        if runtime_bindings.get(target) not in (None, "", [], {})
    }
    receipt_targets = _receipt_targets(state)
    missing_targets = sorted(set(required_targets) - materialized_targets)
    unreceipted_targets = sorted(materialized_targets - receipt_targets)
    if missing_targets or unreceipted_targets:
        details: list[str] = []
        if missing_targets:
            details.append("missing=" + ",".join(missing_targets))
        if unreceipted_targets:
            details.append("unreceipted=" + ",".join(unreceipted_targets))
        return {
            "schema_version": SCHEMA_VERSION,
            "status": STATUS_BLOCKED,
            "reason_code": BLOCKED_FLOW_DATA_MATERIALIZATION_INCOMPLETE,
            "detail": ";".join(details),
            "requirement_id": requirement_id,
            "requirement_fingerprint": requirement_fingerprint,
            "required_targets": required_targets,
            "materialized_targets": sorted(materialized_targets),
            "missing_targets": missing_targets,
            "unreceipted_targets": unreceipted_targets,
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "status": STATUS_VALID,
        "requirement_id": requirement_id,
        "requirement_fingerprint": requirement_fingerprint,
        "required_targets": required_targets,
        "materialized_targets": sorted(materialized_targets),
        "required_target_count": len(required_targets),
        "materialized_target_count": len(materialized_targets),
        "missing_targets": [],
        "unreceipted_targets": [],
        "materialization_authority": authority,
    }


__all__ = [
    "BLOCKED_FLOW_DATA_MATERIALIZATION_INCOMPLETE",
    "BLOCKED_FLOW_DATA_REQUIREMENT_DRIFT",
    "BLOCKED_FLOW_DATA_REQUIREMENT_MISSING",
    "SCHEMA_VERSION",
    "STATUS_BLOCKED",
    "STATUS_VALID",
    "validate_flow_data_materialization",
]
