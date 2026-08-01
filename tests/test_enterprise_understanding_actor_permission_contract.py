"""Actor responsibility and authorization are separate source-backed contracts."""
from __future__ import annotations

import hashlib

from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
    build_chinese_first_comprehension,
)
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.builder import (
    build_enterprise_understanding_model,
)
from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.obligation_compiler import (
    compile_obligations_from_behavior_ir,
)


def _evidence(statement: str, *, source_id: str = "src_roles", locator: str = "line:1") -> dict:
    return {
        "source_id": source_id,
        "locator": locator,
        "quote": statement,
        "quote_hash": hashlib.sha256(statement.encode("utf-8")).hexdigest(),
    }


def _permission(
    *,
    permission_id: str,
    decision: str = "",
    actions: list[str] | None = None,
    denied_actions: list[str] | None = None,
    resource: str = "orders",
) -> dict:
    statement = f"operator {decision or 'unknown'} {resource} {actions or []} {denied_actions or []}"
    return {
        "permission_id": permission_id,
        "source_id": "src_roles",
        "source_locator": f"line:{permission_id}",
        "statement": statement,
        "role": "operator",
        "resource": resource,
        "actions": list(actions or []),
        "denied_actions": list(denied_actions or []),
        **({"decision": decision} if decision else {}),
        "scope": "tenant",
    }


def _asset(*, permissions: list[dict] | None = None, facts: list[dict] | None = None) -> dict:
    return {
        "asset_id": "asset:actor-permission-contract",
        "roles": [
            {
                "role_id": "role:operator",
                "source_id": "src_roles",
                "source_locator": "line:role",
                "statement": "operator role",
                "role": "operator",
            }
        ],
        "permission_matrix": list(permissions or []),
        "business_fact_ledger": {"items": list(facts or [])},
        "enterprise_comprehension_gate": {"entry_allowed": True, "status": "PASS"},
    }


def _actor(model: dict) -> dict:
    return next(row for row in model["actors"] if row["name"] == "operator")


def _fact(
    *,
    modality: str,
    action: str = "approve",
    object_ref: str = "orders",
    authorization_semantics: dict | None = None,
) -> dict:
    statement = f"operator {modality} {action} {object_ref}"
    fact = {
        "fact_id": f"fact:{modality.lower()}:{action}",
        "kind": "RULE",
        "status": "ACCEPTED",
        "raw_statement": statement,
        "modality": modality,
        "polarity": "negative" if modality == "MUST_NOT" else "positive",
        "subject": {"actor_refs": ["operator"], "entity_refs": [object_ref]},
        "object": {"entity_refs": []},
        "action": {"canonical": action, "raw": action},
        "source_spans": [_evidence(statement)],
    }
    if authorization_semantics is not None:
        fact["authorization_semantics"] = authorization_semantics
    return fact


def test_unknown_permission_never_becomes_allow_or_deny() -> None:
    model = build_enterprise_understanding_model(
        _asset(permissions=[_permission(permission_id="p1", actions=["read"])])
    )
    actor = _actor(model)

    assert actor["permissions"] == []
    assert actor["restrictions"] == []
    assert [row["decision"] for row in actor["permission_unknowns"]] == ["UNKNOWN"]
    assert actor["authorization_status"] == "UNRESOLVED"
    assert model["gate"]["authorization_gate"]["entry_allowed"] is False
    assert model["gate"]["authorization_gate"]["unknown_never_authorizes"] is True


def test_mixed_allow_and_denied_actions_are_split_not_collapsed() -> None:
    model = build_enterprise_understanding_model(
        _asset(
            permissions=[
                _permission(
                    permission_id="p2",
                    decision="allow",
                    actions=["read", "list"],
                    denied_actions=["delete"],
                )
            ]
        )
    )
    actor = _actor(model)

    assert {action for row in actor["permissions"] for action in row["actions"]} == {
        "list",
        "read",
    }
    assert {action for row in actor["restrictions"] for action in row["actions"]} == {
        "delete"
    }
    assert actor["permission_unknowns"] == []
    assert actor["authorization_status"] == "RESOLVED"


