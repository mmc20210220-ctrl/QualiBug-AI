"""Externally labeled, closed-world benchmark for enterprise identity resolution.

The evaluator aligns exact source-occurrence mention references. Raw labels are
only diagnostic: duplicate names in different systems must remain distinguishable.
Product clusters are never accepted as Ground Truth.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any, Iterable

from .schema import as_dict, as_list, stable_id, text, unique_text

GROUND_TRUTH_SCHEMA = "qualibug.enterprise-identity-ground-truth.v1"
BENCHMARK_SCHEMA = "qualibug.enterprise-identity-benchmark-result.v2"
QUALITY_POLICY_SCHEMA = "qualibug.enterprise-identity-quality-policy.v1"
QUALITY_GATE_SCHEMA = "qualibug.enterprise-identity-quality-gate.v1"
ANNOTATION_SCOPE = "CLOSED_WORLD_IDENTITY_MENTIONS"
_BUSINESS_MENTION_TYPE = "BUSINESS_OBJECT"


def _not_measured(reason_code: str, **details: Any) -> dict[str, Any]:
    return {
        "schema": BENCHMARK_SCHEMA,
        "status": "NOT_MEASURED",
        "reason_code": reason_code,
        "quality_claim_allowed": False,
        "annotation_scope": ANNOTATION_SCOPE,
        "ground_truth_generated_from_product_output": False,
        "fuzzy_or_llm_alignment_used": False,
        "details": {
            key: value
            for key, value in details.items()
            if value not in (None, "", [], {}, set())
        },
    }


def _pairs(clusters: Iterable[Iterable[str]]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for cluster in clusters:
        values = sorted({text(value) for value in cluster if text(value)})
        result.update((left, right) for left, right in combinations(values, 2))
    return result


def _mention_index(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        text(row.get("mention_id")): row
        for row in as_list(result.get("mentions"))
        if isinstance(row, dict)
        and text(row.get("mention_id"))
        and text(row.get("mention_type") or _BUSINESS_MENTION_TYPE)
        == _BUSINESS_MENTION_TYPE
    }


def _predicted_clusters(
    result: dict[str, Any],
    mention_index: dict[str, dict[str, Any]],
) -> tuple[list[set[str]], dict[str, str]]:
    clusters: list[set[str]] = []
    mention_to_entity: dict[str, str] = {}
    for raw in as_list(result.get("clusters")):
        if not isinstance(raw, dict):
            continue
        entity_id = text(raw.get("entity_id"))
        members = {
            text(value)
            for value in as_list(raw.get("member_mention_ids"))
            if text(value) in mention_index
        }
        if not members:
            continue
        clusters.append(members)
        for mention_ref in members:
            mention_to_entity[mention_ref] = entity_id
    return clusters, mention_to_entity


def _truth_clusters(
    ground_truth: dict[str, Any],
) -> tuple[list[set[str]], dict[str, str], list[dict[str, Any]]]:
    clusters: list[set[str]] = []
    mention_to_cluster: dict[str, str] = {}
    conflicts: list[dict[str, Any]] = []
    for position, raw in enumerate(as_list(ground_truth.get("clusters"))):
        if not isinstance(raw, dict):
            conflicts.append(
                {
                    "kind": "INVALID_IDENTITY_GROUND_TRUTH_CLUSTER",
                    "position": position,
                    "reason_code": "GROUND_TRUTH_CLUSTER_NOT_OBJECT",
                }
            )
            continue
        if text(raw.get("annotation_status") or "CONFIRMED").upper() != "CONFIRMED":
            continue
        cluster_ref = text(
            raw.get("cluster_ref")
            or raw.get("ground_truth_cluster_id")
            or f"ground_truth_cluster:{position}"
        )
        members = unique_text(
            [
                *as_list(raw.get("member_refs")),
                *as_list(raw.get("mention_refs")),
                *as_list(raw.get("member_mention_ids")),
            ]
        )
        if not members:
            conflicts.append(
                {
                    "kind": "INVALID_IDENTITY_GROUND_TRUTH_CLUSTER",
                    "position": position,
                    "cluster_ref": cluster_ref,
                    "reason_code": "GROUND_TRUTH_CLUSTER_MEMBERS_MISSING",
                }
            )
            continue
        cluster_members: set[str] = set()
        for mention_ref in members:
            prior = mention_to_cluster.get(mention_ref)
            if prior and prior != cluster_ref:
                conflicts.append(
                    {
                        "kind": "DUPLICATE_IDENTITY_GROUND_TRUTH_MENTION",
                        "mention_ref": mention_ref,
                        "cluster_refs": sorted({prior, cluster_ref}),
                    }
                )
                continue
            mention_to_cluster[mention_ref] = cluster_ref
            cluster_members.add(mention_ref)
        if cluster_members:
            clusters.append(cluster_members)
    return clusters, mention_to_cluster, conflicts


def _mention_details(
    mention_ref: str,
    mention_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    mention = as_dict(mention_index.get(mention_ref))
    return {
        "mention_ref": mention_ref,
        "label": mention.get("raw_label"),
        "source_id": mention.get("source_id"),
        "source_locator": mention.get("source_locator"),
        "role": mention.get("role"),
        "scope": as_dict(mention.get("scope")),
    }


def _pair_error_rows(
    pairs: set[tuple[str, str]],
    *,
    mention_index: dict[str, dict[str, Any]],
    predicted_entity: dict[str, str],
    expected_cluster: dict[str, str],
    surfaced_pairs: set[tuple[str, str]],
    error_type: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for left, right in sorted(pairs):
        pair = tuple(sorted((left, right)))
        rows.append(
            {
                "error_type": error_type,
                "left": _mention_details(left, mention_index),
                "right": _mention_details(right, mention_index),
                "predicted_entity_refs": unique_text(
                    [predicted_entity.get(left), predicted_entity.get(right)]
                ),
                "expected_cluster_refs": unique_text(
                    [expected_cluster.get(left), expected_cluster.get(right)]
                ),
                "uncertainty_surfaced": pair in surfaced_pairs,
            }
        )
    return rows


def _surfaced_pairs(result: dict[str, Any]) -> set[tuple[str, str]]:
    surfaced: set[tuple[str, str]] = set()
    for edge in as_list(result.get("edges")):
        if not isinstance(edge, dict):
            continue
        if text(edge.get("relation")) != "SAME_AS":
            continue
        if text(edge.get("status")) not in {"CANDIDATE_ONLY", "CONFLICTED"}:
            continue
        left = text(edge.get("left_mention_id"))
        right = text(edge.get("right_mention_id"))
        if left and right and left != right:
            surfaced.add(tuple(sorted((left, right))))
    for conflict in as_list(result.get("conflicts")):
        if not isinstance(conflict, dict):
            continue
        refs = unique_text(
            [
                *as_list(conflict.get("mention_refs")),
                conflict.get("left_mention_id"),
                conflict.get("right_mention_id"),
            ]
        )
        surfaced.update(_pairs([refs]))
    return surfaced


def _cluster_exact_match_rate(
    predicted_clusters: list[set[str]],
    expected_clusters: list[set[str]],
) -> float:
    predicted = {frozenset(row) for row in predicted_clusters}
    expected = {frozenset(row) for row in expected_clusters}
    denominator = max(len(predicted), len(expected))
    return len(predicted & expected) / denominator if denominator else 1.0


def _quality_policy(
    benchmark: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    if not policy:
        return {
            "schema": QUALITY_GATE_SCHEMA,
            "status": "NOT_CONFIGURED",
            "entry_allowed": True,
            "enforced": False,
            "blocking_reasons": [],
        }
    enforce = bool(policy.get("enforce"))
    if text(policy.get("schema")) != QUALITY_POLICY_SCHEMA:
        return {
            "schema": QUALITY_GATE_SCHEMA,
            "status": "INVALID_IDENTITY_QUALITY_POLICY",
            "entry_allowed": not enforce,
            "enforced": enforce,
            "blocking_reasons": ["IDENTITY_QUALITY_POLICY_SCHEMA_INVALID"],
        }
    if text(benchmark.get("status")) != "MEASURED":
        return {
            "schema": QUALITY_GATE_SCHEMA,
            "status": (
                "BLOCKED_IDENTITY_QUALITY_NOT_MEASURED"
                if enforce
                else "NOT_MEASURED"
            ),
            "entry_allowed": not enforce,
            "enforced": enforce,
            "blocking_reasons": [
                text(benchmark.get("reason_code"))
                or "IDENTITY_QUALITY_NOT_MEASURED"
            ],
        }

    thresholds = as_dict(policy.get("thresholds"))
    definitions = (
        ("minimum_pairwise_precision", "pairwise_precision", ">="),
        ("minimum_pairwise_recall", "pairwise_recall", ">="),
        ("minimum_pairwise_f1", "pairwise_f1", ">="),
        ("minimum_exact_cluster_match_rate", "exact_cluster_match_rate", ">="),
        ("maximum_overmerge_rate", "overmerge_rate", "<="),
        ("maximum_undermerge_rate", "undermerge_rate", "<="),
        (
            "minimum_identity_error_unknown_coverage_rate",
            "identity_error_unknown_coverage_rate",
            ">=",
        ),
        ("maximum_silent_identity_error_count", "silent_identity_error_count", "<="),
    )
    metrics = as_dict(benchmark.get("metrics"))
    checks: list[dict[str, Any]] = []
    invalid: list[str] = []
    for threshold_key, metric_key, operator in definitions:
        if threshold_key not in thresholds:
            continue
        try:
            threshold = float(thresholds.get(threshold_key))
            actual = float(metrics.get(metric_key))
        except (TypeError, ValueError):
            invalid.append(threshold_key)
            continue
        if threshold_key != "maximum_silent_identity_error_count" and not (
            0.0 <= threshold <= 1.0
        ):
            invalid.append(threshold_key)
            continue
        if threshold_key == "maximum_silent_identity_error_count" and threshold < 0:
            invalid.append(threshold_key)
            continue
        passed = actual >= threshold if operator == ">=" else actual <= threshold
        checks.append(
            {
                "threshold": threshold_key,
                "metric": metric_key,
                "operator": operator,
                "expected": threshold,
                "actual": actual,
                "passed": passed,
            }
        )

    if invalid or not checks:
        return {
            "schema": QUALITY_GATE_SCHEMA,
            "status": "INVALID_IDENTITY_QUALITY_POLICY",
            "entry_allowed": not enforce,
            "enforced": enforce,
            "blocking_reasons": (
                ["IDENTITY_QUALITY_THRESHOLDS_MISSING"]
                if not checks and not invalid
                else ["IDENTITY_QUALITY_THRESHOLD_INVALID"]
            ),
            "invalid_thresholds": sorted(invalid),
            "checks": checks,
        }

    failed = [row for row in checks if not row["passed"]]
    return {
        "schema": QUALITY_GATE_SCHEMA,
        "status": "PASS" if not failed else "BLOCKED_IDENTITY_QUALITY_THRESHOLD",
        "entry_allowed": not enforce or not failed,
        "enforced": enforce,
        "blocking_reasons": [
            f"{row['metric']} {row['operator']} {row['expected']}"
            for row in failed
        ],
        "checks": checks,
        "failed_check_count": len(failed),
        "passed_check_count": len(checks) - len(failed),
    }


def evaluate_identity_resolution(
    result: dict[str, Any],
    ground_truth: dict[str, Any],
    *,
    quality_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = as_dict(quality_policy)
    if text(ground_truth.get("schema")) != GROUND_TRUTH_SCHEMA:
        benchmark = _not_measured("IDENTITY_GROUND_TRUTH_SCHEMA_MISSING")
        benchmark["quality_gate"] = _quality_policy(benchmark, policy)
        return benchmark
    if text(ground_truth.get("annotation_scope")) != ANNOTATION_SCOPE:
        benchmark = _not_measured(
            "IDENTITY_GROUND_TRUTH_NOT_CLOSED_WORLD",
            required_annotation_scope=ANNOTATION_SCOPE,
        )
        benchmark["quality_gate"] = _quality_policy(benchmark, policy)
        return benchmark
    if bool(ground_truth.get("ground_truth_generated_from_product_output")):
        benchmark = _not_measured(
            "PRODUCT_OUTPUT_CANNOT_BE_IDENTITY_GROUND_TRUTH"
        )
        benchmark["quality_gate"] = _quality_policy(benchmark, policy)
        return benchmark

    mention_index = _mention_index(result)
    predicted_clusters, predicted_entity = _predicted_clusters(
        result, mention_index
    )
    expected_clusters, expected_cluster, annotation_conflicts = _truth_clusters(
        ground_truth
    )
    if annotation_conflicts:
        benchmark = _not_measured(
            "IDENTITY_GROUND_TRUTH_CONFLICTED",
            annotation_conflicts=annotation_conflicts,
        )
        benchmark["quality_gate"] = _quality_policy(benchmark, policy)
        return benchmark
    if not mention_index:
        benchmark = _not_measured("IDENTITY_PREDICTED_MENTION_UNIVERSE_EMPTY")
        benchmark["quality_gate"] = _quality_policy(benchmark, policy)
        return benchmark
    if not expected_clusters:
        benchmark = _not_measured("IDENTITY_GROUND_TRUTH_EMPTY")
        benchmark["quality_gate"] = _quality_policy(benchmark, policy)
        return benchmark

    predicted_universe = set(mention_index)
    expected_universe = set(expected_cluster)
    unannotated_product_mentions = sorted(predicted_universe - expected_universe)
    unknown_ground_truth_mentions = sorted(expected_universe - predicted_universe)
    if unannotated_product_mentions or unknown_ground_truth_mentions:
        benchmark = _not_measured(
            "IDENTITY_GROUND_TRUTH_MENTION_UNIVERSE_INCOMPLETE",
            unannotated_product_mentions=unannotated_product_mentions,
            unknown_ground_truth_mentions=unknown_ground_truth_mentions,
        )
        benchmark["quality_gate"] = _quality_policy(benchmark, policy)
        return benchmark

    predicted = _pairs(predicted_clusters)
    expected = _pairs(expected_clusters)
    true_positive = predicted & expected
    false_positive = predicted - expected
    false_negative = expected - predicted

    precision = (
        len(true_positive) / len(predicted)
        if predicted
        else (1.0 if not expected else 0.0)
    )
    recall = len(true_positive) / len(expected) if expected else 1.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    overmerge = len(false_positive) / len(predicted) if predicted else 0.0
    undermerge = len(false_negative) / len(expected) if expected else 0.0
    surfaced_pairs = _surfaced_pairs(result)
    error_pairs = false_positive | false_negative
    surfaced_error_pairs = error_pairs & surfaced_pairs
    unknown_coverage = (
        len(surfaced_error_pairs) / len(error_pairs) if error_pairs else 1.0
    )
    exact_cluster_match_rate = _cluster_exact_match_rate(
        predicted_clusters, expected_clusters
    )

    metrics = {
        "annotated_mention_count": len(expected_universe),
        "predicted_cluster_count": len(predicted_clusters),
        "expected_cluster_count": len(expected_clusters),
        "predicted_singleton_cluster_count": sum(
            1 for row in predicted_clusters if len(row) == 1
        ),
        "expected_singleton_cluster_count": sum(
            1 for row in expected_clusters if len(row) == 1
        ),
        "predicted_pair_count": len(predicted),
        "expected_pair_count": len(expected),
        "true_positive_pair_count": len(true_positive),
        "false_positive_pair_count": len(false_positive),
        "false_negative_pair_count": len(false_negative),
        "pairwise_precision": round(precision, 6),
        "pairwise_recall": round(recall, 6),
        "pairwise_f1": round(f1, 6),
        "exact_cluster_match_rate": round(exact_cluster_match_rate, 6),
        "overmerge_rate": round(overmerge, 6),
        "undermerge_rate": round(undermerge, 6),
        "identity_error_unknown_coverage_rate": round(unknown_coverage, 6),
        "identity_error_unknown_covered_count": len(surfaced_error_pairs),
        "silent_identity_error_count": len(error_pairs - surfaced_pairs),
    }
    benchmark = {
        "schema": BENCHMARK_SCHEMA,
        "benchmark_id": stable_id(
            "enterprise_identity_benchmark",
            ground_truth.get("benchmark_id"),
            sorted(predicted),
            sorted(expected),
        ),
        "status": "MEASURED",
        "annotation_scope": ANNOTATION_SCOPE,
        "measurement_contract": (
            "EXTERNALLY_LABELED_CLOSED_WORLD_EXACT_SOURCE_OCCURRENCE_IDENTITY"
        ),
        "quality_claim_allowed": True,
        "ground_truth_generated_from_product_output": False,
        "fuzzy_or_llm_alignment_used": False,
        "metrics": metrics,
        "false_positive_pairs": _pair_error_rows(
            false_positive,
            mention_index=mention_index,
            predicted_entity=predicted_entity,
            expected_cluster=expected_cluster,
            surfaced_pairs=surfaced_pairs,
            error_type="OVERMERGE_FALSE_POSITIVE_PAIR",
        ),
        "false_negative_pairs": _pair_error_rows(
            false_negative,
            mention_index=mention_index,
            predicted_entity=predicted_entity,
            expected_cluster=expected_cluster,
            surfaced_pairs=surfaced_pairs,
            error_type="UNDERMERGE_FALSE_NEGATIVE_PAIR",
        ),
    }
    benchmark["quality_gate"] = _quality_policy(benchmark, policy)
    return benchmark


def project_identity_benchmark(
    asset: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    ground_truth = as_dict(asset.get("enterprise_identity_ground_truth"))
    quality_policy = as_dict(asset.get("enterprise_identity_quality_policy"))
    benchmark = (
        evaluate_identity_resolution(
            result,
            ground_truth,
            quality_policy=quality_policy,
        )
        if ground_truth
        else _not_measured("EXTERNAL_IDENTITY_GROUND_TRUTH_NOT_PROVIDED")
    )
    if "quality_gate" not in benchmark:
        benchmark["quality_gate"] = _quality_policy(benchmark, quality_policy)

    result["benchmark"] = benchmark
    asset["enterprise_identity_benchmark"] = benchmark
    quality_gate = as_dict(benchmark.get("quality_gate"))
    gate = dict(as_dict(result.get("gate")))
    gate["measurement_status"] = benchmark.get("status")
    gate["measured_precision_recall_claim_allowed"] = bool(
        benchmark.get("quality_claim_allowed")
    )
    gate["quality_gate"] = quality_gate
    if text(benchmark.get("status")) == "MEASURED":
        gate["metrics"] = {
            **as_dict(gate.get("metrics")),
            **as_dict(benchmark.get("metrics")),
        }
    if bool(quality_gate.get("enforced")) and not bool(
        quality_gate.get("entry_allowed", True)
    ):
        gate.update(
            {
                "status": "BLOCKED_ENTERPRISE_IDENTITY_QUALITY_GATE",
                "entry_allowed": False,
                "business_understanding_allowed": False,
                "required_operator_action": (
                    "improve identity evidence or resolve benchmark errors before "
                    "passing the enforced identity quality policy"
                ),
            }
        )
    result["gate"] = gate
    asset["enterprise_identity_gate"] = gate
    asset["enterprise_identity_resolution"] = result

    summary = dict(as_dict(asset.get("summary")))
    summary.update(
        {
            "enterprise_identity_benchmark_status": benchmark.get("status"),
            "enterprise_identity_quality_claim_allowed": bool(
                benchmark.get("quality_claim_allowed")
            ),
            "enterprise_identity_quality_gate_status": quality_gate.get("status"),
            "enterprise_identity_quality_gate_enforced": bool(
                quality_gate.get("enforced")
            ),
        }
    )
    asset["summary"] = summary
    return result


__all__ = [
    "ANNOTATION_SCOPE",
    "BENCHMARK_SCHEMA",
    "GROUND_TRUTH_SCHEMA",
    "QUALITY_GATE_SCHEMA",
    "QUALITY_POLICY_SCHEMA",
    "evaluate_identity_resolution",
    "project_identity_benchmark",
]
