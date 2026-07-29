from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import enterprise_knowledge_center
from ai_test_asset_center.private_pilot_command_center_understanding import (
    project_existing_understanding_command_center,
)


def _blocked_asset() -> dict:
    return {
        "source_inventory": [
            {
                "source_id": "src_order_rules",
                "filename": "订单业务规则.docx",
            }
        ],
        "summary": {
            "enterprise_understanding_model_id": "eum_receipt",
            "enterprise_understanding_status": "BLOCKED_ENTERPRISE_UNDERSTANDING_CRITICAL_UNKNOWN",
            "enterprise_understanding_ready": False,
        },
        "enterprise_understanding_model": {
            "model_id": "eum_receipt",
            "gate": {
                "status": "BLOCKED_ENTERPRISE_UNDERSTANDING_CRITICAL_UNKNOWN",
                "entry_allowed": False,
                "critical_unknowns": [
                    {
                        "unknown_id": "unknown_order_payment",
                        "reason_code": "OPERATION_OBJECT_UNRESOLVED",
                        "message": "支付操作尚未确定唯一订单对象。",
                        "blocks_formal_understanding": True,
                        "evidence": [
                            {
                                "source_id": "src_order_rules",
                                "source_locator": "第 4.2 节",
                                "quote": "订单支付成功后，不允许重复支付。",
                                "fact_id": "fact_order_payment_once",
                            }
                        ],
                    }
                ],
            },
            "unknowns": [],
            "conflicts": [],
        },
        "scenario_planning_gate": {
            "status": "BLOCKED_SCENARIO_PLANNING_SEMANTIC_GATE",
            "entry_allowed": False,
            "scenario_planning_allowed": False,
        },
        "scenario_ir_gate": {
            "status": "BLOCKED_SCENARIO_IR_UPSTREAM_GATE",
            "entry_allowed": False,
        },
        "scenario_execution_contract_gate": {
            "status": "BLOCKED_EXECUTION_CONTRACT_UPSTREAM_SCENARIO_IR_GATE",
            "entry_allowed": False,
            "execution_contract_ready": False,
        },
        "runtime_plan_gate": {
            "status": "BLOCKED_RUNTIME_PLAN_UPSTREAM_EXECUTION_CONTRACT_GATE",
            "entry_allowed": False,
            "runtime_plan_ready": False,
        },
    }


def test_command_center_projects_existing_source_locator_and_quote(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        enterprise_knowledge_center,
        "load_enterprise_business_knowledge_asset",
        lambda project, root: _blocked_asset(),
    )

    result = project_existing_understanding_command_center(
        {"knowledge_summary": {"active_source_count": 1}},
        project="customer_receipt",
        root=tmp_path,
    )
    summary = result["knowledge_summary"]
    receipts = summary["understanding_blocker_receipts"]

    assert summary["understanding_source_receipt_count"] == 1
    assert len(receipts) == 1
    assert receipts[0]["message"] == "支付操作尚未确定唯一订单对象。"
    assert receipts[0]["source_backed"] is True
    assert receipts[0]["source_evidence"] == [
        {
            "source_id": "src_order_rules",
            "source_name": "订单业务规则.docx",
            "source_locator": "第 4.2 节",
            "quote": "订单支付成功后，不允许重复支付。",
            "fact_id": "fact_order_payment_once",
        }
    ]


def test_command_center_does_not_invent_source_for_unbacked_runtime_gap(
    monkeypatch,
    tmp_path: Path,
) -> None:
    asset = _blocked_asset()
    asset["summary"] = {
        "enterprise_understanding_model_id": "eum_runtime_gap",
        "enterprise_understanding_status": "PASS",
        "enterprise_understanding_ready": True,
    }
    asset["enterprise_understanding_model"] = {
        "model_id": "eum_runtime_gap",
        "gate": {"status": "PASS", "entry_allowed": True},
        "unknowns": [],
        "conflicts": [],
    }
    asset["scenario_planning_gate"] = {
        "status": "PASS",
        "entry_allowed": True,
        "scenario_planning_allowed": True,
    }
    asset["scenario_ir_gate"] = {"status": "PASS", "entry_allowed": True}
    asset["scenario_execution_contract_gate"] = {
        "status": "PASS",
        "entry_allowed": True,
        "execution_contract_ready": True,
    }
    asset["runtime_plan_gate"] = {
        "status": "BLOCKED_RUNTIME_PLAN_INCOMPLETE",
        "entry_allowed": False,
        "runtime_plan_ready": False,
    }
    asset["runtime_plan_unknowns"] = [
        {
            "runtime_plan_unknown_id": "runtime_cleanup_gap",
            "reason_code": "RUNTIME_PLAN_CLEANUP_TEMPLATE_UNRESOLVED",
            "blocks_runtime_plan": True,
        }
    ]
    monkeypatch.setattr(
        enterprise_knowledge_center,
        "load_enterprise_business_knowledge_asset",
        lambda project, root: asset,
    )

    result = project_existing_understanding_command_center(
        {"knowledge_summary": {"active_source_count": 1}},
        project="customer_runtime_gap",
        root=tmp_path,
    )
    summary = result["knowledge_summary"]
    receipts = summary["understanding_blocker_receipts"]

    assert summary["understanding_source_receipt_count"] == 0
    assert len(receipts) == 1
    assert receipts[0]["message"] == "写操作尚未形成安全清理模板"
    assert receipts[0]["source_backed"] is False
    assert receipts[0]["source_evidence"] == []


