from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.api_database_contract_alignment import (
    API_DATABASE_ALIGNMENT_SCHEMA,
    enrich_asset_with_api_database_alignment_candidates,
)


def _api_entity(name: str, schema_id: str = "schema:order") -> dict:
    return {
        "entity_id": f"api_schema_entity:{schema_id}",
        "schema_id": schema_id,
        "source_id": "src_api",
        "name": name,
    }


def _api_field(
    name: str,
    declared_type: str,
    *,
    schema_id: str = "schema:order",
) -> dict:
    return {
        "field_fact_id": f"api_field:{schema_id}:{name}",
        "schema_id": schema_id,
        "source_id": "src_api",
        "field_name": name,
        "property_path": [name],
        "type": declared_type,
        "direction": "component",
        "source_locator": f"orders.json#/components/schemas/Order/properties/{name}",
    }


def _database_table(schema: str, name: str, source_id: str) -> dict:
    qualified = f"{schema}.{name}"
    return {
        "table_id": f"table:{qualified}",
        "source_id": source_id,
        "source_refs": [source_id],
        "schema_name": schema,
        "name": name,
        "qualified_name": qualified,
        "database_model_declarations": [
            {
                "declaration_id": f"decl:{source_id}:{qualified}",
                "source_id": source_id,
                "source_locator": f"{source_id}.pdm#table={qualified}",
            }
        ],
        "derivation": "database_model_document_ir",
        "source_locator": f"{source_id}.pdm#table={qualified}",
    }


def _database_field(
    table_id: str,
    name: str,
    declared_type: str,
    *,
    source_id: str = "src_db",
) -> dict:
    return {
        "field_id": f"db_field:{table_id}:{name}",
        "table_id": table_id,
        "source_id": source_id,
        "field": name,
        "type": declared_type,
        "evidence_kind": "SOURCE_DECLARED_DATABASE_MODEL",
        "derivation": "database_model_document_ir",
        "source_locator": f"{source_id}.pdm#{table_id};column={name}",
    }


def test_field_comparison_is_scoped_by_entity_candidate_not_global_name() -> None:
    order = _database_table("main", "Order", "src_order_db")
    customer = _database_table("main", "Customer", "src_customer_db")
    asset = {
        "openapi_schema_entities": [_api_entity("Order")],
        "openapi_schema_fields": [
            _api_field("id", "integer"),
            _api_field("amount", "number"),
        ],
        "tables": [order, customer],
        "field_dictionary": [
            _database_field(order["table_id"], "id", "BIGINT", source_id="src_order_db"),
            _database_field(
                order["table_id"], "amount", "NUMERIC(12,2)", source_id="src_order_db"
            ),
            _database_field(
                customer["table_id"], "id", "BIGINT", source_id="src_customer_db"
            ),
        ],
        "relationships": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }

    result = enrich_asset_with_api_database_alignment_candidates(asset)

    entity_candidates = result["api_database_entity_alignment_candidates"]
    assert len(entity_candidates) == 1
    assert entity_candidates[0]["database_table_matches"][0]["table_id"] == (
        "table:main.Order"
    )
    assert entity_candidates[0]["automatic_merge_allowed"] is False

    field_candidates = result["api_database_field_alignment_candidates"]
    assert {
        (row["api_field_name"], row["database_table_id"])
        for row in field_candidates
    } == {
        ("id", "table:main.Order"),
        ("amount", "table:main.Order"),
    }
    assert all(row["automatic_mapping_allowed"] is False for row in field_candidates)
    assert all(
        row["comparison_scoped_by_entity_candidate"] is True
        for row in field_candidates
    )
    assert all(
        row["type_compatibility"]["status"] == "COMPATIBLE"
        for row in field_candidates
    )
    assert not any(
        row["database_table_id"] == "table:main.Customer"
        for row in field_candidates
    )

    receipt = result["api_database_contract_alignment"]
    assert receipt["schema"] == API_DATABASE_ALIGNMENT_SCHEMA
    assert receipt["automatic_entity_mapping_count"] == 0
    assert receipt["automatic_field_mapping_count"] == 0
    assert receipt["field_comparison_requires_entity_candidate"] is True


