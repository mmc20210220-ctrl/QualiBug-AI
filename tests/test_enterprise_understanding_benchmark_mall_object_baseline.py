from __future__ import annotations

from pathlib import Path

from benchmark_evaluator.enterprise_understanding.benchmark_mall_object_baseline import (
    PROJECT_ID,
    SOURCE_SPECS,
    verify_frozen_source_snapshot,
)
from benchmark_evaluator.enterprise_understanding.business_object_types import (
    load_business_object_ground_truth,
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


def test_committed_benchmark_mall_sources_match_object_ground_truth_snapshot() -> None:
    ground_truth = load_business_object_ground_truth(GROUND_TRUTH)
    receipt = verify_frozen_source_snapshot(ROOT, ground_truth)

    assert receipt["status"] == "PASS"
    assert receipt["source_count"] == 7
    assert receipt["drift"] == []
    assert {row["status"] for row in receipt["sources"]} == {"MATCH"}
    assert {row["path"] for row in receipt["sources"]} == {
        relative for relative, _source_type in SOURCE_SPECS
    }
    assert {row["actual_blob_sha"] for row in receipt["sources"]} == {
        "5fd06faf7cd391af92c3f8bbbe4e5d8e8dfaee0f",
        "5d2ed786dae93cdebb830b4166ba4469155a43d1",
        "9bde7fe4f8894cdcbc1e749f329699956c080928",
        "7502e130893cb93106d2ec7f0f7df1e265035774",
        "9419bce3daa163bae68c689e5d0b10449fe9de85",
        "1f4ba2f26299af4f272bbb7f89ab3082f3e43a24",
        "5781422adbdb644dc083a017c626c873a0d548f6",
    }


def test_benchmark_mall_ground_truth_is_external_closed_world_annotation() -> None:
    ground_truth = load_business_object_ground_truth(GROUND_TRUTH)
    receipt = ground_truth["validation_receipt"]

    assert ground_truth["project_id"] == PROJECT_ID
    assert ground_truth["ground_truth_generated_from_product_output"] is False
    assert receipt["closed_world"] is True
    assert receipt["generated_from_product_output"] is False
    assert receipt["expected_object_label_count"] == 29
    assert receipt["expected_non_object_label_count"] == 53
    assert receipt["polysemous_label_count"] >= 3
