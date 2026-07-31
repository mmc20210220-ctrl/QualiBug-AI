from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_benchmark import (
    ANNOTATION_SCOPE,
    GROUND_TRUTH_SCHEMA,
    QUALITY_POLICY_SCHEMA,
    evaluate_identity_resolution,
    project_identity_benchmark,
)


def _result(clusters: list[list[tuple[str, str, str]]]) -> dict:
    mentions = []
    projected = []
    for cluster_index, rows in enumerate(clusters):
        mention_ids = []
        for mention_ref, label, source in rows:
            mention_ids.append(mention_ref)
            mentions.append(
                {
                    "mention_id": mention_ref,
                    "mention_type": "BUSINESS_OBJECT",
                    "raw_label": label,
                    "source_id": source,
                    "source_locator": f"{source}:line:1",
                    "scope": {"system": source},
                }
            )
        projected.append(
            {
                "entity_id": f"entity:{cluster_index}",
                "canonical_label": rows[0][1],
                "member_mention_ids": mention_ids,
            }
        )
    return {
        "mentions": mentions,
        "clusters": projected,
        "edges": [],
        "conflicts": [],
        "gate": {"status": "PASS", "entry_allowed": True, "metrics": {}},
    }


def _truth(clusters: list[list[str]]) -> dict:
    return {
        "schema": GROUND_TRUTH_SCHEMA,
        "benchmark_id": "identity-ground-truth",
        "annotation_scope": ANNOTATION_SCOPE,
        "ground_truth_generated_from_product_output": False,
        "clusters": [
            {
                "cluster_ref": f"truth:{index}",
                "member_refs": refs,
                "annotation_status": "CONFIRMED",
            }
            for index, refs in enumerate(clusters)
        ],
    }


def _policy(
    *, enforce: bool = True, precision: float = 0.95, recall: float = 0.95
) -> dict:
    return {
        "schema": QUALITY_POLICY_SCHEMA,
        "enforce": enforce,
        "thresholds": {
            "minimum_pairwise_precision": precision,
            "minimum_pairwise_recall": recall,
            "maximum_overmerge_rate": 0.05,
            "maximum_undermerge_rate": 0.05,
            "maximum_silent_identity_error_count": 0,
        },
    }


def test_perfect_occurrence_clusters_measure_one() -> None:
    result = _result(
        [
            [("m:order", "Order", "prd"), ("m:so", "SO", "api")],
            [
                ("m:customer", "Customer", "prd"),
                ("m:buyer", "Buyer", "db"),
            ],
        ]
    )
    measured = evaluate_identity_resolution(
        result,
        _truth([["m:order", "m:so"], ["m:customer", "m:buyer"]]),
    )

    assert measured["status"] == "MEASURED"
    assert measured["metrics"]["pairwise_precision"] == 1.0
    assert measured["metrics"]["pairwise_recall"] == 1.0
    assert measured["metrics"]["pairwise_f1"] == 1.0
    assert measured["metrics"]["exact_cluster_match_rate"] == 1.0
    assert measured["metrics"]["overmerge_rate"] == 0.0
    assert measured["metrics"]["undermerge_rate"] == 0.0


def test_duplicate_labels_in_different_systems_are_distinct_occurrences() -> None:
    result = _result(
        [[("m:erp-order", "Order", "erp"), ("m:crm-order", "Order", "crm")]]
    )
    measured = evaluate_identity_resolution(
        result,
        _truth([["m:erp-order"], ["m:crm-order"]]),
    )

    assert measured["status"] == "MEASURED"
    assert measured["metrics"]["false_positive_pair_count"] == 1
    assert measured["metrics"]["pairwise_precision"] == 0.0
    assert measured["metrics"]["overmerge_rate"] == 1.0
    assert measured["false_positive_pairs"][0]["left"]["label"] == "Order"
    assert measured["false_positive_pairs"][0]["right"]["label"] == "Order"
    assert (
        measured["false_positive_pairs"][0]["left"]["source_id"]
        != measured["false_positive_pairs"][0]["right"]["source_id"]
    )


def test_overmerge_and_undermerge_are_separate() -> None:
    result = _result(
        [
            [
                ("m:order", "Order", "prd"),
                ("m:so", "SO", "api"),
                ("m:customer", "Customer", "prd"),
            ],
            [("m:buyer", "Buyer", "db")],
        ]
    )
    measured = evaluate_identity_resolution(
        result,
        _truth([["m:order", "m:so"], ["m:customer", "m:buyer"]]),
    )

    assert measured["metrics"]["false_positive_pair_count"] == 2
    assert measured["metrics"]["false_negative_pair_count"] == 1
    assert measured["metrics"]["pairwise_precision"] < 1.0
    assert measured["metrics"]["pairwise_recall"] < 1.0
    assert measured["metrics"]["silent_identity_error_count"] == 3
    assert measured["false_positive_pairs"]
    assert measured["false_negative_pairs"]


