from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center import _parsing
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.interface_runtime_contracts import (
    OPENAPI_RUNTIME_CONTRACT_SCHEMA,
    enrich_openapi_runtime_contracts,
)


def _openapi() -> dict:
    return {
        "openapi": "3.0.3",
        "security": [{"bearerAuth": []}],
        "paths": {
            "/orders/{order_id}/ship": {
                "parameters": [
                    {
                        "name": "order_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "post": {
                    "operationId": "shipOrder",
                    "summary": "发货",
                    "parameters": [
                        {
                            "name": "dry_run",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "boolean"},
                        },
                        {
                            "name": "X-Tenant",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ShipRequest"
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "发货成功",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["success"],
                                        "properties": {
                                            "success": {"type": "boolean"},
                                            "shipment_id": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        },
                        "409": {"description": "状态冲突"},
                    },
                },
            }
        },
        "components": {
            "schemas": {
                "ShipRequest": {
                    "type": "object",
                    "required": ["warehouse_id"],
                    "properties": {
                        "warehouse_id": {"type": "string"},
                        "note": {"type": "string"},
                        "options": {
                            "type": "object",
                            "properties": {
                                "notify": {"type": "boolean"}
                            },
                        },
                    },
                }
            },
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                }
            },
        },
    }


def test_enrichment_preserves_parameter_body_response_and_security_metadata() -> None:
    base = [
        {
            "interface_id": "api:POST:/orders/{order_id}/ship",
            "source_id": "source:openapi",
            "method": "POST",
            "path": "/orders/{order_id}/ship",
            "operation_id": "shipOrder",
            "parameters": ["order_id", "dry_run", "X-Tenant"],
        }
    ]
    rows = enrich_openapi_runtime_contracts(_openapi(), base)
    assert len(rows) == 1
    row = rows[0]

    assert row["runtime_contract_schema"] == OPENAPI_RUNTIME_CONTRACT_SCHEMA
    locations = {
        (item["name"], item["location"])
        for item in row["parameter_contracts"]
    }
    assert locations == {
        ("order_id", "PATH"),
        ("dry_run", "QUERY"),
        ("X-Tenant", "HEADER"),
    }
    body = {item["field"]: item for item in row["request_body_fields"]}
    assert body["warehouse_id"]["location"] == "BODY"
    assert body["warehouse_id"]["required"] is True
    assert body["options.notify"]["schema_type"] == "BOOLEAN"
    assert row["request_body_media_types"] == ["application/json"]
    assert {item["status"] for item in row["response_contracts"]} == {"200", "409"}
    assert row["security_requirements"][0]["scheme"] == "bearerAuth"
    assert row["security_requirements"][0]["credential_value_retained"] is False
    assert row["credential_values_retained"] is False


def test_installed_openapi_parser_adds_metadata_without_changing_interface_identity() -> None:
    rows = _parsing._openapi_operations(_openapi(), source_id="source:openapi")
    assert len(rows) == 1
    row = rows[0]
    assert row["interface_id"] == "api:POST:/orders/{order_id}/ship"
    assert row["method"] == "POST"
    assert row["path"] == "/orders/{order_id}/ship"
    assert row["request_contract_locations_preserved"] is True
    assert any(
        item["field"] == "warehouse_id" and item["location"] == "BODY"
        for item in row["request_body_fields"]
    )
