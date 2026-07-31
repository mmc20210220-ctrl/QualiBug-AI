from __future__ import annotations

from ai_test_asset_center.process_step_execution import (
    PROCESS_EVIDENCE_INCOMPLETE_CODE,
    PROCESS_STEP_CLEANUP_SET_INCOMPLETE,
    ProcessStepLedger,
    attach_ledger_refs_to_observations,
    evaluate_per_step_evidence_completeness,
    step_ids_with_observation_evidence,
    validate_required_actual_step_balance,
)


def _ledger(*, status_code: int = 200, final_status: str = "EXECUTED") -> ProcessStepLedger:
    ledger = ProcessStepLedger(
        experiment_id="exp_process_authority",
        required_step_ids=["treatment_1"],
    )
    ledger.record_step_execution(
        step_id="treatment_1",
        phase="treatment",
        operation_ref="op_write",
        actor_ref="actor_owner",
        response_receipt_id="response_hash",
        observer_receipt_ids=["response_hash"],
        status_code=status_code,
        final_status=final_status,
        target_reached=status_code > 0,
    )
    return ledger


def test_response_receipt_cannot_self_authorize_observation() -> None:
    ledger = _ledger()

    assert step_ids_with_observation_evidence(ledger) == []

    observations: dict = {}
    attach_ledger_refs_to_observations(observations, ledger)
    assert "observation_receipt_ids" not in observations


def test_caller_cannot_widen_observation_authority_with_executed_ids() -> None:
    ledger = _ledger()

    receipt = evaluate_per_step_evidence_completeness(
        planned_step_ids=["treatment_1"],
        ledger=ledger,
        observed_step_ids=["treatment_1"],
    )

    assert receipt["complete"] is False
    assert receipt["reason_code"] == PROCESS_EVIDENCE_INCOMPLETE_CODE
    assert receipt["observed_step_ids"] == []
    assert receipt["missing_observation"] == ["treatment_1"]


def test_independent_state_receipt_is_valid_observation_evidence() -> None:
    ledger = _ledger()
    assert ledger.append_receipt_ref(
        "treatment_1",
        "after_state_receipt_id",
        "after_state_receipt_1",
    )

    assert step_ids_with_observation_evidence(ledger) == ["treatment_1"]

    receipt = evaluate_per_step_evidence_completeness(
        planned_step_ids=["treatment_1"],
        ledger=ledger,
        observed_step_ids=["treatment_1"],
    )
    assert receipt["complete"] is True


def test_blocked_row_is_recorded_but_not_executed() -> None:
    ledger = _ledger(status_code=0, final_status="BLOCKED")

    assert ledger.recorded_step_ids() == ["treatment_1"]
    assert ledger.executed_step_ids() == []
    assert ledger.failed_step_ids() == ["treatment_1"]


def test_explicit_empty_cleanup_set_fails_balance() -> None:
    result = validate_required_actual_step_balance(
        required_step_ids=["treatment_1"],
        executed_step_ids=["treatment_1"],
        observed_step_ids=["treatment_1"],
        oracle_step_ids=["treatment_1"],
        cleanup_step_ids=[],
    )

    assert result["balanced"] is False
    assert result["reason_code"] == PROCESS_STEP_CLEANUP_SET_INCOMPLETE
    assert result["missing_cleanup"] == ["treatment_1"]


def test_cleanup_claim_without_ledger_receipt_cannot_complete() -> None:
    ledger = _ledger()
    ledger.append_receipt_ref(
        "treatment_1",
        "after_state_receipt_id",
        "after_state_receipt_1",
    )

    receipt = evaluate_per_step_evidence_completeness(
        planned_step_ids=["treatment_1"],
        ledger=ledger,
        observed_step_ids=["treatment_1"],
        cleanup_covered_step_ids=["treatment_1"],
    )

    assert receipt["complete"] is False
    assert receipt["missing_cleanup"] == ["treatment_1"]

    assert ledger.append_receipt_ref(
        "treatment_1",
        "cleanup_receipt_ids",
        "cleanup_receipt_1",
    )
    receipt = evaluate_per_step_evidence_completeness(
        planned_step_ids=["treatment_1"],
        ledger=ledger,
        observed_step_ids=["treatment_1"],
        cleanup_covered_step_ids=["treatment_1"],
    )
    assert receipt["complete"] is True
