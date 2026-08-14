"""Grounded fact retrieval (B4 reasoner toolization) + hypothesis model
provenance (A1) + tier routing (A3).

All paths under test are deterministic; LLM transports are mocked or avoided.
"""

from __future__ import annotations

from ai_test_asset_center import llm_reasoning as L
from ai_test_asset_center.reasoning_fact_retrieval import (
    MAX_BLOCK_CHARS,
    retrieve_grounded_facts,
)


# ---------------------------------------------------------------------------
# B4: grounded fact retrieval
# ---------------------------------------------------------------------------

_SAMPLE_PAYLOAD = {
    "business_rules": [
        {
            "normalized_text": "订单总额必须等于明细行金额之和",
            "rule_origin": "explicit",
            "source_refs": [{"kind": "doc", "locator": "PRD-§3.2"}],
        },
        {
            "normalized_text": "库存数量不得为负",
            "rule_origin": "inferred",
            "source_refs": [{"kind": "doc", "locator": "PRD-§9"}],
        },
        {
            "normalized_text": "退款金额不得超过原支付金额",
            "source_refs": [{"kind": "api", "locator": "API_SPEC#refunds"}],
        },
    ],
    "state_machines": [
        {
            "object": "order",
            "states": ["created", "paid", "shipped", "completed"],
            "transitions": [
                {"from": "created", "to": "paid"},
                {"from": "paid", "to": "shipped"},
            ],
        },
    ],
    "relations": [
        {"from_object": "order", "to_object": "payment", "relationship_type": "paid_by"},
    ],
    "entities": [
        {"name": "order", "fields": [{"name": "total"}, {"name": "status"}]},
    ],
}


def test_retrieval_keeps_explicit_and_unmarked_rules_with_source_refs() -> None:
    block, receipt = retrieve_grounded_facts(_SAMPLE_PAYLOAD)
    assert receipt["status"] == "CONSUMED"
    assert receipt["facts"] >= 4
    assert "订单总额必须等于明细行金额之和" in block
    assert "退款金额不得超过原支付金额" in block
    assert "(source: doc:PRD-§3.2)" in block
    # Inferred rules are attention guidance, not grounded facts.
    assert "库存数量不得为负" not in block


def test_retrieval_includes_state_machines_relations_and_entities() -> None:
    block, _ = retrieve_grounded_facts(_SAMPLE_PAYLOAD)
    assert "created->paid" in block
    assert "paid->shipped" in block
    assert "order -paid_by-> payment" in block
    assert "entity=order" in block
    assert "fields=total,status" in block


def test_retrieval_fail_soft_and_bounded() -> None:
    block, receipt = retrieve_grounded_facts(None)
    assert block == ""
    assert receipt["status"] == "SKIPPED"

    block, receipt = retrieve_grounded_facts({"entities": []})
    assert block == ""
    assert receipt["status"] == "EMPTY"

    # Bounded output regardless of payload size.
    big = {
        "business_rules": [
            {"normalized_text": f"规则 {i}" * 50, "source_refs": []} for i in range(200)
        ]
    }
    block, receipt = retrieve_grounded_facts(big)
    assert receipt["chars"] <= MAX_BLOCK_CHARS


def test_retrieval_reads_world_model_documented_rules_and_relationships() -> None:
    # The world-model projection emits ``documented_rules`` (verbatim field
    # ``rule``) and ``relationships`` (``from_entity``/``to_entity``), which
    # differ from the model-dict keys.  The source-anchored fact block must
    # consume both, or the comprehension bridge silently starves the reasoner
    # of its declared rules and relations.
    payload = {
        "documented_rules": [
            {
                "rule": "订单支付前不得发货",
                "source": "src:prd@PRD.md#订单",
                "entities_involved": ["订单", "支付", "发货"],
                "is_verifiable": True,
                "severity": "P0",
            },
        ],
        "relationships": [
            {"from_entity": "orders", "to_entity": "users", "relationship_type": "belongs_to"},
        ],
    }
    block, receipt = retrieve_grounded_facts(payload)
    assert receipt["status"] == "CONSUMED"
    assert "订单支付前不得发货" in block
    assert "(source: src:prd@PRD.md#订单)" in block
    assert "orders -belongs_to-> users" in block


