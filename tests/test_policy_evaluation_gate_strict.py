from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.policy_evaluation_gate import PolicyPromotionGate


def _metrics() -> dict:
    return {
        "evaluation_complete": True,
        "commercial_shape_ready": True,
        "operational_metrics_complete": True,
        "sample_count": 10,
        "unique_industry_count": 3,
        "true_positives": 20,
        "false_positives": 3,
        "false_negatives": 20,
        "held_in_recall": 0.50,
        "held_in_precision": 0.80,
        "held_in_f1": 0.615,
        "held_out_recall": 0.40,
        "held_out_precision": 0.75,
        "held_out_f1": 0.522,
        "shadow_held_in_f1": 0.60,
        "shadow_held_out_f1": 0.50,
        "macro_industry_recall": 0.40,
        "min_industry_recall": 0.25,
        "clean_false_positives": 0,
        "clean_critical_high_false_positives": 0,
        "evidence_quality_score": 0.95,
        "reproducibility_rate": 0.95,
        "engine_success_rate": 0.98,
        "execution_success_rate": 0.96,
        "duplicate_rate": 0.02,
        "regression_failures": 0,
        "safety_incidents": 0,
        "production_http_requests": 0,
        "cleanup_failures": 0,
        "dirty_test_environments": 0,
        "cost_per_true_positive_usd": 10.0,
        "wall_clock_seconds": 100.0,
    }


def _evidence() -> dict:
    return {
        "replay_executed": True,
        "shadow_executed": True,
        "held_in_executed": True,
        "held_out_executed": True,
        "clean_executed": True,
        "dataset_version": "dataset-v1",
        "dataset_manifest_fingerprint": "manifest-fingerprint",
        "paired_target_count": 5,
        "replay_run_ids": [f"replay-{index}" for index in range(10)],
        "shadow_run_ids": [f"shadow-{index}" for index in range(10)],
        "same_runtime_fingerprint": "runtime",
        "same_input_fingerprint": "input",
        "same_fixture_fingerprint": "fixture",
        "same_context_artifact_id": "context",
        "same_environment_id": "environment",
        "target_receipt_fingerprints": [f"target-{index}" for index in range(5)],
    }


def test_promotes_only_non_regressive_measured_split_improvement() -> None:
    champion = _metrics()
    challenger = deepcopy(champion)
    challenger["true_positives"] = 24
    challenger["false_negatives"] = 16
    challenger["held_out_recall"] = 0.50
    challenger["held_out_f1"] = 0.60
    challenger["shadow_held_out_f1"] = 0.58
    challenger["macro_industry_recall"] = 0.50
    challenger["min_industry_recall"] = 0.35
    challenger["cost_per_true_positive_usd"] = 9.0

    decision = PolicyPromotionGate().evaluate(champion, challenger, _evidence())

    assert decision["promote"] is True
    assert decision["reason"] == "PROMOTE_MEASURED_NON_REGRESSIVE_IMPROVEMENT"
    assert decision["split_improvements"]["held_out_recall"] is True
    assert all(item["passed"] for item in decision["hard_checks"])
    assert all(item["passed"] for item in decision["quality_checks"])


def test_rejects_more_bugs_when_held_out_precision_regresses() -> None:
    champion = _metrics()
    challenger = deepcopy(champion)
    challenger["true_positives"] = 25
    challenger["held_out_recall"] = 0.55
    challenger["held_out_f1"] = 0.56
    challenger["held_out_precision"] = 0.60

    decision = PolicyPromotionGate().evaluate(champion, challenger, _evidence())

    assert decision["promote"] is False
    assert decision["reason"] == "REJECTED_QUALITY_REGRESSION"
    failed = {item["name"] for item in decision["quality_checks"] if not item["passed"]}
    assert "held_out_precision" in failed


def test_clean_p0_p1_false_positive_is_hard_blocker() -> None:
    champion = _metrics()
    challenger = deepcopy(champion)
    challenger["held_out_recall"] = 0.50
    challenger["held_out_f1"] = 0.60
    challenger["clean_false_positives"] = 1
    challenger["clean_critical_high_false_positives"] = 1

    decision = PolicyPromotionGate().evaluate(champion, challenger, _evidence())

    assert decision["promote"] is False
    assert decision["reason"] == "BLOCKED_BY_SAFETY_OR_EVALUATION_EVIDENCE"
    failed = {item["name"] for item in decision["hard_checks"] if not item["passed"]}
    assert "clean_critical_high_false_positives" in failed


def test_legacy_estimate_cannot_satisfy_observed_promotion_gate() -> None:
    decision = PolicyPromotionGate().evaluate(
        {"confirmed_bugs": 5},
        {"confirmed_bugs": 10},
        {"replay_executed": True, "dataset_version": "legacy"},
    )

    assert decision["promote"] is False
    assert decision["reason"] == "BLOCKED_BY_SAFETY_OR_EVALUATION_EVIDENCE"
    failed = {item["name"] for item in decision["hard_checks"] if not item["passed"]}
    assert {"shadow_executed", "held_out_executed", "dataset_manifest_fingerprint"} <= failed


def test_rejects_unit_cost_increase_over_ten_percent() -> None:
    champion = _metrics()
    challenger = deepcopy(champion)
    challenger["held_out_recall"] = 0.50
    challenger["held_out_f1"] = 0.60
    challenger["cost_per_true_positive_usd"] = 11.01

    decision = PolicyPromotionGate().evaluate(champion, challenger, _evidence())

    assert decision["promote"] is False
    assert decision["reason"] == "REJECTED_QUALITY_REGRESSION"
    assert "cost_per_true_positive" in {
        item["name"] for item in decision["quality_checks"] if not item["passed"]
    }
