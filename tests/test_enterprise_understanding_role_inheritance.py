"""Explicit role inheritance remains source-backed, scope-safe and account-independent."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
    build_chinese_first_comprehension,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.builder import (
    build_enterprise_understanding_model,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.fact_permission_matrix import (
    materialize_fact_permission_matrix,
)


def _asset() -> dict[str, Any]:
    return {
        "asset_id": "asset:role-inheritance",
        "business_objects": [{"object": "订单"}],
        "roles": [
            {"role": "管理员"},
            {"role": "高级管理员"},
            {"role": "平台管理员"},
            {"role": "财务"},
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


def _understand(text: str) -> dict[str, Any]:
    return build_chinese_first_comprehension(
        _asset(),
        [
            {
                "source_id": "prd:role-inheritance",
                "filename": "角色继承.md",
                "text": text,
            }
        ],
    )


def _runtime_actors() -> list[dict[str, str]]:
    return [
        {
            "role": "高级管理员",
            "account_ref": "senior-a@example.test",
            "secret_ref": "secret_ref:test_accounts:senior-a@example.test",
            "tenant_ref": "tenant-a",
            "status": "active",
        },
        {
            "role": "高级管理员",
            "account_ref": "senior-b@example.test",
            "secret_ref": "secret_ref:test_accounts:senior-b@example.test",
            "tenant_ref": "tenant-b",
            "status": "active",
        },
        {
            "role": "普通用户",
            "account_ref": "user@example.test",
            "secret_ref": "secret_ref:test_accounts:user@example.test",
            "tenant_ref": "tenant-a",
            "status": "active",
        },
    ]


def _runtime_rows(ir: dict[str, Any], role: str) -> list[dict[str, Any]]:
    return [
        row
        for row in ir["actors"]
        if row.get("role") == role and row.get("runtime_bound") is True
    ]


def test_explicit_role_inheritance_projects_to_existing_matrix_and_each_account() -> None:
    understood = _understand(
        "管理员可以查看订单。高级管理员继承管理员权限。普通用户无权查看订单。"
    )
    contracts = understood["role_inheritance_contracts"]
    assert len(contracts) == 1
    assert contracts[0]["inheriting_role"] == "高级管理员"
    assert contracts[0]["inherited_role"] == "管理员"
    assert contracts[0]["status"] == "RESOLVED"

    inherited = [
        row
        for row in understood["permission_matrix"]
        if row.get("authorization_kind") == "role_inherited_permission"
    ]
    assert len(inherited) == 1
    assert inherited[0]["role"] == "高级管理员"
    assert inherited[0]["decision"] == "allow"
    assert inherited[0]["inheritance_path"] == ["高级管理员", "管理员"]
    assert inherited[0]["source_permission_id"]

    ir = build_behavior_ir_from_knowledge_asset(
        understood,
        project_id="role-inheritance-multi-account",
        runtime_actors=_runtime_actors(),
    )
    senior_accounts = _runtime_rows(ir, "高级管理员")
    assert len(senior_accounts) == 2
    assert len({row["id"] for row in senior_accounts}) == 2
    assert len({row["credential_secret_ref"] for row in senior_accounts}) == 2
    operation = next(row for row in ir["operations"] if row.get("path") == "/api/orders")
    for actor in senior_accounts:
        assert any(
            relation.get("relation_type") == "permits"
            and relation.get("actor_ref") == actor["id"]
            and relation.get("operation_ref") == operation["id"]
            for relation in ir["relations"]
        )


def test_role_inheritance_scope_restricts_and_never_widens_tenant_access() -> None:
    asset = _asset()
    asset["roles"] = [
        {"role": "管理员"},
        {
            "role": "高级管理员",
            "inherits_from": "管理员",
            "inheritance_scope": "own_tenant",
            "source_id": "prd:roles",
        },
    ]
    asset["permission_matrix"] = [
        {
            "permission_id": "permission:admin-all",
            "role": "管理员",
            "resource": "/api/orders",
            "interface_id": "api:orders",
            "actions": ["get"],
            "decision": "allow",
            "scope": "all_tenants",
            "source_id": "prd:permissions",
        }
    ]
    materialize_fact_permission_matrix(asset)
    inherited = next(
        row
        for row in asset["permission_matrix"]
        if row.get("authorization_kind") == "role_inherited_permission"
    )
    assert inherited["scope"] == "own_tenant"
    assert asset["governance"]["role_inheritance_never_widens_scope"] is True


def test_direct_deny_revokes_exact_inherited_allow_with_audit_receipt() -> None:
    understood = _understand(
        "管理员可以查看订单。高级管理员继承管理员权限。高级管理员无权查看订单。"
    )
    active = [
        row for row in understood["permission_matrix"] if row.get("role") == "高级管理员"
    ]
    assert len(active) == 1
    assert active[0]["decision"] == "deny"
    superseded = [
        row
        for row in understood["superseded_permission_matrix_rows"]
        if row.get("authorization_kind") == "role_inherited_permission"
    ]
    assert len(superseded) == 1
    receipt = next(
        row
        for row in understood["authorization_precedence_receipts"]
        if row.get("reason_code") == "DIRECT_DENY_REVOKES_INHERITED_ALLOW"
    )
    assert receipt["inherited_permission_id"] == superseded[0]["permission_id"]
    assert receipt["overriding_permission_ids"] == [active[0]["permission_id"]]


def test_transitive_role_inheritance_is_content_addressed_and_acyclic() -> None:
    understood = _understand(
        "管理员可以查看订单。高级管理员继承管理员权限。平台管理员继承高级管理员权限。"
    )
    platform_rows = [
        row
        for row in understood["permission_matrix"]
        if row.get("role") == "平台管理员"
        and row.get("authorization_kind") == "role_inherited_permission"
    ]
    assert len(platform_rows) == 1
    assert platform_rows[0]["inheritance_path"] == ["平台管理员", "高级管理员", "管理员"]
    assert len(platform_rows[0]["inheritance_contract_ids"]) == 2
    receipts = [
        row
        for row in understood["role_inheritance_receipts"]
        if row.get("permission_id") == platform_rows[0]["permission_id"]
    ]
    assert len(receipts) == 1
    assert receipts[0]["receipt_id"].startswith("role_inheritance_receipt:")


def test_role_inheritance_cycle_fails_closed() -> None:
    asset = _asset()
    asset["roles"] = [
        {"role": "管理员", "inherits_from": "高级管理员", "source_id": "prd:roles"},
        {"role": "高级管理员", "inherits_from": "管理员", "source_id": "prd:roles"},
    ]
    asset["permission_matrix"] = [
        {
            "permission_id": "permission:admin",
            "role": "管理员",
            "resource": "/api/orders",
            "interface_id": "api:orders",
            "actions": ["get"],
            "decision": "allow",
            "scope": "unspecified",
        }
    ]
    materialize_fact_permission_matrix(asset)
    assert not any(
        row.get("authorization_kind") == "role_inherited_permission"
        for row in asset["permission_matrix"]
    )
    gap = next(
        row
        for row in asset["coverage_gaps"]
        if row.get("gap_type") == "role_inheritance_cycle_detected"
    )
    assert gap["roles"] == ["管理员", "高级管理员"]


def test_unbound_conditional_role_inheritance_is_not_executable() -> None:
    understood = _understand(
        "管理员可以查看订单。当值班时，高级管理员继承管理员权限。"
    )
    contract = understood["role_inheritance_contracts"][0]
    assert contract["status"] == "UNRESOLVED"
    assert contract["reason_code"] == "ROLE_INHERITANCE_CONDITION_UNBOUND"
    assert not any(
        row.get("authorization_kind") == "role_inherited_permission"
        for row in understood["permission_matrix"]
    )


def test_delegated_permission_is_not_implicitly_inherited() -> None:
    understood = _understand(
        "管理员授权财务代为查看订单。高级管理员继承财务权限。"
    )
    assert any(
        row.get("authorization_kind") == "delegated_permission"
        and row.get("role") == "财务"
        for row in understood["permission_matrix"]
    )
    assert not any(
        row.get("authorization_kind") == "role_inherited_permission"
        and row.get("role") == "高级管理员"
        for row in understood["permission_matrix"]
    )
    assert understood["governance"]["delegated_permission_is_not_role_inheritable"] is True


def test_role_inheritance_changes_enterprise_model_identity() -> None:
    base = _understand("管理员可以查看订单。")
    inherited = _understand("管理员可以查看订单。高级管理员继承管理员权限。")
    base_model = build_enterprise_understanding_model(deepcopy(base))
    inherited_model = build_enterprise_understanding_model(deepcopy(inherited))
    assert base_model["model_id"] != inherited_model["model_id"]


def test_nested_role_names_do_not_create_phantom_shorter_role_permissions() -> None:
    understood = _understand(
        "管理员可以查看订单。高级管理员无权查看订单。"
    )
    admin_rows = [
        row for row in understood["permission_matrix"] if row.get("role") == "管理员"
    ]
    senior_rows = [
        row for row in understood["permission_matrix"] if row.get("role") == "高级管理员"
    ]
    assert [row["decision"] for row in admin_rows] == ["allow"]
    assert [row["decision"] for row in senior_rows] == ["deny"]


def test_explicit_conjoined_nested_roles_remain_two_actor_permissions() -> None:
    understood = _understand(
        "管理员和高级管理员都可以查看订单。"
    )
    decisions = {
        (row.get("role"), row.get("decision"))
        for row in understood["permission_matrix"]
    }
    assert ("管理员", "allow") in decisions
    assert ("高级管理员", "allow") in decisions
