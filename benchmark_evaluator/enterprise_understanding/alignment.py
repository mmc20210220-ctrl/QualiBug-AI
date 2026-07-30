"""Deterministic evaluator-side alignment for the existing understanding asset."""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

ALIGNMENT_SCHEMA = "qualibug.enterprise-understanding-alignment.v1"
EXACT_STATUSES = {"EXACT_MATCH", "UNKNOWN_CORRECTLY_EXPOSED"}
COVERED_STATUSES = {*EXACT_STATUSES, "PARTIAL_MATCH"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _text(value).lower())


def _values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if isinstance(value, dict):
        return [
            _text(value.get("canonical") or value.get("raw") or value.get("name"))
        ] if _text(value.get("canonical") or value.get("raw") or value.get("name")) else []
    return [_text(value)] if _text(value) else []


def _names(row: dict[str, Any], *fields: str) -> set[str]:
    result: set[str] = set()
    for field in fields:
        result.update(_norm(value) for value in _values(row.get(field)) if _norm(value))
    return result


def _model(asset: dict[str, Any]) -> dict[str, Any]:
    value = asset.get("enterprise_understanding_model")
    return value if isinstance(value, dict) else asset


def _candidate_id(row: dict[str, Any]) -> str:
    for field in (
        "object_id", "actor_id", "operation_id", "relation_id", "lifecycle_id",
        "transition_id", "rule_id", "behavior_id", "conflict_id", "unknown_id", "fact_id",
    ):
        value = _text(row.get(field))
        if value:
            return value
    return ""


def _evidence(row: dict[str, Any]) -> list[dict[str, Any]]:
    return _rows(row.get("evidence"))


