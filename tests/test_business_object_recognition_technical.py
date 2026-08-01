from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    build_enterprise_understanding_model,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.business_object_recognition import (
    recognize_business_objects,
)


def _asset(**extra) -> dict:
    value = {
        "asset_id": "object-technical-test",
        "business_fact_ledger": {"items": []},
        "business_objects": [],
        "roles": [],
        "permission_matrix": [],
        "data_tables": [],
        "field_dictionary": [],
        "state_machines": [],
        "entity_relations": [],
        "cross_document_conflicts": [],
        "coverage_gaps": [],
        "source_inventory": [],
        "summary": {},
        "governance": {},
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
    }
    value.update(extra)
    return value


def test_table_and_derived_guess_do_not_become_business_objects() -> None:
    asset = _asset(
        business_objects=[{
            "object": "行业推断对象",
            "source": "industry_inference",
            "derivation": "industry_inference",
        }],
        data_tables=[{"table_id": "table:orders", "name": "orders", "source_id": "db"}],
    )
    recognition = recognize_business_objects(asset)
    statuses = {
        label: row["status"]
        for row in recognition["candidates"]
        for label in row["labels"]
    }

    assert "行业推断对象" not in statuses
    assert statuses["orders"] == "PENDING_TECHNICAL_ONLY"
    assert recognition["gate"]["metrics"]["ignored_derived_input_count"] == 1
    assert recognition["ignored_inputs"][0]["label"] == "行业推断对象"
    assert recognition["derived_object_assets_used_as_authority"] is False
    assert recognition["technical_artifacts_are_business_objects"] is False
    assert build_enterprise_understanding_model(asset)["business_objects"] == []
