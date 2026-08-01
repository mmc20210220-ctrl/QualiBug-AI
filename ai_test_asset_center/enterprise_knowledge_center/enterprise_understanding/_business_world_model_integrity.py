"""Validate reference integrity for the governed business world model."""
from __future__ import annotations

from typing import Any

from ._business_world_model_common import (
    WORLD_STATES,
    _entity_id,
    _label_entity_index,
    _rows,
)
from .schema import as_list, text, unique_text

def _authority_reference_violations(
    model: dict[str, Any], object_ids: set[str]
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    label_index = _label_entity_index(model)

    def check_many(
        collection: str,
        authority_id: str,
        row: dict[str, Any],
        *,
        stable_key: str = "business_entity_refs",
        label_key: str = "object_refs",
    ) -> None:
        stable_refs = set(unique_text(as_list(row.get(stable_key))))
        missing_refs = sorted(stable_refs - object_ids)
        if missing_refs:
            violations.append(
                {
                    "code": "WORLD_AUTHORITY_OBJECT_REF_UNRESOLVED",
                    "authority_collection": collection,
                    "authority_id": authority_id,
                    "missing_entity_refs": missing_refs,
                    "unresolved_object_labels": [],
                }
            )

    for row in _rows(model.get("operations")):
        check_many(
            "operations",
            text(row.get("operation_id")),
            row,
        )
    for row in _rows(model.get("business_behaviors")):
        check_many(
            "business_behaviors",
            text(row.get("behavior_id")),
            row,
        )
    for row in _rows(model.get("lifecycles")):
        stable_ref = text(row.get("business_entity_ref"))
        label = text(row.get("object_ref"))
        unresolved_label = bool(
            not stable_ref and label and len(label_index.get(label, set())) != 1
        )
        if (stable_ref and stable_ref not in object_ids) or unresolved_label:
            violations.append(
                {
                    "code": "WORLD_AUTHORITY_OBJECT_REF_UNRESOLVED",
                    "authority_collection": "lifecycles",
                    "authority_id": text(row.get("lifecycle_id")),
                    "missing_entity_refs": (
                        [stable_ref] if stable_ref and stable_ref not in object_ids else []
                    ),
                    "unresolved_object_labels": [label] if unresolved_label else [],
                }
            )
    for row in _rows(model.get("object_relations")):
        missing_refs: list[str] = []
        unresolved_labels: list[str] = []
        for stable_key, label_key in (
            ("source_entity_ref", "source_object_ref"),
            ("target_entity_ref", "target_object_ref"),
        ):
            stable_ref = text(row.get(stable_key))
            label = text(row.get(label_key))
            if stable_ref and stable_ref not in object_ids:
                missing_refs.append(stable_ref)
            elif not stable_ref and label and len(label_index.get(label, set())) != 1:
                unresolved_labels.append(label)
        if missing_refs or unresolved_labels:
            violations.append(
                {
                    "code": "WORLD_AUTHORITY_OBJECT_REF_UNRESOLVED",
                    "authority_collection": "object_relations",
                    "authority_id": text(row.get("relation_id")),
                    "missing_entity_refs": unique_text(missing_refs),
                    "unresolved_object_labels": unique_text(unresolved_labels),
                }
            )
    return violations


def validate_world_integrity(world: dict[str, Any], model: dict[str, Any]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    object_nodes = _rows(world.get("object_nodes"))
    behavior_nodes = _rows(world.get("behavior_nodes"))
    node_ids = [text(row.get("node_id")) for row in [*object_nodes, *behavior_nodes]]
    if any(not value for value in node_ids):
        violations.append({"code": "WORLD_NODE_ID_MISSING"})
    duplicates = sorted({value for value in node_ids if value and node_ids.count(value) > 1})
    if duplicates:
        violations.append({"code": "WORLD_NODE_ID_DUPLICATED", "node_ids": duplicates})
    for row in [*object_nodes, *behavior_nodes]:
        if text(row.get("world_state")) not in WORLD_STATES:
            violations.append(
                {
                    "code": "WORLD_STATE_INVALID",
                    "node_id": row.get("node_id"),
                    "value": row.get("world_state"),
                }
            )
        if bool(row.get("semantic_payload_copied")):
            violations.append(
                {
                    "code": "WORLD_SEMANTIC_PAYLOAD_COPIED",
                    "node_id": row.get("node_id"),
                }
            )
    object_ids = {text(row.get("node_id")) for row in object_nodes}
    behavior_ids = {text(row.get("node_id")) for row in behavior_nodes}
    violations.extend(_authority_reference_violations(model, object_ids))
    operation_ids = {text(row.get("operation_id")) for row in _rows(model.get("operations"))}
    lifecycle_ids = {text(row.get("lifecycle_id")) for row in _rows(model.get("lifecycles"))}
    valid_targets = object_ids | behavior_ids | operation_ids | lifecycle_ids
    for row in _rows(world.get("edges")):
        if text(row.get("source_ref")) not in object_ids:
            violations.append(
                {
                    "code": "WORLD_EDGE_SOURCE_UNRESOLVED",
                    "edge_id": row.get("edge_id"),
                }
            )
        if text(row.get("target_ref")) not in valid_targets:
            violations.append(
                {
                    "code": "WORLD_EDGE_TARGET_UNRESOLVED",
                    "edge_id": row.get("edge_id"),
                }
            )
    for row in _rows(world.get("identity_hypotheses")):
        refs = set(unique_text(as_list(row.get("candidate_entity_refs"))))
        if not refs or not refs.issubset(object_ids):
            violations.append(
                {
                    "code": "WORLD_HYPOTHESIS_ENTITY_UNRESOLVED",
                    "hypothesis_id": row.get("hypothesis_id"),
                }
            )
        if bool(row.get("automatic_entity_union_allowed")):
            violations.append(
                {
                    "code": "WORLD_HYPOTHESIS_AUTO_UNION_FORBIDDEN",
                    "hypothesis_id": row.get("hypothesis_id"),
                }
            )
    evidence_ids = {
        text(row.get("evidence_ref"))
        for row in _rows(world.get("evidence_registry"))
        if text(row.get("evidence_ref"))
    }
    for row in [
        *_rows(world.get("object_nodes")),
        *_rows(world.get("behavior_nodes")),
        *_rows(world.get("edges")),
        *_rows(world.get("identity_hypotheses")),
    ]:
        missing = sorted(set(unique_text(as_list(row.get("evidence_refs")))) - evidence_ids)
        if missing:
            violations.append(
                {
                    "code": "WORLD_EVIDENCE_REF_UNRESOLVED",
                    "authority_ref": row.get("payload_authority_ref") or row.get("authority_ref"),
                    "evidence_refs": missing,
                }
            )
    authority_rows = _rows(model.get("business_objects"))
    authority_entity_ids = {_entity_id(row) for row in authority_rows if _entity_id(row)}
    if len(object_nodes) != len(authority_entity_ids):
        violations.append(
            {
                "code": "WORLD_OBJECT_NODE_COUNT_DRIFT",
                "world_count": len(object_nodes),
                "authority_unique_entity_count": len(authority_entity_ids),
            }
        )
    for row in object_nodes:
        labels = unique_text(as_list(row.get("canonical_label_candidates")))
        if len(labels) > 1:
            violations.append(
                {
                    "code": "WORLD_ENTITY_CANONICAL_LABEL_CONFLICT",
                    "node_id": row.get("node_id"),
                    "canonical_label_candidates": labels,
                }
            )
    return violations



__all__ = ["validate_world_integrity"]
