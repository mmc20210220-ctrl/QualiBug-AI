"""Project world-model edges, identity hypotheses, and evidence references."""
from __future__ import annotations

from typing import Any, Iterable

from ._business_world_model_common import (
    _evidence_ref,
    _evidence_refs,
    _label_to_entity,
    _resolved_entity_refs,
    _rows,
)
from .schema import as_list, stable_id, text, unique_text

def _edge(
    *,
    kind: str,
    source_ref: str,
    target_ref: str,
    authority_ref: str,
    evidence: Iterable[dict[str, Any]] = (),
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "edge_id": stable_id("business_world_edge", kind, source_ref, target_ref, authority_ref),
        "edge_kind": kind,
        "source_ref": source_ref,
        "target_ref": target_ref,
        "authority_ref": authority_ref,
        "evidence_refs": _evidence_refs(evidence),
        "details": dict(details or {}),
        "semantic_payload_copied": False,
    }


def build_world_edges(model: dict[str, Any]) -> list[dict[str, Any]]:
    label_to_entity = _label_to_entity(model)
    rows: list[dict[str, Any]] = []
    for relation in _rows(model.get("object_relations")):
        relation_id = text(relation.get("relation_id"))
        source_ref = text(relation.get("source_entity_ref")) or label_to_entity.get(
            text(relation.get("source_object_ref")), ""
        )
        target_ref = text(relation.get("target_entity_ref")) or label_to_entity.get(
            text(relation.get("target_object_ref")), ""
        )
        if source_ref and target_ref:
            rows.append(
                _edge(
                    kind="OBJECT_RELATION",
                    source_ref=source_ref,
                    target_ref=target_ref,
                    authority_ref=f"enterprise_understanding_model.object_relations[{relation_id}]",
                    evidence=_rows(relation.get("evidence")),
                    details={
                        "relation_ref": relation_id,
                        "relation_type": text(relation.get("relation_type")),
                    },
                )
            )
    for operation in _rows(model.get("operations")):
        operation_id = text(operation.get("operation_id"))
        for entity_id in _resolved_entity_refs(operation, label_to_entity):
            rows.append(
                _edge(
                    kind="OBJECT_OPERATION",
                    source_ref=entity_id,
                    target_ref=operation_id,
                    authority_ref=f"enterprise_understanding_model.operations[{operation_id}]",
                    evidence=_rows(operation.get("evidence")),
                )
            )
    for lifecycle in _rows(model.get("lifecycles")):
        lifecycle_id = text(lifecycle.get("lifecycle_id"))
        entity_id = text(lifecycle.get("business_entity_ref")) or label_to_entity.get(
            text(lifecycle.get("object_ref")), ""
        )
        if entity_id and lifecycle_id:
            rows.append(
                _edge(
                    kind="OBJECT_LIFECYCLE",
                    source_ref=entity_id,
                    target_ref=lifecycle_id,
                    authority_ref=f"enterprise_understanding_model.lifecycles[{lifecycle_id}]",
                    evidence=_rows(lifecycle.get("evidence")),
                )
            )
    for behavior in _rows(model.get("business_behaviors")):
        behavior_id = text(behavior.get("behavior_id"))
        for entity_id in _resolved_entity_refs(behavior, label_to_entity):
            rows.append(
                _edge(
                    kind="OBJECT_BEHAVIOR",
                    source_ref=entity_id,
                    target_ref=behavior_id,
                    authority_ref=(
                        "enterprise_understanding_model.business_behaviors"
                        f"[{behavior_id}]"
                    ),
                    evidence=_rows(behavior.get("evidence")),
                )
            )
    return sorted(rows, key=lambda row: (text(row.get("edge_kind")), text(row.get("edge_id"))))


def build_identity_hypotheses(model: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in _rows(model.get("identity_structural_candidates")):
        result.append(
            {
                "hypothesis_id": text(row.get("candidate_id")),
                "hypothesis_kind": "POSSIBLE_SAME_BUSINESS_OBJECT",
                "candidate_entity_refs": unique_text(as_list(row.get("candidate_entity_ids"))),
                "world_state": "SUSPECTED",
                "reason_code": text(row.get("reason_code")),
                "matched_dimensions": unique_text(as_list(row.get("matched_dimensions"))),
                "evidence_refs": _evidence_refs(_rows(row.get("evidence"))),
                "authority_ref": (
                    "enterprise_understanding_model.identity_structural_candidates"
                    f"[{text(row.get('candidate_id'))}]"
                ),
                "requires_operator_review": True,
                "automatic_entity_union_allowed": False,
                "formal_object_membership_changed": False,
            }
        )
    return sorted(result, key=lambda row: text(row.get("hypothesis_id")))


def build_evidence_registry(model: dict[str, Any]) -> list[dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}

    def register(collection: str, authority_id: str, rows: Iterable[dict[str, Any]]) -> None:
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            ref = _evidence_ref(row)
            refs.setdefault(
                ref,
                {
                    "evidence_ref": ref,
                    "authority_collection": collection,
                    "authority_id": authority_id,
                    "authority_position": index,
                    "source_id": text(row.get("source_id")),
                    "source_locator": text(row.get("source_locator")),
                    "fact_id": text(row.get("fact_id")),
                },
            )

    collection_ids = {
        "business_objects": ("entity_id", "object_id"),
        "operations": ("operation_id",),
        "object_relations": ("relation_id",),
        "lifecycles": ("lifecycle_id",),
        "business_behaviors": ("behavior_id",),
        "behavior_implementation_bindings": ("binding_id",),
        "identity_structural_candidates": ("candidate_id",),
    }
    for collection, id_keys in collection_ids.items():
        for item in _rows(model.get(collection)):
            authority_id = next(
                (text(item.get(key)) for key in id_keys if text(item.get(key))),
                "",
            )
            register(collection, authority_id, _rows(item.get("evidence")))

    for collection in (
        "evidence_index",
        "implementation_evidence_index",
        "scenario_ir_evidence_index",
        "scenario_execution_contract_evidence_index",
        "runtime_plan_evidence_index",
        "runtime_materialization_evidence_index",
    ):
        register(collection, collection, _rows(model.get(collection)))
    return sorted(refs.values(), key=lambda row: text(row.get("evidence_ref")))





__all__ = [
    "build_evidence_registry",
    "build_identity_hypotheses",
    "build_world_edges",
]
