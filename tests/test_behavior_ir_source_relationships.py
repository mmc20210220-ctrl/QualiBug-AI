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

    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="project")
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

    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="project")

    gap = next(
        row
        for row in ir["coverage_gaps"]
        if row.get("gap_type") == "source_relationship_unresolved"
    )
    assert gap["relationship_id"] == "edge-dangling"
    assert gap["source_rule_ref"] == "rule-one"
    assert gap["source_operation_ref"] == "api:GET:/missing"
    assert gap["operation_match_count"] == 0


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

    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="project")
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
