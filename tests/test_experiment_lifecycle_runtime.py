from __future__ import annotations

from ai_test_asset_center.experiment_lifecycle_runtime import (
    attach_lifecycle_ledger,
    attach_lifecycle_to_result,
    new_experiment_lifecycle_ledger,
    record_stage_rows,
    terminal_result_with_lifecycle,
)


def _experiment() -> dict:
    return {
        "experiment_id": "exp_1",
        "obligation_id": "obl_1",
        "protocol_id": "protocol_1",
        "disposable_fixture_contract": {"fixture_id": "fixture_1"},
        "control_plan": [
            {"step_id": "control_1", "operation_ref": "op_read"}
        ],
        "treatment_plan": [
            {"step_id": "treatment_1", "operation_ref": "op_write"},
            {"step_id": "treatment_2", "operation_ref": "op_confirm"},
        ],
    }


def test_entry_ledger_freezes_declared_business_step_set() -> None:
    ledger = new_experiment_lifecycle_ledger(
        _experiment(),
        experiment_id="exp_1",
        obligation_id="obl_1",
        campaign_id="campaign_1",
        run_id="run_1",
    )

    assert ledger.required_step_ids == [
        "control_1",
        "treatment_1",
        "treatment_2",
    ]
    assert ledger.fixture_id == "fixture_1"
    assert ledger.protocol_id == "protocol_1"
    assert ledger.timeline()[0]["receipt_id"] == "experiment_execution_started"


def test_fixture_barrier_and_cleanup_rows_share_one_timeline() -> None:
    ledger = new_experiment_lifecycle_ledger(
        _experiment(),
        experiment_id="exp_1",
        obligation_id="obl_1",
        campaign_id="campaign_1",
        run_id="run_1",
    )
    record_stage_rows(
        ledger,
        [{"node_id": "fixture_node_1", "status": "resolved"}],
        phase="fixture",
    )
    record_stage_rows(
        ledger,
        [{"step_id": "barrier_1", "status": "executed", "status_code": 200}],
        phase="barrier",
    )
    record_stage_rows(
        ledger,
        [{"step_id": "cleanup_1", "status": "completed", "status_code": 200}],
        phase="cleanup",
    )

    timeline = ledger.timeline()
    assert [row["phase"] for row in timeline] == [
        "lifecycle",
        "fixture",
        "barrier",
        "cleanup",
    ]
    assert timeline[-1]["event_type"] == "CLEANUP_COMPLETED"


def test_terminal_result_carries_immutable_lifecycle_snapshot() -> None:
    ledger = new_experiment_lifecycle_ledger(
        _experiment(),
        experiment_id="exp_1",
        obligation_id="obl_1",
        campaign_id="campaign_1",
        run_id="run_1",
    )
    result = terminal_result_with_lifecycle(
        ledger,
        {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_FIXTURE",
        },
        phase="fixture",
        reason_code="BLOCKED_MISSING_FIXTURE",
    )

    assert result["process_step_ledger_id"] == ledger.ledger_id
    assert result["process_step_ledger_hash"] == ledger.compute_hash()
    assert result["process_step_ledger_receipt"]["ledger_hash"] == (
        ledger.compute_hash()
    )
    assert result["process_timeline"]["events"][-1]["phase"] == "fixture"


def test_live_observations_and_final_result_use_same_ledger_identity() -> None:
    ledger = new_experiment_lifecycle_ledger(
        _experiment(),
        experiment_id="exp_1",
        obligation_id="obl_1",
        campaign_id="campaign_1",
        run_id="run_1",
    )
    observations: dict = {}
    attach_lifecycle_ledger(observations, ledger)
    result = attach_lifecycle_to_result(ledger, {"status": "INDETERMINATE"})

    assert observations["process_step_ledger"] is ledger
    assert observations["process_step_ledger_id"] == result["process_step_ledger_id"]
    assert observations["process_step_ledger_hash"] == result["process_step_ledger_hash"]
