"""Schema-derived compensates relations for identity-bound reverse actions.

DELETE collection members were already derived. Collection creates that only
document ``POST …/{id}/cancel`` (no DELETE) must also emit a unique
``compensates`` relation so fixture setup and write cleanup can proceed.
Forward lifecycle actions (ship/confirm/approve) must never be linked.
"""
from __future__ import annotations

from ai_test_asset_center.behavior_ir_core import _derive_compensation_relations
from ai_test_asset_center.experiment_fixture_materializer_core import (
    _auto_fixture_create_for_binding_target,
)


def _ops(*rows: dict) -> dict:
    return {"operations": list(rows), "relations": [], "actors": []}


def test_derive_cancel_when_no_delete() -> None:
    model = _ops(
        {"id": "op_create", "method": "POST", "path": "/api/orders", "confidence": 0.9},
        {"id": "op_cancel", "method": "POST", "path": "/api/orders/{id}/cancel", "confidence": 0.8},
        {"id": "op_ship", "method": "POST", "path": "/api/orders/{id}/ship", "confidence": 0.8},
    )
    relations = _derive_compensation_relations(model)
    assert len(relations) == 1
    rel = relations[0]
    assert rel["relation_type"] == "compensates"
    assert rel["from_ref"] == "op_cancel"
    assert rel["to_ref"] == "op_create"
    assert rel["operation_ref"] == "op_cancel"
    assert rel["source_refs"], "derived compensates must carry provenance"


def test_derive_prefers_delete_over_cancel() -> None:
    model = _ops(
        {"id": "op_create", "method": "POST", "path": "/api/cart/items"},
        {"id": "op_delete", "method": "DELETE", "path": "/api/cart/items/{id}"},
        {"id": "op_cancel", "method": "POST", "path": "/api/cart/items/{id}/cancel"},
    )
    relations = _derive_compensation_relations(model)
    assert len(relations) == 1
    assert relations[0]["from_ref"] == "op_delete"


def test_derive_ambiguous_reverse_verbs_fail_closed() -> None:
    model = _ops(
        {"id": "op_create", "method": "POST", "path": "/api/reservations"},
        {"id": "op_cancel", "method": "POST", "path": "/api/reservations/{id}/cancel"},
        {"id": "op_void", "method": "POST", "path": "/api/reservations/{id}/void"},
    )
    assert _derive_compensation_relations(model) == []


def test_derive_ignores_forward_lifecycle_only() -> None:
    model = _ops(
        {"id": "op_create", "method": "POST", "path": "/api/orders"},
        {"id": "op_ship", "method": "POST", "path": "/api/orders/{id}/ship"},
        {"id": "op_confirm", "method": "POST", "path": "/api/orders/{id}/confirm"},
    )
    assert _derive_compensation_relations(model) == []


def test_auto_fixture_uses_derived_cancel_without_explicit_relation() -> None:
    """End-to-end: derivation → fixture creator without hand-authored relation."""
    behavior_ir = {
        "operations": [
            {
                "id": "op_create_order",
                "method": "POST",
                "path": "/api/orders",
                "request_example": {
                    "items": [{"sku": "SKU-1", "qty": 1}],
                    "addressId": "<address_id>",
                },
                "source_refs": [{"source_id": "orders-api"}],
            },
            {"id": "op_list_orders", "method": "GET", "path": "/api/orders"},
            {"id": "op_addresses", "method": "GET", "path": "/api/users/addresses"},
            {
                "id": "op_cancel_order",
                "method": "POST",
                "path": "/api/orders/:id/cancel",
                "source_refs": [{"source_id": "orders-api"}],
            },
            {
                "id": "op_ship_order",
                "method": "POST",
                "path": "/api/orders/:id/ship",
            },
        ],
        "actors": [],
        "relations": _derive_compensation_relations(
            {
                "operations": [
                    {
                        "id": "op_create_order",
                        "method": "POST",
                        "path": "/api/orders",
                        "source_refs": [{"source_id": "orders-api"}],
                    },
                    {
                        "id": "op_cancel_order",
                        "method": "POST",
                        "path": "/api/orders/:id/cancel",
                        "source_refs": [{"source_id": "orders-api"}],
                    },
                    {
                        "id": "op_ship_order",
                        "method": "POST",
                        "path": "/api/orders/:id/ship",
                    },
                ]
            }
        ),
    }
    assert any(
        row.get("relation_type") == "compensates"
        and row.get("from_ref") == "op_cancel_order"
        for row in behavior_ir["relations"]
    )
    ops = {row["id"]: row for row in behavior_ir["operations"]}
    actors = {
        "actor_buyer": {
            "id": "actor_buyer",
            "role": "buyer",
            "credential_secret_ref": "secret:buyer",
        }
    }
    auto = _auto_fixture_create_for_binding_target(
        "order_id",
        {
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
        },
        ops,
        {},
        actors=actors,
        behavior_ir=behavior_ir,
    )
    assert auto is not None
    cleanup_paths = {
        (row.get("method"), row.get("path"))
        for row in auto["fixture_setup"]["cleanup_operations"]
    }
    assert ("POST", "/api/orders/{id}/cancel") in cleanup_paths
    assert all("ship" not in (path or "") for _, path in cleanup_paths)
