from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.typed_fact_conflicts import (
    reconcile_typed_fact_conflicts,
)


def _statusless(fact_id: str, combinator: str) -> dict:
    return {
        "fact_id": fact_id,
        "fact_type": "PERMISSION_RULE",
        "subject": {"actor_refs": ["经理"], "entity_refs": ["订单"]},
        "object": {"entity_refs": ["订单"]},
        "predicate": "审批",
        "condition_frame": {
            "combinator": combinator,
            "conditions": ["状态为待审批", "所属部门一致"],
        },
    }


def test_statusless_facts_do_not_create_formal_conflicts(tmp_path) -> None:
    asset = {
        "business_fact_ledger": {
            "items": [
                _statusless("fact:statusless-and", "AND"),
                _statusless("fact:statusless-or", "OR"),
            ]
        },
        "cross_document_conflicts": [],
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "coverage_gaps": [],
    }

    result = reconcile_typed_fact_conflicts(
        asset,
        project_id="demo",
        root=tmp_path,
    )

    assert result["cross_document_conflicts"] == []
    assert result["enterprise_comprehension_gate"]["status"] == "PASS"
    assert result["enterprise_comprehension_gate"]["entry_allowed"] is True
    receipt = result["typed_business_fact_conflict_receipt"]
    assert receipt["active_fact_count"] == 0
    assert receipt["statusless_fact_count"] == 2
    assert receipt["statusless_fact_defaulted_to_accepted"] is False
