"""Deterministic slot-level measurement for explicit business facts.

This evaluator consumes human/source-backed Ground Truth fields already allowed on
business_rules and business_behaviors. It never generates annotations from product
output, never writes back, and never uses fuzzy or LLM alignment.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

FACT_SLOT_MEASUREMENT_SCHEMA = "qualibug.enterprise-business-fact-slot-measurement.v1"

_SLOT_FIELDS = (
    "fact_type",
    "actor_refs",
    "object_refs",
    "modality",
    "condition_frame",
    "exception_scope",
    "state_effects",
    "data_effects",
    "postconditions",
    "compensation",
    "quantity_constraints",
    "time_window_constraints",
    "formula_constraints",
    "source_locators",
)
_ANNOTATION_TRIGGER_FIELDS = tuple(
    field for field in _SLOT_FIELDS if field != "object_refs"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if value in (None, "", {}):
        return []
    return [value]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _text(value).lower())


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _text(key): _canonical(item)
            for key, item in sorted(value.items())
            if item not in (None, "", [], {})
        }
    if isinstance(value, list):
        canonical = [_canonical(item) for item in value if item not in (None, "", [], {})]
        return sorted(
            canonical,
            key=lambda item: json.dumps(
                item, ensure_ascii=False, sort_keys=True, default=str
            ),
        )
    return _norm(value)


def _contains_expected(expected: Any, actual: Any) -> bool:
    """Require every annotated leaf while allowing unannotated evidence metadata."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(
            key in actual and _contains_expected(value, actual[key])
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        used: set[int] = set()
        for expected_item in expected:
            matched_index = next(
                (
                    index
                    for index, actual_item in enumerate(actual)
                    if index not in used
                    and _contains_expected(expected_item, actual_item)
                ),
                None,
            )
            if matched_index is None:
                return False
            used.add(matched_index)
        return True
    return expected == actual


def _fact_operation(fact: dict[str, Any]) -> set[str]:
    action = _dict(fact.get("action"))
    values = {
        _norm(fact.get("predicate")),
        _norm(action.get("canonical")),
        _norm(action.get("raw")),
    }
    for claim in _rows(fact.get("claims")):
        if _text(claim.get("claim_type")) in {"PRIMARY_OPERATION", "ATOMIC_OPERATION"}:
            values.add(_norm(claim.get("predicate")))
    return {value for value in values if value}


def _fact_objects(fact: dict[str, Any]) -> set[str]:
    subject = _dict(fact.get("subject"))
    object_part = _dict(fact.get("object"))
    values = [
        *_values(subject.get("entity_refs")),
        *_values(object_part.get("entity_refs")),
    ]
    for claim in _rows(fact.get("claims")):
        values.extend(_values(claim.get("object_refs")))
    return {_norm(value) for value in values if _norm(value)}


def _fact_actors(fact: dict[str, Any]) -> set[str]:
    subject = _dict(fact.get("subject"))
    values = list(_values(subject.get("actor_refs")))
    for claim in _rows(fact.get("claims")):
        values.extend(_values(claim.get("subject_refs")))
    return {_norm(value) for value in values if _norm(value)}


