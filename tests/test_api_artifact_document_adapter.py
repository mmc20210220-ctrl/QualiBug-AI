from __future__ import annotations

import json

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    build_document_structure_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.api_artifact_adapter import (
    ApiArtifactDocumentAdapter,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    DocumentSource,
)


def _serialized(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _blocks(ir: dict, node_kind: str) -> list[dict]:
    return [
        row
        for row in ir.get("blocks") or []
        if isinstance(row, dict) and row.get("node_kind") == node_kind
    ]


def test_default_pipeline_selects_openapi_adapter_and_preserves_json_pointer() -> None:
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "Orders", "version": "1"},
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer"}
            }
        },
        "paths": {
            "/orders/{id}": {
                "get": {
                    "operationId": "getOrder",
                    "summary": "Read order",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "security": [{"bearerAuth": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }

    ir = build_document_structure_ir(
        json.dumps(payload).encode(),
        filename="orders.openapi.json",
        source_id="src_openapi",
    )

    assert ir["structure_receipt"]["api_artifact_adapter"] is True
    assert ir["artifact_structure"]["artifact_kind"] == "openapi"
    operation = _blocks(ir, "OPENAPI_OPERATION")[0]
    assert operation["http_method"] == "GET"
    assert operation["api_path"] == "/orders/{id}"
    assert operation["json_pointer"] == "/paths/~1orders~1{id}/get"
    assert operation["source_locator"].endswith(
        "#json-pointer=/paths/~1orders~1{id}/get"
    )
    assert operation["evidence_address"]["address_kind"] == "EXACT_SOURCE_LOCATOR"
    assert ir["structure_receipt"]["json_pointer_traceability_rate"] == 1.0


def test_yaml_openapi_is_projected_to_canonical_json_for_existing_semantic_chain() -> None:
    source = b"""openapi: 3.0.3
info:
  title: Inventory
  version: '1'
paths:
  /items:
    post:
      operationId: createItem
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
      responses:
        '201':
          description: Created
"""

    ir = build_document_structure_ir(
        source,
        filename="inventory.openapi.yaml",
        source_id="src_yaml_openapi",
    )

    canonical = json.loads(ir["plain_text"])
    assert canonical["openapi"] == "3.0.3"
    assert canonical["paths"]["/items"]["post"]["operationId"] == "createItem"
    assert _blocks(ir, "OPENAPI_REQUEST_BODY")[0]["json_pointer"].endswith(
        "/requestBody/content/application~1json"
    )


def test_postman_structure_retains_declared_contract_but_removes_secrets() -> None:
    payload = {
        "info": {
            "name": "Orders",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "variable": [
            {"key": "access_token", "value": "COLLECTION_TOKEN_SECRET"}
        ],
        "item": [
            {
                "name": "Create order",
                "request": {
                    "method": "POST",
                    "auth": {
                        "type": "bearer",
                        "bearer": [
                            {"key": "token", "value": "BEARER_TOKEN_SECRET"}
                        ],
                    },
                    "header": [
                        {
                            "key": "Authorization",
                            "value": "Bearer HEADER_SECRET",
                        },
                        {"key": "X-Trace", "value": "trace-1"},
                    ],
                    "url": {
                        "raw": "https://api.example.test/orders?access_token=URL_SECRET&limit=10",
                        "protocol": "https",
                        "host": ["api", "example", "test"],
                        "path": ["orders"],
                        "query": [
                            {"key": "access_token", "value": "QUERY_SECRET"},
                            {"key": "limit", "value": "10"},
                        ],
                    },
                    "body": {
                        "mode": "raw",
                        "raw": "{\"username\":\"alice\",\"password\":\"BODY_SECRET\"}",
                    },
                },
                "event": [
                    {
                        "listen": "test",
                        "script": {
                            "exec": [
                                "pm.test('created', function () {",
                                "  pm.response.to.have.status(201);",
                                "});",
                            ]
                        },
                    }
                ],
                "response": [
                    {"name": "Created", "code": 201, "body": "{\"id\":1}"}
                ],
            }
        ],
    }

    ir = build_document_structure_ir(
        json.dumps(payload).encode(),
        filename="orders.postman_collection.json",
        source_id="src_postman",
    )
    material = _serialized(ir)

    for secret in (
        "COLLECTION_TOKEN_SECRET",
        "BEARER_TOKEN_SECRET",
        "HEADER_SECRET",
        "URL_SECRET",
        "QUERY_SECRET",
        "BODY_SECRET",
    ):
        assert secret not in material
    assert ir["artifact_structure"]["artifact_kind"] == "postman"
    request = _blocks(ir, "POSTMAN_REQUEST")[0]
    assert request["http_method"] == "POST"
    assert request["api_path"] == "/orders"
    assert request["auth_type"] == "BEARER"
    assert request["body_mode"] == "raw"
    assert request["json_pointer"] == "/item/0/request"
    script = _blocks(ir, "POSTMAN_SCRIPT")[0]
    assert script["declared_test_names"] == ["created"]
    assert script["script_source_retained_in_metadata"] is False
    assert "pm.response" not in _serialized(script)


def test_har_observation_structure_keeps_status_and_timing_without_credentials() -> None:
    payload = {
        "log": {
            "version": "1.2",
            "creator": {"name": "Browser", "version": "1"},
            "entries": [
                {
                    "startedDateTime": "2026-07-30T10:00:00Z",
                    "time": 123.4,
                    "request": {
                        "method": "GET",
                        "url": "https://api.example.test/orders/42?access_token=HAR_URL_SECRET",
                        "headers": [
                            {
                                "name": "Authorization",
                                "value": "Bearer HAR_HEADER_SECRET",
                            }
                        ],
                        "queryString": [
                            {"name": "access_token", "value": "HAR_QUERY_SECRET"}
                        ],
                        "cookies": [
                            {"name": "session_id", "value": "HAR_COOKIE_SECRET"}
                        ],
                    },
                    "response": {
                        "status": 500,
                        "statusText": "Internal Server Error",
                        "headers": [
                            {"name": "Set-Cookie", "value": "HAR_SET_COOKIE_SECRET"}
                        ],
                        "content": {
                            "mimeType": "application/json",
                            "text": "{\"password\":\"HAR_BODY_SECRET\"}",
                        },
                    },
                }
            ],
        }
    }

    ir = build_document_structure_ir(
        json.dumps(payload).encode(),
        filename="runtime.har",
        source_id="src_har",
    )
    material = _serialized(ir)

    for secret in (
        "HAR_URL_SECRET",
        "HAR_HEADER_SECRET",
        "HAR_QUERY_SECRET",
        "HAR_COOKIE_SECRET",
        "HAR_SET_COOKIE_SECRET",
        "HAR_BODY_SECRET",
    ):
        assert secret not in material
    entry = _blocks(ir, "HAR_ENTRY")[0]
    assert entry["http_method"] == "GET"
    assert entry["api_path"] == "/orders/42"
    assert entry["response_status"] == 500
    assert entry["elapsed_ms"] == 123.4
    assert entry["json_pointer"] == "/log/entries/0"
    assert entry["credential_values_retained"] is False


def test_plain_json_does_not_get_claimed_as_api_artifact() -> None:
    adapter = ApiArtifactDocumentAdapter()
    source = DocumentSource(
        source_id="src_plain_json",
        filename="settings.json",
        data=json.dumps({"feature": "orders", "enabled": True}).encode(),
    )

    assert adapter.probe(source) is None
    ir = build_document_structure_ir(
        source.data,
        filename=source.filename,
        source_id=source.source_id,
    )
    assert ir["structure_receipt"].get("api_artifact_adapter") is not True
    assert ir["structure_receipt"]["generic_text_fallback"] is True


def test_invalid_har_is_fail_visible_not_silently_treated_as_text() -> None:
    ir = build_document_structure_ir(
        b"{not valid json",
        filename="broken.har",
        source_id="src_broken_har",
    )

    assert ir["structure_receipt"]["status"] == "BLOCKED"
    assert ir["structure_receipt"]["api_artifact_adapter"] is True
    assert ir["unsupported_content"][0]["reason_code"] == "API_ARTIFACT_PARSE_FAILED"
    assert ir["plain_text"] == ""
