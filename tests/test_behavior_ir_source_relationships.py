from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center.behavior_ir import (
    BehaviorIRError,
    build_behavior_ir_from_knowledge_asset,
    validate_behavior_ir,
)
from ai_test_asset_center.enterprise_knowledge_center import (
    build_enterprise_business_knowledge_asset,
    build_runtime_source_knowledge_overlay,
    merge_knowledge_asset_overlay,
)
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir

ROOT = Path(__file__).resolve().parents[1]


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


def test_rule_to_interface_does_not_infer_permission_from_role_text() -> None:
    asset = {
        "rule_library": [{
            "rule_id": "rule-refund-approval",
            "statement": "Only finance may approve a refund.",
            "kind": "authorization",
            "source_id": "prd-source",
        }],
        "interfaces": [{
            "interface_id": "api:POST:/refunds/:id/approve",
            "operation_id": "approve_refund",
            "method": "POST",
            "path": "/refunds/:id/approve",
            "source_id": "api-source",
            "source_excerpt": "Only finance may approve a refund.",
        }],
        "relationships": [{
            "edge_id": "edge-refund-approval",
            "from": "rule-refund-approval",
            "to": "api:POST:/refunds/:id/approve",
            "relation": "rule_to_interface",
            "status": "accepted",
            "derivation": "exact_source_section",
            "evidence_gate": "exact_source_section",
            "evidence": {
                "operation_locator": "POST /refunds/:id/approve",
                "statement_hash": "source-backed",
            },
        }],
    }

    ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id="project",
        runtime_actors=[{
            "role": "finance",
            "account_ref": "finance_a",
            "secret_ref": "secret_ref:test_accounts:finance_a",
        }],
    )

    assert not any(
        relation.get("relation_type") == "permits"
        and any(
            isinstance(source_ref, dict)
            and source_ref.get("kind") == "rule_to_interface_permit"
            for source_ref in relation.get("source_refs") or []
        )
        for relation in ir["relations"]
    )


def test_source_grounded_business_semantic_frame_survives_into_invariant() -> None:
    frame = {
        "schema_version": "qualibug.business-semantic-frame.v1",
        "modality": "PROHIBITED",
        "polarity": "negative",
        "condition": "account status is DISABLED",
        "subject": "account",
        "behavior": "sign in",
        "source_anchors": ["DISABLED"],
        "source_grounded": True,
    }
    asset = {
        "rule_library": [{
            "rule_id": "rule-disabled-sign-in",
            "statement": "account status is DISABLED, cannot sign in",
            "rule_type": "state_transition",
            "semantic_frame": frame,
            "source_id": "test-accounts",
            "source_locator": "line:7",
        }],
    }

    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="project")

    invariant = ir["invariants"][0]
    assert invariant["semantic_frame"] == frame
    # Semantic frame must not pollute expression — that rotates obligation_id.
    assert "modality" not in invariant["expression"]
    assert "polarity" not in invariant["expression"]
    assert "condition" not in invariant["expression"]
    assert "subject" not in invariant["expression"]
    assert "behavior" not in invariant["expression"]
    assert invariant["source_refs"][0]["locator"] == "line:7"


def test_semantic_frame_does_not_rotate_obligation_ids() -> None:
    """Enrichment on invariants must not change stable obligation_id fingerprints."""
    base_rule = {
        "rule_id": "rule-qty-conserved",
        "statement": "The declared quantity equation must be conserved.",
        "kind": "conservation",
        "source_id": "requirements",
        "source_locator": "line:1",
    }
    frame = {
        "schema_version": "qualibug.business-semantic-frame.v1",
        "modality": "INVARIANT",
        "polarity": "positive",
        "condition": "",
        "subject": "",
        "behavior": "The declared quantity equation must be conserved.",
        "source_anchors": ["quantity equation"],
        "source_grounded": True,
    }
    shared = {
        "interfaces": [{
            "interface_id": "api:POST:/transfers",
            "operation_id": "create_transfer",
            "method": "POST",
            "path": "/transfers",
            "source_id": "openapi",
        }],
        "relationships": [{
            "edge_id": "edge-rule-operation",
            "from": "rule-qty-conserved",
            "to": "api:POST:/transfers",
            "relation": "rule_to_interface",
            "confidence": 0.9,
            "source_id": "requirements",
        }],
    }
    ir_plain = build_behavior_ir_from_knowledge_asset(
        {"rule_library": [dict(base_rule)], **shared},
        project_id="project",
    )
    ir_framed = build_behavior_ir_from_knowledge_asset(
        {"rule_library": [{**base_rule, "semantic_frame": frame}], **shared},
        project_id="project",
    )
    assert ir_framed["invariants"][0]["semantic_frame"] == frame
    assert "modality" not in ir_framed["invariants"][0]["expression"]

    plain_ids = {
        row["obligation_id"]
        for row in compile_obligations_from_behavior_ir(ir_plain)["obligations"]
        if row.get("risk_family") == "conservation"
    }
    framed_ids = {
        row["obligation_id"]
        for row in compile_obligations_from_behavior_ir(ir_framed)["obligations"]
        if row.get("risk_family") == "conservation"
    }
    assert plain_ids
    assert plain_ids == framed_ids


