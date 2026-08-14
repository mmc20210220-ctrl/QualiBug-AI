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


def test_inventory_restore_rule_preserves_field_data_effects() -> None:
    _, facts, _ = analyze_chinese_business_source(
        {
            "source_id": "inventory-restore-1",
            "filename": "inventory-rules.md",
            "text": "订单取消时，应减少 locked_qty 并恢复 available_qty",
        },
        asset={
            **_asset(),
            "business_objects": [{"object": "订单"}],
        },
    )

    rule = next(fact for fact in facts if fact.get("kind") == "RULE")
    effects = {
        (effect.get("action"), effect.get("entity"))
        for effect in rule.get("data_effects") or []
    }
    assert ("减少", "locked_qty") in effects
    assert ("恢复", "available_qty") in effects
    assert rule["postconditions"]


def test_field_level_conservation_linkage_is_source_backed_only() -> None:
    _, facts, _ = analyze_chinese_business_source(
        {
            "source_id": "conservation-link-1",
            "filename": "inventory-rules.md",
            "text": "订单取消时，应减少 locked_qty 并恢复 available_qty",
        },
        asset=_asset(),
    )
    rule = next(fact for fact in facts if fact.get("kind") == "RULE")
    linkages = rule.get("conservation_linkages") or []
    assert len(linkages) == 1
    linkage = linkages[0]
    assert linkage["kind"] == "CONSERVATION_LINKAGE"
    assert linkage["dec_verb"] == "减少"
    assert linkage["dec_field"] == "locked_qty"
    assert linkage["inc_verb"] == "恢复"
    assert linkage["inc_field"] == "available_qty"
    assert linkage["source_backed"] is True


def test_conservation_linkage_declines_single_effect() -> None:
    _, facts, _ = analyze_chinese_business_source(
        {
            "source_id": "conservation-link-2",
            "filename": "inventory-rules.md",
            "text": "订单取消时，应锁定库存",
        },
        asset={**_asset(), "business_objects": [{"object": "订单"}, {"object": "库存"}]},
    )
    rule = next(fact for fact in facts if fact.get("kind") == "RULE")
    # A single effect without a coupled increment/decrement is NOT a linkage;
    # the extraction must never invent a field pair the source did not state.
    assert (rule.get("conservation_linkages") or []) == []
    # The newly recognized resource verb is still a data effect.
    assert any(e.get("action") == "锁定" for e in rule.get("data_effects") or [])


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


def test_if_then_else_and_nested_exception_project_explicit_frames() -> None:
    asset = {
        **_asset(),
        "roles": [{"role": "管理员"}, {"role": "普通用户"}, {"role": "财务"}],
    }
    _, facts, _ = analyze_chinese_business_source(
        {
            "source_id": "nested-1",
            "filename": "分支.md",
            "text": (
                "若金额超过1000且审批未完成，则管理员不得提交订单，否则可以提交订单。"
            ),
        },
        asset=asset,
    )
    assert len(facts) == 2
    by_branch = {
        fact["condition_frame"]["branch"]: fact
        for fact in facts
        if fact.get("condition_frame", {}).get("kind") == "IF_THEN_ELSE"
    }
    assert set(by_branch) == {"THEN", "ELSE"}
    then_fact = by_branch["THEN"]
    else_fact = by_branch["ELSE"]
    assert then_fact["condition_combinator"] == "AND"
    assert then_fact["conditions"] == ["金额超过1000", "审批未完成"]
    assert then_fact["modality"] == "MUST_NOT"
    assert else_fact["modality"] == "MAY"
    assert else_fact["conditions"] == ["金额超过1000", "审批未完成"]
    assert else_fact["condition_combinator"] == "AND"
    assert then_fact["subject"]["entity_refs"] == ["订单"]
    assert else_fact["subject"]["entity_refs"] == ["订单"]

    _, except_facts, _ = analyze_chinese_business_source(
        {
            "source_id": "nested-2",
            "filename": "例外覆盖.md",
            "text": "订单不得删除，但管理员除外。",
        },
        asset=asset,
    )
    deny = next(fact for fact in except_facts if fact.get("action", {}).get("canonical") == "删除")
    assert deny["exception_scope"] == ["管理员"]
    assert deny["condition_frame"]["kind"] == "EXCEPT_OVERLAY"
    assert "管理员" not in deny["subject"]["actor_refs"]
    assert deny["status"] == "ACCEPTED"

    _, scoped, _ = analyze_chinese_business_source(
        {
            "source_id": "nested-3",
            "filename": "除外主规则.md",
            "text": "除管理员外，普通用户不得删除订单。",
        },
        asset=asset,
    )
    scoped_deny = next(fact for fact in scoped if fact.get("action", {}).get("canonical") == "删除")
    assert scoped_deny["exception_scope"] == ["管理员"]
    assert scoped_deny["subject"]["actor_refs"] == ["普通用户"]
    assert scoped_deny["condition_frame"]["kind"] == "EXCEPT_OVERLAY"


