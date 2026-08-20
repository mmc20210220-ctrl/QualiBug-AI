"""Later continuation attempts must win over earlier initial receipts."""
from __future__ import annotations


def _batch(oid: str, *, status: str, marker: str) -> dict:
    return {
        "compile_results": {
            oid: {"status": "COMPILED", "marker": f"compile-{marker}"}
        },
        "execution_results": {
            oid: {"status": status, "marker": f"execution-{marker}"}
        },
        "gate_results": {
            oid: {"status": "REJECTED", "marker": f"gate-{marker}"}
        },
    }


def test_expansion_follow_on_overwrites_stale_initial_expansion_receipt() -> None:
    from ai_test_asset_center.discovery_stage_result_merge import (
        merge_discovery_stage_results,
    )

    oid = "retry-expansion"
    compile_results, execution_results, gate_results = (
        merge_discovery_stage_results(
            main_initial_batch={},
            expansion_initial_batch=_batch(
                oid,
                status="BLOCKED",
                marker="initial-expansion",
            ),
            feedback_initial_batch={},
            main_follow_on_batches=[],
            expansion_follow_on_batches=[
                _batch(
                    oid,
                    status="EXECUTED",
                    marker="follow-on-success",
                )
            ],
        )
    )

    assert compile_results[oid]["marker"] == "compile-follow-on-success"
    assert execution_results[oid] == {
        "status": "EXECUTED",
        "marker": "execution-follow-on-success",
    }
    assert gate_results[oid]["marker"] == "gate-follow-on-success"


def test_feedback_initial_is_older_than_any_continuation_follow_on() -> None:
    from ai_test_asset_center.discovery_stage_result_merge import (
        merge_discovery_stage_results,
    )

    oid = "feedback-retry"
    _, execution_results, _ = merge_discovery_stage_results(
        main_initial_batch={},
        expansion_initial_batch={},
        feedback_initial_batch=_batch(
            oid,
            status="BLOCKED",
            marker="feedback-initial",
        ),
        main_follow_on_batches=[
            _batch(
                oid,
                status="EXECUTED",
                marker="main-follow-on",
            )
        ],
        expansion_follow_on_batches=[],
    )

    assert execution_results[oid]["status"] == "EXECUTED"
    assert execution_results[oid]["marker"] == "execution-main-follow-on"


def test_main_initial_is_overwritten_by_later_main_continuation() -> None:
    from ai_test_asset_center.discovery_stage_result_merge import (
        merge_discovery_stage_results,
    )

    oid = "main-retry"
    _, execution_results, _ = merge_discovery_stage_results(
        main_initial_batch=_batch(
            oid,
            status="BLOCKED",
            marker="main-initial",
        ),
        expansion_initial_batch={},
        feedback_initial_batch={},
        main_follow_on_batches=[
            _batch(
                oid,
                status="EXECUTED",
                marker="main-follow-on",
            )
        ],
        expansion_follow_on_batches=[],
    )

    assert execution_results[oid]["status"] == "EXECUTED"
    assert execution_results[oid]["marker"] == "execution-main-follow-on"
