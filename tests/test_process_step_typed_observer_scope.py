import ai_test_asset_center.experiment_outcome_finalizer as finalizer
from ai_test_asset_center.observer_contracts_base import (
    build_observer_receipt,
    validate_observer_receipt,
)
from ai_test_asset_center.operational_receipts import (
    build_canonical_receipt_envelope,
    build_execution_receipt_bundle,
    derive_execution_lifecycle,
)
from ai_test_asset_center.process_step_execution import (
    PROCESS_STEP_RECEIPT_SCHEMA,
    ProcessStepLedger,
)
from ai_test_asset_center.process_step_receipt_scope import (
    extract_receipt_step_scope,
)
from ai_test_asset_center.process_step_semantic_view import ProcessStepSemanticView


IDENTITY = {
    "campaign_id": "campaign-observer",
    "run_id": "run-observer",
    "obligation_id": "obligation-observer",
    "experiment_id": "experiment-observer",
    "fixture_id": "fixture-observer",
    "protocol_id": "protocol-observer",
}


def _typed_receipt(
    observer_id: str,
    *,
    status: str = "OBSERVED",
    reason_code: str = "",
    evidence: dict | None = None,
) -> dict:
    return build_observer_receipt(
        observer_id=observer_id,
        status=status,
        reason_code=reason_code,
        evidence=evidence or {},
        campaign_id=IDENTITY["campaign_id"],
        execution_id=IDENTITY["run_id"],
    )


def _envelope(receipt_type: str, receipt_id: str, payload: dict) -> dict:
    return build_canonical_receipt_envelope(
        receipt_type=receipt_type,
        receipt_id=receipt_id,
        payload=payload,
        code_commit_sha="commit-observer",
        tree_hash="tree-observer",
        **IDENTITY,
    )


def test_http_response_is_reissued_once_per_explicit_execution_step() -> None:
    experiment = {
        "control_plan": [{"step_id": "control-1"}],
        "treatment_plan": [{"step_id": "treatment-1"}],
        "observers": [
            {"observer_id": "http_response"},
            {"observer_id": "business_effect"},
        ],
    }
    observations = {
        "execution_steps": [
            {
                "step_id": "control-1",
                "phase": "control",
                "operation_ref": "operation-control",
                "status_code": 200,
                "body": {"id": 1, "status": "before"},
            },
            {
                "step_id": "treatment-1",
                "phase": "treatment",
                "operation_ref": "operation-treatment",
                "status_code": 201,
                "body": {"id": 1, "status": "after"},
            },
        ]
    }
    aggregate_http = _typed_receipt(
        "http_response",
        evidence={"statuses": [200, 201]},
    )
    aggregate_business = _typed_receipt(
        "business_effect",
        evidence={"business_effect_observed": True, "effect_count": 1},
    )

    receipts = finalizer._scope_generated_observer_receipts(
        experiment=experiment,
        observations=observations,
        generated=[aggregate_http, aggregate_business],
    )

    http_receipts = [
        row for row in receipts if row["observer_id"] == "http_response"
    ]
    business_receipts = [
        row for row in receipts if row["observer_id"] == "business_effect"
    ]
    assert len(http_receipts) == 2
    assert len({row["receipt_id"] for row in http_receipts}) == 2
    assert {
        row["evidence"]["step_id"] for row in http_receipts
    } == {"control-1", "treatment-1"}
    assert {
        row["evidence"]["status_code"] for row in http_receipts
    } == {200, 201}
    assert all(row["status"] == "OBSERVED" for row in http_receipts)
    assert all(
        extract_receipt_step_scope(row)["status"] == "EXACT"
        for row in http_receipts
    )
    assert all(validate_observer_receipt(row) == row for row in http_receipts)

    assert len(business_receipts) == 1
    business = business_receipts[0]
    assert business["evidence"]["step_id"] == "treatment-1"
    assert business["evidence"]["scope_basis"] == (
        "protocol_final_treatment_subject"
    )
    assert business["evidence"]["business_effect_observed"] is True
    assert validate_observer_receipt(business) == business


