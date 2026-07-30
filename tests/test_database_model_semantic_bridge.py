from __future__ import annotations

import sqlite3

from ai_test_asset_center.enterprise_knowledge_center import _crud
from ai_test_asset_center.enterprise_knowledge_center.database_model_index_reconciliation import (
    DATABASE_MODEL_INDEX_RECONCILIATION_SCHEMA,
    reconcile_database_model_index_assets,
)
from ai_test_asset_center.enterprise_knowledge_center.database_model_semantic_bridge import (
    install_database_model_semantic_bridge,
)


def _sqlite_bytes(tmp_path) -> bytes:
    path = tmp_path / "bridge.sqlite"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE customer(
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE sales_order(
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                FOREIGN KEY(customer_id) REFERENCES customer(id)
            );
            CREATE INDEX ix_sales_order_customer
                ON sales_order(customer_id);
            INSERT INTO customer(id, name) VALUES (1, 'PRIVATE_ROW_VALUE');
            """
        )
        connection.commit()
    finally:
        connection.close()
    return path.read_bytes()


def test_explicit_bridge_replaces_generic_database_guess_on_first_crud_parse(
    tmp_path,
) -> None:
    install_database_model_semantic_bridge()

    parsed = _crud.parse_enterprise_source(
        _sqlite_bytes(tmp_path),
        "bridge.sqlite",
        "database_schema",
        "src_bridge",
    )

    assert getattr(
        _crud.parse_enterprise_source,
        "_qualibug_database_model_semantic_bridge",
        False,
    ) is True
    assert {row["table_id"] for row in parsed["tables"]} == {
        "table:main.customer",
        "table:main.sales_order",
    }
    assert parsed["database_model_semantic_receipt"]["database_rows_read"] == 0
    assert parsed["parser_receipt"]["database_model_semantics_use_document_ir"] is True
    assert parsed["parser_receipt"][
        "database_model_generic_markdown_guess_replaced"
    ] is True
    assert "PRIVATE_ROW_VALUE" not in str(parsed)

    index = next(
        row
        for row in parsed["database_model_indexes"]
        if row["name"] == "ix_sales_order_customer"
    )
    assert index["table_id"] == "table:main.sales_order"
    assert index["schema_name"] == "main"
    assert index["table_resolution_status"] == "RESOLVED_UNIQUE_TABLE_NAME"
    reconciliation = parsed["database_model_index_reconciliation"]
    assert reconciliation["schema"] == DATABASE_MODEL_INDEX_RECONCILIATION_SCHEMA
    assert reconciliation["resolved_schema_less_index_count"] >= 1


def test_schema_less_index_is_not_attached_when_table_name_is_ambiguous() -> None:
    asset = {
        "tables": [
            {
                "table_id": "table:tenant_a.orders",
                "name": "orders",
                "schema_name": "tenant_a",
                "qualified_name": "tenant_a.orders",
            },
            {
                "table_id": "table:tenant_b.orders",
                "name": "orders",
                "schema_name": "tenant_b",
                "qualified_name": "tenant_b.orders",
            },
        ],
        "database_model_indexes": [
            {
                "index_id": "index:orders_status",
                "name": "ix_orders_status",
                "table_name": "orders",
                "schema_name": "",
                "table_id": "table:orders",
                "columns": ["status"],
            }
        ],
        "database_model_fact_projection": {"status": "COMPLETE"},
        "governance": {},
    }

    result = reconcile_database_model_index_assets(asset)

    index = result["database_model_indexes"][0]
    assert index["table_id"] == "table:orders"
    assert index["table_resolution_status"] == "AMBIGUOUS_TABLE_NAME"
    assert index["table_resolution_candidates"] == [
        "table:tenant_a.orders",
        "table:tenant_b.orders",
    ]
    assert index["operator_authority_required"] is True
    receipt = result["database_model_index_reconciliation"]
    assert receipt["ambiguous_index_count"] == 1
    assert receipt["automatic_ambiguous_winner_selected"] is False
    assert result["database_model_fact_projection"]["status"] == "PARTIAL"
