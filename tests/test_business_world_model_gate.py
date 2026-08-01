from __future__ import annotations

import ast
from pathlib import Path

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.business_world_model import (  # noqa: E501
    build_business_world_model,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.implementation_binding_projection import (  # noqa: E501
    build_final_scenario_planning_gate,
)
from tests.business_world_model_support import _model

ROOT = Path(__file__).resolve().parents[1]


def test_unresolved_authority_conflict_marks_node_and_blocks_downstream_admission() -> None:
    model = _model()
    model["gate"] = {
        "status": "BLOCKED_ENTERPRISE_UNDERSTANDING_CONFLICTING_FACTS",
        "entry_allowed": False,
    }
    model["conflicts"] = [
        {
            "conflict_id": "conflict:customer",
            "status": "UNRESOLVED",
            "candidate_entity_ids": ["entity:customer"],
        }
    ]

    world = build_business_world_model(model)

    objects = {row["node_id"]: row for row in world["object_nodes"]}
    assert objects["entity:customer"]["world_state"] == "CONFLICTED"
    assert objects["entity:order"]["world_state"] == "CONFIRMED"
    assert world["gate"]["status"] == "BLOCKED_UPSTREAM_UNDERSTANDING"
    assert world["gate"]["world_model_ready"] is True
    assert world["gate"]["entry_allowed"] is False
    assert world["gate"]["downstream_candidate_generation_allowed"] is False


def test_unresolved_reference_fails_world_model_integrity_closed() -> None:
    model = _model()
    model["operations"][0]["business_entity_refs"] = ["entity:missing"]

    world = build_business_world_model(model)

    assert world["gate"]["status"] == "BLOCKED_WORLD_MODEL_INTEGRITY"
    assert world["gate"]["entry_allowed"] is False
    assert world["gate"]["world_model_ready"] is False
    assert any(
        row["code"] == "WORLD_EDGE_SOURCE_UNRESOLVED"
        for row in world["gate"]["integrity_violations"]
    )


def test_label_only_lifecycle_authority_cannot_disappear_silently() -> None:
    model = _model()
    model["lifecycles"][0].pop("business_entity_ref")
    model["lifecycles"][0]["object_ref"] = "不存在对象"

    world = build_business_world_model(model)

    assert world["gate"]["status"] == "BLOCKED_WORLD_MODEL_INTEGRITY"
    assert any(
        row["code"] == "WORLD_AUTHORITY_OBJECT_REF_UNRESOLVED"
        and row["authority_collection"] == "lifecycles"
        and row["unresolved_object_labels"] == ["不存在对象"]
        for row in world["gate"]["integrity_violations"]
    )


def test_scenario_planning_requires_declared_world_model_integrity() -> None:
    model = _model()
    model["implementation_binding_gate"] = {
        "status": "PASS",
        "entry_allowed": True,
        "scenario_planning_allowed": True,
        "metrics": {
            "behavior_binding_count": 1,
            "scenario_ready_binding_count": 1,
        },
    }
    model["business_world_model"] = build_business_world_model(model)

    gate = build_final_scenario_planning_gate(model)

    assert gate["status"] == "PASS"
    assert gate["business_world_model_declared"] is True
    assert gate["business_world_model_ready"] is True
    assert gate["required_contract"][
        "business_world_model_reference_integrity_required"
    ] is True

    model["operations"][0]["business_entity_refs"] = ["entity:missing"]
    model["business_world_model"] = build_business_world_model(model)
    blocked = build_final_scenario_planning_gate(model)

    assert blocked["status"] == "BLOCKED_BUSINESS_WORLD_MODEL_INTEGRITY"
    assert blocked["entry_allowed"] is False
    assert blocked["business_world_model_ready"] is False
    assert "BUSINESS_WORLD_MODEL_NOT_READY" in blocked["blocking_reasons"]


def test_legacy_model_without_declared_world_view_remains_compatible() -> None:
    model = _model()
    model["implementation_binding_gate"] = {
        "status": "PASS",
        "entry_allowed": True,
        "scenario_planning_allowed": True,
        "metrics": {
            "behavior_binding_count": 1,
            "scenario_ready_binding_count": 1,
        },
    }
    model.pop("business_world_model", None)

    gate = build_final_scenario_planning_gate(model)

    assert gate["status"] == "PASS"
    assert gate["business_world_model_declared"] is False
    assert gate["business_world_model_status"] == "LEGACY_NOT_DECLARED"
    assert gate["business_world_model_ready"] is True


def test_integration_projects_world_model_after_identity_and_before_final_gate() -> None:
    source = (
        ROOT
        / "ai_test_asset_center"
        / "enterprise_knowledge_center"
        / "enterprise_understanding"
        / "integration"
        / "__init__.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "enrich_asset_with_enterprise_understanding"
    )
    calls: dict[str, int] = {}
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            calls[node.func.id] = node.lineno
        elif isinstance(node.func, ast.Attribute):
            calls[node.func.attr] = node.lineno

    assert calls["project_identity_to_downstream"] < calls["project_business_world_model"]
    assert calls["project_business_world_model"] < calls["project_final_scenario_planning_gate"]
