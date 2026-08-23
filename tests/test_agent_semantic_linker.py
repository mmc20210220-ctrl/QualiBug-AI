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
        "transition_assessments": [{
            "transition_id": "st:state:transfer:allowed:pending:completed",
            "disposition": "LINKED",
            "reason": "The completion operation performs the declared transition.",
            "relationships": [{
                "interface_id": interface_id,
                "confidence": confidence,
                "reason": "This operation moves the transfer from PENDING to COMPLETED.",
                "evidence_refs": [
                    "st:state:transfer:allowed:pending:completed",
                    interface_id,
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
    assert receipt["accepted_relationship_count"] == 2
    assert receipt["assessed_rule_count"] == 1
    assert receipt["unassessed_rule_count"] == 0
    assert receipt["assessed_transition_count"] == 1
    assert receipt["unassessed_transition_count"] == 0
    relationships = enriched["relationships"]
    assert len(relationships) == 2
    relationship = relationships[0]
    assert relationship["status"] == "accepted"
    assert relationship["derivation"] == "agent_semantic_mapping"
    assert relationship["from"] == "rule-conservation"
    assert relationship["to"] == "api:POST:/transfers"
    assert relationship["evidence"]["supporting_fact_refs"] == [
        "rule-conservation",
        "api:POST:/transfers",
        "table:transfers",
    ]
    transition_relationship = relationships[1]
    assert transition_relationship["relation"] == "state_transition_to_interface"
    assert transition_relationship["from"] == "st:state:transfer:allowed:pending:completed"
    assert transition_relationship["to"] == "api:POST:/transfers"

    behavior_ir = build_behavior_ir_from_knowledge_asset(
        enriched,
        project_id="generic-project",
    )
    obligations = compile_obligations_from_behavior_ir(behavior_ir)["obligations"]
    assert any(row["risk_family"] == "conservation" for row in obligations)


def test_agent_semantic_linker_rejects_invented_behavior_identity_visibly() -> None:
    invented_response = _linked_response(
        interface_id="api:POST:/invented",
        evidence_refs=["rule-conservation", "api:POST:/invented"],
    )
    # The transition link stays on the documented interface so only the rule
    # assessment carries the invented identity.
    invented_response["transition_assessments"] = [{
        "transition_id": "st:state:transfer:allowed:pending:completed",
        "disposition": "LINKED",
        "reason": "The completion operation performs the declared transition.",
        "relationships": [{
            "interface_id": "api:POST:/transfers",
            "confidence": 0.91,
            "reason": "This operation moves the transfer from PENDING to COMPLETED.",
            "evidence_refs": [
                "st:state:transfer:allowed:pending:completed",
                "api:POST:/transfers",
            ],
        }],
    }]
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(),
        client=FakeAgentClient(invented_response),
    )

    assert [row["relation"] for row in enriched["relationships"]] == [
        "state_transition_to_interface"
    ]
    assert receipt["status"] == "VERIFIED_WITH_REJECTIONS"
    assert receipt["accepted_relationship_count"] == 1
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
    # State transitions now have a dedicated request; this test targets the
    # single rule request, so the asset carries no state machines.
    asset = _asset()
    asset["state_machines"] = []

    _, receipt = enrich_knowledge_asset_with_agent_relationships(
        asset,
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
    asset = _asset()
    asset["state_machines"] = []

    _, receipt = enrich_knowledge_asset_with_agent_relationships(
        asset,
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
    asset = _asset()
    asset["state_machines"] = []

    try:
        enrich_knowledge_asset_with_agent_relationships(asset, client=client)
    except AgentSemanticLinkerError as exc:
        assert "agent_semantic_provider_failed:ValueError" in str(exc)
        assert "agent_semantic_all_units_failed" in str(exc)
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
    assert "NO_EXECUTABLE_INTERFACE and AMBIGUOUS require relationships to be exactly an empty array" in prompt
    assert "if disposition is not LINKED, emit `relationships: []`" in prompt
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
        "transition_assessments": [{
            "transition_id": "st:state:transfer:allowed:pending:completed",
            "disposition": "NO_EXECUTABLE_INTERFACE",
            "reason": "No documented interface performs this transition.",
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
    assert receipt["no_executable_transition_count"] == 1
    assert receipt["unassessed_rule_count"] == 0
    assert receipt["rule_assessments"][0]["disposition"] == "NO_EXECUTABLE_INTERFACE"
    assert (
        receipt["transition_assessments"][0]["disposition"]
        == "NO_EXECUTABLE_INTERFACE"
    )


def test_agent_semantic_linker_rejects_invented_supporting_fact() -> None:
    invented_response = _linked_response(
        evidence_refs=[
            "rule-conservation",
            "api:POST:/transfers",
            "table:invented",
        ],
    )
    invented_response["transition_assessments"] = [{
        "transition_id": "st:state:transfer:allowed:pending:completed",
        "disposition": "LINKED",
        "reason": "The completion operation performs the declared transition.",
        "relationships": [{
            "interface_id": "api:POST:/transfers",
            "confidence": 0.91,
            "reason": "This operation moves the transfer from PENDING to COMPLETED.",
            "evidence_refs": [
                "st:state:transfer:allowed:pending:completed",
                "api:POST:/transfers",
                "table:invented",
            ],
        }],
    }]
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(),
        client=FakeAgentClient(invented_response),
    )

    assert enriched["relationships"] == []
    assert receipt["rejected_invalid_evidence_count"] == 2
    assert receipt["rejections"][0]["reason_code"] == "UNKNOWN_EVIDENCE_REF"


def test_linked_without_relationships_is_isolated_not_whole_batch_abort() -> None:
    # A single self-contradictory assessment (disposition=LINKED but empty
    # relationships) used to raise and abort the whole link set, degrading the
    # comprehension channel to source-only. It must now be receipted as an
    # isolated rejection while every other valid edge survives.
    response = _linked_response()
    response["assessments"] = [
        {
            "rule_id": "rule-conservation",
            "disposition": "LINKED",
            "reason": "The operation changes the quantity.",
            "relationships": [],  # self-contradictory
        },
        {
            "rule_id": "rule-conservation",
            "disposition": "LINKED",
            "reason": "The operation changes the quantity.",
            "relationships": [{
                "interface_id": "api:POST:/transfers",
                "confidence": 0.91,
                "reason": "The write carries the governed quantity.",
                "evidence_refs": [
                    "rule-conservation",
                    "api:POST:/transfers",
                    "table:transfers",
                ],
            }],
        },
    ]

    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(),
        client=FakeAgentClient(response),
    )

    # The valid transition edge and the valid rule edge both survive.
    assert receipt["accepted_relationship_count"] == 2
    assert receipt["rejected_inconsistent_disposition_count"] == 1
    assert any(
        row.get("reason_code") == "LINKED_WITHOUT_RELATIONSHIPS"
        for row in receipt["rejections"]
    )
    assert receipt["status"] == "VERIFIED_WITH_REJECTIONS"


# ---------------------------------------------------------------------------
# 输入预算守卫（20260821 成本事故根因修复）：per-call 预算传递 + 超限缩批/显式 BLOCKED
# ---------------------------------------------------------------------------

class TestLinkerInputBudget:
    def _long_asset(self, rule_count: int, statement_chars: int) -> dict:
        asset = _asset()
        rules = []
        for index in range(rule_count):
            rule = dict(asset["rule_library"][0])
            rule["rule_id"] = f"rule-budget-{index}"
            # prompt 行取 semantic_frame.behavior（_prompt_safe_text 上限 640 字符），
            # 单规则约 ~700 token；4 规则默认同批 ≈ 2800+ token。
            rule["semantic_frame"] = dict(rule["semantic_frame"])
            rule["semantic_frame"]["behavior"] = (
                "数量守恒约束必须在转账全流程严格成立，任何一方增减都必须可追溯。"
                * 40
            )
            rules.append(rule)
        asset["rule_library"] = rules
        return asset

    def test_budget_passed_to_client(self, monkeypatch):
        # 纯规则批场景（剔除 transition）：每个请求都必须携带声明的预算
        monkeypatch.setenv("LLM_LINKER_MAX_INPUT_TOKENS", "500000")
        client = FakeAgentClient(_linked_response())
        asset = _asset()
        asset.pop("state_machines", None)
        _, receipt = enrich_knowledge_asset_with_agent_relationships(
            asset, client=client,
        )
        assert receipt["input_budget"]["budget_tokens"] == 500000
        assert client.requests, "expected at least one provider call"
        assert all(
            req.get("max_input_tokens") == 500000 for req in client.requests
        )
        assert receipt["input_budget"]["budget_exhausted_unit_count"] == 0

    def test_tiny_budget_blocks_all_rule_units_without_any_call(self, monkeypatch):
        # 剔除 transition（其按设计走全局预算单请求路径），纯规则批场景下
        # 全部 unit 超限 → 显式 BLOCKED、零 provider 调用、整体 fail-fast。
        monkeypatch.setenv("LLM_LINKER_MAX_INPUT_TOKENS", "10")
        client = FakeAgentClient(_linked_response())
        asset = self._long_asset(rule_count=2, statement_chars=600)
        asset.pop("state_machines", None)
        with pytest.raises(AgentSemanticLinkerError) as exc_info:
            enrich_knowledge_asset_with_agent_relationships(
                asset, client=client,
            )
        assert client.requests == []
        assert "llm_input_budget_exhausted" in str(exc_info.value)

    def test_transition_path_keeps_global_budget_semantics(self, monkeypatch):
        # 语义明确化：预算 scope=rule_batches_only；transition 单元按设计是
        # ≤200 迁移的单次大请求（有独立分页权威契约），不套用规则批预算，
        # 但携带自己的声明式输入上限（实测巨型 transition prompt 两次烧穿
        # 5M token 运行预算，2026-08-23）——超限可见失败而非无界发送。
        monkeypatch.setenv("LLM_LINKER_MAX_INPUT_TOKENS", "10")
        client = FakeAgentClient(_linked_response())
        _, receipt = enrich_knowledge_asset_with_agent_relationships(
            _asset(), client=client,
        )
        assert receipt["input_budget"]["scope"] == "rule_batches_only"
        # 规则批全部被拦截……
        assert receipt["input_budget"]["budget_exhausted_unit_count"] >= 1
        # ……而 transition 仍发出唯一一次请求，但带自己的声明式上限
        assert len(client.requests) == 1
        assert client.requests[0].get("max_input_tokens") == 131072

    def test_moderate_budget_splits_batch_and_still_executes(self, monkeypatch):
        # 实测尺寸：4规则批≈9067 / 2规则≈4854 / 单规则≈2747 est tokens
        monkeypatch.setenv("LLM_LINKER_MAX_INPUT_TOKENS", "5000")
        client = FakeAgentClient(_linked_response())
        asset = self._long_asset(rule_count=4, statement_chars=600)
        enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
            asset, client=client,
        )
        budget_section = receipt["input_budget"]
        assert budget_section["budget_tokens"] == 5000
        # 默认 4 规则/批在此预算下必然拆分；拆分后单规则可执行
        assert budget_section["batches_split_count"] >= 1
        assert budget_section["batches_after_sizing"] > budget_section["batches_before_sizing"]
        assert budget_section["budget_exhausted_unit_count"] == 0
        # 规则批请求必须全部携带声明的预算；transition 请求（如有）按设计
        # 不携带 per-call 覆盖，走全局语义
        budgeted_requests = [
            req for req in client.requests
            if req.get("max_input_tokens") == 5000
        ]
        assert budgeted_requests, "rule batches must carry the declared budget"


# ---------------------------------------------------------------------------
# 链接结果资产化（Tier-0 资产原生复用）：指纹未变零 LLM，变更仅重链该规则
# ---------------------------------------------------------------------------

class TestLinkAssetReuse:
    def test_second_run_reuses_asset_links_with_zero_llm_calls(self):
        client = FakeAgentClient(_linked_response())
        asset = _asset()
        asset.pop("state_machines", None)
        enriched, first = enrich_knowledge_asset_with_agent_relationships(
            asset, client=client,
        )
        assert first["asset_reuse"]["hit_rule_count"] == 0
        assert len(client.requests) >= 1

        counting = _CountingAgentClient(_linked_response())
        _, second = enrich_knowledge_asset_with_agent_relationships(
            enriched, client=counting,
        )
        # 指纹未变：零 provider 调用，命中资产权威链接
        assert counting.complete_json_calls == 0
        assert second["asset_reuse"]["hit_rule_count"] == 1
        assert second["asset_reuse"]["miss_rule_count"] == 0
        assert second["asset_reuse"]["reused_rule_ids"] == ["rule-conservation"]
        # 权威边仍在富集结果中
        edges = [
            row for row in enriched.get("relationships", [])
            if row.get("source_id") == "agent_semantic_linker"
            and row.get("status") == "accepted"
        ]
        assert edges

    def test_rule_text_change_invalidates_only_that_rule(self):
        client = FakeAgentClient(_linked_response())
        asset = _asset()
        asset.pop("state_machines", None)
        enriched, _ = enrich_knowledge_asset_with_agent_relationships(
            asset, client=client,
        )
        mutated = dict(enriched)
        rules = [dict(row) for row in mutated["rule_library"]]
        rules[0]["semantic_frame"] = dict(rules[0]["semantic_frame"])
        rules[0]["semantic_frame"]["behavior"] = (
            "已变更的行为语义：库存扣减必须与出库单联动。"
        )
        mutated["rule_library"] = rules

        counting = _CountingAgentClient(_linked_response())
        _, receipt = enrich_knowledge_asset_with_agent_relationships(
            mutated, client=counting,
        )
        # 仅该规则重链；无其它规则可重链
        assert receipt["asset_reuse"]["hit_rule_count"] == 0
        assert receipt["asset_reuse"]["miss_rule_count"] == 1


class _CountingAgentClient(FakeAgentClient):
    def __init__(self, response: dict) -> None:
        super().__init__(response)
        self.complete_json_calls = 0

    def complete_json(self, **kwargs: object) -> dict:
        self.complete_json_calls += 1
        self.requests.append(dict(kwargs))
        return self.response
