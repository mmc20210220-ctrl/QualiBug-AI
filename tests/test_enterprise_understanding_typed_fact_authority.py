from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.typed_fact_authority import (
    retire_duplicate_compatibility_typed_facts,
)


LOCATOR = "rules.md#line=3;chars=10-30"
STATEMENT = "每张发票必须关联且仅关联一个结算单"
BLOCK_KIND = "BLOCKED_MULTIPLE_STRUCTURE_FIRST_TYPED_AUTHORITIES"


def _fact(
    fact_id: str,
    *,
    derivation: str,
    locator: str = LOCATOR,
    statement: str = STATEMENT,
    fact_type: str = "CARDINALITY_CONSTRAINT",
) -> dict:
    return {
        "fact_id": fact_id,
        "kind": "RULE",
        "fact_type": fact_type,
        "status": "ACCEPTED",
        "derivation": derivation,
        "subject": {"actor_refs": [], "entity_refs": ["发票"]},
        "object": {"entity_refs": ["结算单"]},
        "predicate": "EXACTLY_ONE",
        "raw_statement": statement,
        "source_spans": [
            {
                "source_id": "source:rules",
                "locator": locator,
                "document_block_id": "block:3",
                "address_kind": "EXACT_SOURCE_LOCATOR",
                "quote": statement,
                "quote_hash": f"sha256:{fact_id}",
            }
        ],
        "ambiguities": [],
        "formal_promotion_allowed": True,
    }


def _asset(*facts: dict) -> dict:
    return {
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v2",
            "items": [deepcopy(fact) for fact in facts],
        },
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "coverage_gaps": [],
        "governance": {},
    }


def _by_id(asset: dict) -> dict[str, dict]:
    return {
        str(row.get("fact_id")): row
        for row in asset["business_fact_ledger"]["items"]
    }


def test_exact_compatibility_shell_is_retired_by_structure_authority() -> None:
    structure = _fact(
        "fact:structure",
        derivation="structure_first_explicit_fact_compiler",
    )
    compatibility = _fact(
        "fact:compatibility",
        derivation="legacy_chinese_business_comprehension",
    )

    result = retire_duplicate_compatibility_typed_facts(
        _asset(compatibility, structure)
    )
    facts = _by_id(result)
    receipt = result["typed_fact_authority_retirement_receipt"]

    assert facts["fact:structure"]["status"] == "ACCEPTED"
    assert facts["fact:compatibility"]["status"] == "REJECTED"
    assert facts["fact:compatibility"]["formal_promotion_allowed"] is False
    authority = facts["fact:compatibility"]["typed_fact_authority"]
    assert authority["status"] == "RETIRED_COMPATIBILITY_SHELL"
    assert authority["authority_fact_ref"] == "fact:structure"
    assert authority["authority_fact_refs"] == ["fact:structure"]
    assert authority["retirement_mode"] == "EXACT_FORMAL_COORDINATE"
    assert authority["matching_contract"] == [
        "EXACT_SOURCE_LOCATOR",
        "NORMALIZED_SOURCE_STATEMENT_EXACT",
        "COMPLETE_FORMAL_SEMANTIC_COORDINATE",
    ]
    assert authority["automatic_winner_used"] is False
    assert receipt["status"] == "PASS"
    assert receipt["structure_first_authority_count"] == 1
    assert receipt["retired_compatibility_shell_count"] == 1
    assert receipt["automatic_winner_used"] is False
    assert receipt["silent_authority_ambiguity_allowed"] is False
    assert result["coverage_gaps"] == []
    assert result["enterprise_comprehension_gate"]["entry_allowed"] is True


