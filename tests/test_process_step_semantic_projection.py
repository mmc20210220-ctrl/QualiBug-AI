from __future__ import annotations

from ai_test_asset_center.process_step_execution import ProcessStepLedger
from ai_test_asset_center.process_step_semantic_projection import (
    apply_semantic_verdict,
    project_step_sets,
)


def _ledger() -> ProcessStepLedger:
    ledger = ProcessStepLedger(
        experiment_id="exp_semantic_projection",
        required_step_ids=["step_1"],
    )
    ledger.record_step_execution(
        step_id="step_1",
        phase="treatment",
        operation_ref="op_write",
        actor_ref="actor_owner",
        status_code=200,
        final_status="EXECUTED",
        target_reached=True,
        after_state_receipt_id="after_state_1",
    )
    return ledger


def test_transport_reported_target_does_not_enter_completed_projection() -> None:
    projection = project_step_sets(_ledger())

    assert projection["accepted_step_ids"] == ["step_1"]
    assert projection["completed_step_ids"] == []
    assert projection["pending_semantic_step_ids"] == ["step_1"]


def test_explicit_scoped_observer_verdict_promotes_step() -> None:
    ledger = _ledger()

    promoted = apply_semantic_verdict(
        ledger,
        step_id="step_1",
        receipt_step_id="step_1",
        receipt_id="observer_receipt_1",
        source="state_observer",
        target_reached=True,
    )
    projection = project_step_sets(ledger)

    assert promoted is True
    assert projection["completed_step_ids"] == ["step_1"]
    assert projection["pending_semantic_step_ids"] == []


def test_negative_semantic_verdict_marks_failed_not_completed() -> None:
    ledger = _ledger()

    promoted = apply_semantic_verdict(
        ledger,
        step_id="step_1",
        receipt_step_id="step_1",
        receipt_id="oracle_receipt_1",
        source="postcondition_oracle",
        target_reached=False,
    )
    projection = project_step_sets(ledger)

    assert promoted is True
    assert projection["completed_step_ids"] == []
    assert projection["failed_step_ids"] == ["step_1"]


def test_verdict_cannot_cross_bind_to_another_step() -> None:
    ledger = _ledger()

    promoted = apply_semantic_verdict(
        ledger,
        step_id="step_1",
        receipt_step_id="step_2",
        receipt_id="observer_receipt_wrong_scope",
        source="observer",
        target_reached=True,
    )

    assert promoted is False
    assert project_step_sets(ledger)["completed_step_ids"] == []


def test_verdict_without_supported_authority_is_ignored() -> None:
    ledger = _ledger()

    promoted = apply_semantic_verdict(
        ledger,
        step_id="step_1",
        receipt_step_id="step_1",
        receipt_id="transport_receipt_1",
        source="transport",
        target_reached=True,
    )

    assert promoted is False
    assert project_step_sets(ledger)["completed_step_ids"] == []
