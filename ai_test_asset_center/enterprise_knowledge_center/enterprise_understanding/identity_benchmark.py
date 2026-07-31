"""Externally labeled benchmark metrics for enterprise identity resolution."""
from __future__ import annotations

from itertools import combinations
from typing import Any, Iterable

from .schema import as_dict, as_list, stable_id, text, unique_text

GROUND_TRUTH_SCHEMA = "qualibug.enterprise-identity-ground-truth.v1"
BENCHMARK_SCHEMA = "qualibug.enterprise-identity-benchmark-result.v1"


def _pairs(clusters: Iterable[Iterable[str]]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for cluster in clusters:
        values = sorted({text(value) for value in cluster if text(value)})
        result.update((left, right) for left, right in combinations(values, 2))
    return result


def _predicted_label_clusters(result: dict[str, Any]) -> list[list[str]]:
    mentions = {
        text(row.get("mention_id")): row
        for row in as_list(result.get("mentions"))
        if isinstance(row, dict)
    }
    clusters: list[list[str]] = []
    for cluster in as_list(result.get("clusters")):
        if not isinstance(cluster, dict):
            continue
        labels = unique_text(
            as_dict(mentions.get(text(mention_id))).get("raw_label")
            for mention_id in as_list(cluster.get("member_mention_ids"))
        )
        if labels:
            clusters.append(labels)
    return clusters


def _truth_label_clusters(ground_truth: dict[str, Any]) -> list[list[str]]:
    return [
        unique_text(
            [
                *as_list(row.get("member_labels")),
                *as_list(row.get("labels")),
                *as_list(row.get("aliases")),
                row.get("canonical_label"),
            ]
        )
        for row in as_list(ground_truth.get("clusters"))
        if isinstance(row, dict)
    ]


def evaluate_identity_resolution(
    result: dict[str, Any], ground_truth: dict[str, Any]
) -> dict[str, Any]:
    if text(ground_truth.get("schema")) != GROUND_TRUTH_SCHEMA:
        return {
            "schema": BENCHMARK_SCHEMA,
            "status": "NOT_MEASURED",
            "reason_code": "IDENTITY_GROUND_TRUTH_SCHEMA_MISSING",
            "quality_claim_allowed": False,
        }
    predicted = _pairs(_predicted_label_clusters(result))
    expected = _pairs(_truth_label_clusters(ground_truth))
    true_positive = predicted & expected
    false_positive = predicted - expected
    false_negative = expected - predicted

    precision = len(true_positive) / len(predicted) if predicted else (1.0 if not expected else 0.0)
    recall = len(true_positive) / len(expected) if expected else 1.0
    overmerge = len(false_positive) / len(predicted) if predicted else 0.0
    undermerge = len(false_negative) / len(expected) if expected else 0.0
    return {
        "schema": BENCHMARK_SCHEMA,
        "benchmark_id": stable_id(
            "enterprise_identity_benchmark",
            ground_truth.get("benchmark_id"),
            sorted(predicted),
            sorted(expected),
        ),
        "status": "MEASURED",
        "measurement_contract": "EXTERNALLY_LABELED_PAIRWISE_IDENTITY",
        "quality_claim_allowed": True,
        "metrics": {
            "predicted_pair_count": len(predicted),
            "expected_pair_count": len(expected),
            "true_positive_pair_count": len(true_positive),
            "false_positive_pair_count": len(false_positive),
            "false_negative_pair_count": len(false_negative),
            "pairwise_precision": round(precision, 6),
            "pairwise_recall": round(recall, 6),
            "overmerge_rate": round(overmerge, 6),
            "undermerge_rate": round(undermerge, 6),
        },
        "false_positive_pairs": [list(pair) for pair in sorted(false_positive)],
        "false_negative_pairs": [list(pair) for pair in sorted(false_negative)],
    }


def project_identity_benchmark(
    asset: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    ground_truth = as_dict(asset.get("enterprise_identity_ground_truth"))
    benchmark = (
        evaluate_identity_resolution(result, ground_truth)
        if ground_truth
        else {
            "schema": BENCHMARK_SCHEMA,
            "status": "NOT_MEASURED",
            "reason_code": "EXTERNAL_IDENTITY_GROUND_TRUTH_NOT_PROVIDED",
            "quality_claim_allowed": False,
        }
    )
    result["benchmark"] = benchmark
    asset["enterprise_identity_benchmark"] = benchmark
    gate = dict(as_dict(result.get("gate")))
    gate["measurement_status"] = benchmark.get("status")
    gate["measured_precision_recall_claim_allowed"] = bool(
        benchmark.get("quality_claim_allowed")
    )
    if text(benchmark.get("status")) == "MEASURED":
        gate["metrics"] = {
            **as_dict(gate.get("metrics")),
            **as_dict(benchmark.get("metrics")),
        }
    result["gate"] = gate
    asset["enterprise_identity_gate"] = gate
    asset["enterprise_identity_resolution"] = result
    return result


__all__ = [
    "GROUND_TRUTH_SCHEMA",
    "BENCHMARK_SCHEMA",
    "evaluate_identity_resolution",
    "project_identity_benchmark",
]
