from __future__ import annotations

from pathlib import Path

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
    _first_loss_analysis,
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

    first_loss = _first_loss_analysis(measurement)
    assert first_loss["schema"] == (
        "qualibug.chinese-explicit-fact-first-loss-analysis.v1"
    )
    assert first_loss["repair_policy"].startswith("FIX_ONLY_THE_HIGHEST_IMPACT")
    assert len(first_loss["alignments"]) == 12

    threshold_status, checks = _threshold_status(measurement["metrics"])
    assert threshold_status in {"PASS", "BELOW_TARGET"}
    assert set(checks) == {
        "fact_recall",
        "slot_exact_accuracy",
        "p0_exact_fact_recall",
    }
