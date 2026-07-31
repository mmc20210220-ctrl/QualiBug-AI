"""Fail-closed incremental governance for stable enterprise entity ids."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .identity_types import IDENTITY_REGISTRY_SCHEMA
from .schema import as_dict, as_list, stable_id, text, unique_text


def _reindex(result: dict[str, Any]) -> None:
    mentions = {
        text(row.get("mention_id")): row
        for row in as_list(result.get("mentions"))
        if isinstance(row, dict)
    }
    mention_to_entity: dict[str, str] = {}
    labels: dict[str, set[str]] = defaultdict(set)
    canonical: dict[str, str] = {}
    for cluster in as_list(result.get("clusters")):
        if not isinstance(cluster, dict) or not text(cluster.get("entity_id")):
            continue
        entity_id = text(cluster.get("entity_id"))
        canonical[entity_id] = text(cluster.get("canonical_label"))
        for mention_id in as_list(cluster.get("member_mention_ids")):
            mention_id = text(mention_id)
            mention_to_entity[mention_id] = entity_id
            label = text(as_dict(mentions.get(mention_id)).get("raw_label"))
            if label:
                labels[label].add(entity_id)
    result["mention_to_entity"] = mention_to_entity
    result["label_to_entity"] = {
        label: next(iter(ids)) for label, ids in labels.items() if len(ids) == 1
    }
    result["ambiguous_label_to_entities"] = {
        label: sorted(ids) for label, ids in labels.items() if len(ids) > 1
    }
    result["canonical_label_by_entity"] = canonical


def govern_identity_registry(
    prior_registry: dict[str, Any],
    result: dict[str, Any],
    *,
    asset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prior = {
        text(row.get("entity_id")): row
        for row in as_list(as_dict(prior_registry).get("entities"))
        if isinstance(row, dict) and text(row.get("entity_id"))
    }
    clusters = [
        row for row in as_list(result.get("clusters")) if isinstance(row, dict)
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cluster in clusters:
        grouped[text(cluster.get("entity_id"))].append(cluster)

    conflicts = [
        row for row in as_list(result.get("conflicts")) if isinstance(row, dict)
    ]
    split_ids: list[str] = []
    for prior_id, rows in grouped.items():
        if prior_id not in prior or len(rows) < 2:
            continue
        split_ids.append(prior_id)
        candidate_ids: list[str] = []
        for row in rows:
            labels = unique_text([row.get("canonical_label"), *as_list(row.get("labels"))])
            new_id = stable_id("enterprise_entity_split_candidate", prior_id, labels)
            candidate_ids.append(new_id)
            row.update(
                {
                    "prior_entity_id": prior_id,
                    "entity_id": new_id,
                    "status": "CONFLICTED",
                    "identity_registry_drift": "SPLIT_FROM_PRIOR_ENTITY",
                }
            )
        conflicts.append(
            {
                "conflict_id": stable_id(
                    "enterprise_identity_conflict",
                    "IDENTITY_REGISTRY_SPLIT_CONFLICT",
                    prior_id,
                    sorted(candidate_ids),
                ),
                "kind": "IDENTITY_REGISTRY_SPLIT_CONFLICT",
                "reason_code": "IDENTITY_REGISTRY_SPLIT_CONFLICT",
                "status": "UNRESOLVED",
                "prior_entity_id": prior_id,
                "candidate_entity_ids": sorted(candidate_ids),
                "blocks_formal_understanding": True,
                "automatic_resolution_allowed": False,
                "evidence": [
                    evidence
                    for row in rows
                    for evidence in as_list(row.get("evidence"))
                    if isinstance(evidence, dict)
                ],
            }
        )

    result["conflicts"] = conflicts
    _reindex(result)
    mention_to_entity = as_dict(result.get("mention_to_entity"))
    mentions = {
        text(row.get("mention_id")): row
        for row in as_list(result.get("mentions"))
        if isinstance(row, dict)
    }
    business_mention_by_artifact: dict[str, str] = {}
    for edge in as_list(result.get("edges")):
        if not isinstance(edge, dict) or text(edge.get("relation")) != "IMPLEMENTS_ENTITY":
            continue
        left_id = text(edge.get("left_mention_id"))
        right_id = text(edge.get("right_mention_id"))
        left = as_dict(mentions.get(left_id))
        right = as_dict(mentions.get(right_id))
        if text(left.get("mention_type")) == "TECHNICAL_ARTIFACT":
            technical, business_id = left, right_id
        else:
            technical, business_id = right, left_id
        artifact_ref = text(technical.get("artifact_ref"))
        if artifact_ref and business_id:
            business_mention_by_artifact[artifact_ref] = business_id
    for binding in as_list(result.get("bindings")):
        if not isinstance(binding, dict):
            continue
        mention_id = text(binding.get("business_mention_id"))
        if not mention_id:
            mention_id = business_mention_by_artifact.get(text(binding.get("artifact_ref")), "")
            if mention_id:
                binding["business_mention_id"] = mention_id
        if mention_id and text(mention_to_entity.get(mention_id)):
            binding["entity_id"] = mention_to_entity[mention_id]
        binding["binding_id"] = stable_id(
            "identity_binding",
            binding.get("entity_id"),
            binding.get("artifact_type"),
            binding.get("artifact_ref"),
            binding.get("relation"),
        )

    current_ids = {
        text(row.get("entity_id")) for row in clusters if text(row.get("entity_id"))
    }
    prior_ids = set(prior)
    registry = {
        "schema": IDENTITY_REGISTRY_SCHEMA,
        "entities": [
            {
                "entity_id": row.get("entity_id"),
                "entity_type": row.get("entity_type"),
                "canonical_label": row.get("canonical_label"),
                "aliases": row.get("aliases"),
                "labels": row.get("labels"),
                "status": row.get("status"),
                "prior_entity_id": row.get("prior_entity_id"),
                "evidence": row.get("evidence"),
            }
            for row in clusters
        ],
        "identity_is_name_independent": True,
        "automatic_similarity_merge_allowed": False,
    }
    receipt = {
        "schema": "qualibug.enterprise-identity-registry-recompute-receipt.v1",
        "prior_entity_count": len(prior_ids),
        "current_entity_count": len(current_ids),
        "reused_entity_ids": sorted(current_ids & prior_ids),
        "created_entity_ids": sorted(current_ids - prior_ids),
        "retired_entity_ids": sorted(prior_ids - current_ids),
        "split_conflict_prior_entity_ids": sorted(split_ids),
        "split_conflict_count": len(split_ids),
        "silent_split_identity_reuse_allowed": False,
    }
    result["registry"] = registry
    result["registry_recompute_receipt"] = receipt
    gate = dict(as_dict(result.get("gate")))
    if conflicts:
        gate.update(
            {
                "status": "BLOCKED_ENTERPRISE_IDENTITY_CONFLICT",
                "entry_allowed": False,
                "business_understanding_allowed": False,
                "critical_conflicts": conflicts,
            }
        )
    gate["metrics"] = {
        **as_dict(gate.get("metrics")),
        "registry_split_conflict_count": len(split_ids),
    }
    result["gate"] = gate
    if asset is not None:
        asset["enterprise_identity_registry"] = registry
        asset["enterprise_identity_registry_recompute_receipt"] = receipt
        asset["enterprise_identity_resolution"] = result
        asset["enterprise_identity_gate"] = gate
    return result
