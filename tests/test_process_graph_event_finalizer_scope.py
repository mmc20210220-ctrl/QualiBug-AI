from __future__ import annotations

from ai_test_asset_center.process_graph_event_transition import (
    RECEIPT_SCHEMA_VERSION,
)
from ai_test_asset_center.process_step_execution import ProcessStepLedger
from ai_test_asset_center.process_step_receipt_scope import (
    synchronize_scoped_receipts_from_observations,
)


def _ledger() -> ProcessStepLedger:
    ledger = ProcessStepLedger(
        experiment_id="exp_event_scope",
        campaign_id="campaign_event_scope",
        run_id="run_event_scope",
        obligation_id="obl_event_scope",
        required_step_ids=["submit_order", "consume_notification"],
    )
    for step_id in ("submit_order", "consume_notification"):
        ledger.record_step_execution(
            step_id=step_id,
            phase="treatment",
            operation_ref=f"op_{step_id}",
            actor_ref="actor_1",
            runtime_identity={},
            status_code=200,
            final_status="EXECUTED",
            target_reached=True,
        )
    return ledger


def _event_receipt() -> dict:
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "receipt_id": "event_receipt_exact_scope",
        "step_id": "consume_notification",
        "source_node_id": "submit_order",
        "target_node_id": "consume_notification",
        "contract_fingerprint": "event_contract_fp",
        "semantic_status": "VIOLATION",
        "reason_code": "PROCESS_GRAPH_EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM",
        "coverage_complete": True,
        "observation_window_completed": True,
    }


def test_event_receipt_is_bound_only_to_its_target_step() -> None:
    ledger = _ledger()
    receipt = _event_receipt()
    observations = {
        "process_step_observation_receipts": [receipt],
    }

    audit = synchronize_scoped_receipts_from_observations(
        ledger,
        observations,
    )

    assert audit["observation"]["complete"] is True
    assert audit["observation"]["bound"] == [
        {
            "receipt_id": "event_receipt_exact_scope",
            "step_id": "consume_notification",
            "evidence_kind": "observation",
        }
    ]
    consume = ledger.get_step_row("consume_notification")
    submit = ledger.get_step_row("submit_order")
    assert "event_receipt_exact_scope" in consume["scoped_observation_receipt_ids"]
    assert "event_receipt_exact_scope" not in submit["scoped_observation_receipt_ids"]
    assert observations["observer_receipts"] == [receipt]


def test_unknown_event_target_remains_unbound() -> None:
    ledger = _ledger()
    receipt = _event_receipt()
    receipt["step_id"] = "unknown_step"
    observations = {
        "process_step_observation_receipts": [receipt],
    }

    audit = synchronize_scoped_receipts_from_observations(
        ledger,
        observations,
    )

    assert audit["observation"]["complete"] is False
    assert audit["observation"]["unbound"][0]["status"] == (
        "STEP_SCOPE_UNKNOWN"
    )
    assert not ledger.get_step_row("submit_order")[
        "scoped_observation_receipt_ids"
    ]
    assert not ledger.get_step_row("consume_notification")[
        "scoped_observation_receipt_ids"
    ]
