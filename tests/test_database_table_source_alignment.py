from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.database_table_source_alignment import (
    DATABASE_TABLE_ALIGNMENT_SCHEMA,
    enrich_asset_with_database_table_alignment_candidates,
)


def _model_table(schema: str, name: str, columns: list[str], source_id: str) -> dict:
    qualified = f"{schema}.{name}"
    return {
        "table_id": f"table:{qualified}",
        "source_id": source_id,
        "source_refs": [source_id],
        "name": name,
        "schema_name": schema,
        "qualified_name": qualified,
        "columns": columns,
        "identity_fields": ["id"] if "id" in columns else [],
        "database_model_declarations": [
            {
                "declaration_id": f"declaration:{source_id}:{qualified}",
                "source_id": source_id,
                "qualified_name": qualified,
                "source_locator": f"{source_id}.pdm#table={qualified}",
            }
        ],
        "derivation": "database_model_document_ir",
    }


def _sql_table(name: str, columns: list[str], source_id: str = "src_sql") -> dict:
    return {
        "table_id": f"table:{name}",
        "source_id": source_id,
        "name": name,
        "columns": columns,
        "identity_fields": ["id"] if "id" in columns else [],
        "derivation": "sql_ddl_parser",
    }


def test_unqualified_sql_table_creates_candidate_without_automatic_merge() -> None:
    sql = _sql_table("orders", ["id", "amount"])
    model = _model_table("main", "orders", ["id", "amount", "status"], "src_model")
    asset = {
        "tables": [sql, model],
        "relationships": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }

    result = enrich_asset_with_database_table_alignment_candidates(asset)

    # Physical identities remain separate until schema/source authority is supplied.
    assert {row["table_id"] for row in result["tables"]} == {
        "table:orders",
        "table:main.orders",
    }
    candidate = result["database_table_alignment_candidates"][0]
    assert candidate["schema"] == DATABASE_TABLE_ALIGNMENT_SCHEMA
    assert candidate["source_table_id"] == "table:orders"
    assert candidate["status"] == "PENDING_SCHEMA_OR_SOURCE_AUTHORITY"
    assert candidate["automatic_merge_allowed"] is False
    assert candidate["automatic_winner_selected"] is False
    assert candidate["matches"][0]["target_table_id"] == "table:main.orders"
    overlap = candidate["matches"][0]["column_overlap"]
    assert overlap["shared"] == ["amount", "id"]
    assert overlap["left_subset_of_right"] is True
    assert candidate["field_overlap_is_supporting_not_identity_authority"] is True

    edges = [
        row
        for row in result["relationships"]
        if row.get("relation") == "database_table_alignment_candidate"
    ]
    assert len(edges) == 1
    assert edges[0]["status"] == "pending_authority"
    assert edges[0]["evidence"]["automatic_merge_allowed"] is False
    receipt = result["database_table_source_alignment"]
    assert receipt["candidate_count"] == 1
    assert receipt["automatic_merge_count"] == 0
    assert receipt["schema_omission_blocks_automatic_identity"] is True


def test_unqualified_table_matching_multiple_schemas_is_fail_visible() -> None:
    asset = {
        "tables": [
            _sql_table("orders", ["id", "amount"]),
            _model_table("tenant_a", "orders", ["id", "amount"], "src_a"),
            _model_table("tenant_b", "orders", ["id", "amount"], "src_b"),
        ],
        "relationships": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }

    result = enrich_asset_with_database_table_alignment_candidates(asset)

    candidate = result["database_table_alignment_candidates"][0]
    assert candidate["status"] == "AMBIGUOUS_REQUIRES_AUTHORITY"
    assert [row["target_table_id"] for row in candidate["matches"]] == [
        "table:tenant_a.orders",
        "table:tenant_b.orders",
    ]
    assert candidate["operator_authority_required"] is True
    assert len(result["database_table_alignment_gaps"]) == 1
    gap = result["database_table_alignment_gaps"][0]
    assert gap["kind"] == "DATABASE_TABLE_ALIGNMENT_AMBIGUOUS"
    assert gap["candidate_table_ids"] == [
        "table:tenant_a.orders",
        "table:tenant_b.orders",
    ]
    assert result["database_table_source_alignment"][
        "ambiguous_candidate_count"
    ] == 1
    assert result["governance"][
        "database_table_alignment_requires_schema_or_source_authority"
    ] is True


def test_similar_or_case_changed_names_do_not_create_alignment_candidates() -> None:
    asset = {
        "tables": [
            _sql_table("sales_orders", ["id", "amount"]),
            _sql_table("Orders", ["id", "amount"], "src_sql_case"),
            _model_table("main", "orders", ["id", "amount"], "src_model"),
        ],
        "relationships": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }

    result = enrich_asset_with_database_table_alignment_candidates(asset)

    assert result["database_table_alignment_candidates"] == []
    receipt = result["database_table_source_alignment"]
    assert receipt["candidate_count"] == 0
    assert receipt["exact_name_only"] is True
    assert result["governance"][
        "database_table_alignment_uses_no_vocabulary_inference"
    ] is True
