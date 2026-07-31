from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center import process_graph_cleanup_equivalence as cleanup_eq
from ai_test_asset_center.process_graph_reversibility import (
    GRAPH_REVERSIBILITY_SCHEMA,
)


def _node_proof() -> dict:
    return {
        "schema_version": "qualibug.write-reversibility-proof.v1",
        "proof_id": "proof_write_a",
        "fingerprint": "proof_fp_write_a",
        "proof_status": "PROVEN",
    }


def _graph_proof() -> dict:
    node_proof = _node_proof()
    return {
        "schema_version": GRAPH_REVERSIBILITY_SCHEMA,
        "proof_id": "graph_proof_1",
        "proof_status": "PROVEN",
        "proof_kind": "process_graph_per_source_step",
        "process_graph_write_contract_id": "write_contract_1",
        "write_step_ids": ["write_a"],
        "cleanup_order": ["write_a"],
        "step_proofs_by_id": {"write_a": node_proof},
        "step_proof_fingerprints_by_id": {
            "write_a": node_proof["fingerprint"]
        },
    }


def _graph_cleanup_receipt(
    *,
    status: str = "NOT_REQUIRED",
    reason_code: str = cleanup_eq.GRAPH_SOURCE_WRITE_NOT_REACHED,
    request_reached_transport: bool = False,
) -> dict:
    return {
        "receipt_id": "graph_cleanup_receipt_a",
        "status": status,
        "evidence": {
            "source_step_id": "write_a",
            "reason_code": reason_code,
            "request_reached_transport": request_reached_transport,
            "effectful_write_count": 0,
            "cleanup_write_count": 0,
        },
    }


def _node_cleanup_receipt(*, status: str = "NOT_REQUIRED") -> dict:
    return {
        "schema_version": "qualibug.cleanup-execution-receipt.v1",
        "receipt_id": "cleanup_execution_receipt_a",
        "source_step_id": "write_a",
        "status": status,
        "attempted": status == "ACCEPTED",
        "transport_reached": status == "ACCEPTED",
        "succeeded": status == "ACCEPTED",
        "status_code": 200 if status == "ACCEPTED" else 0,
    }


def _execution_set(
    *,
    graph_receipt: dict | None = None,
    node_receipt: dict | None = None,
    source_step_identity_valid: bool = False,
    rollback_outcome: str = "NOT_REQUIRED",
) -> dict:
    graph_receipt = deepcopy(
        graph_receipt if graph_receipt is not None else _graph_cleanup_receipt()
    )
    node_receipt = deepcopy(
        node_receipt if node_receipt is not None else _node_cleanup_receipt()
    )
    row = {
        "source_step_id": "write_a",
        "system_ref": "system_1",
        "proof": _node_proof(),
        "before_observation": {},
        "after_write_observation": {},
        "after_cleanup_observation": {},
        "runtime_bindings": {},
        "cleanup_execution_receipt": deepcopy(node_receipt),
        "source_step_identity_valid": source_step_identity_valid,
        "cleanup_step_identity_valid": True,
        "graph_cleanup_receipt": deepcopy(graph_receipt),
        "graph_cleanup_receipt_identity_valid": True,
        "rollback_outcome": rollback_outcome,
        "cleanup_execution_receipt_identity_valid": True,
    }
    execution = {
        "schema_version": cleanup_eq.GRAPH_CLEANUP_EXECUTION_SET_SCHEMA,
        "receipt_id": "cleanup_execution_set_1",
        "proof_id": "graph_proof_1",
        "process_graph_write_contract_id": "write_contract_1",
        "write_step_ids": ["write_a"],
        "cleanup_order": ["write_a"],
        "source_receipt_ids": [node_receipt["receipt_id"]],
        "status": "ACCEPTED",
        "step_inputs_by_id": {"write_a": row},
        "step_cleanup_execution_receipts_by_id": {
            "write_a": deepcopy(node_receipt)
        },
        "environment_restoration_receipt": {
            "schema_version": "qualibug.environment-restoration-receipt.v1",
            "receipt_id": "environment_receipt_1",
            "environment_restored": False,
            "final_status": "PENDING_EQUIVALENCE",
            "baseline_comparison": {
                "relevant_tables_match": False,
                "relevant_fields_match": False,
            },
        },
    }
    execution["scope_fingerprint"] = (
        cleanup_eq.build_process_graph_cleanup_scope_fingerprint(execution)
    )
    return execution


