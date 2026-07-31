from __future__ import annotations

from copy import deepcopy

import pytest

from benchmark_evaluator.enterprise_understanding import (
    DocumentGroundTruthValidationError,
    evaluate_document_ground_truth,
    run_benchmark,
    validate_document_ground_truth,
)


def _block(
    block_id: str,
    block_type: str,
    text: str,
    locator: str,
    order: int,
    **coordinates,
) -> dict:
    evidence = {
        "source_id": "source:rules",
        "source_hash": "sha256:rules",
        "source_locator": locator,
        "address_kind": coordinates.pop("address_kind", "EXACT_SOURCE_LOCATOR"),
        **coordinates,
    }
    return {
        "block_id": block_id,
        "type": block_type,
        "region": "body",
        "text": text,
        "source_hash": "sha256:rules",
        "source_locator": locator,
        "order": order,
        "evidence_address": evidence,
        **coordinates,
    }


def _item(blocks: list[dict]) -> dict:
    formal = len(blocks)
    return {
        "source_id": "source:rules",
        "filename": "rules.docx",
        "format": "docx",
        "blocks": blocks,
        "structure_receipt": {"status": "COMPLETE"},
        "evidence_closure_receipt": {
            "status": "PASS",
            "source_id": "source:rules",
            "filename": "rules.docx",
            "source_hash": "sha256:rules",
            "formal_authority_block_count": formal,
            "source_hash_bound_block_count": formal,
            "traceable_authority_block_count": formal,
            "exact_address_authority_block_count": formal,
            "untraceable_authority_block_count": 0,
            "weak_address_authority_block_count": 0,
            "locator_conflict_count": 0,
        },
        "ingestion_pipeline_receipt": {"status": "PASS"},
        "parsing_plan": {"status": "PASS"},
        "adapter_receipts": [{"adapter_name": "native_docx"}],
        "unsupported_content": [],
    }


def _asset(blocks: list[dict]) -> dict:
    return {
        "source_inventory": [{"source_id": "source:rules", "status": "active"}],
        "sources": [{"source_id": "source:rules", "filename": "rules.docx"}],
        "document_structure_assets": {
            "schema": "qualibug.enterprise-document-structure-assets.v1",
            "source_count": 1,
            "items": [_item(blocks)],
            "errors": [],
        },
        "enterprise_understanding_model": {
            "business_objects": [
                {
                    "object_id": "object:ticket",
                    "name": "工单",
                    "status": "CONFIRMED",
                    "evidence": [{"source_id": "source:rules"}],
                }
            ],
            "actors": [],
            "operations": [],
            "object_relations": [],
            "lifecycles": [],
            "rules": [],
            "business_behaviors": [],
            "unknowns": [],
            "conflicts": [],
        },
    }


def _profile() -> dict:
    return {
        "schema": "qualibug.document-ingestion-ground-truth-profile.v1",
        "scope_complete": True,
        "minimum_profile": {
            "sources": 1,
            "elements": 3,
            "reading_order_pairs": 2,
            "table_cells": 1,
        },
        "sources": [
            {
                "ground_truth_id": "gt:document-source:rules",
                "source_id": "source:rules",
                "filename": "rules.docx",
                "source_hash": "sha256:rules",
                "expected_format": "docx",
                "criticality": "P0",
                "annotation_status": "CONFIRMED",
            }
        ],
        "elements": [
            {
                "ground_truth_id": "gt:document-element:heading",
                "source_id": "source:rules",
                "block_type": "HEADING",
                "text": "退款规则",
                "source_locator": "rules.docx#paragraph=1;chars=0-4",
                "address_kind": "EXACT_SOURCE_LOCATOR",
                "order": 1,
                "criticality": "P0",
                "annotation_status": "CONFIRMED",
            },
            {
                "ground_truth_id": "gt:document-element:paragraph",
                "source_id": "source:rules",
                "block_type": "PARAGRAPH",
                "text": "客服可以查看工单",
                "source_locator": "rules.docx#paragraph=2;chars=0-8",
                "address_kind": "EXACT_SOURCE_LOCATOR",
                "order": 2,
                "criticality": "P0",
                "annotation_status": "CONFIRMED",
            },
            {
                "ground_truth_id": "gt:document-element:table-cell",
                "source_id": "source:rules",
                "block_type": "TABLE_CELL",
                "text": "状态",
                "source_locator": "rules.docx#table=1;table-cell=1,1",
                "address_kind": "EXACT_SOURCE_LOCATOR",
                "row_index": 1,
                "column_index": 1,
                "order": 3,
                "criticality": "P1",
                "annotation_status": "CONFIRMED",
            },
        ],
        "reading_order_pairs": [
            {
                "ground_truth_id": "gt:document-order:heading-paragraph",
                "source_id": "source:rules",
                "before_element_id": "gt:document-element:heading",
                "after_element_id": "gt:document-element:paragraph",
                "criticality": "P0",
                "annotation_status": "CONFIRMED",
            },
            {
                "ground_truth_id": "gt:document-order:paragraph-table",
                "source_id": "source:rules",
                "before_element_id": "gt:document-element:paragraph",
                "after_element_id": "gt:document-element:table-cell",
                "criticality": "P1",
                "annotation_status": "CONFIRMED",
            },
        ],
    }


def _exact_blocks() -> list[dict]:
    return [
        _block(
            "block:heading",
            "HEADING",
            "退款规则",
            "rules.docx#paragraph=1;chars=0-4",
            1,
        ),
        _block(
            "block:paragraph",
            "PARAGRAPH",
            "客服可以查看工单",
            "rules.docx#paragraph=2;chars=0-8",
            2,
        ),
        _block(
            "block:cell",
            "TABLE_CELL",
            "状态",
            "rules.docx#table=1;table-cell=1,1",
            3,
            row_index=1,
            column_index=1,
        ),
    ]


