"""Recover async-wait termination authority from persisted execution facts.

The live runtime keeps cleanup receipts in ``process_graph_cleanup_receipts``.
After a process restart that transient list may be absent while the durable
ProcessStepLedger projection and the persisted contract-evidence bundle remain.
This module rebuilds only the exact source-step cleanup receipt chain and
validates the ledger hash before the wait runtime is allowed to trust it.
"""
from __future__ import annotations

from typing import Any

from .process_step_execution import (
    PROCESS_STEP_FACT_MODEL_VERSION,
    PROCESS_STEP_LEDGER_SCHEMA,
    _canonical_step_fact,
    _stable_hash,
)

PERSISTED_TERMINATION_AUTHORITY = "process_step_ledger_cleanup_receipts"
TERMINATION_RECOVERY_SCHEMA = (
    "qualibug.process-graph-wait-termination-recovery.v1"
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique_texts(values: list[Any] | None) -> list[str]:
    out: list[str] = []
    for value in list(values or []):
        text = _text(value)
        if text and text not in out:
            out.append(text)
    return out


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _ordered_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            _safe_int(row.get("step_ordinal")),
            _text(row.get("step_id")),
        ),
    )


def _identity_value(rows: list[dict[str, Any]], field: str) -> tuple[str, str]:
    values = {_text(row.get(field)) for row in rows if _text(row.get(field))}
    if len(values) > 1:
        return "", f"persisted_ledger_{field}_ambiguous"
    return (next(iter(values)) if values else ""), ""


def _fact_snapshot(
    *, observations: dict[str, Any], rows: list[dict[str, Any]], ledger_id: str
) -> tuple[dict[str, Any], str]:
    ordered = _ordered_rows(rows)
    identities: dict[str, str] = {}
    for field in (
        "campaign_id",
        "run_id",
        "obligation_id",
        "experiment_id",
        "fixture_id",
        "protocol_id",
    ):
        value, error = _identity_value(ordered, field)
        if error:
            return {}, error
        identities[field] = value

    rejections = [
        dict(row)
        for row in _list(
            observations.get("process_step_receipt_scope_rejections")
        )
        if isinstance(row, dict)
    ]
    rejections.sort(
        key=lambda row: (
            _text(row.get("step_id")),
            _text(row.get("field")),
            _text(row.get("receipt_id")),
            _text(row.get("reason_code")),
        )
    )
    canonical_rows = [_canonical_step_fact(row) for row in ordered]
    step_ids = [_text(row.get("step_id")) for row in ordered]
    attempted = [
        step_id
        for step_id, row in zip(step_ids, ordered)
        if step_id and row.get("transport_attempted") is True
    ]
    executed = [
        step_id
        for step_id, row in zip(step_ids, ordered)
        if step_id
        and row.get("response_received") is True
        and _text(
            row.get("final_status") or row.get("final_step_status")
        ).upper()
        == "EXECUTED"
    ]
    accepted = [
        step_id
        for step_id, row in zip(step_ids, ordered)
        if step_id and row.get("operation_accepted") is True
    ]
    completed = [
        step_id
        for step_id, row in zip(step_ids, ordered)
        if step_id and _text(row.get("semantic_step_status")) == "TARGET_REACHED"
    ]
    failed = [
        step_id
        for step_id, row in zip(step_ids, ordered)
        if step_id and row.get("step_failed") is True
    ]
    return {
        "schema_version": PROCESS_STEP_LEDGER_SCHEMA,
        "fact_model_version": PROCESS_STEP_FACT_MODEL_VERSION,
        "ledger_id": ledger_id,
        **identities,
        "required_step_ids": _unique_texts(
            _list(observations.get("required_step_ids"))
        ),
        "attempted_step_ids": attempted,
        "executed_step_ids": executed,
        "accepted_step_ids": accepted,
        "completed_step_ids": completed,
        "failed_step_ids": failed,
        "rows": canonical_rows,
        "receipt_scope_rejections": rejections,
    }, ""


