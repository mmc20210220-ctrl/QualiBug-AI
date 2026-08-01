from __future__ import annotations

from pathlib import Path

from benchmark_evaluator.enterprise_understanding.business_object_types import (
    load_business_object_ground_truth,
)
from benchmark_evaluator.enterprise_understanding.crm_prd_only_object_baseline import (
    PROJECT_ID,
    SOURCE_SPECS,
    verify_frozen_source_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
GROUND_TRUTH = (
    ROOT
    / "benchmark_evaluator"
    / "enterprise_understanding"
    / "fixtures"
    / PROJECT_ID
    / "business_object_ground_truth.json"
)


def test_committed_crm_prd_only_source_matches_ground_truth_snapshot() -> None:
    ground_truth = load_business_object_ground_truth(GROUND_TRUTH)
    receipt = verify_frozen_source_snapshot(ROOT, ground_truth)

    assert receipt["status"] == "PASS"
    assert receipt["source_count"] == 1
    assert receipt["drift"] == []
    assert {row["path"] for row in receipt["sources"]} == {
        relative for relative, _source_type in SOURCE_SPECS
    }
    assert {row["actual_blob_sha"] for row in receipt["sources"]} == {
        "c983ccc1889d2582bf9c242f1d9eb3ddda1199c0"
    }


def test_crm_prd_only_ground_truth_is_external_closed_world_annotation() -> None:
    ground_truth = load_business_object_ground_truth(GROUND_TRUTH)
    receipt = ground_truth["validation_receipt"]

    assert ground_truth["project_id"] == PROJECT_ID
    assert ground_truth["ground_truth_generated_from_product_output"] is False
    assert receipt["closed_world"] is True
    assert receipt["generated_from_product_output"] is False
    assert receipt["expected_object_label_count"] == 4
    assert receipt["expected_non_object_label_count"] == 6