def _enterprise_ground_truth(profile: dict) -> dict:
    return {
        "schema": "qualibug.enterprise-understanding-ground-truth.v1",
        "project_id": "document-ground-truth-demo",
        "scope_complete": False,
        "business_objects": [
            {
                "ground_truth_id": "gt:object:ticket",
                "canonical_name": "工单",
                "criticality": "P0",
                "source_refs": ["source:rules"],
                "annotation_status": "CONFIRMED",
            }
        ],
        "actors": [],
        "operations": [],
        "object_relations": [],
        "lifecycles": [],
        "state_transitions": [],
        "business_rules": [],
        "business_behaviors": [],
        "conflicts": [],
        "expected_unknowns": [],
        "bug_dependencies": [],
        "document_ingestion_ground_truth": profile,
    }


def test_exact_human_profile_measures_heading_table_and_reading_order() -> None:
    profile = validate_document_ground_truth(_profile())
    assert profile is not None
    asset = _asset(_exact_blocks())
    before = deepcopy(asset)

    result = evaluate_document_ground_truth(profile, asset)
    metrics = result["metrics"]

    assert result["status"] == "PASS"
    assert result["highest_impact_gap"] == "NONE"
    assert result["profile_five_of_five_pass"] is True
    assert metrics["true_structure_recall_measured"] is True
    assert metrics["source_recall"] == 1.0
    assert metrics["strict_structure_element_recall"] == 1.0
    assert metrics["block_type_accuracy"] == 1.0
    assert metrics["exact_evidence_address_accuracy"] == 1.0
    assert metrics["table_cell_recall"] == 1.0
    assert metrics["reading_order_accuracy"] == 1.0
    assert result["fuzzy_or_llm_alignment_used"] is False
    assert result["automatic_winner_used"] is False
    assert asset == before


def test_duplicate_exact_candidates_are_ambiguous_and_no_winner_is_selected() -> None:
    profile = _profile()
    profile["scope_complete"] = False
    profile["minimum_profile"] = {"sources": 1, "elements": 1}
    profile["reading_order_pairs"] = []
    profile["elements"] = [
        {
            "ground_truth_id": "gt:document-element:duplicate",
            "source_id": "source:rules",
            "block_type": "PARAGRAPH",
            "text": "重复规则",
            "criticality": "P0",
            "annotation_status": "CONFIRMED",
        }
    ]
    validated = validate_document_ground_truth(profile)
    blocks = [
        _block("block:1", "PARAGRAPH", "重复规则", "rules.docx#paragraph=1", 1),
        _block("block:2", "PARAGRAPH", "重复规则", "rules.docx#paragraph=2", 2),
    ]

    result = evaluate_document_ground_truth(validated, _asset(blocks))
    alignment = result["element_alignments"][0]

    assert alignment["alignment_status"] == "AMBIGUOUS"
    assert alignment["candidate"] == {}
    assert len(alignment["candidate_keys"]) == 2
    assert result["highest_impact_gap"] == "DOCUMENT_STRUCTURE_ELEMENT_AMBIGUOUS"
    assert result["metrics"]["strict_structure_element_recall"] == 0.0
    assert result["automatic_winner_used"] is False


def test_wrong_address_and_reading_order_are_reported_not_hidden() -> None:
    profile = _profile()
    profile["elements"][0]["address_kind"] = "PAGE_BBOX"
    validated = validate_document_ground_truth(profile)
    blocks = _exact_blocks()
    blocks[0]["order"] = 4

    result = evaluate_document_ground_truth(validated, _asset(blocks))
    reasons = {row["reason_code"] for row in result["gap_distribution"]}

    assert result["metrics"]["strict_structure_element_recall"] < 1.0
    assert result["metrics"]["exact_evidence_address_accuracy"] < 1.0
    assert result["metrics"]["reading_order_accuracy"] < 1.0
    assert "DOCUMENT_STRUCTURE_ELEMENT_SLOT_WRONG" in reasons
    assert "DOCUMENT_READING_ORDER_WRONG" in reasons


def test_document_ground_truth_validation_rejects_unverifiable_order_pair() -> None:
    profile = _profile()
    profile["reading_order_pairs"][0]["after_element_id"] = "gt:missing"

    with pytest.raises(DocumentGroundTruthValidationError):
        validate_document_ground_truth(profile)


def test_existing_runner_emits_true_structure_outputs_and_next_target_none(tmp_path) -> None:
    profile = _profile()
    asset = _asset(_exact_blocks())
    before = deepcopy(asset)

    result = run_benchmark(
        _enterprise_ground_truth(profile),
        asset,
        output_dir=str(tmp_path),
    )
    ingestion = result["ingestion_evidence_measurement"]

    assert result["status"] == "PASS"
    assert result["next_ingestion_repair_target"] == "NONE"
    assert ingestion["summary"]["five_of_five_readiness_status"] == (
        "DECLARED_DOCUMENT_PROFILE_FIVE_OF_FIVE_PASS"
    )
    assert ingestion["summary"]["structure_block_recall_measured"] is True
    assert ingestion["document_ground_truth_measurement"]["status"] == "PASS"
    assert result["workflow_receipt"]["document_ground_truth_generated_from_product_output"] is False
    assert asset == before
    assert (tmp_path / "document_structure_ground_truth_alignment.json").exists()
    assert (tmp_path / "document_structure_ground_truth_metrics.json").exists()
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "人工Ground Truth真实结构测量" in report
    assert "当前声明的文档Profile已达到5/5门槛" in report
