"""Path-scoped identity binding must not cross collections (cart id → order)."""
from __future__ import annotations

from ai_test_asset_center.runtime_binding_resolver import (
    _find_list_endpoints_for_entity,
    auto_resolve_bindings,
    collect_placeholder_collection_hints,
    collection_path_for_placeholder,
    collection_segment_for_placeholder,
    declared_identity_read_operations,
    materialize_declared_identity_read,
)
from ai_test_asset_center.sandbox_write_executor_base import (
    _identity_scoped_entity_observation,
)


BEHAVIOR_IR = {
    "operations": [
        {"id": "list-orders", "method": "GET", "path": "/api/orders"},
        {"id": "get-order", "method": "GET", "path": "/api/orders/{id}"},
        {"id": "confirm-order", "method": "POST", "path": "/api/orders/{id}/confirm"},
        {"id": "list-cart", "method": "GET", "path": "/api/cart/items"},
        {"id": "get-cart-item", "method": "GET", "path": "/api/cart/items/{id}"},
    ]
}


def test_collection_segment_for_order_confirm() -> None:
    assert (
        collection_segment_for_placeholder("/api/orders/{id}/confirm", "id")
        == "orders"
    )
    assert (
        collection_segment_for_placeholder("/api/cart/items/{id}", "id") == "items"
    )
    assert (
        collection_path_for_placeholder("/api/v1/orders/:orderId/confirm", "orderId")
        == "/api/v1/orders"
    )


def test_identity_proof_uses_only_exact_declared_entity_reads() -> None:
    operations = [
        {"id": "get-order", "method": "GET", "path": "/api/orders/{orderId}"},
        {"id": "head-order", "method": "HEAD", "path": "/api/orders/:orderId"},
        {
            "id": "confirm-order",
            "method": "GET",
            "path": "/api/orders/{orderId}/confirm",
        },
        {"id": "other-order", "method": "GET", "path": "/v2/orders/{orderId}"},
    ]

    declared = declared_identity_read_operations(
        operations,
        collection_path="/api/orders",
    )

    assert [row["id"] for row in declared] == ["get-order", "head-order"]
    assert (
        materialize_declared_identity_read(declared[1], "ord-1")
        == "/api/orders/ord-1"
    )


def test_generic_id_does_not_match_all_list_endpoints() -> None:
    # Without collection hints, bare {id} must not scrape every list.
    assert _find_list_endpoints_for_entity(BEHAVIOR_IR, "id") == []


def test_collection_hints_scope_list_endpoints_to_orders() -> None:
    candidates = _find_list_endpoints_for_entity(
        BEHAVIOR_IR,
        "id",
        collection_hints={"orders"},
    )
    paths = [row["path"] for row in candidates]
    assert paths == ["/api/orders"]
    assert "/api/cart/items" not in paths


def test_head_collection_route_cannot_authorize_an_undeclared_get() -> None:
    behavior_ir = {
        "operations": [
            {"id": "head-orders", "method": "HEAD", "path": "/api/orders"},
            {"id": "get-order", "method": "GET", "path": "/api/orders/{id}"},
        ]
    }

    assert _find_list_endpoints_for_entity(
        behavior_ir,
        "id",
        collection_hints={"orders"},
    ) == []


def test_collect_hints_detects_ambiguous_id_across_orders_and_cart() -> None:
    experiments = [
        {
            "treatment_plan": [
                {"operation_ref": "confirm-order", "path": "/api/orders/{id}/confirm"}
            ]
        },
        {
            "treatment_plan": [
                {"operation_ref": "get-cart-item", "path": "/api/cart/items/{id}"}
            ]
        },
    ]
    hints = collect_placeholder_collection_hints(experiments, BEHAVIOR_IR)
    assert hints["id"] == {"orders", "items"}


