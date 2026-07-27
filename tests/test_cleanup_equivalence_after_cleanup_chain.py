"""Root-cause coverage: after-cleanup observation must drive equivalence.

CLEANUP_EQUIVALENCE_INDETERMINATE was caused by:
1) final_state using write-phase after (cleanup_phase_excluded=True) as after-cleanup
2) db_sql adapter cleanup omitting phase=cleanup rows / cleanup execution success
3) missing sealed after-cleanup readback for equivalence
"""
from __future__ import annotations

from ai_test_asset_center.cleanup_equivalence import evaluate_cleanup_equivalence
from ai_test_asset_center.cleanup_execution_receipt import build_cleanup_execution_receipt
from ai_test_asset_center.cleanup_observation_adapter import (
    build_cleanup_equivalence_inputs,
)
from ai_test_asset_center.observer_contracts_base import observe_experiment_requirements


def _proof(*, mode: str = "created_entity_absent") -> dict:
    return {
        "proof_id": "wrp_test_1",
        "proof_status": "PROVEN",
        "equivalence_contract": {"mode": mode, "compared_fields": ["qty"]},
        "identity_contract": {"identity_fields": ["id"]},
        "cleanup_authority": {"mode": "declared_adapter_cleanup"},
    }


def test_adapter_skips_write_phase_final_state_as_after_cleanup() -> None:
    inputs = build_cleanup_equivalence_inputs(
        exp={},
        observations={
            "final_state_observer_receipt": {
                "status": "OBSERVED",
                "receipt_id": "obs_final_write",
                "evidence": {
                    "cleanup_phase_excluded": True,
                    "status_code": 200,
                    "body": {"id": "x1", "qty": 9},
                    "final_state": "ACTIVE",
                },
            },
            "after_cleanup_observation": {
                "status_code": 404,
                "body": {"error": "not_found"},
                "path": "/entities/x1",
            },
        },
        steps_out=[],
        cleanup_result={},
    )
    assert inputs["source_trace"]["after_cleanup_source"] == (
        "observations_after_cleanup_observation"
    )
    assert inputs["after_cleanup_observation"]["status_code"] == 404


def test_adapter_prefers_cleanup_step_governance_over_write_final_state() -> None:
    inputs = build_cleanup_equivalence_inputs(
        exp={},
        observations={
            "final_state_observer_receipt": {
                "status": "OBSERVED",
                "evidence": {
                    "cleanup_phase_excluded": True,
                    "status_code": 200,
                    "body": {"id": "x1"},
                },
            },
        },
        steps_out=[
            {
                "phase": "cleanup",
                "governance_receipt": {
                    "accepted": True,
                    "observation_path": "/entities/x1",
                    "after": {"status": 404, "body": {"error": "not_found"}},
                },
            }
        ],
        cleanup_result={},
    )
    assert inputs["source_trace"]["after_cleanup_source"] == (
        "cleanup_step_governance_after"
    )
    assert inputs["after_cleanup_observation"]["status_code"] == 404


def test_cleanup_execution_receipt_accepts_adapter_cleaned_without_http_steps() -> None:
    receipt = build_cleanup_execution_receipt(
        experiment_id="exp_1",
        proof_id="wrp_1",
        cleanup_plan=[{"adapter": "db_sql", "table": "orders", "identity_column": "id"}],
        steps_out=[],
        cleanup_failures=0,
        cleanup_status="cleaned",
        proof=_proof(),
        adapter_cleanup_receipts=[
            {
                "status": "CLEANED",
                "adapter": "db_sql",
                "table": "orders",
                "identity_column": "id",
                "identity_value": "o-1",
                "rows_deleted": 1,
            }
        ],
    )
    assert receipt["succeeded"] is True
    assert receipt["attempted"] is True
    assert receipt["status"] == "ACCEPTED"


def test_equivalence_equivalent_when_create_deleted_and_receipt_succeeded() -> None:
    receipt = evaluate_cleanup_equivalence(
        proof=_proof(mode="created_entity_absent"),
        before_observation={"status_code": 404, "body": {}},
        after_write_observation={"status_code": 200, "body": {"id": "o-1"}},
        after_cleanup_observation={"status_code": 404, "body": {"error": "not_found"}},
        runtime_bindings={"id": "o-1"},
        cleanup_execution_receipt={
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "succeeded": True,
            "attempted": True,
            "status_code": 200,
        },
    )
    assert receipt["equivalence_status"] == "EQUIVALENT"


