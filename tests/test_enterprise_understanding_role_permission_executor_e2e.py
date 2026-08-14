"""Real Chinese role/permission facts must survive through the existing executor."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_test_asset_center.behavior_ir import (
    build_behavior_ir_from_knowledge_asset,
)
from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
    build_chinese_first_comprehension,
)
from ai_test_asset_center.experiment_compiler import (
    compile_experiment_for_obligation,
)
from ai_test_asset_center.obligation_compiler import (
    compile_obligations_from_behavior_ir,
)
from ai_test_asset_center import experiment_executor
from ai_test_asset_center import experiment_executor_governance
from ai_test_asset_center import experiment_plan_executor
from ai_test_asset_center import experiment_runtime_support
from ai_test_asset_center import _experiment_runtime_support_mechanics


def _compiled_authorization_experiment() -> tuple[dict[str, Any], dict[str, Any]]:
    asset = {
        "asset_id": "asset:role-permission-executor-e2e",
        "business_objects": [{"object": "订单"}],
        "roles": [
            {"role": "管理员"},
            {"role": "普通用户"},
            {"role": "仓库员"},
        ],
        "interfaces": [{
            "interface_id": "api:list-orders",
            "operation_id": "listOrders",
            "method": "GET",
            "path": "/api/orders",
            "entity_refs": ["订单"],
            "summary": "查看订单",
            "source_id": "api:orders",
        }],
        "rule_library": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }
    understood = build_chinese_first_comprehension(
        asset,
        [{
            "source_id": "prd:role-permission-executor",
            "filename": "角色权限.md",
            "text": (
                "管理员可以查看订单。"
                "普通用户无权查看订单。"
                "仓库员必须登记订单。"
            ),
        }],
    )
    behavior_ir = build_behavior_ir_from_knowledge_asset(
        understood,
        project_id="role-permission-executor-e2e",
        runtime_actors=[
            {
                "role": "管理员",
                "account_ref": "admin@example.test",
                "secret_ref": "secret_ref:test_accounts:admin@example.test",
                "status": "active",
            },
            {
                "role": "普通用户",
                "account_ref": "user@example.test",
                "secret_ref": "secret_ref:test_accounts:user@example.test",
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


def test_chinese_role_permission_detects_real_unauthorized_access(
    monkeypatch,
    tmp_path: Path,
) -> None:
    behavior_ir, experiment = _compiled_authorization_experiment()
    contract = experiment["authorization_comparison_contract"]
    assert contract["same_resource_identity_required"] is True
    assert contract["resource_identity_binding_targets"] == []

    def fake_http(method: str, url: str, **_: Any) -> dict[str, Any]:
        assert method.upper() == "GET"
        assert url.endswith("/api/orders")
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

    result = experiment_executor.execute_one_experiment(
        experiment,
        behavior_ir=behavior_ir,
        root=tmp_path,
        project="role-permission-executor-e2e",
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
        campaign_id="campaign:role-permission-e2e",
        execution_id="execution:role-permission-e2e",
        actor_tokens={
            "secret_ref:test_accounts:admin@example.test": "token-admin",
            "secret_ref:test_accounts:user@example.test": "token-user",
        },
    )

    assert result["status"] == "EXECUTED"
    assert result["reason_code"] == ""
    assert result["oracle_verdict"]["status"] == "VIOLATION"
    assert result["finding"] is not None

    causality = result["authorization_causality_receipt"]
    assert causality["status"] == "PASSED"
    assert causality["same_resource_proven"] is True
    assert len(causality["runtime_resource_identity_fingerprint"]) == 64
    assert causality["runtime_resource_identity_fingerprint"] != (
        "observer_same_resource_proven"
    )

    observer = next(
        row
        for row in result["observer_receipts"]
        if row["observer_id"] == "authorization_comparison"
    )
    proofs = result["finding"]["authorization_causality_binding_proofs"]
    assert proofs == [{
        "receipt_id": observer["receipt_id"],
        "target": (
            "operation:"
            + contract["control_operation_ref"]
            + ":observed_resource_identity"
        ),
        "status": "BOUND",
        "value_fingerprint": proofs[0]["value_fingerprint"],
    }]
    assert len(proofs[0]["value_fingerprint"]) == 64


def test_chinese_role_permission_correct_denial_is_not_reported(
    monkeypatch,
    tmp_path: Path,
) -> None:
    behavior_ir, experiment = _compiled_authorization_experiment()

    def fake_http(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        assert method.upper() == "GET"
        assert url.endswith("/api/orders")
        if kwargs.get("token") == "token-user":
            return {
                "status": 403,
                "body": {"error": "forbidden"},
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

    result = experiment_executor.execute_one_experiment(
        experiment,
        behavior_ir=behavior_ir,
        root=tmp_path,
        project="role-permission-correct-denial-e2e",
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
        campaign_id="campaign:role-permission-correct-denial",
        execution_id="execution:role-permission-correct-denial",
        actor_tokens={
            "secret_ref:test_accounts:admin@example.test": "token-admin",
            "secret_ref:test_accounts:user@example.test": "token-user",
        },
    )

    assert result["status"] == "EXECUTED"
    assert result["reason_code"] == ""
    assert result["oracle_verdict"]["status"] == "PROPERTY_HELD"
    assert result["oracle_verdict"]["verdict"] == "property_held"
    assert result["finding"] is None
