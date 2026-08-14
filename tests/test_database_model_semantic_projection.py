from __future__ import annotations

import sqlite3

from ai_test_asset_center.enterprise_knowledge_center import composition
from ai_test_asset_center.enterprise_knowledge_center.database_model_asset_projection import (
    DATABASE_MODEL_ASSET_SCHEMA,
    enrich_asset_with_database_model_facts,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    DocumentSource,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.database_model_adapter import (
    DatabaseModelDocumentAdapter,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ir_database_model_semantics import (
    DATABASE_MODEL_SEMANTIC_SCHEMA,
    enrich_parsed_database_model_semantics,
    project_database_model_semantics,
)


def _sqlite_bytes(tmp_path, name: str, ddl: str, data_sql: str = "") -> bytes:
    path = tmp_path / name
    connection = sqlite3.connect(path)
    try:
        connection.executescript(ddl)
        if data_sql:
            connection.executescript(data_sql)
        connection.commit()
    finally:
        connection.close()
    return path.read_bytes()


def _structure(data: bytes, source_id: str, filename: str = "model.sqlite") -> dict:
    return DatabaseModelDocumentAdapter().extract(
        DocumentSource(source_id=source_id, filename=filename, data=data)
    )


def test_database_model_ir_projects_exact_tables_fields_keys_and_indexes(tmp_path) -> None:
    data = _sqlite_bytes(
        tmp_path,
        "orders.sqlite",
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE customer(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE sales_order(
            id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            amount NUMERIC NOT NULL DEFAULT 0,
            FOREIGN KEY(customer_id) REFERENCES customer(id) ON DELETE RESTRICT
        );
        CREATE UNIQUE INDEX ux_sales_order_customer
            ON sales_order(customer_id);
        """,
        """
        INSERT INTO customer(id, name) VALUES (1, 'SECRET_CUSTOMER_ROW');
        INSERT INTO sales_order(id, customer_id, amount) VALUES (10, 1, 99.5);
        """,
    )
    ir = _structure(data, "src_sqlite", "orders.sqlite")

    result = project_database_model_semantics(
        ir,
        source_id="src_sqlite",
        source_type="database_schema",
    )

    assert result["schema"] == DATABASE_MODEL_SEMANTIC_SCHEMA
    assert result["status"] == "COMPLETE"
    assert result["database_rows_read"] == 0
    assert result["table_count"] == 2
    assert result["relationship_count"] == 1
    assert result["index_count"] >= 1
    assert result["exact_fact_rate"] == 1.0

    by_id = {row["table_id"]: row for row in result["tables"]}
    assert set(by_id) == {"table:main.customer", "table:main.sales_order"}
    assert by_id["table:main.customer"]["identity_fields"] == ["id"]
    assert by_id["table:main.sales_order"]["foreign_keys"] == ["main.customer"]

    customer_id = next(
        row
        for row in result["field_dictionary"]
        if row["table_id"] == "table:main.customer" and row["field"] == "id"
    )
    assert customer_id["primary_key"] is True
    assert customer_id["identity"] is True
    assert customer_id["evidence_address"]["address_kind"] == "EXACT_SOURCE_LOCATOR"
    assert "SECRET_CUSTOMER_ROW" not in str(result)

    relationship = result["relationships"][0]
    assert relationship["child_table_id"] == "table:main.sales_order"
    assert relationship["child_columns"] == ["customer_id"]
    assert relationship["parent_table_id"] == "table:main.customer"
    assert relationship["parent_columns"] == ["id"]
    assert relationship["delete_rule"] == "RESTRICT"

    enriched = enrich_parsed_database_model_semantics(
        {
            "tables": [{"table_id": "table:wrong", "name": "wrong"}],
            "field_dictionary": [],
        },
        ir,
        source_id="src_sqlite",
        source_type="database_schema",
    )
    assert {row["table_id"] for row in enriched["tables"]} == set(by_id)
    assert enriched["database_model_semantic_receipt"]["database_rows_read"] == 0


def test_database_model_asset_keeps_source_ledger_and_exposes_conflicts(tmp_path) -> None:
    source_a = _sqlite_bytes(
        tmp_path,
        "orders-a.sqlite",
        """
        CREATE TABLE orders(
            id INTEGER PRIMARY KEY,
            amount NUMERIC NOT NULL
        );
        """,
    )
    source_b = _sqlite_bytes(
        tmp_path,
        "orders-b.sqlite",
        """
        CREATE TABLE orders(
            id INTEGER PRIMARY KEY,
            total NUMERIC NOT NULL
        );
        """,
    )
    asset = {
        "source_inventory": [
            {"source_id": "src_a", "source_type": "database_schema"},
            {"source_id": "src_b", "source_type": "database_schema"},
        ],
        "tables": [],
        "field_dictionary": [],
        "relationships": [],
        "summary": {},
        "governance": {},
    }
    sources = [
        {
            "source_id": "src_a",
            "document_structure": _structure(
                source_a, "src_a", "orders-a.sqlite"
            ),
        },
        {
            "source_id": "src_b",
            "document_structure": _structure(
                source_b, "src_b", "orders-b.sqlite"
            ),
        },
    ]

    result = enrich_asset_with_database_model_facts(asset, sources)

    table = next(
        row for row in result["tables"] if row["table_id"] == "table:main.orders"
    )
    assert table["columns"] == ["amount", "id", "total"]
    assert table["database_model_source_count"] == 2
    assert {row["source_id"] for row in table["database_model_declarations"]} == {
        "src_a",
        "src_b",
    }
    assert len(result["database_model_conflicts"]) == 1
    conflict = result["database_model_conflicts"][0]
    assert conflict["status"] == "UNRESOLVED"
    assert conflict["automatic_winner_selected"] is False
    assert conflict["operator_authority_required"] is True
    assert conflict["source_ids"] == ["src_a", "src_b"]

    receipt = result["database_model_fact_projection"]
    assert receipt["schema"] == DATABASE_MODEL_ASSET_SCHEMA
    assert receipt["status"] == "PARTIAL"
    assert receipt["processed_source_count"] == 2
    assert receipt["conflict_count"] == 1
    assert receipt["database_rows_read"] == 0
    assert receipt["source_scoped_declaration_ledger"] is True
    assert result["governance"]["database_model_conflicts_require_authority"] is True


def test_composition_projects_database_models_before_understanding(
    monkeypatch, tmp_path
) -> None:
    calls: list[str] = []
    base_asset = {
        "source_inventory": [],
        "tables": [],
        "field_dictionary": [],
        "relationships": [],
        "rule_library": [],
        "summary": {},
        "governance": {},
    }

    monkeypatch.setattr(composition, "configure_source_parser_extensions", lambda: None)
    monkeypatch.setattr(
        composition._base_api,
        "build_enterprise_business_knowledge_asset",
        lambda project, root, options, **kwargs: dict(base_asset),
    )
    monkeypatch.setattr(
        composition,
        "_parsed_sources_for_context",
        lambda asset, root, **kwargs: [],
    )
    monkeypatch.setattr(
        composition,
        "enrich_asset_with_api_artifact_semantics",
        lambda asset, sources: calls.append("api") or asset,
    )
    monkeypatch.setattr(
        composition,
        "enrich_asset_with_database_model_facts",
        lambda asset, sources: calls.append("database") or asset,
    )
    monkeypatch.setattr(
        composition,
        "enrich_asset_with_enterprise_understanding",
        lambda asset, *, parsed_sources: calls.append("understanding") or asset,
    )
    monkeypatch.setattr(
        composition._downstream,
        "refresh_chinese_business_downstream",
        lambda asset, max_probe_count: (calls.append("downstream") or asset, []),
    )
    monkeypatch.setattr(
        composition,
        "enrich_job_assets_with_governance",
        lambda asset, **kwargs: calls.append("jobs") or asset,
    )
    monkeypatch.setattr(
        composition,
        "refresh_job_behavior_projection",
        lambda asset: calls.append("behavior") or asset,
    )
    monkeypatch.setattr(composition, "probe_generation_block_reason", lambda asset: "")
    monkeypatch.setattr(composition, "build_gated_probes", lambda *args, **kwargs: [])
    monkeypatch.setattr(composition, "_persist_final", lambda *args, **kwargs: None)

    result = composition.build_enterprise_business_knowledge_asset(
        "database_projection_order",
        tmp_path,
        {"probe_limit": 0},
    )

    assert calls[:3] == ["api", "database", "understanding"]
    # Cognition now runs in two passes over the same compiled fact ledger
    # (provisional first pass, then a re-run after structure-first fact
    # compilation and identity governance). Both passes are the same authority.
    assert calls == [
        "api",
        "database",
        "understanding",
        "understanding",
        "downstream",
        "jobs",
        "behavior",
    ]
    assert result["governance"][
        "database_model_projection_precedes_enterprise_understanding"
    ] is True
