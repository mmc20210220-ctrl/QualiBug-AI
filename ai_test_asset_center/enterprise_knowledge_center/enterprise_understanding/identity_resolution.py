"""Single evidence-governed authority for cross-source enterprise identity."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from .identity_clusters import (
    build_alias_conflicts,
    build_identity_bindings,
    build_identity_clusters,
    build_label_to_entity,
)
from .identity_edges import build_identity_edges
from .identity_types import (
    IDENTITY_GATE_SCHEMA,
    IDENTITY_REGISTRY_SCHEMA,
    IDENTITY_RESULT_SCHEMA,
    annotate_fact_identity_mentions,
    collect_identity_mentions,
    fact_mentions,
)
from .authorization_semantics import resolve_fact_authorization
from .schema import (
    as_dict,
    as_list,
    clone_asset_for_understanding_projection,
    stable_id,
    text,
    unique_text,
)


def _authorization_contract_identity(row: dict[str, Any]) -> dict[str, Any]:
    contract = as_dict(row)
    return {
        "authorization_contract_id": text(
            contract.get("authorization_contract_id")
        ),
        "decision": text(contract.get("decision")),
        "declared_decision": text(contract.get("declared_decision")),
        "resource_refs": unique_text(as_list(contract.get("resource_refs"))),
        "actions": unique_text(as_list(contract.get("actions"))),
        "scope": contract.get("scope"),
        "conditions": unique_text(as_list(contract.get("conditions"))),
        "source_ref": text(contract.get("source_ref")),
        "derivation": text(contract.get("derivation")),
        "coordinate_complete": contract.get("coordinate_complete") is True,
    }


def _actor_authorization_identity(model: dict[str, Any]) -> list[dict[str, Any]]:
    actors: list[dict[str, Any]] = []
    for raw_actor in as_list(model.get("actors")):
        if not isinstance(raw_actor, dict):
            continue
        actor = as_dict(raw_actor)
        contracts = [
            _authorization_contract_identity(row)
            for row in as_list(actor.get("authorization_contracts"))
            if isinstance(row, dict)
        ]
        contracts.sort(
            key=lambda row: (
                text(row.get("authorization_contract_id")),
                text(row.get("decision")),
                text(row.get("source_ref")),
            )
        )
        actors.append(
            {
                "actor_id": text(actor.get("actor_id")),
                "name": text(actor.get("name")),
                "authorization_status": text(actor.get("authorization_status")),
                "authorization_contracts": contracts,
            }
        )
    actors.sort(key=lambda row: (row["actor_id"], row["name"]))
    return actors


def resolve_enterprise_identities(asset: dict[str, Any]) -> dict[str, Any]:
    # Identity resolution is the first stage after all structured facts and current
    # interfaces have converged. Materialize source-backed role permissions into the
    # existing permission_matrix SSOT before identity/model fingerprints are sealed.
    from .fact_permission_matrix import materialize_fact_permission_matrix

    materialize_fact_permission_matrix(asset)
    annotate_fact_identity_mentions(asset)
    facts = [
        row
        for row in as_list(as_dict(asset.get("business_fact_ledger")).get("items"))
        if isinstance(row, dict)
    ]
    mentions = collect_identity_mentions(asset, facts)
    edges = build_identity_edges(asset, facts, mentions)
    clusters, mention_to_entity, conflicts = build_identity_clusters(asset, mentions, edges)
    label_to_entity, collisions = build_label_to_entity(mentions, clusters)
    conflicts.extend(build_alias_conflicts(facts, label_to_entity))
    conflicts.extend(
        {
            "kind": "SAME_LABEL_MULTIPLE_IDENTITY_CONFLICT",
            "status": "UNRESOLVED",
            "label": label,
            "candidate_entity_ids": sorted(entity_ids),
            "automatic_resolution_allowed": False,
            "evidence": [],
        }
        for label, entity_ids in sorted(collisions.items())
    )
    bindings, unknowns = build_identity_bindings(
        mentions, edges, mention_to_entity, clusters
    )
    canonical_by_entity = {
        text(row.get("entity_id")): text(row.get("canonical_label")) for row in clusters
    }
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
                "evidence": row.get("evidence"),
            }
            for row in clusters
        ],
        "identity_is_name_independent": True,
        "automatic_similarity_merge_allowed": False,
    }
    asset["enterprise_identity_registry"] = registry

    for fact in facts:
        if text(fact.get("kind")) not in {"RULE", "STATE_TRANSITION"}:
            continue
        refs: list[str] = []
        for side in ("subject", "object"):
            slot = dict(as_dict(fact.get(side)))
            resolved = unique_text(
                label_to_entity.get(label) for label in fact_mentions(fact, side)
            )
            slot["resolved_entity_refs"] = resolved
            fact[side] = slot
            refs.extend(resolved)
        fact["identity_resolution_refs"] = unique_text(refs)

    status = (
        "BLOCKED_ENTERPRISE_IDENTITY_CONFLICT"
        if conflicts
        else "PARTIAL_ENTERPRISE_IDENTITY_BINDING"
        if unknowns
        else "PASS"
    )
    gate = {
        "schema": IDENTITY_GATE_SCHEMA,
        "status": status,
        "entry_allowed": not conflicts,
        "business_understanding_allowed": not conflicts,
        "per_binding_execution_admission": True,
        "automatic_similarity_merge_allowed": False,
        "metrics": {
            "mention_count": len(mentions),
            "identity_edge_count": len(edges),
            "business_entity_count": len(clusters),
            "technical_binding_count": len(bindings),
            "unknown_count": len(unknowns),
            "conflict_count": len(conflicts),
        },
        "critical_conflicts": conflicts,
        "unknowns": unknowns,
        "required_operator_action": (
            "resolve conflicting source-backed enterprise identities"
            if conflicts
            else "bind unresolved technical assets before executing affected behaviors"
            if unknowns
            else ""
        ),
    }
    result = {
        "schema": IDENTITY_RESULT_SCHEMA,
        "mentions": mentions,
        "edges": edges,
        "clusters": clusters,
        "bindings": bindings,
        "unknowns": unknowns,
        "conflicts": conflicts,
        "mention_to_entity": mention_to_entity,
        "label_to_entity": label_to_entity,
        "canonical_label_by_entity": canonical_by_entity,
        "registry": registry,
        "gate": gate,
        "merge_policy": "source_backed_identity_graph_only",
        "technical_artifacts_are_business_entities": False,
        "original_mentions_are_fact_authority": True,
    }
    asset["enterprise_identity_resolution"] = result
    asset["enterprise_identity_gate"] = gate
    return result


def project_asset_for_legacy_builder(
    asset: dict[str, Any], resolution: dict[str, Any]
) -> dict[str, Any]:
    projected = clone_asset_for_understanding_projection(asset)
    label_to_entity = dict(as_dict(resolution.get("label_to_entity")))
    canonical_by_entity = dict(as_dict(resolution.get("canonical_label_by_entity")))
    ledger = dict(as_dict(projected.get("business_fact_ledger")))
    projected_facts: list[dict[str, Any]] = []
    for raw_fact in as_list(ledger.get("items")):
        if not isinstance(raw_fact, dict):
            continue
        fact = deepcopy(raw_fact)
        authorization = resolve_fact_authorization(fact)
        preserve_authorization_source_refs = (
            text(authorization.get("semantic_kind")).upper() == "AUTHORIZATION"
            and authorization.get("authority_declared") is True
        )
        if text(fact.get("kind")) in {"RULE", "STATE_TRANSITION"}:
            for side in ("subject", "object"):
                slot = dict(as_dict(fact.get(side)))
                source_refs = unique_text(as_list(slot.get("entity_refs")))
                mentions = fact_mentions(fact, side)
                entity_ids = unique_text(label_to_entity.get(label) for label in mentions)
                canonical_refs = unique_text(
                    canonical_by_entity.get(entity_id) for entity_id in entity_ids
                )
                slot["entity_mentions"] = mentions
                slot["resolved_entity_refs"] = entity_ids
                slot["entity_refs"] = unique_text(
                    [
                        *canonical_refs,
                        *(
                            source_refs
                            if preserve_authorization_source_refs and not canonical_refs
                            else []
                        ),
                    ]
                )
                if preserve_authorization_source_refs:
                    slot["source_entity_refs"] = source_refs
                    slot["authorization_identity_projection_status"] = (
                        "RESOLVED"
                        if canonical_refs
                        else "UNRESOLVED_SOURCE_REF_PRESERVED"
                    )
                    slot["automatic_identity_merge_allowed"] = False
                fact[side] = slot
        projected_facts.append(fact)
    ledger["items"] = projected_facts
    projected["business_fact_ledger"] = ledger
    projected["business_objects"] = [
        {
            "object_id": row.get("entity_id"),
            "object": row.get("canonical_label"),
            "name": row.get("canonical_label"),
            "aliases": row.get("aliases"),
            "source_refs": row.get("source_refs"),
            "identity_confidence": row.get("confidence"),
        }
        for row in as_list(resolution.get("clusters"))
        if isinstance(row, dict)
    ]
    projected["data_tables"] = []
    projected["field_dictionary"] = []
    return projected


def apply_identity_resolution_to_model(
    model: dict[str, Any], resolution: dict[str, Any]
) -> dict[str, Any]:
    label_to_entity = dict(as_dict(resolution.get("label_to_entity")))
    canonical_by_entity = dict(as_dict(resolution.get("canonical_label_by_entity")))
    clusters = {
        text(row.get("entity_id")): row
        for row in as_list(resolution.get("clusters"))
        if isinstance(row, dict)
    }
    binding_refs: dict[str, list[str]] = defaultdict(list)
    for row in as_list(resolution.get("bindings")):
        if isinstance(row, dict):
            binding_refs[text(row.get("entity_id"))].append(text(row.get("binding_id")))

    for obj in as_list(model.get("business_objects")):
        if not isinstance(obj, dict):
            continue
        entity_id = label_to_entity.get(text(obj.get("name")))
        if not entity_id:
            continue
        cluster = as_dict(clusters.get(entity_id))
        obj["legacy_object_id"] = obj.get("object_id")
        obj["object_id"] = obj["entity_id"] = entity_id
        obj["canonical_label"] = canonical_by_entity.get(entity_id) or obj.get("name")
        obj["mention_refs"] = as_list(cluster.get("member_mention_ids"))
        obj["identity_resolution_status"] = cluster.get("status")
        obj["identity_binding_refs"] = unique_text(binding_refs.get(entity_id, []))
        obj["aliases"] = unique_text(
            [*as_list(obj.get("aliases")), *as_list(cluster.get("aliases"))]
        )

    operation_ids: dict[str, str] = {}
    lifecycle_ids: dict[str, str] = {}
    relation_ids: dict[str, str] = {}
    for row in as_list(model.get("operations")):
        if not isinstance(row, dict):
            continue
        refs = unique_text(
            label_to_entity.get(text(label)) for label in as_list(row.get("object_refs"))
        )
        row["business_entity_refs"] = refs
        old = text(row.get("operation_id"))
        row["legacy_operation_id"] = old
        row["operation_id"] = stable_id("business_operation", row.get("name"), refs)
        operation_ids[old] = row["operation_id"]
    for row in as_list(model.get("lifecycles")):
        if not isinstance(row, dict):
            continue
        entity_id = label_to_entity.get(text(row.get("object_ref")), "")
        row["business_entity_ref"] = entity_id
        old = text(row.get("lifecycle_id"))
        row["legacy_lifecycle_id"] = old
        row["lifecycle_id"] = stable_id(
            "business_lifecycle", entity_id, row.get("states")
        )
        lifecycle_ids[old] = row["lifecycle_id"]
    for row in as_list(model.get("object_relations")):
        if not isinstance(row, dict):
            continue
        source = label_to_entity.get(text(row.get("source_object_ref")), "")
        target = label_to_entity.get(text(row.get("target_object_ref")), "")
        row["source_entity_ref"], row["target_entity_ref"] = source, target
        old = text(row.get("relation_id"))
        row["legacy_relation_id"] = old
        row["relation_id"] = stable_id(
            "business_relation", source, row.get("relation_type"), target
        )
        relation_ids[old] = row["relation_id"]
    for obj in as_list(model.get("business_objects")):
        if not isinstance(obj, dict):
            continue
        obj["operation_refs"] = unique_text(
            operation_ids.get(text(value), text(value))
            for value in as_list(obj.get("operation_refs"))
        )
        obj["lifecycle_refs"] = unique_text(
            lifecycle_ids.get(text(value), text(value))
            for value in as_list(obj.get("lifecycle_refs"))
        )
        obj["relation_refs"] = unique_text(
            relation_ids.get(text(value), text(value))
            for value in as_list(obj.get("relation_refs"))
        )
    for row in as_list(model.get("rules")):
        if isinstance(row, dict):
            row["business_entity_refs"] = unique_text(
                label_to_entity.get(text(label)) for label in as_list(row.get("object_refs"))
            )
    for row in as_list(model.get("processes")):
        if not isinstance(row, dict):
            continue
        labels = list(as_list(row.get("inputs")))
        labels.extend(
            as_dict(value).get("object_ref")
            for value in as_list(row.get("outputs"))
            if isinstance(value, dict)
        )
        refs = unique_text(label_to_entity.get(text(label)) for label in labels)
        row["business_entity_refs"] = refs
        row["legacy_process_id"] = row.get("process_id")
        row["process_id"] = stable_id(
            "business_process",
            row.get("process_type"),
            refs,
            row.get("steps"),
            row.get("branches"),
        )

    model.update(
        {
            "identity_mentions": as_list(resolution.get("mentions")),
            "identity_edges": as_list(resolution.get("edges")),
            "identity_clusters": as_list(resolution.get("clusters")),
            "identity_bindings": as_list(resolution.get("bindings")),
            "identity_unknowns": as_list(resolution.get("unknowns")),
            "identity_conflicts": as_list(resolution.get("conflicts")),
            "identity_registry": as_dict(resolution.get("registry")),
            "identity_gate": as_dict(resolution.get("gate")),
            "term_resolution": {
                "canonicalization_contract": "SOURCE_EVIDENCE_REQUIRED",
                "alias_to_object": {
                    label: canonical_by_entity.get(entity_id, label)
                    for label, entity_id in label_to_entity.items()
                },
                "alias_to_entity": label_to_entity,
                "merge_policy": "source_backed_identity_graph_only",
                "automatic_inference_allowed": False,
            },
        }
    )
    model["unknowns"] = [
        *as_list(model.get("unknowns")),
        *as_list(resolution.get("unknowns")),
    ]
    model["conflicts"] = [
        *as_list(model.get("conflicts")),
        *as_list(resolution.get("conflicts")),
    ]
    identity_gate = as_dict(resolution.get("gate"))
    if not bool(identity_gate.get("entry_allowed", True)):
        gate = dict(as_dict(model.get("gate")))
        gate.update(
            {
                "status": identity_gate.get("status"),
                "entry_allowed": False,
                "identity_gate": identity_gate,
                "required_operator_action": identity_gate.get("required_operator_action"),
                "critical_unknowns": [
                    *as_list(gate.get("critical_unknowns")),
                    *as_list(identity_gate.get("critical_conflicts")),
                ],
            }
        )
        model["gate"] = gate
    model["model_id"] = stable_id(
        "enterprise_understanding",
        model.get("source_asset_id"),
        sorted(
            text(row.get("entity_id"))
            for row in as_list(model.get("business_objects"))
            if isinstance(row, dict)
        ),
        sorted(
            text(row.get("operation_id"))
            for row in as_list(model.get("operations"))
            if isinstance(row, dict)
        ),
        sorted(
            text(row.get("lifecycle_id"))
            for row in as_list(model.get("lifecycles"))
            if isinstance(row, dict)
        ),
        _actor_authorization_identity(model),
        sorted(
            text(row.get("unknown_id"))
            for row in as_list(model.get("authorization_unknowns"))
            if isinstance(row, dict) and text(row.get("unknown_id"))
        ),
    )
    metrics = dict(as_dict(model.get("metrics")))
    metrics.update(
        {
            "enterprise_identity_entity_count": len(as_list(resolution.get("clusters"))),
            "enterprise_identity_binding_count": len(as_list(resolution.get("bindings"))),
            "enterprise_identity_unknown_count": len(as_list(resolution.get("unknowns"))),
            "enterprise_identity_conflict_count": len(as_list(resolution.get("conflicts"))),
            "enterprise_identity_name_independent": True,
            "actor_authorization_bound_to_model_identity": True,
        }
    )
    model["metrics"] = metrics
    return model
