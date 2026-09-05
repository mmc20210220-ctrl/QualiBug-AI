from __future__ import annotations

import pytest

from ai_test_asset_center.private_pilot_project_assets import resolve_knowledge_source_type


def test_resolve_knowledge_source_type_detects_openapi_without_user_type() -> None:
    source_type, resolution = resolve_knowledge_source_type(
        "customer-api.yaml",
        'openapi: "3.0.0"\npaths:\n  /orders:\n    get:\n      responses: {}\n',
    )

    assert source_type == "openapi"
    assert resolution == "automatic"


def test_structured_openapi_identity_outranks_business_rule_prose() -> None:
    source_type, resolution = resolve_knowledge_source_type(
        "openapi.yaml",
        "openapi: 3.0.3\ninfo:\n  description: 业务规则和角色权限优先于实现。\npaths:\n  /orders:\n    get:\n      responses: {}\n",
    )

    assert source_type == "openapi"
    assert resolution == "automatic"


def test_resolve_knowledge_source_type_detects_database_schema() -> None:
    source_type, resolution = resolve_knowledge_source_type(
        "production_schema.sql",
        "CREATE TABLE orders (id bigint primary key, status varchar(32));",
    )

    assert source_type == "database_schema"
    assert resolution == "automatic"


def test_resolve_knowledge_source_type_falls_back_without_claiming_prd() -> None:
    source_type, resolution = resolve_knowledge_source_type(
        "meeting-notes.txt",
        "本周讨论了上线安排和后续协作事项。",
    )

    assert source_type == "collaboration_document"
    assert resolution == "automatic"


def test_resolve_knowledge_source_type_keeps_exception_override_compatible() -> None:
    source_type, resolution = resolve_knowledge_source_type(
        "legacy-contract.json",
        "{}",
        "swagger",
    )

    assert source_type == "openapi"
    assert resolution == "explicit_override"


def test_resolve_knowledge_source_type_rejects_unknown_override() -> None:
    with pytest.raises(ValueError, match="unsupported knowledge source type"):
        resolve_knowledge_source_type("notes.txt", "text", "customer_choice")
