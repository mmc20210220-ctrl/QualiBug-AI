from __future__ import annotations

import json

from ai_test_asset_center.agent_semantic_linker import transition_identity
from ai_test_asset_center.agent_semantic_linker_authority import (
    enrich_knowledge_asset_with_agent_relationships,
)


class OmittedTransitionFakeClient:
    def __init__(self) -> None:
        self.transition_calls = 0
        self.transition_call_ids: list[list[str]] = []

    def complete_json(self, **kwargs: object) -> dict:
        prompt = str(kwargs["user_prompt"])
        if '"assessment_mode":"rule_to_interface"' in prompt:
            return {"assessments": []}
        assert '"assessment_mode":"state_transition_to_interface"' in prompt
        packet = json.loads(
            prompt.split("INPUT:\n", 1)[1].split("\n\nFINAL CONTRACT CHECK", 1)[0]
        )
        rows = packet["business_semantic_model"]["state_transitions_to_assess"]
        transition_ids = [row["transition"]["transition_id"] for row in rows]
        self.transition_calls += 1
        self.transition_call_ids.append(transition_ids)

        target_by_transition = {
            transition_identity("sm:order:status", "allowed", "PENDING", "CANCELLED"): "api:POST:/order/cancel",
            transition_identity("sm:order:status", "allowed", "CANCELLED", "COMPLETED"): "api:POST:/order/complete",
        }
        if self.transition_calls == 1:
            transition_ids = transition_ids[:1]

        return {
            "transition_assessments": [
                {
                    "transition_id": transition_id,
                    "disposition": "LINKED",
                    "reason": "The documented operation performs the supplied state transition.",
                    "relationships": [
                        {
                            "interface_id": target_by_transition[transition_id],
                            "confidence": 0.99,
                            "reason": "The interface is the documented state-changing operation.",
                            "evidence_refs": [transition_id, target_by_transition[transition_id]],
                        }
                    ],
                }
                for transition_id in transition_ids
            ]
        }

    def usage_snapshot(self) -> dict[str, float]:
        return {"request_count": float(self.transition_calls), "total_tokens": 0.0}


def _asset() -> dict:
    return {
        "asset_id": "transition-omission-recovery",
        "rule_library": [
            {
                "rule_id": "rule-order-transition",
                "statement": "An order may move through the documented lifecycle states.",
                "kind": "business_rule",
                "semantic_frame": {
                    "subject": "order lifecycle",
                    "behavior": "An order may move through the documented lifecycle states.",
                    "source_anchors": [],
                },
                "source_id": "prd-order",
            }
        ],
        "interfaces": [
            {
                "interface_id": "api:POST:/order/cancel",
                "operation_id": "order-cancel",
                "method": "POST",
                "path": "/order/cancel",
                "summary": "Cancel order",
                "description": "Moves an order from pending to cancelled.",
                "source_id": "api-order",
                "field_dictionary": ["order_id"],
            },
            {
                "interface_id": "api:POST:/order/complete",
                "operation_id": "order-complete",
                "method": "POST",
                "path": "/order/complete",
                "summary": "Complete order",
                "description": "Moves an order from cancelled to completed.",
                "source_id": "api-order",
                "field_dictionary": ["order_id"],
            },
        ],
        "data_tables": [],
        "field_dictionary": [],
        "roles": [],
        "permission_matrix": [],
        "entity_relations": [],
        "state_machines": [
            {
                "state_machine_id": "sm:order:status",
                "object": "order",
                "states": ["PENDING", "CANCELLED", "COMPLETED"],
                "transitions": [
                    {"from": "PENDING", "to": "CANCELLED"},
                    {"from": "CANCELLED", "to": "COMPLETED"},
                ],
                "forbidden_transitions": [],
                "source_id": "prd-order",
            }
        ],
        "relationships": [],
    }


def test_provider_omitted_transition_is_reassessed_in_targeted_unit() -> None:
    client = OmittedTransitionFakeClient()
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(_asset(), client=client)

    first_transition = transition_identity(
        "sm:order:status", "allowed", "PENDING", "CANCELLED"
    )
    second_transition = transition_identity(
        "sm:order:status", "allowed", "CANCELLED", "COMPLETED"
    )

    assert client.transition_calls == 2
    assert client.transition_call_ids[0] == [first_transition, second_transition]
    assert client.transition_call_ids[1] == [second_transition]
    assert receipt["assessed_transition_count"] == 2
    assert receipt["unassessed_transition_count"] == 0
    assert receipt["unassessed_transition_ids"] == []
    assert receipt["recovered_omitted_transition_count"] == 1
    assert receipt["omitted_transition_recovery"]["initial_omitted_transition_count"] == 1
    assert receipt["omitted_transition_recovery"]["recovered_transition_assessment_count"] == 1
    assert receipt["omitted_transition_recovery"]["remaining_omitted_transition_count"] == 0
    assert receipt["omitted_transition_recovery"]["recovered_transition_ids"] == [second_transition]
    assert receipt["status"] == "VERIFIED"
    assert sum(
        row.get("relation") == "state_transition_to_interface"
        for row in enriched["relationships"]
    ) == 2


def test_transition_recovery_preserves_unresolved_transition_when_provider_omits_again() -> None:
    class AlwaysOmitSecond(OmittedTransitionFakeClient):
        def complete_json(self, **kwargs: object) -> dict:
            prompt = str(kwargs["user_prompt"])
            if '"assessment_mode":"rule_to_interface"' in prompt:
                return {"assessments": []}
            packet = json.loads(
                prompt.split("INPUT:\n", 1)[1].split("\n\nFINAL CONTRACT CHECK", 1)[0]
            )
            rows = packet["business_semantic_model"]["state_transitions_to_assess"]
            ids = [row["transition"]["transition_id"] for row in rows]
            self.transition_calls += 1
            self.transition_call_ids.append(ids)
            target = transition_identity("sm:order:status", "allowed", "PENDING", "CANCELLED")
            return {
                "transition_assessments": [
                    {
                        "transition_id": target,
                        "disposition": "LINKED",
                        "reason": "The cancellation operation performs the transition.",
                        "relationships": [{
                            "interface_id": "api:POST:/order/cancel",
                            "confidence": 0.99,
                            "reason": "The interface performs the state change.",
                            "evidence_refs": [target, "api:POST:/order/cancel"],
                        }],
                    }
                ]
            }

    client = AlwaysOmitSecond()
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(_asset(), client=client)

    second_transition = transition_identity(
        "sm:order:status", "allowed", "CANCELLED", "COMPLETED"
    )
    assert client.transition_calls == 2
    assert receipt["unassessed_transition_count"] == 1
    assert receipt["unassessed_transition_ids"] == [second_transition]
    assert receipt["recovered_omitted_transition_count"] == 0
    assert receipt["omitted_transition_recovery"]["remaining_omitted_transition_count"] == 1
    assert receipt["status"] == "VERIFIED_WITH_REJECTIONS"
    assert sum(
        row.get("relation") == "state_transition_to_interface"
        for row in enriched["relationships"]
    ) == 1
