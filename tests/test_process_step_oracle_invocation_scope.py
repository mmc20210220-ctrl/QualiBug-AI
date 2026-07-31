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
    PROCESS_STEP_ORACLE_INVOCATION_SCHEMA,
    synchronize_scoped_receipts_from_observations,
)
from ai_test_asset_center.process_step_semantic_view import ProcessStepSemanticView


IDENTITY = {
    "campaign_id": "campaign-oracle",
    "run_id": "run-oracle",
    "obligation_id": "obligation-oracle",
    "experiment_id": "experiment-oracle",
    "fixture_id": "fixture-oracle",
    "protocol_id": "protocol-oracle",
}


def _ledger(step_ids: list[str]) -> ProcessStepLedger:
    ledger = ProcessStepLedger(
        experiment_id=IDENTITY["experiment_id"],
        fixture_id=IDENTITY["fixture_id"],
        campaign_id=IDENTITY["campaign_id"],
        run_id=IDENTITY["run_id"],
        obligation_id=IDENTITY["obligation_id"],
        protocol_id=IDENTITY["protocol_id"],
        required_step_ids=step_ids,
    )
    for ordinal, step_id in enumerate(step_ids, start=1):
        ledger.record_step_execution(
            step_id=step_id,
            phase="treatment",
            operation_ref=f"operation-{ordinal}",
            actor_ref="actor-1",
            request_receipt_id=f"request-{ordinal}",
            response_receipt_id=f"response-{ordinal}",
            status_code=200,
            final_status="EXECUTED",
            mutation_occurred=True,
        )
    return ledger


def _verdict(status: str) -> dict:
    return {
        "receipt_id": "oracle-main-1",
        "status": status,
        "verdict": (
            "property_held"
            if status == "PROPERTY_HELD"
            else "indeterminate"
        ),
        "activation_receipt_id": "activation-1",
        "assertion_receipt_ids": ["assertion-1"],
    }


def _envelope(receipt_type: str, receipt_id: str, payload: dict) -> dict:
    return build_canonical_receipt_envelope(
        receipt_type=receipt_type,
        receipt_id=receipt_id,
        payload=payload,
        code_commit_sha="commit-oracle",
        tree_hash="tree-oracle",
        **IDENTITY,
    )


def _observations(step_ids: list[str], oracle_status: str) -> dict:
    return {
        "observer_receipts": [
            {
                "receipt_id": f"observation-{step_id}",
                "step_id": step_id,
                "target_reached": True,
            }
            for step_id in step_ids
        ],
        "cleanup_execution_receipts": [
            {
                "receipt_id": f"cleanup-execution-{step_id}",
                "step_id": step_id,
            }
            for step_id in step_ids
        ],
        "cleanup_verification_receipts": [
            {
                "receipt_id": f"cleanup-verification-{step_id}",
                "step_id": step_id,
            }
            for step_id in step_ids
        ],
        "oracle_verdict": _verdict(oracle_status),
    }


def _bundle(step_row: dict, observations: dict) -> dict:
    invocation_receipts = list(observations["oracle_invocation_receipts"])
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
            "observation-step-1",
            {"step_id": "step-1"},
        ),
        *[
            _envelope(
                "qualibug.oracle-invocation-receipt.v1",
                row["receipt_id"],
                row,
            )
            for row in invocation_receipts
        ],
        _envelope(
            "qualibug.cleanup-execution-receipt.v1",
            "cleanup-execution-step-1",
            {"step_id": "step-1"},
        ),
        _envelope(
            "qualibug.cleanup-verification-receipt.v1",
            "cleanup-verification-step-1",
            {"step_id": "step-1"},
        ),
        _envelope(
            "qualibug.environment-restoration-receipt.v1",
            "environment-1",
            {},
        ),
    ]
    return build_execution_receipt_bundle(
        bundle_id="bundle-oracle",
        receipts=receipts,
        compile_receipt_id="compile-1",
        fixture_provenance_receipt_ids=["fixture-receipt-1"],
        required_step_receipt_ids=[step_row["receipt_id"]],
        transport_receipt_ids=["transport-1"],
        observation_receipt_ids=["observation-step-1"],
        oracle_invocation_receipt_ids=[
            row["receipt_id"] for row in invocation_receipts
        ],
        cleanup_execution_receipt_ids=["cleanup-execution-step-1"],
        cleanup_verification_receipt_ids=["cleanup-verification-step-1"],
        environment_restoration_receipt_id="environment-1",
        **IDENTITY,
    )


