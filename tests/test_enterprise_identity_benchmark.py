from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_benchmark import (
    GROUND_TRUTH_SCHEMA,
    evaluate_identity_resolution,
    project_identity_benchmark,
)


def _result(clusters: list[list[str]]) -> dict:
    mentions = []
    projected = []
    for cluster_index, labels in enumerate(clusters):
        mention_ids = []
        for label_index, label in enumerate(labels):
            mention_id = f"mention:{cluster_index}:{label_index}"
            mention_ids.append(mention_id)
            mentions.append({"mention_id": mention_id, "raw_label": label})
        projected.append(
            {
                "entity_id": f"entity:{cluster_index}",
                "canonical_label": labels[0],
                "member_mention_ids": mention_ids,
            }
        )
    return {
        "mentions": mentions,
        "clusters": projected,
        "gate": {"metrics": {}},
    }


def _truth(clusters: list[list[str]]) -> dict:
    return {
        "schema": GROUND_TRUTH_SCHEMA,
        "benchmark_id": "identity-ground-truth",
        "clusters": [
            {"canonical_label": labels[0], "member_labels": labels}
            for labels in clusters
        ],
    }


def test_perfect_identity_clusters_measure_one() -> None:
    measured = evaluate_identity_resolution(
        _result([["Order", "SO"], ["Customer", "Buyer"]]),
        _truth([["Order", "SO"], ["Customer", "Buyer"]]),
    )

    assert measured["status"] == "MEASURED"
    assert measured["metrics"]["pairwise_precision"] == 1.0
    assert measured["metrics"]["pairwise_recall"] == 1.0
    assert measured["metrics"]["overmerge_rate"] == 0.0
    assert measured["metrics"]["undermerge_rate"] == 0.0


def test_overmerge_and_undermerge_are_separate() -> None:
    measured = evaluate_identity_resolution(
        _result([["Order", "SO", "Customer"], ["Buyer"]]),
        _truth([["Order", "SO"], ["Customer", "Buyer"]]),
    )

    assert measured["metrics"]["false_positive_pair_count"] == 2
    assert measured["metrics"]["false_negative_pair_count"] == 1
    assert measured["metrics"]["pairwise_precision"] < 1.0
    assert measured["metrics"]["pairwise_recall"] < 1.0
    assert measured["false_positive_pairs"]
    assert measured["false_negative_pairs"]


def test_no_ground_truth_is_explicitly_not_measured() -> None:
    asset = {}
    result = _result([["Order", "SO"]])

    projected = project_identity_benchmark(asset, result)

    assert projected["benchmark"]["status"] == "NOT_MEASURED"
    assert projected["benchmark"]["quality_claim_allowed"] is False
    assert projected["gate"]["measurement_status"] == "NOT_MEASURED"
    assert projected["gate"]["measured_precision_recall_claim_allowed"] is False
