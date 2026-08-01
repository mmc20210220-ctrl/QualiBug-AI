"""Project governed business-object and behavior nodes."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from ._business_world_model_common import (
    _behavior_state,
    _canonical_label,
    _entity_id,
    _evidence_refs,
    _label_to_entity,
    _object_state,
    _resolved_entity_refs,
    _rows,
    _unresolved_object_conflict_entities,
)
from .schema import as_list, text, unique_text

def build_object_nodes(model: dict[str, Any]) -> list[dict[str, Any]]:
    conflicted = _unresolved_object_conflict_entities(model)
    label_to_entity = _label_to_entity(model)
    behavior_refs: dict[str, list[str]] = defaultdict(list)
    operation_refs: dict[str, list[str]] = defaultdict(list)
    lifecycle_refs: dict[str, list[str]] = defaultdict(list)
    relation_refs: dict[str, list[str]] = defaultdict(list)

    for behavior in _rows(model.get("business_behaviors")):
        behavior_id = text(behavior.get("behavior_id"))
        for entity_id in _resolved_entity_refs(behavior, label_to_entity):
            behavior_refs[entity_id].append(behavior_id)
    for operation in _rows(model.get("operations")):
        operation_id = text(operation.get("operation_id"))
        for entity_id in _resolved_entity_refs(operation, label_to_entity):
            operation_refs[entity_id].append(operation_id)
    for lifecycle in _rows(model.get("lifecycles")):
        lifecycle_id = text(lifecycle.get("lifecycle_id"))
        entity_id = text(lifecycle.get("business_entity_ref")) or label_to_entity.get(
            text(lifecycle.get("object_ref")), ""
        )
        if entity_id and lifecycle_id:
            lifecycle_refs[entity_id].append(lifecycle_id)
    for relation in _rows(model.get("object_relations")):
        relation_id = text(relation.get("relation_id"))
        source_ref = text(relation.get("source_entity_ref")) or label_to_entity.get(
            text(relation.get("source_object_ref")), ""
        )
        target_ref = text(relation.get("target_entity_ref")) or label_to_entity.get(
            text(relation.get("target_object_ref")), ""
        )
        for entity_id in unique_text([source_ref, target_ref]):
            relation_refs[entity_id].append(relation_id)

    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, row in enumerate(_rows(model.get("business_objects"))):
        grouped[_entity_id(row)].append((index, row))

    nodes: list[dict[str, Any]] = []
    for entity_id, indexed_rows in sorted(grouped.items()):
        rows = [row for _index, row in indexed_rows]
        labels = unique_text(_canonical_label(row) for row in rows)
        canonical = labels[0] if len(labels) == 1 else ""
        states = {_object_state(row, conflicted) for row in rows}
        state = (
            "CONFLICTED"
            if len(labels) > 1 or "CONFLICTED" in states
            else "CONFIRMED"
            if states == {"CONFIRMED"}
            else "UNKNOWN"
        )
        nodes.append(
            {
                "node_id": entity_id,
                "node_kind": "BUSINESS_OBJECT",
                "canonical_label": canonical,
                "canonical_label_candidates": labels,
                "aliases": unique_text(
                    value
                    for row in rows
                    for value in as_list(row.get("aliases"))
                ),
                "world_state": state,
                "source_kinds": unique_text(
                    value
                    for row in rows
                    for value in as_list(row.get("source_kinds"))
                ),
                "identity_binding_refs": unique_text(
                    value
                    for row in rows
                    for value in as_list(row.get("identity_binding_refs"))
                ),
                "operation_refs": unique_text(operation_refs.get(entity_id, [])),
                "lifecycle_refs": unique_text(lifecycle_refs.get(entity_id, [])),
                "relation_refs": unique_text(relation_refs.get(entity_id, [])),
                "behavior_refs": unique_text(behavior_refs.get(entity_id, [])),
                "structural_identity_candidate_refs": unique_text(
                    value
                    for row in rows
                    for value in as_list(row.get("structural_identity_candidate_refs"))
                ),
                "evidence_refs": _evidence_refs(
                    evidence
                    for row in rows
                    for evidence in _rows(row.get("evidence"))
                ),
                "payload_authority_refs": [
                    f"enterprise_understanding_model.business_objects[{index}]"
                    for index, _row in indexed_rows
                ],
                "authority_row_count": len(rows),
                "duplicate_authority_rows_collapsed": max(0, len(rows) - 1),
                "semantic_payload_copied": False,
            }
        )
    return sorted(
        nodes,
        key=lambda row: (
            text(row.get("canonical_label")),
            text(row.get("node_id")),
        ),
    )


def build_behavior_nodes(model: dict[str, Any]) -> list[dict[str, Any]]:
    label_to_entity = _label_to_entity(model)
    binding_refs: dict[str, list[str]] = defaultdict(list)
    for binding in _rows(model.get("behavior_implementation_bindings")):
        behavior_ref = text(binding.get("behavior_ref") or binding.get("source_behavior_ref"))
        binding_id = text(binding.get("binding_id"))
        if behavior_ref and binding_id:
            binding_refs[behavior_ref].append(binding_id)

    nodes: list[dict[str, Any]] = []
    for row in _rows(model.get("business_behaviors")):
        behavior_id = text(row.get("behavior_id"))
        nodes.append(
            {
                "node_id": behavior_id,
                "node_kind": "BUSINESS_BEHAVIOR",
                "world_state": _behavior_state(row),
                "operation_ref": text(row.get("operation_ref")),
                "business_entity_refs": _resolved_entity_refs(row, label_to_entity),
                "actor_refs": unique_text(as_list(row.get("actor_refs"))),
                "implementation_binding_refs": unique_text(
                    [
                        *as_list(row.get("implementation_binding_refs")),
                        *binding_refs.get(behavior_id, []),
                    ]
                ),
                "evidence_refs": _evidence_refs(_rows(row.get("evidence"))),
                "payload_authority_ref": (
                    "enterprise_understanding_model.business_behaviors"
                    f"[{behavior_id}]"
                ),
                "semantic_payload_copied": False,
            }
        )
    return sorted(nodes, key=lambda row: text(row.get("node_id")))



__all__ = ["build_behavior_nodes", "build_object_nodes"]
