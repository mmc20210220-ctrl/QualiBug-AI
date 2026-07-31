"""Experiment-wide ProcessStepLedger lifecycle utilities.

This module owns experiment-stage timeline facts and terminal propagation. It
does not execute fixtures, barriers, requests, observers, or cleanup. Business
step rows remain owned by the existing plan executor and are merged through the
lifecycle adapter.
"""
from __future__ import annotations

from typing import Any

from .process_step_execution import (
    EVENT_CLEANUP_COMPLETED,
    EVENT_STEP_COMPLETED,
    EVENT_STEP_FAILED,
    EVENT_STEP_READY,
    ProcessStepLedger,
    attach_ledger_refs_to_observations,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _required_step_ids(experiment: dict[str, Any]) -> list[str]:
    required: list[str] = []
    for phase in ("control", "treatment"):
        for index, raw in enumerate(_list(experiment.get(f"{phase}_plan")), 1):
            step = _dict(raw)
            step_id = _text(step.get("step_id") or step.get("id"))
            if not step_id:
                operation_ref = _text(step.get("operation_ref"))
                step_id = f"{phase}:{operation_ref or 'operation'}:{index}"
            if step_id not in required:
                required.append(step_id)
    return required


def new_experiment_lifecycle_ledger(
    experiment: dict[str, Any],
    *,
    experiment_id: str,
    obligation_id: str,
    campaign_id: str,
    run_id: str,
) -> ProcessStepLedger:
    exp = _dict(experiment)
    fixture_contract = _dict(exp.get("disposable_fixture_contract"))
    protocol = _dict(exp.get("protocol"))
    ledger = ProcessStepLedger(
        experiment_id=experiment_id,
        fixture_id=_text(
            fixture_contract.get("fixture_id") or exp.get("fixture_id")
        ),
        campaign_id=campaign_id,
        run_id=run_id,
        obligation_id=obligation_id,
        protocol_id=_text(exp.get("protocol_id") or protocol.get("protocol_id")),
        required_step_ids=_required_step_ids(exp),
    )
    ledger.record_timeline_event(
        step_id="experiment",
        phase="lifecycle",
        event_type=EVENT_STEP_READY,
        receipt_id="experiment_execution_started",
    )
    return ledger


def attach_lifecycle_ledger(
    observations: dict[str, Any],
    ledger: ProcessStepLedger,
) -> dict[str, Any]:
    return attach_ledger_refs_to_observations(observations, ledger)


def record_stage_event(
    ledger: ProcessStepLedger,
    *,
    phase: str,
    step_id: str,
    status: str,
    operation_ref: str = "",
    actor_ref: str = "",
    receipt_id: str = "",
) -> None:
    normalized = _text(status).upper()
    if phase == "cleanup" and normalized in {
        "COMPLETED",
        "CLEANED",
        "EXECUTED",
        "SUCCESS",
        "SUCCEEDED",
    }:
        event_type = EVENT_CLEANUP_COMPLETED
    elif normalized in {
        "COMPLETED",
        "EXECUTED",
        "SUCCESS",
        "SUCCEEDED",
        "RESOLVED",
        "READY",
        "BOUND",
        "OBSERVED",
    }:
        event_type = EVENT_STEP_COMPLETED
    else:
        event_type = EVENT_STEP_FAILED
    ledger.record_timeline_event(
        step_id=_text(step_id) or phase,
        phase=phase,
        event_type=event_type,
        operation_ref=_text(operation_ref),
        actor_ref=_text(actor_ref),
        receipt_id=_text(receipt_id) or normalized,
    )


def record_stage_rows(
    ledger: ProcessStepLedger,
    rows: list[Any],
    *,
    phase: str,
) -> None:
    for index, raw in enumerate(rows, 1):
        row = _dict(raw)
        if not row:
            continue
        status_code = 0
        try:
            status_code = int(
                row.get("status_code")
                or _dict(row.get("response")).get("status_code")
                or _dict(row.get("write")).get("status")
                or 0
            )
        except (TypeError, ValueError):
            status_code = 0
        status = _text(
            row.get("final_status")
            or row.get("status")
            or row.get("state")
        )
        blocked = bool(
            row.get("execution_blocked")
            or row.get("skipped_reason")
            or row.get("reason_code")
            or status_code >= 400
        )
        if blocked:
            status = "FAILED"
        elif not status:
            status = "COMPLETED" if status_code else "READY"
        record_stage_event(
            ledger,
            phase=_text(row.get("phase")) or phase,
            step_id=_text(
                row.get("step_id")
                or row.get("subject_id")
                or row.get("node_id")
                or row.get("fixture_id")
                or f"{phase}_{index}"
            ),
            status=status,
            operation_ref=_text(row.get("operation_ref")),
            actor_ref=_text(row.get("actor_ref")),
            receipt_id=_text(
                row.get("receipt_id")
                or row.get("execution_receipt_id")
                or row.get("reason_code")
                or row.get("skipped_reason")
            ),
        )


def terminal_result_with_lifecycle(
    ledger: ProcessStepLedger,
    result: dict[str, Any],
    *,
    phase: str,
    reason_code: str,
) -> dict[str, Any]:
    row = dict(_dict(result))
    record_stage_event(
        ledger,
        phase=phase,
        step_id=phase,
        status="FAILED",
        receipt_id=reason_code,
    )
    snapshot = ledger.to_authority_dict()
    row["process_step_ledger_receipt"] = snapshot
    row["process_step_ledger_id"] = ledger.ledger_id
    row["process_step_ledger_hash"] = ledger.compute_hash()
    row["required_step_ids"] = ledger.required_step_ids
    row["recorded_step_ids"] = ledger.recorded_step_ids()
    row["executed_step_ids"] = ledger.executed_step_ids()
    row["process_timeline"] = ledger.build_timeline_receipt()
    return row


def attach_lifecycle_to_result(
    ledger: ProcessStepLedger,
    result: dict[str, Any],
) -> dict[str, Any]:
    row = dict(_dict(result))
    row.setdefault("process_step_ledger_receipt", ledger.to_authority_dict())
    row.setdefault("process_step_ledger_id", ledger.ledger_id)
    row.setdefault("process_step_ledger_hash", ledger.compute_hash())
    row.setdefault("required_step_ids", ledger.required_step_ids)
    row.setdefault("recorded_step_ids", ledger.recorded_step_ids())
    row.setdefault("executed_step_ids", ledger.executed_step_ids())
    row.setdefault("process_timeline", ledger.build_timeline_receipt())
    return row
