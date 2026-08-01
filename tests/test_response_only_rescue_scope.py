"""Regression: response-only binding rescue never rewrites body placeholders.

The V1.8 rescue attached the write operation's own collection reads to EVERY
blocked binding, including body field placeholders such as an order body's
``addressId``. That cross-bound the wrong entity (GET /api/orders cannot
supply an address id), producing ``runtime resolver invalid`` fixture-DAG
blocks and execution-time ``runtime_read_binding_unresolved:address_id``.

The rescue now applies only to the operation's own path placeholders (with
placeholder-free list reads); body field placeholders stay blocked so the
observation-driven expansion round can recompile them against the expanded IR
that declares the field-entity route.
"""
from __future__ import annotations

from ai_test_asset_center.experiment_compiler_obligation_core import (
    _rescue_binding_for_response_only_family,
)


def _primary_op() -> dict:
    return {
        "id": "op_cancel",
        "method": "POST",
        "path": "/api/orders/:order_id/cancel",
    }


def _ir() -> dict:
    return {
        "operations": [
            {"id": "op_orders_list", "method": "GET", "path": "/api/orders"},
            {"id": "op_orders_detail", "method": "GET", "path": "/api/orders/:id"},
        ],
        "actors": [],
    }


def test_rescue_rewrites_only_primary_path_placeholders() -> None:
    plan = [
        {
            "target": "order_id",
            "target_path": "/api/orders/{order_id}/cancel",
            "status": "blocked",
            "blocked_reason": "PLACEHOLDER_PATH_PARAMETER_NOT_RESOLVED",
        },
        {
            "target": "address_id",
            "target_path": "/{address_id}",
            "status": "blocked",
            "source_priority": "body_placeholder_unresolvable",
            "blocked_reason": "BODY_PARAMETER_NOT_SOURCE_BOUND",
            "body_template_paths": ["addressId"],
        },
    ]
    rescued = _rescue_binding_for_response_only_family(
        plan,
        _primary_op(),
        _ir(),
    )
    assert rescued is True
    by_target = {row["target"]: row for row in plan}
    # Path placeholder belongs to the operation's own resource.
    assert by_target["order_id"]["status"] == "runtime_resolvable"
    assert {
        (r.get("method"), r.get("path"))
        for r in by_target["order_id"]["resolver_operations"]
    } == {("GET", "/api/orders")}
    # Body field placeholder must never be bound from the operation collection.
    assert by_target["address_id"]["status"] == "blocked"
    assert by_target["address_id"].get("resolver_operations") is None


def test_rescue_rejects_placeholder_carrying_resolvers() -> None:
    plan = [
        {
            "target": "order_id",
            "target_path": "/api/orders/{order_id}/cancel",
            "status": "blocked",
            "blocked_reason": "PLACEHOLDER_PATH_PARAMETER_NOT_RESOLVED",
        }
    ]
    rescued = _rescue_binding_for_response_only_family(
        plan,
        _primary_op(),
        _ir(),
    )
    assert rescued is True
    entry = plan[0]
    assert entry["status"] == "runtime_resolvable"
    # Entity-detail resolver (with placeholder) is dropped at rescue time so
    # the fixture DAG stays constructible.
    assert all(
        "{" not in (r.get("path") or "") and ":" not in (r.get("path") or "")
        for r in entry["resolver_operations"]
    )
