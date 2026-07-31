from __future__ import annotations

from ai_test_asset_center.process_step_execution import ProcessStepLedger
from ai_test_asset_center.process_step_observer import (
    EVIDENCE_KEY,
    evaluate_step_sequence_order,
    observe_process_steps,
)


def _observations() -> dict:
    ledger = ProcessStepLedger(
        experiment_id="exp_timeline",
        required_step_ids=["step_1", "step_2"],
    )
    for ordinal, step_id in enumerate(("step_1", "step_2"), 1):
        ledger.record_step_execution(
            step_id=step_id,
            phase="treatment",
            operation_ref=f"op_{ordinal}",
            actor_ref="actor_1",
            status_code=200,
            final_status="EXECUTED",
        )
        ledger.record_timeline_event(
            step_id=step_id,
            phase="treatment",
            event_type="STEP_COMPLETED",
            operation_ref=f"op_{ordinal}",
            actor_ref="actor_1",
        )
    ledger.record_timeline_event(
        step_id="cleanup",
        phase="cleanup",
        event_type="CLEANUP_COMPLETED",
    )
    return {
        "process_step_ledger": ledger,
        "process_timeline": ledger.build_timeline_receipt(),
    }


def test_observer_reads_canonical_timeline_receipt_events() -> None:
    receipt = observe_process_steps({"observations": _observations()})

    assert receipt["status"] == "OBSERVED"
    payload = receipt["evidence"][EVIDENCE_KEY]
    assert payload["observed_order"] == ["step_1", "step_2"]
    assert payload["step_count"] == 2
    assert payload["coverage_complete"] is True
    assert payload["timeline_schema"] == "qualibug.process-timeline.v1"


def test_lifecycle_and_cleanup_events_do_not_pollute_business_order() -> None:
    receipt = observe_process_steps({"observations": _observations()})
    payload = receipt["evidence"][EVIDENCE_KEY]

    assert "cleanup" not in payload["observed_order"]


def test_sequence_assertion_consumes_canonical_observer_projection() -> None:
    receipt = observe_process_steps({"observations": _observations()})
    result = evaluate_step_sequence_order(
        {
            "spec": {"expected_step_order": ["step_1", "step_2"]},
            "observations": {
                EVIDENCE_KEY: receipt["evidence"][EVIDENCE_KEY]
            },
        }
    )

    assert result["passed"] is True


def test_missing_declared_step_is_indeterminate_not_violation() -> None:
    receipt = observe_process_steps({"observations": _observations()})
    result = evaluate_step_sequence_order(
        {
            "spec": {"expected_step_order": ["step_1", "step_3"]},
            "observations": {
                EVIDENCE_KEY: receipt["evidence"][EVIDENCE_KEY]
            },
        }
    )

    assert result["passed"] is None
    assert result["reason_code"] == "DECLARED_STEP_NOT_OBSERVED"


def test_legacy_list_shape_is_read_only_for_migration() -> None:
    observations = _observations()
    observations["process_timeline"] = observations["process_timeline"]["events"]

    receipt = observe_process_steps({"observations": observations})

    assert receipt["status"] == "OBSERVED"
    assert receipt["evidence"][EVIDENCE_KEY]["observed_order"] == [
        "step_1",
        "step_2",
    ]
