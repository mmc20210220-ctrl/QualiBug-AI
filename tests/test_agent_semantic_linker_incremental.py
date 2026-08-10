"""P0-2: incremental agent semantic linker (three-tier) behavior.

Covers the structural upgrade of ``agent_semantic_linker``:

* Tier 1 structured candidate recall is deterministic and never uses the LLM;
* Tier 2 sends "1 rule x candidate shortlist" and state transitions exactly
  once per asset instead of inside every rule batch;
* Tier 3 deterministic contract validation rejects non-candidate and invented
  interfaces;
* content-addressed caching re-issues model calls only when the rule, its
  candidates, its supporting facts, or the model configuration changed;
* batches succeed or fail independently; only an all-failed run degrades to
  source-only, and the degrade receipt stays granular.
"""
from __future__ import annotations

import os
from http.client import IncompleteRead

import pytest

from ai_test_asset_center import agent_semantic_linker as linker
from ai_test_asset_center import discovery_runtime_semantic_binding as binding
from ai_test_asset_center.agent_semantic_linker import (
    MAX_CANDIDATES_PER_RULE,
    MIN_CANDIDATES_PER_RULE,
    AgentSemanticLinkerError,
    enrich_knowledge_asset_with_agent_relationships,
)
from ai_test_asset_center.llm_reasoning import ReasoningConfig


class FakeAgentClient:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.requests: list[dict] = []

    def complete_json(self, **kwargs: object) -> dict:
        self.requests.append(dict(kwargs))
        return self.response

    def usage_snapshot(self) -> dict[str, float]:
        return {"request_count": 1, "total_tokens": 120}


class ConfigurableClient(FakeAgentClient):
    def __init__(self, response: dict, config: ReasoningConfig) -> None:
        super().__init__(response)
        self.config = config


def _rule(rule_id: str, statement: str, *, source_id: str = "prd-source") -> dict:
    return {
        "rule_id": rule_id,
        "statement": statement,
        "kind": "conservation",
        "source_id": source_id,
        "semantic_frame": {
            "modality": "REQUIRED",
            "polarity": "positive",
            "behavior": statement,
            "source_anchors": [],
        },
    }


def _order_asset(rule_ids: list[str] | None = None) -> dict:
    rule_ids = rule_ids or ["rule-0", "rule-1", "rule-2"]
    return {
        "asset_id": "ASSET-INC",
        "rule_library": [
            _rule(rule_id, f"The order total for {rule_id} must be computed.")
            for rule_id in rule_ids
        ],
        "interfaces": [
            {
                "interface_id": "api:GET:/orders",
                "method": "GET",
                "path": "/orders",
                "summary": "List orders with totals",
                "field_dictionary": ["total"],
                "source_id": "api-src",
            },
            {
                "interface_id": "api:POST:/orders",
                "method": "POST",
                "path": "/orders",
                "summary": "Create order with total",
                "field_dictionary": ["total"],
                "source_id": "api-src",
            },
            {
                "interface_id": "api:GET:/coupons",
                "method": "GET",
                "path": "/coupons",
                "summary": "List coupons",
                "field_dictionary": ["code"],
                "source_id": "api-src",
            },
        ],
        "data_tables": [
            {"table_id": "table:orders", "name": "orders", "columns": ["total"]}
        ],
        "relationships": [],
    }


def _linked_response(
    rule_ids: list[str] | None = None,
    interface_id: str = "api:GET:/orders",
) -> dict:
    rule_ids = rule_ids or ["rule-0", "rule-1", "rule-2"]
    return {
        "assessments": [
            {
                "rule_id": rule_id,
                "disposition": "LINKED",
                "reason": "The operation reads the order total.",
                "relationships": [{
                    "interface_id": interface_id,
                    "confidence": 0.9,
                    "reason": "This interface observes the order total.",
                    "evidence_refs": [rule_id, interface_id, "table:orders"],
                }],
            }
            for rule_id in rule_ids
        ]
    }


