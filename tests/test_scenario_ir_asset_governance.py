from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.scenario_ir_asset_governance import (
    project_scenario_ir_asset_governance,
)


def _scenario(*, status: str = "PLANNABLE") -> dict:
    return {
        "scenario_id": "scenario:ship",
        "scenario_type": "POSITIVE",
        "status": status,
        "behavior_ref": "behavior:ship",
        "implementation_binding_ref": "binding:ship",
        "action_entry": {
            "interface_id": "api:POST:/orders/{id}/ship",
            "method": "POST",
            "path": "/orders/{id}/ship",
            "authoritative": True,
            "derivation": "authoritative_relationship",
        },
        "evidence": [
            {
                "source_id": "prd",
                "source_locator": "prd.md#ship",
                "fact_id": "fact:ship",
                "derivation": "source_span",
            }
        ],
        "execution_ready": False,
    }


def test_plannable_scenario_projects_accepted_behavior_and_interface_edges() -> None:
    asset = {
        "scenario_ir": [_scenario()],
        "scenario_ir_unknowns": [],
        "scenario_ir_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "metrics": {
                "positive_scenario_count": 1,
                "rejection_scenario_count": 0,
                "unauthorized_scenario_count": 0,
            },
        },
        "relationships": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }
    model = {}

    project_scenario_ir_asset_governance(asset, model)

    edges = {row["relation"]: row for row in asset["scenario_ir_relationships"]}
    assert edges["behavior_to_scenario_ir"]["status"] == "accepted"
    assert edges["scenario_ir_to_interface"]["status"] == "accepted"
    assert edges["scenario_ir_to_interface"]["evidence"]["execution_ready"] is False
    assert len(asset["relationships"]) == 2
    assert asset["coverage_gaps"] == []
    assert asset["summary"]["scenario_ir_relationship_count"] == 2
    assert asset["summary"]["scenario_ir_positive_count"] == 1
    assert model["scenario_ir_relationships"] == asset["scenario_ir_relationships"]


def test_incomplete_scenario_relationships_remain_candidates() -> None:
    asset = {
        "scenario_ir": [_scenario(status="INCOMPLETE")],
        "scenario_ir_unknowns": [{"kind": "SCENARIO_EXPECTED_OUTCOME_UNRESOLVED"}],
        "scenario_ir_gate": {
            "status": "BLOCKED_SCENARIO_IR_INCOMPLETE",
            "entry_allowed": False,
            "upstream_scenario_planning_status": "PASS",
            "metrics": {"scenario_count": 1},
        },
        "relationships": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }

    project_scenario_ir_asset_governance(asset, {})

    assert all(
        row["status"] == "candidate" for row in asset["scenario_ir_relationships"]
    )
    gap = next(row for row in asset["coverage_gaps"] if row["kind"] == "SCENARIO_IR_INCOMPLETE")
    assert gap["execution_allowed"] is False
    assert gap["scenario_ir_unknown_count"] == 1


def test_upstream_blocked_gap_is_idempotent() -> None:
    asset = {
        "scenario_ir": [],
        "scenario_ir_unknowns": [],
        "scenario_ir_gate": {
            "status": "BLOCKED_SCENARIO_IR_UPSTREAM_GATE",
            "entry_allowed": False,
            "upstream_scenario_planning_status": "PARTIAL_SCENARIO_PLANNING_IMPLEMENTATION_BINDING",
            "metrics": {"scenario_count": 0},
        },
        "relationships": [],
        "coverage_gaps": [
            {"kind": "SCENARIO_IR_UPSTREAM_BLOCKED", "source_id": "*"},
            {"kind": "OTHER_GAP", "source_id": "source-1"},
        ],
        "summary": {},
        "governance": {},
    }

    project_scenario_ir_asset_governance(asset, {})
    first = deepcopy(asset["coverage_gaps"])
    project_scenario_ir_asset_governance(asset, {})

    assert asset["coverage_gaps"] == first
    assert sum(
        1
        for row in asset["coverage_gaps"]
        if row.get("kind") == "SCENARIO_IR_UPSTREAM_BLOCKED"
    ) == 1
    assert any(row.get("kind") == "OTHER_GAP" for row in asset["coverage_gaps"])
    assert asset["governance"]["scenario_ir_projection_is_idempotent"] is True
