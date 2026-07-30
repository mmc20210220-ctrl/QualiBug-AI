"""Deterministic Ground Truth alignment against the existing understanding model."""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

ALIGNMENT_SCHEMA = "qualibug.enterprise-understanding-alignment.v1"
_MATCHED = {"EXACT_MATCH", "PARTIAL_MATCH", "UNKNOWN_CORRECTLY_EXPOSED"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _text(value).lower())


def _values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    return [_text(value)] if _text(value) else []


def _name_set(row: dict[str, Any], *fields: str) -> set[str]:
    values: list[str] = []
    for field in fields:
        values.extend(_values(row.get(field)))
    return {_norm(value) for value in values if _norm(value)}


def _model(asset: dict[str, Any]) -> dict[str, Any]:
    candidate = asset.get("enterprise_understanding_model")
    if isinstance(candidate, dict):
        return candidate
    return asset


def _entity_index(rows: Iterable[dict[str, Any]], *, id_fields: tuple[str, ...]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for row in rows:
        names = _name_set(
            row,
            "name",
            "canonical_name",
            "aliases",
            "raw_names",
            "raw_action_names",
            "display_name",
        )
        for field in id_fields:
            identity = _text(row.get(field))
            if identity:
                result[identity] = names
        for name in names:
            result.setdefault(name, set()).update(names)
    return result


def _resolve_refs(refs: Any, index: dict[str, set[str]]) -> set[str]:
    result: set[str] = set()
    for raw in _values(refs):
        result.update(index.get(raw, set()))
        result.update(index.get(_norm(raw), set()))
        normalized = _norm(raw)
        if normalized:
            result.add(normalized)
    return result


def _evidence(row: dict[str, Any]) -> list[dict[str, Any]]:
    return _rows(row.get("evidence"))


def _candidate_id(row: dict[str, Any]) -> str:
    for field in (
        "object_id",
        "actor_id",
        "operation_id",
        "relation_id",
        "lifecycle_id",
        "transition_id",
        "rule_id",
        "behavior_id",
        "conflict_id",
        "unknown_id",
        "fact_id",
    ):
        value = _text(row.get(field))
        if value:
            return value
    return ""


def _base_alignment(
    ground_truth: dict[str, Any],
    *,
    collection: str,
    status: str,
    candidate: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = {
        "ground_truth_id": ground_truth.get("ground_truth_id"),
        "collection": collection,
        "alignment_status": status,
        "criticality": ground_truth.get("criticality") or "P2",
        "candidate_id": _candidate_id(candidate or {}),
        "candidate_status": _text((candidate or {}).get("status")),
        "candidate_evidence": _evidence(candidate or {}),
        "details": dict(details or {}),
    }
    return row


def _best_name_match(
    ground_truth: dict[str, Any], candidates: list[dict[str, Any]], *, collection: str
) -> dict[str, Any]:
    expected = _name_set(ground_truth, "canonical_name", "aliases")
    matches = [
        row
        for row in candidates
        if expected
        & _name_set(
            row,
            "name",
            "canonical_name",
            "aliases",
            "raw_names",
            "raw_action_names",
            "display_name",
        )
    ]
    if len(matches) == 1:
        return _base_alignment(
            ground_truth, collection=collection, status="EXACT_MATCH", candidate=matches[0]
        )
    if len(matches) > 1:
        return _base_alignment(
            ground_truth,
            collection=collection,
            status="CONFLICTED",
            details={"candidate_ids": [_candidate_id(row) for row in matches]},
        )
    return _base_alignment(ground_truth, collection=collection, status="MISSING")


def _operation_names(row: dict[str, Any]) -> set[str]:
    return _name_set(
        row,
        "name",
        "canonical_name",
        "raw_action_names",
        "operation_ref",
        "operation",
        "action",
    )


def _object_names(
    row: dict[str, Any], object_index: dict[str, set[str]]
) -> set[str]:
    result = _resolve_refs(row.get("object_refs"), object_index)
    result.update(_resolve_refs(row.get("objects"), object_index))
    result.update(_name_set(row, "object", "object_name", "subject_object"))
    return result


def _actor_names(row: dict[str, Any], actor_index: dict[str, set[str]]) -> set[str]:
    result = _resolve_refs(row.get("actor_refs"), actor_index)
    result.update(_resolve_refs(row.get("actors"), actor_index))
    result.update(_name_set(row, "actor", "role", "actor_name"))
    return result


def _align_operation(
    ground_truth: dict[str, Any],
    candidates: list[dict[str, Any]],
    object_index: dict[str, set[str]],
) -> dict[str, Any]:
    expected_action = _name_set(ground_truth, "canonical_name", "aliases")
    expected_objects = {_norm(value) for value in _values(ground_truth.get("object_refs"))}
    action_matches = [row for row in candidates if expected_action & _operation_names(row)]
    exact = [
        row
        for row in action_matches
        if not expected_objects or expected_objects & _object_names(row, object_index)
    ]
    if len(exact) == 1:
        return _base_alignment(
            ground_truth, collection="operations", status="EXACT_MATCH", candidate=exact[0]
        )
    if len(exact) > 1:
        return _base_alignment(
            ground_truth,
            collection="operations",
            status="CONFLICTED",
            details={"candidate_ids": [_candidate_id(row) for row in exact]},
        )
    if action_matches:
        return _base_alignment(
            ground_truth,
            collection="operations",
            status="WRONG_BINDING",
            candidate=action_matches[0] if len(action_matches) == 1 else None,
            details={
                "expected_object_refs": sorted(expected_objects),
                "candidate_object_refs": sorted(
                    {
                        value
                        for row in action_matches
                        for value in _object_names(row, object_index)
                    }
                ),
            },
        )
    return _base_alignment(ground_truth, collection="operations", status="MISSING")


def _relation_type(row: dict[str, Any]) -> str:
    return _norm(
        row.get("relation_type")
        or row.get("relation")
        or row.get("predicate")
        or row.get("type")
    )


def _relation_endpoint(row: dict[str, Any], side: str, index: dict[str, set[str]]) -> set[str]:
    fields = (
        ("from_object", "from_object_ref", "from", "source_object_ref", "subject_ref")
        if side == "from"
        else ("to_object", "to_object_ref", "to", "target_object_ref", "object_ref")
    )
    result: set[str] = set()
    for field in fields:
        result.update(_resolve_refs(row.get(field), index))
    return result


def _align_relation(
    ground_truth: dict[str, Any], candidates: list[dict[str, Any]], object_index: dict[str, set[str]]
) -> dict[str, Any]:
    expected_from = {_norm(ground_truth.get("from_object"))}
    expected_to = {_norm(ground_truth.get("to_object"))}
    expected_type = _norm(ground_truth.get("relation_type"))
    endpoint_matches = [
        row
        for row in candidates
        if expected_from & _relation_endpoint(row, "from", object_index)
        and expected_to & _relation_endpoint(row, "to", object_index)
    ]
    exact = [row for row in endpoint_matches if _relation_type(row) == expected_type]
    if len(exact) == 1:
        return _base_alignment(
            ground_truth,
            collection="object_relations",
            status="EXACT_MATCH",
            candidate=exact[0],
        )
    if endpoint_matches:
        return _base_alignment(
            ground_truth,
            collection="object_relations",
            status="PARTIAL_MATCH",
            candidate=endpoint_matches[0] if len(endpoint_matches) == 1 else None,
            details={"expected_relation_type": expected_type},
        )
    return _base_alignment(ground_truth, collection="object_relations", status="MISSING")


def _transition_rows(model: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _rows(model.get("state_transitions"))
    for lifecycle in _rows(model.get("lifecycles")):
        for transition in _rows(lifecycle.get("transitions")):
            item = dict(transition)
            item.setdefault("object_ref", lifecycle.get("object_ref") or lifecycle.get("object_id"))
            item.setdefault("lifecycle_id", lifecycle.get("lifecycle_id"))
            rows.append(item)
    return rows


def _state(row: dict[str, Any], side: str) -> str:
    fields = (
        ("from_state", "before_state", "source_state")
        if side == "from"
        else ("to_state", "after_state", "target_state")
    )
    for field in fields:
        value = _norm(row.get(field))
        if value:
            return value
    return ""


def _align_transition(
    ground_truth: dict[str, Any], candidates: list[dict[str, Any]], object_index: dict[str, set[str]]
) -> dict[str, Any]:
    expected_object = {_norm(ground_truth.get("object_ref"))}
    expected_from = _norm(ground_truth.get("from_state"))
    expected_to = _norm(ground_truth.get("to_state"))
    exact = [
        row
        for row in candidates
        if expected_object & _resolve_refs(row.get("object_ref"), object_index)
        and _state(row, "from") == expected_from
        and _state(row, "to") == expected_to
    ]
    if len(exact) == 1:
        return _base_alignment(
            ground_truth,
            collection="state_transitions",
            status="EXACT_MATCH",
            candidate=exact[0],
        )
    partial = [
        row
        for row in candidates
        if expected_object & _resolve_refs(row.get("object_ref"), object_index)
        and (_state(row, "from") == expected_from or _state(row, "to") == expected_to)
    ]
    if partial:
        return _base_alignment(
            ground_truth,
            collection="state_transitions",
            status="PARTIAL_MATCH",
            candidate=partial[0] if len(partial) == 1 else None,
        )
    return _base_alignment(ground_truth, collection="state_transitions", status="MISSING")


def _condition_signature(value: Any) -> set[str]:
    result: set[str] = set()
    for row in _rows(value):
        field = _norm(row.get("field") or row.get("field_candidate"))
        operator = _norm(row.get("operator") or row.get("operator_candidate"))
        candidate = row.get("value") if "value" in row else row.get("value_candidate")
        if isinstance(candidate, dict):
            candidate = candidate.get("normalized_value", candidate.get("raw"))
        result.add(json.dumps([field, operator, _norm(candidate)], ensure_ascii=False))
    return result


def _state_effect_signature(value: Any) -> set[str]:
    return {
        json.dumps(
            [_state(row, "from"), _state(row, "to"), _norm(row.get("object_ref"))],
            ensure_ascii=False,
        )
        for row in _rows(value)
    }


def _align_behavior(
    ground_truth: dict[str, Any],
    candidates: list[dict[str, Any]],
    object_index: dict[str, set[str]],
    actor_index: dict[str, set[str]],
    *,
    collection: str,
) -> dict[str, Any]:
    expected_action = {_norm(ground_truth.get("operation"))}
    expected_objects = {_norm(value) for value in _values(ground_truth.get("object_refs"))}
    expected_actors = {_norm(value) for value in _values(ground_truth.get("actor_refs"))}
    action_matches = [row for row in candidates if expected_action & _operation_names(row)]
    object_matches = [
        row
        for row in action_matches
        if expected_objects & _object_names(row, object_index)
    ]
    if not action_matches:
        return _base_alignment(ground_truth, collection=collection, status="MISSING")
    if not object_matches:
        return _base_alignment(
            ground_truth,
            collection=collection,
            status="WRONG_BINDING",
            candidate=action_matches[0] if len(action_matches) == 1 else None,
            details={"slot": "object_refs"},
        )

    expected_permission = _norm(ground_truth.get("permission_decision"))
    expected_conditions = _condition_signature(ground_truth.get("preconditions"))
    expected_effects = _state_effect_signature(ground_truth.get("state_effects"))
    exact: list[dict[str, Any]] = []
    partial: list[tuple[dict[str, Any], list[str]]] = []
    for row in object_matches:
        missing_slots: list[str] = []
        if expected_actors and not expected_actors & _actor_names(row, actor_index):
            missing_slots.append("actor_refs")
        if expected_permission and _norm(row.get("permission_decision")) != expected_permission:
            missing_slots.append("permission_decision")
        candidate_conditions = _condition_signature(row.get("preconditions") or row.get("conditions"))
        if expected_conditions and not expected_conditions.issubset(candidate_conditions):
            missing_slots.append("preconditions")
        candidate_effects = _state_effect_signature(row.get("state_effects"))
        if expected_effects and not expected_effects.issubset(candidate_effects):
            missing_slots.append("state_effects")
        if missing_slots:
            partial.append((row, missing_slots))
        else:
            exact.append(row)
    if len(exact) == 1:
        candidate = exact[0]
        candidate_status = _text(candidate.get("status")).upper()
        status = "EXACT_MATCH" if candidate_status in {"", "CONFIRMED", "ACCEPTED"} else "PARTIAL_MATCH"
        return _base_alignment(
            ground_truth,
            collection=collection,
            status=status,
            candidate=candidate,
            details={"candidate_not_confirmed": status == "PARTIAL_MATCH"},
        )
    if len(exact) > 1:
        return _base_alignment(
            ground_truth,
            collection=collection,
            status="CONFLICTED",
            details={"candidate_ids": [_candidate_id(row) for row in exact]},
        )
    row, missing_slots = partial[0]
    return _base_alignment(
        ground_truth,
        collection=collection,
        status="PARTIAL_MATCH",
        candidate=row if len(partial) == 1 else None,
        details={"missing_or_wrong_slots": missing_slots},
    )


def _align_expected_unknown(
    ground_truth: dict[str, Any], candidates: list[dict[str, Any]]
) -> dict[str, Any]:
    expected = _name_set(ground_truth, "reason_code", "kind", "canonical_name", "aliases")
    matches = [
        row
        for row in candidates
        if expected & _name_set(row, "reason_code", "kind", "question", "message")
    ]
    if matches:
        return _base_alignment(
            ground_truth,
            collection="expected_unknowns",
            status="UNKNOWN_CORRECTLY_EXPOSED",
            candidate=matches[0] if len(matches) == 1 else None,
        )
    return _base_alignment(
        ground_truth,
        collection="expected_unknowns",
        status="UNKNOWN_SHOULD_HAVE_BEEN_RESOLVED",
    )


def _matched_candidate_ids(alignments: Iterable[dict[str, Any]]) -> set[str]:
    return {
        _text(row.get("candidate_id"))
        for row in alignments
        if _text(row.get("candidate_id")) and row.get("alignment_status") in _MATCHED
    }


def align_enterprise_understanding(
    ground_truth: dict[str, Any], asset: dict[str, Any]
) -> dict[str, Any]:
    model = _model(asset)
    objects = _rows(model.get("business_objects"))
    actors = _rows(model.get("actors"))
    operations = _rows(model.get("operations"))
    relations = _rows(model.get("object_relations"))
    transitions = _transition_rows(model)
    rules = _rows(model.get("rules"))
    behaviors = _rows(model.get("business_behaviors"))
    conflicts = _rows(model.get("conflicts"))
    unknowns = _rows(model.get("unknowns"))
    object_index = _entity_index(objects, id_fields=("object_id", "entity_id"))
    actor_index = _entity_index(actors, id_fields=("actor_id", "role_id"))

    alignments: list[dict[str, Any]] = []
    for row in _rows(ground_truth.get("business_objects")):
        alignments.append(_best_name_match(row, objects, collection="business_objects"))
    for row in _rows(ground_truth.get("actors")):
        alignments.append(_best_name_match(row, actors, collection="actors"))
    for row in _rows(ground_truth.get("operations")):
        alignments.append(_align_operation(row, operations, object_index))
    for row in _rows(ground_truth.get("object_relations")):
        alignments.append(_align_relation(row, relations, object_index))
    for row in _rows(ground_truth.get("state_transitions")):
        alignments.append(_align_transition(row, transitions, object_index))
    for row in _rows(ground_truth.get("business_rules")):
        alignments.append(
            _align_behavior(
                row,
                rules,
                object_index,
                actor_index,
                collection="business_rules",
            )
        )
    for row in _rows(ground_truth.get("business_behaviors")):
        alignments.append(
            _align_behavior(
                row,
                behaviors,
                object_index,
                actor_index,
                collection="business_behaviors",
            )
        )
    for row in _rows(ground_truth.get("conflicts")):
        alignments.append(_best_name_match(row, conflicts, collection="conflicts"))
    for row in _rows(ground_truth.get("expected_unknowns")):
        alignments.append(_align_expected_unknown(row, unknowns))

    matched_ids = _matched_candidate_ids(alignments)
    formal_candidates = [*objects, *actors, *operations, *relations, *rules, *behaviors]
    unmatched_confirmed = [
        {
            "candidate_id": _candidate_id(row),
            "candidate_collection": next(
                (
                    name
                    for name, collection_rows in (
                        ("business_objects", objects),
                        ("actors", actors),
                        ("operations", operations),
                        ("object_relations", relations),
                        ("business_rules", rules),
                        ("business_behaviors", behaviors),
                    )
                    if row in collection_rows
                ),
                "unknown",
            ),
            "candidate_status": _text(row.get("status")),
            "candidate_evidence": _evidence(row),
        }
        for row in formal_candidates
        if _candidate_id(row)
        and _candidate_id(row) not in matched_ids
        and _text(row.get("status")).upper() in {"CONFIRMED", "ACCEPTED", "PASS"}
    ]
    return {
        "schema": ALIGNMENT_SCHEMA,
        "project_id": ground_truth.get("project_id"),
        "ground_truth_validation_status": (
            ground_truth.get("validation_receipt", {}).get("status")
            if isinstance(ground_truth.get("validation_receipt"), dict)
            else "UNKNOWN"
        ),
        "alignments": alignments,
        "unmatched_confirmed_candidates": unmatched_confirmed,
        "alignment_authority": "DETERMINISTIC_EXACT_ALIAS_AND_SLOT_MATCHING",
        "fuzzy_or_llm_match_can_confirm": False,
        "model_writeback_allowed": False,
    }


__all__ = ["ALIGNMENT_SCHEMA", "align_enterprise_understanding"]
