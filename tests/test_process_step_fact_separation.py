from ai_test_asset_center.process_step_execution import (
    PROCESS_EVIDENCE_INCOMPLETE,
    PROCESS_FAILED,
    ProcessStepLedger,
    attach_ledger_refs_to_observations,
    evaluate_per_step_evidence_completeness,
    evaluate_process_completion,
)
from ai_test_asset_center.process_step_semantic_projection import (
    apply_semantic_verdict,
    project_step_sets,
)
from ai_test_asset_center.process_step_semantic_view import ProcessStepSemanticView


def _ledger() -> ProcessStepLedger:
    return ProcessStepLedger("experiment-1", required_step_ids=["step-1"])


def _record(
    ledger: ProcessStepLedger,
    *,
    status: int = 200,
    final: str = "EXECUTED",
    response: bool = True,
    mutation: bool | None = None,
    operation_accepted: bool | None = None,
):
    return ledger.record_step_execution(
        step_id="step-1",
        phase="treatment",
        operation_ref="op-1",
        actor_ref="actor-1",
        request_receipt_id="request-1",
        response_receipt_id="response-1" if response else "",
        status_code=status,
        final_status=final,
        mutation_occurred=mutation,
        operation_accepted=operation_accepted,
    )


def test_http_rejection_is_executed_not_accepted_or_completed() -> None:
    ledger = _ledger()
    row = _record(ledger, status=404)

    assert row["transport_attempted"] is True
    assert row["response_received"] is True
    assert row["transport_failed"] is False
    assert row["operation_accepted"] is False
    assert row["semantic_step_status"] == "OPERATION_REJECTED"
    assert row["target_reached"] is None
    assert row["step_completed"] is False
    assert row["step_failed"] is True
    assert ledger.executed_step_ids() == ["step-1"]
    assert ledger.completed_step_ids() == []


def test_explicit_rejection_is_not_overridden_by_http_success() -> None:
    ledger = _ledger()
    row = _record(ledger, status=200, operation_accepted=False)

    assert row["response_received"] is True
    assert row["operation_accepted"] is False
    assert row["semantic_step_status"] == "OPERATION_REJECTED"
    projection = project_step_sets(ledger)
    assert projection["executed_step_ids"] == ["step-1"]
    assert projection["accepted_step_ids"] == []
    assert projection["completed_step_ids"] == []


def test_accepted_without_observation_is_not_business_completion() -> None:
    ledger = _ledger()
    row = _record(ledger)

    assert row["operation_accepted"] is True
    assert row["business_effect_observed"] is False
    assert row["semantic_step_status"] == "PENDING_OBSERVATION"
    assert ledger.executed_step_ids() == ["step-1"]
    assert ledger.accepted_step_ids() == ["step-1"]
    assert ledger.completed_step_ids() == []

    evidence = evaluate_per_step_evidence_completeness(
        planned_step_ids=["step-1"],
        ledger=ledger,
    )
    assert evidence["missing_execution"] == []
    assert evidence["missing_observation"] == ["step-1"]


def test_scoped_target_verdict_completes_business_step() -> None:
    ledger = _ledger()
    _record(ledger)

    assert apply_semantic_verdict(
        ledger,
        step_id="step-1",
        receipt_step_id="step-1",
        receipt_id="observer-1",
        source="state_observer",
        target_reached=True,
    ) is True

    assert ledger.executed_step_ids() == ["step-1"]
    assert ledger.completed_step_ids() == ["step-1"]
    projection = project_step_sets(ledger)
    assert projection["executed_step_ids"] == ["step-1"]
    assert projection["completed_step_ids"] == ["step-1"]


def test_semantic_view_keeps_execution_separate_from_completion() -> None:
    ledger = _ledger()
    _record(ledger)
    view = ProcessStepSemanticView(ledger)

    # The raw ledger keeps transport execution; the view's strict semantic set
    # requires an explicit scoped verdict receipt, so execution and business
    # completion stay separate facts.
    assert ledger.executed_step_ids() == ["step-1"]
    assert view.executed_step_ids() == []
    assert view.completed_step_ids() == []


def test_accepted_write_requires_cleanup_before_target_observation() -> None:
    ledger = _ledger()
    _record(ledger, status=202, mutation=True)

    assert ledger.successful_write_step_ids() == ["step-1"]
    assert ledger.completed_step_ids() == []


def test_no_response_is_transport_failure_not_operation_rejection() -> None:
    ledger = _ledger()
    row = _record(ledger, status=0, final="FAILED", response=False)

    assert row["transport_attempted"] is True
    assert row["response_received"] is False
    assert row["transport_failed"] is True
    assert row["semantic_step_status"] == "TRANSPORT_FAILED"
    assert ledger.attempted_step_ids() == ["step-1"]
    assert ledger.executed_step_ids() == []


def test_process_completion_uses_business_completion() -> None:
    ledger = _ledger()
    _record(ledger)

    outcome = evaluate_process_completion(
        expected_step_ids=["step-1"],
        ledger=ledger,
        evidence_complete=False,
    )
    assert outcome["result"] == PROCESS_EVIDENCE_INCOMPLETE
    assert outcome["executed_step_ids"] == ["step-1"]
    assert outcome["completed_step_ids"] == []


def test_false_verdict_is_failed_never_completed_and_failed() -> None:
    ledger = _ledger()
    _record(ledger)
    apply_semantic_verdict(
        ledger,
        step_id="step-1",
        receipt_step_id="step-1",
        receipt_id="oracle-1",
        source="oracle",
        target_reached=False,
    )

    row = ledger.get_step_row("step-1")
    assert row is not None
    assert row["step_completed"] is False
    assert row["step_failed"] is True

    outcome = evaluate_process_completion(
        expected_step_ids=["step-1"],
        ledger=ledger,
        evidence_complete=True,
    )
    assert outcome["result"] == PROCESS_FAILED


def test_observations_export_distinct_step_sets() -> None:
    ledger = _ledger()
    _record(ledger)
    observations = attach_ledger_refs_to_observations({}, ledger)

    assert observations["attempted_step_ids"] == ["step-1"]
    assert observations["executed_step_ids"] == ["step-1"]
    assert observations["accepted_step_ids"] == ["step-1"]
    assert observations["completed_step_ids"] == []
