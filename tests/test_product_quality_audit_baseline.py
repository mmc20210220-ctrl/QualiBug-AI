from __future__ import annotations

import json
from pathlib import Path

from benchmark_evaluator.product_quality.current_product_audit import (
    AUDIT_QUALITY_CLAIM,
    SAMPLE_SPECS,
    _candidate_ids_for_anchor,
    _review_worksheet,
)
from benchmark_evaluator.product_quality.semantic_funnel import build_semantic_capture


ROOT = Path(__file__).resolve().parents[1]
ANCHORS = (
    ROOT
    / "benchmark_evaluator"
    / "product_quality"
    / "fixtures"
    / "review_anchors.json"
)


def _load_anchors() -> dict:
    return json.loads(ANCHORS.read_text(encoding="utf-8"))


def test_audit_uses_only_three_frozen_representative_samples() -> None:
    assert tuple(SAMPLE_SPECS) == (
        "object_source_conflict",
        "benchmark_mall",
        "warehouse_e",
    )
    assert len(SAMPLE_SPECS["object_source_conflict"]) == 2
    assert len(SAMPLE_SPECS["benchmark_mall"]) == 7
    assert len(SAMPLE_SPECS["warehouse_e"]) == 4
    assert AUDIT_QUALITY_CLAIM == (
        "CURRENT_PRODUCT_CAPTURE_WITH_EXTERNAL_HUMAN_REVIEW_NOT_SELF_SCORED_MODEL_QUALITY"
    )


def test_review_anchors_are_external_and_exactly_present_in_frozen_sources() -> None:
    payload = _load_anchors()
    assert payload["ground_truth_generated_from_product_output"] is False
    anchors = payload["anchors"]
    assert len(anchors) == 17
    assert {row["sample_id"] for row in anchors} == {
        "object_source_conflict",
        "benchmark_mall",
        "warehouse_e",
    }

    for row in anchors:
        source_path = ROOT / row["source_path"]
        assert source_path.is_file(), row["anchor_id"]
        source_text = source_path.read_text(encoding="utf-8")
        assert row["exact_quote"] in source_text, row["anchor_id"]
        assert row["business_expectation"]
        assert row["review_question"]
        assert row["expected_surfaces"]


def test_candidate_narrowing_requires_exact_evidence_quote_not_title_similarity() -> None:
    anchor = {"exact_quote": "取消订单后必须释放库存"}
    requirement_analysis = {
        "findings": [
            {
                "finding_id": "finding:similar-title-only",
                "title": "取消订单释放库存",
                "evidence": [{"quote": "另一条库存规则"}],
            },
            {
                "finding_id": "finding:exact-evidence",
                "evidence": [{"quote": "业务要求：取消订单后必须释放库存。"}],
            },
        ]
    }
    test_analysis = {
        "obligations": [
            {
                "obligation_id": "obligation:exact",
                "evidence": [{"quote": "取消订单后必须释放库存"}],
            },
            {
                "obligation_id": "obligation:similar-only",
                "title": "订单取消库存回滚",
                "evidence": [{"quote": "库存不能为负"}],
            },
        ],
        "test_designs": [
            {
                "design_id": "design:exact",
                "evidence": [{"quote": "取消订单后必须释放库存"}],
            }
        ],
    }

    result = _candidate_ids_for_anchor(anchor, requirement_analysis, test_analysis)

    assert result == {
        "requirement_finding_ids": ["finding:exact-evidence"],
        "test_obligation_ids": ["obligation:exact"],
        "test_design_ids": ["design:exact"],
    }


def test_review_worksheet_never_auto_labels_product_quality() -> None:
    anchors_payload = {
        "anchors": [
            {
                "sample_id": "benchmark_mall",
                "anchor_id": "A-1",
                "exact_quote": "支付成功",
                "business_expectation": "支付成功后状态变化",
                "review_question": "是否有价值？",
                "expected_surfaces": ["test_obligation"],
                "source_path": "sample.md",
            }
        ]
    }
    worksheet = _review_worksheet(
        "benchmark_mall",
        anchors_payload,
        {"findings": []},
        {
            "obligations": [
                {
                    "obligation_id": "obligation:1",
                    "evidence": [{"quote": "支付成功后订单状态变更"}],
                }
            ],
            "test_designs": [],
        },
    )

    assert worksheet["review_status"] == "PENDING_HUMAN_REVIEW"
    assert worksheet["rows"][0]["human_verdict"] == "PENDING_REVIEW"
    assert worksheet["rows"][0]["human_notes"] == ""
    assert worksheet["rows"][0]["candidate_output_ids"]["test_obligation_ids"] == [
        "obligation:1"
    ]


def test_review_worksheet_exposes_semantic_funnel_without_turning_it_into_quality_score() -> None:
    quote = "BR-IDEM-001: 相同order_ref的订单不可重复创建，应返回409"
    anchors_payload = {
        "anchors": [
            {
                "sample_id": "warehouse_e",
                "anchor_id": "WMS-IDEM",
                "exact_quote": quote,
                "business_expectation": "幂等创建",
                "review_question": "是否形成有价值的测试义务？",
                "expected_surfaces": ["test_obligation", "test_design"],
                "source_path": "sample.md",
            }
        ]
    }
    semantic_capture = build_semantic_capture(
        {
            "business_fact_ledger": {
                "items": [
                    {
                        "fact_id": "fact:idem",
                        "kind": "IDEMPOTENCY",
                        "evidence": [{"source_id": "rules", "quote": quote}],
                    }
                ]
            },
            "enterprise_understanding_model": {
                "business_behaviors": [
                    {
                        "behavior_id": "behavior:idem",
                        "status": "INCOMPLETE",
                        "formal_business_rule": False,
                        "operation_ref": "",
                        "object_refs": ["order"],
                        "source_fact_ids": ["fact:idem"],
                        "evidence": [{"source_id": "rules", "quote": quote}],
                    }
                ]
            },
        }
    )

    worksheet = _review_worksheet(
        "warehouse_e",
        anchors_payload,
        {"findings": []},
        {"obligations": [], "test_designs": []},
        semantic_capture,
    )

    funnel = worksheet["rows"][0]["semantic_funnel"]
    assert funnel["first_break_stage"] == "SEMANTIC_GROUNDING"
    assert funnel["matched_fact_ids"] == ["fact:idem"]
    assert funnel["matched_behavior_ids"] == ["behavior:idem"]
    assert funnel["automatic_quality_verdict"] is False
    assert funnel["human_review_required"] is True
    assert worksheet["rows"][0]["human_verdict"] == "PENDING_REVIEW"


def test_audit_source_does_not_define_quality_scores_or_product_inference() -> None:
    source = (
        ROOT
        / "benchmark_evaluator"
        / "product_quality"
        / "current_product_audit.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "quality_score",
        "precision_threshold",
        "recall_threshold",
        "semantic_similarity",
        "model_confidence",
    )
    for token in forbidden:
        assert token not in source
