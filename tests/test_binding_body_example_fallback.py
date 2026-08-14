"""Regression: unresolved body placeholders fall back to source-declared
request-schema example/default values instead of BLOCKING.

E2E finding (2026-08-07, benchmark run 3): 546 binding blocks carried
``BODY_PARAMETER_NOT_SOURCE_BOUND`` — write obligations whose request-body
placeholders (e.g. POST /api/cart/merge userIds, POST /api/products/admin
sku/price, POST /api/coupons/admin/create code/amount) had neither a read
resolver, a fixture, nor a credential source. The operations' own request
schema declares example/default values for exactly those fields, but the
binding plan never consulted them, so the obligations never executed.

Fix mirrors the path-parameter example fallback (7ed1d394): the operation's
own request-body schema property example/default is the last source-grounded
value available; a placeholder without one stays visibly BLOCKED.
"""
from __future__ import annotations

from ai_test_asset_center.runtime_binding_graph import (
    BINDING_PRIORITY,
    build_binding_plan,
    _source_declared_body_example_bindings,
)


def _op_with_body_schema(
    *,
    properties: dict,
    path: str = "/api/things",
    method: str = "POST",
    request_example: dict | None = None,
) -> dict:
    return {
        "id": f"op-{method.lower()}-{path}",
        "method": method,
        "path": path,
        "request_schema": {
            "content": {
                "application/json": {
                    "schema": {"properties": properties},
                }
            }
        },
        "request_example": request_example,
        "parameters": [],
    }


def test_body_example_bindings_resolve_declared_example() -> None:
    op = _op_with_body_schema(properties={
        "sku": {"type": "string", "example": "SKU-PHONE-001"},
        "price": {"type": "number", "default": 6999},
    })
    bindings = _source_declared_body_example_bindings(op, ["sku", "price"], {})
    assert bindings is not None
    assert bindings["sku"]["materialized_value"] == "SKU-PHONE-001"
    assert bindings["price"]["materialized_value"] == "6999"
    assert bindings["sku"]["source_priority"] == "source_declared_body_example"


def test_body_example_bindings_match_body_path_leaf() -> None:
    """An identity-shaped body path (user.id) is fail-closed, never source-bound.

    Source-declared request-schema examples/defaults may supply ordinary
    business scalars, but never resource identity. ``user_id`` names an
    identity reference, so the source-example fallback must refuse to bind it
    (return None) rather than minting a fabricated ``42`` resource id.
    """
    op = _op_with_body_schema(properties={
        "user": {
            "type": "object",
            "properties": {"id": {"type": "integer", "example": 42}},
        }
    })
    bindings = _source_declared_body_example_bindings(
        op,
        ["user_id"],
        {"user_id": ["user.id"]},
    )
    assert bindings is None


def test_body_example_bindings_return_none_without_examples() -> None:
    op = _op_with_body_schema(properties={
        "name": {"type": "string"},
        "quantity": {"type": "integer"},
    })
    assert _source_declared_body_example_bindings(op, ["name"], {}) is None


def test_binding_plan_uses_body_example_fallback() -> None:
    op = _op_with_body_schema(
        properties={
            "code": {"type": "string", "example": "NEW100"},
            "amount": {"type": "number", "example": 500},
        },
        # The body template carries the placeholders the plan must bind.
        request_example={"code": "{code}", "amount": "{amount}"},
    )
    # A body template whose placeholders have no read resolver / fixture /
    # credential source: the plan must bind them from the declared examples
    # instead of marking BODY_PARAMETER_NOT_SOURCE_BOUND.
    plan = build_binding_plan(operation=op, obligation={})
    by_target = {row["target"]: row for row in plan}
    assert by_target["code"]["status"] == "bound"
    assert by_target["code"]["materialized_value"] == "NEW100"
    assert by_target["code"]["source_priority"] == "source_declared_body_example"
    assert by_target["amount"]["status"] == "bound"


def test_placeholder_without_example_stays_blocked() -> None:
    op = _op_with_body_schema(
        properties={"sku": {"type": "string"}},
        request_example={"sku": "{sku}"},
    )
    plan = build_binding_plan(operation=op, obligation={})
    by_target = {row["target"]: row for row in plan}
    assert by_target["sku"]["status"] == "blocked"
    assert by_target["sku"]["blocked_reason"] == "BODY_PARAMETER_NOT_SOURCE_BOUND"


def test_source_priority_registered() -> None:
    assert "source_declared_body_example" in BINDING_PRIORITY
