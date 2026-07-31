from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.post_compile_fact_governance import (
    govern_compiled_business_facts,
)
from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_fact_entailment import (
    derive_rule_candidates_from_business_facts,
)


def test_second_pass_classifies_identity_and_reapplies_conflicts(tmp_path) -> None:
    alias = {
        "fact_id": "fact:alias",
        "kind": "TERM_ALIAS",
        "fact_type": "TERM_ALIAS",
        "status": "ACCEPTED",
        "canonical_term": "采购订单",
        "alias": "PO",
        "source_spans": [
            {
                "source_id": "source:glossary",
                "locator": "glossary.docx#table=1;cell=1,2",
                "quote": "采购订单（PO）",
                "quote_hash": "sha256:alias",
                "document_block_id": "block:alias",
                "address_kind": "EXACT_SOURCE_LOCATOR",
            }
        ],
    }
    asset = {
        "project_id": "demo",
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v2",
            "items": [alias],
        },
        "cross_document_conflicts": [],
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }

    result = govern_compiled_business_facts(
        asset,
        project_id="demo",
        root=tmp_path,
    )

    fact = result["business_fact_ledger"]["items"][0]
    assert fact["identity_evidence_class"] == "SOURCE_DECLARED_ALIAS"
    assert fact["formal_identity_union_allowed"] is True
    assert result["identity_evidence_policy_receipt"][
        "second_pass_after_structure_compilation"
    ] is True
    assert result["identity_evidence_policy_receipt"][
        "conflict_authority_reapplied"
    ] is True
    assert result["governance"]["business_fact_two_pass_identity_governance"] is True


def test_atomic_cardinality_value_reaches_existing_rule_and_formal_slot_contract(
    tmp_path,
) -> None:
    fact = {
        "fact_id": "fact:cardinality",
        "kind": "RULE",
        "fact_type": "CARDINALITY_CONSTRAINT",
        "status": "ACCEPTED",
        "raw_statement": "每张发票只能关联一个结算单。",
        "subject": {"actor_refs": [], "entity_refs": ["发票"]},
        "object": {"entity_refs": ["结算单"]},
        "claims": [
            {
                "claim_id": "claim:cardinality",
                "claim_type": "CARDINALITY_CONSTRAINT",
                "value": {
                    "cardinality": "EXACTLY_ONE",
                    "minimum": 1,
                    "maximum": "1",
                },
            }
        ],
        "source_spans": [
            {
                "source_id": "source:rules",
                "locator": "rules.docx#paragraph=3",
                "quote": "每张发票只能关联一个结算单。",
                "quote_hash": "sha256:cardinality",
                "document_block_id": "block:cardinality",
                "address_kind": "EXACT_SOURCE_LOCATOR",
            }
        ],
        "confidence": 1.0,
    }
    asset = {
        "project_id": "demo",
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v2",
            "items": [fact],
        },
        "rule_library": [],
        "cross_document_conflicts": [],
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }

    result = govern_compiled_business_facts(
        asset,
        project_id="demo",
        root=tmp_path,
    )

    expected = {
        "cardinality": "EXACTLY_ONE",
        "minimum": 1,
        "maximum": "1",
    }
    normalized = result["business_fact_ledger"]["items"][0]
    assert normalized["value"] == expected
    assert normalized["quantity_constraints"] == [expected]
    assert normalized["typed_value_projection"][
        "formal_quantity_constraint_projected"
    ] is True
    receipt = result["typed_fact_value_projection_receipt"]
    assert receipt["status"] == "PASS"
    assert receipt["formal_quantity_constraint_projected_fact_count"] == 1
    assert result["governance"][
        "cardinality_value_and_quantity_constraint_share_one_authority"
    ] is True
    candidates = derive_rule_candidates_from_business_facts(result)
    assert len(candidates) == 1
    assert candidates[0]["logical_form"] == "CARDINALITY"
    assert candidates[0]["consequent"]["maximum"] == "1"


def test_existing_top_level_cardinality_value_also_closes_formal_slot(tmp_path) -> None:
    fact = {
        "fact_id": "fact:top-level-cardinality",
        "kind": "RULE",
        "fact_type": "CARDINALITY_CONSTRAINT",
        "status": "ACCEPTED",
        "raw_statement": "每个采购订单可以包含多个订单明细。",
        "subject": {"actor_refs": [], "entity_refs": ["采购订单"]},
        "object": {"entity_refs": ["订单明细"]},
        "value": {
            "cardinality": "ONE_TO_MANY",
            "minimum": 0,
            "maximum": "MANY",
        },
        "source_spans": [
            {
                "source_id": "source:rules",
                "locator": "rules.docx#paragraph=4",
                "quote": "每个采购订单可以包含多个订单明细。",
                "quote_hash": "sha256:many",
                "document_block_id": "block:many",
                "address_kind": "EXACT_SOURCE_LOCATOR",
            }
        ],
    }
    asset = {
        "project_id": "demo",
        "business_fact_ledger": {"items": [fact]},
        "cross_document_conflicts": [],
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "coverage_gaps": [],
    }

    result = govern_compiled_business_facts(
        asset,
        project_id="demo",
        root=tmp_path,
    )

    normalized = result["business_fact_ledger"]["items"][0]
    assert normalized["quantity_constraints"] == [fact["value"]]
    assert result["typed_fact_value_projection_receipt"][
        "formal_quantity_constraint_projected_fact_count"
    ] == 1


def test_multiple_atomic_values_are_never_auto_selected(tmp_path) -> None:
    fact = {
        "fact_id": "fact:ambiguous-cardinality",
        "kind": "RULE",
        "fact_type": "CARDINALITY_CONSTRAINT",
        "status": "ACCEPTED",
        "raw_statement": "资料对关联数量存在两个声明。",
        "subject": {"actor_refs": [], "entity_refs": ["发票"]},
        "object": {"entity_refs": ["结算单"]},
        "claims": [
            {
                "claim_id": "claim:one",
                "claim_type": "CARDINALITY_CONSTRAINT",
                "value": {"maximum": "1"},
            },
            {
                "claim_id": "claim:many",
                "claim_type": "CARDINALITY_CONSTRAINT",
                "value": {"maximum": "MANY"},
            },
        ],
        "source_spans": [
            {
                "source_id": "source:rules",
                "locator": "rules.docx#paragraph=5",
                "quote": "资料对关联数量存在两个声明。",
                "quote_hash": "sha256:ambiguous",
                "document_block_id": "block:ambiguous",
                "address_kind": "EXACT_SOURCE_LOCATOR",
            }
        ],
    }
    asset = {
        "project_id": "demo",
        "business_fact_ledger": {"items": [fact]},
        "cross_document_conflicts": [],
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "coverage_gaps": [],
    }

    result = govern_compiled_business_facts(
        asset,
        project_id="demo",
        root=tmp_path,
    )

    normalized = result["business_fact_ledger"]["items"][0]
    assert "value" not in normalized
    assert normalized["status"] == "PENDING"
    assert normalized["typed_value_projection"]["automatic_winner_used"] is False
    assert result["typed_fact_value_projection_receipt"]["status"] == "BLOCKED"
