from __future__ import annotations

import json

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.semantic_lexicon_contract import (
    validate_semantic_lexicon_contract,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.structured_fact_compiler import (
    BUSINESS_FACT_LEDGER_V2_SCHEMA,
    compile_structure_first_business_facts,
)


def _block(block_id: str, text: str, order: int, *, region: str = "body") -> dict:
    locator = f"rules.docx#paragraph={order};chars=0-{len(text)}"
    return {
        "block_id": block_id,
        "type": "PARAGRAPH",
        "region": region,
        "text": text,
        "order": order,
        "source_locator": locator,
        "evidence_address": {
            "source_id": "source:rules",
            "source_locator": locator,
            "address_kind": "EXACT_SOURCE_LOCATOR",
        },
    }


def _existing_multiaction_fact(statement: str) -> dict:
    return {
        "fact_id": "fact:existing",
        "kind": "RULE",
        "language": "zh-CN",
        "subject": {
            "actor_refs": ["系统"],
            "entity_refs": ["订单"],
            "resolution_evidence": [],
        },
        "object": {"entity_refs": ["订单"]},
        "conditions": ["订单审批通过后"],
        "condition_combinator": "SINGLE_CONDITION",
        "condition_frame": {
            "kind": "LEAF",
            "combinator": "SINGLE_CONDITION",
            "conditions": ["订单审批通过后"],
            "exception_scopes": [],
            "branch": "",
            "source_backed": True,
        },
        "action": {"canonical": "审批通过", "raw": "审批通过"},
        "scope": {"tenant": "", "organization": "", "ownership": "", "data_scope": ""},
        "modality": "ASSERTS",
        "polarity": "POSITIVE",
        "exceptions": [],
        "exception_scope": [],
        "postconditions": ["生成出库单", "扣减库存", "发送发货通知"],
        "state_effects": [],
        "data_effects": [
            {"statement": "生成出库单", "action": "生成", "entity": "出库单"},
            {"statement": "扣减库存", "action": "扣减", "entity": "库存"},
            {"statement": "发送发货通知", "action": "发送", "entity": "发货通知"},
        ],
        "quantity_constraints": [],
        "time_window_constraints": [],
        "formula_constraints": [],
        "compensation": [],
        "raw_statement": statement,
        "source_spans": [
            {
                "source_id": "source:rules",
                "locator": "rules.docx#section=订单",
                "quote": statement,
            }
        ],
        "confidence": 0.9,
        "status": "ACCEPTED",
        "ambiguities": [],
        "critical": False,
    }


def test_structure_first_compiler_atomizes_and_extracts_non_modal_facts() -> None:
    multiaction = "订单审批通过后，系统生成出库单、扣减库存并发送发货通知。"
    blocks = [
        _block("block:relation", "采购订单由订单头和订单明细组成。", 1),
        _block("block:multi", multiaction, 2),
        _block("block:cardinality", "每张发票只能关联一个结算单。", 3),
        _block("block:formula", "退款金额等于实付金额减去已使用优惠。", 4),
        _block("block:header", "禁止删除订单", 5, region="header"),
    ]
    source = {
        "source_id": "source:rules",
        "filename": "rules.docx",
        "text": "\n".join(row["text"] for row in blocks),
        "document_structure": {
            "schema": "qualibug.document-structure-ir.v1",
            "blocks": blocks,
        },
    }
    asset = {
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v1",
            "fact_authority": "original_chinese_source_span",
            "items": [_existing_multiaction_fact(multiaction)],
        },
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }

    result = compile_structure_first_business_facts(asset, [source])
    ledger = result["business_fact_ledger"]
    assert ledger["schema"] == BUSINESS_FACT_LEDGER_V2_SCHEMA
    facts = ledger["items"]

    relation_pairs = {
        (
            fact["subject"]["entity_refs"][0],
            fact["object"]["entity_refs"][0],
        )
        for fact in facts
        if fact.get("fact_type") == "OBJECT_RELATION"
        and fact.get("predicate") == "COMPOSED_OF"
    }
    assert ("采购订单", "订单头") in relation_pairs
    assert ("采购订单", "订单明细") in relation_pairs
    assert any(fact.get("fact_type") == "CARDINALITY_CONSTRAINT" for fact in facts)
    assert any(fact.get("fact_type") == "DERIVED_VALUE" for fact in facts)

    existing = next(fact for fact in facts if fact.get("fact_id") == "fact:existing")
    predicates = {
        claim["predicate"]
        for claim in existing["claims"]
        if claim["claim_type"] in {"PRIMARY_OPERATION", "ATOMIC_OPERATION", "DATA_EFFECT"}
    }
    assert {"审批通过", "生成", "扣减", "发送"}.issubset(predicates)
    assert existing["evidence_closure"]["status"] == "PASS"
    assert existing["structural_span_attachment"]["document_block_id"] == "block:multi"

    candidates = result["business_fact_candidate_ledger"]["items"]
    assert all(row["terminal"] for row in candidates)
    header = next(row for row in candidates if row["evidence_address"].get("document_block_id") == "block:header")
    assert header["status"] == "NON_FACT_CONTEXT"
    assert result["structure_first_business_fact_compilation_receipt"]["status"] == "PASS"
    assert result["summary"]["business_fact_projection_contract"] == (
        "INTERNAL_EXTRACTION_COMPLETENESS_NOT_RECALL_OR_ACCURACY"
    )