def test_command_center_projects_unresolved_conflict_opposing_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    asset = {
        "source_inventory": [
            {"source_id": "policy_v1", "filename": "采购规则_v1.md"},
            {"source_id": "policy_v2", "filename": "采购规则_v2.md"},
        ],
        "summary": {
            "enterprise_understanding_model_id": "eum_conflict",
            "enterprise_understanding_status": "BLOCKED_ENTERPRISE_UNDERSTANDING_CONFLICTING_FACTS",
            "enterprise_understanding_ready": False,
            "enterprise_understanding_conflict_count": 1,
        },
        "enterprise_understanding_model": {
            "model_id": "eum_conflict",
            "gate": {
                "status": "BLOCKED_ENTERPRISE_UNDERSTANDING_CONFLICTING_FACTS",
                "entry_allowed": False,
                "blocking_reasons": ["UNRESOLVED_BUSINESS_FACT_OR_BEHAVIOR_CONFLICTS"],
                "critical_unknowns": [],
                "unresolved_conflicts": [
                    {
                        "conflict_id": "conflict_modality_1",
                        "kind": "BUSINESS_MODALITY_CONTRADICTION",
                        "status": "UNRESOLVED",
                        "reason": (
                            "same subject/action/condition/scope has incompatible "
                            "modalities MUST_NOT and MAY"
                        ),
                        "automatic_resolution_allowed": False,
                        "resolution_policy": (
                            "explicit source authority/version decision required; "
                            "recency, filename, document order and model confidence "
                            "are not authority"
                        ),
                        "authority_decision": {
                            "status": "UNRESOLVED",
                            "selected_fact_id": "",
                            "operator_required": True,
                            "automatic_resolution_allowed": False,
                        },
                        "facts": [
                            {
                                "fact_id": "fact_deny",
                                "source_id": "policy_v1",
                                "source_locator": "采购规则_v1.md#规则",
                                "quote": "普通用户不得修改已提交采购申请",
                                "modality": "MUST_NOT",
                                "statement": "普通用户不得修改已提交采购申请",
                            },
                            {
                                "fact_id": "fact_allow",
                                "source_id": "policy_v2",
                                "source_locator": "采购规则_v2.md#规则",
                                "quote": "普通用户可以修改已提交采购申请",
                                "modality": "MAY",
                                "statement": "普通用户可以修改已提交采购申请",
                            },
                        ],
                    }
                ],
            },
            "unknowns": [],
            "conflicts": [],
        },
        "scenario_planning_gate": {
            "status": "BLOCKED_SCENARIO_PLANNING_SEMANTIC_GATE",
            "entry_allowed": False,
            "scenario_planning_allowed": False,
        },
        "scenario_ir_gate": {
            "status": "BLOCKED_SCENARIO_IR_UPSTREAM_GATE",
            "entry_allowed": False,
        },
        "scenario_execution_contract_gate": {
            "status": "BLOCKED_EXECUTION_CONTRACT_UPSTREAM_SCENARIO_IR_GATE",
            "entry_allowed": False,
            "execution_contract_ready": False,
        },
        "runtime_plan_gate": {
            "status": "BLOCKED_RUNTIME_PLAN_UPSTREAM_EXECUTION_CONTRACT_GATE",
            "entry_allowed": False,
            "runtime_plan_ready": False,
        },
    }
    monkeypatch.setattr(
        enterprise_knowledge_center,
        "load_enterprise_business_knowledge_asset",
        lambda project, root: asset,
    )

    result = project_existing_understanding_command_center(
        {"knowledge_summary": {"active_source_count": 2}},
        project="customer_conflict_receipt",
        root=tmp_path,
    )
    summary = result["knowledge_summary"]
    receipts = summary["understanding_blocker_receipts"]

    assert summary["understanding_source_receipt_count"] >= 1
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["category"] == "source_conflict"
    assert "MUST_NOT" in receipt["message"] and "MAY" in receipt["message"]
    assert receipt["source_backed"] is True
    assert receipt["blocking"] is True
    assert "recency" in receipt["operator_action"]
    quotes = {row["quote"] for row in receipt["source_evidence"]}
    assert "普通用户不得修改已提交采购申请" in quotes
    assert "普通用户可以修改已提交采购申请" in quotes
    names = {row["source_name"] for row in receipt["source_evidence"]}
    assert "采购规则_v1.md" in names
    assert "采购规则_v2.md" in names
