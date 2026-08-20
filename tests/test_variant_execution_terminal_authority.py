"""Variant execution must survive formal-base terminal accounting."""
from __future__ import annotations


def _mainline_run() -> dict[str, str]:
    return {
        "run_id": "run-variant-terminal",
        "campaign_id": "campaign-variant-terminal",
        "target_id": "target-1",
        "environment_id": "test",
        "policy_version": "policy-1",
        "evaluation_mode": "formal",
        "source_snapshot_hash": "source-1",
        "contract_fingerprint": "contract-1",
    }


def test_real_variant_execution_replaces_only_mechanical_base_gap() -> None:
    from ai_test_asset_center.obligation_attempt_ledger import (
        bind_stage_receipt_identity,
        build_obligation_attempt_ledger,
    )

    base = "obl-validation"
    variant = f"{base}__v_abcdef123456"
    selected = [{
        "obligation_id": base,
        "candidate_id": "candidate-1",
        "selection_status": "SELECTED",
    }]
    compile_results = {
        base: {"status": "COMPILED", "experiment_id": "exp-variant"},
        variant: {"status": "COMPILED", "experiment_id": "exp-variant"},
    }
    execution_results = {
        # This is the mechanical filler created when formal-base accounting
        # cannot see a concrete compiler-expanded execution key.
        base: {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_EXECUTION",
            "detail": "compiled_obligation_has_no_execution_receipt",
            "experiment_id": "exp-variant",
        },
        variant: {
            "status": "EXECUTED",
            "reason_code": "",
            "selected_obligation_id": variant,
            "executed_obligation_id": variant,
            "experiment_id": "exp-variant",
            "execution_id": "execution-variant",
            "observation_receipt_ids": ["obs-variant"],
        },
    }
    gate_results = {
        variant: {
            "status": "REJECTED",
            "reason_code": "ORACLE_NOT_VIOLATED",
            "gate_receipt_id": "gate-variant",
        },
    }

    bound_compile, bound_execution, bound_gate = bind_stage_receipt_identity(
        mainline_run=_mainline_run(),
        selected=selected,
        compile_results=compile_results,
        execution_results=execution_results,
        gate_results=gate_results,
    )

    assert set(bound_execution) == {base}
    assert bound_execution[base]["status"] == "EXECUTED"
    assert bound_execution[base]["selected_obligation_id"] == base
    assert bound_execution[base]["executed_obligation_id"] == variant
    assert set(bound_gate) == {base}

    ledger = build_obligation_attempt_ledger(
        mainline_run=_mainline_run(),
        selected=selected,
        compile_results=bound_compile,
        execution_results=bound_execution,
        gate_results=bound_gate,
    )
    attempt = ledger["attempts"][0]
    assert attempt["obligation_id"] == base
    assert attempt["executed_obligation_id"] == variant
    assert attempt["execution_id"] == "execution-variant"
    assert attempt["terminal_status"] == "REJECTED"
    assert next(
        stage for stage in attempt["stages"] if stage["stage"] == "execution"
    )["status"] == "EXECUTED"


def test_real_base_execution_keeps_precedence_over_variant_face() -> None:
    from ai_test_asset_center.obligation_attempt_ledger import (
        bind_stage_receipt_identity,
    )

    base = "obl-base-wins"
    variant = f"{base}__v_abcdef123456"
    selected = [{"obligation_id": base, "selection_status": "SELECTED"}]

    _, bound_execution, _ = bind_stage_receipt_identity(
        mainline_run=_mainline_run(),
        selected=selected,
        compile_results={base: {"status": "COMPILED", "experiment_id": "exp-base"}},
        execution_results={
            base: {
                "status": "EXECUTED",
                "selected_obligation_id": base,
                "executed_obligation_id": base,
                "experiment_id": "exp-base",
                "execution_id": "execution-base",
            },
            variant: {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "selected_obligation_id": variant,
                "executed_obligation_id": variant,
                "experiment_id": "exp-variant",
            },
        },
        gate_results={},
    )

    assert bound_execution[base]["status"] == "EXECUTED"
    assert bound_execution[base]["execution_id"] == "execution-base"
