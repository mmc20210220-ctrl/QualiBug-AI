from __future__ import annotations

import json

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    build_document_structure_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.openapi_schema_fact_projection import (
    project_openapi_schema_facts,
)


def test_schema_blocks_project_to_request_response_component_and_parameter_facts() -> None:
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "Orders", "version": "1"},
        "paths": {
            "/orders/{orderId}": {
                "parameters": [
                    {
                        "name": "orderId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    }
                ],
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["amount"],
                                    "properties": {
                                        "amount": {
                                            "type": "number",
                                            "minimum": 0,
                                        },
                                        "status": {
                                            "$ref": "#/components/schemas/OrderStatus"
                                        },
                                    },
                                }
                            }
                        }
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["id"],
                                        "properties": {
                                            "id": {"type": "string", "format": "uuid"}
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
            }
        },
        "components": {
            "schemas": {
                "OrderStatus": {
                    "type": "string",
                    "enum": ["PENDING", "APPROVED", "REJECTED"],
                },
                "Order": {
                    "type": "object",
                    "properties": {
                        "tenantId": {"type": "string", "readOnly": True}
                    },
                },
            }
        },
    }
    ir = build_document_structure_ir(
        json.dumps(payload).encode(),
        filename="orders.openapi.json",
        source_id="src_schema_facts",
    )

    result = project_openapi_schema_facts(
        ir,
        source_id="src_schema_facts",
        source_type="openapi",
    )

    assert result["schema_definition_count"] == 5
    assert result["schema_field_count"] == 4
    assert result["required_field_count"] == 2
    assert result["request_field_count"] == 2
    assert result["response_field_count"] == 1
    assert result["component_field_count"] == 1
    assert result["parameter_field_count"] == 0
    assert result["reference_count"] == 1
    assert result["unresolved_reference_count"] == 0
    assert result["exact_evidence_rate"] == 1.0

    amount = next(row for row in result["schema_fields"] if row["field_name"] == "amount")
    assert amount["direction"] == "request"
    assert amount["required"] is True
    assert amount["constraints"]["minimum"] == 0
    assert amount["api_path"] == "/orders/{orderId}"
    assert amount["method"] == "POST"
    assert amount["media_type"] == "application/json"
    assert amount["evidence"]["exact"] is True

    response_id = next(
        row
        for row in result["schema_fields"]
        if row["direction"] == "response" and row["field_name"] == "id"
    )
    assert response_id["response_status"] == "201"
    assert response_id["required"] is True

    parameter = next(
        row for row in result["schema_definitions"] if row["direction"] == "parameter"
    )
    assert parameter["api_path"] == "/orders/{orderId}"
    assert parameter["method"] == ""

    tenant = next(row for row in result["schema_fields"] if row["field_name"] == "tenantId")
    assert tenant["direction"] == "component"
    assert tenant["schema_name"] == "Order"
    assert tenant["read_only"] is True


def test_external_schema_ref_remains_unresolved_and_exactly_evidenced() -> None:
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "External", "version": "1"},
        "paths": {},
        "components": {
            "schemas": {
                "ExternalOrder": {
                    "$ref": "schemas.yaml#/components/schemas/Order"
                }
            }
        },
    }
    ir = build_document_structure_ir(
        json.dumps(payload).encode(),
        filename="external.openapi.json",
        source_id="src_external_schema_fact",
    )

    result = project_openapi_schema_facts(
        ir,
        source_id="src_external_schema_fact",
        source_type="openapi",
    )

    assert result["reference_count"] == 1
    assert result["unresolved_reference_count"] == 1
    reference = result["schema_references"][0]
    assert reference["local"] is False
    assert reference["resolved"] is False
    assert reference["evidence"]["exact"] is True
