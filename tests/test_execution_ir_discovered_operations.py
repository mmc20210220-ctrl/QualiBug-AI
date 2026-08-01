"""Regression: round-1 execution consumes proven runtime-discovered routes.

Round-1 experiments compiled against the immutable documented IR could not
materialize disposable order fixtures because the fixture's documented
addressId dependency needs GET /api/users/addresses, which only exists in the
runtime-observed operation set. Every payment/refund/cancel obligation blocked
at execution with ``fixture_setup_not_generated`` and the expansion round never
recompiled them (they had already compiled).

The execution IR view now appends the governed runtime-discovered operations
(already covered by the behavior-ir-expansion-round receipt) without rebuilding
the IR, so compiled operation identities stay valid and fixture dependencies
resolve against the observed routes.
"""
from __future__ import annotations

from ai_test_asset_center.discovery_runtime_execution import (
    _execution_ir_with_discovered_operations,
)


def test_appends_discovered_operations_preserving_existing_ids() -> None:
    behavior_ir = {
        "model_id": "bir_round0",
        "operations": [
            {"id": "bir_orders", "method": "POST", "path": "/api/orders"},
            {"id": "bir_list_orders", "method": "GET", "path": "/api/orders"},
        ],
        "actors": [],
    }
    discovered = [
        {
            "id": "bir_addresses",
            "method": "GET",
            "path": "/api/users/addresses",
        },
        {
            "id": "bir_orders_dup",
            "method": "GET",
            "path": "/api/orders",
        },
    ]
    merged = _execution_ir_with_discovered_operations(behavior_ir, discovered)
    ops = merged["operations"]
    assert len(ops) == 3
    assert {row.get("id") for row in ops} == {
        "bir_orders",
        "bir_list_orders",
        "bir_addresses",
    }
    # Existing identities are untouched (no IR rebuild).
    assert merged["model_id"] == "bir_round0"
    assert ops[0]["id"] == "bir_orders"


def test_discovered_operation_id_is_normalized_from_operation_id() -> None:
    behavior_ir = {
        "model_id": "bir_round0",
        "operations": [{"id": "bir_orders", "method": "POST", "path": "/api/orders"}],
        "actors": [],
    }
    discovered = [
        {
            "operation_id": "runtime-observed:get:/api/users/addresses",
            "method": "GET",
            "path": "/api/users/addresses",
            "derivation": "runtime-observed",
        }
    ]
    merged = _execution_ir_with_discovered_operations(behavior_ir, discovered)
    assert merged["operations"][1]["id"] == "runtime-observed:get:/api/users/addresses"


def test_no_discovered_operations_returns_unchanged_ir() -> None:
    behavior_ir = {
        "model_id": "bir_round0",
        "operations": [{"id": "bir_orders", "method": "POST", "path": "/api/orders"}],
        "actors": [],
    }
    assert _execution_ir_with_discovered_operations(behavior_ir, []) == behavior_ir
