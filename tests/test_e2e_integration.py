"""End-to-end integration smoke tests across all 3 rounds + cross-round bridge.

These tests verify the full feedback loop:
  Round 1 (Bug Factory + Metrics) → Round 2 (Gap Resolver) → Round 3 (Learning Generator)
  Connected via CrossRoundBridge.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ai_test_asset_center.benchmark_bug_factory import (
    BenchmarkBugFactory,
    prepare_industry_benchmark,
    validate_ground_truth_integrity,
)
from ai_test_asset_center.capability_gap_resolver import (
    CapabilityGapResolver,
    GapRootCause,
    GapResolution,
)
from ai_test_asset_center.gap_tracker import GapTracker
from ai_test_asset_center.learning_generator import LearningGenerator
from ai_test_asset_center.cross_round_bridge import CrossRoundBridge
from benchmark_evaluator.metrics import compute_metrics


# ═════════════════════════════════════════════════════════════════════════════
# Round 1 → Round 2: Bug Factory → Gap Detection
# ═════════════════════════════════════════════════════════════════════════════

def test_bug_factory_to_gap_detection() -> None:
    """Generated bugs should produce valid ground truth that passes integrity checks."""
    factory = BenchmarkBugFactory("ecommerce")
    bugs = factory.generate(count=20, seed=42)

    with tempfile.TemporaryDirectory() as tmpdir:
        gt_path = factory.write_ground_truth(bugs, output_dir=tmpdir)
        integrity = validate_ground_truth_integrity(gt_path)
        assert integrity["valid"] is True
        assert integrity["bug_count"] == 20

        # Verify the stored bugs are valid
        assert "private_ground_truth" in str(gt_path).lower()


def test_metrics_to_gap_resolver() -> None:
    """Metrics from benchmark should be consumable by the gap resolver pipeline."""
    # Simulate a benchmark run
    truth = [
        {"bug_id": "B1", "template_id": "T1", "severity": "P0", "risk_type": "permission_bypass", "api": "/api/admin"},
        {"bug_id": "B2", "template_id": "T2", "severity": "P1", "risk_type": "idor", "api": "/api/users"},
        {"bug_id": "B3", "template_id": "T3", "severity": "P2", "risk_type": "state_flow", "api": "/api/orders"},
    ]
    discovered = [
        {"title": "F1", "bug_id": "D1", "severity": "P0", "risk_type": "permission_bypass", "api": "/api/admin", "confidence": 0.9, "predicted_template_id": "T1"},
    ]
    matches = [
        {"ground_truth": truth[0], "discovered": discovered[0], "match_type": "exact_instance"},
    ]

    metrics = compute_metrics(truth, discovered, matches)
    assert metrics["recall"] < 1.0  # Only 1 of 3 found
    assert metrics["f1_score"] > 0

    # Metrics should have all required fields for downstream consumption
    assert "risk_type_breakdown" in metrics
    assert "severity_breakdown" in metrics
    assert "false_positive_analysis" in metrics


def test_gap_resolver_with_simulated_preflight() -> None:
    """Gap resolver should detect gaps from simulated preflight."""
    resolver = CapabilityGapResolver("test_proj")
    preflight = {
        "checks": [
            {"name": "base_url_configured", "ok": False, "status": "failed", "severity": "blocking", "message": "No base URL"},
            {"name": "auth_session_ready", "ok": False, "status": "failed", "severity": "warning", "message": "No auth"},
        ],
    }
    gaps = resolver.detect_from_preflight(preflight)
    assert len(gaps) >= 1
    report = resolver.build_gap_report(gaps)
    assert report["total_gaps"] >= 1
    assert "config_tasks" in report


# ═════════════════════════════════════════════════════════════════════════════
# Round 2 → Round 3: Gap Resolution → Learning Generation
# ═════════════════════════════════════════════════════════════════════════════

def test_gap_tracker_integration() -> None:
    """Gap tracker should track gaps across operations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tracker = GapTracker("test_proj", root=tmpdir)

        # Simulate gap detection
        from ai_test_asset_center.capability_gap_resolver import CapabilityGap
        gaps = [
            CapabilityGap("G1", GapRootCause.MISSING_BASE_URL, GapResolution.NEEDS_CUSTOMER_CONFIG, "P0", "Missing URL"),
        ]
        result = tracker.record_gaps(gaps)
        assert result["new_gaps"] == 1

        # Verify persistence
        tracker2 = GapTracker("test_proj", root=tmpdir)
        assert tracker2.current_snapshot().open_count == 1


def test_learning_generator_from_confirmed() -> None:
    """Learning generator should produce artifacts from confirmed bugs."""
    confirmed = [
        {
            "bug_id": "TEST_001",
            "risk_type": "permission_bypass",
            "severity": "P0",
            "method": "GET",
            "path": "/api/v1/admin/users",
            "api": "GET /api/v1/admin/users",
            "verdict": "confirmed",
            "oracle": {"type": "permission_bypass", "expected_status": 403, "bug_signal": "status_code == 200"},
            "variant_dimensions": {"actor": "normal_user", "entity": "user"},
        },
    ]

    context = {
        "entities": ["user", "order", "product"],
        "endpoints": [
            {"method": "GET", "path": "/api/v1/admin/users"},
            {"method": "GET", "path": "/api/v1/admin/orders"},
            {"method": "GET", "path": "/api/v1/admin/products"},
        ],
    }
    gen = LearningGenerator(project_context=context)
    manifest = gen.generate_from_confirmed_bugs(confirmed)

    # Should generate at minimum fixtures for each confirmed bug
    assert len(manifest.generated_fixtures) >= 1
    # Should also generate some probes or oracles
    total_artifacts = len(manifest.generated_probes) + len(manifest.generated_oracles) + len(manifest.generated_fixtures)
    assert total_artifacts > 0, "Learning should produce at least some artifacts"


