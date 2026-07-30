from __future__ import annotations

import json

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    build_document_structure_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ir_api_semantics import (
    HAR_RUNTIME_OBSERVATION_SCHEMA,
    POSTMAN_RUNTIME_CONTRACT_SCHEMA,
    enrich_parsed_api_artifact_semantics,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.interface_runtime_contracts import (
    OPENAPI_RUNTIME_CONTRACT_SCHEMA,
)
from ai_test_asset_center.enterprise_knowledge_center.source_ingestion import (
    parse_enterprise_source,
)


def _serialized(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def test_openapi_contracts_and_exact_evidence_enter_canonical_parse_result() -> None:
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "Orders", "version": "1"},
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                }
            },
            "schemas": {
                "OrderCreate": {
                    "type": "object",
                    "required": ["amount"],
                    "properties": {
                        "amount": {"type": "number", "format": "double"},
                        "note": {"type": "string"},
                    },
                },
                "Order": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "status": {"type": "string", "enum": ["NEW", "PAID"]},
                    },
                },
            },
        },
        "paths": {
            "/orders/{id}": {
                "get": {
                    "operationId": "getOrder",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "security": [{"bearerAuth": []}],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Order"}
                                }
                            },
                        }
                    },
                }
            },
            "/orders": {
                "post": {
                    "operationId": "createOrder",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/OrderCreate"}
                            }
                        },
                    },
                    "responses": {"201": {"description": "Created"}},
                }
            },
        },
    }

    parsed = parse_enterprise_source(
        json.dumps(payload).encode(),
        "orders.openapi.json",
        "openapi",
        "src_openapi_semantics",
    )

    by_id = {row["interface_id"]: row for row in parsed["operations"]}
    get_order = by_id["api:GET:/orders/{id}"]
    create_order = by_id["api:POST:/orders"]

    assert get_order["runtime_contract_schema"] == OPENAPI_RUNTIME_CONTRACT_SCHEMA
    assert get_order["json_pointer"] == "/paths/~1orders~1{id}/get"
    assert get_order["source_locator"].endswith(
        "#json-pointer=/paths/~1orders~1{id}/get"
    )
    path_parameter = next(
        row for row in get_order["parameter_contracts"] if row["name"] == "id"
    )
    assert path_parameter["location"] == "PATH"
    assert path_parameter["required"] is True
    assert path_parameter["json_pointer"].endswith("/get/parameters/0")
    assert get_order["response_contracts"][0]["fields"][0]["field"] == "id"
    assert get_order["response_contracts"][0]["json_pointer"].endswith(
        "/get/responses/200"
    )
    assert get_order["security_requirements"][0]["scheme"] == "bearerAuth"
    assert get_order["security_requirements"][0]["credential_value_retained"] is False

    amount = next(
        row for row in create_order["request_body_fields"] if row["field"] == "amount"
    )
    assert amount["required"] is True
    assert amount["location"] == "BODY"
    assert amount["json_pointer"].endswith(
        "/post/requestBody/content/application~1json"
    )
    semantic_receipt = parsed["api_artifact_semantic_receipt"]
    assert semantic_receipt["artifact_kind"] == "openapi"
    assert semantic_receipt["exact_operation_evidence_rate"] == 1.0
    assert parsed["parser_receipt"]["outputs"]["operations"] == 2


def test_postman_duplicate_interface_rows_become_two_evidenced_request_variants() -> None:
    payload = {
        "info": {
            "name": "Order variants",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": [
            {
                "name": "Happy path",
                "item": [
                    {
                        "name": "Create with limit",
                        "request": {
                            "method": "POST",
                            "auth": {
                                "type": "bearer",
                                "bearer": [
                                    {"key": "token", "value": "POSTMAN_SECRET_ONE"}
                                ],
                            },
                            "header": [
                                {
                                    "key": "Authorization",
                                    "value": "Bearer POSTMAN_HEADER_SECRET",
                                }
                            ],
                            "url": {
                                "raw": "https://api.example.test/orders?limit=10",
                                "path": ["orders"],
                                "query": [{"key": "limit", "value": "10"}],
                            },
                            "body": {
                                "mode": "raw",
                                "raw": "{\"password\":\"POSTMAN_BODY_SECRET\"}",
                            },
                        },
                        "event": [
                            {
                                "listen": "test",
                                "script": {"exec": ["pm.test('created', function () {});"]},
                            }
                        ],
                        "response": [{"name": "Created", "code": 201, "body": "{}"}],
                    }
                ],
            },
            {
                "name": "Alternate path",
                "item": [
                    {
                        "name": "Create in dry-run mode",
                        "request": {
                            "method": "POST",
                            "header": [{"key": "X-Mode", "value": "dry-run"}],
                            "url": {
                                "raw": "https://api.example.test/orders",
                                "path": ["orders"],
                            },
                        },
                        "event": [
                            {
                                "listen": "test",
                                "script": {"exec": ["pm.test('accepted', function () {});"]},
                            }
                        ],
                        "response": [{"name": "Accepted", "code": 202, "body": "{}"}],
                    }
                ],
            },
        ],
    }

    parsed = parse_enterprise_source(
        json.dumps(payload).encode(),
        "orders.postman_collection.json",
        "postman",
        "src_postman_semantics",
    )

    assert len(parsed["operations"]) == 1
    operation = parsed["operations"][0]
    assert operation["interface_id"] == "postman:POST:/orders"
    assert operation["runtime_contract_schema"] == POSTMAN_RUNTIME_CONTRACT_SCHEMA
    assert operation["request_variant_count"] == 2
    assert operation["identity_duplicate_count"] == 2
    assert {row["request_name"] for row in operation["postman_request_variants"]} == {
        "Create with limit",
        "Create in dry-run mode",
    }
    assert {row["location"] for row in operation["parameter_contracts"]} >= {
        "QUERY",
        "HEADER",
    }
    assert {row["name"] for row in operation["parameter_contracts"]} >= {
        "limit",
        "X-Mode",
    }
    declared_tests = {
        name
        for script in operation["script_contracts"]
        for name in script["declared_test_names"]
    }
    assert declared_tests == {"created", "accepted"}
    assert {row["status"] for row in operation["response_examples"]} == {"201", "202"}
    assert all(
        row["source_traceability"] == "EXACT_JSON_POINTER"
        for row in operation["postman_request_variants"]
    )
    material = _serialized(parsed)
    for secret in (
        "POSTMAN_SECRET_ONE",
        "POSTMAN_HEADER_SECRET",
        "POSTMAN_BODY_SECRET",
    ):
        assert secret not in material
    assert parsed["api_artifact_semantic_receipt"]["postman_request_variant_count"] == 2


