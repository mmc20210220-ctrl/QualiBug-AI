from __future__ import annotations

import json

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    build_document_structure_ir,
)


def _by_pointer(ir: dict, pointer: str) -> dict:
    return next(
        row
        for row in ir.get("blocks") or []
        if isinstance(row, dict) and row.get("json_pointer") == pointer
    )


def test_component_schema_properties_preserve_constraints_and_exact_addresses() -> None:
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "Orders", "version": "1"},
        "paths": {},
        "components": {
            "schemas": {
                "Order": {
                    "type": "object",
                    "required": ["id", "status", "amount"],
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "status": {
                            "type": "string",
                            "enum": ["PENDING", "APPROVED", "REJECTED"],
                        },
                        "amount": {
                            "type": "number",
                            "format": "decimal",
                            "minimum": 0,
                            "maximum": 999999,
                        },
                        "lines": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"$ref": "#/components/schemas/OrderLine"},
                        },
                    },
                },
                "OrderLine": {
                    "type": "object",
                    "required": ["sku", "quantity"],
                    "properties": {
                        "sku": {"type": "string", "minLength": 1},
                        "quantity": {"type": "integer", "minimum": 1},
                    },
                },
            }
        },
    }

    ir = build_document_structure_ir(
        json.dumps(payload).encode(),
        filename="orders.openapi.json",
        source_id="src_schema",
    )

    receipt = ir["structure_receipt"]
    assert receipt["openapi_schema_projection"] is True
    assert receipt["openapi_schema_count"] == 2
    assert receipt["openapi_schema_property_count"] == 7
    assert receipt["openapi_unresolved_reference_count"] == 0
    amount = _by_pointer(ir, "/components/schemas/Order/properties/amount")
    assert amount["schema_type"] == "number"
    assert amount["schema_format"] == "decimal"
    assert amount["required"] is True
    assert amount["constraints"]["minimum"] == 0
    assert amount["constraints"]["maximum"] == 999999
    assert "#block=json-pointer:/components/schemas/Order/properties/amount" in amount["source_locator"]
    assert amount["source_locator"].endswith(
        "#json-pointer=/components/schemas/Order/properties/amount"
    )
    assert amount["evidence_address"]["address_kind"] == "EXACT_SOURCE_LOCATOR"
    items = _by_pointer(ir, "/components/schemas/Order/properties/lines/items")
    assert items["ref"] == "#/components/schemas/OrderLine"
    assert ir["openapi_schema_projection_receipt"]["exact_json_pointer_addresses"] is True


def test_request_and_response_inline_schemas_are_projected() -> None:
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "Orders", "version": "1"},
        "paths": {
            "/orders": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["customerId"],
                                    "properties": {
                                        "customerId": {"type": "string"},
                                        "note": {"type": "string", "maxLength": 200},
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
                                        "properties": {"id": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }

    ir = build_document_structure_ir(
        json.dumps(payload).encode(),
        filename="orders.openapi.json",
        source_id="src_inline_schema",
    )

    request_pointer = (
        "/paths/~1orders/post/requestBody/content/application~1json/schema/properties/customerId"
    )
    response_pointer = (
        "/paths/~1orders/post/responses/201/content/application~1json/schema/properties/id"
    )
    assert _by_pointer(ir, request_pointer)["required"] is True
    assert _by_pointer(ir, response_pointer)["required"] is True
    assert ir["structure_receipt"]["openapi_schema_count"] == 2


def test_sensitive_schema_defaults_examples_and_enums_are_redacted_before_projection() -> None:
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "Auth", "version": "1"},
        "paths": {},
        "components": {
            "schemas": {
                "Credentials": {
                    "type": "object",
                    "properties": {
                        "password": {
                            "type": "string",
                            "default": "PASSWORD_DEFAULT_SECRET",
                            "example": "PASSWORD_EXAMPLE_SECRET",
                            "enum": ["PASSWORD_ENUM_SECRET"],
                        },
                        "access_token": {
                            "type": "string",
                            "default": "TOKEN_DEFAULT_SECRET",
                        },
                    },
                }
            }
        },
    }

    ir = build_document_structure_ir(
        json.dumps(payload).encode(),
        filename="auth.openapi.json",
        source_id="src_sensitive_schema",
    )
    material = json.dumps(ir, ensure_ascii=False, sort_keys=True)

    for secret in (
        "PASSWORD_DEFAULT_SECRET",
        "PASSWORD_EXAMPLE_SECRET",
        "PASSWORD_ENUM_SECRET",
        "TOKEN_DEFAULT_SECRET",
    ):
        assert secret not in material
    password = _by_pointer(ir, "/components/schemas/Credentials/properties/password")
    assert password["constraints"]["default"] == "<redacted>"
    assert password["constraints"]["enum"] == ["<redacted>"]
    assert password["constraints"]["example_present"] is True
    assert ir["structure_receipt"]["sensitive_schema_defaults_and_examples_redacted"] is True


def test_unresolved_local_and_external_refs_block_formal_schema_understanding() -> None:
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "Broken refs", "version": "1"},
        "paths": {},
        "components": {
            "schemas": {
                "LocalBroken": {"$ref": "#/components/schemas/Missing"},
                "External": {"$ref": "other.yaml#/components/schemas/External"},
            }
        },
    }

    ir = build_document_structure_ir(
        json.dumps(payload).encode(),
        filename="refs.openapi.json",
        source_id="src_refs",
    )

    reasons = {
        row.get("reason_code") for row in ir.get("unsupported_content") or []
    }
    assert "OPENAPI_LOCAL_SCHEMA_REF_UNRESOLVED" in reasons
    assert "OPENAPI_EXTERNAL_SCHEMA_REF_NOT_RESOLVED" in reasons
    assert ir["structure_receipt"]["status"] == "BLOCKED"
    assert ir["structure_receipt"]["openapi_unresolved_reference_count"] == 2
