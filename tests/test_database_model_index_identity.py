from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.database_model_index_reconciliation import (
    reconcile_database_model_index_assets,
)


def _field(table_id: str, name: str) -> dict:
    return {
        "field_id": f"field:{table_id}:{name}",
        "table_id": table_id,
        "field": name,
        "identity": False,
        "unique": False,
        "constraints": {},
    }


def test_single_column_unique_index_promotes_exact_field_identity() -> None:
    table_id = "table:main.customer"
    fields = [_field(table_id, "id"), _field(table_id, "external_code")]
    asset = {
        "tables": [
            {
                "table_id": table_id,
                "name": "customer",
                "schema_name": "main",
                "qualified_name": "main.customer",
                "identity_fields": ["id"],
                "field_dictionary": [dict(row) for row in fields],
            }
        ],
        "field_dictionary": [dict(row) for row in fields],
        "database_model_indexes": [
            {
                "index_id": "index:customer_external_code",
                "name": "ux_customer_external_code",
                "table_name": "customer",
                "schema_name": "",
                "table_id": "table:customer",
                "columns": ["external_code"],
                "unique": True,
                "source_id": "src_db",
                "source_locator": "model.sqlite#index=ux_customer_external_code",
            }
        ],
        "governance": {},
    }

    result = reconcile_database_model_index_assets(asset)

    table = result["tables"][0]
    assert table["identity_fields"] == ["external_code", "id"]
    assert table["identity_keys"][0]["columns"] == ["external_code"]
    assert table["identity_keys"][0]["composite"] is False
    field = next(
        row
        for row in result["field_dictionary"]
        if row["field"] == "external_code"
    )
    assert field["identity"] is True
    assert field["unique"] is True
    assert field["constraints"]["unique"] is True
    assert field["identity_component"] is True
    assert result["database_model_index_reconciliation"][
        "single_column_identity_key_count"
    ] == 1


def test_composite_unique_index_does_not_claim_each_field_is_unique() -> None:
    table_id = "table:main.tenant_order"
    fields = [_field(table_id, "tenant_id"), _field(table_id, "order_no")]
    asset = {
        "tables": [
            {
                "table_id": table_id,
                "name": "tenant_order",
                "schema_name": "main",
                "qualified_name": "main.tenant_order",
                "identity_fields": [],
                "field_dictionary": [dict(row) for row in fields],
            }
        ],
        "field_dictionary": [dict(row) for row in fields],
        "database_model_indexes": [
            {
                "index_id": "index:tenant_order_business_key",
                "name": "ux_tenant_order_business_key",
                "table_name": "tenant_order",
                "schema_name": "main",
                "table_id": table_id,
                "columns": ["tenant_id", "order_no"],
                "unique": True,
                "source_id": "src_db",
                "source_locator": "model.pdm#index=business-key",
            }
        ],
        "governance": {},
    }

    result = reconcile_database_model_index_assets(asset)

    table = result["tables"][0]
    assert table["identity_fields"] == ["order_no", "tenant_id"]
    identity_key = table["identity_keys"][0]
    assert identity_key["columns"] == ["tenant_id", "order_no"]
    assert identity_key["composite"] is True
    assert identity_key["kind"] == "UNIQUE_INDEX"

    for field in result["field_dictionary"]:
        assert field["identity"] is False
        assert field["unique"] is False
        assert field["identity_component"] is True
        assert field["composite_identity_member"] is True
        assert field["identity_key_ids"] == [
            "identity_key:index:tenant_order_business_key"
        ]

    receipt = result["database_model_index_reconciliation"]
    assert receipt["composite_identity_key_count"] == 1
    assert receipt[
        "composite_unique_members_are_not_individually_unique"
    ] is True
    assert result["governance"][
        "database_model_composite_unique_members_not_individually_unique"
    ] is True
