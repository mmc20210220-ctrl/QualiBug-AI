"""Independent exact-scope audit for process-step evidence receipts.

The live ledger binds evidence. This module verifies the sealed result from the
Execution Receipt Bundle: every observation, oracle, and cleanup receipt must
name one recorded step, be referenced by that same step only, and cover the
steps required by its evidence class.
"""
from __future__ import annotations

from typing import Any

from .process_step_receipt_scope import extract_receipt_step_scope

PROCESS_STEP_EVIDENCE_SCOPE_AUDIT_SCHEMA = (
    "qualibug.process-step-evidence-scope-audit.v1"
)

OBSERVATION_RECEIPT_TYPE = "qualibug.observation-receipt.v1"
ORACLE_INVOCATION_RECEIPT_TYPE = "qualibug.oracle-invocation-receipt.v1"
ORACLE_TRACE_RECEIPT_TYPE = "qualibug.oracle-trace-receipt.v1"
CLEANUP_EXECUTION_RECEIPT_TYPE = "qualibug.cleanup-execution-receipt.v1"
CLEANUP_VERIFICATION_RECEIPT_TYPE = "qualibug.cleanup-verification-receipt.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ids(value: Any) -> list[str]:
    out: list[str] = []
    for raw in _list(value):
        text = _text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _discover(
    receipts_by_id: dict[str, dict[str, Any]],
    receipt_types: set[str],
) -> list[str]:
    return sorted(
        receipt_id
        for receipt_id, envelope in receipts_by_id.items()
        if _text(_dict(envelope).get("receipt_type")) in receipt_types
    )


def _owners_by_receipt(
    process_step_payloads: list[dict[str, Any]],
    field: str,
) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for payload in process_step_payloads:
        step_id = _text(payload.get("step_id"))
        if not step_id:
            continue
        for rid in _ids(payload.get(field)):
            owners.setdefault(rid, [])
            if step_id not in owners[rid]:
                owners[rid].append(step_id)
    return owners


def _filter_owners(
    owners: dict[str, list[str]],
    allowed_receipt_ids: list[str],
) -> dict[str, list[str]]:
    allowed = set(allowed_receipt_ids)
    return {rid: list(step_ids) for rid, step_ids in owners.items() if rid in allowed}


def _audit_group(
    *,
    receipts_by_id: dict[str, dict[str, Any]],
    discovered_receipt_ids: list[str],
    owners: dict[str, list[str]],
    known_step_ids: list[str],
    evidence_kind: str,
) -> dict[str, Any]:
    unscoped: list[str] = []
    multi_step: list[str] = []
    unknown_step: list[str] = []
    unreferenced: list[str] = []
    broadcast: list[str] = []
    owner_mismatch: list[str] = []
    exact_owner_by_receipt: dict[str, str] = {}

    for rid in discovered_receipt_ids:
        envelope = _dict(receipts_by_id.get(rid))
        scope = extract_receipt_step_scope(envelope, known_step_ids=known_step_ids)
        status = _text(scope.get("status"))
        step_id = _text(scope.get("step_id"))
        receipt_owners = sorted(set(owners.get(rid, [])))
        if status == "STEP_SCOPE_MISSING":
            unscoped.append(rid)
        elif status == "MULTI_STEP_SCOPE_FORBIDDEN":
            multi_step.append(rid)
        elif status == "STEP_SCOPE_UNKNOWN":
            unknown_step.append(rid)
        elif status == "EXACT":
            exact_owner_by_receipt[rid] = step_id

        if not receipt_owners:
            unreferenced.append(rid)
        elif len(receipt_owners) > 1:
            broadcast.append(rid)
        elif status == "EXACT" and receipt_owners[0] != step_id:
            owner_mismatch.append(rid)

    complete = not any(
        (
            unscoped,
            multi_step,
            unknown_step,
            unreferenced,
            broadcast,
            owner_mismatch,
        )
    )
    return {
        "evidence_kind": evidence_kind,
        "complete": complete,
        "discovered_receipt_ids": discovered_receipt_ids,
        "owners_by_receipt": owners,
        "exact_owner_by_receipt": exact_owner_by_receipt,
        "unscoped_receipt_ids": unscoped,
        "multi_step_receipt_ids": multi_step,
        "unknown_step_receipt_ids": unknown_step,
        "unreferenced_receipt_ids": unreferenced,
        "broadcast_receipt_ids": broadcast,
        "owner_mismatch_receipt_ids": owner_mismatch,
    }


