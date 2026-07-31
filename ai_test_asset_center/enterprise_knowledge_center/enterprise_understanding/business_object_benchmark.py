"""Externally labeled metrics for business-object type recognition.

Identity clustering answers whether two recognized objects are the same entity. This
module measures the earlier question: whether each source label should be promoted to
a business object at all. Product output is never accepted as Ground Truth.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from ._object_candidate_governance import ACCEPTED_OBJECT_STATUSES
from ._object_role_evidence import comparison_key
from .schema import as_dict, as_list, stable_id, text, unique_text

GROUND_TRUTH_SCHEMA = "qualibug.enterprise-business-object-ground-truth.v1"
BENCHMARK_SCHEMA = "qualibug.enterprise-business-object-benchmark-result.v1"
ANNOTATION_SCOPE = "CLOSED_WORLD_SOURCE_LABELS"
OBJECT_TYPE = "BUSINESS_OBJECT"
SUPPORTED_TYPES = frozenset(
    {
        OBJECT_TYPE,
        "ACTOR",
        "ACTION",
        "STATE",
        "FIELD",
        "TECHNICAL_ARTIFACT",
        "OTHER_NON_OBJECT",
    }
)


def _not_measured(reason_code: str, **details: Any) -> dict[str, Any]:
    return {
        "schema": BENCHMARK_SCHEMA,
        "status": "NOT_MEASURED",
        "reason_code": reason_code,
        "quality_claim_allowed": False,
        "ground_truth_generated_from_product_output": False,
        "fuzzy_or_llm_alignment_used": False,
        "details": {key: value for key, value in details.items() if value not in (None, "", [], {})},
    }


def _ground_truth_index(
    ground_truth: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    index: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for position, raw in enumerate(as_list(ground_truth.get("labels"))):
        if not isinstance(raw, dict):
            continue
        if text(raw.get("annotation_status") or "CONFIRMED").upper() != "CONFIRMED":
            continue
        expected_type = text(raw.get("expected_type")).upper()
        labels = unique_text(
            [
                raw.get("label"),
                raw.get("canonical_label"),
                *as_list(raw.get("labels")),
                *as_list(raw.get("aliases")),
            ]
        )
        if expected_type not in SUPPORTED_TYPES or not labels:
            conflicts.append(
                {
                    "kind": "INVALID_BUSINESS_OBJECT_GROUND_TRUTH_ROW",
                    "position": position,
                    "expected_type": expected_type,
                    "labels": labels,
                }
            )
            continue
        for label in labels:
            key = comparison_key(label)
            if not key:
                continue
            prior = index.get(key)
            if prior and text(prior.get("expected_type")) != expected_type:
                conflicts.append(
                    {
                        "kind": "CONTRADICTORY_BUSINESS_OBJECT_TYPE_ANNOTATION",
                        "comparison_key": key,
                        "labels": unique_text([prior.get("label"), label]),
                        "expected_types": unique_text(
                            [prior.get("expected_type"), expected_type]
                        ),
                    }
                )
                continue
            index[key] = {
                "comparison_key": key,
                "label": label,
                "expected_type": expected_type,
                "criticality": text(raw.get("criticality") or "P2").upper(),
                "source_locator": text(raw.get("source_locator")),
                "annotation_ref": text(
                    raw.get("annotation_ref") or raw.get("ground_truth_id")
                ),
            }
    return index, conflicts


def _candidate_index(recognition: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in as_list(recognition.get("candidates")):
        if not isinstance(raw, dict):
            continue
        key = text(raw.get("comparison_key"))
        if not key:
            labels = as_list(raw.get("labels"))
            key = comparison_key(labels[0] if labels else "")
        if key:
            result[key] = raw
    return result


def _unknown_keys(recognition: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for unknown in as_list(recognition.get("unknowns")):
        if not isinstance(unknown, dict):
            continue
        for label in as_list(unknown.get("related_object_refs")):
            key = comparison_key(label)
            if key:
                keys.add(key)
        details = as_dict(unknown.get("details"))
        candidate_key = text(details.get("comparison_key"))
        if candidate_key:
            keys.add(candidate_key)
    return keys


def _error_row(
    key: str,
    truth: dict[str, Any],
    candidate: dict[str, Any] | None,
    *,
    error_type: str,
    surfaced: bool,
) -> dict[str, Any]:
    candidate = candidate or {}
    return {
        "comparison_key": key,
        "label": truth.get("label"),
        "expected_type": truth.get("expected_type"),
        "criticality": truth.get("criticality"),
        "error_type": error_type,
        "candidate_id": candidate.get("candidate_id"),
        "predicted_status": candidate.get("status") or "NOT_COLLECTED",
        "reason_code": candidate.get("reason_code") or "OBJECT_CANDIDATE_NOT_COLLECTED",
        "uncertainty_surfaced": surfaced,
    }


def evaluate_business_object_recognition(
    recognition: dict[str, Any],
    ground_truth: dict[str, Any],
) -> dict[str, Any]:
    if text(ground_truth.get("schema")) != GROUND_TRUTH_SCHEMA:
        return _not_measured("BUSINESS_OBJECT_GROUND_TRUTH_SCHEMA_MISSING")
    if text(ground_truth.get("annotation_scope")) != ANNOTATION_SCOPE:
        return _not_measured(
            "BUSINESS_OBJECT_GROUND_TRUTH_NOT_CLOSED_WORLD",
            required_annotation_scope=ANNOTATION_SCOPE,
        )
    if bool(ground_truth.get("ground_truth_generated_from_product_output")):
        return _not_measured("PRODUCT_OUTPUT_CANNOT_BE_BUSINESS_OBJECT_GROUND_TRUTH")

    truth_by_key, annotation_conflicts = _ground_truth_index(ground_truth)
    if annotation_conflicts:
        return _not_measured(
            "BUSINESS_OBJECT_GROUND_TRUTH_CONFLICTED",
            annotation_conflicts=annotation_conflicts,
        )
    if not truth_by_key:
        return _not_measured("BUSINESS_OBJECT_GROUND_TRUTH_EMPTY")

    candidates = _candidate_index(recognition)
    accepted = {
        text(key)
        for key in as_list(recognition.get("accepted_comparison_keys"))
        if text(key)
    }
    if not accepted:
        accepted = {
            key
            for key, row in candidates.items()
            if text(row.get("status")) in ACCEPTED_OBJECT_STATUSES
        }

    unannotated_candidates = sorted(set(candidates) - set(truth_by_key))
    unannotated_accepted = sorted(accepted - set(truth_by_key))
    if unannotated_candidates or unannotated_accepted:
        return _not_measured(
            "BUSINESS_OBJECT_GROUND_TRUTH_CANDIDATE_UNIVERSE_INCOMPLETE",
            unannotated_candidate_keys=unannotated_candidates,
            unannotated_accepted_keys=unannotated_accepted,
        )

    expected_objects = {
        key for key, row in truth_by_key.items() if text(row.get("expected_type")) == OBJECT_TYPE
    }
    expected_non_objects = set(truth_by_key) - expected_objects
    true_positive = accepted & expected_objects
    false_positive = accepted & expected_non_objects
    false_negative = expected_objects - accepted
    true_negative = expected_non_objects - accepted

    precision = (
        len(true_positive) / (len(true_positive) + len(false_positive))
        if true_positive or false_positive
        else (1.0 if not expected_objects else 0.0)
    )
    recall = len(true_positive) / len(expected_objects) if expected_objects else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    overpromotion = (
        len(false_positive) / len(expected_non_objects) if expected_non_objects else 0.0
    )
    miss_rate = len(false_negative) / len(expected_objects) if expected_objects else 0.0

    unknown_keys = _unknown_keys(recognition)
    surfaced_keys = set(unknown_keys)
    for key, row in candidates.items():
        status = text(row.get("status"))
        if (
            status.startswith("PENDING_")
            or status == "CONFLICTED"
            or status == "ACCEPTED_WITH_ROLE_COLLISION_OVERRIDE"
        ):
            surfaced_keys.add(key)

    error_keys = false_positive | false_negative
    covered_errors = error_keys & surfaced_keys
    unknown_coverage = len(covered_errors) / len(error_keys) if error_keys else 1.0

    false_positive_rows = [
        _error_row(
            key,
            truth_by_key[key],
            candidates.get(key),
            error_type="FALSE_POSITIVE_OBJECT_PROMOTION",
            surfaced=key in surfaced_keys,
        )
        for key in sorted(false_positive)
    ]
    false_negative_rows = [
        _error_row(
            key,
            truth_by_key[key],
            candidates.get(key),
            error_type="FALSE_NEGATIVE_OBJECT_MISS",
            surfaced=key in surfaced_keys,
        )
        for key in sorted(false_negative)
    ]
    confusion_distribution = Counter(
        text(truth_by_key[key].get("expected_type")) for key in false_positive
    )
    missing_candidates = sorted(set(truth_by_key) - set(candidates))

    metrics = {
        "annotated_label_count": len(truth_by_key),
        "expected_object_label_count": len(expected_objects),
        "expected_non_object_label_count": len(expected_non_objects),
        "collected_candidate_label_count": len(candidates),
        "predicted_object_label_count": len(accepted),
        "true_positive_object_count": len(true_positive),
        "false_positive_object_count": len(false_positive),
        "false_negative_object_count": len(false_negative),
        "true_negative_non_object_count": len(true_negative),
        "object_type_precision": round(precision, 6),
        "object_type_recall": round(recall, 6),
        "object_type_f1": round(f1, 6),
        "object_overpromotion_rate": round(overpromotion, 6),
        "object_miss_rate": round(miss_rate, 6),
        "object_error_unknown_coverage_rate": round(unknown_coverage, 6),
        "object_error_unknown_covered_count": len(covered_errors),
        "silent_object_error_count": len(error_keys - surfaced_keys),
        "missing_ground_truth_candidate_count": len(missing_candidates),
        "type_confusion_distribution": dict(sorted(confusion_distribution.items())),
    }
    return {
        "schema": BENCHMARK_SCHEMA,
        "benchmark_id": stable_id(
            "enterprise_business_object_benchmark",
            ground_truth.get("benchmark_id"),
            sorted((key, row.get("expected_type")) for key, row in truth_by_key.items()),
            sorted(accepted),
        ),
        "status": "MEASURED",
        "measurement_contract": "EXTERNALLY_LABELED_CLOSED_WORLD_EXACT_OBJECT_TYPE",
        "quality_claim_allowed": True,
        "ground_truth_generated_from_product_output": False,
        "fuzzy_or_llm_alignment_used": False,
        "metrics": metrics,
        "false_positive_objects": false_positive_rows,
        "false_negative_objects": false_negative_rows,
        "missing_ground_truth_candidates": [
            {
                "comparison_key": key,
                "label": truth_by_key[key].get("label"),
                "expected_type": truth_by_key[key].get("expected_type"),
            }
            for key in missing_candidates
        ],
    }


def project_business_object_benchmark(
    asset: dict[str, Any],
    recognition: dict[str, Any],
) -> dict[str, Any]:
    ground_truth = as_dict(asset.get("enterprise_business_object_ground_truth"))
    benchmark = (
        evaluate_business_object_recognition(recognition, ground_truth)
        if ground_truth
        else _not_measured("EXTERNAL_BUSINESS_OBJECT_GROUND_TRUTH_NOT_PROVIDED")
    )
    recognition["benchmark"] = benchmark
    asset["enterprise_business_object_benchmark"] = benchmark

    gate = dict(as_dict(recognition.get("gate")))
    gate["measurement_status"] = benchmark.get("status")
    gate["measured_precision_recall_claim_allowed"] = bool(
        benchmark.get("quality_claim_allowed")
    )
    if text(benchmark.get("status")) == "MEASURED":
        gate["metrics"] = {
            **as_dict(gate.get("metrics")),
            **as_dict(benchmark.get("metrics")),
        }
    recognition["gate"] = gate
    asset["business_object_recognition"] = recognition

    summary = dict(as_dict(asset.get("summary")))
    summary["business_object_benchmark_status"] = benchmark.get("status")
    summary["business_object_quality_claim_allowed"] = bool(
        benchmark.get("quality_claim_allowed")
    )
    asset["summary"] = summary
    return recognition


__all__ = [
    "ANNOTATION_SCOPE",
    "BENCHMARK_SCHEMA",
    "GROUND_TRUTH_SCHEMA",
    "evaluate_business_object_recognition",
    "project_business_object_benchmark",
]
