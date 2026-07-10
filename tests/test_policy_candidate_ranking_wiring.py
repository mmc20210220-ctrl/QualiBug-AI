from __future__ import annotations

from ai_test_asset_center.policy_registry import StrategyBundle
from ai_test_asset_center.policy_wiring import policy_strategy_override
from ai_test_asset_center.v12_pipeline import _rank_behavior_slices_for_selection


def test_bounded_weakness_recurrence_signal_changes_real_slice_ranking() -> None:
    slices = [
        {
            "slice_id": "high-base-low-recurrence",
            "entity": "first",
            "kind": "invariant",
            "priority": 0.9,
            "source_refs": [{"kind": "api"}],
            "endpoints": ["/first"],
            "weakness_recurrence": 0.1,
        },
        {
            "slice_id": "low-base-high-recurrence",
            "entity": "second",
            "kind": "invariant",
            "priority": 0.2,
            "source_refs": [{"kind": "api"}],
            "endpoints": ["/second"],
            "weakness_recurrence": 7,
        },
    ]
    baseline = StrategyBundle()
    challenger = StrategyBundle()
    challenger.discovery.candidate_ranking_signals.append("weakness_recurrence")

    with policy_strategy_override(baseline):
        assert _rank_behavior_slices_for_selection(slices)[0]["slice_id"] == "high-base-low-recurrence"
    with policy_strategy_override(challenger):
        assert _rank_behavior_slices_for_selection(slices)[0]["slice_id"] == "low-base-high-recurrence"


def test_cleanup_risk_signal_deprioritizes_unrestorable_slice() -> None:
    strategy = StrategyBundle()
    strategy.discovery.candidate_ranking_signals.append("cleanup_risk")
    slices = [
        {
            "slice_id": "unsafe-cleanup",
            "entity": "first",
            "kind": "invariant",
            "priority": 0.9,
            "source_refs": [{"kind": "api"}],
            "endpoints": ["/first"],
            "cleanup_risk": 1,
        },
        {
            "slice_id": "restorable",
            "entity": "second",
            "kind": "invariant",
            "priority": 0.2,
            "source_refs": [{"kind": "api"}],
            "endpoints": ["/second"],
            "cleanup_risk": 0,
        },
    ]

    with policy_strategy_override(strategy):
        assert _rank_behavior_slices_for_selection(slices)[0]["slice_id"] == "restorable"
