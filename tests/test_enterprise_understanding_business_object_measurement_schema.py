from __future__ import annotations

from benchmark_evaluator.enterprise_understanding.business_object_types import (
    BUSINESS_OBJECT_MEASUREMENT_SCHEMA,
    evaluate_business_object_types,
    validate_business_object_ground_truth,
)


def test_evaluator_schema_remains_authoritative_over_product_benchmark_schema() -> None:
    ground_truth = validate_business_object_ground_truth(
        {
            "schema": "qualibug.enterprise-business-object-ground-truth.v1",
            "project_id": "schema-authority",
            "annotation_scope": "CLOSED_WORLD_SOURCE_LABELS",
            "ground_truth_generated_from_product_output": False,
            "source_snapshot": [{"path": "business.md", "blob_sha": "abc"}],
            "labels": [
                {
                    "ground_truth_id": "gt:order",
                    "canonical_label": "Order",
                    "expected_business_object": True,
                    "semantic_roles": ["BUSINESS_OBJECT"],
                    "source_refs": ["business.md"],
                }
            ],
        }
    )
    asset = {
        "business_object_recognition": {
            "candidates": [
                {
                    "candidate_id": "candidate:order",
                    "comparison_key": "order",
                    "labels": ["Order"],
                    "status": "ACCEPTED",
                }
            ],
            "accepted_comparison_keys": ["order"],
            "unknowns": [],
        }
    }

    measured = evaluate_business_object_types(ground_truth, asset)

    assert measured["status"] == "MEASURED"
    assert measured["schema"] == BUSINESS_OBJECT_MEASUREMENT_SCHEMA
    assert (
        measured["product_business_object_benchmark_schema"]
        == "qualibug.enterprise-business-object-benchmark-result.v1"
    )
