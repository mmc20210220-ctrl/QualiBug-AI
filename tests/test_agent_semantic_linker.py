from __future__ import annotations

from http.client import IncompleteRead

import pytest

from ai_test_asset_center.agent_semantic_linker import (
    AgentSemanticLinkerError,
    _default_client,
    enrich_knowledge_asset_with_agent_relationships,
)
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.llm_reasoning import ReasoningClientError, ReasoningConfig
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir


class FakeAgentClient:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.requests: list[dict] = []

    def complete_json(self, **kwargs: object) -> dict:
        self.requests.append(dict(kwargs))
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
            "semantic_frame": {
                "schema_version": "qualibug.business-semantic-frame.v1",
                "modality": "REQUIRED",
                "polarity": "positive",
                "condition": "",
                "behavior": "The transferred quantity must be conserved.",
                "source_anchors": [],
                "source_grounded": True,
            },
        }],
        "interfaces": [{
            "interface_id": "api:POST:/transfers",
            "operation_id": "create_transfer",
            "method": "POST",
            "path": "/transfers",
            "summary": "Create a quantity transfer",
            "field_dictionary": ["source_id", "target_id", "quantity"],
            "source_id": "api-source",
        }],
        "data_tables": [{
            "table_id": "table:transfers",
            "name": "transfers",
            "columns": ["source_id", "target_id", "quantity"],
        }],
        "state_machines": [{
            "state_machine_id": "state:transfer",
            "object": "transfer",
            "states": ["PENDING", "COMPLETED"],
            "transitions": [{"from": "PENDING", "to": "COMPLETED"}],
        }],
        "relationships": [],
    }


def _linked_response(
    *,
    rule_id: str = "rule-conservation",
    interface_id: str = "api:POST:/transfers",
    confidence: float = 0.91,
    evidence_refs: list[str] | None = None,
) -> dict:
    return {
        "assessments": [{
            "rule_id": rule_id,
            "disposition": "LINKED",
            "reason": "The operation changes the quantity governed by the rule.",
            "relationships": [{
                "interface_id": interface_id,
                "confidence": confidence,
                "reason": "The write carries the governed quantity.",
                "evidence_refs": evidence_refs or [
                    rule_id,
                    interface_id,
                    "table:transfers",
                ],
            }],
        }],
    }


def test_agent_semantic_link_becomes_source_bound_runtime_obligation() -> None:
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(),
        client=FakeAgentClient(_linked_response()),
    )

    assert receipt["status"] == "VERIFIED"
    assert receipt["accepted_relationship_count"] == 1
    assert receipt["assessed_rule_count"] == 1
    assert receipt["unassessed_rule_count"] == 0
    relationship = enriched["relationships"][0]
    assert relationship["status"] == "accepted"
    assert relationship["derivation"] == "agent_semantic_mapping"
    assert relationship["from"] == "rule-conservation"
    assert relationship["to"] == "api:POST:/transfers"
    assert relationship["evidence"]["supporting_fact_refs"] == [
        "rule-conservation",
        "api:POST:/transfers",
        "table:transfers",
    ]

    behavior_ir = build_behavior_ir_from_knowledge_asset(
        enriched,
        project_id="generic-project",
    )
    obligations = compile_obligations_from_behavior_ir(behavior_ir)["obligations"]
    assert any(row["risk_family"] == "conservation" for row in obligations)


def test_agent_semantic_linker_rejects_invented_behavior_identity_visibly() -> None:
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(),
        client=FakeAgentClient(_linked_response(
            interface_id="api:POST:/invented",
            evidence_refs=["rule-conservation", "api:POST:/invented"],
        )),
    )

    assert enriched["relationships"] == []
    assert receipt["status"] == "VERIFIED_WITH_REJECTIONS"
    assert receipt["accepted_relationship_count"] == 0
    assert receipt["rejected_invalid_identity_count"] == 1
    assert receipt["rejected_proposal_count"] == 1
    assert receipt["rejections"] == [{
        "assessment_index": 0,
        "relationship_index": 0,
        "reason_code": "UNKNOWN_INTERFACE_ID",
        "proposal_fingerprint": receipt["rejections"][0]["proposal_fingerprint"],
    }]


