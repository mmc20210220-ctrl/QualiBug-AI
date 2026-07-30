from __future__ import annotations

import json

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    build_document_structure_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.openapi_schema_fact_asset_projection import (
    OPENAPI_SCHEMA_FACT_ASSET_SCHEMA,
    enrich_asset_with_openapi_schema_facts,
)


def _structure(payload: dict, filename: str, source_id: str) -> dict:
    return build_document_structure_ir(
        json.dumps(payload).encode("utf-8"),
        filename=filename,
        source_id=source_id,
    )


def _openapi(*, missing_ref: bool = False) -> dict:
    customer_ref = (
        "#/components/schemas/MissingCustomer"
        if missing_ref
        else "#/components/schemas/Customer"
    )
    schemas = {
        "Order": {
            "type": "object",
            "required": ["id", "amount"],
            "properties": {
                "id": {"type": "integer", "format": "int64"},
                "amount": {"type": "number", "minimum": 0},
                "customer": {"$ref": customer_ref},
            },
        }
    }
    if not missing_ref:
        schemas["Customer"] = {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
        }
    return {
        "openapi": "3.0.3",
        "info": {"title": "Orders", "version": "1"},
        "components": {"schemas": schemas},
        "paths": {
            "/orders": {
                "post": {
                    "operationId": "createOrder",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Order"}
                            }
                        }
                    },
                    "responses": {"201": {"description": "Created"}},
                }
            }
        },
    }


def test_current_projector_contract_is_normalized_into_source_scoped_assets() -> None:
    asset = {
        "source_inventory": [{"source_id": "src_api", "source_type": "openapi"}],
        "tables": [{"table_id": "table:main.Order", "name": "Order"}],
        "field_dictionary": [],
        "summary": {},
        "governance": {},
    }
    sources = [
        {
            "source_id": "src_api",
            "document_structure": _structure(
                _openapi(),
                "orders.openapi.json",
                "src_api",
            ),
        }
    ]

    result = enrich_asset_with_openapi_schema_facts(asset, sources)

    definitions = result["openapi_schema_definitions"]
    assert {row["name"] for row in definitions} >= {"Order", "Customer"}
    order = next(row for row in definitions if row["name"] == "Order")
    assert order["schema_id"] == order["schema_definition_id"]
    assert order["json_pointer"] == "/components/schemas/Order"
    assert order["source_locator"].startswith("orders.openapi.json#")

    order_fields = [
        row
        for row in result["openapi_schema_fields"]
        if row["schema_id"] == order["schema_id"]
    ]
    assert {row["field_name"] for row in order_fields} == {
        "id",
        "amount",
        "customer",
    }
    amount = next(row for row in order_fields if row["field_name"] == "amount")
    assert amount["field_fact_id"] == amount["schema_field_id"]
    assert amount["property_path"] == ["amount"]
    assert amount["constraints"]["minimum"] == 0
    assert amount["source_locator"].startswith("orders.openapi.json#")

    reference = next(
        row
        for row in result["openapi_schema_references"]
        if row["target_ref"] == "#/components/schemas/Customer"
    )
    assert reference["reference_id"] == reference["schema_reference_id"]
    assert reference["resolution_status"] == "RESOLVED"

    entity = next(
        row for row in result["openapi_schema_entities"] if row["name"] == "Order"
    )
    assert set(entity["field_ids"]) == {
        row["field_fact_id"] for row in order_fields
    }
    assert reference["reference_id"] in entity["reference_ids"]
    assert entity["database_table"] is False

    # API schemas remain separate from physical database assets.
    assert result["tables"] == asset["tables"]
    receipt = result["openapi_schema_fact_projection"]
    assert receipt["schema"] == OPENAPI_SCHEMA_FACT_ASSET_SCHEMA
    assert receipt["status"] == "COMPLETE"
    assert receipt["unowned_field_count"] == 0
    assert receipt["exact_fact_rate"] == 1.0
    assert result["governance"][
        "openapi_schema_asset_contract_matches_current_projector"
    ] is True


def test_unresolved_local_reference_is_retained_and_fail_visible() -> None:
    asset = {
        "source_inventory": [{"source_id": "src_missing", "source_type": "openapi"}],
        "summary": {},
        "governance": {},
    }
    sources = [
        {
            "source_id": "src_missing",
            "document_structure": _structure(
                _openapi(missing_ref=True),
                "orders-missing.openapi.json",
                "src_missing",
            ),
        }
    ]

    result = enrich_asset_with_openapi_schema_facts(asset, sources)

    reference = next(
        row
        for row in result["openapi_schema_references"]
        if row["target_ref"] == "#/components/schemas/MissingCustomer"
    )
    assert reference["resolution_status"] == "UNRESOLVED"
    assert reference["unresolved_reason"] == "OPENAPI_LOCAL_REF_TARGET_NOT_FOUND"
    assert result["openapi_schema_fact_projection"]["status"] == "PARTIAL"
    assert result["openapi_schema_fact_projection"]["unresolved_reference_count"] == 1


def test_same_schema_name_across_sources_remains_source_scoped() -> None:
    asset = {
        "source_inventory": [
            {"source_id": "src_a", "source_type": "openapi"},
            {"source_id": "src_b", "source_type": "openapi"},
        ],
        "summary": {},
        "governance": {},
    }
    sources = [
        {
            "source_id": "src_a",
            "document_structure": _structure(_openapi(), "a.json", "src_a"),
        },
        {
            "source_id": "src_b",
            "document_structure": _structure(_openapi(), "b.json", "src_b"),
        },
    ]

    result = enrich_asset_with_openapi_schema_facts(asset, sources)

    orders = [
        row for row in result["openapi_schema_definitions"] if row["name"] == "Order"
    ]
    assert len(orders) == 2
    assert {row["source_id"] for row in orders} == {"src_a", "src_b"}
    assert len({row["schema_id"] for row in orders}) == 2
    assert result["openapi_schema_fact_projection"][
        "same_name_cross_source_auto_merge"
    ] is False
