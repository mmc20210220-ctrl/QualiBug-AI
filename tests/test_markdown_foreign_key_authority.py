from __future__ import annotations


def test_id_shaped_field_name_does_not_declare_foreign_key() -> None:
    from ai_test_asset_center.enterprise_knowledge_center._parsing import (
        _markdown_api_operations,
    )

    text = """
POST /api/orders

| 字段 | 类型 | 必填 |
| --- | --- | --- |
| user_id | string | 否 |
| code | string | 否 |
| title | string | 是 |
"""
    rows = _markdown_api_operations(text, "source-md")

    assert len(rows) == 1
    schema = rows[0]["request_schema"]
    assert schema["required"] == ["title"]
    assert set(schema["properties"]) == {"title"}
    assert all(
        prop.get("x-foreign-key") is not True
        for prop in schema["properties"].values()
    )


def test_explicit_markdown_foreign_key_is_preserved() -> None:
    from ai_test_asset_center.enterprise_knowledge_center._parsing import (
        _markdown_api_operations,
    )

    text = """
POST /api/orders

| 字段 | 类型 | 必填 | 外键 |
| --- | --- | --- | --- |
| user_id | string | 否 | 是 |
| title | string | 是 | 否 |
"""
    rows = _markdown_api_operations(text, "source-md")

    schema = rows[0]["request_schema"]
    assert schema["properties"]["user_id"]["x-foreign-key"] is True
    assert schema["properties"]["title"].get("x-foreign-key") is not True


def test_parse_source_uses_governed_markdown_relationship_parser() -> None:
    from ai_test_asset_center.enterprise_knowledge_center._parsing import _parse_source

    text = """
POST /api/payments

| field | type | required |
| --- | --- | --- |
| order_id | string | no |
| amount | number | yes |
"""
    parsed = _parse_source(
        text.encode("utf-8"),
        "api.md",
        "markdown_api",
        "source-api",
    )

    operation = parsed["operations"][0]
    properties = operation["request_schema"]["properties"]
    assert set(properties) == {"amount"}
    assert "order_id" not in properties
