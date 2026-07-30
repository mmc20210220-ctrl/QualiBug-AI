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


def test_path_level_parameter_schema_keeps_path_item_pointer() -> None:
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
                        "schema": {
                            "type": "string",
                            "format": "uuid",
                            "minLength": 36,
                        },
                    }
                ],
                "get": {
                    "responses": {"200": {"description": "OK"}},
                },
                "delete": {
                    "responses": {"204": {"description": "Deleted"}},
                },
            }
        },
    }

    ir = build_document_structure_ir(
        json.dumps(payload).encode(),
        filename="orders.openapi.json",
        source_id="src_path_parameter",
    )

    pointer = "/paths/~1orders~1{orderId}/parameters/0/schema"
    schema = _by_pointer(ir, pointer)
    assert schema["schema_type"] == "string"
    assert schema["schema_format"] == "uuid"
    assert schema["constraints"]["minLength"] == 36
    assert schema["source_locator"].endswith(f"#json-pointer={pointer}")
    assert not any(
        row.get("json_pointer")
        in {
            "/paths/~1orders~1{orderId}/get/parameters/0/schema",
            "/paths/~1orders~1{orderId}/delete/parameters/0/schema",
        }
        for row in ir.get("blocks") or []
        if isinstance(row, dict)
    )
    receipt = ir["structure_receipt"]
    assert receipt["openapi_path_parameter_schema_projection"] is True
    assert receipt["openapi_path_parameter_schema_count"] == 1
    assert receipt["path_level_parameter_schema_pointer_correctness"] is True
