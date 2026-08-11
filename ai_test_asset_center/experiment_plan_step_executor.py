"""Public sequential step-kernel facade with graph async gating.

The original transport/governance implementation remains unchanged in
``experiment_plan_step_executor_core``. Graph scheduling invokes this public
kernel one node at a time. A compile-frozen state wait or event transition is
executed before business transport; delegation occurs only after convergence.

The existing ProcessStepLedger records the async receipt before copied child
transport events. Event receipts are published to the existing exact-step
observation receipt collection, not to a second ledger or Finalizer bridge.
Ordinary plans delegate unchanged.
"""
from __future__ import annotations

from typing import Any

from . import experiment_plan_step_executor_core as _core
from .contract_oracles import build_contract_evidence_receipt
from .process_graph_event_transition import (
    RECEIPT_SCHEMA_VERSION as EVENT_RECEIPT_SCHEMA_VERSION,
)
from .process_graph_executor_support import copy_subledger_rows
from .process_graph_wait_contract import (
    STATUS_BLOCKED as WAIT_BLOCKED,
    STATUS_CONVERGED as WAIT_CONVERGED,
    STATUS_NOT_REQUIRED as WAIT_NOT_REQUIRED,
    WAIT_CONTRACT_INVALID,
    execute_process_graph_wait,
)
from .process_graph_wait_termination import (
    WAIT_TERMINATION_EPOCH_ACTIVE,
    WAIT_TERMINATION_RECEIPT_INVALID,
    resolve_wait_termination_receipt,
)
from .process_step_execution import (
    ProcessStepLedger,
    attach_ledger_refs_to_observations,
)


WAIT_DISPATCH_SCOPE_INVALID = "PROCESS_GRAPH_WAIT_DISPATCH_SCOPE_INVALID"
for _name in dir(_core):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_core, _name)


def _sync_core_hooks() -> None:
    for name in (
        "_http_request",
        "_run_http_step",
        "execute_governed_control_write",
        "sandbox_write_allowed",
    ):
        if name in globals():
            setattr(_core, name, globals()[name])


