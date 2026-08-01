"""Regression: runtime resolver must materialize body placeholders.

The batch resolver previously collected placeholders only from operation path
templates, so an order body's ``addressId`` (bound at compile to an owner-scoped
list read ``GET /api/users/addresses``) was never resolved at runtime and every
order write blocked with ``BLOCKED_MISSING_BINDING`` at execution. Body
placeholders are now collected from binding plans and resolved from the
owner-scoped list read without requiring a separate entity-detail route.
"""
from __future__ import annotations

from ai_test_asset_center import runtime_binding_resolver as resolver


def _ir() -> dict:
    return {
        "operations": [
            {"id": "op_orders", "method": "POST", "path": "/api/orders"},
            {
                "id": "op_addresses",
                "method": "GET",
                "path": "/api/users/addresses",
            },
            {"id": "op_get_orders", "method": "GET", "path": "/api/orders"},
            {
                "id": "op_get_order_detail",
                "method": "GET",
                "path": "/api/orders/:id",
            },
        ],
        "actors": [],
    }


def test_collect_hints_includes_body_placeholder_resolvers() -> None:
    experiment = {
        "binding_plan": [
            {
                "target": "address_id",
                "status": "runtime_resolvable",
                "body_template_paths": ["addressId"],
                "resolver_operations": [
                    {
                        "operation_ref": "op_addresses",
                        "method": "GET",
                        "path": "/api/users/addresses",
                    }
                ],
            }
        ],
        "treatment_plan": [{"operation_ref": "op_orders", "path": "/api/orders"}],
        "control_plan": [],
    }
    hints = resolver.collect_placeholder_collection_hints(
        [experiment],
        _ir(),
    )
    assert "/api/users/addresses" in hints.get("address_id", set())


def test_auto_resolve_body_placeholder_from_owner_scoped_list(
    monkeypatch,
) -> None:
    def fake_call_get(base_url: str, path: str, token: str, timeout: int = 10):
        if path == "/api/users/addresses":
            return {"data": [{"id": "addr-123", "is_default": True}]}
        return None

    monkeypatch.setattr(resolver, "_call_get_endpoint", fake_call_get)
    hints = {"address_id": {"/api/users/addresses"}}
    result = resolver.auto_resolve_bindings(
        _ir(),
        {"buyer": "token-buyer"},
        "http://target.test",
        required_placeholders={"address_id"},
        placeholder_collection_hints=hints,
    )
    assert result["bindings"].get("address_id") == "addr-123"
    assert any(
        row.get("status") == "resolved_body_from_owner_scoped_list"
        for row in result["receipts"]
    )


def test_path_placeholder_still_requires_identity_read(monkeypatch) -> None:
    def fake_call_get(base_url: str, path: str, token: str, timeout: int = 10):
        if path == "/api/orders":
            return {"data": [{"id": "order-9"}]}
        return {"id": "order-9"}

    monkeypatch.setattr(resolver, "_call_get_endpoint", fake_call_get)
    monkeypatch.setattr(resolver, "_call_get_status", lambda *a, **k: 200)
    hints = {"order_id": {"/api/orders"}}
    result = resolver.auto_resolve_bindings(
        _ir(),
        {"buyer": "token-buyer"},
        "http://target.test",
        required_placeholders={"order_id"},
        placeholder_collection_hints=hints,
    )
    # GET /api/orders/{id} exists in this IR, so the identity read resolves.
    assert result["bindings"].get("order_id") == "order-9"


def test_owner_scoped_list_read_tries_actor_tokens(monkeypatch) -> None:
    calls: list[str] = []

    def fake_call_get(base_url: str, path: str, token: str, timeout: int = 10):
        calls.append(token)
        # Admin token sees no addresses; the buyer token owns them.
        if token == "token-admin":
            return {"data": []}
        if token == "token-buyer" and path == "/api/users/addresses":
            return {"data": [{"id": "addr-buyer"}]}
        return None

    monkeypatch.setattr(resolver, "_call_get_endpoint", fake_call_get)
    hints = {"address_id": {"/api/users/addresses"}}
    result = resolver.auto_resolve_bindings(
        _ir(),
        {
            "admin": "token-admin",
            "buyer": "token-buyer",
        },
        "http://target.test",
        required_placeholders={"address_id"},
        placeholder_collection_hints=hints,
    )
    assert result["bindings"].get("address_id") == "addr-buyer"
    assert "token-admin" in calls
    assert "token-buyer" in calls
