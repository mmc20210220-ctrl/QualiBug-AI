from __future__ import annotations


def test_sandbox_base_uses_shared_table_scoped_unique_authority(tmp_path) -> None:
    import ai_test_asset_center.sandbox_write_executor as sandbox
    from ai_test_asset_center.schema_unique_materialization_authority import (
        TableScopedUniqueFields,
    )

    project = "project-unique"
    schema = tmp_path / "platform_inputs" / project / "schema.sql"
    schema.parent.mkdir(parents=True, exist_ok=True)
    schema.write_text(
        """
CREATE TABLE users (id INTEGER PRIMARY KEY, code TEXT UNIQUE);
CREATE TABLE products (id INTEGER PRIMARY KEY, code TEXT, sku TEXT UNIQUE);
""",
        encoding="utf-8",
    )
    sandbox._base._DECLARED_UNIQUE_FIELDS_CACHE.clear()

    authority = sandbox._base._load_declared_unique_fields(tmp_path, project)

    assert isinstance(authority, TableScopedUniqueFields)
    assert authority.by_table == {
        "users": {"code"},
        "products": {"sku"},
    }


def test_sandbox_materializer_never_uses_another_tables_unique_field() -> None:
    import ai_test_asset_center.sandbox_write_executor as sandbox
    from ai_test_asset_center.schema_unique_materialization_authority import (
        TableScopedUniqueFields,
    )

    body = {"code": "PRODUCT-CODE", "sku": "SKU-1"}
    rendered, changed = sandbox._base.materialize_unique_create_fields(
        body,
        "nonce",
        TableScopedUniqueFields(
            {"users": {"code"}, "products": {"sku"}}
        ),
        table_hint="products",
        schema_tables={"users", "products"},
    )

    assert rendered["code"] == "PRODUCT-CODE"
    assert rendered["sku"] == "SKU-1-nonce"
    assert changed == ["sku"]
