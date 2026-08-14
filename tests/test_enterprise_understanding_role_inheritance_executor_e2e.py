"""Inherited role permission must survive the existing real executor chain."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_test_asset_center import experiment_executor
from ai_test_asset_center import experiment_executor_governance
from ai_test_asset_center import experiment_plan_executor
from ai_test_asset_center import experiment_runtime_support
from ai_test_asset_center import _experiment_runtime_support_mechanics
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
    build_chinese_first_comprehension,
)
from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir


def _compiled() -> tuple[dict[str, Any], dict[str, Any]]:
    asset = {
        "asset_id": "asset:role-inheritance-executor",
        "business_objects": [{"object": "订单"}],
        "roles": [
            {"role": "管理员"},
            {"role": "高级管理员"},
            {"role": "普通用户"},
        ],
        "interfaces": [
            {
                "interface_id": "api:orders",
                "operation_id": "listOrders",
                "method": "GET",
                "path": "/api/orders",
                "entity_refs": ["订单"],
                "summary": "查看订单",
                "source_id": "api:orders",
            }
        ],
        "rule_library": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }
    understood = build_chinese_first_comprehension(
        asset,
        [
            {
                "source_id": "prd:role-inheritance-executor",
                "filename": "角色继承.md",
                "text": (
                    "管理员可以查看订单。"
                    "高级管理员继承管理员权限。"
                    "普通用户无权查看订单。"
                ),
            }
        ],
    )
    behavior_ir = build_behavior_ir_from_knowledge_asset(
        understood,
        project_id="role-inheritance-executor",
        runtime_actors=[
            {
                "role": "高级管理员",
                "account_ref": "senior@example.test",
                "secret_ref": "secret_ref:test_accounts:senior@example.test",
                "tenant_ref": "tenant-a",
                "status": "active",
            },
            {
                "role": "普通用户",
                "account_ref": "user@example.test",
                "secret_ref": "secret_ref:test_accounts:user@example.test",
                "tenant_ref": "tenant-a",
                "status": "active",
            },
        ],
    )
    obligation = next(
        row
        for row in compile_obligations_from_behavior_ir(behavior_ir)["obligations"]
        if row["risk_family"] == "authorization"
    )
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=behavior_ir,
        environment_type="test",
    )
    return behavior_ir, experiment


def _execute(
    monkeypatch: Any,
    tmp_path: Path,
    *,
    unauthorized_status: int,
) -> dict[str, Any]:
    behavior_ir, experiment = _compiled()

    def fake_http(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        assert method.upper() == "GET"
        assert url.endswith("/api/orders")
        if kwargs.get("token") == "token-user":
            return {
                "status": unauthorized_status,
                "body": (
                    {"id": "order-1", "status": "active"}
                    if unauthorized_status == 200
                    else {"error": "forbidden"}
                ),
                "headers": {},
                "duration_ms": 1,
                "error": "",
            }
        return {
            "status": 200,
            "body": {"id": "order-1", "status": "active"},
            "headers": {},
            "duration_ms": 1,
            "error": "",
        }

    for module in (
        experiment_executor,
        experiment_executor_governance,
        experiment_plan_executor,
        experiment_runtime_support,
        _experiment_runtime_support_mechanics,
    ):
        monkeypatch.setattr(module, "_http_request", fake_http, raising=False)

    return experiment_executor.execute_one_experiment(
        experiment,
        behavior_ir=behavior_ir,
        root=tmp_path,
        project="role-inheritance-executor",
        base_url="https://staging.example.test",
        runtime_contract={
            "status": "approved",
            "environment_type": "test",
            "environment_kind": "test",
            "environment_ref": "staging",
            "execution_mode": "safe_read_only",
            "approved_base_url": "https://staging.example.test",
            "declared_adapters": ["http_api"],
        },
        campaign_id="campaign:role-inheritance",
        execution_id="execution:role-inheritance",
        actor_tokens={
            "secret_ref:test_accounts:senior@example.test": "token-senior",
            "secret_ref:test_accounts:user@example.test": "token-user",
        },
    )


def test_inherited_permission_detects_real_unauthorized_access(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    result = _execute(monkeypatch, tmp_path, unauthorized_status=200)
    assert result["status"] == "EXECUTED"
    assert result["oracle_verdict"]["status"] == "VIOLATION"
    assert result["finding"] is not None
    assert result["authorization_causality_receipt"]["same_resource_proven"] is True


def test_inherited_permission_correct_denial_is_not_reported(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    result = _execute(monkeypatch, tmp_path, unauthorized_status=403)
    assert result["status"] == "EXECUTED"
    assert result["oracle_verdict"]["status"] == "PROPERTY_HELD"
    assert result["finding"] is None
