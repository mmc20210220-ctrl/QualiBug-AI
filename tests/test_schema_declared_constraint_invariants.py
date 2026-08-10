"""Regression: schema-declared constraint columns derive verification invariants.

Coupon-style constraints (user_limit / global_limit / max_discount /
category_scope) are declared as table columns in the schema even when no
prose rule documents them — enterprise documents are never complete. The
column declaration is source material: a constraint-verification invariant
must compile on the entity's decision/consumption surfaces so the constraint
is exercised and violations are runtime-observed (the target accepting what
the declared column forbids).
"""
from __future__ import annotations

from ai_test_asset_center.behavior_ir_core import (
    build_behavior_ir_from_knowledge_asset,
)


def _minimal_asset() -> dict:
    return {
        "data_tables": [
            {
                "name": "coupons",
                "columns": [
                    "id", "code", "user_limit", "global_limit",
                    "max_discount", "category_scope", "min_order_amount",
                ],
            },
        ],
        "business_objects": [
            {"name": "coupons", "kind": "resource", "source_id": "schema.sql"},
        ],
        "rule_library": [],
        "field_dictionary": [],
        "permission_matrix": [],
        "state_machines": [],
        "relations": [],
    }


def _decision_ops() -> list[dict]:
    return [
        {"id": "op_validate", "operation_id": "api:POST:/api/coupons/validate",
         "method": "POST", "path": "/api/coupons/validate",
         "entity_refs": ["coupons"], "read_write": "write"},
        {"id": "op_use", "operation_id": "api:POST:/api/coupons/use",
         "method": "POST", "path": "/api/coupons/use",
         "entity_refs": ["coupons"], "read_write": "write"},
        {"id": "op_claim", "operation_id": "api:POST:/api/coupons/claim",
         "method": "POST", "path": "/api/coupons/claim",
         "entity_refs": ["coupons"], "read_write": "write"},
    ]


def test_constraint_columns_derive_invariants() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        _minimal_asset(), api_operations=_decision_ops()
    )
    constraints = [
        inv for inv in (ir.get("invariants") or [])
        if (inv.get("derived_invariant_kind") == "schema_declared_constraint")
    ]
    by_field = {
        ((inv.get("expression") or {}).get("operands") or [{}])[0].get("field"): inv
        for inv in constraints
    }
    assert by_field.get("user_limit", {}).get("expression", {}).get("constraint_kind") == "USAGE_LIMIT"
    assert by_field.get("global_limit", {}).get("expression", {}).get("constraint_kind") == "USAGE_LIMIT"
    assert by_field.get("max_discount", {}).get("expression", {}).get("constraint_kind") == "AMOUNT_BOUND"
    assert by_field.get("category_scope", {}).get("expression", {}).get("constraint_kind") == "CATEGORY_SCOPE"
    # Every derived invariant binds the entity's decision surfaces.
    for inv in constraints:
        assert len(inv.get("operation_refs") or []) >= 1
        assert (inv.get("expression") or {}).get("kind") == "validation"
        assert inv.get("derivation") == "schema-derived"


def test_no_constraint_columns_no_invariants() -> None:
    asset = _minimal_asset()
    asset["data_tables"][0]["columns"] = ["id", "code", "name", "status"]
    ir = build_behavior_ir_from_knowledge_asset(asset, api_operations=_decision_ops())
    constraints = [
        inv for inv in (ir.get("invariants") or [])
        if (inv.get("derived_invariant_kind") == "schema_declared_constraint")
    ]
    assert constraints == []


def test_no_decision_surface_no_invariants() -> None:
    asset = _minimal_asset()
    ir = build_behavior_ir_from_knowledge_asset(asset, api_operations=[])
    constraints = [
        inv for inv in (ir.get("invariants") or [])
        if (inv.get("derived_invariant_kind") == "schema_declared_constraint")
    ]
    assert constraints == []


def _state_gate_asset() -> dict:
    return {
        "data_tables": [
            {"name": "products", "columns": ["id", "sku", "status", "price"],
             "foreign_keys": []},
            {"name": "order_items", "columns": ["id", "order_id", "sku", "qty"],
             "foreign_keys": ["orders", "products"]},
        ],
        "business_objects": [
            {"name": "products", "kind": "resource", "source_id": "schema.sql"},
            {"name": "order_items", "kind": "resource", "source_id": "schema.sql"},
        ],
        "rule_library": [],
        "field_dictionary": [],
        "permission_matrix": [],
        "state_machines": [],
        "relations": [],
    }


def test_state_gate_derives_for_fk_consumed_entity() -> None:
    """products.status is consumed by order creation (order_items FK): the
    consumption surface must carry a state-eligibility invariant so ordering
    an inactive (DRAFT) product is exercised — the target accepting it IS
    the runtime-observed defect."""
    ops = [
        {"id": "op_create_order", "operation_id": "api:POST:/api/orders",
         "method": "POST", "path": "/api/orders",
         "request_example": {"items": [{"sku": "SKU-1", "qty": 1}]},
         "read_write": "write"},
        {"id": "op_create_product", "operation_id": "api:POST:/api/products/admin",
         "method": "POST", "path": "/api/products/admin",
         "request_example": {"sku": "SKU-NEW", "status": "DRAFT", "price": "10"},
         "read_write": "write"},
    ]
    ir = build_behavior_ir_from_knowledge_asset(
        _state_gate_asset(), api_operations=ops
    )
    gates = [
        inv for inv in (ir.get("invariants") or [])
        if (inv.get("derived_invariant_kind") == "schema_declared_state_gate")
    ]
    assert len(gates) == 1
    gate = gates[0]
    assert (gate.get("expression") or {}).get("operator") == "state_eligible"
    refs = set(gate.get("operation_refs") or [])
    assert "api:POST:/api/orders" in refs
    # The entity's own create surface is not a consumption.
    assert "api:POST:/api/products/admin" not in refs
    assert gate.get("derivation") == "schema-derived"


def test_state_gate_needs_fk_consumption() -> None:
    """An entity with a status column but no foreign-key consumer gets no
    state gate (nothing consumes it, nothing to gate)."""
    asset = _state_gate_asset()
    asset["data_tables"][1]["foreign_keys"] = ["orders"]
    ir = build_behavior_ir_from_knowledge_asset(asset, api_operations=[
        {"id": "op_create_order", "operation_id": "api:POST:/api/orders",
         "method": "POST", "path": "/api/orders",
         "request_example": {"items": [{"sku": "SKU-1"}]}, "read_write": "write"},
    ])
    gates = [
        inv for inv in (ir.get("invariants") or [])
        if (inv.get("derived_invariant_kind") == "schema_declared_state_gate")
    ]
    assert gates == []
