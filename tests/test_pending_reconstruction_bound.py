"""Generic pending reconstruction may fill only the planner-declared gap."""
from __future__ import annotations

from ai_test_asset_center.recall_pending_continuation_authority import (
    complete_pending_continuation_rows,
)


def _obl(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "risk_family": "validation",
        "confidence": 0.8,
        "required_operations": [],
        "required_actors": [],
        "required_observers": ["http_response"],
        "property": {"template": "input_boundary_validation"},
    }


def _exp(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "experiment_id": f"exp_{oid}",
        "compile_receipt": {"status": "COMPILED"},
    }


def test_obligation_reconstruction_does_not_widen_past_declared_pending_count() -> None:
    ids = ["visible", "allowed-missing", "extra-a", "extra-b"]
    rows, receipt = complete_pending_continuation_rows(
        obligation_plan={
            "plan_authority": "obligation",
            "selected": [],
            "pending_next_round": [{"obligation_id": "visible"}],
            "pending_count": 2,
            "pending_truncated": 1,
        },
        obligations=[_obl(oid) for oid in ids],
        experiments_by_obligation={oid: _exp(oid) for oid in ids},
    )

    assert len(rows) == 2
    assert rows[0]["obligation_id"] == "visible"
    assert receipt["restored_count"] == 1
    assert receipt["declared_restore_budget"] == 1
    assert receipt["eligible_omitted_count"] == 3
    assert receipt["restore_overflow_count"] == 2


def test_coverage_unit_reconstruction_caps_units_to_declared_gap() -> None:
    obligations = [
        {**_obl("visible"), "coverage_unit_id": "u-visible"},
        {**_obl("a"), "coverage_unit_id": "u-a"},
        {**_obl("b"), "coverage_unit_id": "u-b"},
        {**_obl("c"), "coverage_unit_id": "u-c"},
    ]
    rows, receipt = complete_pending_continuation_rows(
        obligation_plan={
            "plan_authority": "coverage_unit",
            "selected": [],
            "pending_next_round": [{
                "obligation_id": "visible",
                "coverage_unit_id": "u-visible",
            }],
            "pending_count": 2,
            "pending_truncated": 1,
        },
        obligations=obligations,
        experiments_by_obligation={
            row["obligation_id"]: _exp(row["obligation_id"])
            for row in obligations
        },
    )

    assert len(rows) == 2
    assert rows[0]["obligation_id"] == "visible"
    assert receipt["restored_count"] == 1
    assert receipt["eligible_omitted_count"] == 3
    assert receipt["restore_overflow_count"] == 2
