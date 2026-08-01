"""Fail-closed admission for multiple structural identity review decisions.

The durable decisions still live in the existing operator authority ledger. This
module only determines which current confirmations may be applied together and
preserves every explicit stable-id retirement in the existing identity registry.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from .identity_structural_review import (
    ACTION_CONFIRM_ALIAS,
    DECISION_KIND,
    identity_structural_candidate_fingerprint,
)
from .schema import as_dict, as_list, stable_id, text, unique_text

ADMISSION_SCHEMA = "qualibug.enterprise-identity-structural-review-admission.v1"


def _candidates(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        text(row.get("candidate_id")): row
        for row in as_list(model.get("identity_structural_candidates"))
        if isinstance(row, dict) and text(row.get("candidate_id"))
    }


def _latest_decisions(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for raw in as_list(asset.get("identity_structural_review_decisions")):
        if not isinstance(raw, dict):
            continue
        if text(raw.get("decision_kind")) != DECISION_KIND:
            continue
        candidate_id = text(raw.get("candidate_id") or raw.get("conflict_id"))
        if candidate_id:
            latest[candidate_id] = dict(raw)
    return latest


def _current_confirmation(
    decision: dict[str, Any], candidate: dict[str, Any]
) -> bool:
    if text(decision.get("action")) != ACTION_CONFIRM_ALIAS:
        return False
    entity_ids = sorted(unique_text(as_list(candidate.get("candidate_entity_ids"))))
    recorded = sorted(unique_text(as_list(decision.get("participant_entity_ids"))))
    return (
        len(entity_ids) == 2
        and recorded == entity_ids
        and text(decision.get("candidate_fingerprint"))
        == identity_structural_candidate_fingerprint(candidate)
        and text(decision.get("canonical_entity_id")) in entity_ids
    )


def govern_identity_structural_review_decision_admission(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    """Block overlapping current confirmations before any alias fact is generated."""
    candidates = _candidates(model)
    latest = _latest_decisions(asset)
    current_confirmations: dict[str, dict[str, Any]] = {}
    entity_to_decisions: dict[str, list[str]] = defaultdict(list)
    for candidate_id, decision in latest.items():
        candidate = as_dict(candidates.get(candidate_id))
        if not candidate or not _current_confirmation(decision, candidate):
            continue
        current_confirmations[candidate_id] = decision
        for entity_id in unique_text(as_list(candidate.get("candidate_entity_ids"))):
            entity_to_decisions[entity_id].append(text(decision.get("decision_id")))

    overlapping_entities = {
        entity_id: sorted(unique_text(decision_ids))
        for entity_id, decision_ids in entity_to_decisions.items()
        if len(unique_text(decision_ids)) > 1
    }
    blocked_decision_ids = {
        decision_id
        for decision_ids in overlapping_entities.values()
        for decision_id in decision_ids
    }
    original = [
        dict(row)
        for row in as_list(asset.get("identity_structural_review_decisions"))
        if isinstance(row, dict)
    ]
    effective = [
        row
        for row in original
        if text(row.get("decision_id")) not in blocked_decision_ids
    ]
    asset["identity_structural_review_decisions"] = effective

    conflicts = [
        {
            "conflict_id": stable_id(
                "enterprise_identity_structural_review_conflict",
                "OVERLAPPING_CONFIRMATIONS",
                entity_id,
                decision_ids,
            ),
            "kind": "IDENTITY_STRUCTURAL_REVIEW_OVERLAPPING_CONFIRMATIONS",
            "reason_code": "IDENTITY_STRUCTURAL_REVIEW_OVERLAPPING_CONFIRMATIONS",
            "status": "UNRESOLVED",
            "entity_id": entity_id,
            "decision_ids": decision_ids,
            "automatic_resolution_allowed": False,
            "blocks_current_identity": False,
            "blocks_review_application": True,
            "required_operator_action": (
                "reject or replace overlapping structural identity confirmations so "
                "each current entity participates in at most one merge decision"
            ),
        }
        for entity_id, decision_ids in sorted(overlapping_entities.items())
    ]
    receipt = {
        "schema": ADMISSION_SCHEMA,
        "admission_id": stable_id(
            "enterprise_identity_structural_review_admission",
            sorted(text(row.get("decision_id")) for row in original),
            sorted(blocked_decision_ids),
        ),
        "status": "BLOCKED_OVERLAPPING_CONFIRMATIONS" if conflicts else "PASS",
        "decision_count": len(original),
        "current_confirmation_count": len(current_confirmations),
        "admitted_decision_count": len(effective),
        "blocked_decision_ids": sorted(blocked_decision_ids),
        "overlapping_entity_ids": sorted(overlapping_entities),
        "conflicts": conflicts,
        "current_identity_gate_changed": False,
        "review_application_allowed": not conflicts,
        "automatic_conflict_winner_allowed": False,
        "uses_existing_operator_authority_ledger": True,
    }
    asset["enterprise_identity_structural_review_admission"] = deepcopy(receipt)
    model["identity_structural_review_admission"] = deepcopy(receipt)
    return model


def preserve_identity_structural_review_registry_merges(
    asset: dict[str, Any], review_receipt: dict[str, Any]
) -> dict[str, Any]:
    """Persist every disjoint operator-authorized retirement before canonical rebuild."""
    applied = [
        dict(row)
        for row in as_list(review_receipt.get("applied_confirmations"))
        if isinstance(row, dict)
        and text(row.get("decision_id"))
        and text(row.get("canonical_entity_id"))
        and text(row.get("retired_entity_id"))
    ]
    merges = [
        {
            "decision_id": text(row.get("decision_id")),
            "candidate_id": text(row.get("candidate_id")),
            "canonical_entity_id": text(row.get("canonical_entity_id")),
            "retired_entity_ids": [text(row.get("retired_entity_id"))],
            "automatic_merge": False,
        }
        for row in applied
    ]
    participants: dict[str, list[str]] = defaultdict(list)
    for merge in merges:
        decision_id = text(merge.get("decision_id"))
        participants[text(merge.get("canonical_entity_id"))].append(decision_id)
        for retired in as_list(merge.get("retired_entity_ids")):
            participants[text(retired)].append(decision_id)
    overlap = {
        entity_id: unique_text(decision_ids)
        for entity_id, decision_ids in participants.items()
        if len(unique_text(decision_ids)) > 1
    }
    if overlap:
        raise ValueError("identity_structural_review_overlap_bypassed_admission")

    registry = dict(as_dict(asset.get("enterprise_identity_registry")))
    existing = [
        dict(row)
        for row in as_list(registry.get("operator_authorized_merges"))
        if isinstance(row, dict) and text(row.get("decision_id"))
    ]
    by_decision = {
        text(row.get("decision_id")): row for row in [*existing, *merges]
    }
    ordered = [by_decision[key] for key in sorted(by_decision)]
    if ordered:
        registry["operator_authorized_merges"] = ordered
        registry["operator_authorized_merge"] = ordered[-1]
    asset["enterprise_identity_registry"] = registry
    review_receipt["operator_authorized_merges"] = deepcopy(ordered)
    review_receipt["operator_authorized_merge_count"] = len(ordered)
    return review_receipt


__all__ = [
    "ADMISSION_SCHEMA",
    "govern_identity_structural_review_decision_admission",
    "preserve_identity_structural_review_registry_merges",
]
