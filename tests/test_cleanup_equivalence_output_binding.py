from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center import cleanup_equivalence as cleanup_authority
from ai_test_asset_center.process_graph_cleanup_equivalence_core import (
    GRAPH_CLEANUP_EQUIVALENCE_SCHEMA,
    GRAPH_CLEANUP_EXECUTION_SET_SCHEMA,
)
from ai_test_asset_center.process_graph_reversibility import (
    GRAPH_REVERSIBILITY_SCHEMA,
)


def _proof() -> dict:
    return {
        "schema_version": GRAPH_REVERSIBILITY_SCHEMA,
        "proof_id": "graph_proof_1",
        "process_graph_write_contract_id": "write_contract_1",
        "write_step_ids": ["write_a", "write_b"],
    }


def _execution_set() -> dict:
    return {
        "schema_version": GRAPH_CLEANUP_EXECUTION_SET_SCHEMA,
        "receipt_id": "cleanup_set_1",
        "proof_id": "graph_proof_1",
        "process_graph_write_contract_id": "write_contract_1",
        "write_step_ids": ["write_a", "write_b"],
        "scope_fingerprint": "scope_fp_1",
        "environment_restoration_receipt": {
            "receipt_id": "environment_1",
            "environment_restored": True,
            "final_status": "ENVIRONMENT_RESTORED",
        },
    }


def _equivalence_receipt(*, contract_id: str = "write_contract_1") -> dict:
    return {
        "schema_version": GRAPH_CLEANUP_EQUIVALENCE_SCHEMA,
        "receipt_id": "equivalence_set_1",
        "fingerprint": "equivalence_fp_1",
        "proof_id": "graph_proof_1",
        "process_graph_write_contract_id": contract_id,
        "cleanup_execution_receipt_id": "cleanup_set_1",
        "write_step_ids": ["write_a", "write_b"],
        "equivalence_status": "EQUIVALENT",
        "step_equivalence_receipts_by_id": {
            "write_a": {
                "schema_version": "qualibug.cleanup-equivalence-receipt.v1",
                "receipt_id": "equivalence_a",
                "source_step_id": "write_a",
                "equivalence_status": "EQUIVALENT",
            },
            "write_b": {
                "schema_version": "qualibug.cleanup-equivalence-receipt.v1",
                "receipt_id": "equivalence_b",
                "source_step_id": "write_b",
                "equivalence_status": "NOT_APPLICABLE",
            },
        },
    }


def _evaluate(monkeypatch, execution: dict, receipt: dict) -> dict:
    monkeypatch.setattr(
        cleanup_authority,
        "evaluate_process_graph_cleanup_equivalence",
        lambda **_: deepcopy(receipt),
    )
    return cleanup_authority.evaluate_cleanup_equivalence(
        proof=_proof(),
        before_observation={},
        after_write_observation={},
        after_cleanup_observation={},
        runtime_bindings={},
        cleanup_execution_receipt=execution,
    )


def test_graph_verification_outputs_bind_to_same_execution_set(monkeypatch) -> None:
    execution = _execution_set()
    receipt = _equivalence_receipt()

    result = _evaluate(monkeypatch, execution, receipt)

    assert result == receipt
    assert execution["cleanup_equivalence_receipt_id"] == "equivalence_set_1"
    assert execution["cleanup_equivalence_fingerprint"] == "equivalence_fp_1"
    assert execution["step_cleanup_verification_receipt_ids_by_id"] == {
        "write_a": "equivalence_a",
        "write_b": "equivalence_b",
    }
    assert set(execution["step_cleanup_verification_receipts_by_id"]) == {
        "write_a",
        "write_b",
    }
    assert execution["cleanup_verification_fingerprint"]
    environment = execution["environment_restoration_receipt"]
    assert environment["aggregate_cleanup_equivalence_receipt_id"] == (
        "equivalence_set_1"
    )
    assert environment["cleanup_verification_fingerprint"] == execution[
        "cleanup_verification_fingerprint"
    ]
    assert execution["environment_restored"] is True


def test_mismatched_equivalence_identity_is_not_bound(monkeypatch) -> None:
    execution = _execution_set()
    receipt = _equivalence_receipt(contract_id="other_contract")

    result = _evaluate(monkeypatch, execution, receipt)

    assert result == receipt
    assert "cleanup_equivalence_receipt_id" not in execution
    assert "step_cleanup_verification_receipts_by_id" not in execution
    assert "cleanup_verification_fingerprint" not in execution
    environment = execution["environment_restoration_receipt"]
    assert "aggregate_cleanup_equivalence_receipt_id" not in environment
