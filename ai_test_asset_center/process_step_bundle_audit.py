"""Deep audit of process-step receipts inside an execution receipt bundle.

The ProcessStepLedger remains the fact authority. This module verifies that the
bundle contains the exact sealed receipt set emitted by that ledger: one ledger
identity, a reconstructable ledger hash, valid per-step fact hashes, balanced
step sets, and exact observation/oracle/cleanup receipt lineage.
"""
from __future__ import annotations

from typing import Any

from .process_step_evidence_scope_audit import audit_process_step_evidence_scope
from .process_step_execution import (
    PROCESS_STEP_FACT_MODEL_VERSION,
    PROCESS_STEP_LEDGER_SCHEMA,
    PROCESS_STEP_RECEIPT_SCHEMA,
    _canonical_step_fact,
    _stable_hash,
)

PROCESS_STEP_BUNDLE_AUDIT_SCHEMA = "qualibug.process-step-bundle-audit.v1"

_IDENTITY_FIELDS = (
    "campaign_id",
    "run_id",
    "obligation_id",
    "experiment_id",
    "fixture_id",
    "protocol_id",
)
_DECLARATION_FIELDS = (
    "ledger_recorded_step_ids",
    "ledger_required_step_ids",
    "ledger_attempted_step_ids",
    "ledger_executed_step_ids",
    "ledger_accepted_step_ids",
    "ledger_completed_step_ids",
    "ledger_failed_step_ids",
    "ledger_pending_semantic_step_ids",
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _ids(value: Any) -> list[str]:
    out: list[str] = []
    for raw in _list(value):
        text = _text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _declared_sets(payload: dict[str, Any]) -> dict[str, list[str]] | None:
    if not all(isinstance(payload.get(field), list) for field in _DECLARATION_FIELDS):
        return None
    return {field: _ids(payload.get(field)) for field in _DECLARATION_FIELDS}


def _sorted_rejections(value: Any) -> list[dict[str, Any]]:
    rows = [dict(_dict(row)) for row in _list(value) if isinstance(row, dict)]
    return sorted(
        rows,
        key=lambda row: (
            _text(row.get("step_id")),
            _text(row.get("field")),
            _text(row.get("receipt_id")),
            _text(row.get("reason_code")),
        ),
    )


def _derived_sets(payloads: list[dict[str, Any]]) -> dict[str, list[str]]:
    ordered = sorted(
        payloads,
        key=lambda row: (
            _safe_int(row.get("step_ordinal")),
            _text(row.get("step_id")),
        ),
    )
    recorded: list[str] = []
    attempted: list[str] = []
    executed: list[str] = []
    accepted: list[str] = []
    completed: list[str] = []
    failed: list[str] = []
    pending: list[str] = []
    required: list[str] = []

    for row in ordered:
        step_id = _text(row.get("step_id"))
        if not step_id:
            continue
        recorded.append(step_id)
        if row.get("required_step") is True:
            required.append(step_id)
        if row.get("transport_attempted") is True:
            attempted.append(step_id)
        if (
            row.get("response_received") is True
            and _text(row.get("final_step_status") or row.get("final_status")).upper()
            == "EXECUTED"
        ):
            executed.append(step_id)
        if row.get("operation_accepted") is True:
            accepted.append(step_id)
        if (
            row.get("semantic_step_status") == "TARGET_REACHED"
            and row.get("step_completed") is True
        ):
            completed.append(step_id)
        if row.get("step_failed") is True:
            failed.append(step_id)
        if row.get("semantic_step_status") == "PENDING_OBSERVATION":
            pending.append(step_id)

    return {
        "ledger_recorded_step_ids": recorded,
        "ledger_required_step_ids": required,
        "ledger_attempted_step_ids": attempted,
        "ledger_executed_step_ids": executed,
        "ledger_accepted_step_ids": accepted,
        "ledger_completed_step_ids": completed,
        "ledger_failed_step_ids": failed,
        "ledger_pending_semantic_step_ids": pending,
    }


def audit_process_step_receipt_bundle(
    *,
    receipts_by_id: dict[str, dict[str, Any]],
    process_step_receipt_ids: list[str],
) -> dict[str, Any]:
    """Fail closed on mixed, stale, tampered, unscoped, or incomplete steps."""

    grouped_receipt_ids = _ids(process_step_receipt_ids)
    discovered_receipt_ids = sorted(
        receipt_id
        for receipt_id, envelope in receipts_by_id.items()
        if _text(_dict(envelope).get("receipt_type")) == PROCESS_STEP_RECEIPT_SCHEMA
    )
    receipt_ids = _ids(grouped_receipt_ids + discovered_receipt_ids)
    group_missing_receipt_ids = sorted(
        set(discovered_receipt_ids) - set(grouped_receipt_ids)
    )
    group_unknown_receipt_ids = sorted(
        set(grouped_receipt_ids) - set(discovered_receipt_ids)
    )

    payloads: list[dict[str, Any]] = []
    receipt_by_step_id: dict[str, str] = {}
    invalid_type_receipt_ids: list[str] = []
    incomplete_receipt_ids: list[str] = []
    receipt_identity_mismatch_ids: list[str] = []
    payload_identity_mismatch_ids: list[str] = []
    step_fact_hash_mismatch_ids: list[str] = []
    duplicate_step_receipt_ids: list[str] = []
    declaration_missing_receipt_ids: list[str] = []
    declaration_mismatch_receipt_ids: list[str] = []
    rejection_declaration_mismatch_receipt_ids: list[str] = []
    declared_reference: dict[str, list[str]] | None = None
    declared_rejections: list[dict[str, Any]] | None = None
    declared_receipt_count: int | None = None
    ledger_ids: dict[str, list[str]] = {}
    ledger_hashes: dict[str, list[str]] = {}
    fact_model_versions: dict[str, list[str]] = {}

    for receipt_id in receipt_ids:
        envelope = _dict(receipts_by_id.get(receipt_id))
        if _text(envelope.get("receipt_type")) != PROCESS_STEP_RECEIPT_SCHEMA:
            invalid_type_receipt_ids.append(receipt_id)
            continue
        payload = dict(_dict(envelope.get("payload")))
        required_values = {
            "receipt_id": _text(payload.get("receipt_id")),
            "step_id": _text(payload.get("step_id")),
            "process_step_ledger_id": _text(payload.get("process_step_ledger_id")),
            "process_step_ledger_hash": _text(payload.get("process_step_ledger_hash")),
            "step_fact_hash": _text(payload.get("step_fact_hash")),
            "receipt_schema_version": _text(payload.get("receipt_schema_version")),
            "fact_model_version": _text(payload.get("fact_model_version")),
        }
        if (
            not all(required_values.values())
            or required_values["receipt_schema_version"] != PROCESS_STEP_RECEIPT_SCHEMA
        ):
            incomplete_receipt_ids.append(receipt_id)
            continue
        if required_values["receipt_id"] != receipt_id:
            receipt_identity_mismatch_ids.append(receipt_id)
        if any(
            _text(payload.get(field)) != _text(envelope.get(field))
            for field in _IDENTITY_FIELDS
        ):
            payload_identity_mismatch_ids.append(receipt_id)

        step_id = required_values["step_id"]
        if step_id in receipt_by_step_id:
            duplicate_step_receipt_ids.extend(
                [receipt_by_step_id[step_id], receipt_id]
            )
        else:
            receipt_by_step_id[step_id] = receipt_id

        observed_step_hash = _stable_hash(_canonical_step_fact(payload))
        if observed_step_hash != required_values["step_fact_hash"]:
            step_fact_hash_mismatch_ids.append(receipt_id)

        ledger_ids.setdefault(required_values["process_step_ledger_id"], []).append(
            receipt_id
        )
        ledger_hashes.setdefault(required_values["process_step_ledger_hash"], []).append(
            receipt_id
        )
        fact_model_versions.setdefault(required_values["fact_model_version"], []).append(
            receipt_id
        )

        declaration = _declared_sets(payload)
        raw_rejections = payload.get("ledger_receipt_scope_rejections")
        if (
            declaration is None
            or not isinstance(payload.get("ledger_receipt_count"), int)
            or not isinstance(raw_rejections, list)
        ):
            declaration_missing_receipt_ids.append(receipt_id)
        else:
            rejections = _sorted_rejections(raw_rejections)
            if declared_reference is None:
                declared_reference = declaration
                declared_rejections = rejections
                declared_receipt_count = int(payload.get("ledger_receipt_count"))
            else:
                if (
                    declaration != declared_reference
                    or int(payload.get("ledger_receipt_count"))
                    != declared_receipt_count
                ):
                    declaration_mismatch_receipt_ids.append(receipt_id)
                if rejections != declared_rejections:
                    rejection_declaration_mismatch_receipt_ids.append(receipt_id)
        payloads.append(payload)

    ledger_identity_value_consistent = len(ledger_ids) == 1 and bool(ledger_ids)
    ledger_hash_value_consistent = len(ledger_hashes) == 1 and bool(ledger_hashes)
    fact_model_consistent = (
        len(fact_model_versions) == 1
        and PROCESS_STEP_FACT_MODEL_VERSION in fact_model_versions
    )

    derived = _derived_sets(payloads)
    set_mismatch_fields: list[str] = []
    if declared_reference is not None:
        for field in _DECLARATION_FIELDS:
            if declared_reference[field] != derived[field]:
                set_mismatch_fields.append(field)
        if declared_receipt_count != len(derived["ledger_recorded_step_ids"]):
            set_mismatch_fields.append("ledger_receipt_count")
    else:
        set_mismatch_fields.append("ledger_declaration_missing")

    actual_recorded = set(derived["ledger_recorded_step_ids"])
    actual_attempted = set(derived["ledger_attempted_step_ids"])
    actual_executed = set(derived["ledger_executed_step_ids"])
    actual_accepted = set(derived["ledger_accepted_step_ids"])
    actual_completed = set(derived["ledger_completed_step_ids"])
    actual_failed = set(derived["ledger_failed_step_ids"])
    actual_pending = set(derived["ledger_pending_semantic_step_ids"])

    invariant_errors: list[str] = []
    if not actual_completed <= actual_accepted:
        invariant_errors.append("completed_not_subset_of_accepted")
    if not actual_accepted <= actual_executed:
        invariant_errors.append("accepted_not_subset_of_executed")
    if not actual_executed <= actual_attempted:
        invariant_errors.append("executed_not_subset_of_attempted")
    if actual_completed & actual_failed:
        invariant_errors.append("completed_and_failed_overlap")
    if actual_pending & (actual_completed | actual_failed):
        invariant_errors.append("pending_overlaps_terminal_semantics")
    if declared_reference is not None and not set(
        declared_reference["ledger_required_step_ids"]
    ) <= actual_recorded:
        invariant_errors.append("required_not_subset_of_recorded")

    evidence_scope_audit = audit_process_step_evidence_scope(
        receipts_by_id=receipts_by_id,
        process_step_payloads=payloads,
    )
    for field in _ids(evidence_scope_audit.get("set_mismatch_fields")):
        if field not in set_mismatch_fields:
            set_mismatch_fields.append(field)

    reconstructed_ledger_hash = ""
    declared_ledger_hash = (
        next(iter(ledger_hashes)) if ledger_hash_value_consistent else ""
    )
    if (
        payloads
        and declared_reference is not None
        and declared_rejections is not None
        and ledger_identity_value_consistent
        and fact_model_consistent
    ):
        first = payloads[0]
        canonical_rows = [
            _canonical_step_fact(row)
            for row in sorted(
                payloads,
                key=lambda row: (
                    _safe_int(row.get("step_ordinal")),
                    _text(row.get("step_id")),
                ),
            )
        ]
        reconstructed_snapshot = {
            "schema_version": PROCESS_STEP_LEDGER_SCHEMA,
            "fact_model_version": PROCESS_STEP_FACT_MODEL_VERSION,
            "ledger_id": next(iter(ledger_ids)),
            "campaign_id": _text(first.get("campaign_id")),
            "run_id": _text(first.get("run_id")),
            "obligation_id": _text(first.get("obligation_id")),
            "experiment_id": _text(first.get("experiment_id")),
            "fixture_id": _text(first.get("fixture_id")),
            "protocol_id": _text(first.get("protocol_id")),
            "required_step_ids": list(
                declared_reference["ledger_required_step_ids"]
            ),
            "attempted_step_ids": list(
                declared_reference["ledger_attempted_step_ids"]
            ),
            "executed_step_ids": list(
                declared_reference["ledger_executed_step_ids"]
            ),
            "accepted_step_ids": list(
                declared_reference["ledger_accepted_step_ids"]
            ),
            "completed_step_ids": list(
                declared_reference["ledger_completed_step_ids"]
            ),
            "failed_step_ids": list(
                declared_reference["ledger_failed_step_ids"]
            ),
            "rows": canonical_rows,
            "receipt_scope_rejections": declared_rejections,
        }
        reconstructed_ledger_hash = _stable_hash(reconstructed_snapshot)
    ledger_hash_content_valid = bool(
        reconstructed_ledger_hash
        and declared_ledger_hash
        and reconstructed_ledger_hash == declared_ledger_hash
    )
    ledger_identity_consistent = (
        ledger_identity_value_consistent and not payload_identity_mismatch_ids
    )
    ledger_hash_consistent = (
        ledger_hash_value_consistent and ledger_hash_content_valid
    )

    validation_errors: list[str] = []
    if not receipt_ids:
        validation_errors.append("process_step_receipts_missing")
    if group_missing_receipt_ids or group_unknown_receipt_ids:
        validation_errors.append("process_step_receipt_group_mismatch")
    if invalid_type_receipt_ids:
        validation_errors.append("process_step_receipt_type_invalid")
    if incomplete_receipt_ids:
        validation_errors.append("process_step_receipt_incomplete")
    if receipt_identity_mismatch_ids or duplicate_step_receipt_ids:
        validation_errors.append("process_step_receipt_identity_mismatch")
    if not ledger_identity_consistent:
        validation_errors.append("process_step_ledger_identity_mismatch")
    if not ledger_hash_value_consistent:
        validation_errors.append("process_step_ledger_hash_mismatch")
    elif not ledger_hash_content_valid:
        validation_errors.append("process_step_ledger_hash_content_mismatch")
    if not fact_model_consistent:
        validation_errors.append("process_step_fact_model_mismatch")
    if step_fact_hash_mismatch_ids:
        validation_errors.append("process_step_fact_hash_mismatch")
    if (
        declaration_missing_receipt_ids
        or declaration_mismatch_receipt_ids
        or rejection_declaration_mismatch_receipt_ids
    ):
        validation_errors.append("process_step_declaration_mismatch")
    for error in _ids(evidence_scope_audit.get("validation_errors")):
        if error not in validation_errors:
            validation_errors.append(error)
    if set_mismatch_fields or invariant_errors:
        validation_errors.append("process_step_set_mismatch")

    complete = not validation_errors
    all_receipt_ids = sorted(set(receipt_ids))
    return {
        "schema_version": PROCESS_STEP_BUNDLE_AUDIT_SCHEMA,
        "complete": complete,
        "ledger_identity_consistent": ledger_identity_consistent,
        "ledger_hash_value_consistent": ledger_hash_value_consistent,
        "ledger_hash_content_valid": ledger_hash_content_valid,
        "ledger_hash_consistent": ledger_hash_consistent,
        "fact_model_consistent": fact_model_consistent,
        "step_fact_hashes_valid": not step_fact_hash_mismatch_ids,
        "step_sets_balanced": (
            not set_mismatch_fields
            and not invariant_errors
            and evidence_scope_audit.get("complete") is True
        ),
        "step_evidence_scopes_complete": evidence_scope_audit.get("complete") is True,
        "evidence_scope_audit": evidence_scope_audit,
        "grouped_process_step_receipt_ids": grouped_receipt_ids,
        "discovered_process_step_receipt_ids": discovered_receipt_ids,
        "process_step_receipt_ids": receipt_ids,
        "group_missing_receipt_ids": group_missing_receipt_ids,
        "group_unknown_receipt_ids": group_unknown_receipt_ids,
        "process_step_ledger_ids": sorted(ledger_ids),
        "process_step_ledger_hashes": sorted(ledger_hashes),
        "declared_process_step_ledger_hash": declared_ledger_hash,
        "reconstructed_process_step_ledger_hash": reconstructed_ledger_hash,
        "fact_model_versions": sorted(fact_model_versions),
        "declared_step_sets": declared_reference or {},
        "derived_step_sets": derived,
        "validation_errors": validation_errors,
        "invalid_type_receipt_ids": sorted(set(invalid_type_receipt_ids)),
        "incomplete_receipt_ids": sorted(set(incomplete_receipt_ids)),
        "receipt_identity_mismatch_ids": sorted(
            set(receipt_identity_mismatch_ids)
        ),
        "payload_identity_mismatch_ids": sorted(
            set(payload_identity_mismatch_ids)
        ),
        "duplicate_step_receipt_ids": sorted(set(duplicate_step_receipt_ids)),
        "ledger_identity_mismatch_receipt_ids": all_receipt_ids
        if not ledger_identity_consistent
        else [],
        "ledger_hash_mismatch_receipt_ids": all_receipt_ids
        if not ledger_hash_consistent
        else [],
        "step_fact_hash_mismatch_receipt_ids": sorted(
            set(step_fact_hash_mismatch_ids)
        ),
        "declaration_missing_receipt_ids": sorted(
            set(declaration_missing_receipt_ids)
        ),
        "declaration_mismatch_receipt_ids": sorted(
            set(declaration_mismatch_receipt_ids)
        ),
        "rejection_declaration_mismatch_receipt_ids": sorted(
            set(rejection_declaration_mismatch_receipt_ids)
        ),
        "receipt_scope_broadcast_ids": _ids(
            evidence_scope_audit.get("broadcast_receipt_ids")
        ),
        "unbound_evidence_receipt_ids": _ids(
            evidence_scope_audit.get("unbound_receipt_ids")
        ),
        "missing_observation_step_ids": _ids(
            evidence_scope_audit.get("missing_observation_step_ids")
        ),
        "missing_oracle_step_ids": _ids(
            evidence_scope_audit.get("missing_oracle_step_ids")
        ),
        "missing_cleanup_execution_step_ids": _ids(
            evidence_scope_audit.get("missing_cleanup_execution_step_ids")
        ),
        "missing_cleanup_verification_step_ids": _ids(
            evidence_scope_audit.get("missing_cleanup_verification_step_ids")
        ),
        "set_mismatch_fields": sorted(set(set_mismatch_fields)),
        "invariant_errors": sorted(set(invariant_errors)),
    }