def test_missing_http_response_produces_exact_failed_receipt() -> None:
    aggregate = _typed_receipt(
        "http_response",
        status="FAILED",
        reason_code="HTTP_RESPONSE_MISSING",
        evidence={"statuses": [0]},
    )
    receipts = finalizer._step_scoped_http_response_receipts(
        observations={
            "execution_steps": [
                {
                    "step_id": "treatment-1",
                    "phase": "treatment",
                    "operation_ref": "operation-1",
                    "status_code": 0,
                    "body": None,
                }
            ]
        },
        aggregate_receipt=aggregate,
    )

    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "HTTP_RESPONSE_MISSING"
    assert receipt["evidence"]["step_id"] == "treatment-1"
    assert extract_receipt_step_scope(receipt)["status"] == "EXACT"
    assert validate_observer_receipt(receipt) == receipt


def test_failed_typed_observation_cannot_complete_bundle() -> None:
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
    valid_observation = _typed_receipt(
        "business_effect",
        evidence={
            "step_id": "step-1",
            "target_reached": True,
            "business_effect_observed": True,
        },
    )
    failed_observation = _typed_receipt(
        "http_response",
        status="FAILED",
        reason_code="HTTP_RESPONSE_MISSING",
        evidence={
            "step_id": "step-1",
            "status_code": 0,
            "response_received": False,
        },
    )
    observations = {
        "observer_receipts": [valid_observation, failed_observation],
        "oracle_invocation_receipts": [
            {"receipt_id": "oracle-1", "step_id": "step-1"}
        ],
        "cleanup_execution_receipts": [
            {"receipt_id": "cleanup-execution-1", "step_id": "step-1"}
        ],
        "cleanup_verification_receipts": [
            {"receipt_id": "cleanup-verification-1", "step_id": "step-1"}
        ],
    }
    step_row = ProcessStepSemanticView(ledger, observations).all_rows()[0]

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
            valid_observation["receipt_id"],
            valid_observation,
        ),
        _envelope(
            "qualibug.observation-receipt.v1",
            failed_observation["receipt_id"],
            failed_observation,
        ),
        _envelope(
            "qualibug.oracle-invocation-receipt.v1",
            "oracle-1",
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
    bundle = build_execution_receipt_bundle(
        bundle_id="bundle-observer",
        receipts=receipts,
        compile_receipt_id="compile-1",
        fixture_provenance_receipt_ids=["fixture-receipt-1"],
        required_step_receipt_ids=[step_row["receipt_id"]],
        transport_receipt_ids=["transport-1"],
        observation_receipt_ids=[
            valid_observation["receipt_id"],
            failed_observation["receipt_id"],
        ],
        oracle_invocation_receipt_ids=["oracle-1"],
        cleanup_execution_receipt_ids=["cleanup-execution-1"],
        cleanup_verification_receipt_ids=["cleanup-verification-1"],
        environment_restoration_receipt_id="environment-1",
        **IDENTITY,
    )
    scope = bundle["process_step_audit"]["evidence_scope_audit"]

    assert bundle["complete"] is False
    assert scope["observation"]["invalid_semantic_status_receipt_ids"] == [
        failed_observation["receipt_id"]
    ]
    assert scope["invalid_semantic_status_receipt_ids"] == [
        failed_observation["receipt_id"]
    ]
    assert "process_step_evidence_status_invalid" in bundle["validation_errors"]
    assert "evidence_status" in bundle["process_step_set_mismatch_fields"]

    lifecycle = derive_execution_lifecycle(
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
    assert lifecycle["true_completed"] is False
    assert lifecycle["lifecycle_state"] == "RECEIPT_INCOMPLETE"
    assert lifecycle["reason_code"] == "PROCESS_STEP_SET_MISMATCH"
