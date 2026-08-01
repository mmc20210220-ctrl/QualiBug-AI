"""Direct revocation has exact precedence over delegated permission only."""
from __future__ import annotations

from typing import Any

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
    build_chinese_first_comprehension,
)


def _understand(text: str) -> dict[str, Any]:
    asset = {
        "asset_id": "asset:delegated-permission-precedence",
        "business_objects": [{"object": "订单"}],
        "roles": [{"role": "管理员"}, {"role": "财务"}],
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
    return build_chinese_first_comprehension(
        asset,
        [
            {
                "source_id": "prd:delegated-permission-precedence",
                "filename": "角色权限.md",
                "text": text,
            }
        ],
    )


def _behavior_ir(asset: dict[str, Any]) -> dict[str, Any]:
    return build_behavior_ir_from_knowledge_asset(
        asset,
        project_id="delegated-permission-precedence",
        runtime_actors=[
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
        ],
    )


def _runtime_actor(ir: dict[str, Any], role: str) -> dict[str, Any]:
    return next(
        row
        for row in ir["actors"]
        if row.get("role") == role and row.get("runtime_bound") is True
    )


def test_direct_deny_revokes_exact_delegated_allow_with_receipt() -> None:
    understood = _understand(
        "管理员可以查看订单。管理员授权财务代为查看订单。财务无权查看订单。"
    )
    finance_rows = [
        row for row in understood["permission_matrix"] if row.get("role") == "财务"
    ]
    assert len(finance_rows) == 1
    assert finance_rows[0]["decision"] == "deny"

    superseded = [
        row
        for row in understood["superseded_permission_matrix_rows"]
        if row.get("role") == "财务"
    ]
    assert len(superseded) == 1
    assert superseded[0]["authorization_kind"] == "delegated_permission"
    assert superseded[0]["status"] == "SUPERSEDED"
    receipt = understood["authorization_precedence_receipts"][0]
    assert receipt["reason_code"] == "DIRECT_DENY_REVOKES_DELEGATED_ALLOW"
    assert receipt["delegated_permission_id"] == superseded[0]["permission_id"]
    assert receipt["overriding_permission_ids"] == [finance_rows[0]["permission_id"]]
    assert understood["summary"]["source_fact_delegated_permission_count"] == 0
    assert understood["summary"][
        "source_fact_delegated_permission_superseded_count"
    ] == 1

    ir = _behavior_ir(understood)
    admin = _runtime_actor(ir, "管理员")
    finance = _runtime_actor(ir, "财务")
    operation = next(row for row in ir["operations"] if row.get("path") == "/api/orders")
    assert any(
        row.get("relation_type") == "permits"
        and row.get("actor_ref") == admin["id"]
        and row.get("operation_ref") == operation["id"]
        for row in ir["relations"]
    )
    assert any(
        row.get("relation_type") == "denies"
        and row.get("actor_ref") == finance["id"]
        and row.get("operation_ref") == operation["id"]
        for row in ir["relations"]
    )
    assert not any(
        row.get("conflict_type") == "permission_decision_conflict"
        and row.get("role_key") == "财务"
        for row in ir["conflicts"]
    )


def test_direct_source_allow_and_deny_remain_fail_closed() -> None:
    understood = _understand("财务可以查看订单。财务无权查看订单。")
    assert understood["authorization_precedence_receipts"] == []
    assert understood["superseded_permission_matrix_rows"] == []

    ir = _behavior_ir(understood)
    finance = _runtime_actor(ir, "财务")
    operation = next(row for row in ir["operations"] if row.get("path") == "/api/orders")
    assert any(
        row.get("relation_type") == "permission_unknown"
        and row.get("actor_ref") == finance["id"]
        and row.get("operation_ref") == operation["id"]
        and row.get("status") == "conflicting"
        for row in ir["relations"]
    )
    assert any(
        row.get("conflict_type") == "permission_decision_conflict"
        and row.get("role_key") == "财务"
        for row in ir["conflicts"]
    )
