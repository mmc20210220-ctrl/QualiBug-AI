from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    build_enterprise_understanding_model,
)


def _span(source: str, quote: str) -> list[dict]:
    return [{
        "source_id": source,
        "locator": f"{source}#object",
        "quote": quote,
        "quote_hash": f"hash-{source}-{quote}",
    }]


def _rule(fact_id: str, objects: list[str], *, actor: str = "管理员") -> dict:
    statement = f"{actor}可以查看{'、'.join(objects)}"
    return {
        "fact_id": fact_id,
        "kind": "RULE",
        "status": "ACCEPTED",
        "raw_statement": statement,
        "subject": {"entity_refs": objects, "actor_refs": [actor]},
        "object": {"entity_refs": objects},
        "action": {"canonical": "查看", "raw": "查看"},
        "conditions": [],
        "condition_combinator": "",
        "state_effects": [],
        "postconditions": [],
        "data_effects": [],
        "exceptions": [],
        "scope": {},
        "modality": "MAY",
        "polarity": "POSITIVE",
        "source_spans": _span(fact_id, statement),
    }


def _asset(facts: list[dict], **extra) -> dict:
    value = {
        "asset_id": "object-recognition-test",
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
    value.update(extra)
    return value


def test_source_backed_object_is_formal_and_original_mentions_survive() -> None:
    asset = _asset([_rule("r-order", ["销售订单"])])
    model = build_enterprise_understanding_model(asset)

    assert [row["name"] for row in model["business_objects"]] == ["销售订单"]
    assert model["business_object_recognition_gate"]["status"] == "PASS"
    fact = asset["business_fact_ledger"]["items"][0]
    assert fact["subject"]["entity_refs"] == ["销售订单"]
    assert fact["subject"]["resolved_entity_refs"]


def test_actor_pollution_blocks_but_valid_object_remains_typed() -> None:
    asset = _asset([_rule("r-collision", ["管理员", "销售订单"])])
    model = build_enterprise_understanding_model(asset)

    assert [row["name"] for row in model["business_objects"]] == ["销售订单"]
    gate = model["business_object_recognition_gate"]
    assert gate["status"] == "BLOCKED_BUSINESS_OBJECT_TYPE_CONFLICT"
    assert gate["entry_allowed"] is False
    assert model["gate"]["entry_allowed"] is False
    fact = asset["business_fact_ledger"]["items"][0]
    assert fact["subject"]["business_object_rejected_mentions"] == ["管理员"]


def test_explicit_object_collision_is_retained_for_review() -> None:
    asset = _asset(
        [_rule("r-explicit", ["管理员"])],
        business_objects=[{"object": "管理员", "object_id": "object:admin"}],
    )
    model = build_enterprise_understanding_model(asset)

    assert [row["name"] for row in model["business_objects"]] == ["管理员"]
    gate = model["business_object_recognition_gate"]
    assert gate["status"] == "PARTIAL_BUSINESS_OBJECT_RECOGNITION"
    assert gate["entry_allowed"] is True
    assert any(
        row["reason_code"] == "EXPLICIT_OBJECT_AUTHORITY_WITH_ROLE_COLLISION"
        for row in model["unknowns"]
    )
