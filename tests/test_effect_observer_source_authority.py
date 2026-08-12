from __future__ import annotations

from ai_test_asset_center.runtime_binding_graph import declared_effect_observers


def test_identity_action_uses_exact_parent_detail_read() -> None:
    write = {"id": "approve", "method": "POST", "path": "/api/resources/{id}/approve"}
    detail = {"id": "detail", "method": "GET", "path": "/api/resources/{id}"}
    health = {"id": "health", "method": "GET", "path": "/api/resources/health"}

    assert declared_effect_observers(
        write,
        behavior_ir={"operations": [write, detail, health], "relations": []},
        max_candidates=1,
    ) == [{"operation_ref": "detail", "method": "GET", "path": "/api/resources/{id}"}]


def test_body_placeholder_read_requires_literal_body_key_and_collection_ancestor() -> None:
    write = {
        "id": "adjust",
        "method": "POST",
        "path": "/api/inventory/admin/adjust",
        "request_example": {"sku": "SKU-1", "delta": 1},
    }
    matching = {"id": "by-sku", "method": "GET", "path": "/api/inventory/{sku}"}
    wrong_key = {"id": "by-id", "method": "GET", "path": "/api/inventory/{id}"}

    assert declared_effect_observers(
        write,
        behavior_ir={"operations": [write, wrong_key, matching], "relations": []},
        max_candidates=1,
    ) == [{"operation_ref": "by-sku", "method": "GET", "path": "/api/inventory/{sku}"}]


def test_collection_create_detail_read_requires_matching_success_response_ref() -> None:
    create = {
        "id": "create-refund",
        "method": "POST",
        "path": "/api/refunds",
        "response_schema": {
            "201": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Refund"}}}}
        },
    }
    detail = {
        "id": "read-refund",
        "method": "GET",
        "path": "/api/refunds/{id}",
        "response_schema": {
            "200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Refund"}}}}
        },
    }

    assert declared_effect_observers(
        create,
        behavior_ir={"operations": [create, detail], "relations": []},
        max_candidates=1,
    ) == [{"operation_ref": "read-refund", "method": "GET", "path": "/api/refunds/{id}"}]


def test_collection_create_rejects_same_path_shape_for_different_response_model() -> None:
    create = {
        "id": "create-refund",
        "method": "POST",
        "path": "/api/refunds",
        "response_schema": {
            "201": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Refund"}}}}
        },
    }
    wrong_detail = {
        "id": "read-audit",
        "method": "GET",
        "path": "/api/refunds/{id}",
        "response_schema": {
            "200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/AuditLog"}}}}
        },
    }

    assert declared_effect_observers(
        create,
        behavior_ir={"operations": [create, wrong_detail], "relations": []},
        max_candidates=1,
    ) == []
