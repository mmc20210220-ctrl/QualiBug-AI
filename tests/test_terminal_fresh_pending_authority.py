"""Terminal sealing must consume exact fresh continuation identity."""
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


def test_terminal_uses_exact_fresh_identity_not_sorted_inference() -> None:
    from ai_test_asset_center.discovery_runtime_execution_terminal import (
        _terminal_pending_rows,
    )

    ids = ["a", "b", "c"]
    pending = _terminal_pending_rows(
        selected_rows=[_row(oid) for oid in ids],
        experiments_by_obligation={oid: _exp(oid) for oid in ids},
        obligation_plan={
            "plan_authority": "obligation",
            "selected": [],
            "pending_next_round": [],
            "pending_count": 1,
            "pending_truncated": 1,
            "fresh_pending_pool": [{"obligation_id": "c"}],
            "fresh_pending_pool_count": 1,
        },
        execution_results={},
    )

    assert pending == [{
        "obligation_id": "c",
        "risk_family": "validation",
        "coverage_unit_id": "",
        "not_in_plan_reason": "CONTINUATION_PENDING",
        "continuation_origin": "fresh_pending_pool",
    }]


def test_terminal_fresh_variant_projects_to_formal_base() -> None:
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
            "selected": [],
            "pending_next_round": [],
            "pending_count": 1,
            "fresh_pending_pool": [{"obligation_id": variant}],
            "fresh_pending_pool_count": 1,
        },
        execution_results={},
    )

    assert pending == [{
        "obligation_id": base,
        "risk_family": "validation",
        "coverage_unit_id": "",
        "not_in_plan_reason": "CONTINUATION_PENDING",
        "continuation_origin": "fresh_pending_variant_formal_projection",
    }]


def test_manual_terminal_marks_fresh_tail_as_continuation_not_budget() -> None:
    from ai_test_asset_center.discovery_runtime_execution_terminal import (
        _manual_terminal_receipts,
    )

    oid = "fresh-tail"
    compile_results = {
        oid: {"status": "COMPILED", "experiment_id": f"exp_{oid}"},
    }
    execution_results: dict[str, dict] = {}

    _manual_terminal_receipts(
        selected_rows=[_row(oid)],
        experiments_by_obligation={oid: _exp(oid)},
        obligation_plan={
            "plan_authority": "obligation",
            "selected": [],
            "pending_next_round": [],
            "pending_count": 1,
            "fresh_pending_pool": [{"obligation_id": oid}],
            "fresh_pending_pool_count": 1,
            "stop_condition": "round_limit_reached",
            "round_limit_reached": True,
        },
        runtime_contract={
            "status": "approved",
            "approved_base_url": "http://example.invalid",
        },
        compile_results=compile_results,
        execution_results=execution_results,
    )

    assert execution_results[oid]["status"] == "DEFERRED"
    assert execution_results[oid]["reason_code"] == "OBLIGATION_CONTINUATION_PENDING"
    assert execution_results[oid]["not_in_plan_reason"] == "CONTINUATION_PENDING"