def test_zero_transport_write_is_not_applicable_and_environment_restored() -> None:
    execution = _execution_set()

    receipt = cleanup_eq.evaluate_process_graph_cleanup_equivalence(
        proof=_graph_proof(),
        cleanup_execution_receipt=execution,
    )

    assert receipt["equivalence_status"] == "EQUIVALENT"
    step_receipt = receipt["step_equivalence_receipts_by_id"]["write_a"]
    assert step_receipt["equivalence_status"] == "NOT_APPLICABLE"
    assert step_receipt["reason_code"] == (
        cleanup_eq.GRAPH_SOURCE_WRITE_NOT_REACHED
    )
    assert step_receipt["transport_proven_absent"] is True
    assert execution["environment_restoration_receipt"][
        "environment_restored"
    ] is True
    assert execution["environment_restoration_receipt"]["final_status"] == (
        "ENVIRONMENT_RESTORED"
    )


def test_fake_not_required_without_zero_transport_reason_stays_indeterminate() -> None:
    execution = _execution_set(
        graph_receipt=_graph_cleanup_receipt(
            reason_code="SOURCE_WRITE_NO_OBSERVED_EFFECT"
        )
    )

    receipt = cleanup_eq.evaluate_process_graph_cleanup_equivalence(
        proof=_graph_proof(),
        cleanup_execution_receipt=execution,
    )

    assert receipt["equivalence_status"] == "INDETERMINATE"
    assert execution["environment_restoration_receipt"][
        "environment_restored"
    ] is False


def test_tampered_step_input_fails_scope_fingerprint_closed() -> None:
    execution = _execution_set()
    execution["step_inputs_by_id"]["write_a"][
        "rollback_outcome"
    ] = "COMPLETED"

    receipt = cleanup_eq.evaluate_process_graph_cleanup_equivalence(
        proof=_graph_proof(),
        cleanup_execution_receipt=execution,
    )

    assert receipt["equivalence_status"] == "INDETERMINATE"
    assert cleanup_eq.GRAPH_EQUIVALENCE_SCOPE_INVALID in receipt["detail"]
    assert (
        "rollback_outcome_mismatch" in receipt["detail"]
        or "scope_fingerprint_mismatch" in receipt["detail"]
    )


def test_tampered_node_cleanup_receipt_fails_closed() -> None:
    execution = _execution_set()
    execution["step_cleanup_execution_receipts_by_id"]["write_a"][
        "status"
    ] = "ACCEPTED"

    receipt = cleanup_eq.evaluate_process_graph_cleanup_equivalence(
        proof=_graph_proof(),
        cleanup_execution_receipt=execution,
    )

    assert receipt["equivalence_status"] == "INDETERMINATE"
    assert cleanup_eq.GRAPH_EQUIVALENCE_SCOPE_INVALID in receipt["detail"]


