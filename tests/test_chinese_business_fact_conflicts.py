from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._chinese_business_conflicts import (
    reconcile_chinese_business_fact_conflicts,
)


def _fact(
    fact_id: str,
    *,
    source_id: str,
    modality: str = "MUST_NOT",
    target_state: str = "",
) -> dict:
    kind = "STATE_TRANSITION" if target_state else "RULE"
    return {
        "fact_id": fact_id,
        "kind": kind,
        "status": "ACCEPTED",
        "subject": {
            "entity_refs": ["采购申请"],
            "actor_refs": ["普通用户"],
        },
        "conditions": ["已提交"],
        "action": {"canonical": "修改" if not target_state else "提交"},
        "scope": {"ownership": "本人"},
        "modality": modality,
        "raw_statement": f"{source_id}:{modality}:{target_state}",
        "state_effects": (
            [{"from_state": "草稿", "to_state": target_state}]
            if target_state
            else []
        ),
        "source_spans": [
            {
                "source_id": source_id,
                "locator": f"{source_id}.md#section=规则",
                "quote": f"{source_id}:{modality}:{target_state}",
                "quote_hash": f"hash-{fact_id}",
            }
        ],
    }


def _rule(fact: dict) -> dict:
    return {
        "rule_id": f"rule:{fact['fact_id']}",
        "statement": fact["raw_statement"],
        "derivation": "chinese_first_business_comprehension",
        "semantic_contract": fact,
    }


def _asset(facts: list[dict]) -> dict:
    return {
        "business_fact_ledger": {"items": facts},
        "rule_library": [_rule(fact) for fact in facts],
        "cross_document_conflicts": [],
        "enterprise_comprehension_gate": {
            "status": "PASS",
            "entry_allowed": True,
        },
        "coverage_gaps": [],
        "summary": {},
    }


def test_opposite_modalities_block_and_remove_both_formal_rules() -> None:
    facts = [
        _fact("f-deny", source_id="prd-1", modality="MUST_NOT"),
        _fact("f-allow", source_id="manual-1", modality="MAY"),
    ]

    asset = reconcile_chinese_business_fact_conflicts(_asset(facts))

    assert asset["rule_library"] == []
    assert {
        fact["status"] for fact in asset["business_fact_ledger"]["items"]
    } == {"CONFLICTING"}
    conflict = asset["cross_document_conflicts"][0]
    assert conflict["kind"] == "BUSINESS_MODALITY_CONTRADICTION"
    assert conflict["source_scope"] == "CROSS_SOURCE"
    assert conflict["automatic_resolution_allowed"] is False
    assert asset["enterprise_comprehension_gate"]["entry_allowed"] is False
    assert (
        asset["enterprise_comprehension_gate"]["status"]
        == "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS"
    )


def test_conflict_carries_standard_evidence_message_and_operator_action() -> None:
    facts = [
        _fact("fact-a", source_id="policy_v1", modality="MUST_NOT"),
        _fact("fact-b", source_id="policy_v2", modality="MAY"),
    ]
    asset = reconcile_chinese_business_fact_conflicts(_asset(facts))
    conflict = asset["cross_document_conflicts"][0]
    assert conflict["status"] == "UNRESOLVED"
    assert conflict["message"]
    assert conflict["operator_action"]
    assert "recency" in conflict["operator_action"]
    assert conflict["automatic_resolution_allowed"] is False
    assert len(conflict["evidence"]) >= 2
    quotes = {row["quote"] for row in conflict["evidence"]}
    assert any("MUST_NOT" in quote for quote in quotes)
    assert any("MAY" in quote for quote in quotes)
    assert conflict["authority_decision"]["selected_fact_id"] == ""
    assert conflict["authority_decision"]["operator_required"] is True


def test_conflict_is_not_resolved_by_recency_or_model_confidence() -> None:
    facts = [
        {
            **_fact("f-old", source_id="old-prd", modality="MUST_NOT"),
            "confidence": 0.4,
            "updated_at": "2020-01-01T00:00:00Z",
        },
        {
            **_fact("f-new", source_id="new-prd", modality="MAY"),
            "confidence": 0.99,
            "updated_at": "2030-01-01T00:00:00Z",
        },
    ]

    asset = reconcile_chinese_business_fact_conflicts(_asset(facts))

    conflict = asset["cross_document_conflicts"][0]
    assert conflict["status"] == "UNRESOLVED"
    assert "recency" in conflict["resolution_policy"]
    assert "model confidence" in conflict["resolution_policy"]
    decision = conflict["authority_decision"]
    assert decision["status"] == "UNRESOLVED"
    assert decision["selected_fact_id"] == ""
    assert decision["automatic_resolution_allowed"] is False
    assert "recency" in decision["disallowed_authority_signals"]
    assert "industry_default" in decision["disallowed_authority_signals"]


def test_different_transition_targets_are_blocked() -> None:
    facts = [
        _fact("f-approved", source_id="prd-1", modality="ASSERTS", target_state="已通过"),
        _fact("f-review", source_id="workflow-1", modality="ASSERTS", target_state="复核中"),
    ]

    asset = reconcile_chinese_business_fact_conflicts(_asset(facts))

    assert asset["cross_document_conflicts"][0]["kind"] == (
        "STATE_TRANSITION_TARGET_CONTRADICTION"
    )
    assert asset["summary"]["chinese_business_conflicting_fact_count"] == 2


def test_matching_facts_do_not_create_false_conflict() -> None:
    facts = [
        _fact("f-1", source_id="prd-1", modality="MUST_NOT"),
        _fact("f-2", source_id="manual-1", modality="MUST_NOT"),
    ]

    asset = reconcile_chinese_business_fact_conflicts(_asset(facts))

    assert asset["cross_document_conflicts"] == []
    assert len(asset["rule_library"]) == 2
    assert asset["enterprise_comprehension_gate"]["entry_allowed"] is True
