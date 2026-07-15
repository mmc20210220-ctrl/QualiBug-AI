from __future__ import annotations

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir


def _operation_ref(ir: dict, operation_id: str) -> str:
    return next(
        operation["id"]
        for operation in ir["operations"]
        if operation.get("operation_id") == operation_id
    )


def test_rule_to_interface_relationship_becomes_exact_ir_relation() -> None:
    asset = {
        "rule_library": [{
            "rule_id": "rule-conservation",
            "statement": "The declared quantity equation must be conserved.",
            "kind": "conservation",
            "source_id": "requirements",
        }],
        "interfaces": [{
            "interface_id": "api:POST:/transfers",
            "operation_id": "create_transfer",
            "method": "POST",
            "path": "/transfers",
            "source_id": "openapi",
        }],
        "relationships": [{
            "edge_id": "edge-rule-operation",
            "from": "rule-conservation",
            "to": "api:POST:/transfers",
            "relation": "rule_to_interface",
            "confidence": 0.84,
        }],
    }

    ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id="project",
        runtime_actors=[
            {"role": "operator", "account_ref": "operator_a", "secret_ref": "secret_ref:test_accounts:operator_a"},
            {"role": "reader", "account_ref": "reader_a", "secret_ref": "secret_ref:test_accounts:reader_a"},
        ],
    )
    invariant_ref = ir["invariants"][0]["id"]
    operation_ref = _operation_ref(ir, "create_transfer")

    assert any(
        relation["relation_type"] == "conserves"
        and relation["from_ref"] == operation_ref
        and relation["to_ref"] == invariant_ref
        and relation["operation_ref"] == operation_ref
        for relation in ir["relations"]
    )
    compiled = compile_obligations_from_behavior_ir(ir)
    assert any(
        obligation["risk_family"] == "conservation"
        and obligation["property"]["operation_ref"] == operation_ref
        for obligation in compiled["obligations"]
    )


def test_dangling_rule_to_interface_relationship_is_a_typed_gap() -> None:
    asset = {
        "rule_library": [{
            "rule_id": "rule-one",
            "statement": "A declared invariant must hold.",
            "kind": "validation",
        }],
        "interfaces": [{
            "interface_id": "api:GET:/resources",
            "operation_id": "list_resources",
            "method": "GET",
            "path": "/resources",
        }],
        "relationships": [{
            "edge_id": "edge-dangling",
            "from": "rule-one",
            "to": "api:GET:/missing",
            "relation": "rule_to_interface",
        }],
    }

    ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id="project",
        runtime_actors=[
            {"role": "operator", "account_ref": "operator_a", "secret_ref": "secret_ref:test_accounts:operator_a"},
            {"role": "reader", "account_ref": "reader_a", "secret_ref": "secret_ref:test_accounts:reader_a"},
        ],
    )

    gap = next(
        row
        for row in ir["coverage_gaps"]
        if row.get("gap_type") == "source_relationship_unresolved"
    )
    assert gap["relationship_id"] == "edge-dangling"
    assert gap["source_rule_ref"] == "rule-one"
    assert gap["source_operation_ref"] == "api:GET:/missing"
    assert gap["operation_match_count"] == 0


def test_token_overlap_rule_to_interface_candidate_is_gap_not_compiled() -> None:
    asset = {
        "rule_library": [{
            "rule_id": "rule-cart-quantity",
            "statement": "Cart quantity must be conserved.",
            "kind": "conservation",
        }],
        "interfaces": [{
            "interface_id": "api:POST:/cart/items",
            "operation_id": "add_cart_item",
            "method": "POST",
            "path": "/cart/items",
        }],
        "relationships": [{
            "edge_id": "edge-token-overlap-only",
            "from": "rule-cart-quantity",
            "to": "api:POST:/cart/items",
            "relation": "rule_to_interface",
            "confidence": 0.71,
            "status": "candidate",
            "derivation": "token_overlap",
            "evidence_gate": "token_overlap_only_requires_explicit_source_relation",
            "evidence": {"token_overlap": ["cart", "quantity"]},
        }],
    }

    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="project")
    operation_ref = _operation_ref(ir, "add_cart_item")

    assert not any(
        relation.get("source_relationship_ref") == "edge-token-overlap-only"
        and relation.get("operation_ref") == operation_ref
        for relation in ir["relations"]
    )
    gap = next(
        row
        for row in ir["coverage_gaps"]
        if row.get("gap_type") == "source_relationship_candidate_only"
    )
    assert gap["relationship_id"] == "edge-token-overlap-only"
    assert gap["candidate_reason"] == "token_overlap_only_requires_explicit_source_relation"

    compiled = compile_obligations_from_behavior_ir(ir)
    assert not any(
        obligation["risk_family"] == "conservation"
        and obligation["property"].get("operation_ref") == operation_ref
        for obligation in compiled["obligations"]
    )


