from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.post_compile_fact_governance import (
    govern_compiled_business_facts,
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