def test_business_semantic_frame_rejects_invented_subject() -> None:
    asset = {
        "rule_library": [{
            "rule_id": "rule-disabled-sign-in",
            "statement": "account status is DISABLED, cannot sign in",
            "semantic_frame": {
                "schema_version": "qualibug.business-semantic-frame.v1",
                "modality": "PROHIBITED",
                "polarity": "negative",
                "condition": "account status is DISABLED",
                "subject": "invented administrator",
                "behavior": "sign in",
                "source_anchors": ["DISABLED"],
                "source_grounded": True,
            },
        }],
    }

    with pytest.raises(
        BehaviorIRError,
        match="business_semantic_frame_subject_not_in_source",
    ):
        build_behavior_ir_from_knowledge_asset(asset, project_id="project")


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


def test_exclusive_contract_field_relationship_compiles_diverse_families() -> None:
    asset = {
        "rule_library": [
            {
                "rule_id": "rule-inventory-conserved",
                "statement": "available_qty and locked_qty must stay non-negative.",
                "kind": "data_conservation",
                "source_id": "business_rules",
            },
            {
                "rule_id": "rule-pay-idempotent",
                "statement": "idempotencyKey must prevent duplicate payment capture.",
                "kind": "idempotency",
                "source_id": "business_rules",
            },
        ],
        "interfaces": [
            {
                "interface_id": "markdown_api:GET:/api/inventory/:sku",
                "operation_id": "get_inventory",
                "method": "GET",
                "path": "/api/inventory/:sku",
                "source_excerpt": "available_qty / locked_qty",
                "field_dictionary": ["available_qty", "locked_qty"],
            },
            {
                "interface_id": "markdown_api:POST:/api/inventory/reserve",
                "operation_id": "reserve_inventory",
                "method": "POST",
                "path": "/api/inventory/reserve",
                "source_excerpt": '{"sku":"X","qty":1}',
                "field_dictionary": ["sku", "qty"],
            },
            {
                "interface_id": "markdown_api:POST:/api/payments/pay",
                "operation_id": "pay_order",
                "method": "POST",
                "path": "/api/payments/pay",
                "source_excerpt": '{"orderId":"1","idempotencyKey":"abc"}',
                "field_dictionary": ["orderId", "idempotencyKey"],
            },
        ],
        "relationships": [],
    }
    # Authoritative edges are produced by the knowledge overlay helpers; attach
    # them here the same way merge_knowledge_asset_overlay would.
    from ai_test_asset_center.enterprise_knowledge_center import (
        _authoritative_rule_to_interface_edges,
    )

    asset["relationships"] = _authoritative_rule_to_interface_edges(
        asset["rule_library"],
        asset["interfaces"],
    )

    ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id="project",
        runtime_actors=[
            {
                "role": "buyer",
                "account_ref": "buyer_a",
                "secret_ref": "secret_ref:test_accounts:buyer_a",
            },
        ],
    )
    compiled = compile_obligations_from_behavior_ir(ir)
    assert compiled["by_family"]["conservation"] >= 1
    assert compiled["by_family"]["idempotency"] >= 1


def test_duplicate_state_machines_merge_state_nodes_by_stable_id() -> None:
    asset = {
        "state_machines": [
            {
                "state_machine_id": "sm-order-source",
                "entity": "order",
                "source_id": "src-rules",
                "states": ["CREATED", "PAID"],
            },
            {
                "state_machine_id": "sm-order-overlay",
                "entity": "order",
                "source_id": "runtime-prd",
                "states": ["CREATED", "PAID", "CANCELLED"],
            },
        ],
    }
    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="project")
    assert validate_behavior_ir(ir, require_explicit_relations=False) == []
    names = sorted(state.get("name") for state in ir["states"])
    assert names == ["CANCELLED", "CREATED", "PAID"]
    created = next(state for state in ir["states"] if state.get("name") == "CREATED")
    source_ids = {
        ref.get("source_id")
        for ref in created.get("source_refs") or []
        if isinstance(ref, dict)
    }
    assert source_ids == {"src-rules", "runtime-prd"}


