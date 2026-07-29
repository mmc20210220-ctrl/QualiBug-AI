from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import enterprise_knowledge_center
from ai_test_asset_center.private_pilot_understanding_preflight import (
    project_existing_understanding_preflight,
)


def _base_payload() -> dict:
    return {
        "ok": True,
        "ready": True,
        "blocking_codes": [],
        "reasons": [],
        "input_checks": {
            "sources": {"status": "passed", "source_count": 3},
        },
    }


def _passed_downstream_gates() -> dict:
    return {
        "scenario_planning_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "scenario_planning_allowed": True,
        },
        "scenario_ir_gate": {"status": "PASS", "entry_allowed": True},
        "scenario_execution_contract_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "execution_contract_ready": True,
        },
        "runtime_plan_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "runtime_plan_ready": True,
            "execution_allowed": False,
        },
        "runtime_materialization_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "runtime_materialization_ready": True,
            "execution_allowed": False,
            "request_sendable": False,
        },
    }


def test_understanding_preflight_reuses_existing_passed_gates(
    monkeypatch, tmp_path: Path
) -> None:
    asset = {
        "summary": {
            "enterprise_understanding_model_id": "eum_passed",
            "enterprise_understanding_status": "PASS",
            "enterprise_understanding_ready": True,
            "understood_business_object_count": 4,
            "understood_actor_count": 2,
            "understood_operation_count": 7,
            "scenario_ir_count": 5,
            "runtime_plan_count": 5,
            "runtime_materialization_count": 5,
        },
        "enterprise_understanding_model": {
            "model_id": "eum_passed",
            "gate": {"status": "PASS", "entry_allowed": True},
        },
        **_passed_downstream_gates(),
    }
    monkeypatch.setattr(
        enterprise_knowledge_center,
        "load_enterprise_business_knowledge_asset",
        lambda project, root: asset,
    )

    result = project_existing_understanding_preflight(
        _base_payload(),
        project="customer_a",
        root=tmp_path,
    )

    assert result["ready"] is True
    assert result["blocking_codes"] == []
    assert (
        result["understanding_summary"]["source_of_truth"]
        == "existing_enterprise_business_knowledge_asset"
    )
    assert result["understanding_summary"]["model_id"] == "eum_passed"
    assert result["understanding_summary"]["runtime_plan_count"] == 5
    assert result["understanding_summary"]["runtime_materialization_count"] == 5
    assert result["input_checks"]["enterprise_understanding"]["status"] == "passed"
    assert result["understanding_summary"]["gates"][-1] == {
        "label": "运行实例化",
        "status": "PASS",
        "ready": True,
    }


