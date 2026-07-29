from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center import _api
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.scenario_ir_asset_governance import (
    project_scenario_ir_asset_governance,
)


def _legacy_probe_asset(*, contract_ready: bool) -> dict:
    return {
        "scenario_planning_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "scenario_planning_allowed": True,
            "execution_allowed": False,
        },
        "scenario_ir_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "scenario_ir_ready": True,
            "execution_allowed": False,
        },
        "scenario_execution_contract_gate": {
            "status": "PASS" if contract_ready else "BLOCKED_EXECUTION_CONTRACT_INCOMPLETE",
            "entry_allowed": contract_ready,
            "execution_contract_ready": contract_ready,
            "execution_allowed": False,
        },
        "interfaces": [
            {
                "interface_id": "interface:ship",
                "method": "POST",
                "path": "/orders/{order_id}/ship",
                "operation_id": "shipOrder",
            }
        ],
        "risk_domains": [
            {
                "risk_id": "risk:ship",
                "source_rule_id": "rule:ship",
                "risk_type": "business_logic",
                "severity": "P1",
                "title": "发货规则验证",
                "expected": "已审核订单才允许发货",
                "evidence": ["source:policy"],
            }
        ],
        "relationships": [
            {
                "edge_id": "edge:rule-ship-interface",
                "from": "rule:ship",
                "to": "interface:ship",
                "relation": "rule_to_interface",
                "status": "accepted",
                "confidence": 1.0,
                "derivation": "exact_source_section",
                "evidence_gate": "exact_source_section",
                "evidence": {"statement_hash": "abc"},
            }
        ],
    }


def test_execution_contract_gate_blocks_legacy_probe_generation() -> None:
    project_scenario_ir_asset_governance(
        {
            "scenario_ir": [],
            "scenario_ir_gate": {"status": "PASS", "entry_allowed": True},
            "summary": {},
            "governance": {},
            "coverage_gaps": [],
            "relationships": [],
        },
        {"source_summary": {}, "metrics": {}},
    )

    probes = _api._probes_from_asset(
        _legacy_probe_asset(contract_ready=False),
        10,
    )
    assert probes == []


def test_execution_contract_gate_pass_preserves_legacy_candidate_probe_compatibility() -> None:
    project_scenario_ir_asset_governance(
        {
            "scenario_ir": [],
            "scenario_ir_gate": {"status": "PASS", "entry_allowed": True},
            "summary": {},
            "governance": {},
            "coverage_gaps": [],
            "relationships": [],
        },
        {"source_summary": {}, "metrics": {}},
    )

    probes = _api._probes_from_asset(
        _legacy_probe_asset(contract_ready=True),
        10,
    )
    assert len(probes) == 1
    assert probes[0]["path"] == "/orders/{order_id}/ship"
    assert probes[0]["execution_policy"] == "sandbox_required"
