"""Tests for rounds_summary.py — verify unified dashboard data aggregation."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ai_test_asset_center.rounds_summary import (
    build_rounds_summary,
    _round1_benchmark,
    _round2_gaps,
    _round3_learning,
    _round4_dsl,
)


# ═════════════════════════════════════════════════════════════════════════════
# Round 1 Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_round1_no_data() -> None:
    """When no benchmark data exists, return unavailable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = _round1_benchmark("test_proj", Path(tmpdir))
        assert result["available"] is False


def test_round1_with_benchmark_metrics() -> None:
    """When benchmark_metrics.json exists, return data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create benchmark directory and metrics file
        bench_dir = Path(tmpdir) / "platform_outputs" / "test_proj" / "benchmark"
        bench_dir.mkdir(parents=True)
        metrics = {
            "benchmark_active": True,
            "recall": 0.75,
            "precision": 0.85,
            "f1_score": 0.8,
            "high_value_recall": 0.9,
            "evidence_completeness_rate": 0.95,
            "ground_truth_bug_count": 20,
            "true_positives": 15,
            "false_positives": 3,
            "false_negatives": 5,
            "bug_type_breakdown": {"idor": {"total": 5, "detected": 4}},
            "risk_family_breakdown": {"permission": {"total": 3, "detected": 3}},
        }
        (bench_dir / "benchmark_metrics.json").write_text(json.dumps(metrics))

        result = _round1_benchmark("test_proj", Path(tmpdir))
        assert result["available"] is True
        assert result["recall"] == 0.75
        assert result["f1_score"] == 0.8
        assert result["ground_truth_bug_count"] == 20


# ═════════════════════════════════════════════════════════════════════════════
# Round 2 Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_round2_no_data() -> None:
    """When no gap tracker file exists, return unavailable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = _round2_gaps("test_proj", Path(tmpdir))
        assert result["available"] is False


def test_round2_with_gap_data() -> None:
    """When gap tracker has data, return gap stats."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Use GapTracker to create data
        from ai_test_asset_center.gap_tracker import GapTracker
        from ai_test_asset_center.capability_gap_resolver import CapabilityGap, GapRootCause, GapResolution

        tracker = GapTracker("test_proj", root=tmpdir)
        gap = CapabilityGap("G1", GapRootCause.MISSING_BASE_URL, GapResolution.NEEDS_CUSTOMER_CONFIG, "P0", "Missing URL")
        tracker.record_gaps([gap])

        result = _round2_gaps("test_proj", Path(tmpdir))
        assert result["available"] is True
        assert result["currently_open"] == 1
        assert result["total_gaps_ever"] >= 1


# ═════════════════════════════════════════════════════════════════════════════
# Round 3 Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_round3_no_data() -> None:
    """When no learning manifests exist, return unavailable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = _round3_learning("test_proj", Path(tmpdir))
        assert result["available"] is False


def test_round3_with_learning_manifest() -> None:
    """When learning manifests exist, return stats."""
    with tempfile.TemporaryDirectory() as tmpdir:
        learning_dir = Path(tmpdir) / "platform_outputs" / "_learning"
        learning_dir.mkdir(parents=True)
        manifest = {
            "source_bug_count": 5,
            "summary": {
                "total_probes_generated": 30,
                "total_oracles_generated": 15,
                "total_fixtures_generated": 10,
                "strategies_used": ["role_variant", "entity_variant"],
            },
        }
        (learning_dir / "learning_manifest_LM-001.json").write_text(json.dumps(manifest))

        result = _round3_learning("test_proj", Path(tmpdir))
        assert result["available"] is True
        assert result["total_probes_generated"] == 30
        assert result["total_oracles_generated"] == 15
        assert result["manifest_count"] == 1


# ═════════════════════════════════════════════════════════════════════════════
# Round 4 Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_round4_always_available() -> None:
    """DSL data is always available (rule library is built-in)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = _round4_dsl("test_proj", Path(tmpdir))
        assert result["available"] is True
        assert result["industry_count"] == 7
        assert result["total_rules"] >= 35  # 7 industries × ~5 rules each
        assert "by_industry" in result


# ═════════════════════════════════════════════════════════════════════════════
# Unified Summary Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_build_rounds_summary_empty() -> None:
    """When no data exists, summary should show all rounds unavailable except DSL."""
    with tempfile.TemporaryDirectory() as tmpdir:
        summary = build_rounds_summary("test_proj", Path(tmpdir))
        assert summary["schema_version"] == "rounds_summary.v1"
        assert "round_1_benchmark" in summary
        assert "round_2_gaps" in summary
        assert "round_3_learning" in summary
        assert "round_4_dsl" in summary
        # R1-R3 should be unavailable (no data), R4 always available
        assert summary["round_1_benchmark"]["available"] is False
        assert summary["round_4_dsl"]["available"] is True
        assert summary["rounds_with_data"] >= 1  # At least DSL


def test_build_rounds_summary_with_data() -> None:
    """When benchmark data exists, summary should reflect it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create benchmark data
        bench_dir = Path(tmpdir) / "platform_outputs" / "test_proj" / "benchmark"
        bench_dir.mkdir(parents=True)
        (bench_dir / "benchmark_metrics.json").write_text(json.dumps({
            "benchmark_active": True, "recall": 0.5, "precision": 0.6,
            "f1_score": 0.545, "ground_truth_bug_count": 10,
            "true_positives": 5, "false_positives": 3, "false_negatives": 5,
            "evidence_completeness_rate": 0.8,
        }))

        summary = build_rounds_summary("test_proj", Path(tmpdir))
        assert summary["round_1_benchmark"]["available"] is True
        assert summary["round_1_benchmark"]["recall"] == 0.5
        assert summary["rounds_with_data"] >= 2  # R1 + R4


def test_build_rounds_summary_structure() -> None:
    """Verify the summary has all required fields."""
    with tempfile.TemporaryDirectory() as tmpdir:
        summary = build_rounds_summary("test_proj", Path(tmpdir))
        required_keys = ["schema_version", "project_id", "generated_at",
                         "round_1_benchmark", "round_2_gaps",
                         "round_3_learning", "round_4_dsl",
                         "rounds_with_data"]
        for key in required_keys:
            assert key in summary, f"Missing key: {key}"
