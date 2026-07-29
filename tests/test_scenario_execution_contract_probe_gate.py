from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center import _api, _linking
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.scenario_execution_probe_guard import (
    install_scenario_execution_probe_guard,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.scenario_ir_asset_governance import (
    project_scenario_ir_asset_governance,
)


def _legacy_probe_asset(
    *,
    contract_ready: bool,
    runtime_ready: bool,
    materialization_ready: bool,
) -> dict:
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
        "runtime_plan_gate": {
            "status": "PASS" if runtime_ready else "BLOCKED_RUNTIME_PLAN_INCOMPLETE",
            "entry_allowed": runtime_ready,
            "runtime_plan_ready": runtime_ready,
            "execution_allowed": False,
        },
        "runtime_materialization_gate": {
            "status": (
                "PASS"
                if materialization_ready
                else "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
            ),
            "entry_allowed": materialization_ready,
            "runtime_materialization_ready": materialization_ready,
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


def _install_guard() -> None:
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
    install_scenario_execution_probe_guard()


def test_execution_contract_gate_blocks_legacy_probe_generation() -> None:
    _install_guard()
    probes = _api._probes_from_asset(
        _legacy_probe_asset(
            contract_ready=False,
            runtime_ready=False,
            materialization_ready=False,
        ),
        10,
    )
    assert probes == []


def test_runtime_plan_gate_blocks_legacy_probe_generation() -> None:
    _install_guard()
    probes = _api._probes_from_asset(
        _legacy_probe_asset(
            contract_ready=True,
            runtime_ready=False,
            materialization_ready=False,
        ),
        10,
    )
    assert probes == []


def test_runtime_materialization_gate_blocks_legacy_probe_generation() -> None:
    _install_guard()
    probes = _api._probes_from_asset(
        _legacy_probe_asset(
            contract_ready=True,
            runtime_ready=True,
            materialization_ready=False,
        ),
        10,
    )
    assert probes == []


def test_direct_linking_probe_entrypoint_is_also_fail_closed() -> None:
    _install_guard()
    probes = _linking._probes_from_asset(
        _legacy_probe_asset(
            contract_ready=True,
            runtime_ready=True,
            materialization_ready=False,
        ),
        10,
    )
    assert probes == []


def test_all_six_gates_preserve_legacy_candidate_probe_compatibility() -> None:
    _install_guard()
    probes = _api._probes_from_asset(
        _legacy_probe_asset(
            contract_ready=True,
            runtime_ready=True,
            materialization_ready=True,
        ),
        10,
    )
    assert len(probes) == 1
    assert probes[0]["path"] == "/orders/{order_id}/ship"
    assert probes[0]["execution_policy"] == "sandbox_required"
