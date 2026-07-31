from __future__ import annotations

import json

from benchmark_evaluator.enterprise_understanding.report import (
    render_markdown_report,
    write_benchmark_outputs,
)


def _result() -> dict:
    return {
        "project_id": "demo",
        "status": "PASS",
        "ground_truth_fingerprint": "gt:1",
        "product_asset_fingerprint": "asset:1",
        "metrics": {},
        "alignment": {"alignments": []},
        "root_cause_analysis": {},
        "ingestion_evidence_measurement": {},
        "business_fact_slot_measurement": {
            "schema": "qualibug.enterprise-business-fact-slot-measurement.v1",
            "status": "PASS",
            "metrics": {
                "annotated_fact_count": 16,
                "fact_recall": 0.75,
                "exact_fact_rate": 0.5,
                "slot_exact_accuracy": 0.8,
                "p0_exact_fact_recall": 0.625,
                "source_locator_annotated_fact_count": 16,
                "source_locator_exact_fact_count": 15,
                "source_locator_exact_accuracy": 15 / 16,
                "accepted_fact_count_in_scope": 17,
                "supported_accepted_fact_count": 16,
                "accepted_fact_precision": 16 / 17,
                "false_accepted_rate": 1 / 17,
                "false_accepted_fact_count": 1,
                "missing_fact_count": 2,
                "ambiguous_fact_count": 1,
            },
            "alignments": [
                {
                    "ground_truth_id": "gt:1",
                    "alignment_status": "PARTIAL",
                    "candidate_id": "fact:1",
                }
            ],
            "false_accepted_facts": [
                {
                    "candidate_id": "fact:false",
                    "fact_type": "OBJECT_RELATION",
                    "reason": "ACCEPTED_FACT_NOT_SELECTED_BY_UNIQUE_GROUND_TRUTH_ALIGNMENT",
                }
            ],
            "ground_truth_generated_from_product_output": False,
            "model_writeback_allowed": False,
        },
    }


def test_fact_slot_measurement_is_a_first_class_benchmark_output(tmp_path) -> None:
    paths = write_benchmark_outputs(_result(), tmp_path)

    assert "business_fact_slot_measurement.json" in paths
    assert "business_fact_slot_alignments.json" in paths
    assert "business_fact_false_accepted.json" in paths
    measurement = json.loads(
        (tmp_path / "business_fact_slot_measurement.json").read_text(encoding="utf-8")
    )
    alignments = json.loads(
        (tmp_path / "business_fact_slot_alignments.json").read_text(encoding="utf-8")
    )
    false_accepted = json.loads(
        (tmp_path / "business_fact_false_accepted.json").read_text(encoding="utf-8")
    )
    assert measurement["metrics"]["annotated_fact_count"] == 16
    assert measurement["metrics"]["source_locator_annotated_fact_count"] == 16
    assert measurement["metrics"]["source_locator_exact_fact_count"] == 15
    assert measurement["metrics"]["false_accepted_fact_count"] == 1
    assert measurement["ground_truth_generated_from_product_output"] is False
    assert alignments[0]["candidate_id"] == "fact:1"
    assert false_accepted[0]["candidate_id"] == "fact:false"


def test_markdown_report_surfaces_explicit_fact_metrics() -> None:
    report = render_markdown_report(_result())

    assert "中文显式业务事实槽位测量" in report
    assert "人工标注事实数 | 16" in report
    assert "显式事实召回率 | 75.0%" in report
    assert "槽位精确准确率 | 80.0%" in report
    assert "P0显式事实精确召回率 | 62.5%" in report
    assert "精确地址标注事实数 | 16" in report
    assert "精确地址命中事实数 | 15" in report
    assert "事实级精确证据地址准确率 | 93.8%" in report
    assert "完整范围内ACCEPTED事实数 | 17" in report
    assert "ACCEPTED事实Precision | 94.1%" in report
    assert "误接受率 | 5.9%" in report
    assert "误接受事实数 | 1" in report