def test_explicit_and_clause_after_comma_is_not_silently_dropped() -> None:
    _, facts, _ = analyze_chinese_business_source(
        {
            "source_id": "and-clause",
            "filename": "并且条件.md",
            "text": "如果金额超过1000，并且状态为草稿，则普通用户可以提交订单，否则不得提交。",
        },
        asset=_asset(),
    )
    assert len(facts) == 2
    then_fact = next(
        fact for fact in facts if fact.get("condition_frame", {}).get("branch") == "THEN"
    )
    else_fact = next(
        fact for fact in facts if fact.get("condition_frame", {}).get("branch") == "ELSE"
    )
    assert then_fact["condition_combinator"] == "AND"
    assert "金额超过1000" in then_fact["conditions"]
    assert any("草稿" in item for item in then_fact["conditions"])
    assert then_fact["modality"] == "MAY"
    assert else_fact["modality"] == "MUST_NOT"
    assert else_fact["condition_combinator"] == "AND"


def test_multi_branch_else_if_chain_projects_explicit_frames() -> None:
    asset = {
        **_asset(),
        "roles": [{"role": "管理员"}, {"role": "普通用户"}, {"role": "财务"}],
    }
    _, facts, _ = analyze_chinese_business_source(
        {
            "source_id": "else-if-1",
            "filename": "多分支.md",
            "text": (
                "若金额超过1000，则管理员可以审批订单，"
                "否则若金额超过500，则财务可以审批订单，"
                "否则普通用户不得审批订单。"
            ),
        },
        asset=asset,
    )
    by_branch = {
        fact["condition_frame"]["branch"]: fact
        for fact in facts
        if fact.get("condition_frame", {}).get("kind") == "IF_THEN_ELSE"
    }
    assert set(by_branch) == {"THEN", "ELSE_IF", "ELSE"}
    assert by_branch["THEN"]["conditions"] == ["金额超过1000"]
    assert by_branch["THEN"]["modality"] == "MAY"
    assert by_branch["THEN"]["subject"]["actor_refs"] == ["管理员"]
    assert by_branch["ELSE_IF"]["conditions"] == ["金额超过500"]
    assert by_branch["ELSE_IF"]["condition_combinator"] != "AND"
    assert by_branch["ELSE_IF"]["modality"] == "MAY"
    assert by_branch["ELSE_IF"]["subject"]["actor_refs"] == ["财务"]
    assert by_branch["ELSE_IF"]["condition_frame"]["parent_conditions"] == ["金额超过1000"]
    assert by_branch["ELSE"]["modality"] == "MUST_NOT"
    assert by_branch["ELSE"]["subject"]["actor_refs"] == ["普通用户"]
    assert by_branch["ELSE"]["subject"]["entity_refs"] == ["订单"]
    assert {fact["condition_frame"]["branch_index"] for fact in by_branch.values()} == {0, 1, 2}
    assert all(fact["status"] == "ACCEPTED" for fact in by_branch.values())


