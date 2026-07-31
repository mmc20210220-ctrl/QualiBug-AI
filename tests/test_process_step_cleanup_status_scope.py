from ai_test_asset_center.operational_receipts import (
    build_canonical_receipt_envelope,
    build_execution_receipt_bundle,
    derive_execution_lifecycle,
)
from ai_test_asset_center.process_step_execution import (
    PROCESS_STEP_RECEIPT_SCHEMA,
    ProcessStepLedger,
)
from ai_test_asset_center.process_step_semantic_view import ProcessStepSemanticView


IDENTITY = {
    "campaign_id": "campaign-cleanup",
    "run_id": "run-cleanup",
    "obligation_id": "obligation-cleanup",
    "experiment_id": "experiment-cleanup",
    "fixture_id": "fixture-cleanup",
    "protocol_id": "protocol-cleanup",
}


def _envelope(receipt_type: str, receipt_id: str, payload: dict) -> dict:
    return build_canonical_receipt_envelope(
        receipt_type=receipt_type,
        receipt_id=receipt_id,
        payload=payload,
        code_commit_sha="commit-cleanup",
        tree_hash="tree-cleanup",
        **IDENTITY,
    )


def _execution_receipt(
    *,
    status: str = "ACCEPTED",
    reason_code: str = "",
) -> dict:
    accepted = status == "ACCEPTED"
    not_required = status == "NOT_REQUIRED"
    return {
        "schema_version": "qualibug.cleanup-execution-receipt.v1",
        "receipt_id": "cleanup-execution-1",
        "source_step_id": "step-1",
        "attempted": accepted,
        "transport_reached": accepted,
        "succeeded": accepted,
        "status_code": 200 if accepted else 0,
        "status": status,
        "reason_code": (
            reason_code
            or ("CLEANUP_NOT_REQUIRED" if not_required else "")
        ),
        "detail": "",
    }


def _verification_receipt(
    *,
    equivalence_status: str = "EQUIVALENT",
    reason_code: str = "",
) -> dict:
    return {
        "schema_version": "qualibug.cleanup-equivalence-receipt.v1",
        "receipt_id": "cleanup-verification-1",
        "source_step_id": "step-1",
        "equivalence_status": equivalence_status,
        "reason_code": reason_code,
        "detail": "",
    }


def _ledger(*, mutation_occurred: bool = True) -> ProcessStepLedger:
    ledger = ProcessStepLedger(
        experiment_id=IDENTITY["experiment_id"],
        fixture_id=IDENTITY["fixture_id"],
        campaign_id=IDENTITY["campaign_id"],
        run_id=IDENTITY["run_id"],
        obligation_id=IDENTITY["obligation_id"],
        protocol_id=IDENTITY["protocol_id"],
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
        mutation_occurred=mutation_occurred,
        cleanup_contract_id="cleanup-contract-1",
    )
    return ledger


def _bundle(
    *,
    cleanup_execution: dict,
    cleanup_verification: dict,
    mutation_occurred: bool = True,
) -> dict:
    observations = {
        "observer_receipts": [
            {
                "receipt_id": "observation-1",
                "step_id": "step-1",
                "target_reached": True,
            }
        ],
        "oracle_invocation_receipts": [
            {
                "receipt_id": "oracle-1",
                "step_id": "step-1",
                "evaluated": True,
            }
        ],
        "cleanup_execution_receipts": [cleanup_execution],
        "cleanup_verification_receipts": [cleanup_verification],
    }
    step_row = ProcessStepSemanticView(
        _ledger(mutation_occurred=mutation_occurred),
        observations,
    ).all_rows()[0]
    receipts = [
        _envelope("qualibug.compile-receipt.v1", "compile-1", {}),
        _envelope(
            "qualibug.fixture-materialization-receipt.v1",
            "fixture-receipt-1",
            {},
        ),
        _envelope(PROCESS_STEP_RECEIPT_SCHEMA, step_row["receipt_id"], step_row),
        _envelope("qualibug.transport-receipt.v1", "transport-1", {}),
        _envelope(
            "qualibug.observation-receipt.v1",
            "observation-1",
            {"step_id": "step-1"},
        ),
        _envelope(
            "qualibug.oracle-invocation-receipt.v1",
            "oracle-1",
            {"step_id": "step-1"},
        ),
        _envelope(
            "qualibug.cleanup-execution-receipt.v1",
            cleanup_execution["receipt_id"],
            cleanup_execution,
        ),
        _envelope(
            "qualibug.cleanup-verification-receipt.v1",
            cleanup_verification["receipt_id"],
            cleanup_verification,
        ),
        _envelope(
            "qualibug.environment-restoration-receipt.v1",
            "environment-1",
            {},
        ),
    ]
    return build_execution_receipt_bundle(
        bundle_id="bundle-cleanup",
        receipts=receipts,
        compile_receipt_id="compile-1",
        fixture_provenance_receipt_ids=["fixture-receipt-1"],
        required_step_receipt_ids=[step_row["receipt_id"]],
        transport_receipt_ids=["transport-1"],
        observation_receipt_ids=["observation-1"],
        oracle_invocation_receipt_ids=["oracle-1"],
        cleanup_execution_receipt_ids=[cleanup_execution["receipt_id"]],
        cleanup_verification_receipt_ids=[cleanup_verification["receipt_id"]],
        environment_restoration_receipt_id="environment-1",
        **IDENTITY,
    )


