from __future__ import annotations

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir
from ai_test_asset_center.runtime_actor_role_identity import resolve_runtime_actor_roles


def _asset(*roles: str) -> dict:
    permission_matrix = []
    for role in roles:
        permission_matrix.append({
            "source_id": "source:permission-contract",
            "role": role,
            "resource": "records",
            "actions": ["GET"],
            "decision": "allow" if role == "admin" else "deny",
            "scope": "all",
        })
    return {
        "asset_id": "asset:runtime-role-identity",
        "permission_matrix": permission_matrix,
        "roles": [
            {
                "source_id": "source:role-catalog",
                "role": role,
                "evidence": role,
            }
            for role in roles
        ],
        "interfaces": [{
            "interface_id": "api:list-records",
            "operation_id": "listRecords",
            "method": "GET",
            "path": "/api/records",
            "resource": "records",
            "summary": "List records",
            "source_id": "source:api-contract",
        }],
        "business_objects": [{"object": "records"}],
        "rule_library": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }


def test_source_declared_roles_bind_localized_runtime_accounts() -> None:
    actors, receipt = resolve_runtime_actor_roles(
        _asset("admin", "finance"),
        [
            {"role": "系统管理员", "account_ref": "admin@example.test"},
            {"role": "财务人员", "account_ref": "finance@example.test"},
        ],
    )

    assert [row["role"] for row in actors] == ["admin", "finance"]
    assert [row["display_role"] for row in actors] == ["系统管理员", "财务人员"]
    assert receipt["resolved_count"] == 2
    assert receipt["ambiguous_count"] == 0
    assert receipt["unresolved_count"] == 0


def test_role_identity_never_uses_undeclared_lexicon_role() -> None:
    actors, receipt = resolve_runtime_actor_roles(
        _asset("admin"),
        [{"role": "财务", "account_ref": "finance@example.test"}],
    )

    assert actors[0]["role"] == "财务"
    assert actors[0]["role_identity_status"] == "UNRESOLVED"
    assert receipt["source_declared_roles"] == ["admin"]
    assert receipt["unresolved_count"] == 1


def test_ambiguous_localized_role_fails_closed() -> None:
    actors, receipt = resolve_runtime_actor_roles(
        _asset("admin", "finance"),
        [{"role": "管理员财务", "account_ref": "mixed@example.test"}],
    )

    assert actors[0]["role"] == "管理员财务"
    assert actors[0]["role_identity_status"] == "AMBIGUOUS"
    assert actors[0]["role_identity_candidates"] == ["admin", "finance"]
    assert receipt["ambiguous_count"] == 1


def test_role_identity_reconnects_permission_relations_to_executable_accounts() -> None:
    behavior_ir = build_behavior_ir_from_knowledge_asset(
        _asset("admin", "buyer"),
        project_id="runtime-role-identity",
        runtime_actors=[
            {
                "role": "系统管理员",
                "account_ref": "admin@example.test",
                "secret_ref": "secret_ref:test_accounts:admin@example.test",
                "status": "active",
            },
            {
                "role": "普通买家",
                "account_ref": "buyer@example.test",
                "secret_ref": "secret_ref:test_accounts:buyer@example.test",
                "status": "active",
            },
        ],
    )

    runtime_by_role = {
        row["role_key"]: row
        for row in behavior_ir["actors"]
        if row.get("runtime_bound") is True and row.get("account_ref")
    }
    assert set(runtime_by_role) == {"admin", "buyer"}
    operation = next(row for row in behavior_ir["operations"] if row["path"] == "/api/records")
    decisions = {
        (row.get("relation_type"), row.get("actor_ref"), row.get("operation_ref"))
        for row in behavior_ir["relations"]
        if row.get("relation_type") in {"permits", "denies"}
    }
    assert ("permits", runtime_by_role["admin"]["id"], operation["id"]) in decisions
    assert ("denies", runtime_by_role["buyer"]["id"], operation["id"]) in decisions

    obligations = compile_obligations_from_behavior_ir(behavior_ir)["obligations"]
    authorization = [row for row in obligations if row["risk_family"] == "authorization"]
    assert len(authorization) == 1
    assert authorization[0]["required_actors"] == [
        runtime_by_role["admin"]["id"],
        runtime_by_role["buyer"]["id"],
    ]
    receipt = behavior_ir["runtime_actor_role_identity_receipt"]
    assert receipt["resolved_count"] == 2
