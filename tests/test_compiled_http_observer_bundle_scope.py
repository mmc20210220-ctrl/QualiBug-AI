from copy import deepcopy

from ai_test_asset_center.experiment_compiler_obligation import make_experiment
from ai_test_asset_center.observer_contracts_base import build_observer_receipt
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
    "campaign_id": "campaign-http-scope",
    "run_id": "run-http-scope",
    "obligation_id": "obligation-http-scope",
    "experiment_id": "experiment-http-scope",
    "fixture_id": "fixture-http-scope",
    "protocol_id": "protocol-http-scope",
}
REQUIRED_STEP_IDS = ["control-1", "treatment-1"]


def _compiled_receipt() -> dict:
    experiment = make_experiment(
        obligation_id=IDENTITY["obligation_id"],
        risk_family="state",
        control_plan=[{"step_id": "control-1"}],
        treatment_plan=[{"step_id": "treatment-1"}],
        observers=[
            {"observer_id": "http_response"},
            {"observer_id": "business_effect"},
        ],
        compile_receipt={"status": "COMPILED", "reason_code": ""},
    )
    return dict(experiment["compile_receipt"])


def _typed_observer(
    observer_id: str,
    step_id: str,
    *,
    target_reached: bool,
) -> dict:
    return build_observer_receipt(
        observer_id=observer_id,
        status="OBSERVED",
        reason_code="",
        evidence={
            "step_id": step_id,
            "target_reached": target_reached,
            "status_code": 200,
            "response_received": True,
        },
        campaign_id=IDENTITY["campaign_id"],
        execution_id=IDENTITY["run_id"],
    )


def _cleanup_execution(step_id: str) -> dict:
    return {
        "schema_version": "qualibug.cleanup-execution-receipt.v1",
        "receipt_id": f"cleanup-execution-{step_id}",
        "source_step_id": step_id,
        "attempted": True,
        "transport_reached": True,
        "succeeded": True,
        "status_code": 200,
        "status": "ACCEPTED",
        "reason_code": "",
    }


def _cleanup_verification(step_id: str) -> dict:
    return {
        "schema_version": "qualibug.cleanup-equivalence-receipt.v1",
        "receipt_id": f"cleanup-verification-{step_id}",
        "source_step_id": step_id,
        "equivalence_status": "EQUIVALENT",
        "reason_code": "",
    }


def _envelope(receipt_type: str, receipt_id: str, payload: dict) -> dict:
    return build_canonical_receipt_envelope(
        receipt_type=receipt_type,
        receipt_id=receipt_id,
        payload=payload,
        code_commit_sha="commit-http-scope",
        tree_hash="tree-http-scope",
        **IDENTITY,
    )


def _bundle(
    *,
    actual_http_step_ids: list[str],
    compile_receipt: dict | None = None,
    include_extra_step: bool = False,
) -> dict:
    recorded_step_ids = [
        *REQUIRED_STEP_IDS,
        *(["unexpected-1"] if include_extra_step else []),
    ]
    ledger = ProcessStepLedger(
        experiment_id=IDENTITY["experiment_id"],
        fixture_id=IDENTITY["fixture_id"],
        campaign_id=IDENTITY["campaign_id"],
        run_id=IDENTITY["run_id"],
        obligation_id=IDENTITY["obligation_id"],
        protocol_id=IDENTITY["protocol_id"],
        required_step_ids=REQUIRED_STEP_IDS,
    )
    for ordinal, step_id in enumerate(recorded_step_ids, start=1):
        required = step_id in REQUIRED_STEP_IDS
        ledger.record_step_execution(
            step_id=step_id,
            phase="control" if step_id.startswith("control") else "treatment",
            operation_ref=f"operation-{ordinal}",
            actor_ref="actor-1",
            request_receipt_id=f"request-{ordinal}",
            response_receipt_id=f"response-{ordinal}",
            status_code=200,
            final_status="EXECUTED",
            mutation_occurred=required,
            cleanup_contract_id=(
                f"cleanup-contract-{ordinal}" if required else ""
            ),
        )

    http_receipts = [
        _typed_observer("http_response", step_id, target_reached=False)
        for step_id in actual_http_step_ids
    ]
    business_receipts = [
        _typed_observer("business_effect", step_id, target_reached=True)
        for step_id in recorded_step_ids
    ]
    oracle_receipts = [
        {"receipt_id": f"oracle-{step_id}", "step_id": step_id}
        for step_id in recorded_step_ids
    ]
    cleanup_execution_receipts = [
        _cleanup_execution(step_id) for step_id in REQUIRED_STEP_IDS
    ]
    cleanup_verification_receipts = [
        _cleanup_verification(step_id) for step_id in REQUIRED_STEP_IDS
    ]
    observations = {
        "observer_receipts": [*http_receipts, *business_receipts],
        "oracle_invocation_receipts": oracle_receipts,
        "cleanup_execution_receipts": cleanup_execution_receipts,
        "cleanup_verification_receipts": cleanup_verification_receipts,
    }
    step_rows = ProcessStepSemanticView(ledger, observations).all_rows()

    compile_payload = dict(compile_receipt or _compiled_receipt())
    receipts = [
        _envelope("qualibug.compile-receipt.v1", "compile-1", compile_payload),
        _envelope(
            "qualibug.fixture-materialization-receipt.v1",
            "fixture-receipt-1",
            {},
        ),
        *[
            _envelope(PROCESS_STEP_RECEIPT_SCHEMA, row["receipt_id"], row)
            for row in step_rows
        ],
        _envelope("qualibug.transport-receipt.v1", "transport-1", {}),
        *[
            _envelope(
                "qualibug.observation-receipt.v1",
                row["receipt_id"],
                row,
            )
            for row in [*http_receipts, *business_receipts]
        ],
        *[
            _envelope(
                "qualibug.oracle-invocation-receipt.v1",
                row["receipt_id"],
                row,
            )
            for row in oracle_receipts
        ],
        *[
            _envelope(
                "qualibug.cleanup-execution-receipt.v1",
                row["receipt_id"],
                row,
            )
            for row in cleanup_execution_receipts
        ],
        *[
            _envelope(
                "qualibug.cleanup-verification-receipt.v1",
                row["receipt_id"],
                row,
            )
            for row in cleanup_verification_receipts
        ],
        _envelope(
            "qualibug.environment-restoration-receipt.v1",
            "environment-1",
            {},
        ),
    ]
    return build_execution_receipt_bundle(
        bundle_id="bundle-http-scope",
        receipts=receipts,
        compile_receipt_id="compile-1",
        fixture_provenance_receipt_ids=["fixture-receipt-1"],
        required_step_receipt_ids=[row["receipt_id"] for row in step_rows],
        transport_receipt_ids=["transport-1"],
        observation_receipt_ids=[
            row["receipt_id"]
            for row in [*http_receipts, *business_receipts]
        ],
        oracle_invocation_receipt_ids=[
            row["receipt_id"] for row in oracle_receipts
        ],
        cleanup_execution_receipt_ids=[
            row["receipt_id"] for row in cleanup_execution_receipts
        ],
        cleanup_verification_receipt_ids=[
            row["receipt_id"] for row in cleanup_verification_receipts
        ],
        environment_restoration_receipt_id="environment-1",
        **IDENTITY,
    )


