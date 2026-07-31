from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
    build_chinese_first_comprehension,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    build_document_structure_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.post_compile_fact_governance import (
    govern_compiled_business_facts,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.structured_fact_compiler import (
    compile_structure_first_business_facts,
)
from benchmark_evaluator.enterprise_understanding.chinese_explicit_fact_baseline import (
    FIXTURE_ROOT,
    PROJECT_ID,
    TARGETS,
    _baseline_exit_code,
    _first_loss_analysis,
    _quality_result_status,
    _threshold_status,
)
from benchmark_evaluator.enterprise_understanding.fact_slot_document import (
    validate_business_fact_slot_document,
)
from benchmark_evaluator.enterprise_understanding.fact_slots import (
    evaluate_business_fact_slots,
)
from benchmark_evaluator.enterprise_understanding.ground_truth import (
    load_ground_truth,
)


def _source() -> dict:
    path = FIXTURE_ROOT / "source_rules.md"
    text = path.read_text(encoding="utf-8")
    source_id = "source:chinese-explicit-fact-baseline-v1"
    document_structure = build_document_structure_ir(
        text.encode("utf-8"),
        filename=path.name,
        source_id=source_id,
    )
    return {
        "source_id": source_id,
        "filename": path.name,
        "source_locator": "source_rules.md#document",
        "text": text,
        "document_structure": document_structure,
    }


def _asset() -> dict:
    return {
        "project_id": PROJECT_ID,
        "asset_id": "asset:chinese-explicit-fact-baseline-v1",
        "rule_library": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
        "business_objects": [],
        "data_tables": [],
        "field_dictionary": [],
        "interfaces": [],
        "entity_relations": [],
        "cross_document_conflicts": [],
    }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _facts(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(_dict(asset.get("business_fact_ledger")).get("items"))
        if isinstance(row, dict)
    ]


def _parent_fact(asset: dict[str, Any], statement_prefix: str) -> dict[str, Any]:
    matches = [
        row
        for row in _facts(asset)
        if not _text(row.get("parent_fact_ref"))
        and _text(row.get("raw_statement")).startswith(statement_prefix)
    ]
    assert len(matches) == 1, [
        {
            "fact_id": row.get("fact_id"),
            "fact_type": row.get("fact_type"),
            "raw_statement": row.get("raw_statement"),
        }
        for row in matches
    ]
    return dict(matches[0])


def _typed_fact(
    asset: dict[str, Any],
    *,
    fact_type: str,
    predicate: str,
    object_ref: str,
) -> dict[str, Any]:
    matches = [
        row
        for row in _facts(asset)
        if _text(row.get("fact_type")) == fact_type
        and _text(row.get("predicate")) == predicate
        and object_ref in _list(_dict(row.get("object")).get("entity_refs"))
    ]
    assert len(matches) == 1, [
        {
            "fact_id": row.get("fact_id"),
            "fact_type": row.get("fact_type"),
            "predicate": row.get("predicate"),
            "subject": row.get("subject"),
            "object": row.get("object"),
        }
        for row in matches
    ]
    return dict(matches[0])


def test_frozen_ground_truth_is_valid_and_source_backed() -> None:
    ground_truth = validate_business_fact_slot_document(
        load_ground_truth(FIXTURE_ROOT / "ground_truth.json")
    )
    receipt = ground_truth["validation_receipt"]
    assert receipt["status"] == "PASS"
    assert receipt["business_fact_slot_contract_validated"] is True
    assert receipt["business_fact_slot_annotation_count"] == 16
    assert receipt["confirmed_business_fact_slot_annotation_count"] == 16
    assert receipt["business_fact_ground_truth_generated_from_product_output"] is False
    assert receipt["explicit_fact_scope_complete"] is True
    assert receipt["explicit_fact_scope_locator_count"] == 10
    assert receipt["all_annotated_fact_locators_inside_explicit_scope"] is True
    assert ground_truth["scope_complete"] is False
    assert ground_truth["explicit_fact_scope_complete"] is True
    assert len(ground_truth["explicit_fact_scope_locators"]) == 10
    assert ground_truth["source_locator_authority"] == (
        "generic_text_document_ir_exact_line_char"
    )
    locator_rows = [
        row
        for row in ground_truth["business_rules"]
        if isinstance(row, dict) and row.get("source_locators")
    ]
    assert len(locator_rows) == 16
    assert all(
        len(row["source_locators"]) == 1
        and row["source_locators"][0].startswith("source_rules.md#line=")
        for row in locator_rows
    )


def test_isolated_baseline_exit_status_requires_quality_targets() -> None:
    assert _quality_result_status("PASS", "PASS") == "PASS"
    assert _quality_result_status("PASS", "BELOW_TARGET") == "BELOW_TARGET"
    assert _quality_result_status("NOT_MEASURED", "NOT_MEASURED") == "NOT_MEASURED"

    assert _baseline_exit_code({"status": "PASS"}) == 0
    assert _baseline_exit_code({"status": "BELOW_TARGET"}) == 3
    assert _baseline_exit_code({"status": "BLOCKED"}) == 2
    assert _baseline_exit_code({"status": "NOT_MEASURED"}) == 2
    assert set(TARGETS) == {
        "fact_recall",
        "slot_exact_accuracy",
        "p0_exact_fact_recall",
        "source_locator_exact_accuracy",
        "accepted_fact_precision",
    }


def test_frozen_baseline_runs_existing_fact_mainline_without_quality_self_label(
    tmp_path: Path,
) -> None:
    source = _source()
    locators = {
        str(row.get("source_locator") or "")
        for row in source["document_structure"].get("blocks") or []
        if isinstance(row, dict) and row.get("source_locator")
    }
    assert "source_rules.md#line=3;chars=17-48" in locators
    assert "source_rules.md#line=17;chars=195-213" in locators
    assert "source_rules.md#line=19;chars=216-248" in locators
    assert "source_rules.md#line=21;chars=251-287" in locators

    asset = build_chinese_first_comprehension(_asset(), [source])
    asset = compile_structure_first_business_facts(asset, [source])
    asset = govern_compiled_business_facts(
        asset,
        project_id=PROJECT_ID,
        root=tmp_path,
    )
    ground_truth = validate_business_fact_slot_document(
        load_ground_truth(FIXTURE_ROOT / "ground_truth.json")
    )
    measurement = evaluate_business_fact_slots(ground_truth, asset)

    assert measurement["status"] == "PASS"
    assert measurement["metrics"]["annotated_fact_count"] == 16
    assert measurement["metrics"]["measured_slot_count"] > 0
    assert measurement["metrics"]["source_locator_annotated_fact_count"] == 16
    assert measurement["metrics"]["source_locator_exact_fact_count"] == 16
    assert measurement["metrics"]["source_locator_exact_accuracy"] == 1.0
    assert measurement["metrics"]["accepted_fact_scope_complete"] is True
    assert measurement["metrics"]["accepted_fact_scope_locator_count"] == 10
    assert measurement["metrics"]["accepted_fact_precision"] == 1.0
    assert measurement["metrics"]["false_accepted_fact_count"] == 0
    assert measurement["metrics"]["false_accepted_rate"] == 0.0
    assert measurement["false_accepted_facts"] == []
    assert measurement["evidence_address_measurement_contract"][
        "annotated_fact_denominator_includes_missing_facts"
    ] is True
    assert measurement["evidence_address_measurement_contract"][
        "annotated_fact_denominator_includes_ambiguous_facts"
    ] is True
    assert measurement["accepted_fact_precision_contract"][
        "accepted_scope_uses_declared_exact_source_locators"
    ] is True
    assert measurement["accepted_fact_precision_contract"][
        "unannotated_scope_blocks_remain_in_precision_scope"
    ] is True
    assert measurement["fuzzy_or_llm_alignment_used"] is False
    assert measurement["automatic_winner_used"] is False
    assert measurement["ground_truth_generated_from_product_output"] is False
    assert asset["business_fact_candidate_ledger"]["all_candidates_terminal"] is True
    assert asset["business_fact_candidate_ledger"]["silent_drop_allowed"] is False

    normalization = asset["explicit_fact_semantic_normalization_receipt"]
    assert normalization["status"] == "PASS"
    assert normalization["existing_ledger_reused"] is True
    assert normalization["existing_source_backed_identity_vocabulary_reused"] is True
    assert normalization["grammar_fragment_identity_reuse_allowed"] is False
    assert normalization["governed_operation_binding"] is True
    assert normalization["qualified_object_conditions_compiled"] is True
    assert normalization["split_if_else_pairing_prefers_exact_structure_locator"] is True
    assert normalization["negative_operation_is_not_negative_modality"] is True
    assert normalization["new_fact_discovery_allowed"] is False
    assert normalization["overlapping_identity_coordinate_emission_allowed"] is False
    assert normalization["formal_typed_coordinate_reinterpretation_allowed"] is False
    assert normalization["automatic_winner_used"] is False

    permission = _parent_fact(asset, "经理只有在订单状态为待审批")
    permission_subject = _dict(permission.get("subject"))
    assert permission["fact_type"] == "PERMISSION_RULE"
    assert permission["modality"] == "MAY"
    assert permission_subject.get("actor_refs") == ["经理"]
    assert permission_subject.get("entity_refs") == ["订单"]
    assert _dict(permission.get("object")).get("entity_refs") == ["订单"]
    assert permission["action"]["canonical"] == "审批通过"
    assert permission["condition_frame"]["kind"] == "ALL"
    assert permission["condition_frame"]["combinator"] == "AND"
    assert permission["condition_frame"]["conditions"] == [
        "订单状态为待审批",
        "所属部门一致",
    ]

    transition = _parent_fact(asset, "订单审批通过后")
    transition_subject = _dict(transition.get("subject"))
    assert transition["fact_type"] == "STATE_TRANSITION"
    assert transition["action"]["canonical"] == "审批通过"
    assert transition_subject.get("entity_refs") == ["订单"]
    assert _dict(transition.get("object")).get("entity_refs") == ["订单"]
    assert any(
        _dict(row).get("from_state") == "待审批"
        and _dict(row).get("to_state") == "已审批"
        for row in _list(transition.get("state_effects"))
    )

    prohibition = _parent_fact(asset, "仓库管理员不得删除")
    prohibition_subject = _dict(prohibition.get("subject"))
    assert prohibition_subject.get("actor_refs") == ["仓库管理员"]
    assert prohibition_subject.get("entity_refs") == ["订单"]
    assert _dict(prohibition.get("object")).get("entity_refs") == ["订单"]

    obligation = _parent_fact(asset, "财务人员必须在付款成功后")
    obligation_subject = _dict(obligation.get("subject"))
    assert obligation["fact_type"] == "BUSINESS_RULE"
    assert obligation["modality"] == "MUST"
    assert obligation["action"]["canonical"] == "开具"
    assert obligation_subject.get("actor_refs") == ["财务人员"]
    assert obligation_subject.get("entity_refs") == ["发票"]
    assert _dict(obligation.get("object")).get("entity_refs") == ["发票"]
    assert any(
        _dict(row).get("anchor") == "付款成功后"
        and _dict(row).get("duration") == "24小时"
        and _dict(row).get("relation") == "WITHIN"
        for row in _list(obligation.get("time_window_constraints"))
    )

    composed_of_header = _typed_fact(
        asset,
        fact_type="OBJECT_RELATION",
        predicate="COMPOSED_OF",
        object_ref="订单头",
    )
    assert _dict(composed_of_header.get("subject")).get("entity_refs") == [
        "采购订单"
    ]
    assert _dict(composed_of_header.get("object")).get("entity_refs") == ["订单头"]
    assert "explicit_semantic_normalization" not in composed_of_header

    belongs_to = _typed_fact(
        asset,
        fact_type="OBJECT_RELATION",
        predicate="BELONGS_TO",
        object_ref="组织",
    )
    assert _dict(belongs_to.get("subject")).get("entity_refs") == ["订单"]
    assert _dict(belongs_to.get("object")).get("entity_refs") == ["组织"]

    withdrawal = _parent_fact(asset, "只有订单创建人可以撤回")
    withdrawal_subject = _dict(withdrawal.get("subject"))
    assert withdrawal["fact_type"] == "PERMISSION_RULE"
    assert withdrawal["modality"] == "MAY"
    assert withdrawal["action"]["canonical"] == "撤回"
    assert withdrawal_subject.get("actor_refs") == ["订单创建人"]
    assert withdrawal_subject.get("entity_refs") == ["订单"]
    assert _dict(withdrawal.get("object")).get("entity_refs") == ["订单"]
    assert withdrawal["condition_frame"]["kind"] == "ALL"
    assert withdrawal["condition_frame"]["combinator"] == "AND"
    assert withdrawal["condition_frame"]["conditions"] == [
        "本人创建",
        "尚未审批",
    ]

    then_branch = _parent_fact(asset, "如果库存充足则系统允许订单出库")
    then_subject = _dict(then_branch.get("subject"))
    then_frame = _dict(then_branch.get("condition_frame"))
    assert then_branch["fact_type"] == "PERMISSION_RULE"
    assert then_branch["modality"] == "MAY"
    assert then_branch["action"]["canonical"] == "发货"
    assert then_subject.get("actor_refs") == ["系统"]
    assert then_subject.get("entity_refs") == ["订单"]
    assert _dict(then_branch.get("object")).get("entity_refs") == ["订单"]
    assert then_frame["kind"] == "IF_THEN_ELSE"
    assert then_frame["conditions"] == ["库存充足"]
    assert then_frame["branch"] == "THEN"
    assert then_frame["branch_index"] == 0

    else_branch = _parent_fact(asset, "否则系统必须拒绝出库")
    else_subject = _dict(else_branch.get("subject"))
    else_frame = _dict(else_branch.get("condition_frame"))
    assert else_branch["fact_type"] == "BUSINESS_RULE"
    assert else_branch["modality"] == "MUST"
    assert else_branch["polarity"] == "POSITIVE"
    assert else_branch["action"]["canonical"] == "驳回"
    assert else_subject.get("actor_refs") == ["系统"]
    assert else_subject.get("entity_refs") == ["订单"]
    assert _dict(else_branch.get("object")).get("entity_refs") == ["订单"]
    assert else_frame["kind"] == "IF_THEN_ELSE"
    assert else_frame["conditions"] == ["库存充足"]
    assert else_frame["branch"] == "ELSE"
    assert else_frame["branch_index"] == 1
    assert "保留订单状态不变" in _list(else_branch.get("postconditions"))

    first_loss = _first_loss_analysis(measurement)
    assert first_loss["schema"] == (
        "qualibug.chinese-explicit-fact-first-loss-analysis.v1"
    )
    assert first_loss["repair_policy"].startswith("FIX_ONLY_THE_HIGHEST_IMPACT")
    assert len(first_loss["alignments"]) == 16

    threshold_status, checks = _threshold_status(measurement["metrics"])
    assert threshold_status == "PASS", {
        "metrics": measurement["metrics"],
        "first_loss": first_loss,
        "false_accepted_facts": measurement["false_accepted_facts"],
    }
    assert set(checks) == set(TARGETS)
    for metric, target in TARGETS.items():
        assert checks[metric]["measurable"] is True
        assert checks[metric]["meets_target"] is True
        assert float(measurement["metrics"][metric]) >= target