def test_retrieval_redacts_credentials() -> None:
    block, _ = retrieve_grounded_facts(
        {"business_rules": [{"normalized_text": "用 bearer sk-abc12345 连接", "source_refs": []}]}
    )
    assert "sk-abc12345" not in block
    assert "[REDACTED]" in block


# ---------------------------------------------------------------------------
# A1: hypothesis model provenance
# ---------------------------------------------------------------------------


class _FakeCfg:
    def __init__(self, model: str, temperature: float = 0.1):
        self.model = model
        self.temperature = temperature
        self.base_url = "http://x"
        self.api_key = "k"
        self.model_light = ""
        self.embedding_model = ""
        self.enabled = True


class _FakeClient:
    def __init__(self, cfg: _FakeCfg):
        self.config = cfg


def test_hypothesis_provenance_binds_model_and_is_content_addressed() -> None:
    raw = [
        {
            "rule": "conservation",
            "severity": "P1",
            "title": "总额与明细不一致",
            "expected": "sum==total",
            "confidence": 0.7,
        }
    ]
    first = L.compile_unverified_semantic_hypotheses(
        raw, engine="causality", type_field="causality_type",
        client=_FakeClient(_FakeCfg("deepseek-v4")),
    )
    assert first[0]["model_id"] == "deepseek-v4"
    assert first[0]["temperature"] == "0.1"
    assert first[0]["prompt_template_hash"]

    # Same model + template -> reproducible hypothesis id.
    replay = L.compile_unverified_semantic_hypotheses(
        raw, engine="causality", type_field="causality_type",
        client=_FakeClient(_FakeCfg("deepseek-v4")),
    )
    assert replay[0]["hypothesis_id"] == first[0]["hypothesis_id"]

    # Different model or temperature -> different id (content-addressed).
    other_model = L.compile_unverified_semantic_hypotheses(
        raw, engine="causality", type_field="causality_type",
        client=_FakeClient(_FakeCfg("deepseek-v4-strong")),
    )
    assert other_model[0]["hypothesis_id"] != first[0]["hypothesis_id"]
    other_temp = L.compile_unverified_semantic_hypotheses(
        raw, engine="causality", type_field="causality_type",
        client=_FakeClient(_FakeCfg("deepseek-v4", temperature=0.9)),
    )
    assert other_temp[0]["hypothesis_id"] != first[0]["hypothesis_id"]


def test_oracle_hypotheses_carry_provenance() -> None:
    hypotheses = L.compile_oracle_hypotheses(
        prd_text="x",
        api_schema="{}",
        heuristic_findings=[],
        known_paths={"/api/orders"},
        client=_FakeClient(_FakeCfg("deepseek-v4")),
    )
    assert hypotheses == []  # model must actually return candidates; no transport here
    assert "model_id" in L.build_model_provenance("oracle_compiler", client=_FakeClient(_FakeCfg("m")))


# ---------------------------------------------------------------------------
# A3: tier routing
# ---------------------------------------------------------------------------


def test_tier_routing_resolution() -> None:
    cfg = _FakeCfg("strong-model")
    cfg.model_light = "light-model"
    assert L.resolve_model_for_tier(cfg, "light") == "light-model"
    assert L.resolve_model_for_tier(cfg, "strong") == "strong-model"
    cfg.model_light = ""
    assert L.resolve_model_for_tier(cfg, "light") == "strong-model"  # fallback
    assert L.resolve_model_for_tier(cfg, "unknown") == "strong-model"  # fail-safe


def test_light_tier_covers_classification_only() -> None:
    assert "defect_classification" in L.LIGHT_TIER_ENGINES
    assert "causality" not in L.LIGHT_TIER_ENGINES
    assert L.DEFAULT_TIER == "strong"
