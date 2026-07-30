from __future__ import annotations

import json

from ai_test_asset_center.enterprise_knowledge_center.source_ingestion import (
    parse_enterprise_source,
)


def _by_operation(parsed: dict, operation_id: str) -> dict:
    return next(
        row
        for row in parsed["operations"]
        if row.get("operation_id") == operation_id
    )


def _pointers(operation: dict) -> set[str]:
    return {
        str(row.get("json_pointer") or "")
        for row in operation.get("technical_declarations") or []
    }


def test_component_ref_closure_attaches_only_reachable_schema_properties() -> None:
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "Orders", "version": "1"},
        "components": {
            "schemas": {
                "OrderCreate": {
                    "type": "object",
                    "required": ["amount", "address"],
                    "properties": {
                        "amount": {
                            "type": "number",
                            "format": "double",
                            "minimum": 0,
                        },
                        "address": {"$ref": "#/components/schemas/Address"},
                    },
                },
                "Address": {
                    "type": "object",
                    "required": ["city"],
                    "properties": {
                        "city": {"type": "string", "minLength": 1},
                        # Cycle verifies that local ref traversal terminates honestly.
                        "lastOrder": {"$ref": "#/components/schemas/OrderCreate"},
                    },
                },
                "UnusedInternalModel": {
                    "type": "object",
                    "properties": {
                        "internalSecret": {"type": "string"},
                    },
                },
            }
        },
        "paths": {
            "/orders": {
                "post": {
                    "operationId": "createOrder",
                    "parameters": [
                        {
                            "name": "dryRun",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "boolean"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/OrderCreate"}
                            }
                        },
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
                                            "id": {"type": "string", "minLength": 1}
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/health": {
                "get": {
                    "operationId": "health",
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    }

    parsed = parse_enterprise_source(
        json.dumps(payload).encode(),
        "orders.openapi.json",
        "openapi",
        "src_openapi_closure",
    )
    create_order = _by_operation(parsed, "createOrder")
    health = _by_operation(parsed, "health")
    pointers = _pointers(create_order)

    assert "/paths/~1orders/post/parameters/0" in pointers
    assert (
        "/paths/~1orders/post/requestBody/content/application~1json/schema"
        in pointers
    )
    assert (
        "/paths/~1orders/post/responses/201/content/application~1json/schema/properties/id"
        in pointers
    )
    assert "/components/schemas/OrderCreate" in pointers
    assert "/components/schemas/OrderCreate/properties/amount" in pointers
    assert "/components/schemas/OrderCreate/properties/address" in pointers
    assert "/components/schemas/Address" in pointers
    assert "/components/schemas/Address/properties/city" in pointers
    assert "/components/schemas/Address/properties/lastOrder" in pointers
    assert not any("UnusedInternalModel" in pointer for pointer in pointers)

    amount = next(
        row
        for row in create_order["technical_declarations"]
        if row.get("property_path") == ["amount"]
        and row.get("json_pointer")
        == "/components/schemas/OrderCreate/properties/amount"
    )
    assert amount["node_kind"] == "OPENAPI_SCHEMA_PROPERTY"
    assert amount["required"] is True
    assert amount["schema_type"] == "number"
    assert amount["schema_format"] == "double"
    assert amount["constraints"]["minimum"] == 0
    assert amount["ownership"] == "LOCAL_REF_COMPONENT_CLOSURE"
    assert amount["source_traceability"] == "EXACT_JSON_POINTER"

    inline_id = next(
        row
        for row in create_order["technical_declarations"]
        if row.get("property_name") == "id"
        and row.get("json_pointer", "").startswith("/paths/~1orders/post/")
    )
    assert inline_id["ownership"] == "INLINE_OPERATION_POINTER_PREFIX"
    assert inline_id["required"] is True
    assert inline_id["constraints"]["minLength"] == 1

    closure = create_order["openapi_local_ref_closure"]
    assert closure["reachable_local_refs"] == [
        "#/components/schemas/Address",
        "#/components/schemas/OrderCreate",
    ]
    assert closure["unrelated_components_attached"] is False
    assert create_order["technical_declaration_count"] == len(
        create_order["technical_declarations"]
    )
    assert create_order["exact_technical_declaration_rate"] == 1.0

    # The unrelated operation must not inherit the order request model.
    assert not any(
        pointer.startswith("/components/schemas/")
        for pointer in _pointers(health)
    )
    receipt = parsed["api_artifact_semantic_receipt"]
    assert receipt["openapi_technical_declaration_count"] >= 10
    assert receipt["openapi_exact_technical_declaration_rate"] == 1.0


def test_inline_schema_without_ref_stays_owned_by_its_operation() -> None:
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "Profiles", "version": "1"},
        "paths": {
            "/profiles": {
                "post": {
                    "operationId": "createProfile",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["email"],
                                    "properties": {
                                        "email": {
                                            "type": "string",
                                            "format": "email",
                                            "maxLength": 320,
                                        }
                                    },
                                }
                            }
                        }
                    },
                    "responses": {"204": {"description": "Created"}},
                }
            },
            "/profiles/{id}": {
                "delete": {
                    "operationId": "deleteProfile",
                    "responses": {"204": {"description": "Deleted"}},
                }
            },
        },
    }

    parsed = parse_enterprise_source(
        json.dumps(payload).encode(),
        "profiles.openapi.json",
        "openapi",
        "src_inline_schema",
    )
    create_profile = _by_operation(parsed, "createProfile")
    delete_profile = _by_operation(parsed, "deleteProfile")

    email_pointer = (
        "/paths/~1profiles/post/requestBody/content/application~1json/"
        "schema/properties/email"
    )
    email = next(
        row
        for row in create_profile["technical_declarations"]
        if row.get("json_pointer") == email_pointer
    )
    assert email["required"] is True
    assert email["schema_format"] == "email"
    assert email["constraints"]["maxLength"] == 320
    assert email["ownership"] == "INLINE_OPERATION_POINTER_PREFIX"
    assert email_pointer not in _pointers(delete_profile)
