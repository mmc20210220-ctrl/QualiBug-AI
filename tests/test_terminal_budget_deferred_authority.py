"""Terminal sealing must honor the exact budget-deferred resume authority."""
from __future__ import annotations


def _row(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "risk_family": "validation",
        "confidence": 0.8,
        "selection_status": "DEFERRED_NOT_SELECTED",
        "pre_transport_executable": True,
    }


def _exp(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "experiment_id": f"exp_{oid}",
        "compile_receipt": {"status": "COMPILED"},
    }


def test_terminal_sealing_uses_exact_budget_deferred_identity_not_sorted_guess() -> None:
    from ai_test_asset_center.discovery_runtime_execution_terminal import (
        _terminal_pending_rows,
    )

    ids = ["a", "b", "c"]
    pending = _terminal_pending_rows(
        selected_rows=[_row(oid) for oid in ids],
        experiments_by_obligation={oid: _exp(oid) for oid in ids},
        obligation_plan={
            "plan_authority": "obligation",
            "selected": [{"obligation_id": oid} for oid in ids],
            "pending_next_round": [],
            "pending_count": 1,
            "pending_truncated": 1,
            "budget_deferred_pool": [{"obligation_id": "c"}],
            "budget_deferred_pool_count": 1,
        },
        execution_results={},
    )

    assert pending == [{
        "obligation_id": "c",
        "risk_family": "validation",
        "coverage_unit_id": "",
        "not_in_plan_reason": "BUDGET_DEFERRED",
        "continuation_origin": "budget_deferred_pool",
    }]


def test_terminal_budget_deferred_variant_projects_to_formal_base() -> None:
    from ai_test_asset_center.discovery_runtime_execution_terminal import (
        _terminal_pending_rows,
    )

    base = "base"
    variant = "base__v_abcdef123456"
    pending = _terminal_pending_rows(
        selected_rows=[_row(base)],
        experiments_by_obligation={variant: _exp(variant)},
        obligation_plan={
            "plan_authority": "obligation",
            "selected": [{"obligation_id": variant}],
            "pending_next_round": [],
            "pending_count": 1,
            "pending_truncated": 1,
            "budget_deferred_pool": [{"obligation_id": variant}],
            "budget_deferred_pool_count": 1,
        },
        execution_results={},
    )

    assert pending == [{
        "obligation_id": base,
        "risk_family": "validation",
        "coverage_unit_id": "",
        "not_in_plan_reason": "BUDGET_DEFERRED",
        "continuation_origin": "budget_deferred_variant_formal_projection",
    }]
