"""Role delegation must remain distinct from direct role authorization."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

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


def _asset(*, method: str, path: str, summary: str) -> dict[str, Any]:
    return {
        "asset_id": "asset:authorization-delegation",
        "business_objects": [{"object": "订单"}],
        "roles": [
            {"role": "管理员"},
            {"role": "财务"},
            {"role": "普通用户"},
        ],
        "interfaces": [
            {
                "interface_id": "api:orders",
                "operation_id": "ordersOperation",
                "method": method,
                "path": path,
                "entity_refs": ["订单"],
                "summary": summary,
                "source_id": "api:orders",
            }
        ],
        "rule_library": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }


def _understand(text: str, *, method: str, path: str, summary: str) -> dict[str, Any]:
    return build_chinese_first_comprehension(
        _asset(method=method, path=path, summary=summary),
        [
            {
                "source_id": "prd:authorization-delegation",
                "filename": "角色委托权限.md",
                "text": text,
            }
        ],
    )


def _runtime_actors() -> list[dict[str, str]]:
    return [
        {
            "role": "管理员",
            "account_ref": "admin@example.test",
            "secret_ref": "secret_ref:test_accounts:admin@example.test",
            "status": "active",
        },
        {
            "role": "财务",
            "account_ref": "finance@example.test",
            "secret_ref": "secret_ref:test_accounts:finance@example.test",
            "status": "active",
        },
        {
            "role": "普通用户",
            "account_ref": "user@example.test",
            "secret_ref": "secret_ref:test_accounts:user@example.test",
            "status": "active",
        },
    ]


def _runtime_actor_by_role(behavior_ir: dict[str, Any], role: str) -> dict[str, Any]:
    return next(
        actor
        for actor in behavior_ir["actors"]
        if actor.get("role") == role and actor.get("runtime_bound") is True
    )


def _authorization_obligations(behavior_ir: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        obligation
        for obligation in compile_obligations_from_behavior_ir(behavior_ir)["obligations"]
        if obligation["risk_family"] == "authorization"
    ]


def test_unconditional_delegation_grants_only_delegatee_and_binds_approve_endpoint() -> None:
    understood = _understand(
        "管理员可以审批订单。管理员授权财务代为审批订单。普通用户无权审批订单。",
        method="POST",
        path="/api/orders/{order_id}/approve",
        summary="审批订单",
    )

    rows = understood["permission_matrix"]
    delegated = [row for row in rows if row.get("authorization_kind") == "delegated_permission"]
    assert len(delegated) == 1
    assert delegated[0]["role"] == "财务"
    assert delegated[0]["delegator_role"] == "管理员"
    assert delegated[0]["delegatee_role"] == "财务"
    assert delegated[0]["delegation_contract"]["condition_binding_required"] is False
    assert {"approve", "post", "审核"}.issubset(set(delegated[0]["actions"]))
    assert understood["summary"]["source_fact_delegated_permission_count"] == 1
    assert understood["governance"]["delegator_is_not_automatically_directly_permitted"] is True

    delegation_fact = next(
        fact
        for fact in understood["business_fact_ledger"]["items"]
        if fact.get("authorization_delegation")
    )
    delegation_id = delegation_fact["fact_id"]
    assert not any(
        row.get("fact_id") == delegation_id and row.get("role") == "管理员"
        for row in rows
    )

    behavior_ir = build_behavior_ir_from_knowledge_asset(
        understood,
        project_id="authorization-delegation-approve",
        runtime_actors=_runtime_actors(),
    )
    finance = _runtime_actor_by_role(behavior_ir, "财务")
    user = _runtime_actor_by_role(behavior_ir, "普通用户")
    operation = next(
        operation
        for operation in behavior_ir["operations"]
        if operation.get("path") == "/api/orders/{order_id}/approve"
    )
    assert any(
        relation.get("relation_type") == "permits"
        and relation.get("actor_ref") == finance["id"]
        and relation.get("operation_ref") == operation["id"]
        for relation in behavior_ir["relations"]
    )
    assert any(
        relation.get("relation_type") == "denies"
        and relation.get("actor_ref") == user["id"]
        and relation.get("operation_ref") == operation["id"]
        for relation in behavior_ir["relations"]
    )

    delegated_obligation = next(
        obligation
        for obligation in _authorization_obligations(behavior_ir)
        if obligation["required_actors"] == [finance["id"], user["id"]]
    )
    assert delegated_obligation["required_operations"] == [operation["id"]]
    assert delegated_obligation["property"]["template"] == (
        "authorization_control_treatment"
    )
    assert any(
        ref.get("kind") == "permission_matrix"
        and ref.get("locator") == "财务"
        for ref in delegated_obligation["source_refs"]
    )



def test_delegation_authority_precedes_generic_explicit_allow() -> None:
    understood = _understand(
        "管理员授权财务代为查看订单。",
        method="GET",
        path="/api/orders",
        summary="查看订单",
    )
    fact = next(
        row
        for row in understood["business_fact_ledger"]["items"]
        if row.get("authorization_delegation")
    )
    fact["authorization_semantics"] = {
        "decision": "ALLOW",
        "source_backed": True,
        "derivation": "generic_explicit_allow",
    }

    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.authorization_semantics import (
        resolve_fact_authorization,
    )

    resolved = resolve_fact_authorization(fact)
    assert resolved["semantic_kind"] == "AUTHORIZATION_DELEGATION"
    assert resolved["delegatee_role"] == "财务"
    assert resolved["delegator_role"] == "管理员"
    assert resolved["derivation"] == "explicit_source_authorization_delegation"

def test_conditional_delegation_is_visible_but_not_executable_without_binding() -> None:
    understood = _understand(
        "管理员可以审批订单。如果金额超过1000，管理员授权财务代为审批订单。普通用户无权审批订单。",
        method="POST",
        path="/api/orders/{order_id}/approve",
        summary="审批订单",
    )

    assert not any(
        row.get("authorization_kind") == "delegated_permission"
        for row in understood["permission_matrix"]
    )
    gaps = [
        gap
        for gap in understood["coverage_gaps"]
        if gap.get("gap_type") == "authorization_delegation_condition_unbound"
    ]
    assert len(gaps) == 1
    assert gaps[0]["delegator_role"] == "管理员"
    assert gaps[0]["delegatee_role"] == "财务"
    assert gaps[0]["condition_contract"]["conditions"] == ["金额超过1000"]
    assert gaps[0]["condition_contract"]["quantity_constraints"] == [
        {
            "raw": "超过1000",
            "operator": "超过",
            "value": "1000",
            "unit": "",
            "source_backed": True,
        }
    ]

    behavior_ir = build_behavior_ir_from_knowledge_asset(
        understood,
        project_id="authorization-delegation-conditional",
        runtime_actors=_runtime_actors(),
    )
    finance = _runtime_actor_by_role(behavior_ir, "财务")
    assert not any(
        relation.get("actor_ref") == finance["id"]
        and relation.get("relation_type") in {"permits", "denies"}
        for relation in behavior_ir["relations"]
    )
    assert not any(
        finance["id"] in obligation.get("required_actors", [])
        for obligation in _authorization_obligations(behavior_ir)
    )


def _compiled_delegated_read_experiment() -> tuple[dict[str, Any], dict[str, Any]]:
    understood = _understand(
        "管理员授权财务代为查看订单。普通用户无权查看订单。",
        method="GET",
        path="/api/orders",
        summary="查看订单",
    )
    behavior_ir = build_behavior_ir_from_knowledge_asset(
        understood,
        project_id="authorization-delegation-read",
        runtime_actors=_runtime_actors(),
    )
    finance = _runtime_actor_by_role(behavior_ir, "财务")
    user = _runtime_actor_by_role(behavior_ir, "普通用户")
    obligation = next(
        obligation
        for obligation in _authorization_obligations(behavior_ir)
        if obligation["required_actors"] == [finance["id"], user["id"]]
    )
    return behavior_ir, compile_experiment_for_obligation(
        obligation,
        behavior_ir=behavior_ir,
        environment_type="test",
    )


def _patch_http(monkeypatch: pytest.MonkeyPatch, fake_http: Any) -> None:
    for module in (
        experiment_executor,
        experiment_executor_governance,
        experiment_plan_executor,
        experiment_runtime_support,
        _experiment_runtime_support_mechanics,
    ):
        monkeypatch.setattr(module, "_http_request", fake_http, raising=False)


def _execute(
    *,
    behavior_ir: dict[str, Any],
    experiment: dict[str, Any],
    tmp_path: Path,
) -> dict[str, Any]:
    return experiment_executor.execute_one_experiment(
        experiment,
        behavior_ir=behavior_ir,
        root=tmp_path,
        project="authorization-delegation-executor",
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
        campaign_id="campaign:authorization-delegation",
        execution_id="execution:authorization-delegation",
        actor_tokens={
            "secret_ref:test_accounts:finance@example.test": "token-finance",
            "secret_ref:test_accounts:user@example.test": "token-user",
        },
    )


def test_delegated_permission_detects_real_unauthorized_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    behavior_ir, experiment = _compiled_delegated_read_experiment()

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

    _patch_http(monkeypatch, fake_http)
    result = _execute(
        behavior_ir=behavior_ir,
        experiment=experiment,
        tmp_path=tmp_path,
    )

    assert result["status"] == "EXECUTED"
    assert result["oracle_verdict"]["status"] == "VIOLATION"
    assert result["finding"] is not None
    assert result["authorization_causality_receipt"]["status"] == "PASSED"


def test_delegated_permission_correct_denial_is_not_reported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    behavior_ir, experiment = _compiled_delegated_read_experiment()

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

    _patch_http(monkeypatch, fake_http)
    result = _execute(
        behavior_ir=behavior_ir,
        experiment=experiment,
        tmp_path=tmp_path,
    )

    assert result["status"] == "EXECUTED"
    assert result["oracle_verdict"]["status"] == "PROPERTY_HELD"
    assert result["finding"] is None
