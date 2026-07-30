"""Metrics for enterprise-understanding Ground Truth alignment."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

METRIC_SCHEMA = "qualibug.enterprise-understanding-benchmark-metrics.v1"
CRITICALITY_WEIGHTS = {"P0": 5.0, "P1": 3.0, "P2": 1.0, "P3": 0.5}
EXACT_STATUSES = {"EXACT_MATCH", "UNKNOWN_CORRECTLY_EXPOSED"}
COVERED_STATUSES = {*EXACT_STATUSES, "PARTIAL_MATCH"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _collection_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(_text(row.get("alignment_status")) for row in rows)
    total = len(rows)
    exact = sum(counts[status] for status in EXACT_STATUSES)
    covered = sum(counts[status] for status in COVERED_STATUSES)
    return {
        "ground_truth_count": total,
        "exact_match_count": exact,
        "partial_match_count": counts["PARTIAL_MATCH"],
        "missing_count": counts["MISSING"],
        "wrong_binding_count": counts["WRONG_BINDING"],
        "conflicted_count": counts["CONFLICTED"],
        "unknown_correctly_exposed_count": counts["UNKNOWN_CORRECTLY_EXPOSED"],
        "strict_recall": _ratio(exact, total),
        "coverage_recall": _ratio(covered, total),
    }


def _ground_truth_index(ground_truth: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in ground_truth.values():
        for row in _rows(value):
            identity = _text(row.get("ground_truth_id"))
            if identity:
                result[identity] = row
    return result


def _evidence_tokens(row: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for evidence in _rows(row.get("candidate_evidence")):
        for field in ("source_id", "source_locator", "asset_ref", "fact_id"):
            value = _text(evidence.get(field))
            if value:
                values.add(value)
    return values


def _expected_evidence_tokens(row: dict[str, Any]) -> set[str]:
    return {
        _text(value)
        for field in ("source_refs", "source_locators")
        for value in (row.get(field) if isinstance(row.get(field), list) else [])
        if _text(value)
    }


def _evidence_metrics(
    alignments: list[dict[str, Any]], ground_truth_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    measurable = 0
    supported = 0
    missing_evidence = 0
    wrong_evidence = 0
    for alignment in alignments:
        if alignment.get("alignment_status") not in COVERED_STATUSES:
            continue
        expected = ground_truth_by_id.get(_text(alignment.get("ground_truth_id")), {})
        expected_tokens = _expected_evidence_tokens(expected)
        if not expected_tokens:
            continue
        measurable += 1
        candidate_tokens = _evidence_tokens(alignment)
        if not candidate_tokens:
            missing_evidence += 1
        elif expected_tokens & candidate_tokens:
            supported += 1
        else:
            wrong_evidence += 1
    return {
        "measurable_alignment_count": measurable,
        "supported_evidence_count": supported,
        "missing_candidate_evidence_count": missing_evidence,
        "wrong_source_evidence_count": wrong_evidence,
        "source_evidence_accuracy": _ratio(supported, measurable),
    }


def _behavior_slot_metrics(
    alignments: list[dict[str, Any]], ground_truth_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    slots = (
        "actor_refs",
        "operation",
        "object_refs",
        "preconditions",
        "permission_decision",
        "state_effects",
        "data_effects",
        "exceptions",
        "compensations",
    )
    totals = Counter()
    correct = Counter()
    for alignment in alignments:
        if alignment.get("collection") not in {"business_rules", "business_behaviors"}:
            continue
        ground_truth = ground_truth_by_id.get(_text(alignment.get("ground_truth_id")), {})
        missing_slots = set(
            alignment.get("details", {}).get("missing_or_wrong_slots", [])
            if isinstance(alignment.get("details"), dict)
            else []
        )
        status = _text(alignment.get("alignment_status"))
        for slot in slots:
            value = ground_truth.get(slot)
            present = bool(value) if not isinstance(value, list) else bool(value)
            if slot in {"operation", "object_refs"}:
                present = True
            if not present:
                continue
            totals[slot] += 1
            if status == "EXACT_MATCH":
                correct[slot] += 1
            elif status == "PARTIAL_MATCH" and slot not in missing_slots:
                # Operation and object binding were already required before slot-level partial
                # comparison. Other slots are only credited when not named as missing/wrong.
                correct[slot] += 1
    return {
        slot: {
            "measurable_count": totals[slot],
            "correct_count": correct[slot],
            "accuracy": _ratio(correct[slot], totals[slot]),
        }
        for slot in slots
    }


def _weighted_recall(alignments: list[dict[str, Any]]) -> dict[str, Any]:
    denominator = 0.0
    exact_numerator = 0.0
    coverage_numerator = 0.0
    by_criticality: dict[str, dict[str, float]] = defaultdict(
        lambda: {"weight": 0.0, "exact": 0.0, "covered": 0.0}
    )
    for row in alignments:
        if row.get("collection") not in {"business_rules", "business_behaviors"}:
            continue
        criticality = _text(row.get("criticality") or "P2").upper()
        weight = CRITICALITY_WEIGHTS.get(criticality, 1.0)
        denominator += weight
        by_criticality[criticality]["weight"] += weight
        status = _text(row.get("alignment_status"))
        if status in EXACT_STATUSES:
            exact_numerator += weight
            coverage_numerator += weight
            by_criticality[criticality]["exact"] += weight
            by_criticality[criticality]["covered"] += weight
        elif status == "PARTIAL_MATCH":
            coverage_numerator += weight
            by_criticality[criticality]["covered"] += weight
    return {
        "weights": CRITICALITY_WEIGHTS,
        "strict_weighted_recall": _ratio(exact_numerator, denominator),
        "coverage_weighted_recall": _ratio(coverage_numerator, denominator),
        "by_criticality": {
            key: {
                "strict_recall": _ratio(value["exact"], value["weight"]),
                "coverage_recall": _ratio(value["covered"], value["weight"]),
            }
            for key, value in sorted(by_criticality.items())
        },
    }


def _bug_dependency_metrics(
    ground_truth: dict[str, Any], alignments: list[dict[str, Any]]
) -> dict[str, Any]:
    status_by_id = {
        _text(row.get("ground_truth_id")): _text(row.get("alignment_status"))
        for row in alignments
    }
    rows: list[dict[str, Any]] = []
    fully_covered = 0
    partially_covered = 0
    for dependency in _rows(ground_truth.get("bug_dependencies")):
        required_ids = [
            _text(value)
            for value in dependency.get("required_ground_truth_ids", [])
            if _text(value)
        ]
        exact_ids = [identity for identity in required_ids if status_by_id.get(identity) in EXACT_STATUSES]
        covered_ids = [identity for identity in required_ids if status_by_id.get(identity) in COVERED_STATUSES]
        missing_ids = [identity for identity in required_ids if status_by_id.get(identity) not in COVERED_STATUSES]
        complete = bool(required_ids) and len(exact_ids) == len(required_ids)
        partial = bool(covered_ids) and not complete
        fully_covered += int(complete)
        partially_covered += int(partial)
        rows.append(
            {
                "bug_id": dependency.get("bug_id"),
                "required_ground_truth_ids": required_ids,
                "exact_dependency_ids": exact_ids,
                "covered_dependency_ids": covered_ids,
                "missing_dependency_ids": missing_ids,
                "all_dependencies_exactly_understood": complete,
                "some_dependencies_understood": partial,
                "dependency_coverage": _ratio(len(covered_ids), len(required_ids)),
            }
        )
    return {
        "bug_count": len(rows),
        "fully_covered_bug_count": fully_covered,
        "partially_covered_bug_count": partially_covered,
        "bug_dependency_rule_coverage_rate": _ratio(fully_covered, len(rows)),
        "bugs": rows,
    }


def _false_confirmation_metrics(
    ground_truth: dict[str, Any], alignment: dict[str, Any]
) -> dict[str, Any]:
    validation = ground_truth.get("validation_receipt")
    validation_status = (
        _text(validation.get("status")) if isinstance(validation, dict) else "UNKNOWN"
    )
    complete_scope = bool(ground_truth.get("scope_complete"))
    unmatched = _rows(alignment.get("unmatched_confirmed_candidates"))
    matched_confirmed = sum(
        1
        for row in _rows(alignment.get("alignments"))
        if _text(row.get("alignment_status")) in EXACT_STATUSES
        and _text(row.get("candidate_status")).upper() in {"CONFIRMED", "ACCEPTED", "PASS"}
    )
    denominator = matched_confirmed + len(unmatched)
    measurable = validation_status == "PASS" and complete_scope
    return {
        "status": "MEASURABLE" if measurable else "NOT_MEASURABLE_INCOMPLETE_GROUND_TRUTH_SCOPE",
        "scope_complete": complete_scope,
        "ground_truth_validation_status": validation_status,
        "unmatched_confirmed_candidate_count": len(unmatched),
        "matched_confirmed_candidate_count": matched_confirmed,
        "false_confirmation_rate": _ratio(len(unmatched), denominator) if measurable else None,
        "unmatched_confirmed_candidates": unmatched,
    }


def calculate_benchmark_metrics(
    ground_truth: dict[str, Any], alignment: dict[str, Any]
) -> dict[str, Any]:
    alignments = _rows(alignment.get("alignments"))
    by_collection: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in alignments:
        by_collection[_text(row.get("collection"))].append(row)
    collection_metrics = {
        collection: _collection_metrics(rows)
        for collection, rows in sorted(by_collection.items())
    }
    ground_truth_by_id = _ground_truth_index(ground_truth)
    return {
        "schema": METRIC_SCHEMA,
        "project_id": ground_truth.get("project_id"),
        "ground_truth_validation_status": ground_truth.get("validation_receipt", {}).get("status"),
        "collection_metrics": collection_metrics,
        "business_object_recall": collection_metrics.get("business_objects", {}).get("strict_recall"),
        "actor_recall": collection_metrics.get("actors", {}).get("strict_recall"),
        "operation_recall": collection_metrics.get("operations", {}).get("strict_recall"),
        "operation_object_binding_accuracy": _ratio(
            collection_metrics.get("operations", {}).get("exact_match_count", 0),
            collection_metrics.get("operations", {}).get("ground_truth_count", 0),
        ),
        "business_rule_recall": collection_metrics.get("business_rules", {}).get("strict_recall"),
        "business_behavior_recall": collection_metrics.get("business_behaviors", {}).get("strict_recall"),
        "state_transition_recall": collection_metrics.get("state_transitions", {}).get("strict_recall"),
        "conflict_exposure_rate": collection_metrics.get("conflicts", {}).get("strict_recall"),
        "expected_unknown_exposure_rate": collection_metrics.get("expected_unknowns", {}).get("strict_recall"),
        "behavior_slot_accuracy": _behavior_slot_metrics(alignments, ground_truth_by_id),
        "source_evidence_metrics": _evidence_metrics(alignments, ground_truth_by_id),
        "critical_rule_weighted_recall": _weighted_recall(alignments),
        "false_confirmation_metrics": _false_confirmation_metrics(ground_truth, alignment),
        "bug_dependency_metrics": _bug_dependency_metrics(ground_truth, alignments),
        "model_writeback_allowed": False,
        "quality_claim": "GROUND_TRUTH_MEASUREMENT_NOT_MODEL_COMPLETENESS_PROJECTION",
    }


__all__ = ["METRIC_SCHEMA", "CRITICALITY_WEIGHTS", "calculate_benchmark_metrics"]
