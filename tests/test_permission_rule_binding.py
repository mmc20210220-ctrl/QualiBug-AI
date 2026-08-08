"""Permission rule-binding channel: authorization obligations bind role
declarations to the verbatim permission-matrix rule statements.

The channel: knowledge-asset permission rows (role/resource/decision +
verbatim evidence text) → Behavior IR permit/deny/owns relations carrying
``source_rule_statements`` → authorization/isolation obligation property
``field_rule_binding`` (rule_type=permission, statement, source_rule_refs) →
finalizer identity + finding 源契约 paragraph.  Nothing is invented: rows
without a statement contribute no binding and no text.
"""
from __future__ import annotations

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.experiment_outcome_finalizer import _source_rule_identity
from ai_test_asset_center.finding_source_contract import _statement_from_property
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir


def _permission_asset() -> dict:
    return {
        "sources": [{"source_id": "src-perm", "filename": "roles.md", "source_type": "roles"}],
        "permission_matrix": [
            {
                "permission_id": "perm:test:admin:items",
                "source_id": "src-perm",
                "role": "admin",
                "resource": "/api/items",
                "actions": ["write"],
                "decision": "allow",
                "scope": "unspecified",
                "evidence": "权限：管理员。\n\n业务约束：管理员可以修改商品价格；买家不能访问后台写接口。",
            },
            {
                "permission_id": "perm:test:buyer:items",
                "source_id": "src-perm",
                "role": "buyer",
                "resource": "/api/items",
                "actions": ["write"],
                "decision": "deny",
                "scope": "unspecified",
                "evidence": "买家不能访问后台写接口",
            },
            # Row WITHOUT evidence: must never produce a statement binding.
            {
                "permission_id": "perm:test:operator:items",
                "source_id": "src-perm",
                "role": "operator",
                "resource": "/api/items",
                "actions": ["write"],
                "decision": "deny",
                "scope": "unspecified",
            },
        ],
        "operations": [
            {"operation_id": "create_item", "method": "POST", "path": "/api/items", "side_effect_class": "write"},
            {"operation_id": "list_items", "method": "GET", "path": "/api/items", "side_effect_class": "read"},
        ],
        "roles": [
            {"role": "admin"},
            {"role": "buyer"},
            {"role": "operator"},
        ],
    }


def _runtime_actors() -> list[dict]:
    return [
        {"role": "admin", "account_ref": "admin-1", "secret_ref": "secret_ref:test_accounts:admin-1"},
        {"role": "buyer", "account_ref": "buyer-1", "secret_ref": "secret_ref:test_accounts:buyer-1"},
        {"role": "operator", "account_ref": "operator-1", "secret_ref": "secret_ref:test_accounts:operator-1"},
    ]


def test_ir_permission_relations_carry_verbatim_statements() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        _permission_asset(),
        project_id="perm-binding-ir",
        runtime_actors=_runtime_actors(),
    )
    write_rel = [
        row
        for row in ir["relations"]
        if row.get("operation_ref") and any(
            op.get("id") == row.get("operation_ref") and op.get("read_write") == "write"
            for op in ir["operations"]
        )
    ]
    statements = [
        _text(row.get("statement"))
        for rel in write_rel
        for row in (rel.get("source_rule_statements") or [])
        if _text(row.get("statement"))
    ]
    joined = " | ".join(statements)
    assert "管理员可以修改商品价格" in joined
    assert "买家不能访问后台写接口" in joined
    # The evidence-less operator row contributes nothing.
    assert "operator" not in joined or "operator" not in " ".join(
        _text(row.get("statement") or "") for row in
        [row for rel in write_rel for row in (rel.get("source_rule_statements") or [])]
    )


def _text(value) -> str:
    return str(value or "").strip()


def _authorization_obligations(ir: dict) -> list[dict]:
    compiled = compile_obligations_from_behavior_ir(ir)
    return [
        row
        for row in compiled["obligations"]
        if row.get("risk_family") == "authorization"
    ]


