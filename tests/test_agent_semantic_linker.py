from __future__ import annotations

import pytest

from ai_test_asset_center.agent_semantic_linker import (
    AgentSemanticLinkerError,
    enrich_knowledge_asset_with_agent_relationships,
)
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir


class FakeAgentClient:
    def __init__(self, response: dict) -> None:
        self.response = response

    def complete_json(self, **_: object) -> dict:
        return self.response

    def usage_snapshot(self) -> dict[str, float]:
        return {"request_count": 1, "total_tokens": 120}


def _asset() -> dict:
    return {
        "asset_id": "ASSET-1",
        "rule_library": [{
            "rule_id": "rule-conservation",
            "statement": "The transferred quantity must be conserved.",
            "kind": "conservation",
            "risk_type": "data_conservation",
            "source_id": "prd-source",
        }],
        "interfaces": [{
            "interface_id": "api:POST:/transfers",
            "operation_id": "create_transfer",
            "method": "POST",
            "path": "/transfers",
            "summary": "Create a quantity transfer",
            "source_id": "api-source",
        }],
        "relationships": [],
    }


def test_agent_semantic_link_becomes_source_bound_runtime_obligation() -> None:
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(),
        client=FakeAgentClient({
            "relationships": [{
                "rule_id": "rule-conservation",
                "interface_id": "api:POST:/transfers",
                "confidence": 0.91,
                "reason": "The operation changes the quantity governed by the rule.",
            }],
        }),
    )

    assert receipt["status"] == "VERIFIED"
    assert receipt["accepted_relationship_count"] == 1
    relationship = enriched["relationships"][0]
    assert relationship["status"] == "accepted"
    assert relationship["derivation"] == "agent_semantic_mapping"
    assert relationship["from"] == "rule-conservation"
    assert relationship["to"] == "api:POST:/transfers"

    behavior_ir = build_behavior_ir_from_knowledge_asset(
        enriched,
        project_id="generic-project",
    )
    obligations = compile_obligations_from_behavior_ir(behavior_ir)["obligations"]
    assert any(row["risk_family"] == "conservation" for row in obligations)


def test_agent_semantic_linker_fails_on_invented_behavior_identity() -> None:
    with pytest.raises(AgentSemanticLinkerError, match="unknown_interface_id"):
        enrich_knowledge_asset_with_agent_relationships(
            _asset(),
            client=FakeAgentClient({
                "relationships": [{
                    "rule_id": "rule-conservation",
                    "interface_id": "api:POST:/invented",
                    "confidence": 0.99,
                    "reason": "invented",
                }],
            }),
        )
