from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.business_world_model import (  # noqa: E501
    build_business_world_model,
    project_business_world_model,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.business_world_model_schema import (
    BUSINESS_WORLD_MODEL_SCHEMA,
    empty_business_world_model,
    validate_business_world_model_shape,
)
from tests.business_world_model_support import _evidence, _model


def test_world_model_is_reference_only_topology_over_existing_authority() -> None:
    world = build_business_world_model(_model())

    assert world["schema"] == BUSINESS_WORLD_MODEL_SCHEMA
    assert world["reference_only_projection"] is True
    assert world["semantic_payload_duplication_allowed"] is False
    assert world["automatic_entity_union_allowed"] is False
    assert world["gate"]["status"] == "PASS"
    assert world["gate"]["entry_allowed"] is True
    assert world["gate"]["integrity_violations"] == []

    objects = {row["node_id"]: row for row in world["object_nodes"]}
    assert set(objects) == {"entity:customer", "entity:order"}
    assert objects["entity:order"]["world_state"] == "CONFIRMED"
    assert objects["entity:order"]["operation_refs"] == ["operation:create-order"]
    assert objects["entity:order"]["lifecycle_refs"] == ["lifecycle:order"]
    assert objects["entity:order"]["relation_refs"] == ["relation:customer-order"]
    assert objects["entity:order"]["behavior_refs"] == ["behavior:create-order"]
    assert "legacy:operation" not in objects["entity:order"]["operation_refs"]
    assert all(row["semantic_payload_copied"] is False for row in world["object_nodes"])

    behaviors = {row["node_id"]: row for row in world["behavior_nodes"]}
    assert behaviors["behavior:create-order"]["world_state"] == "CONFIRMED"
    assert behaviors["behavior:create-order"]["implementation_binding_refs"] == [
        "binding:create-order-api"
    ]
    assert behaviors["behavior:customer-review"]["world_state"] == "SUSPECTED"

    edge_kinds = {row["edge_kind"] for row in world["edges"]}
    assert edge_kinds == {
        "OBJECT_BEHAVIOR",
        "OBJECT_LIFECYCLE",
        "OBJECT_OPERATION",
        "OBJECT_RELATION",
    }
    registered = {row["evidence_ref"] for row in world["evidence_registry"]}
    used = {
        evidence_ref
        for row in [
            *world["object_nodes"],
            *world["behavior_nodes"],
            *world["edges"],
            *world["identity_hypotheses"],
        ]
        for evidence_ref in row.get("evidence_refs", [])
    }
    assert used <= registered


def test_duplicate_authority_rows_collapse_by_stable_entity_without_losing_evidence() -> None:
    model = _model()
    duplicate = dict(model["business_objects"][0])
    duplicate["evidence"] = [
        _evidence(
            "api:order",
            "openapi.yaml#/schemas/Order",
            "fact:order-api",
        )
    ]
    model["business_objects"].append(duplicate)

    world = build_business_world_model(model)

    order = next(row for row in world["object_nodes"] if row["node_id"] == "entity:order")
    assert len(world["object_nodes"]) == 2
    assert order["authority_row_count"] == 2
    assert order["duplicate_authority_rows_collapsed"] == 1
    assert order["canonical_label_candidates"] == ["订单"]
    assert order["world_state"] == "CONFIRMED"
    assert len(order["evidence_refs"]) == 2
    assert world["gate"]["metrics"]["authority_object_row_count"] == 3
    assert world["gate"]["metrics"]["collapsed_duplicate_object_row_count"] == 1
    assert world["gate"]["integrity_violations"] == []


def test_structural_candidate_is_suspected_hypothesis_not_formal_union() -> None:
    model = _model()
    original_objects = [dict(row) for row in model["business_objects"]]

    world = build_business_world_model(model)

    assert model["business_objects"] == original_objects
    assert len(world["identity_hypotheses"]) == 1
    hypothesis = world["identity_hypotheses"][0]
    assert hypothesis["world_state"] == "SUSPECTED"
    assert hypothesis["candidate_entity_refs"] == ["entity:customer", "entity:order"]
    assert hypothesis["requires_operator_review"] is True
    assert hypothesis["automatic_entity_union_allowed"] is False
    assert hypothesis["formal_object_membership_changed"] is False
    assert len(world["object_nodes"]) == 2


def test_projection_publishes_world_model_without_mutating_semantic_collections() -> None:
    model = _model()
    original_objects = [dict(row) for row in model["business_objects"]]
    asset: dict = {}

    projected = project_business_world_model(asset, model)

    assert projected is not model
    assert projected["business_objects"] == original_objects
    assert asset["business_world_model"] == projected["business_world_model"]
    assert projected["metrics"]["business_world_object_node_count"] == 2
    assert projected["metrics"]["business_world_identity_hypothesis_count"] == 1


def test_world_model_schema_is_first_class_and_shape_validated() -> None:
    world = empty_business_world_model()
    assert validate_business_world_model_shape(world) == []

    world["object_nodes"] = {}
    violations = validate_business_world_model_shape(world)
    assert {
        (row["code"], row.get("field"))
        for row in violations
    } >= {("BUSINESS_WORLD_MODEL_COLLECTION_INVALID", "object_nodes")}
