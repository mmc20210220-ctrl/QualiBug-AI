from __future__ import annotations


def _operation() -> dict:
    return {
        "id": "create-order",
        "method": "POST",
        "path": "/api/orders",
        "request_schema": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "orderId": {"type": "string", "example": "DEMO-ORDER-1"},
                            "address_id": {"type": "string", "default": "ADDR-DEMO"},
                            "amount": {"type": "number", "example": 12.5},
                            "reason": {"type": "string", "default": "source reason"},
                        },
                    }
                }
            }
        },
    }


def test_identity_shaped_examples_never_become_bound_runtime_ids() -> None:
    from ai_test_asset_center.runtime_binding_graph import (
        _source_declared_body_example_bindings,
    )

    bindings = _source_declared_body_example_bindings(
        _operation(),
        ["orderId", "address_id"],
        {"orderId": ["orderId"], "address_id": ["address_id"]},
    )

    assert bindings is None


def test_non_identity_business_scalar_may_use_source_example() -> None:
    from ai_test_asset_center.runtime_binding_graph import (
        _source_declared_body_example_bindings,
    )

    bindings = _source_declared_body_example_bindings(
        _operation(),
        ["amount", "reason"],
        {"amount": ["amount"], "reason": ["reason"]},
    )

    assert bindings is not None
    assert bindings["amount"]["source_priority"] == "source_declared_body_example"
    assert bindings["amount"]["materialized_value"] == "12.5"
    assert bindings["reason"]["materialized_value"] == "source reason"


def test_identity_classifier_does_not_confuse_business_word_paid_with_id() -> None:
    from ai_test_asset_center.runtime_binding_graph import _identity_shaped_target

    assert _identity_shaped_target("paid") is False
    assert _identity_shaped_target("valid") is False
    assert _identity_shaped_target("orderId") is True
    assert _identity_shaped_target("order_id") is True
    assert _identity_shaped_target("resourceRef") is True
