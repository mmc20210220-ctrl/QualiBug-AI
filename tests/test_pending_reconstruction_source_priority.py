"""Generic reconstruction must not let compiler-only faces displace source tail."""
from __future__ import annotations

from ai_test_asset_center.recall_pending_continuation_authority import (
    complete_pending_continuation_rows,
)


def _exp(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "experiment_id": f"exp_{oid}",
        "compile_receipt": {"status": "COMPILED"},
    }


def _row(oid: str, *, variant: bool = False, unit: str = "") -> dict:
    row = {
        "obligation_id": oid,
        "risk_family": "validation",
        "confidence": 0.8,
        "pre_transport_executable": True,
    }
    if variant:
        row["compiled_variant_view"] = True
    if unit:
        row["coverage_unit_id"] = unit
    return row


def test_source_obligation_wins_one_remaining_generic_restore_slot() -> None:
    visible = "formal-visible"
    source_tail = "z-formal-tail"
    compiler_only = "a-compiler-only"
    obligations = [
        _row(visible),
        _row(source_tail),
        _row(compiler_only, variant=True),
    ]

    rows, receipt = complete_pending_continuation_rows(
        obligation_plan={
            "plan_authority": "obligation",
            "selected": [],
            "pending_next_round": [{"obligation_id": visible}],
            "pending_count": 2,
            "pending_truncated": 1,
        },
        obligations=obligations,
        experiments_by_obligation={
            row["obligation_id"]: _exp(row["obligation_id"])
            for row in obligations
        },
    )

    assert [row["obligation_id"] for row in rows] == [visible, source_tail]
    assert receipt["eligible_omitted_count"] == 2
    assert receipt["restored_count"] == 1
    assert receipt["restore_overflow_count"] == 1


def test_source_coverage_unit_wins_one_remaining_restore_slot() -> None:
    visible = _row("formal-visible", unit="u-visible")
    source_tail = _row("z-formal-tail", unit="u-source")
    compiler_only = _row(
        "a-compiler-only",
        variant=True,
        unit="u-compiler",
    )
    obligations = [visible, source_tail, compiler_only]

    rows, receipt = complete_pending_continuation_rows(
        obligation_plan={
            "plan_authority": "coverage_unit",
            "selected": [],
            "pending_next_round": [{
                "obligation_id": visible["obligation_id"],
                "coverage_unit_id": visible["coverage_unit_id"],
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

    assert [row["obligation_id"] for row in rows] == [
        visible["obligation_id"],
        source_tail["obligation_id"],
    ]
    assert receipt["eligible_omitted_count"] == 2
    assert receipt["restored_count"] == 1