def test_missing_permission_action_is_unknown_not_deny() -> None:
    asset = {
        "permission_matrix": [
            {"role": "operator", "resource": "/resources/{id}", "actions": ["write"]},
            {"role": "reader", "resource": "/resources/{id}", "actions": ["read"]},
        ],
        "operations": [{
            "operation_id": "update_resource",
            "method": "PUT",
            "path": "/resources/{id}",
        }],
    }

    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="project")
    operation_ref = _operation_ref(ir, "update_resource")
    reader_ref = next(actor["id"] for actor in ir["actors"] if actor["role"] == "reader")
    reader_relation = next(
        relation
        for relation in ir["relations"]
        if relation.get("operation_ref") == operation_ref
        and relation.get("actor_ref") == reader_ref
    )

    assert reader_relation["relation_type"] == "permission_unknown"
    assert reader_relation["permission_decision"] == "UNKNOWN"
    assert not any(
        relation["relation_type"] == "denies"
        and relation.get("operation_ref") == operation_ref
        and relation.get("actor_ref") == reader_ref
        for relation in ir["relations"]
    )
    compiled = compile_obligations_from_behavior_ir(ir)
    assert not any(
        obligation["risk_family"] == "authorization"
        and obligation["property"].get("operation_ref") == operation_ref
        for obligation in compiled["obligations"]
    )


def test_explicit_permission_deny_generates_authorization_obligation() -> None:
    asset = {
        "permission_matrix": [
            {"role": "operator", "resource": "/resources/{id}", "actions": ["manage"]},
            {
                "role": "reader",
                "resource": "/resources/{id}",
                "actions": ["write"],
                "decision": "deny",
            },
        ],
        "operations": [{
            "operation_id": "update_resource",
            "method": "PUT",
            "path": "/resources/{id}",
        }],
    }

    ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id="project",
        runtime_actors=[
            {"role": "operator", "account_ref": "operator_a", "secret_ref": "secret_ref:test_accounts:operator_a"},
            {"role": "reader", "account_ref": "reader_a", "secret_ref": "secret_ref:test_accounts:reader_a"},
        ],
    )
    operation_ref = _operation_ref(ir, "update_resource")
    reader = next(actor for actor in ir["actors"] if actor["role"] == "reader")
    assert reader["allowed_actions"] == []
    decisions = {
        relation["permission_decision"]
        for relation in ir["relations"]
        if relation.get("operation_ref") == operation_ref
        and relation.get("permission_decision")
    }
    assert decisions == {"PERMIT", "DENY"}

    compiled = compile_obligations_from_behavior_ir(ir)
    authorization = next(
        obligation
        for obligation in compiled["obligations"]
        if obligation["risk_family"] == "authorization"
    )
    assert authorization["property"]["operation_ref"] == operation_ref


def test_scoped_own_and_other_permissions_are_not_treated_as_conflicting() -> None:
    asset = {
        "permission_matrix": [
            {
                "role": "seller",
                "resource": "product",
                "actions": ["update"],
                "decision": "allow",
                "scope": "own",
            },
            {
                "role": "seller",
                "resource": "product",
                "actions": ["update"],
                "decision": "deny",
                "scope": "other_owner",
            },
        ],
        "operations": [{
            "operation_id": "update_product",
            "method": "PATCH",
            "path": "/products/{id}",
        }],
    }

    ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id="scoped-permissions",
        runtime_actors=[
            {
                "role": "seller",
                "account_ref": "seller_a",
                "secret_ref": "secret_ref:test_accounts:seller_a",
            },
            {
                "role": "seller",
                "account_ref": "seller_b",
                "secret_ref": "secret_ref:test_accounts:seller_b",
            },
        ],
    )
    operation_ref = _operation_ref(ir, "update_product")
    seller_relations = [
        relation
        for relation in ir["relations"]
        if relation.get("operation_ref") == operation_ref
        and relation.get("relation_type") in {"permits", "denies"}
    ]

    assert not any(
        conflict.get("conflict_type") == "permission_decision_conflict"
        and conflict.get("operation_ref") == operation_ref
        for conflict in ir["conflicts"]
    )
    assert {relation["relation_type"] for relation in seller_relations} == {"permits", "denies"}
    assert {row.get("scope") for relation in seller_relations for row in relation.get("preconditions", [])} == {
        "own",
        "other_owner",
    }

    obligations = compile_obligations_from_behavior_ir(ir)["obligations"]
    assert any(
        obligation["risk_family"] == "authorization"
        and obligation["property"]["operation_ref"] == operation_ref
        for obligation in obligations
    )


def test_unscoped_permission_allow_and_deny_still_fail_closed() -> None:
    asset = {
        "permission_matrix": [
            {
                "role": "operator",
                "resource": "resource",
                "actions": ["update"],
                "decision": "allow",
            },
            {
                "role": "operator",
                "resource": "resource",
                "actions": ["update"],
                "decision": "deny",
            },
        ],
        "operations": [{
            "operation_id": "update_resource",
            "method": "PATCH",
            "path": "/resources/{id}",
        }],
    }

    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="unscoped-permissions")
    operation_ref = _operation_ref(ir, "update_resource")

    assert any(
        conflict.get("conflict_type") == "permission_decision_conflict"
        and conflict.get("operation_ref") == operation_ref
        for conflict in ir["conflicts"]
    )
