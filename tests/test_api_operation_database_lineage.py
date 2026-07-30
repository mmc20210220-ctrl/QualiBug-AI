from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.api_operation_database_projection import (
    API_OPERATION_DATABASE_PROJECTION_SCHEMA,
    enrich_asset_with_api_operation_database_candidates,
)
from ai_test_asset_center.enterprise_knowledge_center.api_operation_schema_binding import (
    API_OPERATION_SCHEMA_BINDING_RECEIPT_SCHEMA,
    enrich_asset_with_api_operation_schema_bindings,
)


def _interface(source_id: str = "src_api") -> dict:
    return {
        "interface_id": "api:POST:/orders",
        "method": "POST",
        "path": "/orders",
        "source_ids": [source_id],
        "api_artifact_source_records": [
            {
                "source_id": source_id,
                "artifact_kind": "openapi",
                "interface_id": "api:POST:/orders",
            }
        ],
    }


def _entity(source_id: str = "src_api") -> dict:
    return {
        "entity_id": "api_schema_entity:schema:order",
        "schema_id": "schema:order",
        "source_id": source_id,
        "name": "Order",
        "field_ids": ["api_field:amount"],
    }


def _request_ref(source_id: str = "src_api") -> dict:
    return {
        "reference_id": "ref:create-order-request",
        "source_id": source_id,
        "api_path": "/orders",
        "method": "POST",
        "direction": "request",
        "context_kind": "request_schema",
        "media_type": "application/json",
        "target_ref": "#/components/schemas/Order",
        "resolution_status": "RESOLVED",
        "json_pointer": (
            "/paths/~1orders/post/requestBody/content/"
            "application~1json/schema"
        ),
        "source_locator": (
            "orders.openapi.json#block=json-pointer:/paths/~1orders/post/"
            "requestBody/content/application~1json/schema"
        ),
    }


def test_exact_openapi_ref_binds_source_local_operation_to_schema() -> None:
    asset = {
        "interfaces": [_interface()],
        "openapi_schema_entities": [_entity()],
        "openapi_schema_references": [_request_ref()],
        "relationships": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }

    result = enrich_asset_with_api_operation_schema_bindings(asset)

    assert len(result["api_operation_schema_bindings"]) == 1
    binding = result["api_operation_schema_bindings"][0]
    assert binding["interface_id"] == "api:POST:/orders"
    assert binding["api_schema_entity_id"] == "api_schema_entity:schema:order"
    assert binding["direction"] == "request"
    assert binding["media_type"] == "application/json"
    assert binding["status"] == "ACCEPTED_EXACT_OPENAPI_REFERENCE"
    assert binding["contract_authority"] == "OPENAPI_EXACT_SCHEMA_REFERENCE"
    assert binding["database_mapping_confirmed"] is False
    assert binding["source_locator"].startswith("orders.openapi.json#block=")

    edge = next(
        row
        for row in result["relationships"]
        if row.get("relation") == "api_operation_uses_schema"
    )
    assert edge["status"] == "accepted"
    assert edge["confidence"] == 1.0
    receipt = result["api_operation_schema_binding_receipt"]
    assert receipt["schema"] == API_OPERATION_SCHEMA_BINDING_RECEIPT_SCHEMA
    assert receipt["status"] == "COMPLETE"
    assert receipt["accepted_binding_count"] == 1
    assert receipt["database_mapping_inferred"] is False


def test_interface_resolution_is_source_scoped_and_fail_visible() -> None:
    asset = {
        "interfaces": [_interface("src_other")],
        "openapi_schema_entities": [_entity()],
        "openapi_schema_references": [_request_ref()],
        "relationships": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }

    result = enrich_asset_with_api_operation_schema_bindings(asset)

    assert result["api_operation_schema_bindings"] == []
    gap = next(
        row
        for row in result["coverage_gaps"]
        if row.get("kind") == "OPENAPI_OPERATION_INTERFACE_NOT_FOUND"
    )
    assert gap["source_id"] == "src_api"
    assert gap["blocks_operation_schema_binding"] is True
    assert result["api_operation_schema_binding_receipt"]["status"] == "PARTIAL"


