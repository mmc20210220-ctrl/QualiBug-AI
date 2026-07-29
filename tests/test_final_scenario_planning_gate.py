from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import integration
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.implementation_binding_projection import (
    build_final_scenario_planning_gate,
    project_final_scenario_planning_gate,
)


def _model(*, semantic_ready: bool, implementation_status: str) -> dict:
    implementation_ready = implementation_status == "PASS"
    return {
        "model_id": "enterprise_understanding:test",
        "gate": {
            "status": "PASS" if semantic_ready else "BLOCKED_ENTERPRISE_UNDERSTANDING_CRITICAL_UNKNOWN",
            "entry_allowed": semantic_ready,
            "critical_unknowns": [] if semantic_ready else [{"kind": "TEST_UNKNOWN"}],
        },
        "implementation_binding_gate": {
            "schema": "qualibug.business-behavior-implementation-binding-gate.v1",
            "status": implementation_status,
            "entry_allowed": implementation_ready,
            "scenario_planning_allowed": implementation_ready,
            "execution_allowed": False,
            "metrics": {
                "behavior_binding_count": 1,
                "scenario_ready_binding_count": 1 if implementation_ready else 0,
            },
        },
        "business_objects": [],
        "actors": [],
        "operations": [],
        "object_relations": [],
        "lifecycles": [],
        "processes": [],
        "unknowns": [],
        "conflicts": [],
        "metrics": {},
    }


def test_final_gate_passes_only_when_semantic_and_implementation_gates_pass() -> None:
    gate = build_final_scenario_planning_gate(
        _model(semantic_ready=True, implementation_status="PASS")
    )

    assert gate["status"] == "PASS"
    assert gate["entry_allowed"] is True
    assert gate["scenario_planning_allowed"] is True
    assert gate["execution_allowed"] is False
    assert gate["request_payload_compiled"] is False
    assert gate["expected_assertion_compiled"] is False
    assert gate["runtime_environment_validated"] is False


def test_semantic_gate_blocks_scenario_even_when_implementation_binding_passes() -> None:
    asset = {
        "summary": {
            "scenario_planning_allowed": True,
            "implementation_binding_status": "PASS",
        },
        "governance": {},
        "coverage_gaps": [
            {
                "kind": "IMPLEMENTATION_BINDING_PARTIAL",
                "source_id": "*",
            }
        ],
    }
    model = _model(semantic_ready=False, implementation_status="PASS")

    project_final_scenario_planning_gate(asset, model)
    project_final_scenario_planning_gate(asset, model)

    gate = asset["scenario_planning_gate"]
    assert gate["status"] == "BLOCKED_SCENARIO_PLANNING_SEMANTIC_GATE"
    assert gate["implementation_binding_ready"] is True
    assert gate["semantic_understanding_ready"] is False
    assert gate["scenario_planning_allowed"] is False
    assert asset["summary"]["scenario_planning_allowed"] is False
    assert asset["summary"]["implementation_binding_ready"] is True
    assert asset["summary"]["implementation_execution_allowed"] is False
    assert asset["governance"]["implementation_binding_cannot_override_semantic_unknowns"] is True
    assert sum(
        1
        for row in asset["coverage_gaps"]
        if row.get("kind") == "BLOCKED_SCENARIO_PLANNING_SEMANTIC_GATE"
    ) == 1
    assert any(
        row.get("kind") == "IMPLEMENTATION_BINDING_PARTIAL"
        for row in asset["coverage_gaps"]
    )


def test_partial_implementation_binding_keeps_scenario_planning_closed() -> None:
    asset = {
        "summary": {},
        "governance": {},
        "coverage_gaps": [
            {
                "kind": "IMPLEMENTATION_BINDING_PARTIAL",
                "source_id": "*",
            }
        ],
    }
    model = _model(
        semantic_ready=True,
        implementation_status="PARTIAL_IMPLEMENTATION_BINDING",
    )

    project_final_scenario_planning_gate(asset, model)

    assert asset["scenario_planning_gate"]["status"] == (
        "PARTIAL_SCENARIO_PLANNING_IMPLEMENTATION_BINDING"
    )
    assert asset["scenario_planning_gate"]["entry_allowed"] is False
    assert asset["summary"]["implementation_binding_ready"] is False
    assert asset["summary"]["scenario_planning_ready"] is False
    assert not any(
        row.get("kind") == "BLOCKED_SCENARIO_PLANNING_SEMANTIC_GATE"
        for row in asset["coverage_gaps"]
    )


def test_integration_projects_final_gate_before_asset_is_returned(monkeypatch) -> None:
    final_model = _model(semantic_ready=True, implementation_status="PASS")
    asset = {
        "enterprise_comprehension_gate": {
            "status": "PASS",
            "entry_allowed": True,
        },
        "summary": {},
        "governance": {},
        "coverage_gaps": [],
    }

    monkeypatch.setattr(
        integration,
        "build_enterprise_understanding_model",
        lambda _asset: {},
    )
    monkeypatch.setattr(
        integration,
        "apply_minimum_understanding_closure",
        lambda _model, _asset: final_model,
    )

    result = integration.enrich_asset_with_enterprise_understanding(
        asset,
        parsed_sources=None,
    )

    assert result["scenario_planning_gate"]["status"] == "PASS"
    assert result["scenario_planning_gate"]["execution_allowed"] is False
    assert result["summary"]["scenario_planning_ready"] is True
    assert result["enterprise_understanding_model"] is final_model
