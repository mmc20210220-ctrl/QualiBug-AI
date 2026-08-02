from __future__ import annotations

import pytest

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.enterprise_knowledge_center import (
    build_runtime_source_knowledge_overlay,
    merge_knowledge_asset_overlay,
)
from ai_test_asset_center.obligation_compiler import (
    compile_obligations_from_behavior_ir,
)


@pytest.mark.parametrize(
    ("project", "resource", "api_text", "schema_text"),
    [
        (
            "asset-lifecycle",
            "assets",
            """
### PATCH /assets/{id}
Asset state must follow the declared transition rule.
""",
            "CREATE TABLE assets (id TEXT PRIMARY KEY, state TEXT NOT NULL);",
        ),
        (
            "support-lifecycle",
            "cases",
            """
### POST /cases/{id}/close
Case status must follow the declared transition rule.
""",
            "CREATE TABLE cases (id TEXT PRIMARY KEY, status TEXT NOT NULL);",
        ),
    ],
)
def test_runtime_sources_use_one_domain_neutral_ir_and_obligation_path(
    project: str,
    resource: str,
    api_text: str,
    schema_text: str,
) -> None:
    overlay = build_runtime_source_knowledge_overlay(
        prd_text=f"# {resource} requirements\nThe operator must review every state change.",
        api_spec_text=api_text,
        db_schema_text=schema_text,
    )
    asset = merge_knowledge_asset_overlay({}, overlay)

    behavior_ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id=project,
        runtime_actors=[{
            "role": "operator",
            "account_ref": "operator-a",
            "secret_ref": "secret_ref:test_accounts:operator-a",
        }],
    )
    obligations = compile_obligations_from_behavior_ir(behavior_ir)

    assert any(entity.get("name") == resource for entity in behavior_ir["entities"])
    assert any(
        relation.get("source_relationship_ref")
        and relation.get("operation_ref")
        for relation in behavior_ir["relations"]
    )
    assert obligations["obligation_count"] > 0
    assert {source.get("source_type") for source in overlay["source_inventory"]} == {
        "prd",
        "markdown_api",
        "database_schema",
    }
    assert "ground_truth" not in str(overlay).lower()


def test_unbound_runtime_rule_is_preserved_as_stable_coverage_gap() -> None:
    overlay = build_runtime_source_knowledge_overlay(
        prd_text="A reviewer must approve every published record.",
        api_spec_text="### GET /records\nList records.",
        db_schema_text="CREATE TABLE records (id TEXT PRIMARY KEY);",
    )
    behavior_ir = build_behavior_ir_from_knowledge_asset(
        merge_knowledge_asset_overlay({}, overlay),
        project_id="unbound-rule",
    )

    gap = next(
        row
        for row in behavior_ir["coverage_gaps"]
        if row.get("gap_type") == "source_invariant_operation_unbound"
    )
    assert gap["invariant_ref"]
    assert gap["source_rule_refs"]


def test_runtime_ddl_columns_union_weak_inventory_same_table_id() -> None:
    """Weak inventory must not erase runtime DDL columns for the same table_id.

    Industry-neutral: no path/entity special cases. Register-style ops bind via
    request-field ↔ entity-column overlap once DDL columns reach the IR.
    """
    from ai_test_asset_center.experiment_compiler_obligation_core import (
        _entity_for_operation,
    )

    weak_asset = {
        "data_tables": [
            {
                "table_id": "table:accounts",
                "name": "accounts",
                "columns": ["role"],
                "identity_fields": [],
                "field_dictionary": [
                    {"field_id": "field:inv:role", "table": "accounts", "field": "role"}
                ],
                "derivation": "entity_inventory_table",
                "source_id": "src:inventory",
            }
        ],
        "field_dictionary": [
            {"field_id": "field:inv:role", "table": "accounts", "field": "role"}
        ],
    }
    overlay = build_runtime_source_knowledge_overlay(
        db_schema_text=(
            "CREATE TABLE accounts ("
            "id UUID PRIMARY KEY, "
            "email TEXT UNIQUE NOT NULL, "
            "name TEXT NOT NULL, "
            "phone TEXT, "
            "role TEXT NOT NULL, "
            "password TEXT NOT NULL"
            ");"
        ),
    )
    merged = merge_knowledge_asset_overlay(weak_asset, overlay)
    accounts = next(
        row
        for row in merged["data_tables"]
        if str(row.get("name") or "").lower() == "accounts"
    )
    columns = {str(value).lower() for value in accounts.get("columns") or []}
    assert {"id", "email", "name", "phone", "role", "password"} <= columns
    assert "role" in columns

    behavior_ir = build_behavior_ir_from_knowledge_asset(
        merged,
        project_id="ddl-column-union",
        api_operations=[
            {
                "method": "POST",
                "path": "/api/auth/register",
                "operation_id": "register",
                "request_example": {
                    "email": "a@example.com",
                    "name": "Ada",
                    "password": "secret",
                    "phone": "10086",
                },
            }
        ],
    )
    entity = next(
        row
        for row in behavior_ir["entities"]
        if str(row.get("name") or "").lower() == "accounts"
    )
    field_names = {
        str(field.get("name") or field.get("field") or "").lower()
        for field in entity.get("fields") or []
        if isinstance(field, dict)
    }
    assert {"email", "name", "phone"}.issubset(field_names)

    register_op = next(
        row
        for row in behavior_ir["operations"]
        if str(row.get("path") or "") == "/api/auth/register"
    )
    bound = _entity_for_operation(register_op, behavior_ir)
    assert bound
    assert str(bound.get("name") or bound.get("table") or "").lower() in {
        "accounts",
        "table:accounts",
    }


def test_interface_merge_keeps_overlay_request_schema_over_stub() -> None:
    """Persisted interface stubs must not erase markdown field-table schemas."""
    from ai_test_asset_center.universal_api_parser import build_api_operations_from_text

    api_text = """
### POST /api/catalog/items

Create a catalog item.

| field | type | required | description |
| --- | --- | --- | --- |
| sku | string | yes | unique item code |
| name | string | no | display name |
"""
    overlay = build_runtime_source_knowledge_overlay(api_spec_text=api_text)
    stub_asset = {
        "interfaces": [
            {
                "interface_id": "markdown_api:POST:/api/catalog/items",
                "method": "POST",
                "path": "/api/catalog/items",
                "request_schema": None,
                "request_example": None,
            }
        ]
    }
    merged = merge_knowledge_asset_overlay(stub_asset, overlay)
    iface = next(
        row
        for row in merged["interfaces"]
        if row.get("interface_id") == "markdown_api:POST:/api/catalog/items"
    )
    schema = iface.get("request_schema") or {}
    assert schema.get("required") == ["sku"]
    assert "sku" in dict(schema.get("properties") or {})

    ops = build_api_operations_from_text(api_text)
    op = next(
        row
        for row in ops
        if str(row.get("method")).upper() == "POST"
        and str(row.get("path")) == "/api/catalog/items"
    )
    assert (op.get("request_schema") or {}).get("required") == ["sku"]
