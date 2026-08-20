"""Selected-identity correlation for continuation execution receipts.

Execution evidence may legitimately report a different executed obligation face
(for example a compiled variant) from the obligation identity that the planner
selected. Continuation scheduling owns the selected identity. Receipt matching
must therefore correlate on ``selected_obligation_id`` while leaving the
original batch/evidence payload unchanged for delivery and audit.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def selected_continuation_identity(row: dict[str, Any]) -> str:
    """Return the planner-selected identity represented by one result row."""
    return _text(row.get("selected_obligation_id") or row.get("obligation_id"))


def _continuation_result_rows(batch: dict[str, Any]) -> list[dict[str, Any]]:
    """Project result rows onto selected identity without mutating evidence."""
    projected: list[dict[str, Any]] = []
    for raw in _list(_dict(batch).get("results")):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        selected_id = selected_continuation_identity(row)
        executed_id = _text(
            row.get("executed_obligation_id") or row.get("obligation_id")
        )
        if selected_id:
            row["obligation_id"] = selected_id
        if executed_id and executed_id != selected_id:
            row["executed_obligation_id"] = executed_id
        projected.append(row)
    return projected


def batch_for_initial_continuation_capture(
    batch: dict[str, Any],
) -> dict[str, Any]:
    """Return a shallow capture-only copy correlated to selected identity."""
    projected = dict(_dict(batch))
    projected["results"] = _continuation_result_rows(projected)
    return projected


class ContinuationBatchView(dict):
    """Dict view whose continuation reader sees selected-identity results.

    The underlying mapping retains the original ``results`` list. Therefore
    ``dict(view)`` — used when follow-on batches are persisted for delivery and
    audit — preserves the executed identity/evidence payload byte-for-byte,
    while ``view.get('results')`` — used by the continuation state machine —
    returns the scheduling identity projection.
    """

    def get(self, key: Any, default: Any = None) -> Any:
        if key == "results":
            return _continuation_result_rows(self)
        return super().get(key, default)


def continuation_execute_batch_view(
    execute_batch: Callable[..., dict[str, Any]],
) -> Callable[..., dict[str, Any]]:
    """Wrap an executor so continuation correlation uses selected identity."""

    @wraps(execute_batch)
    def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raw = execute_batch(*args, **kwargs)
        return ContinuationBatchView(_dict(raw))

    return wrapped


def install_initial_capture_selected_identity_bridge() -> None:
    """Patch the public executor's capture hook without changing returned batch.

    ``execute_selected_experiments`` resolves its private capture hook from the
    module global at call time. Installing this bridge before the first business
    batch lets the existing lossless capture ledger keep its implementation and
    storage semantics while correlating variant results to the selected work.
    """
    from . import experiment_executor as executor

    current = getattr(executor, "_capture_continuation_execution_receipts", None)
    if current is None or getattr(current, "_qualibug_selected_identity_bridge", False):
        return
    original = current

    @wraps(original)
    def bridged(*, campaign_id: str, selected_rows: list[dict[str, Any]], batch: dict[str, Any]) -> None:
        original(
            campaign_id=campaign_id,
            selected_rows=selected_rows,
            batch=batch_for_initial_continuation_capture(batch),
        )

    setattr(bridged, "_qualibug_selected_identity_bridge", True)
    setattr(bridged, "_qualibug_original_capture", original)
    executor._capture_continuation_execution_receipts = bridged


__all__ = [
    "ContinuationBatchView",
    "batch_for_initial_continuation_capture",
    "continuation_execute_batch_view",
    "install_initial_capture_selected_identity_bridge",
    "selected_continuation_identity",
]