def _covered_steps(group: dict[str, Any]) -> set[str]:
    return {
        _text(step_id)
        for step_id in _dict(group.get("exact_owner_by_receipt")).values()
        if _text(step_id)
    }


def audit_process_step_evidence_scope(
    *,
    receipts_by_id: dict[str, dict[str, Any]],
    process_step_payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify exact receipt lineage and per-step evidence coverage."""

    payloads = [
        dict(_dict(row))
        for row in process_step_payloads
        if isinstance(row, dict)
    ]
    known_step_ids = [
        _text(row.get("step_id"))
        for row in payloads
        if _text(row.get("step_id"))
    ]
    required_executed_step_ids = sorted(
        {
            _text(row.get("step_id"))
            for row in payloads
            if row.get("required_step") is True
            and row.get("response_received") is True
            and _text(row.get("final_step_status") or row.get("final_status")).upper()
            == "EXECUTED"
            and _text(row.get("step_id"))
        }
    )
    cleanup_required_step_ids = sorted(
        {
            _text(row.get("step_id"))
            for row in payloads
            if _text(row.get("step_id"))
            and row.get("operation_accepted") is True
            and (
                row.get("mutation_occurred") is True
                or bool(_text(row.get("cleanup_contract_id")))
            )
        }
    )

    observation_ids = _discover(receipts_by_id, {OBSERVATION_RECEIPT_TYPE})
    oracle_invocation_ids = _discover(
        receipts_by_id,
        {ORACLE_INVOCATION_RECEIPT_TYPE},
    )
    oracle_trace_ids = _discover(receipts_by_id, {ORACLE_TRACE_RECEIPT_TYPE})
    cleanup_execution_ids = _discover(
        receipts_by_id,
        {CLEANUP_EXECUTION_RECEIPT_TYPE},
    )
    cleanup_verification_ids = _discover(
        receipts_by_id,
        {CLEANUP_VERIFICATION_RECEIPT_TYPE},
    )

    observation_owners_all = _owners_by_receipt(
        payloads,
        "scoped_observation_receipt_ids",
    )
    oracle_owners_all = _owners_by_receipt(
        payloads,
        "scoped_oracle_receipt_ids",
    )
    cleanup_owners_all = _owners_by_receipt(
        payloads,
        "scoped_cleanup_receipt_ids",
    )

    observation = _audit_group(
        receipts_by_id=receipts_by_id,
        discovered_receipt_ids=observation_ids,
        owners=_filter_owners(observation_owners_all, observation_ids),
        known_step_ids=known_step_ids,
        evidence_kind="observation",
    )
    oracle_invocation = _audit_group(
        receipts_by_id=receipts_by_id,
        discovered_receipt_ids=oracle_invocation_ids,
        owners=_filter_owners(oracle_owners_all, oracle_invocation_ids),
        known_step_ids=known_step_ids,
        evidence_kind="oracle_invocation",
    )
    oracle_trace = _audit_group(
        receipts_by_id=receipts_by_id,
        discovered_receipt_ids=oracle_trace_ids,
        owners=_filter_owners(oracle_owners_all, oracle_trace_ids),
        known_step_ids=known_step_ids,
        evidence_kind="oracle_trace",
    )
    cleanup_execution = _audit_group(
        receipts_by_id=receipts_by_id,
        discovered_receipt_ids=cleanup_execution_ids,
        owners=_filter_owners(cleanup_owners_all, cleanup_execution_ids),
        known_step_ids=known_step_ids,
        evidence_kind="cleanup_execution",
    )
    cleanup_verification = _audit_group(
        receipts_by_id=receipts_by_id,
        discovered_receipt_ids=cleanup_verification_ids,
        owners=_filter_owners(cleanup_owners_all, cleanup_verification_ids),
        known_step_ids=known_step_ids,
        evidence_kind="cleanup_verification",
    )

    observation_unknown_refs = sorted(
        set(observation_owners_all) - set(observation_ids)
    )
    oracle_unknown_refs = sorted(
        set(oracle_owners_all) - set(oracle_invocation_ids) - set(oracle_trace_ids)
    )
    cleanup_unknown_refs = sorted(
        set(cleanup_owners_all)
        - set(cleanup_execution_ids)
        - set(cleanup_verification_ids)
    )

    required = set(required_executed_step_ids)
    cleanup_required = set(cleanup_required_step_ids)
    missing_observation_step_ids = sorted(required - _covered_steps(observation))
    missing_oracle_step_ids = sorted(required - _covered_steps(oracle_invocation))
    missing_cleanup_execution_step_ids = sorted(
        cleanup_required - _covered_steps(cleanup_execution)
    )
    missing_cleanup_verification_step_ids = sorted(
        cleanup_required - _covered_steps(cleanup_verification)
    )

    group_audits = (
        observation,
        oracle_invocation,
        oracle_trace,
        cleanup_execution,
        cleanup_verification,
    )
    broadcast_receipt_ids = sorted(
        {
            rid
            for group in group_audits
            for rid in _ids(group.get("broadcast_receipt_ids"))
        }
    )
    unbound_receipt_ids = sorted(
        {
            *observation_unknown_refs,
            *oracle_unknown_refs,
            *cleanup_unknown_refs,
            *(
                rid
                for group in group_audits
                for field in (
                    "unscoped_receipt_ids",
                    "multi_step_receipt_ids",
                    "unknown_step_receipt_ids",
                    "unreferenced_receipt_ids",
                    "owner_mismatch_receipt_ids",
                )
                for rid in _ids(group.get(field))
            ),
        }
    )

    validation_errors: list[str] = []
    set_mismatch_fields: list[str] = []
    if broadcast_receipt_ids:
        validation_errors.append("process_step_receipt_scope_broadcast")
        set_mismatch_fields.append("receipt_scope_broadcast")
    if unbound_receipt_ids:
        validation_errors.append("process_step_receipt_scope_unbound")
        set_mismatch_fields.append("receipt_scope_unbound")
    if missing_observation_step_ids:
        validation_errors.append("process_step_observation_scope_incomplete")
        set_mismatch_fields.append("observation_scope")
    if missing_oracle_step_ids:
        validation_errors.append("process_step_oracle_scope_incomplete")
        set_mismatch_fields.append("oracle_scope")
    if missing_cleanup_execution_step_ids:
        validation_errors.append("process_step_cleanup_execution_scope_incomplete")
        set_mismatch_fields.append("cleanup_execution_scope")
    if missing_cleanup_verification_step_ids:
        validation_errors.append("process_step_cleanup_verification_scope_incomplete")
        set_mismatch_fields.append("cleanup_verification_scope")

    complete = not validation_errors
    return {
        "schema_version": PROCESS_STEP_EVIDENCE_SCOPE_AUDIT_SCHEMA,
        "complete": complete,
        "known_step_ids": known_step_ids,
        "required_executed_step_ids": required_executed_step_ids,
        "cleanup_required_step_ids": cleanup_required_step_ids,
        "observation": observation,
        "oracle_invocation": oracle_invocation,
        "oracle_trace": oracle_trace,
        "cleanup_execution": cleanup_execution,
        "cleanup_verification": cleanup_verification,
        "observation_unknown_reference_ids": observation_unknown_refs,
        "oracle_unknown_reference_ids": oracle_unknown_refs,
        "cleanup_unknown_reference_ids": cleanup_unknown_refs,
        "missing_observation_step_ids": missing_observation_step_ids,
        "missing_oracle_step_ids": missing_oracle_step_ids,
        "missing_cleanup_execution_step_ids": missing_cleanup_execution_step_ids,
        "missing_cleanup_verification_step_ids": missing_cleanup_verification_step_ids,
        "broadcast_receipt_ids": broadcast_receipt_ids,
        "unbound_receipt_ids": unbound_receipt_ids,
        "validation_errors": validation_errors,
        "set_mismatch_fields": set_mismatch_fields,
        "broadcast_fallback_forbidden": True,
    }
