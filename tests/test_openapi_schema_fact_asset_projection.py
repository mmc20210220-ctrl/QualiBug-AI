from __future__ import annotations

import json

from ai_test_asset_center.enterprise_knowledge_center import composition
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    build_document_structure_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.openapi_schema_fact_asset_projection import (
    OPENAPI_SCHEMA_FACT_ASSET_SCHEMA,
    enrich_asset_with_openapi_schema_facts,
)


def _structure(payload: dict, filename: str, source_id: str) -> dict:
    return build_document_structure_ir(
        json.dumps(payload).encode(),
        filename=filename,
        source_id=source_id,
    )


def _openapi(properties: dict, *, extra_schemas: dict | None = None) -> dict:
    schemas = {
        "Order": {
            "type": "object",
            "required": list(properties),
            "properties": properties,
        }
    }
    schemas.update(extra_schemas or {})
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


def test_same_name_schemas_remain_source_scoped_and_do_not_pollute_database_assets() -> None:
    asset = {
        "source_inventory": [
            {"source_id": "src_a", "source_type": "openapi"},
            {"source_id": "src_b", "source_type": "openapi"},
        ],
        "tables": [{"table_id": "db:orders", "name": "orders"}],
        "field_dictionary": [
            {"field_id": "db:orders:id", "table_id": "db:orders", "field": "id"}
        ],
        "summary": {},
        "governance": {},
    }
    sources = [
        {
            "source_id": "src_a",
            "document_structure": _structure(
                _openapi(
                    {
                        "amount": {
                            "type": "number",
                            "format": "double",
                            "minimum": 0,
                        }
                    }
                ),
                "orders-a.openapi.json",
                "src_a",
            ),
        },
        {
            "source_id": "src_b",
            "document_structure": _structure(
                _openapi(
                    {
                        "externalCode": {
                            "type": "string",
                            "minLength": 1,
                        }
                    }
                ),
                "orders-b.openapi.json",
                "src_b",
            ),
        },
    ]

    result = enrich_asset_with_openapi_schema_facts(asset, sources)

    definitions = [
        row for row in result["openapi_schema_definitions"] if row["name"] == "Order"
    ]
    assert len(definitions) == 2
    assert {row["source_id"] for row in definitions} == {"src_a", "src_b"}
    assert len({row["schema_id"] for row in definitions}) == 2

    fields = result["openapi_schema_fields"]
    assert {row["source_id"] for row in fields} == {"src_a", "src_b"}
    assert {
        (row["source_id"], tuple(row["property_path"])) for row in fields
    } == {
        ("src_a", ("amount",)),
        ("src_b", ("externalCode",)),
    }
    amount = next(row for row in fields if row["source_id"] == "src_a")
    assert amount["constraints"]["minimum"] == 0
    assert amount["source_locator"].startswith("orders-a.openapi.json#")

    entities = [row for row in result["openapi_schema_entities"] if row["name"] == "Order"]
    assert len(entities) == 2
    assert all(row["database_table"] is False for row in entities)
    assert all(row["business_object_confirmed"] is False for row in entities)

    # Existing database assets remain untouched; API models use a separate namespace.
    assert result["tables"] == asset["tables"]
    assert result["field_dictionary"] == asset["field_dictionary"]
    receipt = result["openapi_schema_fact_projection"]
    assert receipt["schema"] == OPENAPI_SCHEMA_FACT_ASSET_SCHEMA
    assert receipt["status"] == "COMPLETE"
    assert receipt["schema_definition_count"] == 2
    assert receipt["schema_field_count"] == 2
    assert receipt["source_scoped_identity"] is True
    assert receipt["same_name_cross_source_auto_merge"] is False
    assert receipt["database_table_projection_used"] is False
    assert result["governance"]["openapi_schema_models_are_not_database_tables"] is True


def test_unresolved_schema_reference_is_retained_and_marks_projection_partial() -> None:
    payload = _openapi(
        {
            "customer": {"$ref": "#/components/schemas/MissingCustomer"},
        }
    )
    asset = {
        "source_inventory": [{"source_id": "src_missing", "source_type": "openapi"}],
        "summary": {},
        "governance": {},
    }
    sources = [
        {
            "source_id": "src_missing",
            "document_structure": _structure(
                payload,
                "orders-missing.openapi.json",
                "src_missing",
            ),
        }
    ]

    result = enrich_asset_with_openapi_schema_facts(asset, sources)

    references = result["openapi_schema_references"]
    assert len(references) == 1
    reference = references[0]
    assert reference["reference_id"]
    assert reference["target_ref"] == "#/components/schemas/MissingCustomer"
    assert reference["resolution_status"] == "UNRESOLVED"
    assert reference["unresolved_reason"] == "OPENAPI_LOCAL_REF_TARGET_NOT_FOUND"
    assert reference["source_locator"].startswith("orders-missing.openapi.json#")

    entity = next(row for row in result["openapi_schema_entities"] if row["name"] == "Order")
    assert entity["reference_ids"] == [reference["reference_id"]]
    receipt = result["openapi_schema_fact_projection"]
    assert receipt["status"] == "PARTIAL"
    assert receipt["unresolved_reference_count"] == 1
    assert receipt["schema_reference_count"] == 1
    assert receipt["exact_fact_rate"] == 1.0


def test_composition_root_runs_schema_fact_projection_before_understanding(monkeypatch) -> None:
    structure = _structure(
        _openapi({"amount": {"type": "number", "minimum": 0}}),
        "orders.openapi.json",
        "src_orders",
    )
    base_asset = {
        "source_inventory": [{"source_id": "src_orders", "source_type": "openapi"}],
        "structured_sources": [
            {"source_id": "src_orders", "document_structure": structure}
        ],
        "interfaces": [],
        "summary": {},
        "governance": {},
    }

    monkeypatch.setattr(
        composition._base,
        "compile_enterprise_knowledge_asset",
        lambda project_id, root=None: base_asset,
    )
    monkeypatch.setattr(
        composition,
        "enrich_asset_with_api_artifact_semantics",
        lambda asset, sources: asset,
    )
    monkeypatch.setattr(
        composition,
        "enrich_asset_with_document_ir_tabular_semantics",
        lambda asset, sources: asset,
    )
    monkeypatch.setattr(
        composition,
        "enrich_asset_with_enterprise_understanding",
        lambda asset, sources: asset,
    )

    result = composition.compile_enterprise_knowledge_asset("project_openapi_schema")

    assert result["openapi_schema_fact_projection"]["status"] == "COMPLETE"
    assert result["openapi_schema_fields"][0]["property_path"] == ["amount"]
    assert result["composition_receipt"]["stages"][1]["stage"] == (
        "openapi_schema_fact_projection"
    )
    assert result["governance"]["openapi_schema_facts_composed_explicitly"] is True
