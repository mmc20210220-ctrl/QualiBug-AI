from __future__ import annotations


def _schema() -> str:
    return """
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    code TEXT UNIQUE
);
CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    code TEXT,
    sku TEXT UNIQUE
);
"""


def test_unique_authority_preserves_owning_table() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _declared_unique_fields_scoped,
    )

    authority = _declared_unique_fields_scoped(_schema())

    assert authority.by_table == {
        "users": {"code"},
        "products": {"sku"},
    }
    # The set surface remains compatible with the historical core truthiness check.
    assert set(authority) == {"code", "sku"}


def test_products_fixture_does_not_rewrite_users_unique_code() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _declared_unique_fields_scoped,
        _materialize_unique_create_fields_scoped,
    )

    authority = _declared_unique_fields_scoped(_schema())
    body = {"code": "PRODUCT-CODE", "sku": "SKU-1"}

    rendered, changed = _materialize_unique_create_fields_scoped(
        body,
        "nonce",
        authority,
        table_hint="products",
        schema_tables={"users", "products"},
    )

    assert rendered["code"] == "PRODUCT-CODE"
    assert rendered["sku"] == "SKU-1-nonce"
    assert changed == ["sku"]
    assert body == {"code": "PRODUCT-CODE", "sku": "SKU-1"}


def test_version_hint_can_resolve_table_only_from_unique_source_structure() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _declared_unique_fields_scoped,
        _materialize_unique_create_fields_scoped,
    )

    authority = _declared_unique_fields_scoped(_schema())
    rendered, changed = _materialize_unique_create_fields_scoped(
        {"sku": "SKU-1", "title": "Widget"},
        "nonce",
        authority,
        table_hint="v1",
        schema_tables={"users", "products"},
    )

    assert rendered["sku"] == "SKU-1-nonce"
    assert changed == ["sku"]


def test_ambiguous_table_identity_does_not_rewrite_any_business_value() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _TableScopedUniqueFields,
        _materialize_unique_create_fields_scoped,
    )

    authority = _TableScopedUniqueFields(
        {
            "users": {"code"},
            "products": {"code"},
        }
    )
    body = {"code": "SAME-FIELD"}

    rendered, changed = _materialize_unique_create_fields_scoped(
        body,
        "nonce",
        authority,
        table_hint="v1",
        schema_tables={"users", "products"},
    )

    assert rendered == body
    assert rendered is not body
    assert changed == []


def test_unique_index_keeps_its_table_identity() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _declared_unique_fields_scoped,
    )

    authority = _declared_unique_fields_scoped(
        """
CREATE TABLE widgets (id INTEGER PRIMARY KEY, slug TEXT);
CREATE UNIQUE INDEX uq_widgets_slug ON widgets(slug);
CREATE TABLE users (id INTEGER PRIMARY KEY, slug TEXT);
"""
    )

    assert authority.by_table == {"widgets": {"slug"}}
