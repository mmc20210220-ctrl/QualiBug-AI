from __future__ import annotations

from pathlib import Path

from benchmark_evaluator.enterprise_understanding.business_object_types import (
    load_business_object_ground_truth,
)
from benchmark_evaluator.enterprise_understanding.ticketsla_object_baseline import (
    PROJECT_ID,
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


def test_committed_ticketsla_sources_match_object_ground_truth_snapshot() -> None:
    ground_truth = load_business_object_ground_truth(GROUND_TRUTH)

    receipt = verify_frozen_source_snapshot(ROOT, ground_truth)

    assert receipt["status"] == "PASS"
    assert receipt["source_count"] == 3
    assert receipt["drift"] == []
    assert {row["status"] for row in receipt["sources"]} == {"MATCH"}
    assert {row["actual_blob_sha"] for row in receipt["sources"]} == {
        "b31f5b1a5da7c16a64767996a836839b8c262745",
        "fbb81d2cc690bcae0cf11cc168e2a0276bb0a45c",
        "33a386809c6f9d75107e864aefa3ae32e5b98560",
    }
