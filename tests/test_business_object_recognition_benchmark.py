from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding._object_recognition_projection import (
    apply_recognition_to_model,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.business_object_benchmark import (
    ANNOTATION_SCOPE,
    GROUND_TRUTH_SCHEMA,
    evaluate_business_object_recognition,
    project_business_object_benchmark,
)


def _candidate(label: str, status: str, reason: str = "") -> dict:
    return {
        "candidate_id": f"candidate:{label}",
        "comparison_key": label.casefold(),
        "labels": [label],
        "status": status,
        "reason_code": reason or status,
    }


def _recognition(candidates: list[dict]) -> dict:
    accepted_statuses = {
        "ACCEPTED",
        "ACCEPTED_BY_SOURCE_ALIAS",
        "ACCEPTED_SURFACE_FORM_IDENTITY_PENDING",
        "ACCEPTED_WITH_ROLE_COLLISION_OVERRIDE",
    }
    return {
        "recognition_id": "recognition:test",
        "candidates": candidates,
        "accepted_comparison_keys": [
            row["comparison_key"]
            for row in candidates
            if row["status"] in accepted_statuses
        ],
        "accepted_labels": [
            row["labels"][0]
            for row in candidates
            if row["status"] in accepted_statuses
        ],
        "unknowns": [],
        "gate": {
            "status": "PASS",
            "entry_allowed": True,
            "metrics": {
                "candidate_count": len(candidates),
                "accepted_label_count": len(
                    [row for row in candidates if row["status"] in accepted_statuses]
                ),
                "type_conflict_count": 0,
            },
        },
    }


def _truth(rows: list[tuple[str, str]]) -> dict:
    return {
        "schema": GROUND_TRUTH_SCHEMA,
        "benchmark_id": "business-object-ground-truth",
        "annotation_scope": ANNOTATION_SCOPE,
        "ground_truth_generated_from_product_output": False,
        "labels": [
            {
                "label": label,
                "expected_type": expected_type,
                "annotation_status": "CONFIRMED",
            }
            for label, expected_type in rows
        ],
    }


def test_perfect_object_type_classification_is_measured() -> None:
    recognition = _recognition(
        [
            _candidate("order", "ACCEPTED"),
            _candidate("admin", "CONFLICTED", "BUSINESS_OBJECT_ROLE_COLLISION"),
            _candidate("draft", "PENDING_SOURCE_EVIDENCE"),
        ]
    )
    measured = evaluate_business_object_recognition(
        recognition,
        _truth(
            [
                ("order", "BUSINESS_OBJECT"),
                ("admin", "ACTOR"),
                ("draft", "STATE"),
            ]
        ),
    )

    assert measured["status"] == "MEASURED"
    assert measured["metrics"]["object_type_precision"] == 1.0
    assert measured["metrics"]["object_type_recall"] == 1.0
    assert measured["metrics"]["object_overpromotion_rate"] == 0.0
    assert measured["metrics"]["object_miss_rate"] == 0.0


def test_overpromotion_miss_confusion_and_unknown_coverage_are_separate() -> None:
    recognition = _recognition(
        [
            _candidate("order", "PENDING_SOURCE_EVIDENCE"),
            _candidate("admin", "ACCEPTED"),
            _candidate("draft", "ACCEPTED_WITH_ROLE_COLLISION_OVERRIDE"),
        ]
    )
    measured = evaluate_business_object_recognition(
        recognition,
        _truth(
            [
                ("order", "BUSINESS_OBJECT"),
                ("admin", "ACTOR"),
                ("draft", "STATE"),
            ]
        ),
    )

    metrics = measured["metrics"]
    assert metrics["false_positive_object_count"] == 2
    assert metrics["false_negative_object_count"] == 1
    assert metrics["type_confusion_distribution"] == {"ACTOR": 1, "STATE": 1}
    assert metrics["object_error_unknown_covered_count"] == 2
    assert metrics["silent_object_error_count"] == 1
    assert metrics["object_error_unknown_coverage_rate"] == 0.666667
    assert measured["false_positive_objects"]
    assert measured["false_negative_objects"]


def test_incomplete_closed_world_annotations_cannot_claim_precision() -> None:
    recognition = _recognition(
        [_candidate("order", "ACCEPTED"), _candidate("admin", "ACCEPTED")]
    )
    measured = evaluate_business_object_recognition(
        recognition,
        _truth([("order", "BUSINESS_OBJECT")]),
    )

    assert measured["status"] == "NOT_MEASURED"
    assert (
        measured["reason_code"]
        == "BUSINESS_OBJECT_GROUND_TRUTH_CANDIDATE_UNIVERSE_INCOMPLETE"
    )
    assert measured["quality_claim_allowed"] is False
    assert measured["details"]["unannotated_candidate_keys"] == ["admin"]


def test_conflicting_annotations_are_not_resolved_by_product_output() -> None:
    recognition = _recognition([_candidate("order", "ACCEPTED")])
    ground_truth = _truth(
        [("order", "BUSINESS_OBJECT"), ("order", "ACTOR")]
    )

    measured = evaluate_business_object_recognition(recognition, ground_truth)

    assert measured["status"] == "NOT_MEASURED"
    assert measured["reason_code"] == "BUSINESS_OBJECT_GROUND_TRUTH_CONFLICTED"
    assert measured["quality_claim_allowed"] is False


def test_projection_keeps_recall_unmeasured_without_external_ground_truth() -> None:
    asset: dict = {}
    recognition = _recognition([_candidate("order", "ACCEPTED")])

    projected = project_business_object_benchmark(asset, recognition)
    model = apply_recognition_to_model(
        {"gate": {}, "metrics": {}, "unknowns": [], "model_id": "model:test"},
        projected,
    )

    assert projected["benchmark"]["status"] == "NOT_MEASURED"
    assert projected["gate"]["measured_precision_recall_claim_allowed"] is False
    assert model["metrics"]["business_object_recognition_is_measured_recall"] is False
    assert model["business_object_benchmark"]["quality_claim_allowed"] is False


def test_measured_projection_exposes_object_quality_in_model_metrics() -> None:
    asset = {
        "enterprise_business_object_ground_truth": _truth(
            [("order", "BUSINESS_OBJECT"), ("admin", "ACTOR")]
        )
    }
    recognition = _recognition(
        [_candidate("order", "ACCEPTED"), _candidate("admin", "CONFLICTED")]
    )

    projected = project_business_object_benchmark(asset, recognition)
    model = apply_recognition_to_model(
        {"gate": {}, "metrics": {}, "unknowns": [], "model_id": "model:test"},
        projected,
    )

    assert projected["benchmark"]["status"] == "MEASURED"
    assert model["metrics"]["business_object_recognition_is_measured_recall"] is True
    assert model["metrics"]["business_object_type_precision"] == 1.0
    assert model["metrics"]["business_object_type_recall"] == 1.0
