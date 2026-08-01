"""Explainable structural identity candidates from the finalized business model.

This is a post-resolution, read-only candidate layer. It never changes clusters,
bindings, registry authority, or execution admission. Only exact source-backed
lifecycle, operation, and relation-neighborhood evidence can create candidates.
"""
from __future__ import annotations

import re
from collections import defaultdict
from copy import deepcopy
from itertools import combinations
from typing import Any, Iterable

from .schema import as_dict, as_list, dedupe_evidence, stable_id, text, unique_text

IDENTITY_STRUCTURAL_EVIDENCE_SCHEMA = (
    "qualibug.enterprise-identity-structural-evidence.v1"
)
IDENTITY_STRUCTURAL_CANDIDATE_SCHEMA = (
    "qualibug.enterprise-identity-structural-candidate.v1"
)

_NORMALIZE_RE = re.compile(r"[^a-z0-9\u3400-\u4dbf\u4e00-\u9fff]+")
_MIN_OPERATION_SET = 2
_MIN_OPERATION_RELATION_SET = 3
_MIN_LIFECYCLE_TRANSITIONS = 2
_MIN_LIFECYCLE_STATES = 3


def _norm(value: Any) -> str:
    return _NORMALIZE_RE.sub("", text(value).casefold())


def _dict_rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _source_evidence(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in as_list(row.get("evidence"))
        if isinstance(item, dict)
    ]


def _operation_name(row: dict[str, Any]) -> str:
    action = as_dict(row.get("action"))
    return _norm(
        row.get("name")
        or row.get("operation")
        or action.get("canonical")
        or action.get("raw")
    )


def _lifecycle_signature(
    lifecycle: dict[str, Any],
) -> tuple[set[str], set[str], list[dict[str, Any]]]:
    states = {_norm(value) for value in as_list(lifecycle.get("states")) if _norm(value)}
    transitions: set[str] = set()
    evidence = _source_evidence(lifecycle)
    for transition in _dict_rows(lifecycle.get("transitions")):
        source = _norm(transition.get("from_state"))
        target = _norm(transition.get("to_state"))
        kind = text(transition.get("transition_kind") or "ALLOWED").upper()
        completeness = text(transition.get("completeness") or "COMPLETE").upper()
        if not source or not target or completeness != "COMPLETE":
            continue
        states.update({source, target})
        transitions.add(f"{source}>{target}|{kind}")
        evidence.extend(_source_evidence(transition))
    return states, transitions, evidence


