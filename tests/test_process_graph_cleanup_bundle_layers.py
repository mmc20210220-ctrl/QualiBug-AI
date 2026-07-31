from copy import deepcopy

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
    "campaign_id": "campaign-graph-cleanup",
    "run_id": "run-graph-cleanup",
    "obligation_id": "obligation-graph-cleanup",
    "experiment_id": "experiment-graph-cleanup",
    "fixture_id": "fixture-graph-cleanup",
    "protocol_id": "protocol-graph-cleanup",
}
STEP_IDS = ["write-1", "write-2"]


def _envelope(receipt_type: str, receipt_id: str, payload: dict) -> dict:
    return build_canonical_receipt_envelope(
        receipt_type=receipt_type,
        receipt_id=receipt_id,
        payload=payload,
        code_commit_sha="commit-graph-cleanup",
        tree_hash="tree-graph-cleanup",
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
        required_step_ids=STEP_IDS,
    )
    for ordinal, step_id in enumerate(STEP_IDS, start=1):
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
            cleanup_contract_id=f"cleanup-contract-{ordinal}",
        )
    return ledger


def _node_execution(step_id: str) -> dict:
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


def _node_verification(step_id: str) -> dict:
    return {
        "schema_version": "qualibug.cleanup-equivalence-receipt.v1",
        "receipt_id": f"cleanup-verification-{step_id}",
        "source_step_id": step_id,
        "equivalence_status": "EQUIVALENT",
        "reason_code": "",
    }


def _aggregate_execution(nodes: dict[str, dict]) -> dict:
    source_ids = [nodes[step_id]["receipt_id"] for step_id in STEP_IDS]
    return {
        "schema_version": "qualibug.process-graph-cleanup-execution-set.v1",
        "receipt_id": "graph-cleanup-execution-set-1",
        "write_step_ids": list(STEP_IDS),
        "attempted": True,
        "transport_reached": True,
        "succeeded": True,
        "status": "ACCEPTED",
        "status_code": 200,
        "source_receipt_ids": source_ids,
        "step_cleanup_execution_receipts_by_id": nodes,
    }


def _aggregate_verification(nodes: dict[str, dict]) -> dict:
    receipt_ids = [nodes[step_id]["receipt_id"] for step_id in STEP_IDS]
    return {
        "schema_version": (
            "qualibug.process-graph-cleanup-equivalence-receipt.v1"
        ),
        "receipt_id": "graph-cleanup-verification-set-1",
        "write_step_ids": list(STEP_IDS),
        "equivalence_status": "EQUIVALENT",
        "reason_code": "",
        "step_equivalence_receipts_by_id": nodes,
        "step_equivalence_receipt_ids": receipt_ids,
        "equivalent_step_count": 2,
        "not_equivalent_step_count": 0,
        "indeterminate_step_count": 0,
    }


def _sealed_materials(*, invalid_aggregate: bool = False):
    execution_nodes = {
        step_id: _node_execution(step_id) for step_id in STEP_IDS
    }
    verification_nodes = {
        step_id: _node_verification(step_id) for step_id in STEP_IDS
    }
    aggregate_execution = _aggregate_execution(execution_nodes)
    if invalid_aggregate:
        aggregate_execution = deepcopy(aggregate_execution)
        aggregate_execution["status"] = "BLOCKED"
        aggregate_execution["succeeded"] = False
        aggregate_execution["status_code"] = 0
    aggregate_verification = _aggregate_verification(verification_nodes)

    observations = {
        "observer_receipts": [
            {
                "receipt_id": f"observation-{step_id}",
                "step_id": step_id,
                "target_reached": True,
            }
            for step_id in STEP_IDS
        ],
        "oracle_invocation_receipts": [
            {
                "receipt_id": f"oracle-{step_id}",
                "step_id": step_id,
                "evaluated": True,
            }
            for step_id in STEP_IDS
        ],
        "cleanup_execution_receipts": [aggregate_execution],
        "cleanup_verification_receipts": [aggregate_verification],
        "process_graph_step_cleanup_execution_receipts": list(
            execution_nodes.values()
        ),
        "process_graph_step_cleanup_verification_receipts": list(
            verification_nodes.values()
        ),
    }
    step_rows = ProcessStepSemanticView(_ledger(), observations).all_rows()
    return observations, step_rows