def _validate_ledger(
    *, observations: dict[str, Any], rows: list[dict[str, Any]]
) -> tuple[dict[str, Any], str]:
    ledger_id = _text(observations.get("process_step_ledger_id"))
    ledger_hash = _text(observations.get("process_step_ledger_hash"))
    if not ledger_id:
        return {}, "persisted_ledger_id_missing"
    if not ledger_hash:
        return {}, "persisted_ledger_hash_missing"

    for row in rows:
        if _text(row.get("process_step_ledger_id")) != ledger_id:
            return {}, "persisted_ledger_row_id_mismatch"
        if _text(row.get("process_step_ledger_hash")) != ledger_hash:
            return {}, "persisted_ledger_row_hash_mismatch"
        step_fact_hash = _text(row.get("step_fact_hash"))
        if not step_fact_hash:
            return {}, "persisted_step_fact_hash_missing"
        if step_fact_hash != _stable_hash(_canonical_step_fact(row)):
            return {}, "persisted_step_fact_hash_mismatch"

    snapshot, error = _fact_snapshot(
        observations=observations,
        rows=rows,
        ledger_id=ledger_id,
    )
    if error:
        return {}, error
    if _stable_hash(snapshot) != ledger_hash:
        return {}, "persisted_ledger_hash_mismatch"
    return {
        "ledger_id": ledger_id,
        "ledger_hash": ledger_hash,
        "fact_snapshot": snapshot,
    }, ""


def _receipt_bodies(observations: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in (
        "contract_evidence_receipts",
        "persisted_contract_evidence_receipts",
    ):
        for raw in _list(observations.get(key)):
            if isinstance(raw, dict):
                rows.append(dict(raw))
    return rows


def recover_persisted_cleanup_receipts(
    *,
    observations: dict[str, Any],
    source_step_id: str,
    experiment_id: str,
    obligation_id: str,
    campaign_id: str,
    execution_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """Return ledger-scoped persisted cleanup receipts and recovery metadata.

    An empty result without an error means no persisted authority applies to the
    current execution. Any malformed current-execution authority fails closed.
    """
    persisted_rows = [
        dict(row)
        for row in _list(observations.get("process_step_receipts"))
        if isinstance(row, dict)
    ]
    if not persisted_rows:
        return [], {}, ""

    source = _text(source_step_id)
    eid = _text(experiment_id)
    oid = _text(obligation_id)
    cid = _text(campaign_id)
    run_id = _text(execution_id)
    matching_source_rows = [
        row
        for row in persisted_rows
        if _text(row.get("step_id")) == source
        and _text(row.get("experiment_id")) == eid
        and _text(row.get("obligation_id")) == oid
        and _text(row.get("campaign_id")) == cid
        and _text(row.get("run_id")) == run_id
    ]
    if not matching_source_rows:
        return [], {}, ""

    metadata: dict[str, Any] = {
        "schema_version": TERMINATION_RECOVERY_SCHEMA,
        "authority": PERSISTED_TERMINATION_AUTHORITY,
        "source_step_id": source,
        "experiment_id": eid,
        "obligation_id": oid,
        "campaign_id": cid,
        "execution_id": run_id,
    }
    if len(matching_source_rows) != 1:
        return [], metadata, "persisted_source_step_cardinality_invalid"

    ledger, error = _validate_ledger(
        observations=observations,
        rows=persisted_rows,
    )
    if error:
        return [], metadata, error

    source_row = matching_source_rows[0]
    scoped_receipt_ids = sorted(
        _unique_texts(_list(source_row.get("scoped_cleanup_receipt_ids")))
    )
    metadata.update(
        {
            "ledger_id": _text(ledger.get("ledger_id")),
            "ledger_hash": _text(ledger.get("ledger_hash")),
            "source_step_fact_hash": _text(source_row.get("step_fact_hash")),
            "cleanup_receipt_ids": list(scoped_receipt_ids),
        }
    )
    if not scoped_receipt_ids:
        return [], metadata, ""

    bodies_by_id: dict[str, dict[str, Any]] = {}
    for receipt in _receipt_bodies(observations):
        receipt_id = _text(receipt.get("receipt_id"))
        if receipt_id not in scoped_receipt_ids:
            continue
        previous = bodies_by_id.get(receipt_id)
        if previous is not None and previous != receipt:
            return [], metadata, f"persisted_cleanup_receipt_conflict:{receipt_id}"
        bodies_by_id[receipt_id] = receipt

    missing = [
        receipt_id
        for receipt_id in scoped_receipt_ids
        if receipt_id not in bodies_by_id
    ]
    if missing:
        return (
            [],
            metadata,
            "persisted_cleanup_receipt_body_missing:" + ",".join(missing),
        )

    receipts = [bodies_by_id[receipt_id] for receipt_id in scoped_receipt_ids]
    metadata["recovered_receipt_count"] = len(receipts)
    observations["process_graph_wait_termination_recovery"] = dict(metadata)
    return receipts, metadata, ""


__all__ = [
    "PERSISTED_TERMINATION_AUTHORITY",
    "TERMINATION_RECOVERY_SCHEMA",
    "recover_persisted_cleanup_receipts",
]
