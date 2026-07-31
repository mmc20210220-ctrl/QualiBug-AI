from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding._object_role_evidence import (
    comparison_key,
)
from benchmark_evaluator.enterprise_understanding.business_object_types import (
    evaluate_business_object_types,
    load_business_object_ground_truth,
    validate_business_object_ground_truth,
)

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "benchmark_evaluator"
    / "enterprise_understanding"
    / "fixtures"
    / "ticketsla_d"
    / "business_object_ground_truth.json"
)


def _candidate(label: str, accepted: bool) -> dict:
    return {
        "candidate_id": f"candidate:{comparison_key(label)}",
        "comparison_key": comparison_key(label),
        "labels": [label],
        "status": "ACCEPTED" if accepted else "PENDING_SOURCE_EVIDENCE",
    }


def test_ticketsla_object_type_ground_truth_is_closed_world_and_source_backed() -> None:
    ground_truth = load_business_object_ground_truth(FIXTURE)
    receipt = ground_truth["validation_receipt"]

    assert receipt["status"] == "PASS"
    assert receipt["closed_world"] is True
    assert receipt["label_row_count"] == 17
    assert receipt["normalized_label_count"] == 187
    assert receipt["expected_object_label_count"] == 26
    assert receipt["expected_non_object_label_count"] == 161
    assert receipt["polysemous_label_count"] == 21
    assert receipt["source_snapshot_count"] == 3
    assert receipt["generated_from_product_output"] is False
    assert receipt["model_writeback_allowed"] is False


def test_object_actor_polysemy_does_not_create_false_ground_truth_conflict() -> None:
    ground_truth = validate_business_object_ground_truth(
        {
            "schema": "qualibug.enterprise-business-object-ground-truth.v1",
            "project_id": "polysemy",
            "annotation_scope": "CLOSED_WORLD_SOURCE_LABELS",
            "ground_truth_generated_from_product_output": False,
            "source_snapshot": [{"path": "business.md", "blob_sha": "abc"}],
            "labels": [
                {
                    "ground_truth_id": "gt:customer:object",
                    "canonical_label": "Customer",
                    "expected_business_object": True,
                    "semantic_roles": ["BUSINESS_OBJECT"],
                    "source_refs": ["business.md"],
                },
                {
                    "ground_truth_id": "gt:customer:actor",
                    "canonical_label": "CUSTOMER",
                    "expected_business_object": True,
                    "semantic_roles": ["ACTOR"],
                    "source_refs": ["business.md"],
                },
            ],
        }
    )
    asset = {
        "business_object_recognition": {
            "candidates": [_candidate("Customer", True)],
            "accepted_comparison_keys": [comparison_key("Customer")],
            "unknowns": [],
        }
    }

    measured = evaluate_business_object_types(ground_truth, asset)

    assert measured["status"] == "MEASURED"
    assert measured["metrics"]["object_type_precision"] == 1.0
    assert measured["metrics"]["object_type_recall"] == 1.0
    assert measured["metrics"]["polysemous_annotated_label_count"] == 1
    assert measured["polysemous_annotations"][0]["semantic_roles"] == [
        "BUSINESS_OBJECT",
        "ACTOR",
    ]


def test_unannotated_product_candidate_blocks_object_quality_claim() -> None:
    ground_truth = validate_business_object_ground_truth(
        {
            "schema": "qualibug.enterprise-business-object-ground-truth.v1",
            "project_id": "closed-world",
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
            "candidates": [_candidate("Order", True), _candidate("Mystery", True)],
            "accepted_comparison_keys": [
                comparison_key("Order"),
                comparison_key("Mystery"),
            ],
            "unknowns": [],
        }
    }

    measured = evaluate_business_object_types(ground_truth, asset)

    assert measured["status"] == "NOT_MEASURED"
    assert (
        measured["reason_code"]
        == "BUSINESS_OBJECT_GROUND_TRUTH_CANDIDATE_UNIVERSE_INCOMPLETE"
    )
    assert measured["quality_claim_allowed"] is False
    assert measured["details"]["unannotated_candidate_keys"] == ["mystery"]


def test_evaluator_reads_frozen_recognition_without_mutating_product_asset() -> None:
    ground_truth = validate_business_object_ground_truth(
        {
            "schema": "qualibug.enterprise-business-object-ground-truth.v1",
            "project_id": "immutable",
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
                },
                {
                    "ground_truth_id": "gt:admin",
                    "canonical_label": "ADMIN",
                    "expected_business_object": False,
                    "semantic_roles": ["ACTOR"],
                    "source_refs": ["business.md"],
                },
            ],
        }
    )
    asset = {
        "enterprise_understanding_model": {
            "business_object_recognition": {
                "candidates": [_candidate("Order", True), _candidate("ADMIN", False)],
                "accepted_comparison_keys": [comparison_key("Order")],
                "unknowns": [],
            }
        }
    }
    before = deepcopy(asset)

    measured = evaluate_business_object_types(ground_truth, asset)

    assert measured["status"] == "MEASURED"
    assert measured["ground_truth_entered_product_runtime"] is False
    assert measured["model_writeback_allowed"] is False
    assert asset == before
