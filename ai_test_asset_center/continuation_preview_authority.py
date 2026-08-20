"""Project bounded continuation preview from exact resume authorities.

Execution state lives in unbounded fresh/retry/deferred pools. Product-facing
``pending_next_round`` and its count/truncation metadata are derived views and
must never retain stale planner-era values after continuation mutates the queue.
"""
from __future__ import annotations

from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def synchronize_continuation_preview(
    obligation_plan: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild current preview/count metadata from exact continuation pools.

    Legacy plans without ``fresh_pending_pool`` are returned unchanged. The
    presence of that field marks the new exact-authority format, including an
    intentionally empty fresh pool.

    Pool precedence matches terminal sealing: retry > budget-deferred > fresh.
    Production keeps these pools mutually exclusive, but older/manual persisted
    plans can overlap and must not expose a weaker stale category for one id.
    """
    from .pipeline_slices import _ABS_MAX_SLICE_BUDGET

    plan = dict(_dict(obligation_plan))
    if "fresh_pending_pool" not in plan:
        return plan

    exact_rows: list[dict[str, Any]] = []
    row_index: dict[str, int] = {}
    authority_priority: dict[str, int] = {}

    def upsert_rows(
        rows: list[Any],
        *,
        priority: int,
        reason: str,
        origin: str,
    ) -> None:
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            oid = _text(raw.get("obligation_id"))
            if not oid:
                continue
            if authority_priority.get(oid, -1) > priority:
                continue
            row = dict(raw)
            row["obligation_id"] = oid
            # Exact category is authoritative. Never preserve a stale weaker
            # reason/origin copied from an older preview row.
            row["not_in_plan_reason"] = reason
            row["continuation_origin"] = origin
            if oid in row_index:
                exact_rows[row_index[oid]] = row
            else:
                row_index[oid] = len(exact_rows)
                exact_rows.append(row)
            authority_priority[oid] = priority

    # Preserve fresh-first display position for distinct identities while using
    # stronger category semantics for overlap. This preview remains descriptive;
    # scheduling reads the exact pools directly.
    upsert_rows(
        _list(plan.get("fresh_pending_pool")),
        priority=1,
        reason="CONTINUATION_PENDING",
        origin="fresh_pending_pool",
    )
    upsert_rows(
        _list(plan.get("budget_deferred_pool")),
        priority=2,
        reason="BUDGET_DEFERRED",
        origin="budget_deferred_pool",
    )
    upsert_rows(
        _list(plan.get("blocked_retry_pool")),
        priority=3,
        reason="CONTINUATION_RETRY_PENDING",
        origin="blocked_retry_pool",
    )

    total = len(exact_rows)
    preview = exact_rows[:_ABS_MAX_SLICE_BUDGET]
    truncated = max(0, total - len(preview))
    plan.update({
        "pending_next_round": preview,
        "pending_count": total,
        "pending_truncated": truncated,
        "pending_truncation_reason": (
            f"CONTINUATION_POOL_SIZE_{total}_EXCEEDS_ABS_MAX_{_ABS_MAX_SLICE_BUDGET}"
            if truncated
            else ""
        ),
        "continuation_outstanding_count": total,
        "continuation_preview_authority": "exact_fresh_deferred_retry_pools",
    })
    early_stop = _text(plan.get("early_stop_reason"))
    if early_stop:
        # Early continuation gates are execution facts. Never leave a stale
        # planner stop condition (commonly "budget_exhausted") beside a more
        # specific round-limit/budget-zero/no-continuation receipt.
        plan["stop_condition"] = early_stop
    return plan


__all__ = ["synchronize_continuation_preview"]
