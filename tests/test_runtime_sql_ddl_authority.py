from ai_test_asset_center.enterprise_knowledge_center.runtime_sql_ddl_authority import parse_runtime_sql_ddl


def test_runtime_sql_ddl_projects_only_explicit_foreign_keys() -> None:
    ddl = """
    CREATE TABLE orders (id UUID PRIMARY KEY, order_no TEXT UNIQUE);
    CREATE TABLE payments (
      id UUID PRIMARY KEY,
      order_id UUID NOT NULL REFERENCES orders(id),
      note TEXT
    );
    CREATE TABLE inventory_locks (id UUID PRIMARY KEY, order_id UUID NOT NULL);
    CREATE INDEX idx_payments_order ON payments(order_id);
    """
    result = parse_runtime_sql_ddl(ddl, source_id="src_db")
    assert len(result["tables"]) == 3
    assert len(result["database_model_relationships"]) == 1
    relation = result["database_model_relationships"][0]
    assert relation["child_table"] == "payments"
    assert relation["child_columns"] == ["order_id"]
    assert relation["parent_table"] == "orders"
    assert relation["parent_columns"] == ["id"]
    assert relation["contract_authority"] == "DATABASE_MODEL_SOURCE_DECLARATION"
    assert relation["evidence_address"]["exact"] is True
    locks = next(row for row in result["tables"] if row["name"] == "inventory_locks")
    assert "order_id" in locks["columns"]
    assert not any(row["child_table"] == "inventory_locks" for row in result["database_model_relationships"])
    assert result["database_model"]["database_rows_read"] == 0
