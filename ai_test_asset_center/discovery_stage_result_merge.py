"""Chronological compile/execution/gate projection across discovery batches.

Continuation retries are later attempts. Their receipts must overwrite earlier
BLOCKED/HARNESS receipts for the same obligation; an initial expansion batch
must never be merged after its own follow-on continuation.
"""
from __future__ import annotations

from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def merge_discovery_stage_results(
    *,
    main_initial_batch: dict[str, Any],
    expansion_initial_batch: dict[str, Any],
    feedback_initial_batch: dict[str, Any],
    main_follow_on_batches: list[dict[str, Any]],
    expansion_follow_on_batches: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    """Return last-attempt-wins stage maps in actual execution chronology."""
    batches: list[dict[str, Any]] = [
        _dict(main_initial_batch),
        _dict(expansion_initial_batch),
    ]
    feedback = _dict(feedback_initial_batch)
    if (
        _dict(feedback.get("compile_results"))
        or _dict(feedback.get("execution_results"))
        or _dict(feedback.get("gate_results"))
    ):
        batches.append(feedback)
    batches.extend(
        _dict(batch) for batch in main_follow_on_batches if isinstance(batch, dict)
    )
    batches.extend(
        _dict(batch)
        for batch in expansion_follow_on_batches
        if isinstance(batch, dict)
    )

    def merged(key: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for batch in batches:
            result.update({
                _text(oid): dict(row)
                for oid, row in _dict(batch.get(key)).items()
                if _text(oid) and isinstance(row, dict)
            })
        return result

    return (
        merged("compile_results"),
        merged("execution_results"),
        merged("gate_results"),
    )


__all__ = ["merge_discovery_stage_results"]