# ---------------------------------------------------------------------------
# Tier 1: deterministic structured candidate recall
# ---------------------------------------------------------------------------


def test_structured_recall_ranks_entity_and_field_channels_first() -> None:
    asset = _order_asset(["rule-order"])
    lexicon = linker._semantic_lexicon()
    signals = linker._build_asset_signals(asset, lexicon)
    interface_signals = linker._interface_signal_map(
        {row["interface_id"]: row for row in asset["interfaces"]},
        lexicon,
    )
    ctx = linker._rule_context(asset["rule_library"][0], signals, lexicon)

    candidate_ids, stats = linker._recall_candidate_interfaces(
        ctx, interface_signals, asset
    )

    assert "api:GET:/orders" in candidate_ids
    assert "api:POST:/orders" in candidate_ids
    assert "entity_token" in stats["channels"]["api:GET:/orders"]
    assert "schema_field_overlap" in stats["channels"]["api:GET:/orders"]
    assert len(candidate_ids) <= MAX_CANDIDATES_PER_RULE


def test_structured_recall_state_token_channel() -> None:
    asset = {
        "asset_id": "A",
        "rule_library": [
            _rule("rule-status", "A submitted order must be marked DISABLED.")
        ],
        "interfaces": [
            {
                "interface_id": "api:POST:/orders/disable",
                "method": "POST",
                "path": "/orders/disable",
                "summary": "Disable an order",
                "source_id": "api-src",
            },
            {
                "interface_id": "api:GET:/orders",
                "method": "GET",
                "path": "/orders",
                "summary": "List orders",
                "source_id": "api-src",
            },
        ],
        "relationships": [],
    }
    lexicon = linker._semantic_lexicon()
    signals = linker._build_asset_signals(asset, lexicon)
    interface_signals = linker._interface_signal_map(
        {row["interface_id"]: row for row in asset["interfaces"]},
        lexicon,
    )
    ctx = linker._rule_context(asset["rule_library"][0], signals, lexicon)

    candidate_ids, stats = linker._recall_candidate_interfaces(
        ctx, interface_signals, asset
    )

    assert "api:POST:/orders/disable" in candidate_ids
    assert "state_token" in stats["channels"]["api:POST:/orders/disable"]


def test_structured_recall_permission_matrix_channel() -> None:
    asset = {
        "asset_id": "A",
        "rule_library": [
            _rule("rule-admin", "Only the admin role may approve an order.")
        ],
        "interfaces": [
            {
                "interface_id": "api:POST:/orders/approve",
                "method": "POST",
                "path": "/orders/approve",
                "summary": "Approve an order",
                "source_id": "api-src",
            },
            {
                "interface_id": "api:GET:/orders",
                "method": "GET",
                "path": "/orders",
                "summary": "List orders",
                "source_id": "api-src",
            },
        ],
        "permission_matrix": [{
            "permission_id": "perm-admin-orders",
            "role": "admin",
            "resource": "order",
            "actions": ["approve"],
            "decision": "allow",
        }],
        "relationships": [],
    }
    lexicon = linker._semantic_lexicon()
    signals = linker._build_asset_signals(asset, lexicon)
    interface_signals = linker._interface_signal_map(
        {row["interface_id"]: row for row in asset["interfaces"]},
        lexicon,
    )
    ctx = linker._rule_context(asset["rule_library"][0], signals, lexicon)

    candidate_ids, stats = linker._recall_candidate_interfaces(
        ctx, interface_signals, asset
    )

    assert "api:POST:/orders/approve" in candidate_ids
    assert "permission_matrix" in stats["channels"]["api:POST:/orders/approve"]


