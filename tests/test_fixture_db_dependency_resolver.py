"""Regression tests for the source-declared persistence (db_sql) dependency resolver.

Covers the fourth leg of the fixture dependency chain: a create-body foreign
key placeholder whose entity declares a storage table but no HTTP list-read
endpoint. Before this capability the fixture setup collapsed into
``fixture_setup_not_generated`` because the only resolver tier was HTTP GET.
"""
from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.experiment_fixture_materializer_core import (
    _run_declared_db_identity_read,
)
from ai_test_asset_center.runtime_binding_materializer_base import (
    _derive_body_bindings_from_template,
    declared_persistence_resolver,
    validated_fixture_setup,
    validated_runtime_resolvers_with_receipts,
)

ADDRESSES_ENTITY = {
    "name": "addresses",
    "table": "addresses",
    "identity_fields": ["id"],
}

ENTITIES = [
    {"name": "order", "table": "orders", "identity_fields": ["id", "order_no"]},
    ADDRESSES_ENTITY,
    # Declared by name only: must never become a table reference.
    {"name": "carts", "identity_fields": ["id"]},
]


def _ops_with_orders_create() -> dict:
    return {
        "op_create_order": {
            "id": "op_create_order",
            "method": "POST",
            "path": "/api/orders",
            "request_example": {
                "items": [{"sku": "SKU-1", "qty": 1}],
                "addressId": "<address_id>",
            },
        },
        "op_cancel_order": {
            "id": "op_cancel_order",
            "method": "POST",
            "path": "/api/orders/{id}/cancel",
        },
    }


# ── declared_persistence_resolver ────────────────────────────────────────────


def test_resolver_matches_declared_entity_table():
    row = declared_persistence_resolver("addressId", "address_id", ENTITIES)
    assert row == {
        "adapter": "db_sql",
        "method": "DB_READ",
        "table": "addresses",
        "identity_column": "id",
    }


def test_resolver_refuses_entity_without_declared_table():
    # 'carts' has a name and identity but no storage table declaration.
    assert declared_persistence_resolver("cartId", "cart_id", ENTITIES) is None


def test_resolver_refuses_unknown_field():
    assert declared_persistence_resolver("warehouseId", "warehouse_id", ENTITIES) is None


def test_resolver_refuses_without_entities():
    assert declared_persistence_resolver("addressId", "address_id", None) is None
    assert declared_persistence_resolver("addressId", "address_id", []) is None


# ── _derive_body_bindings_from_template ──────────────────────────────────────


def test_derive_body_bindings_adds_db_resolver_when_no_http_read():
    ops = _ops_with_orders_create()
    bindings = _derive_body_bindings_from_template(
        ops["op_create_order"]["request_example"],
        operations=ops,
        create_path="/api/orders",
        entities=ENTITIES,
    )
    assert len(bindings) == 1
    binding = bindings[0]
    assert binding["template_token"] == "address_id"
    assert binding["resolver_operations"] == [
        {
            "adapter": "db_sql",
            "method": "DB_READ",
            "table": "addresses",
            "identity_column": "id",
        }
    ]


def test_derive_body_bindings_stays_fail_closed_without_declaration():
    ops = _ops_with_orders_create()
    bindings = _derive_body_bindings_from_template(
        ops["op_create_order"]["request_example"],
        operations=ops,
        create_path="/api/orders",
        entities=None,
    )
    assert bindings[0]["resolver_operations"] == []


def test_derive_body_bindings_prefers_http_read_over_db():
    ops = _ops_with_orders_create()
    ops["op_list_addresses"] = {
        "id": "op_list_addresses",
        "method": "GET",
        "path": "/api/addresses",
    }
    bindings = _derive_body_bindings_from_template(
        ops["op_create_order"]["request_example"],
        operations=ops,
        create_path="/api/orders",
        entities=ENTITIES,
    )
    resolvers = bindings[0]["resolver_operations"]
    assert [r.get("method") for r in resolvers] == ["GET"]
    assert resolvers[0]["path"] == "/api/addresses"


# ── validated_runtime_resolvers_with_receipts ────────────────────────────────


def test_validated_resolvers_accept_db_read_row():
    accepted, rejected = validated_runtime_resolvers_with_receipts(
        {
            "status": "runtime_resolvable",
            "resolver_operations": [
                {
                    "adapter": "db_sql",
                    "method": "DB_READ",
                    "table": "addresses",
                    "identity_column": "id",
                }
            ],
        },
        {},
    )
    assert rejected == []
    assert accepted[0]["adapter"] == "db_sql"
    assert accepted[0]["validation_status"] == "VALIDATED"


def test_validated_resolvers_reject_unsafe_db_identifier():
    accepted, rejected = validated_runtime_resolvers_with_receipts(
        {
            "status": "runtime_resolvable",
            "resolver_operations": [
                {
                    "adapter": "db_sql",
                    "method": "DB_READ",
                    "table": "addresses; DROP TABLE users",
                    "identity_column": "id",
                }
            ],
        },
        {},
    )
    assert accepted == []
    assert rejected[0]["reason"] == "db_identifier_shape_refused"


def test_validated_resolvers_reject_non_read_db_method():
    accepted, rejected = validated_runtime_resolvers_with_receipts(
        {
            "status": "runtime_resolvable",
            "resolver_operations": [
                {
                    "adapter": "db_sql",
                    "method": "DB_WRITE",
                    "table": "addresses",
                    "identity_column": "id",
                }
            ],
        },
        {},
    )
    assert accepted == []
    assert rejected[0]["reason"].startswith("db_resolver_non_read")


# ── validated_fixture_setup end-to-end shape ────────────────────────────────


def _orders_binding() -> dict:
    return {
        "target": "id",
        "target_path": "/api/orders/{id}",
        "resolver_operations": [],
        "fixture_setup": {
            "operation_ref": "op_create_order",
            "method": "POST",
            "path": "/api/orders",
            "cleanup_operations": [
                {
                    "operation_ref": "op_cancel_order",
                    "method": "POST",
                    "path": "/api/orders/{id}/cancel",
                    "compensates_operation_ref": "op_create_order",
                }
            ],
            "actor_refs": ["buyer01"],
        },
    }


def test_fixture_setup_generated_with_declared_db_dependency():
    setup = validated_fixture_setup(
        _orders_binding(),
        _ops_with_orders_create(),
        {"buyer01": {"role": "buyer"}},
        entities=ENTITIES,
    )
    assert setup
    assert setup["body_bindings"][0]["template_token"] == "address_id"
    assert setup["body_bindings"][0]["resolver_operations"][0]["adapter"] == "db_sql"


def test_fixture_setup_still_fail_closed_without_entities():
    setup = validated_fixture_setup(
        _orders_binding(),
        _ops_with_orders_create(),
        {"buyer01": {"role": "buyer"}},
    )
    assert setup == {}


# ── governed read gate ───────────────────────────────────────────────────────


def test_db_read_refused_when_environment_undeclared(tmp_path: Path):
    obs = _run_declared_db_identity_read(
        root=tmp_path,
        project="no_such_project",
        runtime_contract={},
        resolver={
            "adapter": "db_sql",
            "method": "DB_READ",
            "table": "addresses",
            "identity_column": "id",
        },
    )
    assert obs["status_code"] == 0
    assert obs["value"] is None
    assert obs["reason_code"].startswith("persistence_read_not_permitted")
