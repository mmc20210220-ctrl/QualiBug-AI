from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.atomic_claim_projection import (
    project_atomic_claim_facts,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_edges import (
    build_identity_edges,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.typed_relation_projection import (
    project_typed_object_relations,
)
from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_fact_entailment import (
    derive_rule_candidates_from_business_facts,
)


def test_rejected_facts_cannot_cross_formal_projection_boundaries() -> None:
    rejected_effect = {
        "fact_id": "fact:rejected-effect",
        "kind": "RULE",
        "fact_type": "BUSINESS_RULE",
        "status": "REJECTED",
        "raw_statement": "付款后生成出库单。",
        "subject": {"actor_refs": ["用户"], "entity_refs": ["订单"]},
        "source_spans": [{"source_id": "source:rules", "locator": "rules#1"}],
        "claims": [
            {
                "claim_id": "claim:rejected-effect",
                "claim_type": "DATA_EFFECT",
                "predicate": "生成",
                "object_refs": ["出库单"],
                "value": {
                    "statement": "生成出库单",
                    "action": "生成",
                    "entity": "出库单",
                },
                "source_backed": True,
            }
        ],
    }
    rejected_relation = {
        "fact_id": "fact:rejected-relation",
        "kind": "RULE",
        "fact_type": "OBJECT_RELATION",
        "status": "REJECTED",
        "predicate": "BELONGS_TO",
        "subject": {"entity_refs": ["订单"]},
        "object": {"entity_refs": ["组织"]},
        "source_spans": [{"source_id": "source:rules", "locator": "rules#2"}],
    }
    rejected_cardinality = {
        "fact_id": "fact:rejected-cardinality",
        "kind": "RULE",
        "fact_type": "CARDINALITY_CONSTRAINT",
        "status": "REJECTED",
        "raw_statement": "每张发票只能关联一个结算单。",
        "subject": {"entity_refs": ["发票"]},
        "object": {"entity_refs": ["结算单"]},
        "value": {
            "cardinality": "EXACTLY_ONE",
            "minimum": 1,
            "maximum": "1",
        },
        "source_spans": [{"source_id": "source:rules", "locator": "rules#3"}],
    }
    rejected_alias = {
        "fact_id": "fact:rejected-alias",
        "kind": "TERM_ALIAS",
        "fact_type": "TERM_ALIAS",
        "status": "REJECTED",
        "canonical_term": "采购订单",
        "alias": "PO",
        "raw_statement": "采购订单（PO）",
        "source_spans": [
            {"source_id": "source:glossary", "locator": "glossary#1"}
        ],
    }
    facts = [
        rejected_effect,
        rejected_relation,
        rejected_cardinality,
        rejected_alias,
    ]
    base = {
        "business_fact_ledger": {"items": facts},
        "entity_relations": [],
        "rule_library": [],
        "summary": {},
        "governance": {},
        "coverage_gaps": [],
        "enterprise_comprehension_gate": {
            "status": "PASS",
            "entry_allowed": True,
        },
    }

    atomic = project_atomic_claim_facts(dict(base))
    assert atomic["atomic_claim_fact_projection_receipt"][
        "projected_fact_count"
    ] == 0
    assert len(atomic["business_fact_ledger"]["items"]) == len(facts)

    relations = project_typed_object_relations(dict(base))
    assert relations["typed_object_relation_projection_receipt"][
        "typed_relation_fact_count"
    ] == 0
    assert relations["entity_relations"] == []

    assert derive_rule_candidates_from_business_facts(dict(base)) == []
    assert build_identity_edges({}, [rejected_alias], []) == []
