"""Conflict-governed wrapper around the existing source declaration authority."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from ._object_role_evidence import comparison_key
from ._object_source_conflicts import business_object_source_conflicts, object_declaration_fact_id
from ._object_source_preparation import (
    finalize_source_declared_recognition as _finalize_source_declared_recognition,
    prepare_source_declared_asset as _prepare_source_declared_asset,
)
from .schema import as_dict, as_list, new_unknown, stable_id, text, unique_text


def _conflict_participants(conflicts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        text(row.get("fact_id")): dict(row)
        for conflict in conflicts
        for row in as_list(conflict.get("object_declaration_participants"))
        if isinstance(row, dict) and text(row.get("fact_id"))
    }


def _authority_sets(conflicts: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    blocked: set[str] = set()
    selected: set[str] = set()
    for conflict in conflicts:
        participants = {
            text(row.get("fact_id"))
            for row in as_list(conflict.get("object_declaration_participants"))
            if isinstance(row, dict) and text(row.get("fact_id"))
        }
        decision = as_dict(conflict.get("authority_decision"))
        winner = text(decision.get("selected_fact_id"))
        if text(conflict.get("status")) == "RESOLVED" and winner in participants:
            selected.add(winner)
            blocked.update(participants - {winner})
        else:
            blocked.update(participants)
    return blocked, selected


def _object_participant_fact_ids(
    row: dict[str, Any], participants: dict[str, dict[str, Any]]
) -> set[str]:
    canonical = comparison_key(row.get("object") or row.get("name"))
    evidence_sources = {
        text(item.get("source_id"))
        for item in as_list(row.get("evidence"))
        if isinstance(item, dict) and text(item.get("source_id"))
    }
    return {
        fact_id
        for fact_id, participant in participants.items()
        if comparison_key(participant.get("canonical_label")) == canonical
        and text(participant.get("source_id")) in evidence_sources
    }


def _filter_slot(slot: dict[str, Any], allowed_keys: set[str]) -> dict[str, Any]:
    copied = deepcopy(slot)
    mentions = unique_text(
        [*as_list(slot.get("entity_refs")), *as_list(slot.get("entity_mentions"))]
    )
    allowed = [value for value in mentions if comparison_key(value) in allowed_keys]
    rejected = [value for value in mentions if comparison_key(value) not in allowed_keys]
    copied["raw_entity_mentions"] = unique_text(
        [*as_list(slot.get("raw_entity_mentions")), *mentions]
    )
    copied["entity_refs"] = allowed
    copied["entity_mentions"] = allowed
    copied["business_object_rejected_mentions"] = unique_text(
        [*as_list(slot.get("business_object_rejected_mentions")), *rejected]
    )
    return copied


def _filter_fact_slots(asset: dict[str, Any], allowed_keys: set[str]) -> None:
    ledger = dict(as_dict(asset.get("business_fact_ledger")))
    items: list[dict[str, Any]] = []
    for raw in as_list(ledger.get("items")):
        if not isinstance(raw, dict):
            continue
        fact = deepcopy(raw)
        if text(fact.get("kind")) in {"RULE", "STATE_TRANSITION"}:
            for side in ("subject", "object"):
                fact[side] = _filter_slot(as_dict(fact.get(side)), allowed_keys)
        items.append(fact)
    if ledger:
        ledger["items"] = items
        asset["business_fact_ledger"] = ledger


def prepare_conflict_governed_source_asset(
    asset: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prepare declarations, then remove unresolved/operator-superseded participants."""

    prepared, authority = _prepare_source_declared_asset(asset)
    conflicts = business_object_source_conflicts(asset)
    if not conflicts:
        authority = dict(authority)
        authority["structured_source_declaration_present"] = bool(
            authority.get("declared_labels")
        )
        authority["source_authority_conflicts"] = []
        return prepared, authority

    result = deepcopy(prepared)
    authority = deepcopy(authority)
    participants = _conflict_participants(conflicts)
    blocked, selected = _authority_sets(conflicts)
    retained_objects: list[dict[str, Any]] = []
    for raw in as_list(result.get("business_objects")):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        fact_ids = _object_participant_fact_ids(row, participants)
        if fact_ids & blocked:
            continue
        if fact_ids & selected:
            row["authority_resolution"] = "OPERATOR_SELECTED"
        retained_objects.append(row)
    result["business_objects"] = retained_objects

    allowed_keys = {
        comparison_key(value)
        for row in retained_objects
        for value in [
            row.get("object") or row.get("name"),
            *as_list(row.get("aliases")),
        ]
        if comparison_key(value)
    }
    authority["declared_labels"] = {
        key: value
        for key, value in as_dict(authority.get("declared_labels")).items()
        if comparison_key(key) in allowed_keys
    }
    authority["declaration_surface_modes"] = {
        key: value
        for key, value in as_dict(authority.get("declaration_surface_modes")).items()
        if comparison_key(key) in allowed_keys
    }
    authority["surface_parents"] = {
        key: values
        for key, values in as_dict(authority.get("surface_parents")).items()
        if comparison_key(key) in allowed_keys
    }
    authority["structured_source_declaration_present"] = True
    authority["source_authority_conflicts"] = conflicts
    _filter_fact_slots(result, allowed_keys)
    return result, authority


