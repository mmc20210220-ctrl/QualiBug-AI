"""Semantic completion projection for process-step ledger rows.

Ledger rows keep raw execution facts. This module derives the distinct
attempted, executed, accepted, completed, failed, and pending sets used by
lifecycle and completion gates without modifying stored rows.
"""
from __future__ import annotations

from typing import Any

from .process_step_execution import ProcessStepLedger


_VERDICT_SOURCES = {
    "observer",
    "state_observer",
    "oracle",
    "postcondition_oracle",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _accepted(row: dict[str, Any]) -> bool:
    try:
        code = int(row.get("status_code") or 0)
    except (TypeError, ValueError):
        code = 0
    return (
        row.get("operation_accepted") is True
        or (
            _text(row.get("final_status")).upper() == "EXECUTED"
            and 200 <= code < 400
        )
    )


def _explicit_verdict(row: dict[str, Any]) -> bool | None:
    source = _text(row.get("semantic_verdict_source")).lower()
    receipt_id = _text(row.get("semantic_verdict_receipt_id"))
    verdict = row.get("target_reached")
    if source not in _VERDICT_SOURCES or not receipt_id:
        return None
    return verdict if isinstance(verdict, bool) else None


def project_step_sets(ledger: ProcessStepLedger) -> dict[str, list[str]]:
    """Project every lifecycle-relevant step set without semantic aliasing."""
    recorded: list[str] = []
    attempted: list[str] = []
    executed: list[str] = []
    accepted: list[str] = []
    completed: list[str] = []
    failed: list[str] = []
    pending: list[str] = []

    for row in ledger.all_rows():
        step_id = _text(row.get("step_id"))
        if not step_id:
            continue
        recorded.append(step_id)
        response_received = row.get("response_received") is True
        transport_attempted = (
            row.get("transport_attempted") is True
            or response_received
            or bool(_text(row.get("request_receipt_id")))
            or bool(_text(row.get("transport_receipt_id")))
        )
        if transport_attempted:
            attempted.append(step_id)
        if (
            response_received
            and _text(row.get("final_status")).upper() == "EXECUTED"
        ):
            executed.append(step_id)

        is_accepted = _accepted(row)
        if is_accepted:
            accepted.append(step_id)
        verdict = _explicit_verdict(row)
        if is_accepted and verdict is True:
            completed.append(step_id)
        elif verdict is False:
            failed.append(step_id)
        elif not is_accepted and _text(row.get("final_status")).upper() in {
            "FAILED",
            "BLOCKED",
            "EXECUTED",
        }:
            failed.append(step_id)
        elif is_accepted:
            pending.append(step_id)

    return {
        "recorded_step_ids": recorded,
        "attempted_step_ids": attempted,
        "executed_step_ids": executed,
        "accepted_step_ids": accepted,
        "completed_step_ids": completed,
        "failed_step_ids": failed,
        "pending_semantic_step_ids": pending,
    }


def apply_semantic_verdict(
    ledger: ProcessStepLedger,
    *,
    step_id: str,
    receipt_step_id: str,
    receipt_id: str,
    source: str,
    target_reached: bool,
) -> bool:
    """Attach one explicitly scoped semantic verdict to its matching row."""
    normalized_step_id = _text(step_id)
    normalized_receipt_step_id = _text(receipt_step_id)
    normalized_receipt_id = _text(receipt_id)
    normalized_source = _text(source).lower()
    row = ledger.get_step_row(normalized_step_id)
    if (
        row is None
        or normalized_step_id != normalized_receipt_step_id
        or not normalized_receipt_id
        or normalized_source not in _VERDICT_SOURCES
        or not isinstance(target_reached, bool)
    ):
        return False

    field = (
        "observation_receipt_ids"
        if normalized_source in {"observer", "state_observer"}
        else "oracle_receipt_ids"
    )
    if not ledger.append_scoped_receipt_ref(
        step_id=normalized_step_id,
        receipt_step_id=normalized_receipt_step_id,
        field=field,
        receipt_id=normalized_receipt_id,
    ):
        return False

    row["semantic_verdict_receipt_id"] = normalized_receipt_id
    row["semantic_verdict_source"] = normalized_source
    row["target_state_observed"] = True
    row["business_effect_observed"] = True
    row["target_reached"] = target_reached
    row["semantic_step_status"] = (
        "TARGET_REACHED" if target_reached else "TARGET_NOT_REACHED"
    )
    row["step_completed"] = bool(target_reached and _accepted(row))
    row["step_failed"] = bool(not target_reached or not _accepted(row))
    return True
