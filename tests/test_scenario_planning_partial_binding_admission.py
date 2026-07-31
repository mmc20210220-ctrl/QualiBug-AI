from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.implementation_binding_projection import (
    build_final_scenario_planning_gate,
)


def _model(
    *,
    semantic_ready: bool = True,
    implementation_status: str = "PARTIAL_IMPLEMENTATION_BINDING",
    binding_count: int = 3,
    ready_count: int = 1,
) -> dict:
    return {
        "gate": {
            "status": "PASS" if semantic_ready else "BLOCKED_ENTERPRISE_UNDERSTANDING_MODEL_INCOMPLETE",
            "entry_allowed": semantic_ready,
        },
        "implementation_binding_gate": {
            "status": implementation_status,
            "entry_allowed": implementation_status == "PASS",
            "scenario_planning_allowed": implementation_status == "PASS",
            "metrics": {
                "behavior_binding_count": binding_count,
                "scenario_ready_binding_count": ready_count,
            },
        },
    }


def test_ready_bindings_enter_planning_without_waiting_for_unready_bindings() -> None:
    gate = build_final_scenario_planning_gate(_model())

    assert gate["status"] == "PARTIAL_PASS_SCENARIO_PLANNING"
    assert gate["entry_allowed"] is True
    assert gate["scenario_planning_allowed"] is True
    assert gate["implementation_binding_full_ready"] is False
    assert gate["partial_binding_admission"] is True
    assert gate["admitted_ready_binding_count"] == 1
    assert gate["isolated_unready_binding_count"] == 2
    assert gate["required_contract"]["admission_scope"] == "PER_GOVERNED_BEHAVIOR_BINDING"
    assert gate["required_contract"]["unready_binding_can_block_ready_binding"] is False


def test_conflicted_binding_is_isolated_when_another_binding_is_ready() -> None:
    gate = build_final_scenario_planning_gate(
        _model(implementation_status="BLOCKED_IMPLEMENTATION_BINDING_CONFLICT")
    )

    assert gate["status"] == "PARTIAL_PASS_SCENARIO_PLANNING"
    assert gate["scenario_planning_allowed"] is True
    assert "IMPLEMENTATION_BINDING_CONFLICT_ISOLATED" in gate["isolated_reasons"]


def test_partial_gate_stays_closed_when_no_binding_is_ready() -> None:
    gate = build_final_scenario_planning_gate(_model(ready_count=0))

    assert gate["status"] == "PARTIAL_SCENARIO_PLANNING_IMPLEMENTATION_BINDING"
    assert gate["entry_allowed"] is False
    assert gate["scenario_planning_allowed"] is False
    assert gate["admitted_ready_binding_count"] == 0


def test_semantic_gate_still_blocks_all_implementation_bindings() -> None:
    gate = build_final_scenario_planning_gate(_model(semantic_ready=False))

    assert gate["status"] == "BLOCKED_SCENARIO_PLANNING_SEMANTIC_GATE"
    assert gate["entry_allowed"] is False
    assert gate["scenario_planning_allowed"] is False
    assert gate["admitted_ready_binding_count"] == 0


def test_full_implementation_pass_remains_unchanged() -> None:
    gate = build_final_scenario_planning_gate(
        _model(
            implementation_status="PASS",
            binding_count=3,
            ready_count=3,
        )
    )

    assert gate["status"] == "PASS"
    assert gate["entry_allowed"] is True
    assert gate["implementation_binding_full_ready"] is True
    assert gate["partial_binding_admission"] is False
    assert gate["admitted_ready_binding_count"] == 3
    assert gate["isolated_unready_binding_count"] == 0