def _alignment(
    gt: dict[str, Any],
    collection: str,
    status: str,
    candidate: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate = candidate or {}
    return {
        "ground_truth_id": gt.get("ground_truth_id"),
        "collection": collection,
        "alignment_status": status,
        "criticality": gt.get("criticality") or "P2",
        "candidate_id": _candidate_id(candidate),
        "candidate_status": _text(candidate.get("status")),
        "candidate_evidence": _evidence(candidate),
        "details": dict(details or {}),
    }


def _entity_index(rows: Iterable[dict[str, Any]], id_fields: tuple[str, ...]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for row in rows:
        aliases = _names(
            row, "name", "canonical_name", "aliases", "raw_names", "raw_action_names", "display_name"
        )
        for field in id_fields:
            identity = _text(row.get(field))
            if identity:
                result[identity] = set(aliases)
        for alias in aliases:
            result.setdefault(alias, set()).update(aliases)
    return result


def _resolve(value: Any, index: dict[str, set[str]]) -> set[str]:
    result: set[str] = set()
    for raw in _values(value):
        result.update(index.get(raw, set()))
        result.update(index.get(_norm(raw), set()))
        if _norm(raw):
            result.add(_norm(raw))
    return result


def _best_name(gt: dict[str, Any], candidates: list[dict[str, Any]], collection: str) -> dict[str, Any]:
    expected = _names(gt, "canonical_name", "aliases", "reason_code", "kind")
    matches = [
        row for row in candidates
        if expected & _names(
            row, "name", "canonical_name", "aliases", "raw_names", "raw_action_names",
            "display_name", "reason_code", "kind", "conflict_type",
        )
    ]
    if len(matches) == 1:
        return _alignment(gt, collection, "EXACT_MATCH", matches[0])
    if len(matches) > 1:
        return _alignment(
            gt, collection, "CONFLICTED",
            details={"candidate_ids": [_candidate_id(row) for row in matches]},
        )
    return _alignment(gt, collection, "MISSING")


def _operation_names(row: dict[str, Any]) -> set[str]:
    return _names(
        row, "name", "canonical_name", "raw_action_names", "operation_ref", "operation", "action"
    )


def _object_names(row: dict[str, Any], index: dict[str, set[str]]) -> set[str]:
    result = _resolve(row.get("object_refs"), index)
    result.update(_resolve(row.get("objects"), index))
    result.update(_names(row, "object", "object_name", "subject_object"))
    return result


def _actor_names(row: dict[str, Any], index: dict[str, set[str]]) -> set[str]:
    result = _resolve(row.get("actor_refs"), index)
    result.update(_resolve(row.get("actors"), index))
    result.update(_names(row, "actor", "role", "actor_name"))
    return result


def _align_operation(gt: dict[str, Any], candidates: list[dict[str, Any]], object_index: dict[str, set[str]]) -> dict[str, Any]:
    expected_action = _names(gt, "canonical_name", "aliases")
    expected_objects = {_norm(value) for value in _values(gt.get("object_refs"))}
    action_matches = [row for row in candidates if expected_action & _operation_names(row)]
    exact = [
        row for row in action_matches
        if not expected_objects or expected_objects & _object_names(row, object_index)
    ]
    if len(exact) == 1:
        return _alignment(gt, "operations", "EXACT_MATCH", exact[0])
    if len(exact) > 1:
        return _alignment(
            gt, "operations", "CONFLICTED",
            details={"candidate_ids": [_candidate_id(row) for row in exact]},
        )
    if action_matches:
        return _alignment(
            gt, "operations", "WRONG_BINDING",
            action_matches[0] if len(action_matches) == 1 else None,
            {
                "expected_object_refs": sorted(expected_objects),
                "candidate_object_refs": sorted({
                    value for row in action_matches for value in _object_names(row, object_index)
                }),
            },
        )
    return _alignment(gt, "operations", "MISSING")


def _relation_endpoint(row: dict[str, Any], side: str, index: dict[str, set[str]]) -> set[str]:
    fields = (
        ("from_object", "from_object_ref", "from", "source_object_ref", "subject_ref")
        if side == "from"
        else ("to_object", "to_object_ref", "to", "target_object_ref", "object_ref")
    )
    result: set[str] = set()
    for field in fields:
        result.update(_resolve(row.get(field), index))
    return result


def _align_relation(gt: dict[str, Any], candidates: list[dict[str, Any]], index: dict[str, set[str]]) -> dict[str, Any]:
    expected_from = {_norm(gt.get("from_object"))}
    expected_to = {_norm(gt.get("to_object"))}
    expected_type = _norm(gt.get("relation_type"))
    endpoint_matches = [
        row for row in candidates
        if expected_from & _relation_endpoint(row, "from", index)
        and expected_to & _relation_endpoint(row, "to", index)
    ]
    exact = [
        row for row in endpoint_matches
        if _norm(row.get("relation_type") or row.get("relation") or row.get("predicate") or row.get("type"))
        == expected_type
    ]
    if len(exact) == 1:
        return _alignment(gt, "object_relations", "EXACT_MATCH", exact[0])
    if endpoint_matches:
        return _alignment(
            gt, "object_relations", "PARTIAL_MATCH",
            endpoint_matches[0] if len(endpoint_matches) == 1 else None,
            {"expected_relation_type": expected_type},
        )
    return _alignment(gt, "object_relations", "MISSING")


def _state(row: dict[str, Any], side: str) -> str:
    fields = (
        ("from_state", "before_state", "source_state")
        if side == "from"
        else ("to_state", "after_state", "target_state")
    )
    for field in fields:
        if _norm(row.get(field)):
            return _norm(row.get(field))
    return ""


def _transition_rows(model: dict[str, Any]) -> list[dict[str, Any]]:
    result = _rows(model.get("state_transitions"))
    for lifecycle in _rows(model.get("lifecycles")):
        for transition in _rows(lifecycle.get("transitions")):
            item = dict(transition)
            item.setdefault("object_ref", lifecycle.get("object_ref") or lifecycle.get("object_id"))
            item.setdefault("lifecycle_id", lifecycle.get("lifecycle_id"))
            result.append(item)
    return result


def _align_transition(gt: dict[str, Any], candidates: list[dict[str, Any]], index: dict[str, set[str]]) -> dict[str, Any]:
    expected_object = {_norm(gt.get("object_ref"))}
    expected_from = _norm(gt.get("from_state"))
    expected_to = _norm(gt.get("to_state"))
    exact = [
        row for row in candidates
        if expected_object & _resolve(row.get("object_ref"), index)
        and _state(row, "from") == expected_from and _state(row, "to") == expected_to
    ]
    if len(exact) == 1:
        return _alignment(gt, "state_transitions", "EXACT_MATCH", exact[0])
    partial = [
        row for row in candidates
        if expected_object & _resolve(row.get("object_ref"), index)
        and (_state(row, "from") == expected_from or _state(row, "to") == expected_to)
    ]
    if partial:
        return _alignment(
            gt, "state_transitions", "PARTIAL_MATCH",
            partial[0] if len(partial) == 1 else None,
        )
    return _alignment(gt, "state_transitions", "MISSING")


def _condition_signature(value: Any) -> set[str]:
    result: set[str] = set()
    for row in _rows(value):
        candidate = row.get("value") if "value" in row else row.get("value_candidate")
        if isinstance(candidate, dict):
            candidate = candidate.get("normalized_value", candidate.get("raw"))
        result.add(json.dumps([
            _norm(row.get("field") or row.get("field_candidate")),
            _norm(row.get("operator") or row.get("operator_candidate")),
            _norm(candidate),
        ], ensure_ascii=False))
    return result


def _state_effect_signature(value: Any) -> set[str]:
    return {
        json.dumps([_state(row, "from"), _state(row, "to")], ensure_ascii=False)
        for row in _rows(value)
    }


def _align_behavior(
    gt: dict[str, Any],
    candidates: list[dict[str, Any]],
    object_index: dict[str, set[str]],
    actor_index: dict[str, set[str]],
    collection: str,
) -> dict[str, Any]:
    expected_action = {_norm(gt.get("operation"))}
    expected_objects = {_norm(value) for value in _values(gt.get("object_refs"))}
    expected_actors = {_norm(value) for value in _values(gt.get("actor_refs"))}
    action_matches = [row for row in candidates if expected_action & _operation_names(row)]
    object_matches = [row for row in action_matches if expected_objects & _object_names(row, object_index)]
    if not action_matches:
        return _alignment(gt, collection, "MISSING")
    if not object_matches:
        return _alignment(
            gt, collection, "WRONG_BINDING",
            action_matches[0] if len(action_matches) == 1 else None,
            {"slot": "object_refs"},
        )
    expected_permission = _norm(gt.get("permission_decision"))
    expected_conditions = _condition_signature(gt.get("preconditions"))
    expected_effects = _state_effect_signature(gt.get("state_effects"))
    exact: list[dict[str, Any]] = []
    partial: list[tuple[dict[str, Any], list[str]]] = []
    for row in object_matches:
        missing: list[str] = []
        if expected_actors and not expected_actors & _actor_names(row, actor_index):
            missing.append("actor_refs")
        if expected_permission and _norm(row.get("permission_decision")) != expected_permission:
            missing.append("permission_decision")
        if expected_conditions and not expected_conditions.issubset(
            _condition_signature(row.get("preconditions") or row.get("conditions"))
        ):
            missing.append("preconditions")
        if expected_effects and not expected_effects.issubset(
            _state_effect_signature(row.get("state_effects"))
        ):
            missing.append("state_effects")
        if missing:
            partial.append((row, missing))
        else:
            exact.append(row)
    if len(exact) == 1:
        candidate = exact[0]
        confirmed = _text(candidate.get("status")).upper() in {"", "CONFIRMED", "ACCEPTED"}
        return _alignment(
            gt, collection, "EXACT_MATCH" if confirmed else "PARTIAL_MATCH", candidate,
            {"candidate_not_confirmed": not confirmed},
        )
    if len(exact) > 1:
        return _alignment(
            gt, collection, "CONFLICTED",
            details={"candidate_ids": [_candidate_id(row) for row in exact]},
        )
    candidate, missing = partial[0]
    return _alignment(
        gt, collection, "PARTIAL_MATCH", candidate if len(partial) == 1 else None,
        {"missing_or_wrong_slots": missing},
    )


def _align_expected_unknown(gt: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    expected = _names(gt, "reason_code", "kind", "canonical_name", "aliases")
    matches = [
        row for row in candidates
        if expected & _names(row, "reason_code", "kind", "question", "message")
    ]
    if matches:
        return _alignment(
            gt, "expected_unknowns", "UNKNOWN_CORRECTLY_EXPOSED",
            matches[0] if len(matches) == 1 else None,
        )
    return _alignment(gt, "expected_unknowns", "MISSING")


def align_enterprise_understanding(ground_truth: dict[str, Any], asset: dict[str, Any]) -> dict[str, Any]:
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
    object_index = _entity_index(objects, ("object_id", "entity_id"))
    actor_index = _entity_index(actors, ("actor_id", "role_id"))
    alignments: list[dict[str, Any]] = []
    for row in _rows(ground_truth.get("business_objects")):
        alignments.append(_best_name(row, objects, "business_objects"))
    for row in _rows(ground_truth.get("actors")):
        alignments.append(_best_name(row, actors, "actors"))
    for row in _rows(ground_truth.get("operations")):
        alignments.append(_align_operation(row, operations, object_index))
    for row in _rows(ground_truth.get("object_relations")):
        alignments.append(_align_relation(row, relations, object_index))
    for row in _rows(ground_truth.get("state_transitions")):
        alignments.append(_align_transition(row, transitions, object_index))
    for row in _rows(ground_truth.get("business_rules")):
        alignments.append(_align_behavior(row, rules, object_index, actor_index, "business_rules"))
    for row in _rows(ground_truth.get("business_behaviors")):
        alignments.append(_align_behavior(row, behaviors, object_index, actor_index, "business_behaviors"))
    for row in _rows(ground_truth.get("conflicts")):
        alignments.append(_best_name(row, conflicts, "conflicts"))
    for row in _rows(ground_truth.get("expected_unknowns")):
        alignments.append(_align_expected_unknown(row, unknowns))

    matched_candidate_ids = {
        _text(row.get("candidate_id"))
        for row in alignments
        if _text(row.get("candidate_id")) and row.get("alignment_status") in COVERED_STATUSES
    }
    formal_collections = {
        "business_objects": objects,
        "actors": actors,
        "operations": operations,
        "object_relations": relations,
        "business_rules": rules,
        "business_behaviors": behaviors,
    }
    unmatched_confirmed: list[dict[str, Any]] = []
    for collection, candidates in formal_collections.items():
        for row in candidates:
            identity = _candidate_id(row)
            if identity and identity not in matched_candidate_ids and _text(row.get("status")).upper() in {
                "CONFIRMED", "ACCEPTED", "PASS"
            }:
                unmatched_confirmed.append({
                    "candidate_id": identity,
                    "candidate_collection": collection,
                    "candidate_status": _text(row.get("status")),
                    "candidate_evidence": _evidence(row),
                })

    expected_unknown_candidate_ids = {
        _text(row.get("candidate_id"))
        for row in alignments
        if row.get("collection") == "expected_unknowns" and _text(row.get("candidate_id"))
    }
    unexpected_unknowns = [
        {
            "candidate_id": _candidate_id(row),
            "candidate_collection": "unknowns",
            "candidate_status": _text(row.get("status")),
            "candidate_evidence": _evidence(row),
            "alignment_status": "UNKNOWN_SHOULD_HAVE_BEEN_RESOLVED",
            "reason_code": row.get("reason_code") or row.get("kind"),
        }
        for row in unknowns
        if _candidate_id(row) and _candidate_id(row) not in expected_unknown_candidate_ids
    ]
    return {
        "schema": ALIGNMENT_SCHEMA,
        "project_id": ground_truth.get("project_id"),
        "ground_truth_validation_status": (
            ground_truth.get("validation_receipt", {}).get("status")
            if isinstance(ground_truth.get("validation_receipt"), dict) else "UNKNOWN"
        ),
        "alignments": alignments,
        "unmatched_confirmed_candidates": unmatched_confirmed,
        "unexpected_unknowns": unexpected_unknowns,
        "alignment_authority": "DETERMINISTIC_EXACT_ALIAS_AND_SLOT_MATCHING",
        "fuzzy_or_llm_match_can_confirm": False,
        "model_writeback_allowed": False,
    }


__all__ = [
    "ALIGNMENT_SCHEMA", "EXACT_STATUSES", "COVERED_STATUSES", "align_enterprise_understanding"
]