def test_structured_recall_source_id_co_location() -> None:
    asset = {
        "asset_id": "A",
        "rule_library": [
            _rule("rule-src", "The balance must be zero after refund.",
                  source_id="doc:finance_section_3")
        ],
        "interfaces": [
            {
                "interface_id": "api:POST:/refunds",
                "method": "POST",
                "path": "/refunds",
                "summary": "Create refund",
                "source_id": "doc:finance_section_3",
            },
            {
                "interface_id": "api:GET:/orders",
                "method": "GET",
                "path": "/orders",
                "summary": "List orders",
                "source_id": "api-src",
            },
        ],
        "relationships": [],
    }
    lexicon = linker._semantic_lexicon()
    signals = linker._build_asset_signals(asset, lexicon)
    interface_signals = linker._interface_signal_map(
        {row["interface_id"]: row for row in asset["interfaces"]},
        lexicon,
    )
    ctx = linker._rule_context(asset["rule_library"][0], signals, lexicon)

    candidate_ids, stats = linker._recall_candidate_interfaces(
        ctx, interface_signals, asset
    )

    assert "api:POST:/refunds" in candidate_ids
    assert "source_id_co_location" in stats["channels"]["api:POST:/refunds"]


def test_structured_recall_entity_relation_expansion() -> None:
    asset = {
        "asset_id": "A",
        "rule_library": [
            _rule("rule-rel", "The refund amount must equal the payment amount.")
        ],
        "interfaces": [
            {
                "interface_id": "api:GET:/payments",
                "method": "GET",
                "path": "/payments",
                "summary": "List payments",
                "source_id": "api-src",
            },
            {
                "interface_id": "api:GET:/orders",
                "method": "GET",
                "path": "/orders",
                "summary": "List orders",
                "source_id": "api-src",
            },
        ],
        "entity_relations": [{
            "from_entity": "refund",
            "to_entity": "payment",
            "relation_type": "references",
        }],
        "relationships": [],
    }
    lexicon = linker._semantic_lexicon()
    signals = linker._build_asset_signals(asset, lexicon)
    interface_signals = linker._interface_signal_map(
        {row["interface_id"]: row for row in asset["interfaces"]},
        lexicon,
    )
    ctx = linker._rule_context(asset["rule_library"][0], signals, lexicon)

    candidate_ids, stats = linker._recall_candidate_interfaces(
        ctx, interface_signals, asset
    )

    # refund (rule) -> payment (relation endpoint) recalls the payments
    # interface without the rule ever mentioning "payment".
    assert "api:GET:/payments" in candidate_ids
    assert "entity_relation" in stats["channels"]["api:GET:/payments"]


def test_structured_recall_falls_back_on_sparse_rule() -> None:
    asset = {
        "asset_id": "A",
        "rule_library": [
            _rule("rule-sparse", "zzqxwv ytrpqm must be kept.")
        ],
        "interfaces": [
            {
                "interface_id": "api:GET:/orders",
                "method": "GET",
                "path": "/orders",
                "summary": "List orders",
                "source_id": "api-src",
            },
            {
                "interface_id": "api:GET:/coupons",
                "method": "GET",
                "path": "/coupons",
                "summary": "List coupons",
                "source_id": "api-src",
            },
        ],
        "relationships": [],
    }
    lexicon = linker._semantic_lexicon()
    signals = linker._build_asset_signals(asset, lexicon)
    interface_signals = linker._interface_signal_map(
        {row["interface_id"]: row for row in asset["interfaces"]},
        lexicon,
    )
    ctx = linker._rule_context(asset["rule_library"][0], signals, lexicon)

    candidate_ids, stats = linker._recall_candidate_interfaces(
        ctx, interface_signals, asset
    )

    assert len(candidate_ids) >= min(MIN_CANDIDATES_PER_RULE, 2)
    assert stats["fallback"] is True
    assert any(
        "global_fallback" in channels
        for channels in stats["channels"].values()
    )


