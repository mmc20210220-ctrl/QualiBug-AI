"""Closed-world evaluator for name-independent object review candidates."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

GROUND_TRUTH_SCHEMA = "qualibug.enterprise-object-distinctness-review-ground-truth.v1"
MEASUREMENT_SCHEMA = "qualibug.enterprise-object-distinctness-review-measurement.v1"


def _labels(row: dict[str, Any]) -> tuple[str, ...]:
    canonical = row.get("canonical_labels") or {}
    values = canonical.values() if isinstance(canonical, dict) else []
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


def _expected_pairs(rows: Iterable[Any]) -> dict[tuple[str, ...], dict[str, Any]]:
    result: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        labels = tuple(sorted({str(value).strip() for value in row.get("labels") or [] if str(value).strip()}))
        if len(labels) == 2:
            result[labels] = dict(row)
    return result


def load_object_distinctness_ground_truth(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != GROUND_TRUTH_SCHEMA:
        raise ValueError("object_distinctness_ground_truth_schema_invalid")
    if payload.get("ground_truth_generated_from_product_output") is not False:
        raise ValueError("object_distinctness_ground_truth_must_be_external")
    receipt = payload.get("validation_receipt") or {}
    if receipt.get("closed_world") is not True or receipt.get("generated_from_product_output") is not False:
        raise ValueError("object_distinctness_ground_truth_not_closed_world_external")
    return payload


def evaluate_object_distinctness_review(
    ground_truth: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, Any]:
    model = snapshot.get("enterprise_understanding_model") or {}
    candidates = [dict(row) for row in model.get("identity_structural_candidates") or [] if isinstance(row, dict)]
    receipt = model.get("identity_structural_evidence") or {}
    suppressed = [dict(row) for row in receipt.get("suppressed_pairs") or [] if isinstance(row, dict)]

    expected = _expected_pairs(ground_truth.get("expected_review_pairs") or [])
    predicted = {_labels(row): row for row in candidates if len(_labels(row)) == 2}
    expected_keys, predicted_keys = set(expected), set(predicted)
    true_positive = len(expected_keys & predicted_keys)
    false_positive = len(predicted_keys - expected_keys)
    false_negative = len(expected_keys - predicted_keys)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    dimension_mismatches: list[dict[str, Any]] = []
    for pair in sorted(expected_keys & predicted_keys):
        required = set(expected[pair].get("required_dimensions") or [])
        actual = set(predicted[pair].get("matched_dimensions") or [])
        if not required.issubset(actual):
            dimension_mismatches.append({"labels": list(pair), "required": sorted(required), "actual": sorted(actual)})

    expected_suppressed = _expected_pairs(ground_truth.get("expected_suppressed_pairs") or [])
    actual_suppressed = {_labels(row): row for row in suppressed if len(_labels(row)) == 2}
    suppressed_missing = sorted(set(expected_suppressed) - set(actual_suppressed))
    suppressed_unexpected = sorted(set(actual_suppressed) - set(expected_suppressed))
    suppression_reason_mismatches: list[dict[str, Any]] = []
    for pair in sorted(set(expected_suppressed) & set(actual_suppressed)):
        expected_reason = str(expected_suppressed[pair].get("reason_code") or "")
        actual_reason = str(actual_suppressed[pair].get("reason_code") or "")
        if expected_reason != actual_reason:
            suppression_reason_mismatches.append({"labels": list(pair), "expected": expected_reason, "actual": actual_reason})

    objects = [row for row in model.get("business_objects") or [] if isinstance(row, dict)]
    expected_objects = set(ground_truth.get("expected_distinct_formal_objects") or [])
    entity_by_label = {
        str(row.get("canonical_label") or row.get("name") or ""): str(row.get("entity_id") or row.get("object_id") or "")
        for row in objects
    }
    distinct_ids = {entity_by_label.get(label, "") for label in expected_objects}
    distinct_formal_objects_preserved = "" not in distinct_ids and len(distinct_ids) == len(expected_objects)
    unsafe_candidates = [
        row for row in candidates
        if row.get("automatic_entity_union_allowed") is not False
        or row.get("automatic_resolution_allowed") is not False
        or row.get("requires_operator_review") is not True
    ]

    measured = not any([
        false_positive,
        false_negative,
        dimension_mismatches,
        suppressed_missing,
        suppressed_unexpected,
        suppression_reason_mismatches,
        unsafe_candidates,
        not distinct_formal_objects_preserved,
    ])
    return {
        "schema": MEASUREMENT_SCHEMA,
        "status": "MEASURED" if measured else "MEASURED_WITH_ERRORS",
        "reason_code": "" if measured else "OBJECT_DISTINCTNESS_REVIEW_MISMATCH",
        "metrics": {
            "expected_review_pair_count": len(expected_keys),
            "predicted_review_pair_count": len(predicted_keys),
            "true_positive_review_pairs": true_positive,
            "false_positive_review_pairs": false_positive,
            "false_negative_review_pairs": false_negative,
            "review_pair_precision": precision,
            "review_pair_recall": recall,
            "review_pair_f1": f1,
            "expected_suppressed_pair_count": len(expected_suppressed),
            "observed_suppressed_pair_count": len(actual_suppressed),
            "lifecycle_contradiction_veto_coverage": (
                (len(expected_suppressed) - len(suppressed_missing)) / len(expected_suppressed)
                if expected_suppressed else 1.0
            ),
            "distinct_formal_objects_preserved": distinct_formal_objects_preserved,
            "automatic_entity_union_count": len(unsafe_candidates),
        },
        "false_positive_pairs": [list(pair) for pair in sorted(predicted_keys - expected_keys)],
        "false_negative_pairs": [list(pair) for pair in sorted(expected_keys - predicted_keys)],
        "dimension_mismatches": dimension_mismatches,
        "suppressed_missing": [list(pair) for pair in suppressed_missing],
        "suppressed_unexpected": [list(pair) for pair in suppressed_unexpected],
        "suppression_reason_mismatches": suppression_reason_mismatches,
        "unsafe_candidates": unsafe_candidates,
        "ground_truth_generated_from_product_output": False,
    }


__all__ = [
    "GROUND_TRUTH_SCHEMA",
    "MEASUREMENT_SCHEMA",
    "evaluate_object_distinctness_review",
    "load_object_distinctness_ground_truth",
]
