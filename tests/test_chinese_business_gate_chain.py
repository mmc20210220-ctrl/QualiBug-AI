from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._chinese_business_downstream import (
    refresh_chinese_business_downstream,
)


def test_downstream_preserves_upstream_conflict_gate_reason() -> None:
    asset = {
        "rule_library": [],
        "interfaces": [],
        "relationships": [],
        "risk_domains": [],
        "oracle_library": [],
        "coverage_gaps": [
            {
                "kind": "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS",
            }
        ],
        "enterprise_comprehension_gate": {
            "status": "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS",
            "entry_allowed": False,
        },
        "summary": {},
    }

    enriched, probes = refresh_chinese_business_downstream(asset)

    assert probes == []
    assert (
        enriched["enterprise_comprehension_gate"]["status"]
        == "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS"
    )
    downstream = enriched["enterprise_comprehension_gate"]["downstream"]
    assert downstream["status"] == "BLOCKED_UPSTREAM_BUSINESS_COMPREHENSION_GATE"
    assert downstream["upstream_gate_status"] == (
        "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS"
    )
    assert downstream["blocked_rule_count"] == 0
