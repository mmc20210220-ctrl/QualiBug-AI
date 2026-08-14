from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    enrich_asset_with_enterprise_understanding,
)


def test_field_inventory_without_business_behavior_cannot_pass() -> None:
    asset = {
        "asset_id": "asset-field-only",
        "source_inventory": [
            {
                "source_id": "schema-1",
                "status": "active",
                "source_type": "database_schema",
            }
        ],
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v1",
            "items": [],
        },
        "business_objects": [],
        "roles": [],
        "permission_matrix": [],
        "data_tables": [
            {
                "table_id": "table:orders",
                "source_id": "schema-1",
                "name": "订单",
                "description": "订单",
                "derivation": "entity_inventory_table",
                "source_locator": "BUSINESS_RULES.md#core-entities",
                "columns": ["订单号", "状态"],
            }
        ],
        "field_dictionary": [],
        "state_machines": [],
        "entity_relations": [],
        "cross_document_conflicts": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
        "enterprise_comprehension_gate": {
            "status": "PASS",
            "entry_allowed": True,
        },
    }

    enriched = enrich_asset_with_enterprise_understanding(asset)
    model = enriched["enterprise_understanding_model"]

    assert [row["name"] for row in model["business_objects"]] == ["订单"]
    assert model["operations"] == []
    assert model["gate"]["status"] == "BLOCKED_ENTERPRISE_UNDERSTANDING_CRITICAL_UNKNOWN"
    assert any(
        row["reason_code"] == "NO_BUSINESS_BEHAVIOR_UNDERSTOOD"
        for row in model["unknowns"]
    )
    assert enriched["governance"]["field_or_entity_inventory_alone_cannot_pass_understanding_gate"] is True