def test_structured_recall_respects_candidate_cap() -> None:
    interfaces = [
        {
            "interface_id": f"api:GET:/orders/sub{i:02d}",
            "method": "GET",
            "path": f"/orders/sub{i:02d}",
            "summary": f"Order subresource {i}",
            "field_dictionary": ["total"],
            "source_id": "api-src",
        }
        for i in range(20)
    ]
    asset = {
        "asset_id": "A",
        "rule_library": [_rule("rule-cap", "The order total must be computed.")],
        "interfaces": interfaces,
        "data_tables": [
            {"table_id": "table:orders", "name": "orders", "columns": ["total"]}
        ],
        "relationships": [],
    }
    lexicon = linker._semantic_lexicon()
    signals = linker._build_asset_signals(asset, lexicon)
    interface_signals = linker._interface_signal_map(
        {row["interface_id"]: row for row in asset["interfaces"]},
        lexicon,
    )
    ctx = linker._rule_context(asset["rule_library"][0], signals, lexicon)

    candidate_ids, _stats = linker._recall_candidate_interfaces(
        ctx, interface_signals, asset
    )

    assert len(candidate_ids) == MAX_CANDIDATES_PER_RULE
    assert len(candidate_ids) == len(set(candidate_ids))


# ---------------------------------------------------------------------------
# Content-addressed cache
# ---------------------------------------------------------------------------


def test_cache_hit_skips_provider_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    cache_dir = str(linker.Path(linker.__file__).parent.parent / ".scratch" / "p02_cache_test")
    monkeypatch.setenv(linker.CACHE_DIRECTORY_ENV, cache_dir)
    try:
        first = FakeAgentClient(_linked_response())
        _, receipt_first = enrich_knowledge_asset_with_agent_relationships(
            _order_asset(),
            client=first,
        )
        assert receipt_first["cache"]["miss_count"] == 3
        assert receipt_first["cache"]["hit_count"] == 0

        second = FakeAgentClient(_linked_response())
        _, receipt_second = enrich_knowledge_asset_with_agent_relationships(
            _order_asset(),
            client=second,
        )
        assert second.requests == []
        assert receipt_second["cache"]["hit_count"] == 3
        assert receipt_second["cache"]["miss_count"] == 0
        assert receipt_second["accepted_relationship_count"] == 3
    finally:
        monkeypatch.delenv(linker.CACHE_DIRECTORY_ENV, raising=False)
        import shutil

        shutil.rmtree(cache_dir, ignore_errors=True)


def test_cache_invalidates_when_rule_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    cache_dir = str(
        linker.Path(linker.__file__).parent.parent / ".scratch" / "p02_cache_rule"
    )
    monkeypatch.setenv(linker.CACHE_DIRECTORY_ENV, cache_dir)
    try:
        first = FakeAgentClient(_linked_response())
        enrich_knowledge_asset_with_agent_relationships(
            _order_asset(),
            client=first,
        )
        changed = _order_asset()
        changed["rule_library"][0]["statement"] = "The coupon code must be unique."
        changed["rule_library"][0]["semantic_frame"]["behavior"] = (
            "The coupon code must be unique."
        )
        second = FakeAgentClient(_linked_response())
        _, receipt = enrich_knowledge_asset_with_agent_relationships(
            changed,
            client=second,
        )
        assert len(second.requests) == 1  # only the changed rule is re-sent
        assert receipt["cache"]["hit_count"] == 2
        assert receipt["cache"]["miss_count"] == 1
    finally:
        monkeypatch.delenv(linker.CACHE_DIRECTORY_ENV, raising=False)
        import shutil

        shutil.rmtree(cache_dir, ignore_errors=True)


def test_cache_invalidates_when_interface_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_dir = str(
        linker.Path(linker.__file__).parent.parent / ".scratch" / "p02_cache_iface"
    )
    monkeypatch.setenv(linker.CACHE_DIRECTORY_ENV, cache_dir)
    try:
        first = FakeAgentClient(_linked_response())
        enrich_knowledge_asset_with_agent_relationships(
            _order_asset(),
            client=first,
        )
        changed = _order_asset()
        changed["interfaces"][0]["path"] = "/orders/v2"
        second = FakeAgentClient(_linked_response())
        _, receipt = enrich_knowledge_asset_with_agent_relationships(
            changed,
            client=second,
        )
        assert receipt["cache"]["hit_count"] == 0
        assert receipt["cache"]["miss_count"] == 3
    finally:
        monkeypatch.delenv(linker.CACHE_DIRECTORY_ENV, raising=False)
        import shutil

        shutil.rmtree(cache_dir, ignore_errors=True)


