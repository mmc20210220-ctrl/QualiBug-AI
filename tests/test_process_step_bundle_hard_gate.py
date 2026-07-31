from copy import deepcopy

from ai_test_asset_center.operational_receipts import (
    build_canonical_receipt_envelope,
    build_execution_receipt_bundle,
    derive_execution_lifecycle,
)
from ai_test_asset_center.process_step_execution import (
    PROCESS_STEP_RECEIPT_SCHEMA,
    ProcessStepLedger,
    _canonical_step_fact,
    _stable_hash,
)
from ai_test_asset_center.process_step_semantic_view import ProcessStepSemanticView


IDENTITY = {
    "campaign_id": "campaign-1",
    "run_id": "run-1",
    "obligation_id": "obligation-1",
    "experiment_id": "experiment-1",
    "fixture_id": "fixture-1",
    "protocol_id": "protocol-1",
}


def _envelope(receipt_type: str, receipt_id: str, payload: dict):
    return build_canonical_receipt_envelope(
        receipt_type=receipt_type,
        receipt_id=receipt_id,
        payload=payload,
        **IDENTITY,
        code_commit_sha="commit-1",
        tree_hash="tree-1",
    )


def _step_rows() -> list[dict]:
    ledger = ProcessStepLedger(
        IDENTITY["experiment_id"],
        fixture_id=IDENTITY["fixture_id"],
        campaign_id=IDENTITY["campaign_id"],
        run_id=IDENTITY["run_id"],
        obligation_id=IDENTITY["obligation_id"],
        protocol_id=IDENTITY["protocol_id"],
        required_step_ids=["step-1", "step-2"],
    )
    for ordinal in (1, 2):
        ledger.record_step_execution(
            step_id=f"step-{ordinal}",
            phase="treatment",
            operation_ref=f"operation-{ordinal}",
            actor_ref="actor-1",
            request_receipt_id=f"request-{ordinal}",
            response_receipt_id=f"response-{ordinal}",
            status_code=200,
            final_status="EXECUTED",
            mutation_occurred=True,
        )
    observations = {
        "observer_receipts": [
            {
                "receipt_id": "observer-1",
                "step_id": "step-1",
                "target_reached": True,
            },
            {
                "receipt_id": "observer-2",
                "step_id": "step-2",
                "target_reached": True,
            },
        ]
    }
    return ProcessStepSemanticView(ledger, observations).all_rows()


def _bundle(step_rows: list[dict]):
    step_receipts = [
        _envelope(PROCESS_STEP_RECEIPT_SCHEMA, row["receipt_id"], row)
        for row in step_rows
    ]
    compile_receipt = _envelope("qualibug.compile-receipt.v1", "compile-1", {})
    fixture_receipt = _envelope(
        "qualibug.fixture-materialization-receipt.v1", "fixture-r1", {}
    )
    transport_receipt = _envelope(
        "qualibug.transport-receipt.v1", "transport-1", {}
    )
    observation_receipt = _envelope(
        "qualibug.observation-receipt.v1", "observation-1", {}
    )
    oracle_receipt = _envelope(
        "qualibug.oracle-invocation-receipt.v1", "oracle-1", {}
    )
    cleanup_execution_receipt = _envelope(
        "qualibug.cleanup-execution-receipt.v1", "cleanup-exec-1", {}
    )
    cleanup_verification_receipt = _envelope(
        "qualibug.cleanup-verification-receipt.v1", "cleanup-ver-1", {}
    )
    environment_receipt = _envelope(
        "qualibug.environment-restoration-receipt.v1", "environment-1", {}
    )
    receipts = [
        compile_receipt,
        fixture_receipt,
        *step_receipts,
        transport_receipt,
        observation_receipt,
        oracle_receipt,
        cleanup_execution_receipt,
        cleanup_verification_receipt,
        environment_receipt,
    ]
    return build_execution_receipt_bundle(
        bundle_id="bundle-1",
        receipts=receipts,
        compile_receipt_id="compile-1",
        fixture_provenance_receipt_ids=["fixture-r1"],
        required_step_receipt_ids=[row["receipt_id"] for row in step_rows],
        transport_receipt_ids=["transport-1"],
        observation_receipt_ids=["observation-1"],
        oracle_invocation_receipt_ids=["oracle-1"],
        cleanup_execution_receipt_ids=["cleanup-exec-1"],
        cleanup_verification_receipt_ids=["cleanup-ver-1"],
        environment_restoration_receipt_id="environment-1",
        **IDENTITY,
    )


