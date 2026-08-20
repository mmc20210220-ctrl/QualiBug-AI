"""An exact empty continuation queue must retain an explicit terminal reason."""
from __future__ import annotations


def test_empty_exact_queue_is_sealed_as_pending_queue_empty() -> None:
    from ai_test_asset_center.continuation_preview_authority import (
        synchronize_continuation_preview,
    )

    result = synchronize_continuation_preview({
        "fresh_pending_pool": [],
        "fresh_pending_pool_count": 0,
        "budget_deferred_pool": [],
        "budget_deferred_pool_count": 0,
        "blocked_retry_pool": [],
        "blocked_retry_pool_count": 0,
    })

    assert result["continuation_outstanding_count"] == 0
    assert result["pending_count"] == 0
    assert result["pending_next_round"] == []
    assert result["early_stop_reason"] == "PENDING_QUEUE_EMPTY"
    assert result["stop_condition"] == "PENDING_QUEUE_EMPTY"


def test_existing_current_early_stop_still_wins_over_empty_queue_default() -> None:
    from ai_test_asset_center.continuation_preview_authority import (
        synchronize_continuation_preview,
    )

    result = synchronize_continuation_preview({
        "fresh_pending_pool": [],
        "budget_deferred_pool": [],
        "blocked_retry_pool": [],
        "early_stop_reason": "NO_CONTINUATION_EXPERIMENTS",
    })

    assert result["continuation_outstanding_count"] == 0
    assert result["stop_condition"] == "NO_CONTINUATION_EXPERIMENTS"