def test_cache_invalidates_when_supporting_fact_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_dir = str(
        linker.Path(linker.__file__).parent.parent / ".scratch" / "p02_cache_fact"
    )
    monkeypatch.setenv(linker.CACHE_DIRECTORY_ENV, cache_dir)
    try:
        first = FakeAgentClient(_linked_response())
        enrich_knowledge_asset_with_agent_relationships(
            _order_asset(),
            client=first,
        )
        changed = _order_asset()
        changed["data_tables"][0]["columns"] = ["total", "discount"]
        second = FakeAgentClient(_linked_response())
        _, receipt = enrich_knowledge_asset_with_agent_relationships(
            changed,
            client=second,
        )
        assert receipt["cache"]["hit_count"] == 0
        assert receipt["cache"]["miss_count"] == 3
    finally:
        monkeypatch.delenv(linker.CACHE_DIRECTORY_ENV, raising=False)
        import shutil

        shutil.rmtree(cache_dir, ignore_errors=True)


def test_cache_invalidates_when_model_config_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_dir = str(
        linker.Path(linker.__file__).parent.parent / ".scratch" / "p02_cache_model"
    )
    monkeypatch.setenv(linker.CACHE_DIRECTORY_ENV, cache_dir)
    config_a = ReasoningConfig(
        base_url="https://provider.example/v1",
        api_key="key",
        model="model-a",
        temperature=0.0,
    )
    config_b = ReasoningConfig(
        base_url="https://provider.example/v1",
        api_key="key",
        model="model-b",
        temperature=0.0,
    )
    try:
        first = ConfigurableClient(_linked_response(), config_a)
        enrich_knowledge_asset_with_agent_relationships(
            _order_asset(),
            client=first,
        )
        second = ConfigurableClient(_linked_response(), config_b)
        _, receipt = enrich_knowledge_asset_with_agent_relationships(
            _order_asset(),
            client=second,
        )
        assert receipt["cache"]["hit_count"] == 0
        assert receipt["cache"]["miss_count"] == 3
    finally:
        monkeypatch.delenv(linker.CACHE_DIRECTORY_ENV, raising=False)
        import shutil

        shutil.rmtree(cache_dir, ignore_errors=True)