def test_benchmark_mall_asset_overlay_merge_builds_valid_behavior_ir() -> None:
    asset = build_enterprise_business_knowledge_asset("benchmark_mall")
    overlay = build_runtime_source_knowledge_overlay(
        prd_text=(ROOT / "projects/benchmark_mall/input/BUSINESS_RULES.md").read_text(
            encoding="utf-8"
        ),
        api_spec_text=(ROOT / "projects/benchmark_mall/input/API_SPEC.md").read_text(
            encoding="utf-8"
        ),
        db_schema_text=(ROOT / "projects/benchmark_mall/input/DB_SCHEMA.md").read_text(
            encoding="utf-8"
        ),
    )
    merged = merge_knowledge_asset_overlay(asset, overlay)
    ir = build_behavior_ir_from_knowledge_asset(merged, project_id="benchmark_mall")
    assert validate_behavior_ir(ir) == []


def test_sibling_action_names_do_not_create_compensation_relation() -> None:
    asset = {
        "interfaces": [
            {
                "interface_id": "markdown_api:POST:/api/inventory/reserve",
                "operation_id": "reserve_inventory",
                "method": "POST",
                "path": "/api/inventory/reserve",
                "summary": "Reserve inventory stock",
                "request_example": {"sku": "X", "qty": 1, "orderId": "1"},
            },
            {
                "interface_id": "markdown_api:POST:/api/inventory/release",
                "operation_id": "release_inventory",
                "method": "POST",
                "path": "/api/inventory/release",
                "summary": "Release reserved inventory stock",
                "request_example": {"sku": "X", "qty": 1, "orderId": "1"},
            },
            {
                "interface_id": "markdown_api:POST:/api/inventory/consume",
                "operation_id": "consume_inventory",
                "method": "POST",
                "path": "/api/inventory/consume",
                "summary": "Consume locked inventory after payment",
                "request_example": {"sku": "X", "qty": 1, "orderId": "1"},
            },
        ],
    }
    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="project")
    reserve_ref = _operation_ref(ir, "reserve_inventory")
    release_ref = _operation_ref(ir, "release_inventory")
    consume_ref = _operation_ref(ir, "consume_inventory")
    assert not any(
        relation.get("relation_type") == "compensates"
        and relation.get("to_ref") == reserve_ref
        and relation.get("operation_ref") == release_ref
        for relation in ir["relations"]
    )
    assert not any(
        relation.get("relation_type") == "compensates"
        and relation.get("to_ref") == consume_ref
        for relation in ir["relations"]
    )


def test_shared_request_keys_do_not_prove_action_compensation() -> None:

    asset = {
        "interfaces": [
            {
                "interface_id": "markdown_api:POST:/api/inventory/reserve",
                "operation_id": "reserve_inventory",
                "method": "POST",
                "path": "/api/inventory/reserve",
                "summary": "预占库存",
                "request_example": {"sku": "X", "qty": 1, "orderId": "1"},
            },
            {
                "interface_id": "markdown_api:POST:/api/inventory/release",
                "operation_id": "release_inventory",
                "method": "POST",
                "path": "/api/inventory/release",
                "summary": "释放预占库存",
                "request_example": {"sku": "X", "qty": 1, "orderId": "1"},
            },
        ],
    }
    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="project")
    reserve_ref = _operation_ref(ir, "reserve_inventory")
    release_ref = _operation_ref(ir, "release_inventory")
    assert not any(
        relation.get("relation_type") == "compensates"
        and relation.get("to_ref") == reserve_ref
        and relation.get("operation_ref") == release_ref
        for relation in ir["relations"]
    )


def test_benchmark_mall_conservation_writes_fail_closed_without_actor_binding() -> None:
    api_text = (
        ROOT / "projects" / "benchmark_mall" / "input" / "API_SPEC.md"
    ).read_text(encoding="utf-8")
    rules_text = (
        ROOT / "projects" / "benchmark_mall" / "input" / "BUSINESS_RULES.md"
    ).read_text(encoding="utf-8")
    overlay = build_runtime_source_knowledge_overlay(
        prd_text=rules_text,
        api_spec_text=api_text,
        db_schema_text="",
    )
    ir = build_behavior_ir_from_knowledge_asset(
        overlay,
        project_id="benchmark_mall",
        runtime_actors=[
            {
                "role": "buyer",
                "account_ref": "buyer01",
                "secret_ref": "secret_ref:test_accounts:buyer01",
            },
            {
                "role": "admin",
                "account_ref": "admin",
                "secret_ref": "secret_ref:test_accounts:admin",
            },
        ],
    )
    from ai_test_asset_center.experiment_compiler import compile_experiments

    compiled = compile_obligations_from_behavior_ir(ir)
    conservation = [
        row for row in compiled["obligations"] if row.get("risk_family") == "conservation"
    ]
    assert conservation, compiled["by_family"]
    experiments = compile_experiments(
        conservation,
        behavior_ir=ir,
        environment_type="test",
        policy_version="v1",
    )
    assert experiments["blocked_count"] >= 1
    assert experiments["block_reason_counts"].get("BLOCKED_MISSING_ACTOR", 0) >= 1


