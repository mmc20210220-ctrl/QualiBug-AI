"""Lifecycle adapter around the existing sequential plan executor.

The core executor keeps request logic. This adapter preserves a ledger created
at experiment entry and merges core step facts through public ledger methods,
so fixture, barrier, business, and cleanup stages share one public authority.
"""
from __future__ import annotations

import sys
from typing import Any, Callable

from .experiment_plan_executor import (
    execute_non_barrier_plans as _execute_non_barrier_plans,
)
from .process_step_execution import (
    ProcessStepLedger,
    attach_ledger_refs_to_observations,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _ledger(value: Any) -> ProcessStepLedger | None:
    return value if isinstance(value, ProcessStepLedger) else None


def _copy_step_rows(
    source: ProcessStepLedger,
    target: ProcessStepLedger,
) -> None:
    existing = set(target.recorded_step_ids())
    for row in source.all_rows():
        step_id = str(row.get("step_id") or "").strip()
        if not step_id or step_id in existing:
            continue
        target.record_step_execution(
            step_id=step_id,
            phase=str(row.get("phase") or ""),
            operation_ref=str(
                row.get("operation_ref") or row.get("operation_id") or ""
            ),
            actor_ref=str(row.get("actor_ref") or ""),
            runtime_identity=_dict(row.get("runtime_identity")),
            request_receipt_id=str(row.get("request_receipt_id") or ""),
            response_receipt_id=str(row.get("response_receipt_id") or ""),
            transport_receipt_id=str(row.get("transport_receipt_id") or ""),
            before_state_receipt_id=str(
                row.get("before_state_receipt_id") or ""
            ),
            after_state_receipt_id=str(
                row.get("after_state_receipt_id") or ""
            ),
            observer_receipt_ids=list(
                row.get("scoped_observation_receipt_ids")
                or row.get("observation_receipt_ids")
                or []
            ),
            oracle_receipt_ids=list(
                row.get("scoped_oracle_receipt_ids")
                or row.get("oracle_receipt_ids")
                or []
            ),
            cleanup_contract_id=str(
                row.get("cleanup_contract_id") or ""
            ),
            cleanup_receipt_ids=list(
                row.get("scoped_cleanup_receipt_ids")
                or row.get("cleanup_receipt_ids")
                or []
            ),
            status_code=int(row.get("status_code") or 0),
            final_status=str(
                row.get("final_step_status")
                or row.get("final_status")
                or "EXECUTED"
            ),
            mutation_occurred=row.get("mutation_occurred"),
            target_reached=row.get("target_reached"),
        )
        existing.add(step_id)


def _copy_timeline(
    source: ProcessStepLedger,
    target: ProcessStepLedger,
) -> None:
    existing = {
        (
            str(row.get("step_id") or ""),
            str(row.get("phase") or ""),
            str(row.get("event_type") or ""),
            str(row.get("receipt_id") or ""),
        )
        for row in target.timeline()
    }
    for row in source.timeline():
        key = (
            str(row.get("step_id") or ""),
            str(row.get("phase") or ""),
            str(row.get("event_type") or ""),
            str(row.get("receipt_id") or ""),
        )
        if key in existing:
            continue
        target.record_timeline_event(
            step_id=key[0],
            phase=key[1],
            event_type=key[2],
            operation_ref=str(row.get("operation_ref") or ""),
            actor_ref=str(row.get("actor_ref") or ""),
            receipt_id=key[3],
        )
        existing.add(key)



def current_raw_plan_delegate() -> Callable[..., dict[str, Any]]:
    """Return the transport delegate wrapped by the lifecycle authority."""
    return _execute_non_barrier_plans


def install_raw_plan_delegate(
    delegate: Callable[..., dict[str, Any]],
) -> Callable[..., dict[str, Any]]:
    """Install one raw transport wrapper without replacing public authority.

    Runtime plugins may specialize transport, but every public executor alias
    must continue through :func:`execute_non_barrier_plans` so the entry ledger
    remains authoritative.
    """
    global _execute_non_barrier_plans
    _execute_non_barrier_plans = delegate

    from . import experiment_plan_executor as raw_plan_module

    raw_plan_module.execute_non_barrier_plans = execute_non_barrier_plans
    for module_name in (
        f"{__package__}.experiment_executor_core",
        f"{__package__}.experiment_executor_governance",
        f"{__package__}.experiment_executor",
    ):
        module = sys.modules.get(module_name)
        if module is not None:
            setattr(module, "execute_non_barrier_plans", execute_non_barrier_plans)
    return delegate

def execute_non_barrier_plans(**kwargs: Any) -> dict[str, Any]:
    observations = _dict(kwargs.get("observations"))
    entry_ledger = _ledger(observations.get("process_step_ledger"))

    result = _dict(_execute_non_barrier_plans(**kwargs))
    core_ledger = _ledger(
        result.get("process_step_ledger")
        or observations.get("process_step_ledger")
    )
    authority = entry_ledger or core_ledger
    if entry_ledger is not None and core_ledger is not None:
        _copy_step_rows(core_ledger, entry_ledger)
        _copy_timeline(core_ledger, entry_ledger)
        if core_ledger.required_step_ids:
            entry_ledger.set_required_step_ids(core_ledger.required_step_ids)
        authority = entry_ledger

    if authority is not None:
        attach_ledger_refs_to_observations(observations, authority)
        result.update(
            {
                "process_step_ledger": authority,
                "process_step_ledger_id": authority.ledger_id,
                "process_step_ledger_hash": authority.compute_hash(),
                "required_step_ids": authority.required_step_ids,
                "planned_step_ids": authority.required_step_ids,
                "executed_step_ids": authority.executed_step_ids(),
                "process_timeline": authority.build_timeline_receipt(),
            }
        )
    return result
