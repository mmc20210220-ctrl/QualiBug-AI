from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.schema import (
    RUNTIME_PLAN_GATE_SCHEMA,
    empty_model,
    validate_model_shape,
)


def test_empty_model_includes_runtime_plan_containers() -> None:
    model = empty_model()

    assert model["runtime_plans"] == []
    assert model["runtime_plan_unknowns"] == []
    assert model["runtime_plan_evidence_index"] == []
    assert model["runtime_plan_relationships"] == []
    assert model["runtime_plan_gate"] == {
        "schema": RUNTIME_PLAN_GATE_SCHEMA,
        "status": "NOT_BUILT",
        "entry_allowed": False,
        "runtime_plan_ready": False,
        "execution_allowed": False,
        "metrics": {},
    }


def test_persisted_pre_runtime_plan_model_remains_shape_compatible() -> None:
    model = empty_model()
    for key in (
        "runtime_plans",
        "runtime_plan_unknowns",
        "runtime_plan_evidence_index",
        "runtime_plan_relationships",
        "runtime_plan_gate",
    ):
        model.pop(key)

    assert validate_model_shape(model) == []


def test_runtime_plan_container_type_errors_are_visible() -> None:
    model = empty_model()
    model["runtime_plans"] = {}
    model["runtime_plan_gate"] = []

    violations = validate_model_shape(model)
    assert {row["field"] for row in violations} >= {
        "runtime_plans",
        "runtime_plan_gate",
    }
