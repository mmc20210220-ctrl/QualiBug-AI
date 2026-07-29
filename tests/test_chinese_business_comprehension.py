from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
    analyze_chinese_business_source,
    build_chinese_first_comprehension,
)


def _asset() -> dict:
    return {
        "business_objects": [
            {"object": "采购申请"},
            {"object": "出库单"},
            {"object": "订单"},
        ],
        "roles": [
            {"role": "管理员"},
            {"role": "普通用户"},
            {"role": "原申请人"},
        ],
        "rule_library": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }


def test_chinese_nested_permission_and_exception_are_structured() -> None:
    source = {
        "source_id": "prd-1",
        "filename": "采购申请PRD.md",
        "text": (
            "除管理员外，普通用户只能查看本人创建且处于草稿状态的申请；"
            "已提交的申请不得修改，但审批退回后可由原申请人重新编辑。"
        ),
    }

    coverage, facts, _ = analyze_chinese_business_source(source, asset=_asset())

    assert coverage[0]["status"] == "UNDERSTOOD"
    by_action = {fact["action"]["canonical"]: fact for fact in facts}
    assert by_action["查看"]["modality"] == "ONLY_IF"
    assert by_action["查看"]["scope"]["ownership"] == "本人"
    assert by_action["修改"]["modality"] == "MUST_NOT"
    assert by_action["修改"]["conditions"] == ["已提交的申请"]
    assert by_action["重新编辑"]["modality"] == "MAY"
    assert by_action["重新编辑"]["subject"]["actor_refs"] == ["原申请人"]
    assert all(fact["status"] == "ACCEPTED" for fact in facts)


def test_unresolved_chinese_coreference_fails_closed() -> None:
    enriched = build_chinese_first_comprehension(
        _asset(),
        [{"source_id": "prd-2", "filename": "订单规则.md", "text": "其不得发货。"}],
    )

    gate = enriched["enterprise_comprehension_gate"]
    assert gate["status"] == "BLOCKED_BUSINESS_COMPREHENSION_INCOMPLETE"
    assert gate["entry_allowed"] is False
    assert gate["metrics"]["critical_ambiguity_count"] == 1
    assert enriched["business_fact_ledger"]["items"][0]["status"] == "PENDING"
    assert not enriched["rule_library"]
    assert any(
        gap["kind"] == "BLOCKED_BUSINESS_COMPREHENSION_INCOMPLETE"
        for gap in enriched["coverage_gaps"]
    )


def test_original_chinese_source_span_is_formal_fact_authority() -> None:
    statement = "审批通过后自动生成出库单，库存不足则转为待补货。"
    enriched = build_chinese_first_comprehension(
        _asset(),
        [{"source_id": "prd-3", "filename": "出库流程.md", "text": statement}],
    )

    ledger = enriched["business_fact_ledger"]
    assert ledger["fact_authority"] == "original_chinese_source_span"
    assert ledger["translation_intermediate_forbidden"] is True
    assert enriched["governance"]["chinese_source_text_is_fact_authority"] is True
    quotes = {
        span["quote"]
        for fact in ledger["items"]
        for span in fact.get("source_spans", [])
    }
    assert any("审批通过后自动生成出库单" in quote for quote in quotes)
    assert all("translated" not in fact for fact in ledger["items"])


def test_enterprise_aliases_require_source_evidence() -> None:
    enriched = build_chinese_first_comprehension(
        _asset(),
        [
            {
                "source_id": "prd-4",
                "filename": "术语表.md",
                "text": "生产任务单（MO）由计划员创建。",
            }
        ],
    )

    glossary = enriched["chinese_business_glossary"]
    assert glossary["merge_policy"] == "source_evidence_required"
    alias = next(item for item in glossary["items"] if item["alias"] == "MO")
    assert alias["canonical_term"] == "生产任务单"
    assert alias["source_spans"][0]["quote"] == "生产任务单（MO）"


