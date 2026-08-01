"""Regression: disposable order fixture with state-transition compensation.

On the Windows-native benchmark the order list is empty at run start, so
payment/refund/cancel obligations blocked at execution with
``runtime_read_binding_unresolved:order_id/id`` (empty collection) because the
auto-fixture creator only accepted DELETE compensation routes. The benchmark
documents no order DELETE; it documents ``POST /api/orders/:id/cancel`` as the
identity-bound compensating action.

The auto-fixture creator now accepts an identity-bound POST/PUT/PATCH
state-transition action only when the Behavior IR carries an explicit
source-backed ``compensates`` relation. The cleanup restoration proof derives
the created identity from the materialized compensation route when the create
request body carries a server-minted id.
"""
from __future__ import annotations

from ai_test_asset_center.experiment_cleanup import (
    _cleanup_compensates_created_resource,
)
from ai_test_asset_center.experiment_fixture_materializer_core import (
    _auto_fixture_create_for_binding_target,
)
from ai_test_asset_center.runtime_binding_materializer import (
    validated_fixture_setup,
)


def _orders_ir() -> dict:
    return {
        "operations": [
            {
                "id": "op_create_order",
                "method": "POST",
                "path": "/api/orders",
                "request_example": {
                    "items": [{"sku": "SKU-PHONE-001", "qty": 1}],
                    "couponCode": "NEW100",
                    "addressId": "<address_id>",
                },
            },
            {
                "id": "op_list_orders",
                "method": "GET",
                "path": "/api/orders",
            },
            {
                "id": "op_addresses",
                "method": "GET",
                "path": "/api/users/addresses",
            },
            {
                "id": "op_cancel_order",
                "method": "POST",
                "path": "/api/orders/:id/cancel",
            },
            {
                "id": "op_ship_order",
                "method": "POST",
                "path": "/api/orders/:id/ship",
            },
        ],
        "actors": [],
        "relations": [
            {
                "relation_type": "compensates",
                "from_ref": "op_cancel_order",
                "to_ref": "op_create_order",
                "operation_ref": "op_cancel_order",
                "status": "accepted",
                "source_refs": [{"source_id": "orders-api"}],
            }
        ],
    }


def test_auto_fixture_uses_state_transition_compensation_when_no_delete() -> None:
    binding = {
        "target": "order_id",
        "target_path": "/{order_id}",
        "status": "runtime_resolvable",
        "resolver_operations": [
            {
                "operation_ref": "op_list_orders",
                "method": "GET",
                "path": "/api/orders",
            }
        ],
    }
    ops = {
        row["id"]: row
        for row in _orders_ir()["operations"]
    }
    actors = {
        "actor_buyer": {
            "id": "actor_buyer",
            "role": "buyer",
            "credential_secret_ref": "secret:buyer",
        }
    }
    auto = _auto_fixture_create_for_binding_target(
        "order_id",
        binding,
        ops,
        {},
        actors=actors,
        behavior_ir=_orders_ir(),
    )
    assert auto is not None
    setup = auto["fixture_setup"]
    assert setup["operation_ref"] == "op_create_order"
    assert setup["method"] == "POST"
    cleanup_paths = {
        (row.get("method"), row.get("path"))
        for row in setup["cleanup_operations"]
    }
    assert ("POST", "/api/orders/{id}/cancel") in cleanup_paths
    # Ship/confirm are lifecycle-forward actions and must not be picked as
    # compensation.
    assert all(
        "ship" not in (row.get("path") or "") for row in setup["cleanup_operations"]
    )
    cancel_row = next(
        row
        for row in setup["cleanup_operations"]
        if (row.get("path") or "").endswith("/cancel")
    )
    assert cancel_row["compensates_operation_ref"] == "op_create_order"


def test_validated_fixture_setup_accepts_cancel_compensation() -> None:
    binding = {
        "target": "order_id",
        "status": "runtime_resolvable",
        "fixture_setup": {
            "operation_ref": "op_create_order",
            "method": "POST",
            "path": "/api/orders",
            "actor_refs": ["actor_buyer"],
            "cleanup_operations": [
                {
                    "operation_ref": "op_cancel_order",
                    "method": "POST",
                    "path": "/api/orders/:id/cancel",
                    "compensates_operation_ref": "op_create_order",
                }
            ],
        },
    }
    ops = {
        row["id"]: row
        for row in _orders_ir()["operations"]
    }
    actors = {
        "actor_buyer": {
            "id": "actor_buyer",
            "role": "buyer",
            "credential_secret_ref": "secret:buyer",
        }
    }
    setup = validated_fixture_setup(binding, ops, actors)
    assert setup["operation_ref"] == "op_create_order"
    assert setup["body_template"]["addressId"] == "<address_id>"
    dependency = next(
        row
        for row in setup["body_bindings"]
        if row["template_token"] == "address_id"
    )
    assert any(
        resolver.get("path") == "/api/users/addresses"
        for resolver in dependency["resolver_operations"]
    )
    assert len(setup["cleanup_operations"]) == 1
    assert setup["cleanup_operations"][0]["method"] == "POST"


def test_cleanup_restoration_proves_create_cancel_with_minted_id() -> None:
    original = {
        "accepted": True,
        "method": "POST",
        "path": "/api/orders",
        "write": {
            "body": {
                "items": [{"sku": "SKU-PHONE-001", "qty": 1}],
                "addressId": "addr-1",
            }
        },
        "before": {"status": 200, "body": []},
        "after": {
            "status": 200,
            "body": [{"id": "43b7eff2-6505-447e-8dde-8e43a85a5284", "status": "PENDING_PAYMENT"}],
        },
        "receipt_id": "audit-create",
    }
    cleanup = {
        "accepted": True,
        "method": "POST",
        "path": "/api/orders/43b7eff2-6505-447e-8dde-8e43a85a5284/cancel",
        "before": {
            "status": 200,
            "body": [{"id": "43b7eff2-6505-447e-8dde-8e43a85a5284", "status": "PENDING_PAYMENT"}],
        },
        "after": {
            "status": 200,
            "body": [{"id": "43b7eff2-6505-447e-8dde-8e43a85a5284", "status": "CANCELLED"}],
        },
        "receipt_id": "audit-cancel",
    }
    assert _cleanup_compensates_created_resource(original, cleanup) is True
