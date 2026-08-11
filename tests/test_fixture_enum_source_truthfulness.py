from __future__ import annotations


def test_fixture_enum_conflict_is_reported_without_rewriting_source_value() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _preserve_source_enum_conflicts,
    )

    body = {"status": "ACTIVE", "name": "source-name"}
    rendered, conflicts = _preserve_source_enum_conflicts(
        body,
        {"products": {"status": ["DRAFT", "ON_SALE"]}},
        table_hint="products",
    )

    assert rendered == body
    assert rendered is not body
    assert rendered["status"] == "ACTIVE"
    assert conflicts == ["status"]


def test_declared_legal_enum_value_is_left_unchanged() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _preserve_source_enum_conflicts,
    )

    rendered, conflicts = _preserve_source_enum_conflicts(
        {"status": "ON_SALE"},
        {"products": {"status": ["DRAFT", "ON_SALE"]}},
        table_hint="products",
    )

    assert rendered == {"status": "ON_SALE"}
    assert conflicts == []