def _binding(bundle: dict) -> dict:
    return bundle["process_step_audit"]["evidence_scope_audit"][
        "observer_subject_binding"
    ]


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


def test_compiled_http_scope_matches_exact_runtime_receipts() -> None:
    bundle = _bundle(actual_http_step_ids=list(REQUIRED_STEP_IDS))
    binding = _binding(bundle)

    assert bundle["complete"] is True
    assert binding["complete"] is True
    assert binding["declaration_valid"] is True
    assert binding["expected_http_step_ids"] == REQUIRED_STEP_IDS
    assert binding["actual_http_step_ids"] == REQUIRED_STEP_IDS
    assert binding["missing_http_step_ids"] == []
    assert binding["unexpected_http_step_ids"] == []
    assert binding["stored_binding_hash"] == binding[
        "recomputed_binding_hash"
    ]
    assert _lifecycle(bundle)["lifecycle_state"] == "TRUE_COMPLETED"


def test_missing_compiled_http_step_blocks_bundle() -> None:
    bundle = _bundle(actual_http_step_ids=["control-1"])
    binding = _binding(bundle)

    assert bundle["complete"] is False
    assert binding["missing_http_step_ids"] == ["treatment-1"]
    assert binding["unexpected_http_step_ids"] == []
    assert "compiled_observer_subject_scope_mismatch" in bundle[
        "validation_errors"
    ]
    assert "observer_subject_scope" in bundle[
        "process_step_set_mismatch_fields"
    ]
    assert _lifecycle(bundle)["true_completed"] is False


def test_unexpected_runtime_http_step_blocks_bundle() -> None:
    bundle = _bundle(
        actual_http_step_ids=[*REQUIRED_STEP_IDS, "unexpected-1"],
        include_extra_step=True,
    )
    binding = _binding(bundle)

    assert bundle["complete"] is False
    assert binding["missing_http_step_ids"] == []
    assert binding["unexpected_http_step_ids"] == ["unexpected-1"]
    assert binding["actual_http_step_ids"] == [
        "control-1",
        "treatment-1",
        "unexpected-1",
    ]
    assert "compiled_observer_subject_scope_mismatch" in bundle[
        "validation_errors"
    ]


def test_tampered_binding_hash_blocks_even_when_step_sets_match() -> None:
    compile_receipt = _compiled_receipt()
    compile_receipt = deepcopy(compile_receipt)
    compile_receipt["observer_subject_binding_receipt"][
        "binding_hash"
    ] = "tampered-binding-hash"
    bundle = _bundle(
        actual_http_step_ids=list(REQUIRED_STEP_IDS),
        compile_receipt=compile_receipt,
    )
    binding = _binding(bundle)

    assert bundle["complete"] is False
    assert binding["expected_http_step_ids"] == REQUIRED_STEP_IDS
    assert binding["actual_http_step_ids"] == REQUIRED_STEP_IDS
    assert binding["declaration_valid"] is False
    assert binding["stored_binding_hash"] != binding[
        "recomputed_binding_hash"
    ]


def test_typed_http_without_compile_binding_fails_closed() -> None:
    compile_receipt = _compiled_receipt()
    compile_receipt = deepcopy(compile_receipt)
    compile_receipt.pop("observer_subject_binding_receipt", None)
    bundle = _bundle(
        actual_http_step_ids=list(REQUIRED_STEP_IDS),
        compile_receipt=compile_receipt,
    )
    binding = _binding(bundle)

    assert bundle["complete"] is False
    assert binding["required"] is True
    assert binding["declared"] is False
    assert binding["declaration_valid"] is False
    assert binding["compile_subject_authority_enforced"] is True
    assert "compiled_observer_subject_scope_mismatch" in bundle[
        "validation_errors"
    ]
