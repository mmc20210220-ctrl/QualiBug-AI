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


def test_same_section_coreference_resolves_via_unique_prior_and_alias() -> None:
    asset = {
        **_asset(),
        "business_objects": [{"object": "生产任务单"}, {"object": "MO"}],
        "roles": [{"role": "计划员"}],
    }
    _, facts, _ = analyze_chinese_business_source(
        {
            "source_id": "prd-section-ref",
            "filename": "任务规则.md",
            "text": (
                "生产任务单（MO）由计划员创建。"
                "该单据不得删除。"
            ),
        },
        asset=asset,
    )

    deny = next(fact for fact in facts if fact.get("action", {}).get("canonical") == "删除")
    assert deny["status"] == "ACCEPTED"
    assert deny["subject"]["entity_refs"] == ["生产任务单"]
    assert deny["subject"]["resolution_evidence"][0]["method"] == "nearest_unambiguous_entity_context"
    assert deny["subject"]["resolution_evidence"][0]["section_scoped"] is True


def test_section_boundary_resets_extract_time_coreference_context() -> None:
    asset = {
        **_asset(),
        "business_objects": [{"object": "订单"}, {"object": "出库单"}],
    }
    _, facts, _ = analyze_chinese_business_source(
        {
            "source_id": "prd-section-reset",
            "filename": "分段规则.md",
            "text": (
                "# 订单\n"
                "订单可以查看。\n"
                "# 出库单\n"
                "其不得删除。"
            ),
        },
        asset=asset,
    )

    deny = next(fact for fact in facts if fact.get("raw_statement") == "其不得删除")
    # Extract-time context must not leak 订单 across the section boundary.
    assert "订单" not in deny["subject"]["entity_refs"]
    assert deny["status"] == "PENDING"
    assert any(
        value.startswith("COREFERENCE_") or value.startswith("BUSINESS_SUBJECT_")
        for value in deny["ambiguities"]
    )


def test_ambiguous_omitted_actor_fails_closed() -> None:
    _, facts, _ = analyze_chinese_business_source(
        {
            "source_id": "prd-omitted-actor",
            "filename": "角色省略.md",
            "text": (
                "管理员可以查看订单。普通用户可以提交订单。"
                "完成后不得修改订单。"
            ),
        },
        asset=_asset(),
    )

    deny = next(fact for fact in facts if fact.get("action", {}).get("canonical") == "修改")
    assert deny["status"] == "PENDING"
    assert any(value.startswith("OMITTED_ACTOR_AMBIGUOUS") for value in deny["ambiguities"])
    assert deny["subject"]["actor_refs"] == []


def test_explicit_exception_scope_is_accepted() -> None:
    _, facts, _ = analyze_chinese_business_source(
        {
            "source_id": "prd-exception-scope",
            "filename": "例外范围.md",
            "text": "订单不得删除管理员除外。",
        },
        asset=_asset(),
    )

    deny = next(fact for fact in facts if fact.get("action", {}).get("canonical") == "删除")
    assert deny["exception_scope"] == ["管理员"]
    assert "EXCEPTION_SCOPE_UNRESOLVED" not in deny["ambiguities"]
    assert deny["status"] == "ACCEPTED"


def test_structured_quantity_time_formula_and_delegation_from_source() -> None:
    asset = {
        **_asset(),
        "roles": [{"role": "管理员"}, {"role": "财务"}, {"role": "普通用户"}],
    }
    _, facts, _ = analyze_chinese_business_source(
        {
            "source_id": "prd-structured",
            "filename": "结构化约束.md",
            "text": (
                "如果金额超过1000，并且提交之后24小时以内，"
                "管理员授权财务代为审批订单。"
                "金额=单价×数量。"
            ),
        },
        asset=asset,
    )

    rule_facts = [fact for fact in facts if fact.get("kind") == "RULE"]
    assert rule_facts
    structured = next(
        fact
        for fact in rule_facts
        if fact.get("quantity_constraints") or fact.get("authorization_delegation")
    )
    assert structured["quantity_constraints"]
    assert structured["quantity_constraints"][0]["operator"] == "超过"
    assert structured["quantity_constraints"][0]["value"] == "1000"
    assert structured["time_window_constraints"]
    assert structured["authorization_delegation"]["delegator"] == "管理员"
    assert structured["authorization_delegation"]["delegatee"] == "财务"
    formula = next(
        (fact for fact in rule_facts if fact.get("formula_constraints")),
        None,
    )
    # Formula sentence without rule modality may be absent; quantity/time/delegation
    # must still be source-backed when attached to a rule signal unit.
    assert structured["authorization_delegation"]["source_backed"] is True
    if formula:
        assert formula["formula_constraints"][0]["lhs"] == "金额"