def test_critical_uncompiled_structure_candidate_blocks() -> None:
    text = "经理不得越权处理该事项。"
    source = {
        "source_id": "source:rules",
        "filename": "rules.docx",
        "document_structure": {"blocks": [_block("block:critical", text, 1)]},
    }
    asset = {
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v1",
            "items": [],
        },
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
    }

    result = compile_structure_first_business_facts(asset, [source])

    assert result["structure_first_business_fact_compilation_receipt"]["status"] == "BLOCKED"
    assert result["enterprise_comprehension_gate"]["status"] == (
        "BLOCKED_STRUCTURE_FIRST_FACT_COMPILATION_INCOMPLETE"
    )
    candidate = result["business_fact_candidate_ledger"]["items"][0]
    assert candidate["status"] == "PENDING_WITH_REASON"
    assert candidate["critical"] is True


def _valid_lexicon() -> dict:
    return {
        "version": 1,
        "business_rule_document_markers": ["业务规则"],
        "rule_required_markers": ["必须"],
        "rule_prohibited_markers": ["不得"],
        "rule_condition_markers": ["如果"],
        "role_words": {"admin": ["管理员"]},
        "risk_terms": {"permission": ["权限"]},
        "state_machine_heading_markers": ["状态机"],
        "allowed_transition_markers": ["允许的流转"],
        "forbidden_transition_markers": ["禁止的流转"],
        "permission_decision_markers": {
            "allow": ["允许"],
            "deny": ["禁止"],
        },
        "positive_integer_markers": ["正整数"],
        "entity_token_lexicon": {"订单": ["order"]},
        "entity_alias_groups": [["订单", "order"]],
        "verb_action_lexicon": {"提交": ["create"]},
    }


def test_semantic_lexicon_contract_is_fail_closed(tmp_path) -> None:
    valid = tmp_path / "semantic_lexicon.json"
    valid.write_text(json.dumps(_valid_lexicon(), ensure_ascii=False), encoding="utf-8")
    assert validate_semantic_lexicon_contract(valid)["status"] == "PASS"

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    receipt = validate_semantic_lexicon_contract(invalid)
    assert receipt["status"] == "BLOCKED_COMPREHENSION_POLICY_INVALID"
    assert receipt["entry_allowed"] is False
    assert receipt["errors"]


def test_specific_cardinality_grammar_owns_relation_span_and_persists_value() -> None:
    blocks = [
        _block(
            "block:exactly-one",
            "每张发票必须关联且仅关联一个结算单。",
            1,
        ),
        _block(
            "block:one-to-many",
            "每个采购订单可以包含多个订单明细。",
            2,
        ),
    ]
    source = {
        "source_id": "source:rules",
        "filename": "rules.docx",
        "document_structure": {
            "schema": "qualibug.document-structure-ir.v1",
            "blocks": blocks,
        },
    }
    asset = {
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v1",
            "items": [],
        },
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }

    result = compile_structure_first_business_facts(asset, [source])
    facts = result["business_fact_ledger"]["items"]
    cardinalities = [
        fact for fact in facts if fact.get("fact_type") == "CARDINALITY_CONSTRAINT"
    ]

    assert len(cardinalities) == 2
    by_predicate = {fact["predicate"]: fact for fact in cardinalities}

    exactly_one = by_predicate["EXACTLY_ONE"]
    assert exactly_one["subject"]["entity_refs"] == ["发票"]
    assert exactly_one["object"]["entity_refs"] == ["结算单"]
    assert exactly_one["value"] == {
        "cardinality": "EXACTLY_ONE",
        "minimum": 1,
        "maximum": "1",
    }
    assert exactly_one["quantity_constraints"] == [exactly_one["value"]]

    one_to_many = by_predicate["ONE_TO_MANY"]
    assert one_to_many["subject"]["entity_refs"] == ["采购订单"]
    assert one_to_many["object"]["entity_refs"] == ["订单明细"]
    assert one_to_many["value"] == {
        "cardinality": "ONE_TO_MANY",
        "minimum": 0,
        "maximum": "MANY",
    }
    assert one_to_many["quantity_constraints"] == [one_to_many["value"]]

    # The more specific cardinality grammar is the only authority for these spans.
    assert not [
        fact
        for fact in facts
        if fact.get("fact_type") == "OBJECT_RELATION"
        and {
            *fact.get("subject", {}).get("entity_refs", []),
            *fact.get("object", {}).get("entity_refs", []),
        }.intersection({"发票", "结算单", "采购订单", "订单明细"})
    ]