def test_required_business_participant_is_responsible_not_automatically_authorized() -> None:
    model = build_enterprise_understanding_model(_asset(facts=[_fact(modality="MUST")]))
    actor = _actor(model)

    assert actor["responsibility_operation_refs"]
    assert actor["permissions"] == []
    assert actor["restrictions"] == []
    assert actor["authorization_status"] == "NOT_DECLARED"


def test_source_modal_allow_and_explicit_deny_project_authorization_only() -> None:
    model = build_enterprise_understanding_model(
        _asset(
            facts=[
                _fact(modality="MAY", action="read"),
                _fact(
                    modality="MUST_NOT",
                    action="delete",
                    authorization_semantics={
                        "decision": "DENY",
                        "source_backed": True,
                        "derivation": "explicit_authorization_semantics",
                    },
                ),
            ]
        )
    )
    actor = _actor(model)

    assert {action for row in actor["permissions"] for action in row["actions"]} == {
        "read"
    }
    assert {action for row in actor["restrictions"] for action in row["actions"]} == {
        "delete"
    }


def test_chinese_role_permission_source_stays_distinct_from_responsibility() -> None:
    asset = {
        "asset_id": "asset:role-permission-e2e",
        "business_objects": [{"object": "订单"}],
        "roles": [{"role": "管理员"}, {"role": "仓库员"}],
        "rule_library": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }
    enriched = build_chinese_first_comprehension(
        asset,
        [{
            "source_id": "prd:role-permission",
            "filename": "角色权限.md",
            "text": (
                "管理员可以查看订单。"
                "仓库员必须登记订单。"
                "仓库员不得重复登记订单。"
            ),
        }],
    )

    promoted = {
        row["statement"]: row
        for row in enriched["rule_library"]
        if row.get("derivation") == "chinese_first_business_comprehension"
    }
    assert promoted["管理员可以查看订单"]["risk_type"] == "authorization"
    assert promoted["管理员可以查看订单"]["authorization_decision"] == "ALLOW"
    assert promoted["仓库员必须登记订单"]["risk_type"] == "business_logic"

    model = build_enterprise_understanding_model(enriched)
    admin = next(row for row in model["actors"] if row["name"] == "管理员")
    warehouse = next(row for row in model["actors"] if row["name"] == "仓库员")
    assert admin["authorization_status"] == "RESOLVED"
    assert {
        (action, resource)
        for contract in admin["permissions"]
        for action in contract["actions"]
        for resource in contract["resource_refs"]
    } == {("查看", "订单")}
    assert warehouse["permissions"] == []
    assert warehouse["restrictions"] == []
    assert warehouse["authorization_status"] == "NOT_DECLARED"


