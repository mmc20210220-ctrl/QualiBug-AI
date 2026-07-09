"""Tests for cross_round_bridge.py — verify benchmark → gap → learning feedback loop."""

from __future__ import annotations

import pytest

from ai_test_asset_center.cross_round_bridge import CrossRoundBridge, PrioritySignal


# ═════════════════════════════════════════════════════════════════════════════
# Priority Signal Derivation Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_derive_signals_low_recall() -> None:
    """Very low recall should produce a high-boost signal."""
    bridge = CrossRoundBridge()
    metrics = {
        "risk_type_breakdown": {
            "permission_bypass": {"total": 10, "detected": 1, "recall": 0.1},
        },
    }
    signals = bridge.derive_priority_signals_from_benchmark(metrics)
    assert len(signals) == 1
    assert signals[0].risk_type == "permission_bypass"
    assert signals[0].priority_boost == 0.4
    assert signals[0].source == "benchmark_recall_gap"


def test_derive_signals_moderate_recall() -> None:
    """Moderate recall should produce a medium-boost signal."""
    bridge = CrossRoundBridge()
    metrics = {
        "risk_type_breakdown": {
            "idor": {"total": 10, "detected": 4, "recall": 0.4},
        },
    }
    signals = bridge.derive_priority_signals_from_benchmark(metrics)
    assert len(signals) == 1
    assert signals[0].priority_boost == 0.25


def test_derive_signals_good_recall_no_signal() -> None:
    """Good recall should produce no signals."""
    bridge = CrossRoundBridge()
    metrics = {
        "risk_type_breakdown": {
            "state_flow": {"total": 10, "detected": 9, "recall": 0.9},
        },
    }
    signals = bridge.derive_priority_signals_from_benchmark(metrics)
    assert len(signals) == 0


def test_derive_signals_high_value_recall_low() -> None:
    """Low high_value_recall should produce a wildcard signal."""
    bridge = CrossRoundBridge()
    metrics = {
        "risk_type_breakdown": {},
        "high_value_recall": 0.3,
    }
    signals = bridge.derive_priority_signals_from_benchmark(metrics)
    assert len(signals) == 1
    assert signals[0].risk_type == "*"
    assert signals[0].priority_boost == 0.3


def test_derive_signals_evidence_weighted_low() -> None:
    """Low evidence_weighted_recall should produce a wildcard signal."""
    bridge = CrossRoundBridge()
    metrics = {
        "risk_type_breakdown": {},
        "high_value_recall": 0.8,
        "evidence_weighted_recall": 0.15,
    }
    signals = bridge.derive_priority_signals_from_benchmark(metrics)
    assert len(signals) == 1
    assert signals[0].priority_boost == 0.2


def test_derive_signals_multiple() -> None:
    """Multiple low-recall types should produce multiple signals."""
    bridge = CrossRoundBridge()
    metrics = {
        "risk_type_breakdown": {
            "permission_bypass": {"total": 5, "detected": 1, "recall": 0.2},
            "idor": {"total": 5, "detected": 2, "recall": 0.4},
            "state_flow": {"total": 5, "detected": 5, "recall": 1.0},
        },
    }
    signals = bridge.derive_priority_signals_from_benchmark(metrics)
    # permission_bypass (0.2 → 0.4 boost), idor (0.4 → 0.25 boost), state_flow (1.0 → no signal)
    assert len(signals) == 2


def test_derive_signals_empty_breakdown() -> None:
    """Empty risk_type_breakdown should produce no signals."""
    bridge = CrossRoundBridge()
    signals = bridge.derive_priority_signals_from_benchmark({"risk_type_breakdown": {}})
    assert len(signals) == 0


# ═════════════════════════════════════════════════════════════════════════════
# Gap Resolution Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_on_gap_resolved() -> None:
    """Resolving a gap should produce a priority signal."""
    bridge = CrossRoundBridge()
    signals = bridge.on_gap_resolved("security_boundary")
    assert len(signals) == 1
    assert signals[0].risk_type == "security_boundary"
    assert signals[0].priority_boost == 0.35
    assert signals[0].source == "gap_resolved"