def _fact_locators(fact: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for span in _rows(fact.get("source_spans")):
        locator = _text(span.get("locator") or span.get("source_locator"))
        if locator:
            values.add(locator)
    return values


def _fact_type(fact: dict[str, Any]) -> str:
    return _norm(fact.get("fact_type") or fact.get("kind"))


def _expected_base(gt: dict[str, Any]) -> tuple[set[str], set[str], str]:
    operations = {
        _norm(value)
        for value in [
            gt.get("operation"),
            gt.get("canonical_name"),
            *_values(gt.get("operation_aliases")),
        ]
        if _norm(value)
    }
    objects = {
        _norm(value)
        for value in _values(gt.get("object_refs"))
        if _norm(value)
    }
    return operations, objects, _norm(gt.get("fact_type"))


def _candidate_base_matches(gt: dict[str, Any], fact: dict[str, Any]) -> bool:
    operations, objects, expected_fact_type = _expected_base(gt)
    if expected_fact_type and expected_fact_type != _fact_type(fact):
        return False
    if operations and not operations.intersection(_fact_operation(fact)):
        return False
    candidate_objects = _fact_objects(fact)
    if objects and not objects.issubset(candidate_objects):
        return False
    return True


def _expected_slot(gt: dict[str, Any], field: str) -> Any:
    if field == "condition_frame":
        value = gt.get("condition_frame")
        if value in (None, "", {}):
            preconditions = gt.get("preconditions")
            return preconditions if preconditions not in (None, "", []) else None
        return value
    if field == "source_locators":
        values = [
            _text(value)
            for value in _values(gt.get("source_locators"))
            if _text(value)
        ]
        return sorted(values) if values else None
    if field == "compensation":
        value = gt.get("compensation")
        if value in (None, "", []):
            value = gt.get("compensations")
        return value if value not in (None, "", []) else None
    value = gt.get(field)
    return value if value not in (None, "", [], {}) else None


def _candidate_slot(fact: dict[str, Any], field: str) -> Any:
    if field == "actor_refs":
        return sorted(_fact_actors(fact))
    if field == "object_refs":
        return sorted(_fact_objects(fact))
    if field == "source_locators":
        return sorted(_fact_locators(fact))
    if field == "compensation":
        return fact.get("compensation") or fact.get("compensations") or []
    return fact.get(field)


def _slot_alignment(expected: Any, actual: Any) -> str:
    expected_canonical = _canonical(expected)
    actual_canonical = _canonical(actual)
    if expected_canonical == actual_canonical:
        return "EXACT"
    if expected_canonical in (None, "", [], {}):
        return "NOT_ANNOTATED"
    if actual_canonical in (None, "", [], {}):
        return "MISSING"
    if _contains_expected(expected_canonical, actual_canonical):
        return "EXACT"
    return "WRONG"


def _candidate_id(fact: dict[str, Any]) -> str:
    return _text(fact.get("fact_id") or fact.get("rule_id") or fact.get("behavior_id"))


def _annotated_rows(ground_truth: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for collection in ("business_rules", "business_behaviors"):
        for row in _rows(ground_truth.get(collection)):
            if _text(row.get("annotation_status") or "CONFIRMED") != "CONFIRMED":
                continue
            if any(
                _expected_slot(row, field) is not None
                for field in _ANNOTATION_TRIGGER_FIELDS
            ):
                result.append((collection, row))
    return result


def _source_locator_metrics(
    annotated: list[tuple[str, dict[str, Any]]],
    alignments: list[dict[str, Any]],
) -> dict[str, Any]:
    annotated_ids = {
        _text(row.get("ground_truth_id"))
        for _collection, row in annotated
        if _expected_slot(row, "source_locators") is not None
        and _text(row.get("ground_truth_id"))
    }
    statuses: Counter[str] = Counter()
    for alignment in alignments:
        ground_truth_id = _text(alignment.get("ground_truth_id"))
        if ground_truth_id not in annotated_ids:
            continue
        fact_status = _text(alignment.get("alignment_status")).upper()
        if fact_status == "MISSING":
            statuses["MISSING"] += 1
            continue
        if fact_status == "AMBIGUOUS":
            statuses["AMBIGUOUS"] += 1
            continue
        locator_alignment = _dict(
            _dict(alignment.get("slot_alignments")).get("source_locators")
        )
        locator_status = _text(locator_alignment.get("status")).upper() or "MISSING"
        statuses[locator_status] += 1

    annotated_count = len(annotated_ids)
    exact_count = statuses["EXACT"]
    return {
        "source_locator_annotated_fact_count": annotated_count,
        "source_locator_exact_fact_count": exact_count,
        "source_locator_missing_fact_count": statuses["MISSING"],
        "source_locator_wrong_fact_count": statuses["WRONG"],
        "source_locator_ambiguous_fact_count": statuses["AMBIGUOUS"],
        "source_locator_exact_accuracy": (
            exact_count / annotated_count if annotated_count else None
        ),
        "source_locator_status_distribution": dict(statuses),
    }


def _accepted_fact_precision_metrics(
    ground_truth: dict[str, Any],
    facts: list[dict[str, Any]],
    alignments: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Measure false ACCEPTED facts against the declared complete locator universe."""
    scope_complete = ground_truth.get("explicit_fact_scope_complete") is True
    scope_locators = {
        _text(value)
        for value in _values(ground_truth.get("explicit_fact_scope_locators"))
        if _text(value)
    }
    if not scope_complete or not scope_locators:
        return (
            {
                "accepted_fact_scope_complete": False,
                "accepted_fact_scope_locator_count": len(scope_locators),
                "accepted_fact_count_in_scope": 0,
                "supported_accepted_fact_count": 0,
                "false_accepted_fact_count": 0,
                "accepted_fact_precision": None,
                "false_accepted_rate": None,
            },
            [],
        )

    scoped_facts = [
        fact
        for fact in facts
        if _fact_type(fact) != _norm("TERM_ALIAS")
        and bool(scope_locators.intersection(_fact_locators(fact)))
    ]
    selected_ids = {
        _text(row.get("candidate_id"))
        for row in alignments
        if _text(row.get("alignment_status")).upper() in {"EXACT", "PARTIAL"}
        and _text(row.get("candidate_id"))
    }
    supported = [fact for fact in scoped_facts if _candidate_id(fact) in selected_ids]
    false_accepted = [fact for fact in scoped_facts if _candidate_id(fact) not in selected_ids]
    accepted_count = len(scoped_facts)
    supported_count = len(supported)
    false_count = len(false_accepted)
    false_rows = [
        {
            "candidate_id": _candidate_id(fact),
            "fact_type": fact.get("fact_type") or fact.get("kind"),
            "operations": sorted(_fact_operation(fact)),
            "objects": sorted(_fact_objects(fact)),
            "actors": sorted(_fact_actors(fact)),
            "source_locators": sorted(_fact_locators(fact)),
            "raw_statement": fact.get("raw_statement"),
            "reason": "ACCEPTED_FACT_NOT_SELECTED_BY_UNIQUE_GROUND_TRUTH_ALIGNMENT",
        }
        for fact in false_accepted
    ]
    return (
        {
            "accepted_fact_scope_complete": True,
            "accepted_fact_scope_locator_count": len(scope_locators),
            "accepted_fact_count_in_scope": accepted_count,
            "supported_accepted_fact_count": supported_count,
            "false_accepted_fact_count": false_count,
            "accepted_fact_precision": (
                supported_count / accepted_count if accepted_count else 0.0
            ),
            "false_accepted_rate": false_count / accepted_count if accepted_count else 1.0,
        },
        false_rows,
    )


def evaluate_business_fact_slots(
    ground_truth: dict[str, Any],
    product_asset: dict[str, Any],
) -> dict[str, Any]:
    annotated = _annotated_rows(ground_truth)
    ledger = _dict(product_asset.get("business_fact_ledger"))
    facts = [
        row
        for row in _rows(ledger.get("items"))
        if _text(row.get("status")) in {"", "ACCEPTED", "CONFIRMED"}
    ]
    if not annotated:
        return {
            "schema": FACT_SLOT_MEASUREMENT_SCHEMA,
            "status": "NOT_MEASURED",
            "reason": "no confirmed business rule/behavior Ground Truth rows declare extended slot fields",
            "metrics": {},
            "alignments": [],
            "fuzzy_or_llm_alignment_used": False,
            "model_writeback_allowed": False,
        }

    alignments: list[dict[str, Any]] = []
    slot_statuses: Counter[str] = Counter()
    fact_statuses: Counter[str] = Counter()
    p0_total = 0
    p0_exact = 0
    for collection, gt in annotated:
        criticality = _text(gt.get("criticality") or "P2").upper()
        if criticality == "P0":
            p0_total += 1
        matches = [fact for fact in facts if _candidate_base_matches(gt, fact)]
        if not matches:
            fact_status = "MISSING"
            fact_statuses[fact_status] += 1
            alignments.append(
                {
                    "ground_truth_id": gt.get("ground_truth_id"),
                    "collection": collection,
                    "criticality": criticality,
                    "alignment_status": fact_status,
                    "candidate_id": "",
                    "slot_alignments": {},
                }
            )
            continue
        if len(matches) > 1:
            fact_status = "AMBIGUOUS"
            fact_statuses[fact_status] += 1
            alignments.append(
                {
                    "ground_truth_id": gt.get("ground_truth_id"),
                    "collection": collection,
                    "criticality": criticality,
                    "alignment_status": fact_status,
                    "candidate_id": "",
                    "candidate_ids": sorted(_candidate_id(row) for row in matches),
                    "slot_alignments": {},
                }
            )
            continue

        candidate = matches[0]
        slot_rows: dict[str, Any] = {}
        measured_statuses: list[str] = []
        for field in _SLOT_FIELDS:
            expected = _expected_slot(gt, field)
            if expected is None:
                continue
            actual = _candidate_slot(candidate, field)
            status = _slot_alignment(expected, actual)
            slot_statuses[status] += 1
            measured_statuses.append(status)
            slot_rows[field] = {
                "status": status,
                "expected": expected,
                "actual": actual,
            }
        if measured_statuses and all(status == "EXACT" for status in measured_statuses):
            fact_status = "EXACT"
            if criticality == "P0":
                p0_exact += 1
        else:
            fact_status = "PARTIAL"
        fact_statuses[fact_status] += 1
        alignments.append(
            {
                "ground_truth_id": gt.get("ground_truth_id"),
                "collection": collection,
                "criticality": criticality,
                "alignment_status": fact_status,
                "candidate_id": _candidate_id(candidate),
                "candidate_status": candidate.get("status"),
                "slot_alignments": slot_rows,
            }
        )

    measured_slot_count = sum(slot_statuses.values())
    exact_slot_count = slot_statuses["EXACT"]
    total_facts = len(annotated)
    covered_facts = fact_statuses["EXACT"] + fact_statuses["PARTIAL"]
    evidence_metrics = _source_locator_metrics(annotated, alignments)
    precision_metrics, false_accepted_facts = _accepted_fact_precision_metrics(
        ground_truth,
        facts,
        alignments,
    )
    return {
        "schema": FACT_SLOT_MEASUREMENT_SCHEMA,
        "status": "PASS",
        "metrics": {
            "annotated_fact_count": total_facts,
            "covered_fact_count": covered_facts,
            "exact_fact_count": fact_statuses["EXACT"],
            "partial_fact_count": fact_statuses["PARTIAL"],
            "missing_fact_count": fact_statuses["MISSING"],
            "ambiguous_fact_count": fact_statuses["AMBIGUOUS"],
            "fact_recall": covered_facts / total_facts if total_facts else 0.0,
            "exact_fact_rate": fact_statuses["EXACT"] / total_facts if total_facts else 0.0,
            "measured_slot_count": measured_slot_count,
            "exact_slot_count": exact_slot_count,
            "missing_slot_count": slot_statuses["MISSING"],
            "wrong_slot_count": slot_statuses["WRONG"],
            "slot_exact_accuracy": (
                exact_slot_count / measured_slot_count if measured_slot_count else 0.0
            ),
            "p0_fact_count": p0_total,
            "p0_exact_fact_count": p0_exact,
            "p0_exact_fact_recall": p0_exact / p0_total if p0_total else None,
            **evidence_metrics,
            **precision_metrics,
            "slot_status_distribution": dict(slot_statuses),
            "fact_status_distribution": dict(fact_statuses),
        },
        "alignments": alignments,
        "false_accepted_facts": false_accepted_facts,
        "candidate_selection_contract": {
            "fact_type_exact": True,
            "ground_truth_objects_must_be_subset_of_candidate_objects": True,
            "operation_alias_intersection_allowed": True,
            "annotated_nested_coordinates_must_be_exact_subsets": True,
            "unannotated_product_evidence_fields_are_ignored": True,
            "single_shared_object_is_sufficient": False,
        },
        "evidence_address_measurement_contract": {
            "annotated_fact_denominator_includes_missing_facts": True,
            "annotated_fact_denominator_includes_ambiguous_facts": True,
            "exact_source_locator_required": True,
            "workspace_absolute_path_allowed": False,
        },
        "accepted_fact_precision_contract": {
            "explicit_fact_scope_complete_required": True,
            "explicit_fact_scope_locators_required": True,
            "accepted_scope_uses_declared_exact_source_locators": True,
            "unannotated_scope_blocks_remain_in_precision_scope": True,
            "duplicate_candidates_count_as_false_accepted": True,
            "ambiguous_candidates_count_as_false_accepted": True,
            "automatic_winner_allowed": False,
        },
        "alignment_authority": "DETERMINISTIC_EXACT_TYPED_SLOT_MATCHING",
        "fuzzy_or_llm_alignment_used": False,
        "automatic_winner_used": False,
        "ground_truth_generated_from_product_output": False,
        "model_writeback_allowed": False,
    }


__all__ = [
    "FACT_SLOT_MEASUREMENT_SCHEMA",
    "evaluate_business_fact_slots",
]