def test_har_entries_for_same_interface_are_runtime_observations_not_contracts() -> None:
    payload = {
        "log": {
            "version": "1.2",
            "creator": {"name": "Browser", "version": "1"},
            "entries": [
                {
                    "startedDateTime": "2026-07-30T10:00:00Z",
                    "time": 80,
                    "request": {
                        "method": "GET",
                        "url": "https://api.example.test/orders/42?access_token=HAR_SECRET_ONE",
                        "headers": [{"name": "Authorization", "value": "Bearer HAR_HEADER_ONE"}],
                        "queryString": [{"name": "access_token", "value": "HAR_QUERY_ONE"}],
                    },
                    "response": {
                        "status": 200,
                        "headers": [],
                        "content": {"mimeType": "application/json", "text": "{}"},
                    },
                },
                {
                    "startedDateTime": "2026-07-30T10:01:00Z",
                    "time": 240,
                    "request": {
                        "method": "GET",
                        "url": "https://api.example.test/orders/42?access_token=HAR_SECRET_TWO",
                        "headers": [{"name": "Cookie", "value": "session=HAR_COOKIE_TWO"}],
                        "queryString": [{"name": "access_token", "value": "HAR_QUERY_TWO"}],
                    },
                    "response": {
                        "status": 500,
                        "headers": [{"name": "Set-Cookie", "value": "HAR_SET_COOKIE_TWO"}],
                        "content": {
                            "mimeType": "application/json",
                            "text": "{\"password\":\"HAR_BODY_TWO\"}",
                        },
                    },
                },
            ],
        }
    }
    document_ir = build_document_structure_ir(
        json.dumps(payload).encode(),
        filename="orders.har",
        source_id="src_har_semantics",
    )
    enriched = enrich_parsed_api_artifact_semantics(
        {
            "operations": [
                {
                    "path": "/orders/42",
                    "method": "GET",
                    "source": "har_traffic",
                    "summary": "observed endpoint",
                }
            ]
        },
        document_ir,
        source_id="src_har_semantics",
        source_type="har",
    )

    assert len(enriched["operations"]) == 1
    operation = enriched["operations"][0]
    assert operation["interface_id"] == "har:GET:/orders/42"
    assert operation["runtime_observation_schema"] == HAR_RUNTIME_OBSERVATION_SCHEMA
    assert operation["observation_count"] == 2
    assert operation["observed_status_distribution"] == {"200": 1, "500": 1}
    assert operation["observed_error_count"] == 1
    assert operation["minimum_elapsed_ms"] == 80.0
    assert operation["maximum_elapsed_ms"] == 240.0
    assert operation["contract_authority"] is False
    assert operation["observation_authority"] == "HAR_RUNTIME_EVIDENCE"
    assert all(
        row["source_traceability"] == "EXACT_JSON_POINTER"
        for row in operation["runtime_observations"]
    )
    assert enriched["api_artifact_semantic_receipt"]["har_runtime_observation_count"] == 2
    assert enriched["api_artifact_semantic_receipt"][
        "har_is_runtime_observation_not_design_contract"
    ] is True
    material = _serialized(document_ir)
    for secret in (
        "HAR_SECRET_ONE",
        "HAR_HEADER_ONE",
        "HAR_QUERY_ONE",
        "HAR_SECRET_TWO",
        "HAR_COOKIE_TWO",
        "HAR_QUERY_TWO",
        "HAR_SET_COOKIE_TWO",
        "HAR_BODY_TWO",
    ):
        assert secret not in material