def test_on_gaps_resolved_batch() -> None:
    """Batch gap resolution should produce multiple signals."""
    bridge = CrossRoundBridge()
    signals = bridge.on_gaps_resolved_batch(["security_boundary", "ui"])
    assert len(signals) == 2


def test_get_resolved_families() -> None:
    """get_resolved_families should accumulate resolved families."""
    bridge = CrossRoundBridge()
    bridge.on_gap_resolved("data_integrity")
    bridge.on_gap_resolved("performance")
    families = bridge.get_resolved_families()
    assert "data_integrity" in families
    assert "performance" in families
    assert len(families) == 2


# ═════════════════════════════════════════════════════════════════════════════
# Priority Boosts Accumulation Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_get_learning_priority_boosts() -> None:
    """Priority boosts should accumulate from multiple sources."""
    bridge = CrossRoundBridge()
    bridge.derive_priority_signals_from_benchmark({
        "risk_type_breakdown": {
            "permission_bypass": {"total": 5, "detected": 1, "recall": 0.2},
        },
    })
    bridge.on_gap_resolved("permission_bypass")

    boosts = bridge.get_learning_priority_boosts()
    assert "permission_bypass" in boosts
    # 0.4 (benchmark) + 0.35 (gap) = 0.75, capped at 1.0
    assert boosts["permission_bypass"] == 0.75


def test_priority_boost_capped() -> None:
    """Priority boost total should be capped at 1.0."""
    bridge = CrossRoundBridge()
    for _ in range(5):
        bridge.on_gap_resolved("test_family")
    boosts = bridge.get_learning_priority_boosts()
    assert boosts["test_family"] <= 1.0


# ═════════════════════════════════════════════════════════════════════════════
# Hook Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_register_hook_on_gap_resolved() -> None:
    """Hook should fire when gap is resolved."""
    calls: list[str] = []
    bridge = CrossRoundBridge()
    bridge.register_hook("on_gap_resolved", lambda family, signal: calls.append(family))
    bridge.on_gap_resolved("test_family")
    assert calls == ["test_family"]


def test_register_hook_on_benchmark_complete() -> None:
    """Hook should fire when benchmark completes."""
    calls: list[int] = []
    bridge = CrossRoundBridge()
    bridge.register_hook("on_benchmark_complete", lambda metrics, signals: calls.append(len(signals)))
    bridge.derive_priority_signals_from_benchmark({
        "risk_type_breakdown": {"test": {"total": 5, "detected": 1, "recall": 0.2}},
    })
    assert calls == [1]


def test_hook_exception_does_not_crash() -> None:
    """Hook exceptions should be caught gracefully."""
    bridge = CrossRoundBridge()
    bridge.register_hook("on_gap_resolved", lambda family, signal: (_ for _ in ()).throw(Exception("boom")))
    # Should not raise
    signals = bridge.on_gap_resolved("test_family")
    assert len(signals) == 1


# ═════════════════════════════════════════════════════════════════════════════
# Summary Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_build_closed_loop_summary() -> None:
    """build_closed_loop_summary should produce a valid dict."""
    bridge = CrossRoundBridge()
    bridge.derive_priority_signals_from_benchmark({
        "risk_type_breakdown": {"test": {"total": 5, "detected": 1, "recall": 0.2}},
    })
    bridge.on_gap_resolved("test")
    bridge.on_learning_generated({"probes": 10, "oracles": 5, "fixtures": 3})

    summary = bridge.build_closed_loop_summary()
    assert summary["schema_version"] == "cross_round_bridge.v1"
    assert summary["priority_signals_count"] == 2
    assert "test" in summary["resolved_families"]
    assert "learning_priority_boosts" in summary


def test_log_observable() -> None:
    """Log should record bridge activity."""
    bridge = CrossRoundBridge()
    bridge.derive_priority_signals_from_benchmark({
        "risk_type_breakdown": {"test": {"total": 5, "detected": 1, "recall": 0.2}},
    })
    bridge.on_gap_resolved("test")
    log = bridge.get_log()
    assert len(log) == 2
    assert any("derive_priority_signals" in entry for entry in log)
    assert any("on_gap_resolved" in entry for entry in log)