def test_only_same_statement_empty_cross_type_wrapper_is_retired() -> None:
    structure = _fact(
        "fact:structure",
        derivation="structure_first_explicit_fact_compiler",
    )
    different_statement = _fact(
        "fact:different-statement",
        derivation="legacy_chinese_business_comprehension",
        statement="每张发票必须关联至少一个结算单",
    )
    different_locator = _fact(
        "fact:different-locator",
        derivation="legacy_chinese_business_comprehension",
        locator="rules.md#line=5;chars=31-50",
    )
    different_type = _fact(
        "fact:different-type",
        derivation="legacy_chinese_business_comprehension",
        fact_type="OBJECT_RELATION",
    )

    result = retire_duplicate_compatibility_typed_facts(
        _asset(structure, different_statement, different_locator, different_type)
    )
    facts = _by_id(result)
    receipt = result["typed_fact_authority_retirement_receipt"]

    assert facts["fact:structure"]["status"] == "ACCEPTED"
    assert facts["fact:different-statement"]["status"] == "ACCEPTED"
    assert facts["fact:different-locator"]["status"] == "ACCEPTED"
    assert facts["fact:different-type"]["status"] == "REJECTED"
    assert facts["fact:different-type"]["typed_fact_authority"][
        "retirement_mode"
    ] == "SAME_STATEMENT_EMPTY_COMPATIBILITY_WRAPPER"
    assert receipt["status"] == "PASS"
    assert receipt["retired_compatibility_shell_count"] == 1
    assert receipt["cross_statement_merge_allowed"] is False
    assert receipt["cross_locator_merge_allowed"] is False
    assert result["coverage_gaps"] == []


def test_multiple_structure_authorities_fail_closed_without_selecting_one() -> None:
    first = _fact(
        "fact:structure:1",
        derivation="structure_first_explicit_fact_compiler",
    )
    second = _fact(
        "fact:structure:2",
        derivation="structure_first_explicit_fact_compiler",
    )
    compatibility = _fact(
        "fact:compatibility",
        derivation="legacy_chinese_business_comprehension",
    )

    result = retire_duplicate_compatibility_typed_facts(
        _asset(first, second, compatibility)
    )
    facts = _by_id(result)
    receipt = result["typed_fact_authority_retirement_receipt"]

    assert receipt["status"] == "BLOCKED"
    assert receipt["structure_first_authority_count"] == 0
    assert receipt["retired_compatibility_shell_count"] == 0
    assert len(receipt["ambiguous_structure_authorities"]) == 1
    assert set(
        receipt["ambiguous_structure_authorities"][0]["authority_fact_ids"]
    ) == {"fact:structure:1", "fact:structure:2"}
    assert {fact["status"] for fact in facts.values()} == {"ACCEPTED"}
    assert result["enterprise_comprehension_gate"]["entry_allowed"] is False
    assert result["enterprise_comprehension_gate"]["status"] == BLOCK_KIND
    assert result["enterprise_comprehension_gate"]["required_operator_action"]
    assert len(result["coverage_gaps"]) == 1
    gap = result["coverage_gaps"][0]
    assert gap["kind"] == BLOCK_KIND
    assert gap["gap_type"] == "multiple_structure_first_typed_authorities"
    assert gap["operator_action"]
    assert len(gap["ambiguous_structure_authorities"]) == 1

def test_same_statement_multiple_atomic_relations_are_not_duplicate_authorities() -> None:
    statement = "采购订单由订单头和订单明细组成"
    first = _fact(
        "fact:header",
        derivation="structure_first_explicit_fact_compiler",
        statement=statement,
        fact_type="OBJECT_RELATION",
    )
    first["subject"]["entity_refs"] = ["采购订单"]
    first["object"]["entity_refs"] = ["订单头"]
    first["predicate"] = "COMPOSED_OF"
    second = deepcopy(first)
    second["fact_id"] = "fact:line"
    second["object"]["entity_refs"] = ["订单明细"]

    result = retire_duplicate_compatibility_typed_facts(_asset(first, second))
    receipt = result["typed_fact_authority_retirement_receipt"]

    assert receipt["status"] == "PASS"
    assert receipt["structure_first_fact_count"] == 2
    assert receipt["unique_structure_first_coordinate_count"] == 2
    assert receipt["ambiguous_structure_authorities"] == []
    assert {row["status"] for row in result["business_fact_ledger"]["items"]} == {
        "ACCEPTED"
    }
