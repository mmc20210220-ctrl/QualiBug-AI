from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.builder import (
    build_enterprise_understanding_model,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.typed_relation_projection import (
    project_typed_object_relations,
)


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
                "locator": "rules.docx#paragraph=1",
                "quote": "采购订单由订单头和订单明细组成。",
                "quote_hash": "sha256:relation",
                "document_block_id": "block:relation",
                "address_kind": "EXACT_SOURCE_LOCATOR",
            }
        ],
    }


def test_typed_relation_uses_existing_object_graph_without_text_reparse() -> None:
    asset = {
        "asset_id": "asset:demo",
        "business_fact_ledger": {"items": [_relation_fact()]},
        "entity_relations": [],
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "coverage_gaps": [],
        "business_objects": [],
        "data_tables": [],
        "field_dictionary": [],
        "interfaces": [],
    }

    projected = project_typed_object_relations(asset)
    receipt = projected["typed_object_relation_projection_receipt"]
    assert receipt["status"] == "PASS"
    assert receipt["projected_relation_count"] == 1
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
    asset = {
        "business_fact_ledger": {"items": [fact]},
        "entity_relations": [],
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "coverage_gaps": [],
    }

    projected = project_typed_object_relations(asset)
    receipt = projected["typed_object_relation_projection_receipt"]
    assert receipt["status"] == "BLOCKED"
    assert receipt["projected_relation_count"] == 0
    assert receipt["blocked_relation_count"] == 1
    assert receipt["automatic_endpoint_inference_allowed"] is False
    assert projected["enterprise_comprehension_gate"]["entry_allowed"] is False
