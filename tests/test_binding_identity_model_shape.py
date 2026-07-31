from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.schema import (
    BINDING_IDENTITY_GATE_SCHEMA,
    BINDING_IDENTITY_SCHEMA,
    empty_model,
    validate_model_shape,
)


def _codes(model: dict) -> list[tuple[str, str]]:
    return [
        (str(row.get("code") or ""), str(row.get("field") or ""))
        for row in validate_model_shape(model)
    ]


def test_empty_model_contains_canonical_binding_identity_contract() -> None:
    model = empty_model()

    graph = model["binding_identity_graph"]
    gate = model["binding_identity_gate"]
    assert graph["schema"] == BINDING_IDENTITY_SCHEMA
    assert graph["action_surface_bindings"] == []
    assert graph["contract_field_bindings"] == []
    assert graph["runtime_value_bindings"] == []
    assert graph["observer_bindings"] == []
    assert graph["formal_ui_surface_bindings"] == []
    assert model["binding_identity_unknowns"] == []
    assert model["binding_identity_relationships"] == []
    assert gate["schema"] == BINDING_IDENTITY_GATE_SCHEMA
    assert gate["status"] == "NOT_BUILT"
    assert gate["entry_allowed"] is False
    assert gate["binding_identity_ready"] is False
    assert validate_model_shape(model) == []


def test_binding_identity_graph_collection_shape_is_validated() -> None:
    model = empty_model()
    model["binding_identity_graph"]["contract_field_bindings"] = {}

    assert (
        "MODEL_COLLECTION_INVALID",
        "binding_identity_graph.contract_field_bindings",
    ) in _codes(model)


def test_binding_identity_graph_schema_is_validated() -> None:
    model = empty_model()
    model["binding_identity_graph"]["schema"] = "qualibug.invalid-binding-graph"

    violations = validate_model_shape(model)
    assert any(
        row["code"] == "BINDING_IDENTITY_GRAPH_SCHEMA_INVALID"
        for row in violations
    )


def test_binding_identity_top_level_container_shape_is_validated() -> None:
    model = empty_model()
    model["binding_identity_unknowns"] = {}
    model["binding_identity_relationships"] = {}
    model["binding_identity_gate"] = []

    codes = _codes(model)
    assert ("MODEL_COLLECTION_INVALID", "binding_identity_unknowns") in codes
    assert ("MODEL_COLLECTION_INVALID", "binding_identity_relationships") in codes
    assert ("MODEL_OBJECT_INVALID", "binding_identity_gate") in codes


def test_persisted_pre_binding_identity_model_remains_compatible() -> None:
    model = deepcopy(empty_model())
    for key in (
        "binding_identity_graph",
        "binding_identity_unknowns",
        "binding_identity_relationships",
        "binding_identity_gate",
    ):
        model.pop(key)

    assert validate_model_shape(model) == []