def test_understanding_preflight_surfaces_first_existing_blocker(
    monkeypatch, tmp_path: Path
) -> None:
    asset = {
        "summary": {
            "enterprise_understanding_model_id": "eum_blocked",
            "enterprise_understanding_status": "BLOCKED_ENTERPRISE_UNDERSTANDING_CRITICAL_UNKNOWN",
            "enterprise_understanding_ready": False,
            "enterprise_understanding_unknown_count": 1,
        },
        "enterprise_understanding_model": {
            "model_id": "eum_blocked",
            "unknowns": [
                {
                    "kind": "OPERATION_OBJECT_UNRESOLVED",
                    "reason_code": "OPERATION_OBJECT_UNRESOLVED",
                    "blocks_formal_understanding": True,
                }
            ],
            "gate": {
                "status": "BLOCKED_ENTERPRISE_UNDERSTANDING_CRITICAL_UNKNOWN",
                "entry_allowed": False,
                "blocking_reasons": ["OPERATION_OBJECT_UNRESOLVED"],
                "critical_unknowns": [
                    {
                        "kind": "OPERATION_OBJECT_UNRESOLVED",
                        "reason_code": "OPERATION_OBJECT_UNRESOLVED",
                    }
                ],
            },
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
        "runtime_materialization_gate": {
            "status": "BLOCKED_RUNTIME_MATERIALIZATION_UPSTREAM_PLAN_GATE",
            "entry_allowed": False,
            "runtime_materialization_ready": False,
        },
    }
    monkeypatch.setattr(
        enterprise_knowledge_center,
        "load_enterprise_business_knowledge_asset",
        lambda project, root: asset,
    )

    result = project_existing_understanding_preflight(
        _base_payload(),
        project="customer_b",
        root=tmp_path,
    )

    assert result["ready"] is False
    assert result["blocking_codes"] == ["ENTERPRISE_UNDERSTANDING_BLOCKED"]
    assert result["input_checks"]["enterprise_understanding"]["status"] == "blocked"
    assert "部分业务操作尚未确定唯一作用对象" in result["reasons"][0]["message"]
    assert "人工确认" in result["reasons"][0]["message"]


def test_runtime_plan_is_the_first_blocker_after_prior_gates_pass(
    monkeypatch, tmp_path: Path
) -> None:
    asset = {
        "summary": {
            "enterprise_understanding_model_id": "eum_runtime_blocked",
            "enterprise_understanding_status": "PASS",
            "enterprise_understanding_ready": True,
        },
        "enterprise_understanding_model": {
            "model_id": "eum_runtime_blocked",
            "gate": {"status": "PASS", "entry_allowed": True},
        },
        **_passed_downstream_gates(),
        "runtime_plan_gate": {
            "status": "BLOCKED_RUNTIME_PLAN_INCOMPLETE",
            "entry_allowed": False,
            "runtime_plan_ready": False,
        },
        "runtime_materialization_gate": {
            "status": "BLOCKED_RUNTIME_MATERIALIZATION_UPSTREAM_PLAN_GATE",
            "entry_allowed": False,
            "runtime_materialization_ready": False,
        },
        "runtime_plan_unknowns": [
            {
                "kind": "RUNTIME_PLAN_REQUEST_FIELD_LOCATION_UNRESOLVED",
                "reason_code": "RUNTIME_PLAN_REQUEST_FIELD_LOCATION_UNRESOLVED",
                "blocks_runtime_plan": True,
            }
        ],
    }
    monkeypatch.setattr(
        enterprise_knowledge_center,
        "load_enterprise_business_knowledge_asset",
        lambda project, root: asset,
    )

    result = project_existing_understanding_preflight(
        _base_payload(),
        project="customer_runtime_blocked",
        root=tmp_path,
    )

    assert result["ready"] is False
    assert result["blocking_codes"] == ["RUNTIME_PLAN_BLOCKED"]
    runtime_gate = next(
        row
        for row in result["understanding_summary"]["gates"]
        if row["label"] == "Runtime Plan"
    )
    assert runtime_gate == {
        "label": "Runtime Plan",
        "status": "BLOCKED_RUNTIME_PLAN_INCOMPLETE",
        "ready": False,
    }
    assert "请求字段在接口契约中的位置尚未明确" in result["reasons"][0]["message"]


def test_runtime_materialization_is_first_blocker_after_plan_passes(
    monkeypatch, tmp_path: Path
) -> None:
    asset = {
        "summary": {
            "enterprise_understanding_model_id": "eum_materialization_blocked",
            "enterprise_understanding_status": "PASS",
            "enterprise_understanding_ready": True,
            "runtime_plan_count": 1,
            "runtime_materialization_count": 1,
        },
        "enterprise_understanding_model": {
            "model_id": "eum_materialization_blocked",
            "gate": {"status": "PASS", "entry_allowed": True},
        },
        **_passed_downstream_gates(),
        "runtime_materialization_gate": {
            "status": "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE",
            "entry_allowed": False,
            "runtime_materialization_ready": False,
            "execution_allowed": False,
        },
        "runtime_materialization_unknowns": [
            {
                "kind": "RUNTIME_MATERIALIZATION_REQUIRED_VALUE_BINDING_MISSING",
                "reason_code": "RUNTIME_MATERIALIZATION_REQUIRED_VALUE_BINDING_MISSING",
                "field": "order_id",
                "blocks_runtime_materialization": True,
            }
        ],
    }
    monkeypatch.setattr(
        enterprise_knowledge_center,
        "load_enterprise_business_knowledge_asset",
        lambda project, root: asset,
    )

    result = project_existing_understanding_preflight(
        _base_payload(),
        project="customer_materialization_blocked",
        root=tmp_path,
    )

    assert result["ready"] is False
    assert result["blocking_codes"] == ["RUNTIME_MATERIALIZATION_BLOCKED"]
    assert result["understanding_summary"]["gates"][-1] == {
        "label": "运行实例化",
        "status": "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE",
        "ready": False,
    }
    assert "RUNTIME MATERIALIZATION REQUIRED VALUE BINDING MISSING" in result["reasons"][0]["message"]


def test_understanding_preflight_does_not_duplicate_no_source_blocker(
    tmp_path: Path,
) -> None:
    payload = _base_payload()
    payload["ready"] = False
    payload["blocking_codes"] = ["NO_SOURCE"]
    payload["reasons"] = [{"code": "NO_SOURCE", "message": "请先上传资料"}]
    payload["input_checks"]["sources"] = {"status": "blocked", "source_count": 0}

    result = project_existing_understanding_preflight(
        payload,
        project="customer_c",
        root=tmp_path,
    )

    assert result == payload
