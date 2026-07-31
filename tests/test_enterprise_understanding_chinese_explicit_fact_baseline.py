from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
    build_chinese_first_comprehension,
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
    blocks = []
    order = 0
    for paragraph in [row.strip() for row in text.split("\n\n") if row.strip()]:
        order += 1
        block_type = "HEADING" if paragraph.startswith("#") else "PARAGRAPH"
        blocks.append(
            {
                "block_id": f"block:{order}",
                "type": block_type,
                "region": "body",
                "order": order,
                "text": paragraph.lstrip("# ") if block_type == "HEADING" else paragraph,
                "source_locator": f"source_rules.md#paragraph={order}",
                "evidence_address": {"address_kind": "EXACT_SOURCE_LOCATOR"},
            }
        )
    return {
        "source_id": "source:chinese-explicit-fact-baseline-v1",
        "filename": "source_rules.md",
        "source_locator": "source_rules.md#document",
        "text": text,
        "document_structure": {
            "schema": "qualibug.document-structure-ir.v1",
            "plain_text": text,
            "blocks": blocks,
        },
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
    assert receipt["business_fact_slot_annotation_count"] == 12
    assert receipt["confirmed_business_fact_slot_annotation_count"] == 12
    assert receipt["business_fact_ground_truth_generated_from_product_output"] is False
    assert ground_truth["scope_complete"] is False
    assert ground_truth["explicit_fact_scope_complete"] is True


def test_isolated_baseline_exit_status_requires_quality_targets() -> None:
    assert _quality_result_status("PASS", "PASS") == "PASS"
    assert _quality_result_status("PASS", "BELOW_TARGET") == "BELOW_TARGET"
    assert _quality_result_status("NOT_MEASURED", "NOT_MEASURED") == "NOT_MEASURED"

    assert _baseline_exit_code({"status": "PASS"}) == 0
    assert _baseline_exit_code({"status": "BELOW_TARGET"}) == 3
    assert _baseline_exit_code({"status": "BLOCKED"}) == 2
    assert _baseline_exit_code({"status": "NOT_MEASURED"}) == 2


def test_frozen_baseline_runs_existing_fact_mainline_without_quality_self_label(
    tmp_path: Path,
) -> None:
    source = _source()
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
    assert measurement["metrics"]["annotated_fact_count"] == 12
    assert measurement["metrics"]["measured_slot_count"] > 0
    assert measurement["fuzzy_or_llm_alignment_used"] is False
    assert measurement["automatic_winner_used"] is False
    assert measurement["ground_truth_generated_from_product_output"] is False
    assert asset["business_fact_candidate_ledger"]["all_candidates_terminal"] is True
    assert asset["business_fact_candidate_ledger"]["silent_drop_allowed"] is False

    normalization = asset["explicit_fact_semantic_normalization_receipt"]
    assert normalization["status"] == "PASS"
    assert normalization["existing_ledger_reused"] is True
    assert normalization["existing_source_backed_identity_vocabulary_reused"] is True
    assert normalization["governed_operation_binding"] is True
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

    first_loss = _first_loss_analysis(measurement)
    assert first_loss["schema"] == (
        "qualibug.chinese-explicit-fact-first-loss-analysis.v1"
    )
    assert first_loss["repair_policy"].startswith("FIX_ONLY_THE_HIGHEST_IMPACT")
    assert len(first_loss["alignments"]) == 12

    threshold_status, checks = _threshold_status(measurement["metrics"])
    assert threshold_status == "PASS", {
        "metrics": measurement["metrics"],
        "first_loss": first_loss,
    }
    assert set(checks) == set(TARGETS)
    for metric, target in TARGETS.items():
        assert checks[metric]["measurable"] is True
        assert checks[metric]["meets_target"] is True
        assert float(measurement["metrics"][metric]) >= target
