from __future__ import annotations

from ai_test_asset_center.agent_semantic_linker_authority import (
    enrich_knowledge_asset_with_agent_relationships,
)


class LowConfidenceClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, **_: object) -> dict:
        self.calls += 1
        return {
            "assessments": [{
                "rule_id": "rule-order-cancel",
                "disposition": "LINKED",
                "reason": "The documented endpoint is the executable cancellation surface.",
                "relationships": [{
                    "interface_id": "api:POST:/orders/cancel",
                    "confidence": 0.64,
                    "reason": "The endpoint cancels the order governed by the rule.",
                    "evidence_refs": [
                        "rule-order-cancel",
                        "api:POST:/orders/cancel",
                    ],
                }],
            }],
        }

    def usage_snapshot(self) -> dict[str, float]:
        return {"request_count": float(self.calls), "total_tokens": 0.0}


def _asset() -> dict:
    return {
        "asset_id": "confidence-recall-regression",
        "rule_library": [{
            "rule_id": "rule-order-cancel",
            "statement": "An order may be cancelled through the cancellation endpoint.",
            "kind": "business_rule",
            "source_id": "prd-source",
            "semantic_frame": {
                "condition": "",
                "subject": "order cancellation",
                "behavior": "An order may be cancelled through the cancellation endpoint.",
                "source_anchors": [],
                "source_grounded": True,
            },
        }],
        "interfaces": [{
            "interface_id": "api:POST:/orders/cancel",
            "operation_id": "cancel_order",
            "method": "POST",
            "path": "/orders/cancel",
            "summary": "Cancel order",
            "description": "Cancel an order",
            "field_dictionary": ["order_id"],
            "source_id": "api-source",
        }],
        "data_tables": [],
        "field_dictionary": [],
        "roles": [],
        "permission_matrix": [],
        "entity_relations": [],
        "state_machines": [],
        "relationships": [],
    }


def test_low_confidence_link_is_reassessed_instead_of_permanently_dropped() -> None:
    client = LowConfidenceClient()

    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(),
        client=client,
    )

    assert client.calls == 2
    assert receipt["accepted_relationship_count"] == 1
    assert receipt["recovered_low_confidence_count"] == 1
    assert receipt["confidence_recovery"]["enabled"] is True
    assert receipt["confidence_recovery"]["initial_rejected_low_confidence_count"] == 1
    assert receipt["confidence_recovery"]["remaining_rejected_low_confidence_count"] == 0
    assert receipt["status"] == "VERIFIED"
    assert len(enriched["relationships"]) == 1
    assert enriched["relationships"][0]["confidence"] == 0.64
    assert enriched["relationships"][0]["to"] == "api:POST:/orders/cancel"