def _profiles(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    for obj in _dict_rows(model.get("business_objects")):
        entity_id = text(obj.get("entity_id") or obj.get("object_id"))
        if not entity_id:
            continue
        profiles[entity_id] = {
            "entity_id": entity_id,
            "canonical_label": text(
                obj.get("canonical_label") or obj.get("name") or obj.get("object")
            ),
            "operation_names": set(),
            "operation_refs": [],
            "operation_evidence": [],
            "lifecycle_states": set(),
            "lifecycle_transitions": set(),
            "lifecycle_refs": [],
            "lifecycle_evidence": [],
            "incoming_relations": set(),
            "outgoing_relations": set(),
            "relation_refs": [],
            "relation_evidence": [],
            "object_evidence": _source_evidence(obj),
        }

    for operation in _dict_rows(model.get("operations")):
        name = _operation_name(operation)
        if not name:
            continue
        refs = unique_text(
            [
                *as_list(operation.get("business_entity_refs")),
                *as_list(operation.get("entity_refs")),
            ]
        )
        for entity_id in refs:
            profile = profiles.get(entity_id)
            if not profile:
                continue
            profile["operation_names"].add(name)
            profile["operation_refs"].append(text(operation.get("operation_id")))
            profile["operation_evidence"].extend(_source_evidence(operation))

    for lifecycle in _dict_rows(model.get("lifecycles")):
        entity_id = text(
            lifecycle.get("business_entity_ref") or lifecycle.get("entity_ref")
        )
        profile = profiles.get(entity_id)
        if not profile:
            continue
        states, transitions, evidence = _lifecycle_signature(lifecycle)
        profile["lifecycle_states"].update(states)
        profile["lifecycle_transitions"].update(transitions)
        profile["lifecycle_refs"].append(text(lifecycle.get("lifecycle_id")))
        profile["lifecycle_evidence"].extend(evidence)

    for relation in _dict_rows(model.get("object_relations")):
        source = text(relation.get("source_entity_ref"))
        target = text(relation.get("target_entity_ref"))
        relation_type = text(relation.get("relation_type")).upper()
        if not source or not target or not relation_type or source == target:
            continue
        relation_ref = text(relation.get("relation_id"))
        evidence = _source_evidence(relation)
        if source in profiles:
            profiles[source]["outgoing_relations"].add(
                f"OUT|{relation_type}|{target}"
            )
            profiles[source]["relation_refs"].append(relation_ref)
            profiles[source]["relation_evidence"].extend(evidence)
        if target in profiles:
            profiles[target]["incoming_relations"].add(
                f"IN|{source}|{relation_type}"
            )
            profiles[target]["relation_refs"].append(relation_ref)
            profiles[target]["relation_evidence"].extend(evidence)

    for profile in profiles.values():
        for key in ("operation_refs", "lifecycle_refs", "relation_refs"):
            profile[key] = unique_text(profile[key])
        for key in (
            "object_evidence",
            "operation_evidence",
            "lifecycle_evidence",
            "relation_evidence",
        ):
            profile[key] = dedupe_evidence(profile[key])
    return profiles


def _exact_set(
    left: Iterable[str], right: Iterable[str], minimum: int
) -> list[str]:
    left_set = {value for value in left if value}
    right_set = {value for value in right if value}
    if len(left_set) < minimum or left_set != right_set:
        return []
    return sorted(left_set)


def _matched_dimensions(
    left: dict[str, Any], right: dict[str, Any]
) -> dict[str, list[str]]:
    operations = _exact_set(
        left["operation_names"], right["operation_names"], _MIN_OPERATION_SET
    )
    states = _exact_set(
        left["lifecycle_states"], right["lifecycle_states"], _MIN_LIFECYCLE_STATES
    )
    transitions = _exact_set(
        left["lifecycle_transitions"],
        right["lifecycle_transitions"],
        _MIN_LIFECYCLE_TRANSITIONS,
    )
    lifecycle = transitions if states and transitions else []
    relation_context = sorted(
        (left["incoming_relations"] | left["outgoing_relations"])
        & (right["incoming_relations"] | right["outgoing_relations"])
    )
    return {
        "operations": operations,
        "lifecycle_states": states if lifecycle else [],
        "lifecycle_transitions": lifecycle,
        "relation_context": relation_context,
    }


def _candidate_allowed(dimensions: dict[str, list[str]]) -> bool:
    lifecycle = bool(dimensions["lifecycle_transitions"])
    operations = bool(dimensions["operations"])
    relations = bool(dimensions["relation_context"])
    if lifecycle and (operations or relations):
        return True
    return (
        operations
        and relations
        and len(dimensions["operations"]) >= _MIN_OPERATION_RELATION_SET
    )


def _candidate_evidence(
    left: dict[str, Any],
    right: dict[str, Any],
    dimensions: dict[str, list[str]],
) -> list[dict[str, Any]]:
    evidence = [*left["object_evidence"], *right["object_evidence"]]
    if dimensions["operations"]:
        evidence.extend(left["operation_evidence"])
        evidence.extend(right["operation_evidence"])
    if dimensions["lifecycle_transitions"]:
        evidence.extend(left["lifecycle_evidence"])
        evidence.extend(right["lifecycle_evidence"])
    if dimensions["relation_context"]:
        evidence.extend(left["relation_evidence"])
        evidence.extend(right["relation_evidence"])
    return dedupe_evidence(evidence)


def _candidate(
    left: dict[str, Any],
    right: dict[str, Any],
    dimensions: dict[str, list[str]],
) -> dict[str, Any]:
    entity_ids = sorted([left["entity_id"], right["entity_id"]])
    matched = [
        name
        for name, values in (
            ("EXACT_OPERATION_SET", dimensions["operations"]),
            ("EXACT_LIFECYCLE_TOPOLOGY", dimensions["lifecycle_transitions"]),
            ("SHARED_RELATION_NEIGHBORHOOD", dimensions["relation_context"]),
        )
        if values
    ]
    return {
        "schema": IDENTITY_STRUCTURAL_CANDIDATE_SCHEMA,
        "candidate_id": stable_id(
            "enterprise_identity_structural_candidate", entity_ids, dimensions
        ),
        "candidate_entity_ids": entity_ids,
        "canonical_labels": {
            left["entity_id"]: left["canonical_label"],
            right["entity_id"]: right["canonical_label"],
        },
        "status": "CANDIDATE_ONLY",
        "reason_code": "INDEPENDENT_STRUCTURAL_EVIDENCE_MATCH",
        "strength": (
            "STRONG_STRUCTURAL_CANDIDATE"
            if len(matched) == 3
            else "REVIEW_STRUCTURAL_CANDIDATE"
        ),
        "matched_dimensions": matched,
        "matched_operation_names": dimensions["operations"],
        "matched_lifecycle_states": dimensions["lifecycle_states"],
        "matched_lifecycle_transitions": dimensions["lifecycle_transitions"],
        "matched_relation_context": dimensions["relation_context"],
        "source_refs": {
            left["entity_id"]: unique_text(
                [
                    *left["operation_refs"],
                    *left["lifecycle_refs"],
                    *left["relation_refs"],
                ]
            ),
            right["entity_id"]: unique_text(
                [
                    *right["operation_refs"],
                    *right["lifecycle_refs"],
                    *right["relation_refs"],
                ]
            ),
        },
        "automatic_resolution_allowed": False,
        "automatic_entity_union_allowed": False,
        "requires_operator_review": True,
        "operator_action": (
            "confirm or reject the candidate through the existing identity "
            "authority / TERM_ALIAS workflow"
        ),
        "evidence": _candidate_evidence(left, right, dimensions),
    }


def project_identity_structural_candidates(
    asset: dict[str, Any],
    model: dict[str, Any],
    resolution: dict[str, Any],
) -> dict[str, Any]:
    """Project exact structural candidates without mutating identity authority."""
    profiles = _profiles(model)
    candidates: list[dict[str, Any]] = []
    for left_id, right_id in combinations(sorted(profiles), 2):
        left, right = profiles[left_id], profiles[right_id]
        if _norm(left["canonical_label"]) == _norm(right["canonical_label"]):
            continue
        dimensions = _matched_dimensions(left, right)
        if _candidate_allowed(dimensions):
            candidates.append(_candidate(left, right, dimensions))

    candidates.sort(
        key=lambda row: (
            text(row.get("strength")),
            tuple(as_list(row.get("candidate_entity_ids"))),
            text(row.get("candidate_id")),
        )
    )
    receipt = {
        "schema": IDENTITY_STRUCTURAL_EVIDENCE_SCHEMA,
        "receipt_id": stable_id(
            "enterprise_identity_structural_evidence",
            [row.get("candidate_id") for row in candidates],
        ),
        "entity_profile_count": len(profiles),
        "candidate_count": len(candidates),
        "strong_candidate_count": sum(
            1
            for row in candidates
            if text(row.get("strength")) == "STRONG_STRUCTURAL_CANDIDATE"
        ),
        "candidate_pairs": candidates,
        "candidate_dimensions": [
            "EXACT_OPERATION_SET",
            "EXACT_LIFECYCLE_TOPOLOGY",
            "SHARED_RELATION_NEIGHBORHOOD",
        ],
        "source_backed_exact_structure_only": True,
        "post_resolution_candidate_layer": True,
        "changes_identity_resolution": False,
        "automatic_similarity_merge_allowed": False,
        "automatic_entity_union_allowed": False,
    }

    candidate_refs: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        candidate_id = text(candidate.get("candidate_id"))
        for entity_id in as_list(candidate.get("candidate_entity_ids")):
            candidate_refs[text(entity_id)].append(candidate_id)
    for obj in as_list(model.get("business_objects")):
        if not isinstance(obj, dict):
            continue
        entity_id = text(obj.get("entity_id") or obj.get("object_id"))
        obj["structural_identity_candidate_refs"] = unique_text(
            candidate_refs.get(entity_id, [])
        )

    model["identity_structural_evidence"] = receipt
    model["identity_structural_candidates"] = candidates
    metrics = dict(as_dict(model.get("metrics")))
    metrics.update(
        {
            "enterprise_identity_structural_profile_count": len(profiles),
            "enterprise_identity_structural_candidate_count": len(candidates),
            "enterprise_identity_strong_structural_candidate_count": receipt[
                "strong_candidate_count"
            ],
        }
    )
    model["metrics"] = metrics

    resolution["identity_structural_evidence"] = deepcopy(receipt)
    asset["enterprise_identity_structural_evidence"] = deepcopy(receipt)
    return model


__all__ = [
    "IDENTITY_STRUCTURAL_CANDIDATE_SCHEMA",
    "IDENTITY_STRUCTURAL_EVIDENCE_SCHEMA",
    "project_identity_structural_candidates",
]