def test_authorization_obligation_binds_permission_rule_statement() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        _permission_asset(),
        project_id="perm-binding-obligations",
        runtime_actors=_runtime_actors(),
    )
    authorization = _authorization_obligations(ir)
    assert authorization, "expected at least one authorization obligation"

    bound = [
        row for row in authorization
        if _dict(row.get("property")).get("field_rule_binding")
    ]
    assert bound, "authorization obligations must bind permission rule text"

    for obligation in bound:
        prop = _dict(obligation.get("property"))
        binding = _dict(prop.get("field_rule_binding"))
        assert binding.get("rule_type") == "permission"
        statement = _text(binding.get("statement"))
        assert statement, "permission binding must carry a statement"
        assert "买家不能访问后台写接口" in statement
        refs = binding.get("source_rule_refs") or []
        assert any("perm:test:" in ref for ref in refs), (
            "permission rule ids must be carried as source_rule_refs"
        )
        assert binding.get("operation_id"), "permission binding must name its operation"


def test_permission_binding_flows_into_finalizer_identity() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        _permission_asset(),
        project_id="perm-binding-finalizer",
        runtime_actors=_runtime_actors(),
    )
    authorization = _authorization_obligations(ir)
    bound = [
        row for row in authorization
        if _dict(row.get("property")).get("field_rule_binding")
    ]
    assert bound
    obligation = bound[0]
    prop = _dict(obligation.get("property"))
    binding = _dict(prop.get("field_rule_binding"))

    # Simulate the experiment compiler's assertion contract (property copied
    # onto the assertion, rule_id projected, source_refs carried).
    exp = {
        "source_refs": list(obligation.get("source_refs") or []),
        "assertions": [{
            "kind": "owner_tenant_visibility",
            "property": dict(prop),
            "rule_id": _text(binding.get("rule_id")),
        }],
    }
    identity = _source_rule_identity(exp)
    assert identity, "finalizer must derive identity from the permission binding"
    assert identity["source_rule_ref"], identity
    assert "买家不能访问后台写接口" in identity["source_rule_statement"], identity


def test_permission_binding_flows_into_source_contract_statement() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        _permission_asset(),
        project_id="perm-binding-source-contract",
        runtime_actors=_runtime_actors(),
    )
    authorization = _authorization_obligations(ir)
    bound = [
        row for row in authorization
        if _dict(row.get("property")).get("field_rule_binding")
    ]
    assert bound
    prop = _dict(bound[0].get("property"))
    statement = _statement_from_property(prop)
    assert statement
    assert "买家不能访问后台写接口" in statement


def test_no_rule_text_without_source_statement() -> None:
    """Obligations from evidence-less permission rows bind no invented text."""
    from ai_test_asset_center.behavior_ir import empty_behavior_ir

    ir = empty_behavior_ir(project_id="perm-binding-no-invention")
    ir.update({
        "operations": [{
            "id": "op-admin-write",
            "method": "POST",
            "path": "/admin/write",
            "read_write": "write",
        }],
        "actors": [
            {
                "id": "actor-admin",
                "role": "admin",
                "account_ref": "admin-1",
                "credential_secret_ref": "secret_ref:test_accounts:admin-1",
                "runtime_bound": True,
                "account_status": "active",
            },
            {
                "id": "actor-buyer",
                "role": "buyer",
                "account_ref": "buyer-1",
                "credential_secret_ref": "secret_ref:test_accounts:buyer-1",
                "runtime_bound": True,
                "account_status": "active",
            },
        ],
        "relations": [
            {
                "id": "rel-permit-admin",
                "relation_type": "permits",
                "from_ref": "actor-admin",
                "to_ref": "op-admin-write",
                "operation_ref": "op-admin-write",
                "actor_ref": "actor-admin",
                "preconditions": [],
                "effects": [],
                "source_rule_statements": [],
                "source_refs": [{"source_id": "perm", "kind": "permission_matrix", "locator": "admin->admin"}],
            },
            {
                "id": "rel-deny-buyer",
                "relation_type": "denies",
                "from_ref": "actor-buyer",
                "to_ref": "op-admin-write",
                "operation_ref": "op-admin-write",
                "actor_ref": "actor-buyer",
                "preconditions": [],
                "effects": [],
                "source_rule_statements": [],
                "source_refs": [{"source_id": "perm", "kind": "permission_matrix", "locator": "buyer->admin"}],
            },
        ],
    })
    authorization = _authorization_obligations(ir)
    assert authorization
    for obligation in authorization:
        prop = _dict(obligation.get("property"))
        assert not prop.get("field_rule_binding"), (
            "evidence-less permission relations must not bind rule text"
        )


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}