def test_only_accepted_chinese_facts_enter_rule_library() -> None:
    enriched = build_chinese_first_comprehension(
        _asset(),
        [
            {
                "source_id": "prd-5",
                "filename": "订单权限.md",
                "text": "其不得发货。管理员可以查看订单。",
            }
        ],
    )

    promoted = [
        rule
        for rule in enriched["rule_library"]
        if rule.get("derivation") == "chinese_first_business_comprehension"
    ]
    assert len(promoted) == 1
    assert promoted[0]["statement"] == "管理员可以查看订单"
    assert promoted[0]["semantic_contract"]["status"] == "ACCEPTED"
    assert enriched["summary"]["chinese_business_fact_pending"] == 1


def test_multi_condition_without_explicit_combinator_stays_pending() -> None:
    coverage, facts, _ = analyze_chinese_business_source(
        {
            "source_id": "prd-6",
            "filename": "条件组合.md",
            "text": "当金额超过1000时，如果审批未完成，管理员不得提交订单。",
        },
        asset=_asset(),
    )

    assert any(fact["status"] == "PENDING" for fact in facts)
    pending = next(fact for fact in facts if fact["status"] == "PENDING")
    assert len(pending["conditions"]) > 1
    assert pending["condition_combinator"] == "UNRESOLVED"
    assert "CONDITION_COMBINATOR_UNRESOLVED" in pending["ambiguities"]
    assert coverage[0]["status"] == "AMBIGUOUS"


def test_explicit_and_combinator_is_accepted() -> None:
    _, facts, _ = analyze_chinese_business_source(
        {
            "source_id": "prd-7",
            "filename": "条件并且.md",
            "text": "如果金额超过1000，并且如果审批未完成，管理员不得提交订单。",
        },
        asset=_asset(),
    )

    assert facts
    assert all(fact["status"] == "ACCEPTED" for fact in facts)
    assert facts[0]["condition_combinator"] == "AND"
    assert len(facts[0]["conditions"]) > 1


def test_cross_document_term_alias_rewrites_entity_refs() -> None:
    enriched = build_chinese_first_comprehension(
        {
            **_asset(),
            "business_objects": [
                {"object": "生产任务单"},
                {"object": "MO"},
            ],
        },
        [
            {
                "source_id": "glossary",
                "filename": "术语表.md",
                "text": "生产任务单（MO）由计划员创建。",
            },
            {
                "source_id": "prd-mo",
                "filename": "MO规则.md",
                "text": "管理员可以查看MO。",
            },
        ],
    )

    merge = enriched["term_alias_identity_merge"]
    assert merge["alias_to_canonical"]["MO"] == "生产任务单"
    assert merge["rewritten_entity_ref_count"] >= 1
    assert merge["conflict_count"] == 0
    viewing = next(
        fact
        for fact in enriched["business_fact_ledger"]["items"]
        if fact.get("action", {}).get("canonical") == "查看"
    )
    assert viewing["subject"]["entity_refs"] == ["生产任务单"]
    assert enriched["enterprise_comprehension_gate"]["entry_allowed"] is True


def test_conflicting_term_alias_mappings_fail_closed() -> None:
    enriched = build_chinese_first_comprehension(
        _asset(),
        [
            {
                "source_id": "glossary-a",
                "filename": "术语A.md",
                "text": "生产任务单（MO）由计划员创建。",
            },
            {
                "source_id": "glossary-b",
                "filename": "术语B.md",
                "text": "制造订单（MO）由计划员创建。",
            },
        ],
    )

    assert enriched["term_alias_identity_merge"]["conflict_count"] == 1
    assert enriched["enterprise_comprehension_gate"]["entry_allowed"] is False
    pending_aliases = [
        fact
        for fact in enriched["business_fact_ledger"]["items"]
        if fact.get("kind") == "TERM_ALIAS" and fact.get("status") == "PENDING"
    ]
    assert pending_aliases
    assert all(
        "TERM_ALIAS_IDENTITY_CONFLICT" in fact["ambiguities"] for fact in pending_aliases
    )
