from __future__ import annotations

import json

from ai_test_asset_center.agent_semantic_linker_authority import (
    enrich_knowledge_asset_with_agent_relationships,
)


class SupportingFactPagingFakeClient:
    def __init__(self) -> None:
        self.calls = 0
        self.rule_calls = 0
        self.fact_batch_sizes: list[int] = []
        self.seen_fact_ids: list[list[str]] = []

    def complete_json(self, **kwargs: object) -> dict:
        self.calls += 1
        prompt = str(kwargs["user_prompt"])
        packet = json.loads(
            prompt.split("INPUT:\n", 1)[1].split("\n\nFINAL CONTRACT CHECK", 1)[0]
        )
        assert packet["assessment_mode"] == "rule_to_interface"
        self.rule_calls += 1
        row = packet["business_semantic_model"]["rules_to_assess"][0]
        facts = row["supporting_facts"]
        fact_ids = [fact["fact_id"] for fact in facts]
        self.fact_batch_sizes.append(len(fact_ids))
        self.seen_fact_ids.append(fact_ids)
        target_fact = "table:target"
        target_interface = "api:POST:/target"
        if target_fact not in fact_ids:
            return {
                "assessments": [{
                    "rule_id": row["rule"]["rule_id"],
                    "disposition": "NO_EXECUTABLE_INTERFACE",
                    "reason": "The decisive source fact is not present in this evidence window.",
                    "relationships": [],
                }]
            }
        return {
            "assessments": [{
                "rule_id": row["rule"]["rule_id"],
                "disposition": "LINKED",
                "reason": "The decisive source fact identifies the target operation as the executable surface.",
                "relationships": [{
                    "interface_id": target_interface,
                    "confidence": 0.98,
                    "reason": "The target fact and documented target operation jointly establish the link.",
                    "evidence_refs": [row["rule"]["rule_id"], target_interface, target_fact],
                }],
            }]
        }

    def usage_snapshot(self) -> dict[str, float]:
        return {"request_count": float(self.calls), "total_tokens": 0.0}


def _asset(fact_count: int) -> dict:
    facts = []
    for index in range(fact_count):
        if index == fact_count - 1:
            fact_id = "table:target"
            name = "target"
        else:
            fact_id = f"table:decoy:{index:02d}"
            name = "target-decoy"
        facts.append({
            "table_id": fact_id,
            "name": name,
            "columns": ["target"],
            "source_id": "schema-source",
        })
    return {
        "asset_id": "supporting-fact-paging-regression",
        "rule_library": [{
            "rule_id": "rule-001",
            "statement": "The target operation must preserve the target quantity.",
            "kind": "business_rule",
            "source_id": "prd-source",
            "semantic_frame": {
                "condition": "",
                "subject": "target quantity",
                "behavior": "The target operation must preserve the target quantity.",
                "source_anchors": [],
                "source_grounded": True,
            },
        }],
        "interfaces": [{
            "interface_id": "api:POST:/target",
            "operation_id": "target_operation",
            "method": "POST",
            "path": "/target",
            "summary": "Target operation",
            "description": "Documented target operation",
            "source_id": "api-source",
            "field_dictionary": ["target"],
        }],
        "data_tables": facts,
        "field_dictionary": [],
        "roles": [],
        "permission_matrix": [],
        "entity_relations": [],
        "state_machines": [],
        "relationships": [],
    }


def test_supporting_fact_recall_pages_past_the_20_fact_window() -> None:
    client = SupportingFactPagingFakeClient()
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(21),
        client=client,
    )

    assert receipt["supporting_fact_paging"] == {
        "enabled": True,
        "window_size": 20,
        "window_count": 2,
        "source_fact_count": 21,
        "window_fact_counts": [20, 1],
        "unconsumed_tail_fact_count": 0,
        "fact_budget_skipped_count": 0,
        # The rule closes only when MAX_LINKS_PER_RULE links accumulate; one
        # link leaves it unresolved after both windows, so fact exhaustion is
        # what terminates the paging loop.
        "unresolved_rule_counts_after_window": [1, 1],
        "relationship_closure_rule_counts_after_window": [1, 1],
        "zero_score_fact_fill_enabled": True,
        "reason_code": "SOURCE_SUPPORTING_FACTS_PAGED_UNTIL_RULE_LINK_CLOSURE_OR_FACT_EXHAUSTION",
    }
    assert client.rule_calls == 2
    assert client.fact_batch_sizes == [20, 1]
    assert "table:target" not in client.seen_fact_ids[0]
    assert "table:target" in client.seen_fact_ids[1]
    assert receipt["accepted_relationship_count"] == 1
    assert any(
        row.get("relation") == "rule_to_interface"
        and row.get("to") == "api:POST:/target"
        for row in enriched["relationships"]
    )
    assert receipt["status"] == "VERIFIED"


def test_supporting_fact_recall_below_the_window_keeps_original_single_request_path() -> None:
    client = SupportingFactPagingFakeClient()
    _, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(20),
        client=client,
    )

    assert "supporting_fact_paging" not in receipt
    assert client.rule_calls == 1
    assert client.fact_batch_sizes == [20]