def test_nested_except_inside_branch_and_chained_overlays() -> None:
    asset = {
        **_asset(),
        "roles": [{"role": "管理员"}, {"role": "普通用户"}, {"role": "财务"}],
    }
    _, nested_facts, _ = analyze_chinese_business_source(
        {
            "source_id": "nested-except-branch",
            "filename": "分支内例外.md",
            "text": "若状态为草稿，则除管理员外普通用户不得提交订单，否则可以提交订单。",
        },
        asset=asset,
    )
    then_fact = next(
        fact
        for fact in nested_facts
        if fact.get("condition_frame", {}).get("branch") == "THEN"
    )
    assert then_fact["exception_scope"] == ["管理员"]
    assert then_fact["condition_frame"]["kind"] == "IF_THEN_ELSE"
    assert then_fact["condition_frame"]["overlays"] == [
        {"kind": "EXCEPT_OVERLAY", "exception_scopes": ["管理员"], "source_backed": True}
    ]
    assert then_fact["subject"]["actor_refs"] == ["普通用户"]
    assert then_fact["status"] == "ACCEPTED"

    _, chained, _ = analyze_chinese_business_source(
        {
            "source_id": "chained-except",
            "filename": "连锁例外.md",
            "text": "订单不得删除，但管理员除外，财务除外。",
        },
        asset=asset,
    )
    deny = next(fact for fact in chained if fact.get("action", {}).get("canonical") == "删除")
    assert deny["exception_scope"] == ["管理员", "财务"]
    assert deny["condition_frame"]["kind"] == "EXCEPT_OVERLAY"
    assert deny["condition_frame"]["overlays"][0]["exception_scopes"] == ["管理员", "财务"]
    assert deny["status"] == "ACCEPTED"
    assert "EXCEPTION_SCOPE_UNRESOLVED" not in deny.get("ambiguities", [])


def test_underdetermined_nested_branch_stays_unresolved() -> None:
    _, facts, _ = analyze_chinese_business_source(
        {
            "source_id": "under-nested",
            "filename": "未决嵌套.md",
            "text": "若金额超过1000，则管理员可以审批订单，否则若金额超过500。",
        },
        asset=_asset(),
    )
    pending = [
        fact
        for fact in facts
        if "NESTED_BRANCH_UNDERDETERMINED" in fact.get("ambiguities", [])
        or "IF_THEN_ELSE_UNDERDETERMINED" in fact.get("ambiguities", [])
    ]
    assert pending
    assert all(fact["status"] == "PENDING" for fact in pending)
    assert all(fact["condition_frame"]["kind"] == "IF_THEN_ELSE" for fact in pending)
    assert all(fact["condition_combinator"] == "UNRESOLVED" for fact in pending)


def test_action_nouns_qualifiers_and_prohibitions_do_not_emit_fake_effects() -> None:
    asset = {
        **_asset(),
        "business_objects": [
            {"object": "订单"},
            {"object": "退款金额"},
            {"object": "实付金额"},
            {"object": "优惠金额"},
        ],
        "roles": [
            {"role": "仓库管理员"},
            {"role": "订单创建人"},
        ],
    }
    _, facts, _ = analyze_chinese_business_source(
        {
            "source_id": "no-fake-effects",
            "filename": "显式规则.md",
            "text": (
                "仓库管理员不得删除已出库订单。"
                "只有订单创建人可以撤回本人创建且尚未审批的订单。"
                "退款金额 = 实付金额 - 优惠金额。"
            ),
        },
        asset=asset,
    )

    delete = next(
        fact
        for fact in facts
        if fact.get("action", {}).get("canonical") == "删除"
    )
    assert delete["modality"] == "MUST_NOT"
    assert delete["data_effects"] == []

    withdraw = next(
        fact
        for fact in facts
        if fact.get("action", {}).get("canonical") == "撤回"
    )
    assert withdraw["data_effects"] == []
    assert not any(
        effect.get("action") == "创建"
        for effect in withdraw.get("data_effects", [])
    )

    formula = next(
        fact for fact in facts if fact.get("formula_constraints")
    )
    assert not formula.get("action")
    assert formula["data_effects"] == []
    assert formula["compensation"] == []
    assert formula["formula_constraints"][0]["lhs"] == "退款金额"


def test_lifecycle_implied_process_ordering_is_action_pair_only() -> None:
    _, facts, _ = analyze_chinese_business_source(
        {
            "source_id": "process-order-1",
            "filename": "lifecycle.md",
            "text": "发货后应可确认收货",
        },
        asset=_asset(),
    )
    rule = next(fact for fact in facts if fact.get("kind") == "RULE")
    ordering = rule.get("process_ordering") or []
    assert len(ordering) == 1
    assert ordering[0]["kind"] == "PROCESS_ORDERING"
    assert ordering[0]["from_action"] == "发货"
    assert ordering[0]["to_action"] == "收货"
    assert ordering[0]["source_backed"] is True
