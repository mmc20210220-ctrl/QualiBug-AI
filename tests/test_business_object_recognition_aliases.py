from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    build_enterprise_understanding_model,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.business_object_recognition import (
    recognize_business_objects,
)


def _span(source: str, quote: str) -> list[dict]:
    return [{
        "source_id": source,
        "locator": f"{source}#object",
        "quote": quote,
        "quote_hash": f"hash-{source}-{quote}",
    }]


def _alias(fact_id: str, canonical: str, alias: str, statement: str | None = None) -> dict:
    raw = statement or f"{canonical}（{alias}）"
    return {
        "fact_id": fact_id,
        "kind": "TERM_ALIAS",
        "status": "ACCEPTED",
        "canonical_term": canonical,
        "alias": alias,
        "raw_statement": raw,
        "source_spans": _span(fact_id, raw),
    }


def _rule() -> dict:
    statement = "管理员可以查看ProductionOrder"
    return {
        "fact_id": "r-en",
        "kind": "RULE",
        "status": "ACCEPTED",
        "raw_statement": statement,
        "subject": {"entity_refs": ["ProductionOrder"], "actor_refs": ["管理员"]},
        "object": {"entity_refs": ["ProductionOrder"]},
        "action": {"canonical": "查看", "raw": "查看"},
        "conditions": [],
        "state_effects": [],
        "postconditions": [],
        "data_effects": [],
        "exceptions": [],
        "scope": {},
        "modality": "MAY",
        "polarity": "POSITIVE",
        "source_spans": _span("r-en", statement),
    }


def _asset(facts: list[dict]) -> dict:
    return {
        "asset_id": "object-alias-test",
        "business_fact_ledger": {"items": facts},
        "business_objects": [],
        "roles": [],
        "permission_matrix": [],
        "data_tables": [],
        "field_dictionary": [],
        "state_machines": [],
        "entity_relations": [],
        "cross_document_conflicts": [],
        "coverage_gaps": [],
        "source_inventory": [],
        "summary": {},
        "governance": {},
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
    }


def test_hard_alias_chain_propagates_type_then_identity_fuses() -> None:
    asset = _asset([
        _alias("a-cn", "生产任务单", "MO"),
        _alias("a-en", "MO", "ProductionOrder"),
        _rule(),
    ])
    model = build_enterprise_understanding_model(asset)

    assert set(model["business_object_recognition"]["accepted_labels"]) == {
        "生产任务单", "MO", "ProductionOrder"
    }
    assert len(model["business_objects"]) == 1
    assert set(model["identity_clusters"][0]["labels"]) == {
        "生产任务单", "MO", "ProductionOrder"
    }


def test_definition_expression_cannot_seed_object_type() -> None:
    asset = _asset([_alias("definition", "金额", "单价×数量", "金额是指单价×数量")])
    recognition = recognize_business_objects(asset)

    assert recognition["accepted_labels"] == []
    assert {row["status"] for row in recognition["candidates"]} == {"PENDING_ALIAS_ONLY"}
    model = build_enterprise_understanding_model(asset)
    assert model["business_objects"] == []
    assert model["identity_clusters"] == []