def test_auto_resolve_leaves_ambiguous_id_unbound(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_get(base_url: str, path: str, token: str, timeout: int = 10):
        calls.append(path)
        if path == "/api/cart/items":
            return [{"id": "cart-item-uuid-0001"}]
        if path == "/api/orders":
            return [{"id": "order-uuid-0001"}]
        return None

    monkeypatch.setattr(
        "ai_test_asset_center.runtime_binding_resolver._call_get_endpoint",
        _fake_get,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.runtime_binding_resolver._call_get_status",
        lambda *args, **kwargs: 200,
    )

    result = auto_resolve_bindings(
        BEHAVIOR_IR,
        {"admin": "tok"},
        "http://127.0.0.1:8080",
        required_placeholders={"id"},
        placeholder_collection_hints={"id": {"orders", "items"}},
    )
    assert result["bindings"] == {}
    assert any(
        row.get("status") == "ambiguous_collection_context"
        for row in result["receipts"]
    )
    assert calls == []


def test_auto_resolve_orders_proves_entity_get(monkeypatch) -> None:
    def _fake_get(base_url: str, path: str, token: str, timeout: int = 10):
        if path == "/api/orders":
            return [{"id": "order-uuid-0001"}]
        return None

    statuses: list[str] = []

    def _fake_status(base_url: str, path: str, token: str, timeout: int = 10):
        statuses.append(path)
        if path == "/api/orders/order-uuid-0001":
            return 200
        return 404

    monkeypatch.setattr(
        "ai_test_asset_center.runtime_binding_resolver._call_get_endpoint",
        _fake_get,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.runtime_binding_resolver._call_get_status",
        _fake_status,
    )

    result = auto_resolve_bindings(
        BEHAVIOR_IR,
        {"admin": "tok"},
        "http://127.0.0.1:8080",
        required_placeholders={"id"},
        placeholder_collection_hints={"id": {"orders"}},
    )
    assert result["bindings"] == {"id": "order-uuid-0001"}
    assert "/api/orders/order-uuid-0001" in statuses
    resolved = next(
        row for row in result["receipts"] if row.get("status") == "resolved"
    )
    assert resolved["entity_operation_ref"] == "get-order"


def test_auto_resolve_refuses_to_invent_missing_entity_get(monkeypatch) -> None:
    behavior_ir = {
        "operations": [
            {"id": "list-orders", "method": "GET", "path": "/api/orders"},
            {
                "id": "confirm-order",
                "method": "POST",
                "path": "/api/orders/{id}/confirm",
            },
        ]
    }

    monkeypatch.setattr(
        "ai_test_asset_center.runtime_binding_resolver._call_get_endpoint",
        lambda *args, **kwargs: [{"id": "order-uuid-0001"}],
    )
    status_calls: list[str] = []
    monkeypatch.setattr(
        "ai_test_asset_center.runtime_binding_resolver._call_get_status",
        lambda base_url, path, token, timeout=10: status_calls.append(path) or 200,
    )

    result = auto_resolve_bindings(
        behavior_ir,
        {"admin": "tok"},
        "http://127.0.0.1:8080",
        required_placeholders={"id"},
        placeholder_collection_hints={"id": {"orders"}},
    )

    assert result["bindings"] == {}
    assert status_calls == []
    assert any(
        row.get("status") == "identity_observer_not_declared"
        for row in result["receipts"]
    )


def test_auto_resolve_rejects_list_id_when_entity_get_404(monkeypatch) -> None:
    def _fake_get(base_url: str, path: str, token: str, timeout: int = 10):
        if path == "/api/orders":
            return [{"id": "cart-item-uuid-leak"}]
        return None

    monkeypatch.setattr(
        "ai_test_asset_center.runtime_binding_resolver._call_get_endpoint",
        _fake_get,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.runtime_binding_resolver._call_get_status",
        lambda *args, **kwargs: 404,
    )

    result = auto_resolve_bindings(
        BEHAVIOR_IR,
        {"admin": "tok"},
        "http://127.0.0.1:8080",
        required_placeholders={"id"},
        placeholder_collection_hints={"id": {"orders"}},
    )
    assert result["bindings"] == {}
    assert any(row.get("status") == "identity_unobservable" for row in result["receipts"])


def test_identity_scoped_observation_for_confirm() -> None:
    assert _identity_scoped_entity_observation(
        "/api/orders/6df4c20e-7499-4a7f-a075-5f3542c6b722/confirm",
        "/api/orders/6df4c20e-7499-4a7f-a075-5f3542c6b722",
    )
    assert not _identity_scoped_entity_observation(
        "/api/orders",
        "/api/orders",
    )


def test_short_id_ord1_is_identity_scoped() -> None:
    """Short opaque ids must block unobservable entity paths — no UUID guessing."""
    assert _identity_scoped_entity_observation(
        "/api/orders/ord-1/confirm",
        "/api/orders/ord-1",
    )
    assert _identity_scoped_entity_observation(
        "/api/orders/ord-1",
        "/api/orders/ord-1",
    )
    assert not _identity_scoped_entity_observation(
        "/api/v1/orders",
        "/api/v1/orders",
    )
