from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import enterprise_knowledge_center
from ai_test_asset_center.private_pilot_command_center_understanding import (
    project_existing_understanding_command_center,
)
from ai_test_asset_center.private_pilot_understanding_preflight import (
    project_existing_understanding_preflight,
)


def _asset_with_blocked_materialization() -> dict:
    return {
        "source_inventory": [
            {
                "source_id": "source:openapi",
                "filename": "企业接口契约.yaml",
            }
        ],
        "summary": {
            "enterprise_understanding_model_id": "eum:materialization-gate",
            "enterprise_understanding_status": "PASS",
            "enterprise_understanding_ready": True,
            "scenario_ir_count": 1,
            "runtime_plan_count": 1,
            "runtime_materialization_count": 1,
        },
        "enterprise_understanding_model": {
            "model_id": "eum:materialization-gate",
            "business_objects": [{"object_id": "order"}],
            "actors": [{"actor_id": "warehouse-user"}],
            "operations": [{"operation_id": "ship-order"}],
            "unknowns": [],
            "conflicts": [],
            "gate": {
                "status": "PASS",
                "entry_allowed": True,
                "metrics": {"source_traceability_rate": 1.0},
            },
        },
        "scenario_planning_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "scenario_planning_allowed": True,
        },
        "scenario_ir": [{"scenario_id": "scenario:ship-order"}],
        "scenario_ir_gate": {"status": "PASS", "entry_allowed": True},
        "scenario_execution_contract_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "execution_contract_ready": True,
        },
        "runtime_plans": [{"plan_id": "runtime-plan:ship-order", "status": "TEMPLATE_READY"}],
        "runtime_plan_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "runtime_plan_ready": True,
        },
        "runtime_materializations": [
            {
                "materialization_id": "runtime-materialization:ship-order",
                "runtime_plan_ref": "runtime-plan:ship-order",
                "status": "INCOMPLETE",
                "execution_allowed": False,
            }
        ],
        "runtime_materialization_gate": {
            "status": "BLOCKED_RUNTIME_MATERIALIZATION_CRITICAL_UNKNOWN",
            "entry_allowed": False,
            "runtime_materialization_ready": False,
            "execution_allowed": False,
            "blocking_reasons": [
                "RUNTIME_MATERIALIZATION_CREDENTIAL_REF_UNRESOLVED"
            ],
            "metrics": {
                "runtime_materialization_count": 1,
                "ready_runtime_materialization_count": 0,
                "incomplete_runtime_materialization_count": 1,
                "runtime_materialization_unknown_count": 1,
            },
        },
        "runtime_materialization_unknowns": [
            {
                "unknown_id": "runtime-materialization-unknown:credential",
                "reason_code": "RUNTIME_MATERIALIZATION_CREDENTIAL_REF_UNRESOLVED",
                "message": "仓管员尚未绑定唯一测试凭据引用。",
                "blocks_runtime_materialization": True,
                "evidence": [
                    {
                        "source_id": "source:openapi",
                        "source_locator": "POST /orders/{order_id}/ship",
                        "quote": "该操作要求 bearerAuth。",
                    }
                ],
            }
        ],
    }


def test_preflight_blocks_when_first_five_gates_pass_but_materialization_is_blocked(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        enterprise_knowledge_center,
        "load_enterprise_business_knowledge_asset",
        lambda project, root: _asset_with_blocked_materialization(),
    )

    result = project_existing_understanding_preflight(
        {
            "ready": True,
            "reasons": [],
            "input_checks": {"sources": {"source_count": 1}},
        },
        project="customer_materialization",
        root=tmp_path,
    )

    assert result["ready"] is False
    assert "RUNTIME_MATERIALIZATION_BLOCKED" in result["blocking_codes"]
    summary = result["understanding_summary"]
    assert summary["ready"] is False
    assert summary["runtime_materialization_count"] == 1
    assert len(summary["gates"]) == 6
    assert summary["gates"][-1] == {
        "label": "运行实例化",
        "status": "BLOCKED_RUNTIME_MATERIALIZATION_CRITICAL_UNKNOWN",
        "ready": False,
    }
    assert "仓管员尚未绑定唯一测试凭据引用" in result["reasons"][-1]["message"]


def test_command_center_never_reports_ready_before_materialization_gate_passes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        enterprise_knowledge_center,
        "load_enterprise_business_knowledge_asset",
        lambda project, root: _asset_with_blocked_materialization(),
    )

    result = project_existing_understanding_command_center(
        {
            "project_id": "customer_materialization",
            "knowledge_summary": {"active_source_count": 1, "knowledge_ready": True},
        },
        project="customer_materialization",
        root=tmp_path,
    )
    summary = result["knowledge_summary"]

    assert summary["active_source_count"] == 1
    assert summary["formal_scenario_chain_ready"] is False
    assert summary["formal_runtime_chain_ready"] is False
    assert summary["runtime_materialization_ready"] is False
    assert summary["runtime_materialization_count"] == 1
    assert summary["runtime_materialization_unknown_count"] == 1
    assert len(summary["understanding_gates"]) == 6
    assert summary["understanding_gates"][-1]["key"] == "runtime_materialization"
    receipts = summary["understanding_blocker_receipts"]
    receipt = next(
        row for row in receipts if row["category"] == "runtime_materialization_unknown"
    )
    assert receipt["blocking"] is True
    assert receipt["source_backed"] is True
    assert receipt["source_evidence"][0]["source_name"] == "企业接口契约.yaml"
    assert receipt["source_evidence"][0]["source_locator"] == "POST /orders/{order_id}/ship"
