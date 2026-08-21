from __future__ import annotations

import json

from ai_test_asset_center.agent_semantic_linker_authority import (
    enrich_knowledge_asset_with_agent_relationships,
)


class FactWindowClosureFakeClient:
    def __init__(self) -> None:
        self.rule_calls = 0
        self.supporting_fact_ids: list[list[str]] = []

    def complete_json(self, **kwargs: object) -> dict:
        prompt = str(kwargs["user_prompt"])
        assert '"assessment_mode":"rule_to_interface"' in prompt
        packet = json.loads(
            prompt.split("INPUT:\n", 1)[1].split("\n\nFINAL CONTRACT CHECK", 1)[0]
        )
        unit = packet["business_semantic_model"]["rules_to_assess"][0]
        fact_ids = [fact["fact_id"] for fact in unit["supporting_facts"]]
        self.rule_calls += 1
        self.supporting_fact_ids.append(fact_ids)

        if "fact:decisive" in fact_ids:
            interface_id = "api:POST:/order/complete"
            reason = "The later supporting fact establishes the completion operation as the second executable surface."
        else:
            interface_id = "api:POST:/order/cancel"
            reason = "The first supporting fact establishes the cancellation operation as an executable surface."

        return {
            "assessments": [{
                "rule_id": unit["rule"]["rule_id"],
                "disposition": "LINKED",
                "reason": reason,
                "relationships": [{
                    "interface_id": interface_id,
                    "confidence": 0.99,
                    "reason": reason,
                    "evidence_refs": [unit["rule"]["rule_id"], interface_id, fact_ids[0]],
                }],
            }]
        }

    def usage_snapshot(self) -> dict[str, float]:
        return {"request_count": float(self.rule_calls), "total_tokens": 0.0}


def _asset() -> dict:
    facts = [
        {
            "table_id": f"fact:{index:02d}",
            "name": f"Unrelated table {index}",
            "columns": ["unrelated_field"],
            "source_id": "schema",
        }
        for index in range(20)
    ]
    facts.append({
        "table_id": "fact:decisive",
        "name": "Order lifecycle evidence",
        "columns": ["order_id", "status"],
        "source_id": "prd-order",
    })
    return {
        "asset_id": "fact-window-closure",
        "rule_library": [{
            "rule_id": "rule-order-lifecycle",
            "statement": "An order lifecycle rule is exercised by its documented executable surfaces.",
            "kind": "business_rule",
            "semantic_frame": {
                "subject": "order lifecycle",
                "behavior": "An order lifecycle rule is exercised by its documented executable surfaces.",
                "source_anchors": [],
            },
            "source_id": "prd-order",
        }],
        "interfaces": [
            {
                "interface_id": "api:POST:/order/cancel",
                "operation_id": "order-cancel",
                "method": "POST",
                "path": "/order/cancel",
                "summary": "Cancel order",
                "description": "Cancels an order.",
                "source_id": "api-order",
                "field_dictionary": ["order_id"],
            },
            {
                "interface_id": "api:POST:/order/complete",
                "operation_id": "order-complete",
                "method": "POST",
                "path": "/order/complete",
                "summary": "Complete order",
                "description": "Completes an order.",
                "source_id": "api-order",
                "field_dictionary": ["order_id"],
            },
        ],
        "data_tables": facts,
        "field_dictionary": [],
        "roles": [],
        "permission_matrix": [],
        "entity_relations": [],
        "state_machines": [],
        "relationships": [],
    }


def test_fact_paging_does_not_close_a_rule_after_one_link() -> None:
    client = FactWindowClosureFakeClient()
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(_asset(), client=client)

    assert client.rule_calls == 2
    assert len(client.supporting_fact_ids[0]) == 20
    assert "fact:decisive" not in client.supporting_fact_ids[0]
    assert client.supporting_fact_ids[1] == ["fact:decisive"]
    assert receipt["accepted_relationship_count"] == 2
    assert receipt["supporting_fact_paging"]["window_count"] == 2
    assert receipt["supporting_fact_paging"]["source_fact_count"] == 21
    assert receipt["supporting_fact_paging"]["relationship_closure_rule_counts_after_window"] == [1, 1]
    assert receipt["status"] == "VERIFIED"
    assert {
        row["to"]
        for row in enriched["relationships"]
        if row.get("relation") == "rule_to_interface"
    } == {
        "api:POST:/order/cancel",
        "api:POST:/order/complete",
    }
