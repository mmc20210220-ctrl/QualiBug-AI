from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center import _api
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.scenario_ir_asset_governance import (
    project_scenario_ir_asset_governance,
)


def _legacy_probe_asset(*, scenario_ready: bool) -> dict:
    return {
        "asset_id": "asset:test",
        "scenario_planning_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "scenario_planning_allowed": True,
            "execution_allowed": False,
        },
        "scenario_ir": [],
        "scenario_ir_unknowns": [],
        "scenario_ir_gate": {
            "status": "PASS" if scenario_ready else "BLOCKED_SCENARIO_IR_INCOMPLETE",
            "entry_allowed": scenario_ready,
            "scenario_ir_ready": scenario_ready,
            "execution_allowed": False,
            "metrics": {"scenario_count": 1 if scenario_ready else 0},
        },
        "interfaces": [
            {
                "interface_id": "api:POST:/orders/{id}/ship",
                "method": "POST",
                "path": "/orders/{id}/ship",
                "operation_id": "shipOrder",
            }
        ],
        "relationships": [
            {
                "edge_id": "edge:rule-interface",
                "from": "rule:ship",
                "to": "api:POST:/orders/{id}/ship",
                "relation": "rule_to_interface",
                "status": "accepted",
                "derivation": "exact_source_section",
                "evidence_gate": "exact_source_section",
                "evidence": {"operation_locator": "POST /orders/{id}/ship"},
            }
        ],
        "risk_domains": [
            {
                "risk_id": "risk:ship",
                "source_rule_id": "rule:ship",
                "risk_type": "business_logic",
                "severity": "P1",
                "title": "发货规则",
                "expected": "满足条件后允许发货",
            }
        ],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }


def test_scenario_ir_failure_blocks_legacy_probe_generation() -> None:
    asset = _legacy_probe_asset(scenario_ready=False)
    project_scenario_ir_asset_governance(asset, {})

    probes = _api._probes_from_asset(asset, 20)

    assert probes == []
    assert asset["governance"]["legacy_probe_generation_requires_scenario_ir_gate"] is True


def test_scenario_ir_pass_preserves_later_legacy_probe_compatibility() -> None:
    asset = _legacy_probe_asset(scenario_ready=True)
    project_scenario_ir_asset_governance(asset, {})

    probes = _api._probes_from_asset(asset, 20)

    assert len(probes) == 1
    assert probes[0]["path"] == "/orders/{id}/ship"
    assert probes[0]["execution_policy"] == "sandbox_required"
