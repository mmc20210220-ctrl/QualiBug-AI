from __future__ import annotations

import benchmark_evaluator.benchmark_compute as benchmark_compute
from benchmark_evaluator.commercial_scoring_contract import (
    NOT_MEASURED,
    SCHEMA_VERSION,
    apply_commercial_scoring_contract,
)


def test_gt_unmatched_runtime_defect_is_not_false_positive() -> None:
    governed = apply_commercial_scoring_contract({
        "benchmark_active": True,
        "canonical_defects_evaluated": 5,
        "true_positives": 3,
        "false_positives": 2,
        "false_negatives": 7,
        "precision": 0.6,
        "false_positive_rate": 0.4,
        "f1_score": 0.4,
        "canonical_unmatched": ["defect:a", "defect:b"],
    })

    assert governed["true_positives"] == 3
    assert governed["false_negatives"] == 7
    assert governed["ground_truth_unmatched_runtime_defect_count"] == 2
    assert governed["benchmark_match_rate"] == 0.6
    assert governed["false_positives"] is None
    assert governed["precision"] == NOT_MEASURED
    assert governed["false_positive_rate"] == NOT_MEASURED
    assert governed["f1_score"] == NOT_MEASURED
    assert governed["benchmark_scoring_states"] == {
        "GT_MATCHED_RUNTIME_DEFECT": 3,
        "GT_MISSED_DEFECT": 7,
        "GT_UNMATCHED_RUNTIME_DEFECT": 2,
        "FALSE_POSITIVE": NOT_MEASURED,
        "TRUE_NEGATIVE": NOT_MEASURED,
    }
    assert governed["scoring_contract"] == SCHEMA_VERSION


def test_inactive_benchmark_is_not_rewritten() -> None:
    payload = {
        "benchmark_active": False,
        "ground_truth_available": False,
        "reason": "ground_truth_missing",
    }
    assert apply_commercial_scoring_contract(payload) == payload


def test_package_installs_contract_around_compute_benchmark() -> None:
    assert getattr(
        benchmark_compute.compute_benchmark,
        "_qualibug_commercial_scoring_contract",
        False,
    ) is True
    assert callable(
        getattr(
            benchmark_compute.compute_benchmark,
            "_qualibug_original_compute_benchmark",
            None,
        )
    )
