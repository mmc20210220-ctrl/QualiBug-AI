"""Planner truncation authority survives later preview-only row appends."""
from __future__ import annotations

from ai_test_asset_center.recall_pending_continuation_authority import (
    complete_pending_continuation_rows,
)


def _obl(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "risk_family": "validation",
        "confidence": 0.8,
        "pre_transport_executable": True,
    }


def _exp(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "experiment_id": f"exp_{oid}",
        "compile_receipt": {"status": "COMPILED"},
    }


def test_pending_truncated_restores_tail_even_when_deferred_append_grows_preview() -> None:
    visible = "formal-visible"
    omitted = "formal-omitted-tail"
    selected_deferred = "selected-budget-deferred"
    obligations = [
        _obl(visible),
        _obl(omitted),
        _obl(selected_deferred),
    ]

    rows, receipt = complete_pending_continuation_rows(
        obligation_plan={
            "plan_authority": "obligation",
            "selected": [{"obligation_id": selected_deferred}],
            # Planner originally exposed one of two pending rows; the executor
            # later appended its selected-but-budget-deferred row to this view.
            "pending_next_round": [
                {"obligation_id": visible},
                {"obligation_id": selected_deferred},
            ],
            "pending_count": 2,
            "pending_truncated": 1,
        },
        obligations=obligations,
        experiments_by_obligation={
            row["obligation_id"]: _exp(row["obligation_id"])
            for row in obligations
        },
    )

    assert [row["obligation_id"] for row in rows] == [
        visible,
        selected_deferred,
        omitted,
    ]
    assert receipt["declared_restore_budget"] == 1
    assert receipt["restored_count"] == 1
    assert receipt["restore_overflow_count"] == 0