def test_agent_semantic_linker_retries_one_transient_provider_read_failure() -> None:
    client = FlakyAgentClient(_linked_response())

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

    client = JsonParseFlakyClient(_linked_response())

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


def test_agent_semantic_linker_uses_deterministic_provider_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ReasoningConfig(
        base_url="https://example.invalid/v1",
        api_key="test-key",
        model="test-model",
        temperature=0.7,
    )
    monkeypatch.setattr(
        ReasoningConfig,
        "from_env",
        classmethod(lambda cls: config),
    )

    client = _default_client()

    assert client.config.temperature == 0.0
    assert client.config.timeout_seconds >= 300
    assert client.config.max_tokens >= 32768


def test_agent_semantic_linker_sends_expert_context_without_credentials() -> None:
    asset = _asset()
    asset["rule_library"][0]["statement"] = (
        "buyer@example.com | Test@123456 | status is DISABLED, cannot submit"
    )
    client = FakeAgentClient(_linked_response())

    enrich_knowledge_asset_with_agent_relationships(asset, client=client)

    prompt = str(client.requests[0]["user_prompt"])
    assert "business semantic model" in prompt
    assert "state:transfer" in prompt
    assert "table:transfers" in prompt
    assert "semantic_frame" in prompt
    assert "buyer@example.com" not in prompt
    assert "Test@123456" not in prompt


def test_agent_semantic_linker_includes_source_grounded_entity_relations() -> None:
    asset = _asset()
    asset["entity_relations"] = [{
        "from_entity": "transfer",
        "to_entity": "account",
        "relation_type": "belongs_to",
        "source_id": "schema-source",
    }]
    client = FakeAgentClient(_linked_response())

    enrich_knowledge_asset_with_agent_relationships(asset, client=client)

    prompt = str(client.requests[0]["user_prompt"])
    assert "entity_relations" in prompt
    assert "belongs_to" in prompt
    assert "fact:entity_relation:" in prompt


@pytest.mark.parametrize(
    ("collection", "id_key"),
    [
        ("rule_library", "rule_id"),
        ("interfaces", "interface_id"),
    ],
)
def test_agent_semantic_linker_fails_closed_on_duplicate_input_identity(
    collection: str,
    id_key: str,
) -> None:
    asset = _asset()
    asset[collection].append(dict(asset[collection][0]))

    with pytest.raises(
        AgentSemanticLinkerError,
        match=rf"agent_semantic_duplicate_identity:{collection}:"
              rf"{asset[collection][0][id_key]}",
    ):
        enrich_knowledge_asset_with_agent_relationships(
            asset,
            client=FakeAgentClient(_linked_response()),
        )


def test_agent_semantic_linker_records_explicit_unlinked_assessment() -> None:
    client = FakeAgentClient({
        "assessments": [{
            "rule_id": "rule-conservation",
            "disposition": "NO_EXECUTABLE_INTERFACE",
            "reason": "No documented interface can exercise the rule.",
            "relationships": [],
        }],
    })

    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(),
        client=client,
    )

    assert enriched["relationships"] == []
    assert receipt["status"] == "VERIFIED_WITH_GAPS"
    assert receipt["accepted_relationship_count"] == 0
    assert receipt["no_executable_interface_count"] == 1
    assert receipt["unassessed_rule_count"] == 0
    assert receipt["rule_assessments"][0]["disposition"] == "NO_EXECUTABLE_INTERFACE"


def test_agent_semantic_linker_rejects_invented_supporting_fact() -> None:
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(),
        client=FakeAgentClient(_linked_response(
            evidence_refs=[
                "rule-conservation",
                "api:POST:/transfers",
                "table:invented",
            ],
        )),
    )

    assert enriched["relationships"] == []
    assert receipt["rejected_invalid_evidence_count"] == 1
    assert receipt["rejections"][0]["reason_code"] == "UNKNOWN_EVIDENCE_REF"
