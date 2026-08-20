"""Keep unavailable fresh work authoritative without starving runnable retries.

Fresh continuation work has scheduling priority, but an exact fresh identity
whose current experiment is absent/unusable cannot consume a round. Only when a
retry identity is currently runnable do we hold those unavailable fresh rows out
of the active queue; otherwise the established engine path remains unchanged.
"""
from __future__ import annotations

from typing import Any

from .continuation_preview_authority import synchronize_continuation_preview


_USABLE_CONTINUATION_STATUSES = {
    "COMPILED",
    "BLOCKED",
    "BLOCKED_MISSING_BINDING",
    "HARNESS_FAILED",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _compile_status(experiment: Any) -> str:
    exp = _dict(experiment)
    receipt = _dict(exp.get("compile_receipt"))
    return _text(receipt.get("status") or exp.get("compile_status")).upper()


def hold_unrunnable_fresh(
    obligation_plan: dict[str, Any],
    experiments_by_obligation: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Hold unavailable fresh rows only when they would starve runnable retry."""
    plan = dict(_dict(obligation_plan))
    if "fresh_pending_pool" not in plan:
        return plan, []

    experiments = {
        _text(key): dict(value)
        for key, value in _dict(experiments_by_obligation).items()
        if _text(key) and isinstance(value, dict)
    }
    runnable_retry_exists = any(
        _text(row.get("obligation_id"))
        and _compile_status(
            experiments.get(_text(row.get("obligation_id")))
        ) in _USABLE_CONTINUATION_STATUSES
        for row in _list(plan.get("blocked_retry_pool"))
        if isinstance(row, dict)
    )
    if not runnable_retry_exists:
        return plan, []

    original_fresh = [
        dict(row)
        for row in _list(plan.get("fresh_pending_pool"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    ]
    runnable: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    for row in original_fresh:
        oid = _text(row.get("obligation_id"))
        if _compile_status(experiments.get(oid)) in _USABLE_CONTINUATION_STATUSES:
            runnable.append(row)
        else:
            held.append(row)

    if not held:
        return plan, []

    plan["fresh_pending_pool"] = runnable
    plan["fresh_pending_pool_count"] = len(runnable)
    plan["held_unrunnable_fresh_count"] = len(held)
    plan["held_unrunnable_fresh_reason"] = "CURRENT_EXPERIMENT_UNAVAILABLE"
    # Rebuild the active preview from exact pools. This removes held fresh rows
    # from the engine-visible pending queue without changing their persisted
    # resume authority, which is restored after the engine returns.
    plan = synchronize_continuation_preview(plan)
    return plan, held


def restore_unrunnable_fresh(
    final_plan: dict[str, Any],
    held_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Restore held exact fresh identities after runnable work is consumed."""
    plan = dict(_dict(final_plan))
    held = [
        dict(row)
        for row in held_rows
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    ]
    if not held:
        return plan

    active_final = [
        dict(row)
        for row in _list(plan.get("fresh_pending_pool"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    ]
    held_by_id = {
        _text(row.get("obligation_id")): row for row in held
    }
    restored: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Held rows were removed from an already ordered exact fresh pool. Preserve
    # their relative order, then append any still-active fresh rows.
    for row in held:
        oid = _text(row.get("obligation_id"))
        if oid and oid not in seen:
            restored.append(dict(row))
            seen.add(oid)
    for row in active_final:
        oid = _text(row.get("obligation_id"))
        if oid and oid not in seen:
            restored.append(dict(row))
            seen.add(oid)

    plan["fresh_pending_pool"] = restored
    plan["fresh_pending_pool_count"] = len(restored)
    plan["held_unrunnable_fresh_count"] = len(held_by_id)
    plan["held_unrunnable_fresh_reason"] = "CURRENT_EXPERIMENT_UNAVAILABLE"

    # If the runnable subqueue drained normally, the overall queue is not empty:
    # exact fresh work remains, but it currently has no usable experiment.
    if _text(plan.get("stop_condition")) in {"", "PENDING_QUEUE_EMPTY"}:
        reason = "NO_CONTINUATION_EXPERIMENTS"
        plan["early_stop_reason"] = reason
        plan["stop_condition"] = reason
        plan.pop("round_limit_reached", None)
        plan.pop("follow_on_round_limit", None)
    return synchronize_continuation_preview(plan)


__all__ = [
    "hold_unrunnable_fresh",
    "restore_unrunnable_fresh",
]
