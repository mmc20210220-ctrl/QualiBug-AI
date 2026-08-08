"""Schema-declared constraint dimension (root-cause fix for DB reachability).

A schema that declares money/quantity/idempotency columns without the guards
those columns need is a source-declared structural property of the system.
This test pins the derivation (DDL-only, industry-neutral) and the read-only
audit evaluation of the derived invariants.
"""
from __future__ import annotations

from typing import Any

from ai_test_asset_center.database_schema_invariants import (
    derive_database_constraint_rules,
)
from ai_test_asset_center.readonly_audit_protocol import (
    _compile_readonly_validation_audit,
    _evaluate_readonly_numeric_audit,
)

SCHEMA = """
CREATE TABLE orders (
  id UUID PRIMARY KEY,
  payable_amount NUMERIC(12,2) NOT NULL,
  total_amount NUMERIC(12,2) NOT NULL CHECK (total_amount >= 0),
  status TEXT NOT NULL CHECK (status IN ('PAID','CANCELLED'))
);
CREATE TABLE payments (
  id UUID PRIMARY KEY,
  idempotency_key TEXT,
  amount NUMERIC(12,2) NOT NULL CHECK (amount >= 0)
);
CREATE TABLE inventory (
  available_qty INT NOT NULL,
  safety_stock INT NOT NULL CHECK (safety_stock > 0)
);
CREATE TABLE cart_items (
  qty INT NOT NULL
);
CREATE TABLE orders_guarded (
  id UUID PRIMARY KEY,
  payable_amount NUMERIC(12,2) NOT NULL CHECK (payable_amount >= 0)
);
"""


def _tables() -> list[dict[str, Any]]:
    from ai_test_asset_center.enterprise_knowledge_center._parsing import _sql_tables

    return _sql_tables(SCHEMA, "runtime:database_schema:test")


def test_schema_derives_boundary_and_uniqueness_rules_from_missing_guards() -> None:
    rules = derive_database_constraint_rules(_tables(), "runtime:database_schema:test")
    by_key = {}
    for rule in rules:
        locator = rule.get("source_locator") or ""
        operator = rule.get("equation", {}).get("operator") or "uniqueness"
        by_key[f"{locator}:{operator}"] = rule

    # orders.payable_amount has no non-negative CHECK -> non_negative rule.
    assert "table:orders:column:payable_amount:non_negative" in by_key
    rule = by_key["table:orders:column:payable_amount:non_negative"]
    assert rule["kind"] == "numeric_boundary"
    assert rule["equation"]["operator"] == "non_negative"
    assert rule["operands"][0]["entity_ref"] == "orders"
    assert rule["operands"][0]["field"] == "payable_amount"

    # orders.total_amount HAS a CHECK -> no rule.
    assert "table:orders:column:total_amount:non_negative" not in by_key

    # inventory.available_qty has no guard -> both non_negative and positive.
    assert "table:inventory:column:available_qty:positive" in by_key
    assert "table:inventory:column:available_qty:non_negative" in by_key
    # safety_stock HAS a positive CHECK -> no rules.
    assert "table:inventory:column:safety_stock:positive" not in by_key

    # cart_items.qty has no guard -> boundary rules.
    assert "table:cart_items:column:qty:positive" in by_key

    # payments.idempotency_key is a uniqueness-intent column without UNIQUE.
    uniqueness = by_key.get("table:payments:column:idempotency_key:uniqueness")
    assert uniqueness is not None
    assert uniqueness["kind"] == "validation_uniqueness"
    assert uniqueness["operands"][0]["field"] == "idempotency_key"

    # Guarded table produces nothing for its guarded column.
    assert "table:orders_guarded:column:payable_amount:non_negative" not in by_key


def test_schema_rules_are_stable_and_deterministic() -> None:
    first = derive_database_constraint_rules(_tables(), "runtime:database_schema:test")
    second = derive_database_constraint_rules(_tables(), "runtime:database_schema:test")
    assert [r["rule_id"] for r in first] == [r["rule_id"] for r in second]


def test_readonly_numeric_audit_compiles_and_evaluates_boundary() -> None:
    compiled = _compile_readonly_validation_audit({
        "operation": {"method": "GET", "path": "/api/orders"},
        "operation_ref": "op:list-orders",
        "treatment_actor_ref": "actor:admin",
        "property_spec": {
            "template": "readonly_audit_validation",
            "invariant_ref": "bir:payable",
            "expression": {
                "kind": "numeric_boundary",
                "operator": "non_negative",
                "operands": [{"entity_ref": "orders", "field": "payable_amount"}],
            },
        },
    })
    assert compiled["status"] == "COMPILED"
    assertion = compiled["assertion"]
    assert assertion["kind"] == "readonly_numeric_audit"
    assert assertion["field"] == "payable_amount"
    assert assertion["operator"] == "non_negative"

    verdict = _evaluate_readonly_numeric_audit({
        "spec": assertion,
        "observations": {
            "status_code": 200,
            "body": {"items": [
                {"payable_amount": "100.00"},
                {"payable_amount": "0.00"},
                {"payable_amount": "-5.00"},
            ]},
        },
    })
    assert verdict["passed"] is False
    assert verdict["reason_code"] == "NUMERIC_BOUNDARY_VIOLATION_OBSERVED"

    clean = _evaluate_readonly_numeric_audit({
        "spec": assertion,
        "observations": {
            "status_code": 200,
            "body": {"items": [
                {"payable_amount": "100.00"},
                {"payable_amount": "1.00"},
            ]},
        },
    })
    assert clean["passed"] is True

    # Too few rows must never read as a verified boundary.
    small = _evaluate_readonly_numeric_audit({
        "spec": assertion,
        "observations": {
            "status_code": 200,
            "body": {"items": [{"payable_amount": "100.00"}]},
        },
    })
    assert small["passed"] is None
    assert small["reason_code"] == "AUDIT_COLLECTION_TOO_SMALL"


def test_readonly_audit_positive_operator_rejects_zero() -> None:
    compiled = _compile_readonly_validation_audit({
        "operation": {"method": "GET", "path": "/api/cart"},
        "operation_ref": "op:list-cart",
        "treatment_actor_ref": "actor:buyer",
        "property_spec": {
            "template": "readonly_audit_validation",
            "invariant_ref": "bir:qty",
            "expression": {
                "kind": "numeric_boundary",
                "operator": "positive",
                "operands": [{"entity_ref": "cart_items", "field": "qty"}],
            },
        },
    })
    assert compiled["status"] == "COMPILED"
    assert compiled["assertion"]["operator"] == "positive"

    zero_row = _evaluate_readonly_numeric_audit({
        "spec": compiled["assertion"],
        "observations": {
            "status_code": 200,
            "body": [{"qty": 0}, {"qty": 3}],
        },
    })
    assert zero_row["passed"] is False

    negative_row = _evaluate_readonly_numeric_audit({
        "spec": compiled["assertion"],
        "observations": {
            "status_code": 200,
            "body": [{"qty": -1}, {"qty": 3}],
        },
    })
    assert negative_row["passed"] is False
