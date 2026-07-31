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
from .process_step_semantic_projection import project_step_sets
from .process_step_semantic_view import ProcessStepSemanticView


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _plan_step_ids(rows: list[Any], phase: str) -> list[str]:
    result: list[str] = []
    for index, raw in enumerate(rows, 1):
        step = _dict(raw)
        step_id = _text(step.get("step_id") or step.get("id"))
        if not step_id:
            operation_ref = _text(step.get("operation_ref"))
            step_id = f"{phase}:{operation_ref or 'operation'}:{index}"
        if step_id not in result:
            result.append(step_id)
    return result


def _required_step_ids(experiment: dict[str, Any]) -> list[str]:
    required: list[str] = []
    for phase in ("control", "treatment"):
        for step_id in _plan_step_ids(
            _list(experiment.get(f"{phase}_plan")), phase
        ):
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
    ledger.precondition_step_ids = _plan_step_ids(
        _list(exp.get("precondition_plan")), "precondition"
    )
    ledger.record_timeline_event(
        step_id="experiment",
        phase="lifecycle",
        event_type=EVENT_STEP_READY,
        receipt_id="experiment_execution_started",
    )
    return ledger


def _finalizer_inputs_sealed(ledger: ProcessStepLedger) -> bool:
    return any(
        _text(event.get("phase")) == "finalizer"
        and _text(event.get("receipt_id")) == "finalizer_inputs_sealed"
        for event in ledger.timeline()
        if isinstance(event, dict)
    )


def _precondition_projection(ledger: ProcessStepLedger) -> dict[str, Any]:
    required = [
        _text(step_id)
        for step_id in list(getattr(ledger, "precondition_step_ids", []) or [])
        if _text(step_id)
    ]
    completed = {
        _text(event.get("step_id"))
        for event in ledger.timeline()
        if isinstance(event, dict)
        and _text(event.get("phase")) in {"precondition", "fixture"}
        and _text(event.get("event_type")) == EVENT_STEP_COMPLETED
        and _text(event.get("step_id"))
    }
    missing = [step_id for step_id in required if step_id not in completed]
    return {
        "required": bool(required),
        "required_step_ids": required,
        "completed_step_ids": [
            step_id for step_id in required if step_id in completed
        ],
        "missing_step_ids": missing,
        "established": not missing,
    }


def attach_lifecycle_ledger(
    observations: dict[str, Any],
    ledger: ProcessStepLedger,
) -> dict[str, Any]:
    target = attach_ledger_refs_to_observations(observations, ledger)
    projection = project_step_sets(ledger)
    target["process_step_semantic_projection"] = projection
    target["recorded_step_ids"] = projection["recorded_step_ids"]
    target["accepted_step_ids"] = projection["accepted_step_ids"]
    target["executed_step_ids"] = projection["completed_step_ids"]
    target["completed_step_ids"] = projection["completed_step_ids"]
    target["failed_step_ids"] = projection["failed_step_ids"]
    target["pending_semantic_step_ids"] = projection[
        "pending_semantic_step_ids"
    ]
    precondition = _precondition_projection(ledger)
    target["state_precondition_receipt"] = precondition
    target["state_precondition_established"] = precondition["established"]
    if _finalizer_inputs_sealed(ledger):
        target["process_step_ledger"] = ProcessStepSemanticView(
            ledger,
            observations=target,
        )
        target["process_step_ledger_view"] = "semantic_completion"
    return target


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


def _attach_projection_to_result(
    ledger: ProcessStepLedger,
    row: dict[str, Any],
) -> dict[str, Any]:
    projection = project_step_sets(ledger)
    row["process_step_semantic_projection"] = projection
    row["recorded_step_ids"] = projection["recorded_step_ids"]
    row["accepted_step_ids"] = projection["accepted_step_ids"]
    row["executed_step_ids"] = projection["completed_step_ids"]
    row["completed_step_ids"] = projection["completed_step_ids"]
    row["failed_step_ids"] = projection["failed_step_ids"]
    row["pending_semantic_step_ids"] = projection[
        "pending_semantic_step_ids"
    ]
    row["state_precondition_receipt"] = _precondition_projection(ledger)
    row["state_precondition_established"] = row[
        "state_precondition_receipt"
    ]["established"]
    return row


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
    row["process_timeline"] = ledger.build_timeline_receipt()
    return _attach_projection_to_result(ledger, row)


def attach_lifecycle_to_result(
    ledger: ProcessStepLedger,
    result: dict[str, Any],
) -> dict[str, Any]:
    row = dict(_dict(result))
    row.setdefault("process_step_ledger_receipt", ledger.to_authority_dict())
    row.setdefault("process_step_ledger_id", ledger.ledger_id)
    row.setdefault("process_step_ledger_hash", ledger.compute_hash())
    row.setdefault("required_step_ids", ledger.required_step_ids)
    row.setdefault("process_timeline", ledger.build_timeline_receipt())
    return _attach_projection_to_result(ledger, row)