def test_same_table_name_across_schemas_is_ambiguous_and_never_auto_selected() -> None:
    tenant_a = _database_table("tenant_a", "Order", "src_a")
    tenant_b = _database_table("tenant_b", "Order", "src_b")
    asset = {
        "openapi_schema_entities": [_api_entity("Order")],
        "openapi_schema_fields": [_api_field("id", "integer")],
        "tables": [tenant_a, tenant_b],
        "field_dictionary": [
            _database_field(tenant_a["table_id"], "id", "BIGINT", source_id="src_a"),
            _database_field(tenant_b["table_id"], "id", "BIGINT", source_id="src_b"),
        ],
        "relationships": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }

    result = enrich_asset_with_api_database_alignment_candidates(asset)

    candidate = result["api_database_entity_alignment_candidates"][0]
    assert candidate["status"] == "AMBIGUOUS_REQUIRES_AUTHORITY"
    assert candidate["automatic_winner_selected"] is False
    assert {
        row["table_id"] for row in candidate["database_table_matches"]
    } == {"table:tenant_a.Order", "table:tenant_b.Order"}
    assert result["api_database_contract_alignment"][
        "ambiguous_entity_candidate_count"
    ] == 1
    gap = next(
        row
        for row in result["coverage_gaps"]
        if row.get("kind") == "API_SCHEMA_DATABASE_TABLE_ALIGNMENT_AMBIGUOUS"
    )
    assert set(gap["candidate_table_ids"]) == {
        "table:tenant_a.Order",
        "table:tenant_b.Order",
    }


def test_similar_plural_or_case_changed_names_do_not_create_candidates() -> None:
    asset = {
        "openapi_schema_entities": [
            _api_entity("OrderDto"),
            _api_entity("ORDER", "schema:upper"),
        ],
        "openapi_schema_fields": [
            _api_field("id", "integer"),
            _api_field("id", "integer", schema_id="schema:upper"),
        ],
        "tables": [_database_table("main", "orders", "src_db")],
        "field_dictionary": [
            _database_field("table:main.orders", "id", "BIGINT")
        ],
        "relationships": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }

    result = enrich_asset_with_api_database_alignment_candidates(asset)

    assert result["api_database_entity_alignment_candidates"] == []
    assert result["api_database_field_alignment_candidates"] == []
    receipt = result["api_database_contract_alignment"]
    assert receipt["exact_case_sensitive_names_only"] is True
    assert receipt["camel_snake_plural_inference_used"] is False


def test_type_conflict_is_fail_visible_but_not_auto_resolved() -> None:
    order = _database_table("main", "Order", "src_db")
    asset = {
        "openapi_schema_entities": [_api_entity("Order")],
        "openapi_schema_fields": [_api_field("amount", "string")],
        "tables": [order],
        "field_dictionary": [
            _database_field(order["table_id"], "amount", "NUMERIC(12,2)")
        ],
        "relationships": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }

    result = enrich_asset_with_api_database_alignment_candidates(asset)

    candidate = result["api_database_field_alignment_candidates"][0]
    assert candidate["status"] == "TYPE_CONFLICT_REQUIRES_AUTHORITY"
    assert candidate["type_compatibility"]["status"] == "INCOMPATIBLE"
    assert candidate["automatic_mapping_allowed"] is False
    assert result["api_database_contract_alignment"][
        "type_conflict_candidate_count"
    ] == 1
    gap = next(
        row
        for row in result["coverage_gaps"]
        if row.get("kind") == "API_DATABASE_FIELD_TYPE_CONFLICT_CANDIDATE"
    )
    assert gap["blocks_automatic_mapping"] is True
