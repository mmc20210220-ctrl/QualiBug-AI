from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import enterprise_knowledge_center
from ai_test_asset_center.private_pilot_command_center_understanding import (
    UnderstandingCommandCenterProjectionMixin,
    project_existing_understanding_command_center,
)


def _asset() -> dict:
    return {
        "summary": {
            "enterprise_understanding_model_id": "eum_customer_a",
            "enterprise_understanding_status": "PASS",
            "enterprise_understanding_ready": True,
            "understood_business_object_count": 5,
            "understood_actor_count": 3,
            "understood_operation_count": 11,
            "understood_object_relation_count": 4,
            "understood_lifecycle_count": 2,
            "understood_process_count": 1,
            "enterprise_understanding_unknown_count": 0,
            "enterprise_understanding_conflict_count": 0,
            "scenario_ir_count": 7,
        },
        "enterprise_understanding_model": {
            "model_id": "eum_customer_a",
            "business_objects": [{"object_id": "order"}],
            "gate": {
                "status": "PASS",
                "entry_allowed": True,
                "metrics": {
                    "source_traceability_rate": 0.95,
                    "operation_object_binding_rate": 1.0,
                    "lifecycle_completeness": 0.8,
                },
            },
        },
        "scenario_planning_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "scenario_planning_allowed": True,
        },
        "scenario_ir_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "metrics": {
                "scenario_count": 7,
                "plannable_scenario_count": 7,
                "incomplete_scenario_count": 0,
            },
        },
        "scenario_execution_contract_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "execution_contract_ready": True,
            "metrics": {
                "execution_contract_count": 7,
                "incomplete_execution_contract_count": 0,
                "execution_contract_unknown_count": 0,
            },
        },
    }


def test_command_center_enriches_existing_knowledge_summary_without_replacing_it(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        enterprise_knowledge_center,
        "load_enterprise_business_knowledge_asset",
        lambda project, root: _asset(),
    )
    payload = {
        "project_id": "customer_a",
        "knowledge_summary": {
            "active_source_count": 6,
            "rule_count": 20,
            "knowledge_ready": True,
        },
    }

    result = project_existing_understanding_command_center(
        payload,
        project="customer_a",
        root=tmp_path,
    )

    summary = result["knowledge_summary"]
    assert summary["active_source_count"] == 6
    assert summary["rule_count"] == 20
    assert summary["enterprise_understanding_model_id"] == "eum_customer_a"
    assert summary["understood_business_object_count"] == 5
    assert summary["scenario_ir_count"] == 7
    assert summary["scenario_execution_contract_count"] == 7
    assert summary["formal_scenario_chain_ready"] is True
    assert summary["understanding_source_of_truth"] == "existing_enterprise_business_knowledge_asset"
    assert summary["understanding_projection_contract"] == "EXISTING_KNOWLEDGE_ASSET_GATE_PROJECTION_NOT_SECOND_AUTHORITY"


def test_command_center_projects_existing_blocker_without_claiming_readiness(
    monkeypatch,
    tmp_path: Path,
) -> None:
    asset = _asset()
    asset["enterprise_understanding_model"]["gate"] = {
        "status": "BLOCKED_ENTERPRISE_UNDERSTANDING_CRITICAL_UNKNOWN",
        "entry_allowed": False,
        "critical_unknowns": [
            {
                "reason_code": "OPERATION_OBJECT_UNRESOLVED",
                "blocks_formal_understanding": True,
            }
        ],
    }
    asset["summary"]["enterprise_understanding_status"] = "BLOCKED_ENTERPRISE_UNDERSTANDING_CRITICAL_UNKNOWN"
    asset["summary"]["enterprise_understanding_ready"] = False
    asset["scenario_planning_gate"] = {
        "status": "BLOCKED_SCENARIO_PLANNING_SEMANTIC_GATE",
        "entry_allowed": False,
        "scenario_planning_allowed": False,
        "blocking_reasons": ["SEMANTIC_UNDERSTANDING_NOT_CLOSED"],
    }
    asset["scenario_ir_gate"] = {
        "status": "BLOCKED_SCENARIO_IR_UPSTREAM_GATE",
        "entry_allowed": False,
    }
    asset["scenario_execution_contract_gate"] = {
        "status": "BLOCKED_EXECUTION_CONTRACT_UPSTREAM_SCENARIO_IR_GATE",
        "entry_allowed": False,
        "execution_contract_ready": False,
    }
    monkeypatch.setattr(
        enterprise_knowledge_center,
        "load_enterprise_business_knowledge_asset",
        lambda project, root: asset,
    )

    result = project_existing_understanding_command_center(
        {"knowledge_summary": {"active_source_count": 3}},
        project="customer_b",
        root=tmp_path,
    )
    summary = result["knowledge_summary"]

    assert summary["formal_scenario_chain_ready"] is False
    assert summary["enterprise_understanding_ready"] is False
    assert "部分业务操作尚未确定唯一作用对象" in summary["understanding_blockers"]
    assert summary["understanding_gates"][0]["ready"] is False


def test_command_center_mixin_preserves_original_builder_result(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        enterprise_knowledge_center,
        "load_enterprise_business_knowledge_asset",
        lambda project, root: _asset(),
    )

    class ExistingBuilder:
        def _build_command_center(self, project_id: str, root: Path) -> dict:
            return {
                "project_id": project_id,
                "defects": [{"id": "bug-1"}],
                "knowledge_summary": {"active_source_count": 2},
            }

    class Handler(UnderstandingCommandCenterProjectionMixin, ExistingBuilder):
        pass

    result = Handler()._build_command_center("customer_c", tmp_path)

    assert result["defects"] == [{"id": "bug-1"}]
    assert result["knowledge_summary"]["active_source_count"] == 2
    assert result["knowledge_summary"]["formal_scenario_chain_ready"] is True
