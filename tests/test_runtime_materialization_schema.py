from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.schema import (
    RUNTIME_MATERIALIZATION_GATE_SCHEMA,
    empty_model,
    validate_model_shape,
)


def test_empty_model_includes_runtime_materialization_containers() -> None:
    model = empty_model()

    assert model["runtime_materializations"] == []
    assert model["runtime_materialization_unknowns"] == []
    assert model["runtime_materialization_evidence_index"] == []
    assert model["runtime_materialization_relationships"] == []
    assert model["runtime_materialization_gate"] == {
        "schema": RUNTIME_MATERIALIZATION_GATE_SCHEMA,
        "status": "NOT_BUILT",
        "entry_allowed": False,
        "runtime_materialization_ready": False,
        "execution_allowed": False,
        "metrics": {},
    }


def test_persisted_pre_materialization_model_remains_shape_compatible() -> None:
    model = empty_model()
    for key in (
        "runtime_materializations",
        "runtime_materialization_unknowns",
        "runtime_materialization_evidence_index",
        "runtime_materialization_relationships",
        "runtime_materialization_gate",
    ):
        model.pop(key)

    assert validate_model_shape(model) == []


def test_materialization_container_type_errors_are_visible() -> None:
    model = empty_model()
    model["runtime_materializations"] = {}
    model["runtime_materialization_gate"] = []

    violations = validate_model_shape(model)
    assert {row["field"] for row in violations} >= {
        "runtime_materializations",
        "runtime_materialization_gate",
    }