def test_final_state_uses_cleanup_phase_when_present() -> None:
    receipts = observe_experiment_requirements(
        {
            "assertions": [{"kind": "state_transition"}],
            "observers": [{"observer_id": "final_state"}],
        },
        observations={
            "execution_steps": [
                {
                    "phase": "treatment",
                    "step_id": "treatment_1",
                    "method": "POST",
                    "path": "/orders",
                    "status_code": 201,
                    "body": {"id": "o-1"},
                    "governance_receipt": {
                        "accepted": True,
                        "before": {
                            "status": 200,
                            "body": [{"id": "o-1", "status": "PENDING"}],
                        },
                        "write": {"status": 201, "body": {"id": "o-1"}},
                        "after": {
                            "status": 200,
                            "body": [{"id": "o-1", "status": "PAID"}],
                        },
                    },
                },
                {
                    "phase": "cleanup",
                    "step_id": "cleanup_1",
                    "method": "PUT",
                    "path": "/orders/o-1",
                    "status_code": 200,
                    "governance_receipt": {
                        "accepted": True,
                        "after": {
                            "status": 200,
                            "body": [{"id": "o-1", "status": "PENDING"}],
                        },
                    },
                },
            ],
        },
    )
    assert receipts[0]["status"] == "OBSERVED"
    assert receipts[0]["evidence"]["cleanup_phase_excluded"] is False
    assert receipts[0]["evidence"]["final_state"] == "PENDING"


def test_final_state_without_cleanup_keeps_write_snapshot_contract() -> None:
    receipts = observe_experiment_requirements(
        {
            "assertions": [{"kind": "state_transition"}],
            "observers": [{"observer_id": "final_state"}],
        },
        observations={
            "execution_steps": [
                {
                    "phase": "treatment",
                    "step_id": "treatment_1",
                    "method": "POST",
                    "path": "/orders",
                    "status_code": 201,
                    "body": {"id": "o-1"},
                    "governance_receipt": {
                        "accepted": True,
                        "before": {
                            "status": 200,
                            "body": [{"id": "o-1", "status": "PENDING"}],
                        },
                        "write": {"status": 201, "body": {"id": "o-1"}},
                        "after": {
                            "status": 200,
                            "body": [{"id": "o-1", "status": "PAID"}],
                        },
                    },
                }
            ],
        },
    )
    assert receipts[0]["status"] == "OBSERVED"
    assert receipts[0]["evidence"]["cleanup_phase_excluded"] is True
    assert receipts[0]["evidence"]["final_state"] == "PAID"


def test_seal_records_visible_block_when_write_step_missing() -> None:
    from ai_test_asset_center.experiment_cleanup_executor import (
        seal_after_cleanup_observation,
    )
    from pathlib import Path

    observations: dict = {}
    sealed = seal_after_cleanup_observation(
        steps_out=[],
        observations=observations,
        actors={},
        tokens={},
        base_url="http://127.0.0.1:8080",
        root=Path("."),
        project="benchmark_mall_131",
        runtime_contract={"status": "approved", "approved_base_url": "http://127.0.0.1:8080"},
    )
    assert sealed == {}
    assert observations["after_cleanup_observation_seal"]["reason_code"] == (
        "AFTER_CLEANUP_WRITE_STEP_MISSING"
    )


def test_adapter_runtime_step_accepts_lowercase_cleaned_status() -> None:
    from ai_test_asset_center.experiment_cleanup_executor import (
        _append_adapter_cleanup_runtime_step,
    )

    steps: list[dict] = []
    _append_adapter_cleanup_runtime_step(
        steps_out=steps,
        cleanup_subject_id="cleanup_1",
        adapter_receipt={
            "status": "cleaned",
            "table": "orders",
            "rows_deleted": 1,
        },
        after_cleanup_obs={
            "status_code": 404,
            "body": {"error": "not_found"},
            "path": "/orders/1",
        },
    )
    assert steps[0]["governance_receipt"]["accepted"] is True
    assert steps[0]["status_code"] == 200
