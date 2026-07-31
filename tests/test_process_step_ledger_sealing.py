from ai_test_asset_center.process_step_execution import (
    PROCESS_STEP_FACT_MODEL_VERSION,
    PROCESS_STEP_RECEIPT_SCHEMA,
    ProcessStepLedger,
)
from ai_test_asset_center.process_step_semantic_view import ProcessStepSemanticView


def _ledger() -> ProcessStepLedger:
    ledger = ProcessStepLedger(
        "experiment-1",
        fixture_id="fixture-1",
        campaign_id="campaign-1",
        run_id="run-1",
        obligation_id="obligation-1",
        protocol_id="protocol-1",
        required_step_ids=["step-1"],
    )
    ledger.record_step_execution(
        step_id="step-1",
        phase="treatment",
        operation_ref="operation-1",
        actor_ref="actor-1",
        request_receipt_id="request-1",
        response_receipt_id="response-1",
        status_code=200,
        final_status="EXECUTED",
        mutation_occurred=True,
    )
    return ledger


def test_status_code_is_sealed_into_ledger_hash() -> None:
    ledger = _ledger()
    before = ledger.compute_hash()
    row = ledger.get_step_row("step-1")
    assert row is not None
    row["status_code"] = 201
    assert ledger.compute_hash() != before


def test_response_and_transport_facts_are_sealed() -> None:
    ledger = _ledger()
    before = ledger.compute_hash()
    row = ledger.get_step_row("step-1")
    assert row is not None
    row["response_received"] = False
    row["transport_failed"] = True
    assert ledger.compute_hash() != before


def test_business_effect_and_completion_facts_are_sealed() -> None:
    ledger = _ledger()
    before = ledger.compute_hash()
    row = ledger.get_step_row("step-1")
    assert row is not None
    row["business_effect_observed"] = True
    row["target_state_observed"] = True
    row["target_reached"] = True
    row["semantic_step_status"] = "TARGET_REACHED"
    row["step_completed"] = True
    assert ledger.compute_hash() != before


def test_mutation_fact_is_sealed_for_cleanup_authority() -> None:
    ledger = _ledger()
    before = ledger.compute_hash()
    row = ledger.get_step_row("step-1")
    assert row is not None
    row["mutation_occurred"] = False
    assert ledger.compute_hash() != before


def test_receipt_ready_row_binds_step_and_ledger_hashes() -> None:
    ledger = _ledger()
    row = ledger.all_rows()[0]

    assert row["receipt_schema_version"] == PROCESS_STEP_RECEIPT_SCHEMA
    assert row["process_step_ledger_id"] == ledger.ledger_id
    assert row["process_step_ledger_hash"] == ledger.compute_hash()
    assert row["step_fact_hash"] == ledger.step_fact_hash("step-1")
    assert row["fact_model_version"] == PROCESS_STEP_FACT_MODEL_VERSION
    assert row["required_step"] is True
    assert row["receipt_id"].startswith("psr_")
    assert ledger.all_rows()[0]["receipt_id"] == row["receipt_id"]


def test_all_rows_returns_snapshot_not_mutable_authority_row() -> None:
    ledger = _ledger()
    before = ledger.compute_hash()
    snapshot = ledger.all_rows()[0]
    snapshot["status_code"] = 599

    assert ledger.get_step_row("step-1")["status_code"] == 200
    assert ledger.compute_hash() == before


def test_authority_dict_exports_same_fact_snapshot_and_sets() -> None:
    ledger = _ledger()
    authority = ledger.to_authority_dict()

    assert authority["ledger_hash"] == ledger.compute_hash()
    assert authority["fact_model_version"] == PROCESS_STEP_FACT_MODEL_VERSION
    assert authority["attempted_step_ids"] == ["step-1"]
    assert authority["executed_step_ids"] == ["step-1"]
    assert authority["accepted_step_ids"] == ["step-1"]
    assert authority["completed_step_ids"] == []
    assert authority["fact_snapshot"]["completed_step_ids"] == []


def test_semantic_view_synchronizes_before_hash_sealing() -> None:
    ledger = _ledger()
    before = ledger.compute_hash()
    view = ProcessStepSemanticView(
        ledger,
        observations={
            "observer_receipts": [
                {
                    "receipt_id": "observer-1",
                    "step_id": "step-1",
                    "target_reached": True,
                }
            ]
        },
    )

    sealed = view.compute_hash()
    assert sealed != before
    assert sealed == ledger.compute_hash()
    assert view.completed_step_ids() == ["step-1"]
    receipt_row = view.all_rows()[0]
    assert receipt_row["process_step_ledger_hash"] == sealed
    assert receipt_row["step_completed"] is True
