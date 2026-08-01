from __future__ import annotations

from ai_test_asset_center.process_step_execution import ProcessStepLedger
from ai_test_asset_center.process_step_receipt_scope import (
    build_exact_step_receipt_projection,
    synchronize_scoped_receipts_from_observations,
)


def _ledger() -> ProcessStepLedger:
    ledger = ProcessStepLedger(
        experiment_id="experiment-1",
        campaign_id="campaign-1",
        run_id="run-1",
        obligation_id="obligation-1",
        protocol_id="protocol-1",
        required_step_ids=["write-1"],
    )
    ledger.record_step_execution(
        step_id="write-1",
        phase="treatment",
        operation_ref="create-order",
        actor_ref="admin",
        request_receipt_id="request-1",
        response_receipt_id="response-1",
        transport_receipt_id="transport-1",
        status_code=201,
        final_status="EXECUTED",
        operation_accepted=True,
    )
    return ledger


def test_fingerprint_projection_has_stable_identity() -> None:
    source = {
        "schema_version": "qualibug.cleanup-equivalence-receipt.v1",
        "fingerprint": "sealed-cleanup-fingerprint",
        "equivalence_status": "EQUIVALENT",
    }
    first = build_exact_step_receipt_projection(
        source,
        step_id="write-1",
        projection_kind="cleanup_equivalence",
    )
    second = build_exact_step_receipt_projection(
        source,
        step_id="write-1",
        projection_kind="cleanup_equivalence",
    )

    assert first == second
    assert first["receipt_id"].startswith("psp_")
    assert first["step_id"] == "write-1"


def test_diagnostic_cleanup_summary_never_becomes_formal_verification() -> None:
    ledger = _ledger()
    diagnostic = {
        "status": "COMPLETED",
        "database_cleanup_count": 0,
        "api_cleanup_count": 0,
    }
    observations = {"cleanup_verification": diagnostic}

    audit = synchronize_scoped_receipts_from_observations(ledger, observations)

    assert audit["complete"] is True
    assert observations.get("cleanup_verification_receipts", []) == []
    assert observations.get("process_step_cleanup_verification_receipts", []) == []
    assert ledger.get_step_row("write-1")["scoped_cleanup_receipt_ids"] == []


def test_repeated_scope_sealing_does_not_grow_formal_receipts() -> None:
    ledger = _ledger()
    verification = {
        "schema_version": "qualibug.cleanup-equivalence-receipt.v1",
        "receipt_id": "cleanup-verification-1",
        "source_step_id": "write-1",
        "equivalence_status": "EQUIVALENT",
    }
    observations = {"cleanup_verification_receipts": [verification]}

    first = synchronize_scoped_receipts_from_observations(ledger, observations)
    second = synchronize_scoped_receipts_from_observations(ledger, observations)

    assert first["complete"] is True
    assert second["complete"] is True
    assert observations["cleanup_verification_receipts"] == [verification]
    assert observations["process_step_cleanup_verification_receipts"] == [verification]
    assert ledger.get_step_row("write-1")["scoped_cleanup_receipt_ids"] == [
        "cleanup-verification-1"
    ]
