"""Exact continuation category precedence is consistent across projections."""
from __future__ import annotations


def test_retry_overrides_deferred_and_fresh_for_same_identity() -> None:
    from ai_test_asset_center.continuation_preview_authority import (
        synchronize_continuation_preview,
    )

    result = synchronize_continuation_preview({
        "fresh_pending_pool": [{
            "obligation_id": "same",
            "not_in_plan_reason": "STALE_FRESH_REASON",
        }],
        "budget_deferred_pool": [{
            "obligation_id": "same",
            "not_in_plan_reason": "STALE_DEFERRED_REASON",
        }],
        "blocked_retry_pool": [{
            "obligation_id": "same",
            "block_reason": "BLOCKED_MISSING_BINDING",
            "not_in_plan_reason": "STALE_RETRY_REASON",
        }],
    })

    assert result["pending_count"] == 1
    assert result["continuation_outstanding_count"] == 1
    assert result["pending_next_round"] == [{
        "obligation_id": "same",
        "block_reason": "BLOCKED_MISSING_BINDING",
        "not_in_plan_reason": "CONTINUATION_RETRY_PENDING",
        "continuation_origin": "blocked_retry_pool",
    }]


def test_budget_deferred_overrides_fresh_when_retry_is_absent() -> None:
    from ai_test_asset_center.continuation_preview_authority import (
        synchronize_continuation_preview,
    )

    result = synchronize_continuation_preview({
        "fresh_pending_pool": [{"obligation_id": "same"}],
        "budget_deferred_pool": [{"obligation_id": "same"}],
        "blocked_retry_pool": [],
    })

    assert result["pending_count"] == 1
    assert result["pending_next_round"] == [{
        "obligation_id": "same",
        "not_in_plan_reason": "BUDGET_DEFERRED",
        "continuation_origin": "budget_deferred_pool",
    }]
