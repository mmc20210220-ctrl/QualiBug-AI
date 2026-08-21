from __future__ import annotations

import json

from ai_test_asset_center.agent_semantic_linker_authority import (
    enrich_knowledge_asset_with_agent_relationships,
)


class TransitionPagingFakeClient:
    def __init__(self) -> None:
        self.calls = 0
        self.rule_calls = 0
        self.transition_calls = 0
        self.transition_batch_sizes: list[int] = []

    def complete_json(self, **kwargs: object) -> dict:
        self.calls += 1
        prompt = str(kwargs["user_prompt"])
        packet = json.loads(
            prompt.split("INPUT:\n", 1)[1].split("\n\nFINAL CONTRACT CHECK", 1)[0]
        )
        mode = packet["assessment_mode"]
        if mode == "rule_to_interface":
            self.rule_calls += 1
            row = packet["business_semantic_model"]["rules_to_assess"][0]
            rule = row["rule"]
            interface_id = row["candidate_interfaces"][0]["interface_id"]
            return {
                "assessments": [{
                    "rule_id": rule["rule_id"],
                    "disposition": "LINKED",
                    "reason": "The documented interface exercises the rule.",
                    "relationships": [{
                        "interface_id": interface_id,
                        "confidence": 0.95,
                        "reason": "The interface performs the documented operation.",
                        "evidence_refs": [rule["rule_id"], interface_id],
                    }],
                }]
            }

        self.transition_calls += 1
        rows = packet["business_semantic_model"]["state_transitions_to_assess"]
        self.transition_batch_sizes.append(len(rows))
        return {
            "transition_assessments": [
                {
                    "transition_id": row["transition"]["transition_id"],
                    "disposition": "LINKED",
                    "reason": "The documented interface performs the state transition.",
                    "relationships": [{
                        "interface_id": row["candidate_interfaces"][0]["interface_id"],
                        "confidence": 0.95,
                        "reason": "The interface is the documented state-changing operation.",
                        "evidence_refs": [
                            row["transition"]["transition_id"],
                            row["candidate_interfaces"][0]["interface_id"],
                        ],
                    }],
                }
                for row in rows
            ]
        }

    def usage_snapshot(self) -> dict[str, float]:
        return {"request_count": float(self.calls), "total_tokens": 0.0}


def _asset(transition_count: int) -> dict:
    transitions = [
        {
            "from": f"S{index:03d}",
            "to": f"S{index + 1:03d}",
        }
        for index in range(transition_count)
    ]
    return {
        "asset_id": "transition-paging-regression",
        "rule_library": [{
            "rule_id": "rule-0001",
            "statement": "An order must be processed through the documented operation.",
            "kind": "business_rule",
            "source_id": "source-1",
            "semantic_frame": {
                "condition": "",
                "subject": "order",
                "behavior": "An order must be processed through the documented operation.",
                "source_anchors": [],
                "source_grounded": True,
            },
        }],
        "interfaces": [{
            "interface_id": "api:POST:/orders/process",
            "operation_id": "process_order",
            "method": "POST",
            "path": "/orders/process",
            "summary": "Process an order and change its state",
            "source_id": "source-1",
            "field_dictionary": ["order_id", "status"],
        }],
        "data_tables": [],
        "field_dictionary": [],
        "roles": [],
        "permission_matrix": [],
        "entity_relations": [],
        "state_machines": [{
            "state_machine_id": "sm:order",
            "object": "order",
            "states": [f"S{index:03d}" for index in range(transition_count + 1)],
            "transitions": transitions,
            "forbidden_transitions": [],
            "source_id": "source-1",
        }],
        "relationships": [],
    }


def test_transition_recall_is_lossless_past_the_200_transition_window() -> None:
    client = TransitionPagingFakeClient()
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(201),
        client=client,
    )

    assert receipt["transition_count"] == 201
    assert receipt["assessed_transition_count"] == 201
    assert receipt["unassessed_transition_count"] == 0
    assert receipt["transition_budget_skipped_count"] == 0
    assert receipt["transition_paging"] == {
        "enabled": True,
        "window_size": 200,
        "window_count": 2,
        "transition_count": 201,
        "budget_skipped_transition_count": 0,
        "rule_response_reuse_count": 1,
        "reason_code": "SOURCE_TRANSITIONS_PAGED_INSTEAD_OF_TRUNCATED",
    }
    assert client.rule_calls == 1
    assert client.transition_calls == 2
    assert client.transition_batch_sizes == [200, 1]
    assert sum(
        row["relation"] == "state_transition_to_interface"
        for row in enriched["relationships"]
    ) == 201
    assert receipt["status"] == "VERIFIED"


def test_transition_linking_below_the_window_keeps_original_single_request_path() -> None:
    client = TransitionPagingFakeClient()
    _, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(200),
        client=client,
    )

    assert "transition_paging" not in receipt
    assert receipt["transition_count"] == 200
    assert receipt["assessed_transition_count"] == 200
    assert receipt["transition_budget_skipped_count"] == 0
    assert client.rule_calls == 1
    assert client.transition_calls == 1
    assert client.transition_batch_sizes == [200]