def test_transition_unit_is_cached_across_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_dir = str(
        linker.Path(linker.__file__).parent.parent / ".scratch" / "p02_cache_trans"
    )
    monkeypatch.setenv(linker.CACHE_DIRECTORY_ENV, cache_dir)
    asset = _order_asset(["rule-0"])
    asset["state_machines"] = [{
        "state_machine_id": "state:order",
        "object": "order",
        "states": ["DRAFT", "COMPLETED"],
        "transitions": [{"from": "DRAFT", "to": "COMPLETED"}],
    }]
    response = {
        "assessments": _linked_response(["rule-0"])["assessments"],
        "transition_assessments": [{
            "transition_id": "st:state:order:allowed:draft:completed",
            "disposition": "LINKED",
            "reason": "The completion operation performs the transition.",
            "relationships": [{
                "interface_id": "api:GET:/orders",
                "confidence": 0.9,
                "reason": "Observes the completed order.",
                "evidence_refs": [
                    "st:state:order:allowed:draft:completed",
                    "api:GET:/orders",
                ],
            }],
        }],
    }
    try:
        first = FakeAgentClient(response)
        _, receipt_first = enrich_knowledge_asset_with_agent_relationships(
            asset,
            client=first,
        )
        assert len(first.requests) == 2  # rule request + transition request
        assert receipt_first["transition_request_count"] == 1

        second = FakeAgentClient(response)
        _, receipt_second = enrich_knowledge_asset_with_agent_relationships(
            asset,
            client=second,
        )
        assert second.requests == []
        assert receipt_second["cache"]["transition_cache_hit"] is True
        assert receipt_second["assessed_transition_count"] == 1
    finally:
        monkeypatch.delenv(linker.CACHE_DIRECTORY_ENV, raising=False)
        import shutil

        shutil.rmtree(cache_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tier 2: state transitions are sent exactly once
# ---------------------------------------------------------------------------


def test_state_transitions_sent_once_not_per_rule_batch() -> None:
    # 45 rules -> 2 rule batches; transitions must appear in exactly one
    # dedicated request instead of inside every rule batch.
    rule_ids = [f"rule-{index}" for index in range(45)]
    asset = _order_asset(rule_ids)
    asset["state_machines"] = [{
        "state_machine_id": "state:order",
        "object": "order",
        "states": ["DRAFT", "COMPLETED"],
        "transitions": [{"from": "DRAFT", "to": "COMPLETED"}],
    }]
    response = {
        "assessments": _linked_response(rule_ids)["assessments"],
        "transition_assessments": [{
            "transition_id": "st:state:order:allowed:draft:completed",
            "disposition": "LINKED",
            "reason": "The completion operation performs the transition.",
            "relationships": [{
                "interface_id": "api:GET:/orders",
                "confidence": 0.9,
                "reason": "Observes the completed order.",
                "evidence_refs": [
                    "st:state:order:allowed:draft:completed",
                    "api:GET:/orders",
                ],
            }],
        }],
    }
    client = FakeAgentClient(response)
    _, receipt = enrich_knowledge_asset_with_agent_relationships(
        asset,
        client=client,
    )

    assert len(client.requests) == 3  # 2 rule batches + 1 transition request
    transition_prompts = [
        request for request in client.requests
        if "state_transitions_to_assess" in str(request["user_prompt"])
    ]
    assert len(transition_prompts) == 1
    assert receipt["transition_request_count"] == 1
    assert receipt["accepted_relationship_count"] == 46


# ---------------------------------------------------------------------------
# Tier 3: deterministic contract validation on the candidate shortlist
# ---------------------------------------------------------------------------


def test_non_candidate_interface_is_rejected_visibly() -> None:
    interfaces = [
        {
            "interface_id": f"api:GET:/orders/sub{i:02d}",
            "method": "GET",
            "path": f"/orders/sub{i:02d}",
            "summary": f"Order subresource {i}",
            "field_dictionary": ["total"],
            "source_id": "api-src",
        }
        for i in range(15)
    ]
    interfaces.append({
        "interface_id": "api:GET:/coupons",
        "method": "GET",
        "path": "/coupons",
        "summary": "List coupons",
        "field_dictionary": ["code"],
        "source_id": "api-src",
    })
    asset = {
        "asset_id": "A",
        "rule_library": [_rule("rule-order", "The order total must be computed.")],
        "interfaces": interfaces,
        "data_tables": [
            {"table_id": "table:orders", "name": "orders", "columns": ["total"]}
        ],
        "relationships": [],
    }
    response = {
        "assessments": [{
            "rule_id": "rule-order",
            "disposition": "LINKED",
            "reason": "x",
            "relationships": [{
                "interface_id": "api:GET:/coupons",
                "confidence": 0.9,
                "reason": "coupon link",
                "evidence_refs": ["rule-order", "api:GET:/coupons", "table:orders"],
            }],
        }]
    }
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        asset,
        client=FakeAgentClient(response),
    )

    assert enriched["relationships"] == []
    assert receipt["rejected_non_candidate_count"] == 1
    assert receipt["rejections"][0]["reason_code"] == "NON_CANDIDATE_INTERFACE"
    assert receipt["status"] == "VERIFIED_WITH_REJECTIONS"


