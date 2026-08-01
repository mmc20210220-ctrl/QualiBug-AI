"""Shared primitives for the reference-only business world model."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from .schema import as_list, stable_id, text, unique_text

WORLD_STATES = {"CONFIRMED", "SUSPECTED", "CONFLICTED", "UNKNOWN"}

def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _entity_id(row: dict[str, Any]) -> str:
    return text(row.get("entity_id") or row.get("object_id"))


def _canonical_label(row: dict[str, Any]) -> str:
    return text(row.get("canonical_label") or row.get("name") or row.get("object"))


def _evidence_ref(row: dict[str, Any]) -> str:
    return stable_id(
        "business_world_evidence",
        row.get("source_id"),
        row.get("source_locator"),
        row.get("quote_hash"),
        row.get("fact_id"),
        row.get("asset_ref"),
        row.get("derivation"),
    )


def _evidence_refs(rows: Iterable[dict[str, Any]]) -> list[str]:
    return unique_text(_evidence_ref(row) for row in rows if isinstance(row, dict))


def _label_entity_index(model: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for row in _rows(model.get("business_objects")):
        entity_id = _entity_id(row)
        for value in [
            _canonical_label(row),
            row.get("name"),
            row.get("object"),
            *as_list(row.get("aliases")),
        ]:
            label = text(value)
            if entity_id and label:
                result[label].add(entity_id)
    return dict(result)


def _label_to_entity(model: dict[str, Any]) -> dict[str, str]:
    return {
        label: next(iter(entity_ids))
        for label, entity_ids in _label_entity_index(model).items()
        if len(entity_ids) == 1
    }


def _resolved_entity_refs(
    row: dict[str, Any],
    label_to_entity: dict[str, str],
    *,
    stable_key: str = "business_entity_refs",
    label_key: str = "object_refs",
) -> list[str]:
    stable = unique_text(as_list(row.get(stable_key)))
    if stable:
        return stable
    return unique_text(
        label_to_entity.get(text(value))
        for value in as_list(row.get(label_key))
        if label_to_entity.get(text(value))
    )


def _unresolved_object_conflict_entities(model: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for conflict in _rows(model.get("conflicts")):
        if text(conflict.get("status")) in {"RESOLVED", "SUPERSEDED", "DISMISSED"}:
            continue
        for key in (
            "candidate_entity_ids",
            "entity_ids",
            "related_entity_ids",
            "object_refs",
            "related_object_refs",
        ):
            result.update(text(value) for value in as_list(conflict.get(key)) if text(value))
        for participant in _rows(conflict.get("object_declaration_participants")):
            result.add(text(participant.get("entity_id")))
            result.add(text(participant.get("canonical_label")))
    for conflict in _rows(model.get("source_authority_conflicts")):
        if text(conflict.get("status")) == "RESOLVED":
            continue
        for participant in _rows(conflict.get("object_declaration_participants")):
            result.add(text(participant.get("entity_id")))
            result.add(text(participant.get("canonical_label")))
    return {value for value in result if value}


def _object_state(row: dict[str, Any], conflicted: set[str]) -> str:
    entity_id = _entity_id(row)
    label = _canonical_label(row)
    if entity_id in conflicted or label in conflicted:
        return "CONFLICTED"
    identity_status = text(row.get("identity_resolution_status")).upper()
    source_status = text(row.get("status")).upper()
    if identity_status in {"CONFLICTED", "AMBIGUOUS"} or source_status == "CONFLICTED":
        return "CONFLICTED"
    if entity_id and label and source_status in {"", "UNDERSTOOD", "CONFIRMED", "ACCEPTED"}:
        return "CONFIRMED"
    return "UNKNOWN"


def _behavior_state(row: dict[str, Any]) -> str:
    status = text(row.get("status")).upper()
    if status == "CONFIRMED":
        return "CONFIRMED"
    if status in {"CONFLICTED", "AMBIGUOUS", "BLOCKED"}:
        return "CONFLICTED"
    if status in {"CANDIDATE", "CANDIDATE_ONLY", "INCOMPLETE", "PARTIAL", "PENDING"}:
        return "SUSPECTED"
    return "UNKNOWN"



__all__ = [
    "WORLD_STATES",
    "_behavior_state",
    "_canonical_label",
    "_entity_id",
    "_evidence_ref",
    "_evidence_refs",
    "_label_entity_index",
    "_label_to_entity",
    "_object_state",
    "_resolved_entity_refs",
    "_rows",
    "_unresolved_object_conflict_entities",
]
