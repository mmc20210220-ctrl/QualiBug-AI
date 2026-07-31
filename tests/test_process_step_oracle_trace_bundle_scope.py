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
    "campaign_id": "campaign-trace",
    "run_id": "run-trace",
    "obligation_id": "obligation-trace",
    "experiment_id": "experiment-trace",
    "fixture_id": "fixture-trace",
    "protocol_id": "protocol-trace",
}


def _envelope(receipt_type: str, receipt_id: str, payload: dict) -> dict:
    return build_canonical_receipt_envelope(
        receipt_type=receipt_type,
        receipt_id=receipt_id,
        payload=payload,
        code_commit_sha="commit-trace",
        tree_hash="tree-trace",
        **IDENTITY,
    )


def _ledger() -> ProcessStepLedger:
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
        mutation_occurred=True,
    )
    return ledger


def _base_observations() -> dict:
    return {
        "observer_receipts": [
            {
                "receipt_id": "observation-1",
                "step_id": "step-1",
                "target_reached": True,
            }
        ],
        "oracle_invocation_receipts": [
            {
                "receipt_id": "oracle-invocation-1",
                "step_id": "step-1",
                "evaluated": True,
            }
        ],
        "cleanup_execution_receipts": [
            {
                "receipt_id": "cleanup-execution-1",
                "step_id": "step-1",
                "executed": True,
            }
        ],
        "cleanup_verification_receipts": [
            {
                "receipt_id": "cleanup-verification-1",
                "step_id": "step-1",
                "verified": True,
            }
        ],
    }


def _bundle(step_row: dict, *, include_trace: bool) -> dict:
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
            "oracle-invocation-1",
            {"step_id": "step-1"},
        ),
        _envelope(
            "qualibug.cleanup-execution-receipt.v1",
            "cleanup-execution-1",
            {"step_id": "step-1"},
        ),
        _envelope(
            "qualibug.cleanup-verification-receipt.v1",
            "cleanup-verification-1",
            {"step_id": "step-1"},
        ),
        _envelope(
            "qualibug.environment-restoration-receipt.v1",
            "environment-1",
            {},
        ),
    ]
    oracle_trace_ids: list[str] = []
    if include_trace:
        receipts.append(
            _envelope(
                "qualibug.oracle-trace-receipt.v1",
                "oracle-trace-1",
                {"step_id": "step-1", "trace_kind": "evaluation"},
            )
        )
        oracle_trace_ids.append("oracle-trace-1")

    return build_execution_receipt_bundle(
        bundle_id="bundle-trace",
        receipts=receipts,
        compile_receipt_id="compile-1",
        fixture_provenance_receipt_ids=["fixture-receipt-1"],
        required_step_receipt_ids=[step_row["receipt_id"]],
        transport_receipt_ids=["transport-1"],
        observation_receipt_ids=["observation-1"],
        oracle_invocation_receipt_ids=["oracle-invocation-1"],
        oracle_trace_receipt_ids=oracle_trace_ids,
        cleanup_execution_receipt_ids=["cleanup-execution-1"],
        cleanup_verification_receipt_ids=["cleanup-verification-1"],
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


def test_identified_raw_oracle_trace_closes_bundle_scope() -> None:
    observations = _base_observations()
    observations["oracle_trace"] = [
        {
            "receipt_id": "oracle-trace-1",
            "step_id": "step-1",
            "trace_kind": "evaluation",
        }
    ]
    step_row = ProcessStepSemanticView(_ledger(), observations).all_rows()[0]

    assert observations["oracle_trace_receipts"] == observations["oracle_trace"]
    assert step_row["scoped_oracle_receipt_ids"] == [
        "oracle-invocation-1",
        "oracle-trace-1",
    ]

    bundle = _bundle(step_row, include_trace=True)
    scope = bundle["process_step_audit"]["evidence_scope_audit"]

    assert bundle["complete"] is True
    assert scope["oracle_invocation"]["complete"] is True
    assert scope["oracle_trace"]["complete"] is True
    assert scope["oracle_trace"]["exact_owner_by_receipt"] == {
        "oracle-trace-1": "step-1"
    }
    assert _lifecycle(bundle)["lifecycle_state"] == "TRUE_COMPLETED"


def test_anonymous_raw_oracle_trace_never_enters_formal_bundle() -> None:
    observations = _base_observations()
    anonymous_trace = {
        "step_id": "step-1",
        "trace_kind": "diagnostic",
    }
    observations["oracle_trace"] = [anonymous_trace]
    step_row = ProcessStepSemanticView(_ledger(), observations).all_rows()[0]

    assert observations["oracle_trace"] == [anonymous_trace]
    assert observations["oracle_trace_receipts"] == []
    assert step_row["scoped_oracle_receipt_ids"] == ["oracle-invocation-1"]

    bundle = _bundle(step_row, include_trace=False)
    scope = bundle["process_step_audit"]["evidence_scope_audit"]

    assert bundle["complete"] is True
    assert scope["oracle_trace"]["discovered_receipt_ids"] == []
    assert scope["oracle_trace"]["owners_by_receipt"] == {}
    assert _lifecycle(bundle)["lifecycle_state"] == "TRUE_COMPLETED"
