from __future__ import annotations

import json

from benchmark_evaluator.enterprise_understanding.report import (
    render_markdown_report,
    write_benchmark_outputs,
)


def _result() -> dict:
    return {
        "project_id": "implicit-demo",
        "status": "PASS",
        "ground_truth_fingerprint": "gt:implicit",
        "product_asset_fingerprint": "asset:implicit",
        "next_implicit_rule_repair_target": "IMPLICIT_RULE_AUTHORITY_OVERPROMOTION",
        "metrics": {},
        "alignment": {"alignments": []},
        "root_cause_analysis": {},
        "ingestion_evidence_measurement": {},
        "business_fact_slot_measurement": {},
        "implicit_rule_measurement": {
            "schema": "qualibug.enterprise-understanding-implicit-rule-measurement.v1",
            "status": "MEASURED",
            "next_repair_target": "IMPLICIT_RULE_AUTHORITY_OVERPROMOTION",
            "metrics": {
                "candidate_precision": 0.9,
                "candidate_recall": 0.8,
                "promotion_precision": 0.75,
                "promotion_recall": 0.6,
                "overpromotion_rate": 0.25,
                "criticality_weighted_overpromotion_rate": 0.1,
                "lifecycle_accuracy": 0.7,
                "stale_precision": 1.0,
                "stale_recall": 0.5,
                "source_version_traceability_rate": 0.8,
                "authoritative_operation_binding_recall": 0.5,
                "oracle_projection_recall": 0.5,
                "executable_projection_recall": 0.4,
                "runtime_observation_recall": None,
                "promotion_false_positive_count": 2,
                "candidate_false_negative_count": 3,
            },
            "alignments": [{"ground_truth_id": "gt:rule:1"}],
            "false_promotions": [{"ground_truth_id": "gt:rule:false"}],
            "missed_rules": [{"ground_truth_id": "gt:rule:missing"}],
            "lifecycle_errors": [{"ground_truth_id": "gt:rule:stale"}],
            "execution_bridge_gaps": [{"ground_truth_id": "gt:rule:exec"}],
            "ground_truth_entered_product_runtime": False,
            "model_writeback_allowed": False,
        },
    }


def test_implicit_rule_measurement_is_a_first_class_benchmark_output(tmp_path):
    paths = write_benchmark_outputs(_result(), tmp_path)

    expected_files = {
        "implicit_rule_measurement.json",
        "implicit_rule_metrics.json",
        "implicit_rule_alignments.json",
        "implicit_rule_false_promotions.json",
        "implicit_rule_missed_rules.json",
        "implicit_rule_lifecycle_errors.json",
        "implicit_rule_execution_bridge_gaps.json",
    }
    assert expected_files <= set(paths)
    measurement = json.loads(
        (tmp_path / "implicit_rule_measurement.json").read_text(encoding="utf-8")
    )
    metrics = json.loads(
        (tmp_path / "implicit_rule_metrics.json").read_text(encoding="utf-8")
    )
    assert measurement["ground_truth_entered_product_runtime"] is False
    assert measurement["model_writeback_allowed"] is False
    assert metrics["promotion_precision"] == 0.75


def test_markdown_report_surfaces_implicit_rule_governance_metrics():
    report = render_markdown_report(_result())

    assert "隐式规则治理质量测量" in report
    assert "候选发现精确率 | 90.0%" in report
    assert "权威晋升精确率 | 75.0%" in report
    assert "误晋升率 | 25.0%" in report
    assert "可执行投影召回率 | 40.0%" in report
    assert "隐式规则下一修复点：`IMPLICIT_RULE_AUTHORITY_OVERPROMOTION`" in report
