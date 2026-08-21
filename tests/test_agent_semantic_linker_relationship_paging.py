from __future__ import annotations

import json

from ai_test_asset_center.agent_semantic_linker_authority import (
    enrich_knowledge_asset_with_agent_relationships,
)


class FourLinkWindowFakeClient:
    def __init__(self) -> None:
        self.calls = 0
        self.call_interface_ids: list[list[str]] = []

    def complete_json(self, **kwargs: object) -> dict:
        self.calls += 1
        prompt = str(kwargs["user_prompt"])
        packet = json.loads(
            prompt.split("INPUT:\n", 1)[1].split("\n\nFINAL CONTRACT CHECK", 1)[0]
        )
        candidate_rows = packet["business_semantic_model"]["rules_to_assess"][0]["candidate_interfaces"]
        candidate_ids = [row["interface_id"] for row in candidate_rows]
        self.call_interface_ids.append(candidate_ids)

        targets = [
            "api:POST:/order/cancel",
            "api:POST:/order/cancel-admin",
            "api:POST:/order/cancel-system",
            "api:POST:/order/cancel-bulk",
            "api:POST:/order/cancel-retry",
        ]
        available_targets = [target for target in targets if target in candidate_ids]
        links = available_targets[:4]
        rule_id = packet["business_semantic_model"]["rules_to_assess"][0]["rule"]["rule_id"]
        return {
            "assessments": [{
                "rule_id": rule_id,
                "disposition": "LINKED",
                "reason": "The candidate interfaces independently exercise the cancellation rule.",
                "relationships": [
                    {
                        "interface_id": interface_id,
                        "confidence": 0.99,
                        "reason": "The interface is a documented executable surface for the rule.",
                        "evidence_refs": [rule_id, interface_id],
                    }
                    for interface_id in links
                ],
            }]
        }

    def usage_snapshot(self) -> dict[str, float]:
        return {"request_count": float(self.calls), "total_tokens": 0.0}


def _asset(interface_count: int = 12) -> dict:
    target_paths = [
        "/order/cancel",
        "/order/cancel-admin",
        "/order/cancel-system",
        "/order/cancel-bulk",
        "/order/cancel-retry",
    ]
    interfaces = []
    for index in range(interface_count):
        if index < len(target_paths):
            path = target_paths[index]
        else:
            path = f"/opaque-{index:02d}"
        interfaces.append({
            "interface_id": f"api:POST:{path}",
            "operation_id": f"operation-{index:02d}",
            "method": "POST",
            "path": path,
            "summary": "Order cancellation surface" if index < len(target_paths) else "Opaque endpoint",
            "description": "Executable order cancellation interface" if index < len(target_paths) else "Unrelated executable interface",
            "source_id": "api-source",
            "field_dictionary": ["order_id"] if index < len(target_paths) else [],
        })
    return {
        "asset_id": "relationship-paging-regression",
        "rule_library": [{
            "rule_id": "rule-cancel-order",
            "statement": "An order may be cancelled through the documented cancellation surfaces.",
            "kind": "business_rule",
            "source_id": "prd-source",
            "semantic_frame": {
                "condition": "",
                "subject": "order cancellation",
                "behavior": "An order may be cancelled through the documented cancellation surfaces.",
                "source_anchors": [],
                "source_grounded": True,
            },
        }],
        "interfaces": interfaces,
        "data_tables": [],
        "field_dictionary": [],
        "roles": [],
        "permission_matrix": [],
        "entity_relations": [],
        "state_machines": [],
        "relationships": [],
    }


def test_rule_link_budget_pages_after_four_accepted_links() -> None:
    client = FourLinkWindowFakeClient()
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(_asset(), client=client)

    assert client.calls == 2
    assert len(client.call_interface_ids[0]) == 12
    assert set(client.call_interface_ids[0]) >= {
        "api:POST:/order/cancel",
        "api:POST:/order/cancel-admin",
        "api:POST:/order/cancel-system",
        "api:POST:/order/cancel-bulk",
        "api:POST:/order/cancel-retry",
    }
    assert "api:POST:/order/cancel-retry" in client.call_interface_ids[1]
    assert receipt["accepted_relationship_count"] == 5
    assert receipt["relationship_paging"]["enabled"] is True
    assert receipt["relationship_paging"]["window_size"] == 4
    assert receipt["relationship_paging"]["pass_count"] == 2
    assert receipt["relationship_paging"]["followup_call_count"] == 1
    assert receipt["relationship_paging"]["saturated_rule_count"] == 1
    assert sum(
        row.get("relation") == "rule_to_interface"
        and row.get("from") == "rule-cancel-order"
        for row in enriched["relationships"]
    ) == 5


def test_rule_with_four_links_does_not_page_when_no_candidates_remain() -> None:
    client = FourLinkWindowFakeClient()
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(_asset(4), client=client)

    assert client.calls == 1
    assert receipt["accepted_relationship_count"] == 4
    assert receipt["relationship_paging"]["enabled"] is False
    assert receipt["relationship_paging"]["pass_count"] == 1
    assert sum(
        row.get("relation") == "rule_to_interface"
        and row.get("from") == "rule-cancel-order"
        for row in enriched["relationships"]
    ) == 4
