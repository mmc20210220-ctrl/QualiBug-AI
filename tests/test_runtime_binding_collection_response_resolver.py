from __future__ import annotations

from ai_test_asset_center.runtime_binding_graph import declared_runtime_read_resolvers


def _object_response(schema_name: str) -> dict:
    return {
        "200": {
            "content": {
                "application/json": {
                    "schema": {"$ref": f"#/components/schemas/{schema_name}"}
                }
            }
        }
    }


def _array_response(schema_name: str) -> dict:
    return {
        "200": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "array",
                        "items": {"$ref": f"#/components/schemas/{schema_name}"},
                    }
                }
            }
        }
    }


def test_action_path_uses_source_declared_sibling_array_of_same_resource() -> None:
    target = {
        "id": "approve",
        "method": "POST",
        "path": "/api/resources/{id}/approve",
        "response_schema": _object_response("Ack"),
    }
    behavior_ir = {
        "operations": [
            target,
            {
                "id": "read-resource",
                "method": "GET",
                "path": "/api/resources/{id}",
                "response_schema": _object_response("Resource"),
            },
            {
                "id": "health",
                "method": "GET",
                "path": "/api/resources/health",
                "response_schema": _object_response("Health"),
            },
            {
                "id": "admin-list",
                "method": "GET",
                "path": "/api/resources/admin/all",
                "response_schema": _array_response("Resource"),
            },
            {
                "id": "wrong-subresource-list",
                "method": "GET",
                "path": "/api/resources/locks/list",
                "response_schema": _array_response("ResourceLock"),
            },
        ]
    }

    assert declared_runtime_read_resolvers(target, behavior_ir=behavior_ir) == [
        {
            "operation_ref": "admin-list",
            "method": "GET",
            "path": "/api/resources/admin/all",
        }
    ]


def test_shared_prefix_array_of_different_schema_is_not_identity_authority() -> None:
    target = {
        "id": "read-inventory",
        "method": "GET",
        "path": "/api/inventory/{sku}",
        "response_schema": _object_response("Inventory"),
    }
    behavior_ir = {
        "operations": [
            target,
            {
                "id": "lock-list",
                "method": "GET",
                "path": "/api/inventory/locks/list",
                "response_schema": _array_response("InventoryLock"),
            },
        ]
    }

    assert declared_runtime_read_resolvers(target, behavior_ir=behavior_ir) == []
