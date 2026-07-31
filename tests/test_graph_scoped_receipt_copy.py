from __future__ import annotations

from ai_test_asset_center.process_graph_executor_support import copy_subledger_rows
from ai_test_asset_center.process_step_execution import ProcessStepLedger


def _ledger(name: str) -> ProcessStepLedger:
    return ProcessStepLedger(
        experiment_id=name,
        campaign_id="campaign_1",
        run_id="run_1",
        obligation_id="obl_1",
        required_step_ids=["step_1"],
    )


def test_copy_preserves_scoped_only_receipts_and_timeline() -> None:
    child = _ledger("child")
    row = child.record_step_execution(
        step_id="step_1",
        phase="treatment",
        operation_ref="op_1",
        actor_ref="actor_1",
        runtime_identity={"id": "42"},
        final_status="EXECUTED",
    )
    # Simulate a strict/hydrated artifact that intentionally omits legacy aliases.
    row["scoped_observation_receipt_ids"] = ["wait_receipt_1"]
    row["scoped_oracle_receipt_ids"] = ["oracle_receipt_1"]
    row["scoped_cleanup_receipt_ids"] = ["cleanup_receipt_1"]
    row["observation_receipt_ids"] = []
    row["observer_receipt_ids"] = []
    row["oracle_receipt_ids"] = []
    row["cleanup_receipt_ids"] = []
    child.record_timeline_event(
        step_id="step_1",
        phase="wait",
        event_type="WAIT_CONVERGED",
        operation_ref="op_wait",
        actor_ref="actor_1",
        receipt_id="wait_receipt_1",
    )

    master = _ledger("master")
    copied = copy_subledger_rows(master, child)

    assert copied == {"step_1"}
    target = master.get_step_row("step_1")
    assert target["scoped_observation_receipt_ids"] == ["wait_receipt_1"]
    assert target["scoped_oracle_receipt_ids"] == ["oracle_receipt_1"]
    assert target["scoped_cleanup_receipt_ids"] == ["cleanup_receipt_1"]
    assert master.timeline()[0]["event_type"] == "WAIT_CONVERGED"
