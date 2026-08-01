from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.post_compile_fact_governance import (
    _normalize_typed_fact_values,
)


def test_rejected_cardinality_is_not_reactivated_or_gate_blocking() -> None:
    rejected = {
        "fact_id": "fact:retired",
        "fact_type": "CARDINALITY_CONSTRAINT",
        "status": "REJECTED",
        "claims": [
            {
                "claim_type": "CARDINALITY_CONSTRAINT",
                "value": {"maximum": "1"},
            },
            {
                "claim_type": "CARDINALITY_CONSTRAINT",
                "value": {"maximum": "MANY"},
            },
        ],
        "ambiguities": ["DUPLICATE_COMPATIBILITY_TYPED_SHELL"],
    }
    asset = {
        "business_fact_ledger": {"items": [rejected]},
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "coverage_gaps": [],
        "governance": {},
    }

    result = _normalize_typed_fact_values(asset)

    fact = result["business_fact_ledger"]["items"][0]
    assert fact["status"] == "REJECTED"
    assert "typed_value_projection" not in fact
    receipt = result["typed_fact_value_projection_receipt"]
    assert receipt["status"] == "PASS"
    assert receipt["skipped_nonaccepted_fact_count"] == 1
    assert result["enterprise_comprehension_gate"] == {
        "status": "PASS",
        "entry_allowed": True,
    }
    assert result["coverage_gaps"] == []
