"""Explicit database-constraint authority regressions.

DDL may describe a constraint, but a missing constraint is never authority for
inventing the business rule that the schema 'should' have contained.
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
  idempotency_key TEXT UNIQUE,
  amount NUMERIC(12,2) NOT NULL CHECK (amount >= 0)
);
CREATE TABLE inventory (
  available_qty INT NOT NULL,
  safety_stock INT NOT NULL CHECK (safety_stock > 0)
);
CREATE TABLE cart_items (
  qty INT NOT NULL
);
"""


def _tables() -> list[dict[str, Any]]:
    from ai_test_asset_center.enterprise_knowledge_center._parsing import _sql_tables

    return _sql_tables(SCHEMA, "runtime:database_schema:test")


def _by_locator() -> dict[str, dict[str, Any]]:
    return {
        str(rule.get("source_locator") or ""): rule
        for rule in derive_database_constraint_rules(
            _tables(), "runtime:database_schema:test"
        )
    }


def test_missing_guards_never_generate_business_rules() -> None:
    by_locator = _by_locator()

    # Names such as payable_amount / available_qty / qty carry no authority by
    # themselves. Missing CHECK clauses must stay gaps, not fabricated rules.
    assert "table:orders:column:payable_amount:check" not in by_locator
    assert "table:inventory:column:available_qty:check" not in by_locator
    assert "table:cart_items:column:qty:check" not in by_locator


def test_only_explicit_check_and_unique_constraints_are_projected() -> None:
    by_locator = _by_locator()

    total = by_locator["table:orders:column:total_amount:check"]
    assert total["kind"] == "numeric_boundary"
    assert total["equation"]["operator"] == "non_negative"
    assert total["constraint_authority"]["kind"] == "DDL_CHECK"
    assert total["inference_used"] is False

    stock = by_locator["table:inventory:column:safety_stock:check"]
    assert stock["equation"]["operator"] == "positive"
    assert stock["constraint_authority"]["operator"] == ">"

    unique = by_locator["table:payments:column:idempotency_key:unique"]
    assert unique["kind"] == "validation_uniqueness"
    assert unique["constraint_authority"] == {
        "kind": "DDL_UNIQUE",
        "table": "payments",
        "column": "idempotency_key",
    }
    assert unique["inference_used"] is False


def test_schema_rules_are_stable_and_deterministic() -> None:
    first = derive_database_constraint_rules(
        _tables(), "runtime:database_schema:test"
    )
    second = derive_database_constraint_rules(
        _tables(), "runtime:database_schema:test"
    )
    assert [row["rule_id"] for row in first] == [
        row["rule_id"] for row in second
    ]


def test_readonly_numeric_audit_compiles_and_evaluates_explicit_boundary() -> None:
    compiled = _compile_readonly_validation_audit({
        "operation": {"method": "GET", "path": "/api/orders"},
        "operation_ref": "op:list-orders",
        "treatment_actor_ref": "actor:admin",
        "property_spec": {
            "template": "readonly_audit_validation",
            "invariant_ref": "bir:total",
            "expression": {
                "kind": "numeric_boundary",
                "operator": "non_negative",
                "operands": [
                    {"entity_ref": "orders", "field": "total_amount"}
                ],
            },
        },
    })
    assert compiled["status"] == "COMPILED"
    assertion = compiled["assertion"]
    assert assertion["kind"] == "readonly_numeric_audit"
    assert assertion["field"] == "total_amount"
    assert assertion["operator"] == "non_negative"

    verdict = _evaluate_readonly_numeric_audit({
        "spec": assertion,
        "observations": {
            "status_code": 200,
            "body": {"items": [
                {"total_amount": "100.00"},
                {"total_amount": "0.00"},
                {"total_amount": "-5.00"},
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
                {"total_amount": "100.00"},
                {"total_amount": "1.00"},
            ]},
        },
    })
    assert clean["passed"] is True

    # A numeric boundary is decidable on a single row (one observed row below
    # the boundary is already violation evidence), unlike a uniqueness claim
    # which needs two rows to exhibit a duplicate. A single clean row therefore
    # passes instead of sealing INDETERMINATE.
    small = _evaluate_readonly_numeric_audit({
        "spec": assertion,
        "observations": {
            "status_code": 200,
            "body": {"items": [{"total_amount": "100.00"}]},
        },
    })
    assert small["passed"] is True
    assert small["reason_code"] == ""


def test_readonly_audit_positive_operator_rejects_zero() -> None:
    compiled = _compile_readonly_validation_audit({
        "operation": {"method": "GET", "path": "/api/inventory"},
        "operation_ref": "op:list-inventory",
        "treatment_actor_ref": "actor:operator",
        "property_spec": {
            "template": "readonly_audit_validation",
            "invariant_ref": "bir:safety-stock",
            "expression": {
                "kind": "numeric_boundary",
                "operator": "positive",
                "operands": [
                    {"entity_ref": "inventory", "field": "safety_stock"}
                ],
            },
        },
    })
    assert compiled["status"] == "COMPILED"
    assert compiled["assertion"]["operator"] == "positive"

    zero_row = _evaluate_readonly_numeric_audit({
        "spec": compiled["assertion"],
        "observations": {
            "status_code": 200,
            "body": [{"safety_stock": 0}, {"safety_stock": 3}],
        },
    })
    assert zero_row["passed"] is False

    negative_row = _evaluate_readonly_numeric_audit({
        "spec": compiled["assertion"],
        "observations": {
            "status_code": 200,
            "body": [{"safety_stock": -1}, {"safety_stock": 3}],
        },
    })
    assert negative_row["passed"] is False