def test_exact_operation_schema_plus_pending_storage_mapping_stays_candidate_only() -> None:
    bound = enrich_asset_with_api_operation_schema_bindings(
        {
            "interfaces": [_interface()],
            "openapi_schema_entities": [_entity()],
            "openapi_schema_references": [_request_ref()],
            "relationships": [],
            "coverage_gaps": [],
            "summary": {},
            "governance": {},
        }
    )
    bound["api_database_entity_alignment_candidates"] = [
        {
            "candidate_id": "entity-candidate:order",
            "api_entity_id": "api_schema_entity:schema:order",
            "database_table_matches": [
                {
                    "table_id": "table:main.Order",
                    "schema_name": "main",
                    "qualified_name": "main.Order",
                }
            ],
            "status": "PENDING_BUSINESS_OBJECT_AUTHORITY",
            "automatic_merge_allowed": False,
        }
    ]
    bound["api_database_field_alignment_candidates"] = [
        {
            "candidate_id": "field-candidate:amount",
            "entity_candidate_id": "entity-candidate:order",
            "api_field_id": "api_field:amount",
            "api_field_name": "amount",
            "api_property_path": ["amount"],
            "database_table_id": "table:main.Order",
            "database_field_id": "db_field:main.Order:amount",
            "database_field_name": "amount",
            "type_compatibility": {
                "status": "COMPATIBLE",
                "api_declared_type": "number",
                "database_declared_type": "NUMERIC(12,2)",
            },
            "automatic_mapping_allowed": False,
        }
    ]

    result = enrich_asset_with_api_operation_database_candidates(bound)

    table_candidate = result["api_operation_database_table_candidates"][0]
    assert table_candidate["interface_id"] == "api:POST:/orders"
    assert table_candidate["direction"] == "request"
    assert table_candidate["database_table_id"] == "table:main.Order"
    assert table_candidate["status"] == "PENDING_STORAGE_AUTHORITY"
    assert table_candidate["write_target_allowed"] is False
    assert table_candidate["oracle_authority_allowed"] is False

    field_candidate = result["api_operation_database_field_candidates"][0]
    assert field_candidate["api_field_id"] == "api_field:amount"
    assert field_candidate["database_field_id"] == "db_field:main.Order:amount"
    assert field_candidate["status"] == "PENDING_STORAGE_FIELD_AUTHORITY"
    assert field_candidate["automatic_mapping_allowed"] is False
    assert field_candidate["write_target_allowed"] is False
    assert field_candidate["oracle_authority_allowed"] is False

    table_edge = next(
        row
        for row in result["relationships"]
        if row.get("relation")
        == "api_operation_database_table_alignment_candidate"
    )
    field_edge = next(
        row
        for row in result["relationships"]
        if row.get("relation")
        == "api_operation_database_field_alignment_candidate"
    )
    assert table_edge["status"] == "pending_authority"
    assert field_edge["status"] == "pending_authority"

    receipt = result["api_operation_database_candidate_projection"]
    assert receipt["schema"] == API_OPERATION_DATABASE_PROJECTION_SCHEMA
    assert receipt["automatic_table_mapping_count"] == 0
    assert receipt["automatic_field_mapping_count"] == 0
    assert receipt["write_target_authority_count"] == 0
    assert receipt["oracle_authority_count"] == 0
    assert receipt["accepted_operation_schema_binding_is_not_storage_authority"] is True

    entity = result["api_database_entity_alignment_candidates"][0]
    field = result["api_database_field_alignment_candidates"][0]
    assert entity["operation_scope_status"] == "REFERENCED_BY_EXACT_API_OPERATION"
    assert field["operation_scope_status"] == "REFERENCED_BY_EXACT_API_OPERATION"
    assert entity["operation_schema_binding_ids"]
    assert field["operation_schema_binding_ids"]


def test_unreferenced_storage_candidates_do_not_become_operation_candidates() -> None:
    asset = {
        "api_operation_schema_bindings": [],
        "api_database_entity_alignment_candidates": [
            {
                "candidate_id": "entity-candidate:unused",
                "api_entity_id": "api_schema_entity:schema:unused",
                "database_table_matches": [{"table_id": "table:main.Unused"}],
            }
        ],
        "api_database_field_alignment_candidates": [],
        "relationships": [],
        "summary": {},
        "governance": {},
    }

    result = enrich_asset_with_api_operation_database_candidates(asset)

    assert result["api_operation_database_table_candidates"] == []
    assert result["api_operation_database_field_candidates"] == []
    assert result["api_operation_database_candidate_projection"]["status"] == (
        "NOT_APPLICABLE"
    )
    assert result["api_database_entity_alignment_candidates"][0][
        "operation_scope_status"
    ] == "NOT_REFERENCED_BY_EXACT_API_OPERATION"
