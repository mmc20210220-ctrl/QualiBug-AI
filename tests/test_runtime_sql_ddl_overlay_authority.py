from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._api import (
    build_runtime_source_knowledge_overlay,
    merge_knowledge_asset_overlay,
)


def test_runtime_sql_ddl_overlay_preserves_exact_fk_and_rejects_name_only_relation() -> None:
    overlay = build_runtime_source_knowledge_overlay(
        db_schema_text="""
        CREATE TABLE orders (
          id UUID PRIMARY KEY,
          order_no TEXT UNIQUE NOT NULL
        );
        CREATE TABLE payments (
          id UUID PRIMARY KEY,
          order_id UUID NOT NULL REFERENCES orders(id),
          amount NUMERIC(12,2) NOT NULL
        );
        CREATE TABLE inventory_locks (
          id UUID PRIMARY KEY,
          order_id UUID
        );
        CREATE INDEX idx_payments_order ON payments(order_id);
        """,
    )
    relations = overlay["database_model_relationships"]
    assert len(relations) == 1
    relation = relations[0]
    assert relation["child_table"] == "payments"
    assert relation["child_columns"] == ["order_id"]
    assert relation["parent_table"] == "orders"
    assert relation["parent_columns"] == ["id"]
    assert relation["contract_authority"] == "DATABASE_MODEL_SOURCE_DECLARATION"
    assert relation["evidence_address"]["exact"] is True
    assert len(overlay["database_model_indexes"]) == 1
    assert not any(
        row.get("child_table") == "inventory_locks"
        and row.get("child_columns") == ["order_id"]
        for row in relations
    )

    merged = merge_knowledge_asset_overlay({}, overlay)
    assert merged["database_model_relationships"] == relations
    assert merged["database_model_indexes"] == overlay["database_model_indexes"]
