from __future__ import annotations

from ai_test_asset_center.process_step_execution import (
    PROCESS_EVIDENCE_INCOMPLETE_CODE,
    PROCESS_STEP_CLEANUP_SET_INCOMPLETE,
    PROCESS_STEP_RECEIPT_IDENTITY_MISMATCH,
    ProcessStepLedger,
    attach_ledger_refs_to_observations,
    evaluate_per_step_evidence_completeness,
    step_ids_with_cleanup_evidence,
    step_ids_with_observation_evidence,
    step_ids_with_oracle_evidence,
    validate_required_actual_step_balance,
)


def _completed_ledger(
    *,
    status_code: int = 200,
    final_status: str = "EXECUTED",
) -> ProcessStepLedger:
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
        after_state_receipt_id="after_state_receipt_1",
        status_code=status_code,
        final_status=final_status,
        target_reached=status_code > 0,
    )
    return ledger


def test_response_receipt_cannot_self_authorize_observation() -> None:
    ledger = ProcessStepLedger(
        experiment_id="exp_response_only",
        required_step_ids=["treatment_1"],
    )
    ledger.record_step_execution(
        step_id="treatment_1",
        phase="treatment",
        operation_ref="op_write",
        actor_ref="actor_owner",
        response_receipt_id="response_hash",
        observer_receipt_ids=["response_hash"],
        status_code=200,
        final_status="EXECUTED",
        target_reached=True,
    )

    # Transport-executed (the step reached a real response); the observer
    # receipt that merely repeats the response id is self-authorization and
    # never observation evidence.
    assert ledger.executed_step_ids() == ["treatment_1"]
    assert step_ids_with_observation_evidence(ledger) == []

    observations: dict = {}
    attach_ledger_refs_to_observations(observations, ledger)
    assert "observation_receipt_ids" not in observations


def test_caller_cannot_widen_observation_authority_with_executed_ids() -> None:
    ledger = _completed_ledger()
    row = ledger.get_step_row("treatment_1")
    assert row is not None
    row["after_state_receipt_id"] = ""
    row["scoped_observation_receipt_ids"] = []

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
    ledger = _completed_ledger()

    assert step_ids_with_observation_evidence(ledger) == ["treatment_1"]

    receipt = evaluate_per_step_evidence_completeness(
        planned_step_ids=["treatment_1"],
        ledger=ledger,
        observed_step_ids=["treatment_1"],
    )
    assert receipt["complete"] is True


def test_blocked_row_is_recorded_but_not_executed() -> None:
    ledger = _completed_ledger(status_code=0, final_status="BLOCKED")

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


def test_unscoped_late_receipts_are_rejected_by_authority_boundary() -> None:
    ledger = _completed_ledger()

    for field, receipt_id in (
        ("observation_receipt_ids", "observation_receipt_2"),
        ("oracle_receipt_ids", "oracle_receipt_1"),
        ("cleanup_receipt_ids", "cleanup_receipt_1"),
    ):
        assert ledger.append_receipt_ref(
            "treatment_1",
            field,
            receipt_id,
        ) is False

    assert step_ids_with_oracle_evidence(ledger) == []
    assert step_ids_with_cleanup_evidence(ledger) == []
    assert len(ledger.receipt_scope_rejections) == 3
    assert {
        row["reason_code"] for row in ledger.receipt_scope_rejections
    } == {PROCESS_STEP_RECEIPT_IDENTITY_MISMATCH}


def test_scoped_late_receipts_complete_only_the_matching_step() -> None:
    ledger = _completed_ledger()

    assert ledger.append_scoped_receipt_ref(
        step_id="treatment_1",
        receipt_step_id="treatment_1",
        field="oracle_receipt_ids",
        receipt_id="oracle_receipt_1",
    )
    assert ledger.append_scoped_receipt_ref(
        step_id="treatment_1",
        receipt_step_id="treatment_1",
        field="cleanup_receipt_ids",
        receipt_id="cleanup_receipt_1",
    )

    assert step_ids_with_oracle_evidence(ledger) == ["treatment_1"]
    assert step_ids_with_cleanup_evidence(ledger) == ["treatment_1"]

    receipt = evaluate_per_step_evidence_completeness(
        planned_step_ids=["treatment_1"],
        ledger=ledger,
        observed_step_ids=["treatment_1"],
        cleanup_covered_step_ids=["treatment_1"],
    )
    assert receipt["complete"] is True


def test_mismatched_receipt_step_identity_cannot_cross_bind() -> None:
    ledger = _completed_ledger()

    assert ledger.append_scoped_receipt_ref(
        step_id="treatment_1",
        receipt_step_id="treatment_2",
        field="oracle_receipt_ids",
        receipt_id="oracle_receipt_wrong_scope",
    ) is False
    assert step_ids_with_oracle_evidence(ledger) == []
    assert ledger.receipt_scope_rejections[-1]["reason_code"] == (
        PROCESS_STEP_RECEIPT_IDENTITY_MISMATCH
    )
