"""Lifecycle adapter around the existing sequential plan executor.

The core executor keeps request logic. This adapter preserves a ledger created
at experiment entry and merges core step facts through public ledger methods,
so fixture, barrier, business, and cleanup stages share one public authority.

This boundary also seals request-build truth before finalization. A governed
write that explicitly reports ``write_request_attempt_count == 0`` is a
pre-transport block when it carries a non-transport reason; it must never fall
through to the finalizer's legacy ``HARNESS_REQUEST_BUILD_FAILED`` fallback.
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


_REQUEST_FIRST_LOSS_SCHEMA = "qualibug.request-build-first-loss.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ledger(value: Any) -> ProcessStepLedger | None:
    return value if isinstance(value, ProcessStepLedger) else None


def _request_first_loss_category(reason: str) -> str:
    """Classify one explicit pre-transport reason without guessing business semantics."""

    upper = _text(reason).upper()
    if any(
        marker in upper
        for marker in (
            "TARGET_POLICY",
            "READ_ONLY",
            "PRODUCTION",
            "ENVIRONMENT_",
            "SANDBOX_",
            "APPROVED_BASE_URL",
        )
    ):
        return "TARGET_POLICY"
    if any(
        marker in upper
        for marker in (
            "CREDENTIAL",
            "TOKEN",
            "MISSING_ACTOR",
            "ACTOR_IDENTITY",
            "AUTH_INJECTION",
        )
    ):
        return "ACTOR_CREDENTIAL"
    if any(
        marker in upper
        for marker in (
            "BINDING",
            "PLACEHOLDER",
            "REQUIRED_BODY",
            "FOREIGN_KEY",
            "IDENTITY_",
            "MISSING_REQUIRED",
            "MATERIALIZATION",
            "UNRESOLVED",
        )
    ):
        return "BINDING_OR_REQUEST_MATERIALIZATION"
    if any(
        marker in upper
        for marker in (
            "MISSING_OPERATION",
            "OPERATION_",
            "ROUTE_",
            "METHOD_",
            "PATH_MISSING",
        )
    ):
        return "OPERATION_CONTRACT"
    if "OBSERVER" in upper or "OBSERVATION_PATH" in upper:
        return "OBSERVER_CONTRACT"
    return "PRE_TRANSPORT_REQUEST_BUILD"


def _seal_pre_transport_request_blocks(
    result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Promote explicit zero-transport request blocks into the formal block list.

    The step kernel already rejects unresolved path/body/FK/identity conditions
    before transport. Historically one shape escaped the bridge: sandbox
    governance returned ``write_request_attempt_count=0`` with an explicit
    reason and no successful before-GET. Because no reason reached
    ``pre_transport_block_reasons``, finalization classified the experiment as
    ``HARNESS_REQUEST_BUILD_FAILED`` even though the harness had behaved
    correctly by refusing to send an unbuildable/unsafe request.

    Connection/timeout/network reasons are intentionally excluded; those are
    real runtime transport failures and remain eligible for HARNESS_*.
    """

    governed = dict(result)
    reasons = [
        _text(value)
        for value in list(governed.get("pre_transport_block_reasons") or [])
        if _text(value)
    ]
    rows: list[dict[str, Any]] = []

    for raw in list(governed.get("steps") or []):
        if not isinstance(raw, dict):
            continue
        step = raw
        governance = _dict(step.get("governance_receipt"))
        reason = ""
        write_attempts = 0
        write_status = 0
        if governance:
            try:
                write_attempts = int(
                    governance.get("write_request_attempt_count") or 0
                )
            except (TypeError, ValueError):
                write_attempts = 0
            write = _dict(governance.get("write"))
            try:
                write_status = int(write.get("status") or 0)
            except (TypeError, ValueError):
                write_status = 0
            reason = _text(
                governance.get("reason")
                or write.get("error")
                or step.get("reason")
                or step.get("detail")
            )
            is_explicit_zero_transport_block = (
                write_attempts == 0
                and write_status == 0
                and bool(reason)
            )
        else:
            reason = _text(
                step.get("skipped_reason")
                or step.get("reason")
                or step.get("detail")
            )
            try:
                step_status = int(step.get("status_code") or 0)
            except (TypeError, ValueError):
                step_status = 0
            is_explicit_zero_transport_block = bool(
                step_status == 0
                and reason
                and _text(step.get("status")).lower()
                in {"blocked_request", "blocked_write", "blocked", ""}
            )

        if not is_explicit_zero_transport_block:
            continue
        lower = reason.lower()
        if any(
            marker in lower
            for marker in ("connection", "timeout", "network", "transport_error")
        ):
            continue
        if reason not in reasons:
            reasons.append(reason)
        rows.append(
            {
                "step_id": _text(step.get("step_id") or step.get("subject_id")),
                "phase": _text(step.get("phase")),
                "operation_ref": _text(step.get("operation_ref")),
                "actor_ref": _text(step.get("actor_ref")),
                "method": _text(step.get("method")).upper(),
                "path": _text(step.get("path")),
                "reason_code": reason,
                "category": _request_first_loss_category(reason),
                "write_request_attempt_count": write_attempts,
                "request_reached_transport": False,
            }
        )

    governed["pre_transport_block_reasons"] = list(dict.fromkeys(reasons))
    category_counts: dict[str, int] = {}
    for row in rows:
        category = _text(row.get("category")) or "PRE_TRANSPORT_REQUEST_BUILD"
        category_counts[category] = category_counts.get(category, 0) + 1
    receipt = {
        "schema_version": _REQUEST_FIRST_LOSS_SCHEMA,
        "status": "BLOCKED" if rows else "NOT_APPLICABLE",
        "row_count": len(rows),
        "rows": rows,
        "by_category": category_counts,
        "transport_attempted": False if rows else None,
        "harness_failure_claimed": False,
    }
    governed["request_build_first_loss_receipt"] = receipt
    return governed, receipt


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
    result, first_loss_receipt = _seal_pre_transport_request_blocks(result)
    if first_loss_receipt.get("row_count"):
        observations["request_build_first_loss_receipt"] = first_loss_receipt

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
