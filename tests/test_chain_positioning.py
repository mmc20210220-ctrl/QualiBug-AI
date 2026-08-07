"""Chain positioning receipt (链路定位 ②) — 阶段级卡点定位 + first-loss 判定.

Covers: 9-stage projection from existing receipts, first-loss-stage
determination, blocked reason-code breakdown, guidance-enriched top blockers,
fail-soft degradation on missing receipts, markdown report, and the
two-run stage diff (champion/challenger positioning).
"""

from __future__ import annotations

from ai_test_asset_center.chain_positioning import (
    CHAIN_POSITIONING_SCHEMA,
    STAGE_ORDER,
    build_chain_positioning,
    render_chain_diff_markdown,
    render_chain_positioning_markdown,
)
from ai_test_asset_center.discovery_quality_projection import SCHEMA_VERSION


def _synthetic_result(**overrides: object) -> dict:
    result: dict = {
        "behavior_ir_input_receipt": {"api_operation_count": 20, "runtime_actor_count": 3},
        "runtime_source_overlay_receipt": {"status": "CONSUMED"},
        "behavior_ir": {"entities": [{"id": "e1"}], "invariants": [{"id": "i1"}]},
        "test_obligations": {
            "mainline_reasoner_report": {
                "total_hypotheses": 8,
                "llm_engines": ["causality", "invariant", "saga"],
                "failed_engines": ["saga"],
                "degraded_engines": [],
                "engine_error_codes": {"saga": "http_429"},
                "model_attempt_count": 4,
                "model_response_count": 3,
            },
            "obligations": [{"obligation_id": "o1"}, {"obligation_id": "o2"}],
        },
        "experiment_compile": {
            "all_experiments": [{"experiment_id": "e1"}],
            "blocked_experiments": [
                {
                    "experiment_id": "b1",
                    "compile_receipt": {
                        "status": "BLOCKED",
                        "reason_code": "BLOCKED_MISSING_BINDING",
                        "detail": "FIELD_NOT_BOUND",
                    },
                }
            ],
        },
        "obligation_attempt_ledger": {
            "selected_count": 1,
            "terminal_count": 1,
            "attempts": [
                {
                    "obligation_id": "o1",
                    "terminal_status": "DELIVERABLE",
                    "reason_code": "",
                    "observation_receipt_ids": ["obs-1"],
                    "oracle_reason_code": "",
                }
            ],
        },
        "formal_count_projection": {
            "schema_version": SCHEMA_VERSION,
            "canonical_defect_ids": ["d1"],
            "delivery_occurrence_finding_ids": ["d1"],
            "formal_customer_deliverable_count": 1,
            "delivery_occurrence_count": 1,
        },
    }
    result.update(overrides)
    return result


def test_stage_order_and_first_loss_at_hypothesis() -> None:
    receipt = build_chain_positioning(_synthetic_result())
    assert receipt["schema_version"] == CHAIN_POSITIONING_SCHEMA
    stages = [row["stage"] for row in receipt["stages"]]
    assert stages == list(STAGE_ORDER)
    assert receipt["first_loss"]["stage"] == "hypothesis"
    assert receipt["first_loss"]["basis"] == "blocked_count=1"


def test_blocked_experiment_reason_breakdown_and_guidance() -> None:
    receipt = build_chain_positioning(_synthetic_result())
    stage = next(row for row in receipt["stages"] if row["stage"] == "experiment_compile")
    assert stage["blocked_count"] == 1
    assert stage["reason_code_breakdown"] == {"BLOCKED_MISSING_BINDING": 1}
    top = receipt["chain_summary"]["top_blocker_codes"]
    codes = {row["reason_code"] for row in top}
    assert "BLOCKED_MISSING_BINDING" in codes
    assert "http_429" in codes
    for row in top:
        assert row["meaning"] and row["suggested_action"]
    assert receipt["chain_summary"]["total_deliverables"] == 1


def test_no_significant_loss_run() -> None:
    result = _synthetic_result()
    result["test_obligations"]["mainline_reasoner_report"]["failed_engines"] = []
    result["test_obligations"]["mainline_reasoner_report"]["engine_error_codes"] = {}
    result["experiment_compile"]["blocked_experiments"] = []
    receipt = build_chain_positioning(result)
    assert receipt["first_loss"]["stage"] == "NO_SIGNIFICANT_LOSS"


def test_zero_output_comprehension_is_first_loss() -> None:
    result = _synthetic_result()
    result["behavior_ir"] = {"entities": [], "invariants": []}
    result["test_obligations"]["mainline_reasoner_report"]["failed_engines"] = []
    result["test_obligations"]["mainline_reasoner_report"]["engine_error_codes"] = {}
    receipt = build_chain_positioning(result)
    assert receipt["first_loss"]["stage"] == "comprehension"
    assert "zero_output" in receipt["first_loss"]["basis"]


def test_fail_soft_when_ledger_or_formal_projection_missing() -> None:
    result = _synthetic_result()
    del result["obligation_attempt_ledger"]
    del result["formal_count_projection"]
    receipt = build_chain_positioning(result)  # must not raise
    assert len(receipt["stages"]) == len(STAGE_ORDER)
    execution = next(row for row in receipt["stages"] if row["stage"] == "execution")
    assert "attempt_ledger_unavailable" in execution["note"]
    delivery = next(row for row in receipt["stages"] if row["stage"] == "delivery_gate")
    assert "formal_projection_unavailable" in delivery["note"]


def test_markdown_report_is_diagnostic_and_contains_guidance() -> None:
    receipt = build_chain_positioning(_synthetic_result())
    md = render_chain_positioning_markdown(receipt)
    assert "第一损失点" in md
    assert "BLOCKED_MISSING_BINDING" in md
    assert "非交付证据" in md


def test_diff_detects_stage_migration_and_reason_code_changes() -> None:
    run_a = _synthetic_result()
    run_b = _synthetic_result()
    run_b["test_obligations"]["mainline_reasoner_report"]["failed_engines"] = []
    run_b["test_obligations"]["mainline_reasoner_report"]["engine_error_codes"] = {}
    run_b["experiment_compile"]["blocked_experiments"] = [
        {
            "experiment_id": "b1",
            "compile_receipt": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_UNSUPPORTED_ADAPTER",
                "detail": "event_observer_http",
            },
        }
    ]
    md = render_chain_diff_markdown(run_a, run_b)
    assert "假设生成" in md
    assert "新增:BLOCKED_UNSUPPORTED_ADAPTER" in md
    assert "消失:http_429" in md
    assert "迁移" in md
    assert "不构成交付证据" in md


def test_diff_with_unchanged_first_loss() -> None:
    run_a = _synthetic_result()
    run_b = _synthetic_result()
    md = render_chain_diff_markdown(run_a, run_b)
    assert "保持不变" in md
