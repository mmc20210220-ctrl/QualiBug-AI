from __future__ import annotations

import pytest

from ai_test_asset_center.discovery_evaluation_contract import (
    EvaluationContractError,
    _aggregate_seeded,
    policy_metrics_from_evaluation_reports,
)


def _receipt(*, tp: int, fn: int, evaluated: int, unmatched: int) -> dict:
    return {
        "expectation": "seeded_defects",
        "measurement_status": "MEASURED",
        "metrics": {
            "true_positives": tp,
            "false_positives": None,
            "false_negatives": fn,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "precision": "NOT_MEASURED",
            "canonical_defects_evaluated": evaluated,
            "ground_truth_unmatched_runtime_defect_count": unmatched,
        },
    }


def test_seeded_aggregate_keeps_recall_but_not_precision_or_fp() -> None:
    aggregate = _aggregate_seeded(
        [
            _receipt(tp=3, fn=7, evaluated=5, unmatched=2),
            _receipt(tp=2, fn=8, evaluated=4, unmatched=2),
        ]
    )

    assert aggregate["true_positives"] == 5
    assert aggregate["false_negatives"] == 15
    assert aggregate["micro_recall"] == 0.25
    assert aggregate["false_positives"] is None
    assert aggregate["micro_precision"] is None
    assert aggregate["micro_f1"] is None
    assert aggregate["precision_measurement_status"] == "NOT_MEASURED"
    assert aggregate["false_positive_measurement_status"] == "NOT_MEASURED"
    assert aggregate["ground_truth_unmatched_runtime_defect_count"] == 4
    assert aggregate["canonical_defects_evaluated"] == 9
    assert aggregate["benchmark_match_rate"] == 0.5556


def test_policy_promotion_fails_closed_when_seeded_precision_is_unmeasured() -> None:
    report = {
        "held_in": {
            "measured_seeded_target_count": 1,
            "micro_precision": None,
        },
        "held_out": {
            "measured_seeded_target_count": 1,
            "micro_precision": None,
        },
    }

    with pytest.raises(
        EvaluationContractError,
        match="seeded_precision_not_measured",
    ):
        policy_metrics_from_evaluation_reports(report, report)
