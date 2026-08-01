from __future__ import annotations

from pathlib import Path

from benchmark_evaluator.enterprise_understanding.business_object_types import (
    load_business_object_ground_truth,
)
from benchmark_evaluator.enterprise_understanding.warehouse_object_baseline import (
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


def test_committed_warehouse_sources_match_object_ground_truth_snapshot() -> None:
    ground_truth = load_business_object_ground_truth(GROUND_TRUTH)

    receipt = verify_frozen_source_snapshot(ROOT, ground_truth)

    assert receipt["status"] == "PASS"
    assert receipt["source_count"] == 4
    assert receipt["drift"] == []
    assert {row["status"] for row in receipt["sources"]} == {"MATCH"}
    assert {row["path"] for row in receipt["sources"]} == {
        relative for relative, _source_type in SOURCE_SPECS
    }
    assert {row["actual_blob_sha"] for row in receipt["sources"]} == {
        "8bb44a140b13ef730e3df610ca182630996aa138",
        "3833524fb39869f2b1738cc440b35355cd1c06ba",
        "50e6c35b1572da3fb57b6f14275bd9c966ed01ac",
        "eed984a73ef99a53a86109861e4b1b752d1c4afa",
    }


def test_warehouse_ground_truth_is_external_closed_world_annotation() -> None:
    ground_truth = load_business_object_ground_truth(GROUND_TRUTH)
    receipt = ground_truth["validation_receipt"]

    assert ground_truth["project_id"] == PROJECT_ID
    assert ground_truth["ground_truth_generated_from_product_output"] is False
    assert receipt["closed_world"] is True
    assert receipt["generated_from_product_output"] is False
    assert receipt["expected_object_label_count"] == 28
    assert receipt["polysemous_label_count"] >= 3