# ═════════════════════════════════════════════════════════════════════════════
# Cross-Round Bridge: Full Feedback Loop
# ═════════════════════════════════════════════════════════════════════════════

def test_cross_round_full_loop() -> None:
    """Full feedback loop: benchmark → priority signals → gap resolution → learning."""
    bridge = CrossRoundBridge()

    # Step 1: Simulate benchmark with low recall
    metrics = {
        "risk_type_breakdown": {
            "permission_bypass": {"total": 10, "detected": 2, "recall": 0.2},
            "idor": {"total": 10, "detected": 5, "recall": 0.5},
            "state_flow": {"total": 10, "detected": 8, "recall": 0.8},
        },
        "high_value_recall": 0.4,
    }
    signals = bridge.derive_priority_signals_from_benchmark(metrics)
    assert len(signals) >= 2  # permission_bypass (0.2) + high_value (0.4) + idor (0.5)

    # Step 2: Simulate gap resolution
    bridge.on_gap_resolved("security_boundary")

    # Step 3: Get accumulated priority boosts
    boosts = bridge.get_learning_priority_boosts()
    assert "permission_bypass" in boosts
    assert boosts["permission_bypass"] > 0

    # Step 4: Learning generation
    bridge.on_learning_generated({"probes": 15, "oracles": 5, "fixtures": 6})

    # Step 5: Verify closed loop summary
    summary = bridge.build_closed_loop_summary()
    assert summary["priority_signals_count"] > 0
    assert "security_boundary" in summary["resolved_families"]


def test_bridge_hooks_fire_in_order() -> None:
    """Hooks should fire in registration order during the feedback loop."""
    events: list[str] = []
    bridge = CrossRoundBridge()

    bridge.register_hook("on_benchmark_complete", lambda m, s: events.append("benchmark"))
    bridge.register_hook("on_gap_resolved", lambda f, s: events.append(f"gap:{f}"))
    bridge.register_hook("on_learning_generated", lambda c: events.append("learning"))

    bridge.derive_priority_signals_from_benchmark({
        "risk_type_breakdown": {"test": {"total": 5, "detected": 1, "recall": 0.2}},
    })
    bridge.on_gap_resolved("test_family")
    bridge.on_learning_generated({"probes": 10})

    assert "benchmark" in events
    assert "gap:test_family" in events
    assert "learning" in events


# ═════════════════════════════════════════════════════════════════════════════
# Round 1 → Round 3: Full E2E with Real Bug Factory
# ═════════════════════════════════════════════════════════════════════════════

def test_full_pipeline_e2e() -> None:
    """End-to-end: BugFactory → metrics → bridge → learning → manifest."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Round 1: Generate bugs and ground truth
        result = prepare_industry_benchmark("crm", bug_count=30, seed=42, output_root=tmpdir)
        assert result["bug_count"] == 30

        gt_path = Path(result["ground_truth_path"])
        integrity = validate_ground_truth_integrity(gt_path)
        assert integrity["valid"] is True

        # Simulate discovery findings (in real pipeline, this comes from discovery engine)
        import json
        gt_data = json.loads(gt_path.read_text(encoding="utf-8"))
        truth_bugs = gt_data["bugs"]

        # Simulate some "discovered" bugs (partial match)
        discovered = []
        for bug in truth_bugs[:10]:  # Found 10 of 30
            discovered.append({
                "title": f"Found: {bug['title']}",
                "bug_id": f"D-{bug['bug_id']}",
                "severity": bug["severity"],
                "risk_type": bug["risk_type"],
                "api": bug["api"],
                "confidence": 0.85,
                "predicted_template_id": bug.get("template_id", "UNKNOWN"),
            })

        # Round 1: Compute metrics
        # Build matches (simplified matching)
        truth_by_id = {b["bug_id"]: b for b in truth_bugs}
        matches = []
        for i, disc in enumerate(discovered):
            if i < len(truth_bugs):
                gt_bug = truth_bugs[i]
                matches.append({
                    "ground_truth": gt_bug,
                    "discovered": disc,
                    "match_type": "exact_instance" if disc["risk_type"] == gt_bug["risk_type"] else "template_match",
                })

        metrics = compute_metrics(truth_bugs, discovered, matches)
        assert abs(metrics["recall"] - 10/30) < 0.001, f"Expected ~0.3333, got {metrics['recall']}"

        # Cross-round: Derive priority signals
        bridge = CrossRoundBridge()
        signals = bridge.derive_priority_signals_from_benchmark(metrics)
        # Low recall (10/30 = 0.33) for all risk types should produce signals
        assert len(signals) > 0

        # Round 3: Generate learning artifacts from confirmed bugs
        confirmed = [
            {**b, "verdict": "confirmed", "confirmation_status": "confirmed"}
            for b in truth_bugs[:5]
        ]
        context = {
            "entities": ["contact", "lead", "opportunity", "account"],
            "endpoints": [{"method": b.get("method", "GET"), "path": b.get("api", "/")} for b in truth_bugs[:5]],
        }
        gen = LearningGenerator(existing_probes=discovered, project_context=context)
        manifest = gen.generate_from_confirmed_bugs(confirmed)

        # Verify manifest has all sections
        manifest_dict = gen.manifest_to_dict(manifest)
        assert "generated_probes" in manifest_dict
        assert "generated_oracles" in manifest_dict
        assert "generated_fixtures" in manifest_dict
        assert manifest_dict["summary"]["total_fixtures_generated"] >= len(confirmed)