def test_chinese_role_permission_reaches_authorization_experiment_mainline() -> None:
    asset = {
        "asset_id": "asset:role-permission-runtime-e2e",
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
    enriched = build_chinese_first_comprehension(
        asset,
        [{
            "source_id": "prd:role-permission-runtime",
            "filename": "角色权限.md",
            "text": (
                "管理员可以查看订单。"
                "普通用户无权查看订单。"
                "仓库员必须登记订单。"
            ),
        }],
    )
    behavior_ir = build_behavior_ir_from_knowledge_asset(
        enriched,
        project_id="role-permission-runtime-e2e",
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

    runtime_actors = {
        row["role"]: row
        for row in behavior_ir["actors"]
        if row.get("runtime_bound") is True and row.get("account_ref")
    }
    operation = next(
        row for row in behavior_ir["operations"] if row["path"] == "/api/orders"
    )
    decisions = {
        (row["relation_type"], row["actor_ref"], row["operation_ref"])
        for row in behavior_ir["relations"]
        if row.get("relation_type") in {"permits", "denies"}
    }
    assert (
        "permits",
        runtime_actors["管理员"]["id"],
        operation["id"],
    ) in decisions
    assert (
        "denies",
        runtime_actors["普通用户"]["id"],
        operation["id"],
    ) in decisions
    assert not any(
        row.get("actor_ref") == next(
            actor["id"]
            for actor in behavior_ir["actors"]
            if actor["role"] == "仓库员"
        )
        and row.get("relation_type") in {"permits", "denies"}
        for row in behavior_ir["relations"]
    )

    authorization_obligations = [
        row
        for row in compile_obligations_from_behavior_ir(behavior_ir)["obligations"]
        if row["risk_family"] == "authorization"
    ]
    assert len(authorization_obligations) == 1
    obligation = authorization_obligations[0]
    assert obligation["property"]["template"] == "authorization_control_treatment"
    assert obligation["required_actors"] == [
        runtime_actors["管理员"]["id"],
        runtime_actors["普通用户"]["id"],
    ]
    assert obligation["required_operations"] == [operation["id"]]
    assert any(
        ref.get("kind") == "permission_matrix"
        and ref.get("source_id") == "prd:role-permission-runtime"
        for ref in obligation["source_refs"]
    )

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=behavior_ir,
        environment_type="test",
    )
    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert experiment["compile_receipt"][
        "authorization_comparison_contract_status"
    ] == "COMPILED_RUNTIME_VERIFICATION_REQUIRED"
    assert experiment["control_plan"][0]["actor_ref"] == (
        runtime_actors["管理员"]["id"]
    )
    assert experiment["treatment_plan"][0]["actor_ref"] == (
        runtime_actors["普通用户"]["id"]
    )


def test_incomplete_declared_authorization_is_visible_and_not_executable() -> None:
    fact = _fact(
        modality="MAY",
        action="read",
        object_ref="",
        authorization_semantics={
            "decision": "ALLOW",
            "source_backed": True,
            "derivation": "explicit_authorization_semantics",
        },
    )
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.fact_permission_matrix import (
        materialize_fact_permission_matrix,
    )

    source_asset = _asset(facts=[fact])
    source_asset["interfaces"] = [{
        "interface_id": "api:list-orders",
        "operation_id": "listOrders",
        "method": "GET",
        "path": "/api/orders",
        "entity_refs": ["orders"],
    }]
    behavior_ir = build_behavior_ir_from_knowledge_asset(
        materialize_fact_permission_matrix(source_asset),
        project_id="incomplete-role-permission",
        api_operations=[{
            "operation_id": "listOrders",
            "method": "GET",
            "path": "/api/orders",
            "entity_refs": ["orders"],
        }],
        runtime_actors=[{
            "role": "operator",
            "account_ref": "operator@example.test",
            "secret_ref": "secret_ref:test_accounts:operator@example.test",
        }],
    )

    gaps = [
        row
        for row in behavior_ir["coverage_gaps"]
        if row.get("gap_type") == "authorization_coordinate_incomplete"
    ]
    assert len(gaps) == 1
    assert gaps[0]["missing_coordinate_fields"] == ["resource_refs"]
    assert not any(
        row.get("relation_type") in {"permits", "denies"}
        for row in behavior_ir["relations"]
    )


def test_authorization_change_changes_model_identity() -> None:
    allow_model = build_enterprise_understanding_model(
        _asset(
            permissions=[
                _permission(permission_id="p3", decision="allow", actions=["read"])
            ]
        )
    )
    deny_model = build_enterprise_understanding_model(
        _asset(
            permissions=[
                _permission(permission_id="p3", decision="deny", actions=["read"])
            ]
        )
    )

    assert allow_model["model_id"] != deny_model["model_id"]


def test_authorization_unknowns_are_local_not_global_business_unknowns() -> None:
    model = build_enterprise_understanding_model(
        _asset(permissions=[_permission(permission_id="p4", actions=["read"])])
    )

    assert model["authorization_unknowns"]
    assert not any(
        row.get("kind") == "ACTOR_AUTHORIZATION_UNRESOLVED"
        for row in model["unknowns"]
    )
    assert model["gate"]["authorization_gate"]["status"] == (
        "PARTIAL_AUTHORIZATION_UNRESOLVED"
    )
