"""Negative-evidence governance for existing structural identity candidates.

This module reuses the existing exact structural profile builder. It adds source
independence and complete-lifecycle contradiction vetoes without changing identity
clusters or automatically unioning entities.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from itertools import combinations
from typing import Any

from . import identity_structural_evidence as _base
from .schema import as_dict, as_list, dedupe_evidence, stable_id, text, unique_text


def _source_ids(profile: dict[str, Any]) -> set[str]:
    return {
        text(row.get("source_id"))
        for key in (
            "object_evidence",
            "operation_evidence",
            "lifecycle_evidence",
            "relation_evidence",
        )
        for row in as_list(profile.get(key))
        if isinstance(row, dict) and text(row.get("source_id"))
    }


def _complete_lifecycle(profile: dict[str, Any]) -> bool:
    return (
        len(profile["lifecycle_states"]) >= _base._MIN_LIFECYCLE_STATES
        and len(profile["lifecycle_transitions"]) >= _base._MIN_LIFECYCLE_TRANSITIONS
    )


def _contradictions(left: dict[str, Any], right: dict[str, Any]) -> dict[str, list[str]]:
    if not (_complete_lifecycle(left) and _complete_lifecycle(right)):
        return {}
    left_states, right_states = set(left["lifecycle_states"]), set(right["lifecycle_states"])
    left_edges, right_edges = set(left["lifecycle_transitions"]), set(right["lifecycle_transitions"])
    if left_states == right_states and left_edges == right_edges:
        return {}
    return {
        "CONTRADICTORY_COMPLETE_LIFECYCLE": sorted(
            {
                *[f"LEFT_STATE|{value}" for value in left_states - right_states],
                *[f"RIGHT_STATE|{value}" for value in right_states - left_states],
                *[f"LEFT_TRANSITION|{value}" for value in left_edges - right_edges],
                *[f"RIGHT_TRANSITION|{value}" for value in right_edges - left_edges],
            }
        )
    }


def _independent(left: dict[str, Any], right: dict[str, Any]) -> tuple[bool, dict[str, list[str]]]:
    left_ids, right_ids = _source_ids(left), _source_ids(right)
    return bool(left_ids and right_ids and left_ids.isdisjoint(right_ids)), {
        left["entity_id"]: sorted(left_ids),
        right["entity_id"]: sorted(right_ids),
    }


def _candidate(
    left: dict[str, Any],
    right: dict[str, Any],
    dimensions: dict[str, list[str]],
    source_ids: dict[str, list[str]],
) -> dict[str, Any]:
    row = _base._candidate(left, right, dimensions)
    row["independent_source_ids"] = source_ids
    row["source_independence_verified"] = True
    row["contradictory_dimensions"] = []
    return row


def _suppressed(
    left: dict[str, Any],
    right: dict[str, Any],
    dimensions: dict[str, list[str]],
    source_ids: dict[str, list[str]],
    contradictions: dict[str, list[str]],
    reason_code: str,
) -> dict[str, Any]:
    entity_ids = sorted([left["entity_id"], right["entity_id"]])
    evidence = _base._candidate_evidence(left, right, dimensions)
    if contradictions:
        evidence = dedupe_evidence([
            *evidence,
            *left["lifecycle_evidence"],
            *right["lifecycle_evidence"],
        ])
    return {
        "assessment_id": stable_id(
            "enterprise_identity_structural_suppressed_pair",
            entity_ids,
            reason_code,
            dimensions,
            contradictions,
            source_ids,
        ),
        "candidate_entity_ids": entity_ids,
        "canonical_labels": {
            left["entity_id"]: left["canonical_label"],
            right["entity_id"]: right["canonical_label"],
        },
        "status": "SUPPRESSED",
        "reason_code": reason_code,
        "matched_operation_names": dimensions["operations"],
        "matched_lifecycle_states": dimensions["lifecycle_states"],
        "matched_lifecycle_transitions": dimensions["lifecycle_transitions"],
        "matched_relation_context": dimensions["relation_context"],
        "contradictory_dimensions": sorted(contradictions),
        "contradiction_details": contradictions,
        "source_ids": source_ids,
        "automatic_resolution_allowed": False,
        "automatic_entity_union_allowed": False,
        "evidence": evidence,
    }


def project_distinctness_structural_candidates(
    asset: dict[str, Any],
    model: dict[str, Any],
    resolution: dict[str, Any],
) -> dict[str, Any]:
    profiles = _base._profiles(model)
    candidates: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for left_id, right_id in combinations(sorted(profiles), 2):
        left, right = profiles[left_id], profiles[right_id]
        if _base._norm(left["canonical_label"]) == _base._norm(right["canonical_label"]):
            continue
        dimensions = _base._matched_dimensions(left, right)
        contradiction = _contradictions(left, right)
        independent, source_ids = _independent(left, right)
        meaningful_overlap = (
            len(dimensions["operations"]) >= _base._MIN_OPERATION_SET
            or bool(dimensions["relation_context"])
        )
        if contradiction and meaningful_overlap:
            suppressed.append(_suppressed(
                left,
                right,
                dimensions,
                source_ids,
                contradiction,
                "STRUCTURAL_MATCH_REJECTED_BY_COMPLETE_LIFECYCLE_CONTRADICTION",
            ))
            continue
        if not _base._candidate_allowed(dimensions):
            continue
        if not independent:
            suppressed.append(_suppressed(
                left,
                right,
                dimensions,
                source_ids,
                {},
                "STRUCTURAL_MATCH_NOT_SOURCE_INDEPENDENT",
            ))
            continue
        candidates.append(_candidate(left, right, dimensions, source_ids))

    candidates.sort(key=lambda row: (
        text(row.get("strength")),
        tuple(as_list(row.get("candidate_entity_ids"))),
        text(row.get("candidate_id")),
    ))
    suppressed.sort(key=lambda row: (
        text(row.get("reason_code")),
        tuple(as_list(row.get("candidate_entity_ids"))),
        text(row.get("assessment_id")),
    ))
    receipt = {
        "schema": _base.IDENTITY_STRUCTURAL_EVIDENCE_SCHEMA,
        "receipt_id": stable_id(
            "enterprise_identity_structural_evidence",
            [row.get("candidate_id") for row in candidates],
            [row.get("assessment_id") for row in suppressed],
        ),
        "entity_profile_count": len(profiles),
        "candidate_count": len(candidates),
        "strong_candidate_count": sum(
            1 for row in candidates
            if text(row.get("strength")) == "STRONG_STRUCTURAL_CANDIDATE"
        ),
        "candidate_pairs": candidates,
        "suppressed_pair_count": len(suppressed),
        "suppressed_lifecycle_contradiction_count": sum(
            1 for row in suppressed
            if text(row.get("reason_code"))
            == "STRUCTURAL_MATCH_REJECTED_BY_COMPLETE_LIFECYCLE_CONTRADICTION"
        ),
        "suppressed_not_source_independent_count": sum(
            1 for row in suppressed
            if text(row.get("reason_code")) == "STRUCTURAL_MATCH_NOT_SOURCE_INDEPENDENT"
        ),
        "suppressed_pairs": suppressed,
        "candidate_dimensions": [
            "EXACT_OPERATION_SET",
            "EXACT_LIFECYCLE_TOPOLOGY",
            "SHARED_RELATION_NEIGHBORHOOD",
        ],
        "source_backed_exact_structure_only": True,
        "independent_source_evidence_required": True,
        "complete_lifecycle_contradiction_veto": True,
        "post_resolution_candidate_layer": True,
        "changes_identity_resolution": False,
        "automatic_similarity_merge_allowed": False,
        "automatic_entity_union_allowed": False,
    }

    refs: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        candidate_id = text(candidate.get("candidate_id"))
        for entity_id in as_list(candidate.get("candidate_entity_ids")):
            refs[text(entity_id)].append(candidate_id)
    for obj in as_list(model.get("business_objects")):
        if not isinstance(obj, dict):
            continue
        entity_id = text(obj.get("entity_id") or obj.get("object_id"))
        obj["structural_identity_candidate_refs"] = unique_text(refs.get(entity_id, []))

    model["identity_structural_evidence"] = receipt
    model["identity_structural_candidates"] = candidates
    metrics = dict(as_dict(model.get("metrics")))
    metrics.update({
        "enterprise_identity_structural_profile_count": len(profiles),
        "enterprise_identity_structural_candidate_count": len(candidates),
        "enterprise_identity_strong_structural_candidate_count": receipt["strong_candidate_count"],
        "enterprise_identity_structural_suppressed_pair_count": len(suppressed),
        "enterprise_identity_structural_lifecycle_contradiction_count": receipt[
            "suppressed_lifecycle_contradiction_count"
        ],
    })
    model["metrics"] = metrics
    resolution["identity_structural_evidence"] = deepcopy(receipt)
    asset["enterprise_identity_structural_evidence"] = deepcopy(receipt)
    return model


__all__ = ["project_distinctness_structural_candidates"]
