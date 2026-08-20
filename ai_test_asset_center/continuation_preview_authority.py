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
    """
    from .pipeline_slices import _ABS_MAX_SLICE_BUDGET

    plan = dict(_dict(obligation_plan))
    if "fresh_pending_pool" not in plan:
        return plan

    exact_rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def append_rows(
        rows: list[Any],
        *,
        default_reason: str,
        origin: str,
    ) -> None:
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            oid = _text(raw.get("obligation_id"))
            if not oid or oid in seen:
                continue
            row = dict(raw)
            row["obligation_id"] = oid
            row.setdefault("not_in_plan_reason", default_reason)
            row.setdefault("continuation_origin", origin)
            exact_rows.append(row)
            seen.add(oid)

    # Execution scheduling gives never-attempted/capacity-deferred work a fresh
    # opportunity before retry-only loops. Preview follows the same category
    # order; it is descriptive only and does not become scheduling authority.
    append_rows(
        _list(plan.get("fresh_pending_pool")),
        default_reason="CONTINUATION_PENDING",
        origin="fresh_pending_pool",
    )
    append_rows(
        _list(plan.get("budget_deferred_pool")),
        default_reason="BUDGET_DEFERRED",
        origin="budget_deferred_pool",
    )
    append_rows(
        _list(plan.get("blocked_retry_pool")),
        default_reason="CONTINUATION_RETRY_PENDING",
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
    return plan


__all__ = ["synchronize_continuation_preview"]