def test_oracle_verdict_materializes_one_unique_invocation_per_step() -> None:
    ledger = _ledger(["step-1", "step-2"])
    observations = _observations(
        ["step-1", "step-2"],
        "PROPERTY_HELD",
    )

    first = synchronize_scoped_receipts_from_observations(ledger, observations)
    receipt_ids_before = [
        row["receipt_id"] for row in observations["oracle_invocation_receipts"]
    ]
    second = synchronize_scoped_receipts_from_observations(ledger, observations)
    receipts = observations["oracle_invocation_receipts"]

    assert len(receipts) == 2
    assert len(set(receipt_ids_before)) == 2
    assert [row["receipt_id"] for row in receipts] == receipt_ids_before
    assert {row["step_id"] for row in receipts} == {"step-1", "step-2"}
    assert all(
        row["schema_version"] == PROCESS_STEP_ORACLE_INVOCATION_SCHEMA
        for row in receipts
    )
    assert all(row["source_oracle_receipt_id"] == "oracle-main-1" for row in receipts)
    assert all(row["evaluated"] is True for row in receipts)
    assert first["oracle_verdict_excluded_from_step_scope"] is True
    assert second["materialized_oracle_invocation_receipt_count"] == 2
    assert first["oracle"]["unbound"] == []

    rows = {row["step_id"]: row for row in ledger.all_rows()}
    assert rows["step-1"]["scoped_oracle_receipt_ids"] == [
        next(row["receipt_id"] for row in receipts if row["step_id"] == "step-1")
    ]
    assert rows["step-2"]["scoped_oracle_receipt_ids"] == [
        next(row["receipt_id"] for row in receipts if row["step_id"] == "step-2")
    ]


def test_existing_exact_invocation_is_preserved_without_duplicate_generation() -> None:
    ledger = _ledger(["step-1", "step-2"])
    existing = {
        "receipt_id": "existing-step-1",
        "step_id": "step-1",
        "evaluated": True,
    }
    observations = _observations(
        ["step-1", "step-2"],
        "PROPERTY_HELD",
    )
    observations["oracle_invocation_receipts"] = [existing]

    synchronize_scoped_receipts_from_observations(ledger, observations)
    receipts = observations["oracle_invocation_receipts"]

    assert receipts[0] == existing
    assert len(receipts) == 2
    assert {row["step_id"] for row in receipts} == {"step-1", "step-2"}
    assert sum(row["step_id"] == "step-1" for row in receipts) == 1


def test_property_held_step_invocation_allows_bundle_completion() -> None:
    ledger = _ledger(["step-1"])
    observations = _observations(["step-1"], "PROPERTY_HELD")
    step_row = ProcessStepSemanticView(ledger, observations).all_rows()[0]
    bundle = _bundle(step_row, observations)

    scope = bundle["process_step_audit"]["evidence_scope_audit"]
    assert bundle["complete"] is True
    assert scope["oracle_invocation"]["complete"] is True
    assert scope["oracle_invocation"]["invalid_semantic_status_receipt_ids"] == []

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
    assert lifecycle["lifecycle_state"] == "TRUE_COMPLETED"


def test_indeterminate_step_invocation_cannot_complete_bundle() -> None:
    ledger = _ledger(["step-1"])
    observations = _observations(["step-1"], "INDETERMINATE")
    step_row = ProcessStepSemanticView(ledger, observations).all_rows()[0]
    invocation = observations["oracle_invocation_receipts"][0]
    bundle = _bundle(step_row, observations)

    scope = bundle["process_step_audit"]["evidence_scope_audit"]
    assert invocation["evaluated"] is False
    assert bundle["complete"] is False
    assert scope["oracle_invocation"]["invalid_semantic_status_receipt_ids"] == [
        invocation["receipt_id"]
    ]
    assert scope["missing_oracle_step_ids"] == ["step-1"]
    assert "process_step_evidence_status_invalid" in bundle["validation_errors"]

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