def test_benchmark_mall_runtime_overlay_diversifies_obligation_families() -> None:
    api_text = (
        ROOT / "projects" / "benchmark_mall" / "input" / "API_SPEC.md"
    ).read_text(encoding="utf-8")
    rules_text = (
        ROOT / "projects" / "benchmark_mall" / "input" / "BUSINESS_RULES.md"
    ).read_text(encoding="utf-8")
    overlay = build_runtime_source_knowledge_overlay(
        prd_text=rules_text,
        api_spec_text=api_text,
        db_schema_text="",
    )
    ir = build_behavior_ir_from_knowledge_asset(
        overlay,
        project_id="benchmark_mall",
        runtime_actors=[
            {
                "role": "buyer",
                "account_ref": "buyer01",
                "secret_ref": "secret_ref:test_accounts:buyer01",
            },
            {
                "role": "admin",
                "account_ref": "admin",
                "secret_ref": "secret_ref:test_accounts:admin",
            },
        ],
    )
    compiled = compile_obligations_from_behavior_ir(ir)
    assert int(compiled["by_family"].get("conservation") or 0) >= 2, compiled["by_family"]
    assert int(compiled["by_family"].get("concurrency") or 0) == 0, compiled["by_family"]
    diverse = (
        int(compiled["by_family"].get("conservation") or 0)
        + int(compiled["by_family"].get("concurrency") or 0)
        + int(compiled["by_family"].get("idempotency") or 0)
    )
    assert diverse >= 2, compiled["by_family"]


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


def test_causal_postcondition_keeps_parent_source_binding_unambiguous() -> None:
    asset = {
        "rule_library": [{
            "rule_id": "rule-order-state",
            "statement": "order_status must be PENDING_PAYMENT",
            "kind": "state_transition",
            "causal_chain": {
                "trigger_action": "订单",
                "postconditions": [{
                    "entity": "order",
                    "field": "status",
                    "must_become": "PAID",
                }],
            },
        }],
        "interfaces": [{
            "interface_id": "api:POST:/orders",
            "operation_id": "submit_order",
            "method": "POST",
            "path": "/orders",
        }],
        "relationships": [{
            "edge_id": "edge-order-state",
            "from": "rule-order-state",
            "to": "api:POST:/orders",
            "relation": "rule_to_interface",
            "status": "accepted",
            "derivation": "exact_source_section",
            "evidence_gate": "exact_source_section",
        }],
    }

    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="causal-binding")
    operation_ref = _operation_ref(ir, "submit_order")
    linked_invariants = [
        invariant
        for invariant in ir["invariants"]
        if "rule-order-state" in (invariant.get("source_rule_refs") or [])
    ]
    assert len(linked_invariants) == 2
    assert all(operation_ref in (row.get("operation_refs") or []) for row in linked_invariants)
    assert len([
        relation
        for relation in ir["relations"]
        if relation.get("source_relationship_ref") == "edge-order-state"
    ]) == 2
    assert not any(
        gap.get("gap_type") == "source_relationship_unresolved"
        and gap.get("relationship_id") == "edge-order-state"
        for gap in ir["coverage_gaps"]
    )


def test_causal_trigger_text_does_not_bind_an_operation() -> None:
    asset = {
        "rule_library": [{
            "rule_id": "rule-order-state",
            "statement": "order_status must be PENDING_PAYMENT",
            "kind": "state_transition",
            "causal_chain": {
                "trigger_action": "订单",
                "postconditions": [{
                    "entity": "order",
                    "field": "status",
                    "must_become": "PAID",
                }],
            },
        }],
        "interfaces": [{
            "interface_id": "api:POST:/orders",
            "operation_id": "submit_order",
            "method": "POST",
            "path": "/orders",
        }],
    }

    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="causal-no-text-join")
    operation_ref = _operation_ref(ir, "submit_order")
    assert not any(
        operation_ref in (invariant.get("operation_refs") or [])
        for invariant in ir["invariants"]
    )
    assert not any(
        relation.get("operation_ref") == operation_ref
        and relation.get("relation_type") in {"transitions", "conserves", "observes"}
        for relation in ir["relations"]
    )
