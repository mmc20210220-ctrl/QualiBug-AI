from __future__ import annotations

import json
import re

from ai_test_asset_center.agent_semantic_linker_authority import (
    enrich_knowledge_asset_with_agent_relationships,
)


class PagingAwareFakeClient:
    def __init__(self) -> None:
        self.calls = 0
        self.request_rule_counts: list[int] = []

    def complete_json(self, **kwargs: object) -> dict:
        self.calls += 1
        prompt = str(kwargs["user_prompt"])
        packet = json.loads(
            prompt.split("INPUT:\n", 1)[1].split("\n\nFINAL CONTRACT CHECK", 1)[0]
        )
        rows = packet["business_semantic_model"]["rules_to_assess"]
        self.request_rule_counts.append(len(rows))
        assessments = []
        for row in rows:
            rule_id = row["rule"]["rule_id"]
            interface_id = row["candidate_interfaces"][0]["interface_id"]
            assessments.append({
                "rule_id": rule_id,
                "disposition": "LINKED",
                "reason": "The documented transfer interface exercises the rule.",
                "relationships": [{
                    "interface_id": interface_id,
                    "confidence": 0.95,
                    "reason": "The interface is source-co-located with the rule.",
                    "evidence_refs": [rule_id, interface_id],
                }],
            })
        return {"assessments": assessments}

    def usage_snapshot(self) -> dict[str, float]:
        return {"request_count": float(self.calls), "total_tokens": 0.0}


def _asset(rule_count: int) -> dict:
    return {
        "asset_id": "lossless-rule-scheduling",
        "rule_library": [
            {
                "rule_id": f"rule-{index:04d}",
                "statement": "The transfer quantity must be conserved.",
                "kind": "conservation",
                "source_id": "shared-source",
                "semantic_frame": {
                    "condition": "",
                    "subject": "transfer",
                    "behavior": "The transfer quantity must be conserved.",
                    "source_anchors": [],
                    "source_grounded": True,
                },
            }
            for index in range(rule_count)
        ],
        "interfaces": [{
            "interface_id": "api:POST:/transfers",
            "operation_id": "create_transfer",
            "method": "POST",
            "path": "/transfers",
            "summary": "Create a transfer quantity",
            "source_id": "shared-source",
            "field_dictionary": ["quantity"],
        }],
        "data_tables": [],
        "state_machines": [],
        "relationships": [],
    }


def test_rules_after_the_320_window_are_still_semantically_linked() -> None:
    client = PagingAwareFakeClient()
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(321),
        client=client,
    )

    assert receipt["rule_count"] == 321
    assert receipt["assessed_rule_count"] == 321
    assert receipt["unassessed_rule_count"] == 0
    assert receipt["budget_skipped_rule_count"] == 0
    assert receipt["budget_skipped_rule_ids"] == []
    assert receipt["lossless_rule_scheduling"] == {
        "enabled": True,
        "window_size": 320,
        "window_count": 2,
        "budget_skipped_rule_count": 0,
        "reason_code": "SOURCE_RULES_PAGED_INSTEAD_OF_GLOBALLY_TRUNCATED",
    }
    assert client.request_rule_counts == [40] * 8 + [1]
    assert client.calls == 9
    assert receipt["accepted_relationship_count"] == 321
    assert len(enriched["relationships"]) == 321
    assert {row["from"] for row in enriched["relationships"]} == {
        f"rule-{index:04d}" for index in range(321)
    }
    assert receipt["status"] == "VERIFIED"


def test_lossless_rule_scheduler_is_not_activated_below_the_window() -> None:
    client = PagingAwareFakeClient()
    _, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(320),
        client=client,
    )

    assert "lossless_rule_scheduling" not in receipt
    assert receipt["rule_count"] == 320
    assert receipt["assessed_rule_count"] == 320
    assert receipt["budget_skipped_rule_count"] == 0
    assert client.request_rule_counts == [40] * 8