def test_completed_cleanup_delegates_to_existing_core(monkeypatch) -> None:
    graph_receipt = _graph_cleanup_receipt(
        status="COMPLETED",
        reason_code="",
        request_reached_transport=True,
    )
    node_receipt = _node_cleanup_receipt(status="ACCEPTED")
    execution = _execution_set(
        graph_receipt=graph_receipt,
        node_receipt=node_receipt,
        source_step_identity_valid=True,
        rollback_outcome="COMPLETED",
    )
    expected = {
        "schema_version": cleanup_eq.GRAPH_CLEANUP_EQUIVALENCE_SCHEMA,
        "receipt_id": "core_equivalence_receipt",
        "proof_id": "graph_proof_1",
        "process_graph_write_contract_id": "write_contract_1",
        "cleanup_execution_receipt_id": "cleanup_execution_set_1",
        "write_step_ids": ["write_a"],
        "cleanup_order": ["write_a"],
        "equivalence_status": "EQUIVALENT",
        "reason_code": "",
        "detail": "",
        "step_equivalence_receipts_by_id": {
            "write_a": {
                "receipt_id": "step_equivalence_a",
                "equivalence_status": "EQUIVALENT",
            }
        },
        "step_equivalence_receipt_ids": ["step_equivalence_a"],
        "equivalent_step_count": 1,
        "not_equivalent_step_count": 0,
        "indeterminate_step_count": 0,
        "fingerprint": "core_fp",
    }
    monkeypatch.setattr(
        cleanup_eq._core,
        "evaluate_process_graph_cleanup_equivalence",
        lambda **_: deepcopy(expected),
    )

    receipt = cleanup_eq.evaluate_process_graph_cleanup_equivalence(
        proof=_graph_proof(),
        cleanup_execution_receipt=execution,
    )

    assert receipt == expected


def test_public_finalizer_seals_exact_graph_receipt_and_rollback_scope(
    monkeypatch,
) -> None:
    node_receipt = _node_cleanup_receipt()
    old_execution = {
        "schema_version": cleanup_eq.GRAPH_CLEANUP_EXECUTION_SET_SCHEMA,
        "receipt_id": "cleanup_execution_set_1",
        "proof_id": "graph_proof_1",
        "process_graph_write_contract_id": "write_contract_1",
        "write_step_ids": ["write_a"],
        "cleanup_order": ["write_a"],
        "source_receipt_ids": [node_receipt["receipt_id"]],
        "status": "ACCEPTED",
        "step_inputs_by_id": {
            "write_a": {
                "source_step_id": "write_a",
                "system_ref": "system_1",
                "proof": _node_proof(),
                "before_observation": {},
                "after_write_observation": {},
                "after_cleanup_observation": {},
                "runtime_bindings": {},
                "cleanup_execution_receipt": deepcopy(node_receipt),
                "source_step_identity_valid": False,
                "cleanup_step_identity_valid": True,
            }
        },
        "step_cleanup_execution_receipts_by_id": {
            "write_a": deepcopy(node_receipt)
        },
        "environment_restoration_receipt": {
            "receipt_id": "environment_receipt_1",
            "environment_restored": False,
        },
    }
    output = {
        "observations": {
            "cleanup_execution_receipt": old_execution,
            "process_graph_cleanup_receipts": [
                _graph_cleanup_receipt()
            ],
            "process_graph_rollback_outcomes": {
                "write_a": "NOT_REQUIRED"
            },
        }
    }
    monkeypatch.setattr(
        cleanup_eq._core,
        "finalize_process_graph_cleanup_equivalence_inputs",
        lambda **_: deepcopy(output),
    )

    finalized = cleanup_eq.finalize_process_graph_cleanup_equivalence_inputs(
        exp={},
        result={},
        resolved_campaign_id="campaign_1",
        runtime_bindings={},
    )
    execution = finalized["observations"]["cleanup_execution_receipt"]
    row = execution["step_inputs_by_id"]["write_a"]

    assert row["graph_cleanup_receipt"]["receipt_id"] == (
        "graph_cleanup_receipt_a"
    )
    assert row["graph_cleanup_receipt_identity_valid"] is True
    assert row["cleanup_execution_receipt_identity_valid"] is True
    assert row["rollback_outcome"] == "NOT_REQUIRED"
    assert execution["scope_fingerprint"] == (
        cleanup_eq.build_process_graph_cleanup_scope_fingerprint(execution)
    )
    assert execution["environment_restoration_receipt"][
        "cleanup_execution_scope_fingerprint"
    ] == execution["scope_fingerprint"]
