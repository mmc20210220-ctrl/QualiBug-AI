from ai_test_asset_center.process_step_execution import (
    ProcessStepLedger,
    PROCESS_EVIDENCE_INCOMPLETE,
    PROCESS_FAILED,
    evaluate_process_completion,
)


def _ledger() -> ProcessStepLedger:
    return ProcessStepLedger(
        "exp-1",
        required_step_ids=["step-1"],
    )


def test_http_error_cannot_be_completed_or_target_reached() -> None:
    ledger = _ledger()
    row = ledger.record_step_execution(
        step_id="step-1",
        phase="business",
        operation_ref="POST /orders",
        actor_ref="buyer",
        status_code=404,
        final_status="EXECUTED",
    )

    assert row["response_received"] is True
    assert row["operation_accepted"] is False
    assert row["target_state_observed"] is False
    assert row["target_reached"] is None
    assert row["step_completed"] is False
    assert row["step_failed"] is True
    # Transport-executed: the step reached a real response (a 4xx/5xx is still
    # an execution attempt), while business completion stays separate.
    assert ledger.executed_step_ids() == ["step-1"]
    assert ledger.completed_step_ids() == []


def test_successful_http_response_does_not_prove_business_state() -> None:
    ledger = _ledger()
    row = ledger.record_step_execution(
        step_id="step-1",
        phase="business",
        operation_ref="POST /orders",
        actor_ref="buyer",
        status_code=201,
        final_status="EXECUTED",
        response_receipt_id="response-1",
    )

    assert row["operation_accepted"] is True
    assert row["target_state_observed"] is False
    assert row["target_reached"] is None
    assert row["semantic_step_status"] == "PENDING_OBSERVATION"
    assert row["step_completed"] is False
    assert row["step_failed"] is False
    # Executed at transport level; business state is NOT proven without an
    # independent observation (executed ≠ completed).
    assert ledger.executed_step_ids() == ["step-1"]
    assert ledger.completed_step_ids() == []


def test_explicit_target_without_independent_observation_is_not_proof() -> None:
    ledger = _ledger()
    row = ledger.record_step_execution(
        step_id="step-1",
        phase="business",
        operation_ref="POST /orders",
        actor_ref="buyer",
        status_code=201,
        final_status="EXECUTED",
        response_receipt_id="response-1",
        observer_receipt_ids=["response-1"],
        target_reached=True,
    )

    assert row["target_state_observed"] is False
    assert row["target_reached"] is None
    assert row["step_completed"] is False


def test_independent_observation_can_prove_target_state() -> None:
    ledger = _ledger()
    row = ledger.record_step_execution(
        step_id="step-1",
        phase="business",
        operation_ref="POST /orders",
        actor_ref="buyer",
        status_code=201,
        final_status="EXECUTED",
        response_receipt_id="response-1",
        after_state_receipt_id="db-state-1",
        target_reached=True,
    )

    assert row["operation_accepted"] is True
    assert row["target_state_observed"] is True
    assert row["target_reached"] is True
    assert row["semantic_step_status"] == "TARGET_REACHED"
    assert row["step_completed"] is True
    assert row["step_failed"] is False
    assert ledger.executed_step_ids() == ["step-1"]


def test_target_not_reached_is_failed_but_never_completed() -> None:
    ledger = _ledger()
    row = ledger.record_step_execution(
        step_id="step-1",
        phase="business",
        operation_ref="POST /orders",
        actor_ref="buyer",
        status_code=201,
        final_status="EXECUTED",
        after_state_receipt_id="db-state-1",
        target_reached=False,
    )

    assert row["semantic_step_status"] == "TARGET_NOT_REACHED"
    assert row["step_completed"] is False
    assert row["step_failed"] is True
    # Transport-executed; business completion is false (never completed).
    assert ledger.executed_step_ids() == ["step-1"]
    assert ledger.completed_step_ids() == []


def test_process_completion_fails_when_semantic_step_failed() -> None:
    ledger = _ledger()
    ledger.record_step_execution(
        step_id="step-1",
        phase="business",
        operation_ref="POST /orders",
        actor_ref="buyer",
        status_code=500,
        final_status="EXECUTED",
    )

    result = evaluate_process_completion(
        expected_step_ids=["step-1"],
        ledger=ledger,
        evidence_complete=False,
    )

    assert result["result"] == PROCESS_FAILED


def test_process_completion_requires_evidence_after_target_reached() -> None:
    ledger = _ledger()
    ledger.record_step_execution(
        step_id="step-1",
        phase="business",
        operation_ref="POST /orders",
        actor_ref="buyer",
        status_code=201,
        final_status="EXECUTED",
        after_state_receipt_id="db-state-1",
        target_reached=True,
    )

    result = evaluate_process_completion(
        expected_step_ids=["step-1"],
        ledger=ledger,
        evidence_complete=False,
    )

    assert result["result"] == PROCESS_EVIDENCE_INCOMPLETE
