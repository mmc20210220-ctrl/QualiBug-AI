from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.builder import (
    build_enterprise_understanding_model,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.typed_relation_projection import (
    project_typed_object_relations,
)


LOCATOR = "rules.docx#paragraph=1"


def _relation_fact() -> dict:
    return {
        "fact_id": "fact:purchase-order-composition",
        "kind": "RULE",
        "fact_type": "OBJECT_RELATION",
        "status": "ACCEPTED",
        "subject": {"actor_refs": [], "entity_refs": ["采购订单"]},
        "object": {"entity_refs": ["订单明细"]},
        "predicate": "COMPOSED_OF",
        "conditions": [],
        "exception_scope": [],
        "raw_statement": "采购订单由订单头和订单明细组成。",
        "source_spans": [
            {
                "source_id": "source:rules",
                "locator": LOCATOR,
                "quote": "采购订单由订单头和订单明细组成。",
                "quote_hash": "sha256:relation",
                "document_block_id": "block:relation",
                "address_kind": "EXACT_SOURCE_LOCATOR",
            }
        ],
        "ambiguities": [],
    }


def _fact(
    fact_id: str,
    *,
    source: str,
    target: str,
    relation: str = "BELONGS_TO",
) -> dict:
    fact = deepcopy(_relation_fact())
    fact["fact_id"] = fact_id
    fact["subject"] = {"actor_refs": [], "entity_refs": [source]}
    fact["object"] = {"entity_refs": [target]}
    fact["predicate"] = relation
    fact["raw_statement"] = f"{source}{relation}{target}"
    fact["source_spans"][0]["quote"] = fact["raw_statement"]
    fact["source_spans"][0]["quote_hash"] = f"sha256:{fact_id}"
    return fact


def _asset(*facts: dict) -> dict:
    return {
        "asset_id": "asset:demo",
        "business_fact_ledger": {"items": [deepcopy(fact) for fact in facts]},
        "entity_relations": [],
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "coverage_gaps": [],
        "governance": {},
        "business_objects": [],
        "data_tables": [],
        "field_dictionary": [],
        "interfaces": [],
    }


def test_typed_relation_uses_existing_object_graph_without_text_reparse() -> None:
    projected = project_typed_object_relations(_asset(_relation_fact()))
    receipt = projected["typed_object_relation_projection_receipt"]
    assert receipt["status"] == "PASS"
    assert receipt["projected_relation_count"] == 1
    assert receipt["grammar_fragment_rejected_count"] == 0
    assert receipt["raw_statement_reparsed"] is False
    edge = projected["entity_relations"][0]
    assert edge["from_entity"] == "采购订单"
    assert edge["relation"] == "COMPOSED_OF"
    assert edge["to_entity"] == "订单明细"
    assert edge["fact_ref"] == "fact:purchase-order-composition"

    # The existing object graph consumes the projected authority directly.
    model = build_enterprise_understanding_model(projected)
    relation = next(
        row
        for row in model["object_relations"]
        if row["source_object_ref"] == "采购订单"
        and row["target_object_ref"] == "订单明细"
    )
    assert relation["relation_type"] == "COMPOSED_OF"
    assert relation["derivation"] == "typed_fact_object_relation"
    assert relation["evidence"]


def test_ambiguous_typed_relation_never_infers_an_endpoint() -> None:
    fact = _relation_fact()
    fact["subject"]["entity_refs"] = ["采购订单", "销售订单"]

    projected = project_typed_object_relations(_asset(fact))
    receipt = projected["typed_object_relation_projection_receipt"]
    assert receipt["status"] == "BLOCKED"
    assert receipt["projected_relation_count"] == 0
    assert receipt["blocked_relation_count"] == 1
    assert receipt["automatic_endpoint_inference_allowed"] is False
    assert projected["enterprise_comprehension_gate"]["entry_allowed"] is False


def test_valid_belongs_to_relation_is_not_rejected() -> None:
    result = project_typed_object_relations(
        _asset(_fact("fact:belongs", source="订单", target="组织"))
    )

    fact = result["business_fact_ledger"]["items"][0]
    receipt = result["typed_object_relation_projection_receipt"]
    assert fact["status"] == "ACCEPTED"
    assert fact["typed_relation_coordinate_validation"]["status"] == "PASS"
    assert fact["object_graph_projection_authority"] == "EXISTING_ENTITY_RELATIONS"
    assert len(result["entity_relations"]) == 1
    assert result["entity_relations"][0]["from_entity"] == "订单"
    assert result["entity_relations"][0]["to_entity"] == "组织"
    assert receipt["status"] == "PASS"
    assert receipt["projected_relation_count"] == 1
    assert receipt["grammar_fragment_rejected_count"] == 0
    assert receipt["blocked_relation_count"] == 0


def test_connector_and_modal_fragments_are_rejected_without_closing_gate() -> None:
    result = project_typed_object_relations(
        _asset(
            _fact("fact:connector", source="并", target="出库单", relation="GENERATES"),
            _fact(
                "fact:modal",
                source="发票必须",
                target="结算单",
                relation="ASSOCIATES_WITH",
            ),
            _fact(
                "fact:quantified",
                source="采购订单可以",
                target="多个订单明细",
                relation="CONTAINS",
            ),
        )
    )

    facts = result["business_fact_ledger"]["items"]
    receipt = result["typed_object_relation_projection_receipt"]
    assert {fact["status"] for fact in facts} == {"REJECTED"}
    assert all(fact["formal_promotion_allowed"] is False for fact in facts)
    assert all(
        fact["typed_relation_coordinate_validation"]["status"] == "REJECTED"
        for fact in facts
    )
    assert result["entity_relations"] == []
    assert receipt["status"] == "PASS"
    assert receipt["grammar_fragment_rejected_count"] == 3
    assert receipt["blocked_relation_count"] == 0
    assert receipt["deterministic_grammar_fragment_rejection_blocks_gate"] is False
    assert result["enterprise_comprehension_gate"]["entry_allowed"] is True
    assert result["coverage_gaps"] == []


def test_incomplete_typed_relation_still_blocks_formal_projection() -> None:
    fact = _fact("fact:incomplete", source="订单", target="组织")
    fact["object"] = {"entity_refs": []}
    result = project_typed_object_relations(_asset(fact))

    receipt = result["typed_object_relation_projection_receipt"]
    assert receipt["status"] == "BLOCKED"
    assert receipt["grammar_fragment_rejected_count"] == 0
    assert receipt["blocked_relation_count"] == 1
    assert result["enterprise_comprehension_gate"]["entry_allowed"] is False
    assert result["enterprise_comprehension_gate"]["status"] == (
        "BLOCKED_TYPED_OBJECT_RELATION_BINDING_INCOMPLETE"
    )
    assert result["coverage_gaps"][0]["kind"] == (
        "BLOCKED_TYPED_OBJECT_RELATION_BINDING_INCOMPLETE"
    )