def _bundle(*, invalid_aggregate: bool = False) -> dict:
    observations, step_rows = _sealed_materials(
        invalid_aggregate=invalid_aggregate
    )
    cleanup_execution_rows = list(observations["cleanup_execution_receipts"])
    cleanup_verification_rows = list(
        observations["cleanup_verification_receipts"]
    )
    receipts = [
        _envelope("qualibug.compile-receipt.v1", "compile-1", {}),
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
                f"observation-{step_id}",
                {"step_id": step_id},
            )
            for step_id in STEP_IDS
        ],
        *[
            _envelope(
                "qualibug.oracle-invocation-receipt.v1",
                f"oracle-{step_id}",
                {"step_id": step_id},
            )
            for step_id in STEP_IDS
        ],
        *[
            _envelope(
                "qualibug.cleanup-execution-receipt.v1",
                row["receipt_id"],
                row,
            )
            for row in cleanup_execution_rows
        ],
        *[
            _envelope(
                "qualibug.cleanup-verification-receipt.v1",
                row["receipt_id"],
                row,
            )
            for row in cleanup_verification_rows
        ],
        _envelope(
            "qualibug.environment-restoration-receipt.v1",
            "environment-1",
            {},
        ),
    ]
    return build_execution_receipt_bundle(
        bundle_id="bundle-graph-cleanup",
        receipts=receipts,
        compile_receipt_id="compile-1",
        fixture_provenance_receipt_ids=["fixture-receipt-1"],
        required_step_receipt_ids=[row["receipt_id"] for row in step_rows],
        transport_receipt_ids=["transport-1"],
        observation_receipt_ids=[
            f"observation-{step_id}" for step_id in STEP_IDS
        ],
        oracle_invocation_receipt_ids=[
            f"oracle-{step_id}" for step_id in STEP_IDS
        ],
        cleanup_execution_receipt_ids=[
            row["receipt_id"] for row in cleanup_execution_rows
        ],
        cleanup_verification_receipt_ids=[
            row["receipt_id"] for row in cleanup_verification_rows
        ],
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


def test_valid_graph_bundle_carries_aggregate_and_exact_cleanup_layers() -> None:
    bundle = _bundle()
    scope = bundle["process_step_audit"]["evidence_scope_audit"]

    assert bundle["complete"] is True
    assert scope["aggregate_cleanup_execution_receipt_ids"] == [
        "graph-cleanup-execution-set-1"
    ]
    assert scope["aggregate_cleanup_verification_receipt_ids"] == [
        "graph-cleanup-verification-set-1"
    ]
    assert scope["cleanup_execution"]["discovered_receipt_ids"] == [
        "cleanup-execution-write-1",
        "cleanup-execution-write-2",
    ]
    assert scope["cleanup_verification"]["discovered_receipt_ids"] == [
        "cleanup-verification-write-1",
        "cleanup-verification-write-2",
    ]
    assert scope["broadcast_receipt_ids"] == []
    assert scope["unbound_receipt_ids"] == []
    assert scope["aggregate_cleanup_receipts_excluded_from_step_scope"] is True
    assert _lifecycle(bundle)["lifecycle_state"] == "TRUE_COMPLETED"


def test_invalid_graph_aggregate_blocks_even_when_nodes_are_valid() -> None:
    bundle = _bundle(invalid_aggregate=True)
    scope = bundle["process_step_audit"]["evidence_scope_audit"]

    assert scope["cleanup_execution"]["complete"] is True
    assert scope["cleanup_verification"]["complete"] is True
    assert scope["invalid_aggregate_cleanup_execution_receipt_ids"] == [
        "graph-cleanup-execution-set-1"
    ]
    assert "graph-cleanup-execution-set-1" in scope[
        "invalid_semantic_status_receipt_ids"
    ]
    assert bundle["complete"] is False
    assert "process_step_evidence_status_invalid" in bundle["validation_errors"]
    assert _lifecycle(bundle)["true_completed"] is False


def test_missing_exact_node_envelope_is_detected_despite_valid_aggregate() -> None:
    bundle = _bundle()
    removed_id = "cleanup-execution-write-2"
    bundle["receipts"] = [
        row for row in bundle["receipts"] if row["receipt_id"] != removed_id
    ]
    rebuilt = build_execution_receipt_bundle(
        bundle_id=bundle["bundle_id"],
        receipts=bundle["receipts"],
        compile_receipt_id=bundle["compile_receipt_id"],
        fixture_provenance_receipt_ids=bundle[
            "fixture_provenance_receipt_ids"
        ],
        required_step_receipt_ids=bundle["required_step_receipt_ids"],
        transport_receipt_ids=bundle["transport_receipt_ids"],
        observation_receipt_ids=bundle["observation_receipt_ids"],
        oracle_invocation_receipt_ids=bundle[
            "oracle_invocation_receipt_ids"
        ],
        oracle_trace_receipt_ids=bundle["oracle_trace_receipt_ids"],
        cleanup_execution_receipt_ids=bundle[
            "cleanup_execution_receipt_ids"
        ],
        cleanup_verification_receipt_ids=bundle[
            "cleanup_verification_receipt_ids"
        ],
        environment_restoration_receipt_id=bundle[
            "environment_restoration_receipt_id"
        ],
        **IDENTITY,
    )

    scope = rebuilt["process_step_audit"]["evidence_scope_audit"]
    assert rebuilt["complete"] is False
    assert removed_id in rebuilt["missing_receipt_ids"]
    assert removed_id in scope["cleanup_unknown_reference_ids"]
    assert scope["missing_cleanup_execution_step_ids"] == ["write-2"]
