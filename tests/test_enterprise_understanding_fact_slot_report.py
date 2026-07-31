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
                "annotated_fact_count": 12,
                "fact_recall": 0.75,
                "exact_fact_rate": 0.5,
                "slot_exact_accuracy": 0.8,
                "p0_exact_fact_recall": 0.625,
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
            "ground_truth_generated_from_product_output": False,
            "model_writeback_allowed": False,
        },
    }


def test_fact_slot_measurement_is_a_first_class_benchmark_output(tmp_path) -> None:
    paths = write_benchmark_outputs(_result(), tmp_path)

    assert "business_fact_slot_measurement.json" in paths
    assert "business_fact_slot_alignments.json" in paths
    measurement = json.loads(
        (tmp_path / "business_fact_slot_measurement.json").read_text(encoding="utf-8")
    )
    alignments = json.loads(
        (tmp_path / "business_fact_slot_alignments.json").read_text(encoding="utf-8")
    )
    assert measurement["metrics"]["annotated_fact_count"] == 12
    assert measurement["ground_truth_generated_from_product_output"] is False
    assert alignments[0]["candidate_id"] == "fact:1"


def test_markdown_report_surfaces_explicit_fact_metrics() -> None:
    report = render_markdown_report(_result())

    assert "中文显式业务事实槽位测量" in report
    assert "显式事实召回率 | 75.0%" in report
    assert "槽位精确准确率 | 80.0%" in report
    assert "P0显式事实精确召回率 | 62.5%" in report
