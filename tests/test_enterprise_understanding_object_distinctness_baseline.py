from __future__ import annotations

from pathlib import Path

from benchmark_evaluator.enterprise_understanding.business_object_baseline import verify_frozen_source_snapshot
from benchmark_evaluator.enterprise_understanding.object_distinctness_baseline import PROJECT_ID, SOURCE_SPECS, SPEC
from benchmark_evaluator.enterprise_understanding.object_distinctness_review import load_object_distinctness_ground_truth

ROOT = Path(__file__).resolve().parents[1]
GROUND_TRUTH = ROOT / "benchmark_evaluator" / "enterprise_understanding" / "fixtures" / PROJECT_ID / "structural_review_ground_truth.json"


def test_object_distinctness_sources_match_frozen_snapshot() -> None:
    truth = load_object_distinctness_ground_truth(GROUND_TRUTH)
    receipt = verify_frozen_source_snapshot(ROOT, truth, SPEC)

    assert receipt["status"] == "PASS"
    assert receipt["source_count"] == 3
    assert receipt["drift"] == []
    assert {row["path"] for row in receipt["sources"]} == {relative for relative, _source_type in SOURCE_SPECS}


def test_object_distinctness_ground_truth_is_external_closed_world() -> None:
    truth = load_object_distinctness_ground_truth(GROUND_TRUTH)
    receipt = truth["validation_receipt"]

    assert truth["project_id"] == PROJECT_ID
    assert truth["ground_truth_generated_from_product_output"] is False
    assert receipt["closed_world"] is True
    assert receipt["generated_from_product_output"] is False
    assert receipt["expected_review_pair_count"] == 1
    assert receipt["expected_suppressed_pair_count"] == 2
    assert receipt["automatic_entity_union_allowed"] is False
