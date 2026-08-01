from __future__ import annotations

from ai_test_asset_center.process_step_execution import ProcessStepLedger
from ai_test_asset_center.process_step_receipt_scope import (
    synchronize_scoped_receipts_from_observations,
)


def _ledger() -> ProcessStepLedger:
    ledger = ProcessStepLedger(
        experiment_id="experiment-1",
        campaign_id="campaign-1",
        run_id="run-1",
        obligation_id="obligation-1",
        protocol_id="protocol-1",
        required_step_ids=["write-a", "write-b"],
    )
    for step_id in ("write-a", "write-b"):
        ledger.record_step_execution(
            step_id=step_id,
            phase="treatment",
            operation_ref=f"operation-{step_id}",
            actor_ref="actor-1",
            request_receipt_id=f"request-{step_id}",
            response_receipt_id=f"response-{step_id}",
            status_code=200,
            final_status="EXECUTED",
            operation_accepted=True,
        )
    return ledger


def _execution(step_id: str) -> dict:
    return {
        "schema_version": "qualibug.cleanup-execution-receipt.v1",
        "receipt_id": f"cleanup-execution-{step_id}",
        "source_step_id": step_id,
        "status": "ACCEPTED",
    }


def _verification(step_id: str) -> dict:
    return {
        "schema_version": "qualibug.cleanup-equivalence-receipt.v1",
        "receipt_id": f"cleanup-verification-{step_id}",
        "source_step_id": step_id,
        "equivalence_status": "EQUIVALENT",
    }


def test_graph_step_verifications_remain_formal_and_bind_exact_steps() -> None:
    ledger = _ledger()
    execution_set = {
        "schema_version": (
            "qualibug.process-graph-cleanup-execution-set.v1"
        ),
        "receipt_id": "graph-cleanup-execution-set",
        "write_step_ids": ["write-a", "write-b"],
        "step_cleanup_execution_receipts_by_id": {
            "write-a": _execution("write-a"),
            "write-b": _execution("write-b"),
        },
        # cleanup_equivalence binds these outputs onto the execution set before
        # the Finalizer hook publishes the same rows for exact Ledger binding.
        "step_cleanup_verification_receipts_by_id": {
            "write-a": _verification("write-a"),
            "write-b": _verification("write-b"),
        },
    }
    observations = {
        "cleanup_execution_receipt": execution_set,
        "cleanup_execution_receipts": [execution_set],
        # This is the transient Finalizer-hook shape. The aggregate equivalence
        # receipt is still the function return value and is not in observations.
        "cleanup_verification_receipts": [
            _verification("write-a"),
            _verification("write-b"),
        ],
    }

    audit = synchronize_scoped_receipts_from_observations(
        ledger,
        observations,
    )

    assert audit["complete"] is True
    assert audit["aggregate_cleanup_execution_receipt_ids"] == [
        "graph-cleanup-execution-set"
    ]
    assert audit["aggregate_cleanup_verification_receipt_ids"] == []
    # The formal Bundle layer keeps exact verification receipts until the
    # aggregate equivalence receipt is published; Ledger binding uses the same
    # immutable rows rather than a second materialization.
    assert [
        row["receipt_id"]
        for row in observations["cleanup_verification_receipts"]
    ] == [
        "cleanup-verification-write-a",
        "cleanup-verification-write-b",
    ]
    assert [
        row["receipt_id"]
        for row in observations[
            "process_step_cleanup_verification_receipts"
        ]
    ] == [
        "cleanup-verification-write-a",
        "cleanup-verification-write-b",
    ]
    assert ledger.get_step_row("write-a")[
        "scoped_cleanup_receipt_ids"
    ] == [
        "cleanup-execution-write-a",
        "cleanup-verification-write-a",
    ]
    assert ledger.get_step_row("write-b")[
        "scoped_cleanup_receipt_ids"
    ] == [
        "cleanup-execution-write-b",
        "cleanup-verification-write-b",
    ]


def test_ordinary_verification_receipt_remains_in_bundle_list() -> None:
    ledger = _ledger()
    ordinary = _verification("write-a")
    observations = {"cleanup_verification_receipts": [ordinary]}

    audit = synchronize_scoped_receipts_from_observations(
        ledger,
        observations,
    )

    assert audit["complete"] is True
    assert audit["aggregate_cleanup_receipt_ids"] == []
    assert observations["cleanup_verification_receipts"] == [ordinary]
    assert observations["process_step_cleanup_verification_receipts"] == [
        ordinary
    ]
