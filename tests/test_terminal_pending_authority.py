"""Regressions for terminal sealing from the full continuation authority."""
from __future__ import annotations


def _accounting_row(
    oid: str,
    *,
    confidence: float = 0.8,
    coverage_unit_id: str = "",
) -> dict:
    row = {
        "obligation_id": oid,
        "risk_family": "validation",
        "confidence": confidence,
        "selection_status": "DEFERRED_NOT_SELECTED",
        "pre_transport_executable": True,
    }
    if coverage_unit_id:
        row["coverage_unit_id"] = coverage_unit_id
    return row


def _experiment(oid: str, *, coverage_unit_id: str = "") -> dict:
    row = {
        "obligation_id": oid,
        "experiment_id": f"exp_{oid}",
        "compile_receipt": {"status": "COMPILED"},
    }
    if coverage_unit_id:
        row["coverage_unit_id"] = coverage_unit_id
    return row


def test_manual_terminal_sealing_restores_tail_beyond_public_preview() -> None:
    from ai_test_asset_center.discovery_runtime_execution_terminal import (
        _manual_terminal_receipts,
    )

    ids = [f"pending-{index:03d}" for index in range(137)]
    preview_ids = ids[:32]
    accounting_rows = [_accounting_row(oid) for oid in ids]
    experiments = {oid: _experiment(oid) for oid in ids}
    compile_results: dict[str, dict] = {}
    execution_results: dict[str, dict] = {}

    _manual_terminal_receipts(
        selected_rows=accounting_rows,
        experiments_by_obligation=experiments,
        obligation_plan={
            "plan_authority": "obligation",
            "selected": [],
            "pending_next_round": [
                {
                    "obligation_id": oid,
                    "not_in_plan_reason": "CONTINUATION_PENDING",
                }
                for oid in preview_ids
            ],
            "pending_count": len(ids),
        },
        runtime_contract={
            "status": "approved",
            "approved_base_url": "http://example.invalid",
        },
        compile_results=compile_results,
        execution_results=execution_results,
    )

    assert set(execution_results) == set(ids)
    assert all(
        row["status"] == "DEFERRED"
        and row["reason_code"] == "OBLIGATION_BUDGET_REACHED"
        for row in execution_results.values()
    )
    assert execution_results[ids[-1]]["not_in_plan_reason"] == (
        "CONTINUATION_VIEW_TRUNCATED"
    )


def test_terminal_pending_coverage_unit_rebuild_skips_completed_unit_alternates() -> None:
    from ai_test_asset_center.discovery_runtime_execution_terminal import (
        _terminal_pending_rows,
    )

    rows = [
        _accounting_row("a1", confidence=0.9, coverage_unit_id="unit-a"),
        _accounting_row("a2", confidence=0.7, coverage_unit_id="unit-a"),
        _accounting_row("b1", confidence=0.9, coverage_unit_id="unit-b"),
        _accounting_row("b2", confidence=0.7, coverage_unit_id="unit-b"),
    ]
    experiments = {
        row["obligation_id"]: _experiment(
            row["obligation_id"],
            coverage_unit_id=row["coverage_unit_id"],
        )
        for row in rows
    }

    pending = _terminal_pending_rows(
        selected_rows=rows,
        experiments_by_obligation=experiments,
        obligation_plan={
            "plan_authority": "coverage_unit",
            "selected": [],
            "pending_next_round": [],
            "pending_count": 1,
        },
        execution_results={
            "a1": {"status": "EXECUTED", "experiment_id": "exp_a1"},
        },
    )

    assert pending == [{
        "obligation_id": "b1",
        "risk_family": "validation",
        "coverage_unit_id": "unit-b",
        "not_in_plan_reason": "CONTINUATION_VIEW_TRUNCATED",
        "continuation_origin": "terminal_reconstructed_coverage_unit",
    }]


def test_terminal_pending_mixed_retry_and_fresh_never_exceeds_pending_count() -> None:
    from ai_test_asset_center.discovery_runtime_execution_terminal import (
        _terminal_pending_rows,
    )

    ids = ["visible", "retry", "fresh-a", "fresh-b"]
    rows = [_accounting_row(oid) for oid in ids]
    experiments = {oid: _experiment(oid) for oid in ids}

    pending = _terminal_pending_rows(
        selected_rows=rows,
        experiments_by_obligation=experiments,
        obligation_plan={
            "plan_authority": "obligation",
            "selected": [],
            "pending_next_round": [{"obligation_id": "visible"}],
            "pending_count": 3,
            "blocked_retry_pool": [{
                "obligation_id": "retry",
                "block_reason": "BLOCKED_MISSING_BINDING",
            }],
            "blocked_retry_pool_count": 1,
        },
        execution_results={},
    )

    assert len(pending) == 3
    assert [row["obligation_id"] for row in pending] == [
        "visible",
        "retry",
        "fresh-a",
    ]