def _lifecycle(bundle: dict) -> dict:
    return derive_execution_lifecycle(
        execution_status="EXECUTED",
        compile_succeeded=True,
        fixture_required=True,
        fixture_materialized=True,
        state_precondition_required=False,
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


def test_accepted_execution_and_equivalent_verification_complete() -> None:
    bundle = _bundle(
        cleanup_execution=_execution_receipt(),
        cleanup_verification=_verification_receipt(),
    )
    scope = bundle["process_step_audit"]["evidence_scope_audit"]

    assert bundle["complete"] is True
    assert scope["cleanup_execution"]["complete"] is True
    assert scope["cleanup_verification"]["complete"] is True
    assert scope["invalid_semantic_status_receipt_ids"] == []
    assert _lifecycle(bundle)["lifecycle_state"] == "TRUE_COMPLETED"


def test_blocked_cleanup_execution_cannot_cover_write_step() -> None:
    execution = _execution_receipt(
        status="BLOCKED",
        reason_code="CLEANUP_BLOCKED_BEFORE_TRANSPORT",
    )
    bundle = _bundle(
        cleanup_execution=execution,
        cleanup_verification=_verification_receipt(),
    )
    scope = bundle["process_step_audit"]["evidence_scope_audit"]

    assert bundle["complete"] is False
    assert scope["cleanup_execution"]["invalid_semantic_status_receipt_ids"] == [
        execution["receipt_id"]
    ]
    assert scope["missing_cleanup_execution_step_ids"] == ["step-1"]
    assert "process_step_evidence_status_invalid" in bundle["validation_errors"]
    assert _lifecycle(bundle)["lifecycle_state"] == "RECEIPT_INCOMPLETE"


def test_indeterminate_cleanup_verification_cannot_cover_write_step() -> None:
    verification = _verification_receipt(
        equivalence_status="INDETERMINATE",
        reason_code="AFTER_CLEANUP_OBSERVATION_MISSING",
    )
    bundle = _bundle(
        cleanup_execution=_execution_receipt(),
        cleanup_verification=verification,
    )
    scope = bundle["process_step_audit"]["evidence_scope_audit"]

    assert bundle["complete"] is False
    assert scope["cleanup_verification"][
        "invalid_semantic_status_receipt_ids"
    ] == [verification["receipt_id"]]
    assert scope["missing_cleanup_verification_step_ids"] == ["step-1"]
    assert _lifecycle(bundle)["true_completed"] is False


def test_proven_not_required_cleanup_is_valid_terminal_evidence() -> None:
    execution = _execution_receipt(status="NOT_REQUIRED")
    verification = _verification_receipt(
        equivalence_status="NOT_APPLICABLE",
        reason_code="CLEANUP_NOT_REQUIRED",
    )
    bundle = _bundle(
        cleanup_execution=execution,
        cleanup_verification=verification,
        mutation_occurred=False,
    )
    scope = bundle["process_step_audit"]["evidence_scope_audit"]

    assert bundle["complete"] is True
    assert scope["cleanup_execution"]["complete"] is True
    assert scope["cleanup_verification"]["complete"] is True
    assert scope["invalid_semantic_status_receipt_ids"] == []
    assert _lifecycle(bundle)["lifecycle_state"] == "TRUE_COMPLETED"


def test_not_equivalent_cleanup_verification_is_never_success() -> None:
    verification = _verification_receipt(
        equivalence_status="NOT_EQUIVALENT",
        reason_code="BUSINESS_STATE_NOT_RESTORED",
    )
    bundle = _bundle(
        cleanup_execution=_execution_receipt(),
        cleanup_verification=verification,
    )
    scope = bundle["process_step_audit"]["evidence_scope_audit"]

    assert bundle["complete"] is False
    assert verification["receipt_id"] in scope[
        "invalid_semantic_status_receipt_ids"
    ]
    assert _lifecycle(bundle)["lifecycle_state"] == "RECEIPT_INCOMPLETE"