def test_synonym_markers_emit_source_backed_term_aliases() -> None:
    samples = [
        ("生产任务单又称MO。", "生产任务单", "MO"),
        ("生产任务单也称制造订单。", "生产任务单", "制造订单"),
        ("生产任务单又名MO。", "生产任务单", "MO"),
        ("生产任务单即MO。", "生产任务单", "MO"),
        ("生产任务单等同于制造订单。", "生产任务单", "制造订单"),
        ("生产任务单是指MO。", "生产任务单", "MO"),
        ("ManufacturingOrder also known as MO", "ManufacturingOrder", "MO"),
    ]
    asset = {
        **_asset(),
        "business_objects": [
            {"object": "生产任务单"},
            {"object": "制造订单"},
            {"object": "MO"},
            {"object": "ManufacturingOrder"},
        ],
    }
    for text, canonical, alias in samples:
        _, _, glossary = analyze_chinese_business_source(
            {"source_id": "syn", "filename": "术语.md", "text": text},
            asset=asset,
        )
        matched = [
            row
            for row in glossary
            if row.get("canonical_term") == canonical and row.get("alias") == alias
        ]
        assert matched, f"expected TERM_ALIAS {canonical}->{alias} from {text!r}, got {glossary}"
        assert matched[0]["status"] == "ACCEPTED"
        assert matched[0]["source_spans"][0]["quote"]


def test_glossary_definition_table_emits_term_aliases() -> None:
    text = (
        "| 术语 | 别名 |\n"
        "| --- | --- |\n"
        "| 生产任务单 | MO |\n"
        "| 制造订单 | WO |\n"
    )
    _, _, glossary = analyze_chinese_business_source(
        {"source_id": "glossary-table", "filename": "术语表.md", "text": text},
        asset={
            **_asset(),
            "business_objects": [
                {"object": "生产任务单"},
                {"object": "制造订单"},
                {"object": "MO"},
                {"object": "WO"},
            ],
        },
    )
    pairs = {(row["canonical_term"], row["alias"]) for row in glossary}
    assert ("生产任务单", "MO") in pairs
    assert ("制造订单", "WO") in pairs


def test_cross_document_synonym_alias_enables_identity_merge() -> None:
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
                "text": "生产任务单又称MO。",
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
    assert merge["conflict_count"] == 0
    viewing = next(
        fact
        for fact in enriched["business_fact_ledger"]["items"]
        if fact.get("action", {}).get("canonical") == "查看"
    )
    assert viewing["subject"]["entity_refs"] == ["生产任务单"]
    assert enriched["enterprise_comprehension_gate"]["entry_allowed"] is True


def test_trigger_effect_fills_condition_postcondition_and_compensation() -> None:
    asset = {
        **_asset(),
        "business_objects": [
            {"object": "出库单"},
            {"object": "订单"},
            {"object": "库存"},
        ],
    }
    _, generate_facts, _ = analyze_chinese_business_source(
        {
            "source_id": "effect-1",
            "filename": "出库.md",
            "text": "审批通过后自动生成出库单。",
        },
        asset=asset,
    )
    generate = next(fact for fact in generate_facts if fact.get("kind") == "RULE")
    assert generate["status"] == "ACCEPTED"
    assert generate["action"]["canonical"] == "审批通过"
    assert any("审批通过" in item for item in generate["conditions"])
    assert any("生成出库单" in item for item in generate["postconditions"])
    assert generate["data_effects"]
    assert generate["data_effects"][0]["entity"] == "出库单"

    _, cancel_facts, _ = analyze_chinese_business_source(
        {
            "source_id": "effect-2",
            "filename": "取消.md",
            "text": "取消订单后必须补偿释放库存。",
        },
        asset=asset,
    )
    cancel = next(fact for fact in cancel_facts if fact.get("kind") == "RULE")
    assert cancel["status"] == "ACCEPTED"
    assert cancel["action"]["canonical"] == "取消"
    assert cancel["compensation"]
    assert any("补偿" in item for item in cancel["compensation"])
    assert cancel["compensations"] == cancel["compensation"]


def test_only_if_permission_is_allow_not_unspecified() -> None:
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.behavior_ir import (
        _behavior_from_fact,
        _fact_permission,
    )

    _, facts, _ = analyze_chinese_business_source(
        {
            "source_id": "perm-1",
            "filename": "权限.md",
            "text": "普通用户只能查看本人订单。",
        },
        asset=_asset(),
    )
    fact = next(fact for fact in facts if fact.get("action", {}).get("canonical") == "查看")
    assert fact["modality"] == "ONLY_IF"
    assert fact["status"] == "ACCEPTED"
    assert _fact_permission(fact) == "ALLOW"
    behavior = _behavior_from_fact(fact)
    assert behavior is not None
    assert behavior["permission_decision"] == "ALLOW"
    assert behavior["status"] == "CONFIRMED"
