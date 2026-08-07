"""Chain positioning receipt (链路定位 ②) — 阶段级卡点定位 + first-loss 判定.

Covers: 9-stage projection from existing receipts, first-loss-stage
determination, blocked reason-code breakdown, guidance-enriched top blockers,
fail-soft degradation on missing receipts, markdown report, and the
two-run stage diff (champion/challenger positioning).
"""

from __future__ import annotations

from collections import Counter

from ai_test_asset_center.chain_positioning import (
    CHAIN_POSITIONING_SCHEMA,
    STAGE_ORDER,
    build_chain_positioning,
    render_chain_diff_markdown,
    render_chain_positioning_markdown,
)
from ai_test_asset_center.discovery_quality_projection import SCHEMA_VERSION


def _ledger(specs: list[dict]) -> dict:
    """Build a schema-valid obligation-attempt ledger through the production
    builder (same path the mainline uses), so the positioning layer reads a
    ledger the product would actually accept."""
    from ai_test_asset_center._obligation_attempt_ledger_single_occurrence_mechanics import (
        build_obligation_attempt_ledger,
    )

    selected: list[dict] = []
    compile_results: dict = {}
    execution_results: dict = {}
    gate_results: dict = {}
    for index, spec in enumerate(specs):
        obligation_id = spec.get("obligation_id", f"o{index + 1}")
        selected.append({
            "obligation_id": obligation_id,
            "risk_family": "positioning",
            "required_operations": [],
            "required_actors": [],
            "adapter": "http_api",
            "planning_round": 1,
            "source_refs": [],
        })
        if spec.get("compile_blocked"):
            compile_results[obligation_id] = {
                "status": "BLOCKED",
                "reason_code": spec.get("reason_code", "BLOCKED_MISSING_BINDING"),
            }
            continue
        compile_results[obligation_id] = {"status": "COMPILED", "experiment_id": f"exp-{obligation_id}"}
        if spec.get("execution_blocked"):
            execution_results[obligation_id] = {
                "status": "BLOCKED",
                "reason_code": spec.get("reason_code", "BLOCKED_EXECUTION"),
            }
            continue
        execution_results[obligation_id] = {
            "status": "EXECUTED",
            "execution_id": f"exec-{obligation_id}",
            "observation_receipt_ids": spec.get("observation_receipt_ids", []),
        }
        gate_results[obligation_id] = {
            "status": spec.get("gate_status", "REJECTED"),
            "reason_code": spec.get("oracle_reason_code") or "ORACLE_NOT_VIOLATED",
        }
        if spec.get("gate_status") == "DELIVERABLE":
            gate_results[obligation_id]["finding_id"] = spec.get("finding_id", f"f-{obligation_id}")
    return build_obligation_attempt_ledger(
        mainline_run={"run_id": "RUN-POSITIONING", "campaign_id": "CMP-POSITIONING"},
        selected=selected,
        compile_results=compile_results,
        execution_results=execution_results,
        gate_results=gate_results,
    )


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
        "obligation_attempt_ledger": _ledger([
            {
                "obligation_id": "o1",
                "gate_status": "REJECTED",
                "oracle_reason_code": "ORACLE_NOT_VIOLATED",
                "observation_receipt_ids": ["obs-1"],
            }
        ]),
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


# ---------------------------------------------------------------------------
# 陌生系统适用性: classification must come from the SSOT registries, never
# from closed local lists.  Future statuses / reason codes emitted by any
# target system stay classified (or visibly unclassified), never silently
# dropped.
# ---------------------------------------------------------------------------


def test_future_observer_family_code_is_classified_via_registry() -> None:
    from ai_test_asset_center.blocker_attribution import (
        REASON_CODE_REGISTRY,
        register_reason_code,
    )
    from ai_test_asset_center import blocker_attribution as _ba

    # A code that NO hardcoded list knows about, registered with the observer
    # family: the positioning layer must still attribute it to observation.
    register_reason_code(
        "FUTURE_OBSERVER_CODE_XYZ",
        attribution="OBSERVER_CAPABILITY_GAP",
        recoverability="RECOVERABLE",
        meaning="未来观察家族码",
        likely_root_cause="未来观察器",
        suggested_action="未来动作",
    )
    try:
        result = _synthetic_result(
            obligation_attempt_ledger=_ledger([
                {
                    "obligation_id": "o1",
                    "execution_blocked": True,
                    "reason_code": "FUTURE_OBSERVER_CODE_XYZ",
                }
            ])
        )
        receipt = build_chain_positioning(result)
        observation = next(row for row in receipt["stages"] if row["stage"] == "observation")
        assert observation["blocked_count"] == 1
        assert observation["reason_code_breakdown"] == {"FUTURE_OBSERVER_CODE_XYZ": 1}
        execution = next(row for row in receipt["stages"] if row["stage"] == "execution")
        assert execution["blocked_count"] == 1
    finally:
        REASON_CODE_REGISTRY.pop("FUTURE_OBSERVER_CODE_XYZ", None)
        _ba._CODE_GUIDANCE.pop("FUTURE_OBSERVER_CODE_XYZ", None)


def test_unknown_terminal_status_fails_visibly_not_silently() -> None:
    """The ledger authority rejects data it does not know; the positioning
    layer surfaces that as a visible note instead of silently misattributing
    the attempt."""
    result = _synthetic_result()
    result["obligation_attempt_ledger"]["attempts"][0]["terminal_status"] = "SOME_FUTURE_STATUS"
    receipt = build_chain_positioning(result)
    execution = next(row for row in receipt["stages"] if row["stage"] == "execution")
    assert "attempt_ledger_unavailable" in execution["note"]


def test_blocking_reason_breakdown_derives_from_registry() -> None:
    result = _synthetic_result(
        obligation_attempt_ledger=_ledger([
            {
                "obligation_id": "o1",
                "execution_blocked": True,
                "reason_code": "BLOCKED_UNSUPPORTED_ADAPTER",
            },
            {
                "obligation_id": "o2",
                "gate_status": "REJECTED",
                "oracle_reason_code": "ORACLE_NOT_VIOLATED",  # non-blocking family
                "observation_receipt_ids": ["obs-1"],
            },
        ])
    )
    receipt = build_chain_positioning(result)
    execution = next(row for row in receipt["stages"] if row["stage"] == "execution")
    # only the blocking reason appears; the normal-outcome code is not a blocker
    assert execution["reason_code_breakdown"] == {"BLOCKED_UNSUPPORTED_ADAPTER": 1}
    assert execution["blocked_count"] == 1
    # verdict: o2 completed with a normal verdict -> counted as output
    verdict = next(row for row in receipt["stages"] if row["stage"] == "verdict")
    assert verdict["output_count"] == 1
    assert verdict["blocked_count"] == 0