def finalize_conflict_governed_source_recognition(
    recognition: dict[str, Any], authority: dict[str, Any]
) -> dict[str, Any]:
    result = _finalize_source_declared_recognition(recognition, authority)
    source_conflicts = [
        dict(row)
        for row in as_list(authority.get("source_authority_conflicts"))
        if isinstance(row, dict)
    ]
    if not source_conflicts:
        return result

    unresolved = [row for row in source_conflicts if text(row.get("status")) != "RESOLVED"]
    source_unknowns = []
    for conflict in unresolved:
        participant_labels = unique_text(
            label
            for row in as_list(conflict.get("object_declaration_participants"))
            if isinstance(row, dict)
            for label in [row.get("canonical_label"), *as_list(row.get("labels"))]
        )
        source_unknowns.append(
            new_unknown(
                "BUSINESS_OBJECT_SOURCE_AUTHORITY_CONFLICT",
                (
                    f"业务对象名称“{text(conflict.get('entity'))}”在多个独立来源中"
                    "指向不同正式对象；未获得显式来源权威裁决。"
                ),
                related_objects=participant_labels,
                evidence=as_list(conflict.get("evidence")),
                severity="P0",
                blocks_formal_understanding=True,
                reason_code="BUSINESS_OBJECT_DECLARATION_ALIAS_CONFLICT",
                details={
                    "conflict_id": conflict.get("conflict_id"),
                    "participant_fact_ids": [
                        row.get("fact_id")
                        for row in as_list(conflict.get("object_declaration_participants"))
                        if isinstance(row, dict)
                    ],
                    "automatic_winner_selected": False,
                    "required_authority_action": "SELECT_FACT or LEAVE_UNRESOLVED",
                    "disallowed_authority_signals": [
                        "recency",
                        "filename",
                        "document_order",
                        "model_confidence",
                        "industry_default",
                    ],
                },
            )
        )

    gate = dict(as_dict(result.get("gate")))
    metrics = dict(as_dict(gate.get("metrics")))
    if source_unknowns:
        gate["status"] = "BLOCKED_BUSINESS_OBJECT_SOURCE_AUTHORITY_CONFLICT"
        gate["entry_allowed"] = False
        gate["identity_resolution_allowed"] = False
        gate["critical_conflicts"] = [
            *as_list(gate.get("critical_conflicts")),
            *source_unknowns,
        ]
        gate["unknowns"] = [*as_list(gate.get("unknowns")), *source_unknowns]
        gate["required_operator_action"] = (
            "resolve each business-object source declaration conflict through the "
            "existing operator SELECT_FACT / LEAVE_UNRESOLVED authority ledger"
        )
    metrics["source_authority_conflict_count"] = len(source_conflicts)
    metrics["unresolved_source_authority_conflict_count"] = len(unresolved)
    metrics["resolved_source_authority_conflict_count"] = len(source_conflicts) - len(unresolved)
    gate["metrics"] = metrics
    result["source_authority_conflicts"] = source_conflicts
    result["gate"] = gate
    result["recognition_id"] = stable_id(
        "business_object_recognition",
        result.get("recognition_id"),
        [
            (row.get("conflict_id"), row.get("status"))
            for row in source_conflicts
        ],
    )
    return result


__all__ = [
    "finalize_conflict_governed_source_recognition",
    "prepare_conflict_governed_source_asset",
]