def _required_step_ids(activation_requirements: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for phase in ("control", "treatment"):
        for value in list(activation_requirements.get(phase) or []):
            token = str(value or "").strip()
            if token and token not in values:
                values.append(token)
    return values


def _is_event_receipt(receipt: dict[str, Any]) -> bool:
    return str(receipt.get("schema_version") or "").strip() == (
        EVENT_RECEIPT_SCHEMA_VERSION
    )



def _append_unique_receipt(
    observations: dict[str, Any],
    key: str,
    receipt: dict[str, Any],
) -> None:
    rows = observations.setdefault(key, [])
    if not isinstance(rows, list):
        rows = []
        observations[key] = rows
    receipt_id = str(receipt.get("receipt_id") or "").strip()
    if receipt_id and any(
        isinstance(row, dict)
        and str(row.get("receipt_id") or "").strip() == receipt_id
        for row in rows
    ):
        return
    rows.append(receipt)


def _append_async_receipt_projection(
    observations: dict[str, Any],
    receipt: dict[str, Any],
) -> None:
    _append_unique_receipt(observations, "process_graph_wait_receipts", receipt)
    if _is_event_receipt(receipt):
        _append_unique_receipt(
            observations,
            "process_graph_async_transition_receipts",
            receipt,
        )
        # The exact-scope Finalizer already consumes this canonical collection
        # and binds receipts by their explicit step_id.
        _append_unique_receipt(
            observations,
            "process_step_observation_receipts",
            receipt,
        )


def _new_wait_ledger(kwargs: dict[str, Any]) -> ProcessStepLedger:
    observations = kwargs.get("observations")
    obs = observations if isinstance(observations, dict) else {}
    fixture = obs.get("disposable_fixture_contract")
    fixture_row = fixture if isinstance(fixture, dict) else {}
    protocol = obs.get("protocol")
    protocol_row = protocol if isinstance(protocol, dict) else {}
    return ProcessStepLedger(
        experiment_id=str(kwargs.get("eid") or "").strip(),
        fixture_id=str(fixture_row.get("fixture_id") or "").strip(),
        campaign_id=str(
            kwargs.get("resolved_campaign_id")
            or kwargs.get("campaign_id")
            or ""
        ).strip(),
        run_id=str(kwargs.get("resolved_execution_id") or "").strip(),
        obligation_id=str(kwargs.get("oid") or "").strip(),
        protocol_id=str(
            obs.get("protocol_id") or protocol_row.get("protocol_id") or ""
        ).strip(),
        required_step_ids=_required_step_ids(
            kwargs.get("activation_requirements")
            if isinstance(kwargs.get("activation_requirements"), dict)
            else {}
        ),
    )


def _blocked_wait_result(
    *,
    kwargs: dict[str, Any],
    step: dict[str, Any],
    wait_receipt: dict[str, Any],
    reason_code: str,
    detail: str,
) -> dict[str, Any]:
    observations = kwargs.get("observations")
    obs = observations if isinstance(observations, dict) else {}
    ledger = _new_wait_ledger(kwargs)
    step_id = str(step.get("step_id") or "").strip()
    operation_ref = str(step.get("operation_ref") or "").strip()
    actor_ref = str(step.get("actor_ref") or "").strip()
    receipt_id = str(wait_receipt.get("receipt_id") or "").strip()
    event_receipt = _is_event_receipt(wait_receipt)
    ledger.record_step_execution(
        step_id=step_id,
        phase="treatment",
        operation_ref=operation_ref,
        actor_ref=actor_ref,
        runtime_identity=(
            kwargs.get("runtime_bindings")
            if isinstance(kwargs.get("runtime_bindings"), dict)
            else {}
        ),
        observer_receipt_ids=[receipt_id] if receipt_id else [],
        final_status="BLOCKED",
        operation_accepted=False,
        business_effect_observed=False,
        target_reached=False,
    )
    ledger.record_timeline_event(
        step_id=step_id,
        phase="event" if event_receipt else "wait",
        event_type=("ASYNC_EVENT_FAILED" if event_receipt else "WAIT_FAILED"),
        operation_ref=str(
            wait_receipt.get("observer_operation_ref") or ""
        ).strip(),
        actor_ref=actor_ref,
        receipt_id=receipt_id,
    )
    _append_async_receipt_projection(obs, wait_receipt)
    attach_ledger_refs_to_observations(obs, ledger)
    contract_receipt = build_contract_evidence_receipt(
        kind="treatment",
        experiment_id=str(kwargs.get("eid") or "").strip(),
        obligation_id=str(kwargs.get("oid") or "").strip(),
        campaign_id=str(kwargs.get("resolved_campaign_id") or "").strip(),
        execution_id=str(kwargs.get("resolved_execution_id") or "").strip(),
        subject_id=step_id,
        status="BLOCKED",
        evidence={
            "request_reached_transport": False,
            "reason_code": reason_code,
            "detail": detail,
            "wait_receipt_id": receipt_id,
            "wait_attempt_count": int(wait_receipt.get("attempt_count") or 0),
            "wait_timed_out": wait_receipt.get("timed_out") is True,
            "async_transition_kind": (
                str(wait_receipt.get("delivery_kind") or "").strip()
                if event_receipt
                else "state_wait"
            ),
            "async_semantic_status": str(
                wait_receipt.get("semantic_status") or ""
            ).strip(),
            "observed_unique_event_count": int(
                wait_receipt.get("observed_unique_event_count") or 0
            ),
            "correlation_identity_mismatch_count": int(
                wait_receipt.get("correlation_identity_mismatch_count") or 0
            ),
            "event_identity_type_conflict_count": int(
                wait_receipt.get("event_identity_type_conflict_count") or 0
            ),
            "idempotency_mismatch_count": int(
                wait_receipt.get("idempotency_mismatch_count") or 0
            ),
            "retry_limit_violation_count": int(
                wait_receipt.get("retry_limit_violation_count") or 0
            ),
            "termination_epoch_authority": str(
                wait_receipt.get("termination_epoch_authority") or ""
            ).strip(),
            "termination_epoch_contract_fingerprint": str(
                wait_receipt.get(
                    "termination_epoch_contract_fingerprint"
                )
                or ""
            ).strip(),
            "termination_cleanup_receipt_ids": list(
                wait_receipt.get("termination_cleanup_receipt_ids") or []
            ),
        },
    )
    blocked_step = {
        "phase": "treatment",
        "step_id": step_id,
        "status": "blocked_request",
        "reason": reason_code,
        "detail": detail,
        "method": str(step.get("method") or "").upper(),
        "path": str(step.get("path") or step.get("path_template") or ""),
        "status_code": 0,
        "actor_ref": actor_ref,
        "operation_ref": operation_ref,
        "wait_receipt_id": receipt_id,
        "async_transition_kind": (
            str(wait_receipt.get("delivery_kind") or "").strip()
            if event_receipt
            else "state_wait"
        ),
    }
    result = {
        "steps": [blocked_step],
        "contract_evidence_receipts": [contract_receipt],
        "request_bodies_for_cleanup": {},
        "pre_transport_block_reasons": [f"{reason_code}:{detail}"],
        "cleanup_failures": int(kwargs.get("cleanup_failures") or 0),
        "process_step_ledger": ledger,
        "process_step_ledger_id": ledger.ledger_id,
        "process_step_ledger_hash": ledger.compute_hash(),
        "process_timeline": ledger.build_timeline_receipt(),
        "required_step_ids": list(ledger.required_step_ids),
        "planned_step_ids": list(ledger.required_step_ids),
        "executed_step_ids": ledger.executed_step_ids(),
        "process_graph_wait_receipts": [wait_receipt],
    }
    if event_receipt:
        result["process_graph_async_transition_receipts"] = [wait_receipt]
    return result


def _wait_step(kwargs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    control = [
        row
        for row in list(kwargs.get("control_plan") or [])
        if isinstance(row, dict)
    ]
    treatment = [
        row
        for row in list(kwargs.get("treatment_plan") or [])
        if isinstance(row, dict)
    ]
    waiting = [
        row for row in treatment if isinstance(row.get("wait_contract"), dict)
    ]
    if not waiting:
        return {}, {}
    if control or len(treatment) != 1 or len(waiting) != 1:
        step = waiting[0]
        return step, {
            "schema_version": "qualibug.process-graph-wait-receipt.v1",
            "status": WAIT_BLOCKED,
            "reason_code": WAIT_DISPATCH_SCOPE_INVALID,
            "detail": "graph_wait_requires_single_node_kernel_dispatch",
            "step_id": str(step.get("step_id") or "").strip(),
            "wait_id": str(
                (step.get("wait_contract") or {}).get("wait_id") or ""
            ).strip(),
            "attempt_count": 0,
            "converged": False,
            "timed_out": False,
        }
    step = waiting[0]
    graph = step.get("_execution_graph")
    graph_row = graph if isinstance(graph, dict) else {}
    if not graph_row:
        return step, {
            "schema_version": "qualibug.process-graph-wait-receipt.v1",
            "status": WAIT_BLOCKED,
            "reason_code": WAIT_CONTRACT_INVALID,
            "detail": "wait_step_execution_graph_missing",
            "step_id": str(step.get("step_id") or "").strip(),
            "attempt_count": 0,
            "converged": False,
            "timed_out": False,
        }
    termination_receipt = resolve_wait_termination_receipt(
        step=step,
        observations=(
            kwargs.get("observations")
            if isinstance(kwargs.get("observations"), dict)
            else {}
        ),
        experiment_id=str(kwargs.get("eid") or "").strip(),
        obligation_id=str(kwargs.get("oid") or "").strip(),
        campaign_id=str(kwargs.get("resolved_campaign_id") or "").strip(),
        execution_id=str(kwargs.get("resolved_execution_id") or "").strip(),
    )
    if termination_receipt:
        return step, termination_receipt
    receipt = execute_process_graph_wait(
        graph=graph_row,
        step=step,
        context={
            "bindings": (
                kwargs.get("runtime_bindings")
                if isinstance(kwargs.get("runtime_bindings"), dict)
                else {}
            ),
            "base_url": str(kwargs.get("base_url") or "").strip(),
        },
        actors=(
            kwargs.get("actors")
            if isinstance(kwargs.get("actors"), dict)
            else {}
        ),
        tokens=(
            kwargs.get("tokens")
            if isinstance(kwargs.get("tokens"), dict)
            else {}
        ),
    )
    return step, receipt


def execute_non_barrier_plans(**kwargs: Any) -> dict[str, Any]:
    """Gate one graph node on its compiled async transition."""
    _sync_core_hooks()
    step, wait_receipt = _wait_step(kwargs)
    if not step:
        return _core.execute_non_barrier_plans(**kwargs)

    wait_status = str(wait_receipt.get("status") or "").strip()
    if wait_status == WAIT_BLOCKED:
        reason = str(
            wait_receipt.get("reason_code") or WAIT_CONTRACT_INVALID
        ).strip()
        detail = str(wait_receipt.get("detail") or reason).strip()
        return _blocked_wait_result(
            kwargs=kwargs,
            step=step,
            wait_receipt=wait_receipt,
            reason_code=reason,
            detail=detail,
        )
    if wait_status not in {WAIT_CONVERGED, WAIT_NOT_REQUIRED}:
        return _blocked_wait_result(
            kwargs=kwargs,
            step=step,
            wait_receipt=wait_receipt,
            reason_code=WAIT_CONTRACT_INVALID,
            detail=f"unexpected_wait_status:{wait_status or '<empty>'}",
        )

    master = _new_wait_ledger(kwargs)
    receipt_id = str(wait_receipt.get("receipt_id") or "").strip()
    event_receipt = _is_event_receipt(wait_receipt)
    if wait_status == WAIT_CONVERGED:
        master.record_timeline_event(
            step_id=str(step.get("step_id") or "").strip(),
            phase="event" if event_receipt else "wait",
            event_type=(
                "ASYNC_EVENT_VERIFIED" if event_receipt else "WAIT_CONVERGED"
            ),
            operation_ref=str(
                wait_receipt.get("observer_operation_ref") or ""
            ).strip(),
            actor_ref=str(step.get("actor_ref") or "").strip(),
            receipt_id=receipt_id,
        )
        observations = kwargs.get("observations")
        if isinstance(observations, dict):
            _append_async_receipt_projection(observations, wait_receipt)

    result = _core.execute_non_barrier_plans(**kwargs)
    child = result.get("process_step_ledger")
    copy_subledger_rows(master, child)
    step_id = str(step.get("step_id") or "").strip()
    if receipt_id:
        master.append_scoped_receipt_ref(
            step_id=step_id,
            field="observer_receipt_ids",
            receipt_id=receipt_id,
            receipt_step_id=step_id,
        )
    observations = kwargs.get("observations")
    if isinstance(observations, dict):
        attach_ledger_refs_to_observations(observations, master)
    result.update(
        {
            "process_step_ledger": master,
            "process_step_ledger_id": master.ledger_id,
            "process_step_ledger_hash": master.compute_hash(),
            "process_timeline": master.build_timeline_receipt(),
            "required_step_ids": list(master.required_step_ids),
            "planned_step_ids": list(master.required_step_ids),
            "executed_step_ids": master.executed_step_ids(),
            "process_graph_wait_receipts": [wait_receipt],
        }
    )
    if event_receipt:
        result["process_graph_async_transition_receipts"] = [wait_receipt]
    return result


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__") and name not in {"_core", "_name"}
)