def _lifecycle(bundle: dict):
    return derive_execution_lifecycle(
        execution_status="EXECUTED",
        compile_succeeded=True,
        fixture_required=True,
        fixture_materialized=True,
        state_precondition_required=True,
        state_precondition_established=True,
        required_steps_declared=True,
        all_required_steps_executed=True,
        observation_completed=True,
        oracle_evaluated=True,
        oracle_indeterminate=False,
        cleanup_required=True,
        cleanup_executed=True,
        cleanup_verified=True,
        environment_restored=True,
        receipt_bundle=bundle,
    )


def test_balanced_sealed_step_receipts_allow_true_completed() -> None:
    bundle = _bundle(_step_rows())

    assert bundle["complete"] is True
    assert bundle["process_step_audit"]["complete"] is True
    assert bundle["process_step_ledger_identity_consistent"] is True
    assert bundle["process_step_ledger_hash_consistent"] is True
    assert bundle["process_step_fact_hashes_valid"] is True
    assert bundle["process_step_sets_balanced"] is True
    assert _lifecycle(bundle)["lifecycle_state"] == "TRUE_COMPLETED"


def test_removed_step_is_detected_from_sealed_recorded_set() -> None:
    rows = _step_rows()
    bundle = _bundle(rows[:1])

    assert bundle["complete"] is False
    assert "process_step_set_mismatch" in bundle["validation_errors"]
    assert "ledger_recorded_step_ids" in bundle["process_step_set_mismatch_fields"]
    result = _lifecycle(bundle)
    assert result["lifecycle_state"] == "RECEIPT_INCOMPLETE"
    assert result["reason_code"] == "PROCESS_STEP_SET_MISMATCH"


def test_mixed_ledger_id_is_identity_mismatch() -> None:
    rows = _step_rows()
    rows[1] = deepcopy(rows[1])
    rows[1]["process_step_ledger_id"] = "psl-other"
    bundle = _bundle(rows)

    assert bundle["complete"] is False
    assert bundle["process_step_ledger_identity_consistent"] is False
    result = _lifecycle(bundle)
    assert result["lifecycle_state"] == "IDENTITY_MISMATCH"
    assert result["reason_code"] == "PROCESS_STEP_LEDGER_IDENTITY_MISMATCH"


def test_mixed_ledger_hash_is_receipt_incomplete() -> None:
    rows = _step_rows()
    rows[1] = deepcopy(rows[1])
    rows[1]["process_step_ledger_hash"] = "stale-ledger-hash"
    bundle = _bundle(rows)

    assert bundle["complete"] is False
    assert bundle["process_step_ledger_hash_consistent"] is False
    result = _lifecycle(bundle)
    assert result["lifecycle_state"] == "RECEIPT_INCOMPLETE"
    assert result["reason_code"] == "PROCESS_STEP_LEDGER_HASH_MISMATCH"


def test_outer_resign_cannot_hide_step_fact_tamper() -> None:
    rows = _step_rows()
    rows[1] = deepcopy(rows[1])
    rows[1]["status_code"] = 503
    bundle = _bundle(rows)

    assert bundle["complete"] is False
    assert bundle["process_step_fact_hashes_valid"] is False
    assert rows[1]["receipt_id"] in bundle[
        "process_step_fact_hash_mismatch_receipt_ids"
    ]
    result = _lifecycle(bundle)
    assert result["reason_code"] == "PROCESS_STEP_FACT_HASH_MISMATCH"


def test_reclassified_step_with_recomputed_fact_hash_still_breaks_set_balance() -> None:
    rows = _step_rows()
    rows[1] = deepcopy(rows[1])
    rows[1]["operation_accepted"] = False
    rows[1]["semantic_step_status"] = "OPERATION_REJECTED"
    rows[1]["step_completed"] = False
    rows[1]["step_failed"] = True
    rows[1]["step_fact_hash"] = _stable_hash(_canonical_step_fact(rows[1]))
    bundle = _bundle(rows)

    assert bundle["complete"] is False
    assert bundle["process_step_fact_hashes_valid"] is True
    assert bundle["process_step_sets_balanced"] is False
    assert "ledger_accepted_step_ids" in bundle["process_step_set_mismatch_fields"]
    result = _lifecycle(bundle)
    assert result["reason_code"] == "PROCESS_STEP_SET_MISMATCH"