def test_prompt_restricts_links_to_candidate_shortlist() -> None:
    client = FakeAgentClient(_linked_response())
    enrich_knowledge_asset_with_agent_relationships(
        _order_asset(["rule-0"]),
        client=client,
    )
    prompt = str(client.requests[0]["user_prompt"])
    assert "Link ONLY interfaces listed in that rule's candidate_interfaces" in prompt
    assert "never link an interface that is not listed" in prompt
    assert '"candidate_interfaces"' in prompt
    assert "state_transitions_to_assess" not in prompt


# ---------------------------------------------------------------------------
# Batch independence and degradation granularity
# ---------------------------------------------------------------------------


class _PartialFailClient:
    """Fails the first rule batch, links two rules of the second batch."""

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, **kwargs: object) -> dict:
        self.calls += 1
        prompt = str(kwargs["user_prompt"])
        if "rule-0" in prompt and self.calls == 1:
            raise ValueError("provider down for batch 1")
        if "rule-40" in prompt:
            return {
                "assessments": [
                    _linked_response(["rule-40", "rule-41"])["assessments"][0],
                    _linked_response(["rule-40", "rule-41"])["assessments"][1],
                ]
            }
        return {"assessments": []}

    def usage_snapshot(self) -> dict[str, float]:
        return {}


def test_failed_batch_does_not_discard_successful_batch() -> None:
    rule_ids = [f"rule-{index}" for index in range(45)]
    asset = _order_asset(rule_ids)
    client = _PartialFailClient()

    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        asset,
        client=client,
    )

    assert [row["from"] for row in enriched["relationships"]] == [
        "rule-40",
        "rule-41",
    ]
    assert receipt["accepted_relationship_count"] == 2
    assert receipt["failed_unit_count"] == 1
    assert receipt["failed_units"][0]["unit_kind"] == "rule_batch"
    assert receipt["failed_units"][0]["reason_code"] == "provider_failure"
    assert receipt["unassessed_rule_count"] == 43
    assert receipt["batch_count"] == 2


def test_all_units_failed_raises_and_wrapper_receipt_stays_granular() -> None:
    class AlwaysFailClient:
        def complete_json(self, **_: object) -> dict:
            raise ValueError("provider totally down")

        def usage_snapshot(self) -> dict[str, float]:
            return {}

    asset = _order_asset(["rule-0"])

    with pytest.raises(AgentSemanticLinkerError) as excinfo:
        enrich_knowledge_asset_with_agent_relationships(
            asset,
            client=AlwaysFailClient(),
        )
    assert "agent_semantic_all_units_failed" in str(excinfo.value)
    assert "agent_semantic_provider_failed:ValueError" in str(excinfo.value)
    assert ":units_failed=" in str(excinfo.value)

    preserved, receipt = binding._agent_semantic_linker_with_visible_failure(
        asset,
        client=AlwaysFailClient(),
    )
    assert receipt["status"] == "FAILED"
    assert receipt["semantic_linking_degraded_to_source_only"] is True
    assert receipt["failed_unit_count"] == 1
    assert preserved["agent_semantic_link_receipt"]["status"] == "FAILED"


def test_all_units_failed_message_reports_unit_count() -> None:
    class AlwaysFailClient:
        def complete_json(self, **_: object) -> dict:
            raise ValueError("provider totally down")

        def usage_snapshot(self) -> dict[str, float]:
            return {}

    asset = _order_asset(["rule-0"])
    asset["state_machines"] = [{
        "state_machine_id": "state:order",
        "object": "order",
        "states": ["DRAFT", "COMPLETED"],
        "transitions": [{"from": "DRAFT", "to": "COMPLETED"}],
    }]

    with pytest.raises(AgentSemanticLinkerError) as excinfo:
        enrich_knowledge_asset_with_agent_relationships(
            asset,
            client=AlwaysFailClient(),
        )
    # Both the rule batch and the transition unit failed.
    assert ":units_failed=2" in str(excinfo.value)