def test_candidate_edge_surfaces_undermerge_without_changing_truth() -> None:
    result = _result(
        [[("m:order", "Order", "prd")], [("m:so", "SO", "api")]]
    )
    result["edges"] = [
        {
            "relation": "SAME_AS",
            "status": "CANDIDATE_ONLY",
            "left_mention_id": "m:order",
            "right_mention_id": "m:so",
        }
    ]
    measured = evaluate_identity_resolution(
        result,
        _truth([["m:order", "m:so"]]),
    )

    assert measured["metrics"]["false_negative_pair_count"] == 1
    assert measured["metrics"]["identity_error_unknown_coverage_rate"] == 1.0
    assert measured["metrics"]["silent_identity_error_count"] == 0
    assert measured["false_negative_pairs"][0]["uncertainty_surfaced"] is True


def test_incomplete_closed_world_annotations_cannot_claim_precision() -> None:
    result = _result(
        [
            [("m:order", "Order", "prd")],
            [("m:customer", "Customer", "prd")],
        ]
    )
    measured = evaluate_identity_resolution(
        result,
        _truth([["m:order"]]),
    )

    assert measured["status"] == "NOT_MEASURED"
    assert (
        measured["reason_code"]
        == "IDENTITY_GROUND_TRUTH_MENTION_UNIVERSE_INCOMPLETE"
    )
    assert measured["quality_claim_allowed"] is False
    assert measured["details"]["unannotated_product_mentions"] == ["m:customer"]


def test_ground_truth_cannot_be_copied_from_product_output() -> None:
    result = _result([[("m:order", "Order", "prd")]])
    ground_truth = _truth([["m:order"]])
    ground_truth["ground_truth_generated_from_product_output"] = True

    measured = evaluate_identity_resolution(result, ground_truth)

    assert measured["status"] == "NOT_MEASURED"
    assert measured["reason_code"] == "PRODUCT_OUTPUT_CANNOT_BE_IDENTITY_GROUND_TRUTH"
    assert measured["quality_claim_allowed"] is False


def test_enforced_quality_policy_blocks_identity_gate_on_real_errors() -> None:
    asset = {
        "enterprise_identity_ground_truth": _truth(
            [["m:erp-order"], ["m:crm-order"]]
        ),
        "enterprise_identity_quality_policy": _policy(),
    }
    result = _result(
        [[("m:erp-order", "Order", "erp"), ("m:crm-order", "Order", "crm")]]
    )

    projected = project_identity_benchmark(asset, result)

    assert projected["benchmark"]["status"] == "MEASURED"
    assert (
        projected["benchmark"]["quality_gate"]["status"]
        == "BLOCKED_IDENTITY_QUALITY_THRESHOLD"
    )
    assert projected["gate"]["status"] == "BLOCKED_ENTERPRISE_IDENTITY_QUALITY_GATE"
    assert projected["gate"]["entry_allowed"] is False
    assert projected["gate"]["business_understanding_allowed"] is False


def test_non_enforced_quality_policy_reports_failure_without_blocking() -> None:
    asset = {
        "enterprise_identity_ground_truth": _truth(
            [["m:erp-order"], ["m:crm-order"]]
        ),
        "enterprise_identity_quality_policy": _policy(enforce=False),
    }
    result = _result(
        [[("m:erp-order", "Order", "erp"), ("m:crm-order", "Order", "crm")]]
    )

    projected = project_identity_benchmark(asset, result)

    assert (
        projected["benchmark"]["quality_gate"]["status"]
        == "BLOCKED_IDENTITY_QUALITY_THRESHOLD"
    )
    assert projected["benchmark"]["quality_gate"]["entry_allowed"] is True
    assert projected["gate"]["status"] == "PASS"
    assert projected["gate"]["entry_allowed"] is True


def test_no_ground_truth_is_not_measured_and_not_blocked_by_default() -> None:
    asset: dict = {}
    result = _result([[("m:order", "Order", "prd")]])

    projected = project_identity_benchmark(asset, result)

    assert projected["benchmark"]["status"] == "NOT_MEASURED"
    assert projected["benchmark"]["quality_claim_allowed"] is False
    assert projected["benchmark"]["quality_gate"]["status"] == "NOT_CONFIGURED"
    assert projected["gate"]["measurement_status"] == "NOT_MEASURED"
    assert projected["gate"]["entry_allowed"] is True
