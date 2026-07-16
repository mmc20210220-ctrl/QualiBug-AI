from __future__ import annotations

from http.client import IncompleteRead

from ai_test_asset_center.agent_semantic_linker import (
    AgentSemanticLinkerError,
    enrich_knowledge_asset_with_agent_relationships,
)
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.llm_reasoning import ReasoningClientError
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir


class FakeAgentClient:
    def __init__(self, response: dict) -> None:
        self.response = response

    def complete_json(self, **_: object) -> dict:
        return self.response

    def usage_snapshot(self) -> dict[str, float]:
        return {"request_count": 1, "total_tokens": 120}


class FlakyAgentClient(FakeAgentClient):
    def __init__(self, response: dict) -> None:
        super().__init__(response)
        self.calls = 0

    def complete_json(self, **_: object) -> dict:
        self.calls += 1
        if self.calls == 1:
            raise IncompleteRead(b"{", 32)
        return self.response


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


def test_agent_semantic_linker_rejects_invented_behavior_identity_visibly() -> None:
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
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

    assert enriched["relationships"] == []
    assert receipt["status"] == "VERIFIED_WITH_REJECTIONS"
    assert receipt["accepted_relationship_count"] == 0
    assert receipt["rejected_invalid_identity_count"] == 1
    assert receipt["rejected_proposal_count"] == 1
    assert receipt["rejections"] == [{
        "proposal_index": 0,
        "reason_code": "UNKNOWN_INTERFACE_ID",
        "proposal_fingerprint": receipt["rejections"][0]["proposal_fingerprint"],
    }]


def test_agent_semantic_linker_retries_one_transient_provider_read_failure() -> None:
    client = FlakyAgentClient({
        "relationships": [{
            "rule_id": "rule-conservation",
            "interface_id": "api:POST:/transfers",
            "confidence": 0.91,
            "reason": "The operation changes the quantity governed by the rule.",
        }],
    })

    _, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(),
        client=client,
    )

    assert client.calls == 2
    assert receipt["provider_attempt_count"] == 2
    assert receipt["provider_retry_count"] == 1


def test_agent_semantic_linker_retries_one_transient_json_parse_failure() -> None:
    class JsonParseFlakyClient(FlakyAgentClient):
        def complete_json(self, **_: object) -> dict:
            self.calls += 1
            if self.calls == 1:
                raise ReasoningClientError("LLM response did not include JSON content")
            return self.response

    client = JsonParseFlakyClient({
        "relationships": [{
            "rule_id": "rule-conservation",
            "interface_id": "api:POST:/transfers",
            "confidence": 0.91,
            "reason": "The operation changes the quantity governed by the rule.",
        }],
    })

    _, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(),
        client=client,
    )

    assert client.calls == 2
    assert receipt["provider_retry_count"] == 1


def test_agent_semantic_linker_does_not_retry_non_transient_provider_failure() -> None:
    class BadRequestClient(FakeAgentClient):
        def __init__(self) -> None:
            super().__init__({})
            self.calls = 0

        def complete_json(self, **_: object) -> dict:
            self.calls += 1
            raise ValueError("bad request")

    client = BadRequestClient()

    try:
        enrich_knowledge_asset_with_agent_relationships(_asset(), client=client)
    except AgentSemanticLinkerError as exc:
        assert "agent_semantic_provider_failed:ValueError" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected provider failure")
    assert client.calls == 1
