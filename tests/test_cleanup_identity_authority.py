from __future__ import annotations


def test_http_cleanup_does_not_bind_generic_id_from_nested_related_object() -> None:
    from ai_test_asset_center.runtime_binding_materializer import runtime_cleanup_paths

    paths, missing = runtime_cleanup_paths(
        "/api/orders/{id}",
        [
            {
                "step_id": "treatment_1",
                "phase": "treatment",
                "method": "POST",
                "path": "/api/orders",
                "status_code": 201,
                "body": {"user": {"id": "wrong-user-id"}, "status": "CREATED"},
            }
        ],
    )

    assert paths == []
    assert missing == ["treatment_1:id"]


def test_http_cleanup_accepts_generic_id_from_standard_response_envelope() -> None:
    from ai_test_asset_center.runtime_binding_materializer import runtime_cleanup_paths

    paths, missing = runtime_cleanup_paths(
        "/api/orders/{id}",
        [
            {
                "step_id": "treatment_1",
                "phase": "treatment",
                "method": "POST",
                "path": "/api/orders",
                "status_code": 201,
                "body": {"data": {"id": "order-123"}},
            }
        ],
    )

    assert paths == [("/api/orders/order-123", {"id": "order-123"})]
    assert missing == []


def test_http_cleanup_never_substitutes_different_domain_id_field() -> None:
    from ai_test_asset_center.runtime_binding_materializer import runtime_cleanup_paths

    paths, missing = runtime_cleanup_paths(
        "/api/orders/{id}",
        [
            {
                "step_id": "treatment_1",
                "phase": "treatment",
                "method": "POST",
                "path": "/api/orders",
                "status_code": 201,
                "body": {"orderId": "order-123"},
            }
        ],
    )

    assert paths == []
    assert missing == ["treatment_1:id"]
