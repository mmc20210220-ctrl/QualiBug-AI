"""Root-cause coverage: after-cleanup observation must drive equivalence.

CLEANUP_EQUIVALENCE_INDETERMINATE was caused by:
1) final_state using write-phase after (cleanup_phase_excluded=True) as after-cleanup
2) db_sql adapter cleanup omitting phase=cleanup rows / cleanup execution success
3) missing sealed after-cleanup readback for equivalence
"""
from __future__ import annotations

from ai_test_asset_center.cleanup_equivalence import evaluate_cleanup_equivalence
from ai_test_asset_center.experiment_outcome_finalizer import (
    _cleanup_equivalence_gate,
    _requires_cleanup_equivalence,
)
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
                "receipt_id": "cleanup_adapter_1",
                "status": "CLEANED",
                "adapter": "db_sql",
                "table": "orders",
                "identity_column": "id",
                "identity_value": "o-1",
                "mode": "row_delete",
                "ownership_basis": "creation_receipt",
                "rows_deleted": 1,
            }
        ],
    )
    assert receipt["succeeded"] is True
    assert receipt["attempted"] is True
    assert receipt["status"] == "ACCEPTED"
    assert receipt["source_receipt_ids"] == ["cleanup_adapter_1"]


def test_cleanup_execution_receipt_rejects_unidentified_adapter_success() -> None:
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
                "mode": "row_delete",
                "ownership_basis": "creation_receipt",
                "rows_deleted": 1,
            }
        ],
    )

    assert receipt["succeeded"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] == "ADAPTER_CLEANUP_RECEIPT_ID_MISSING"


def test_cleanup_execution_receipt_rejects_zero_effect_adapter_success() -> None:
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
                "receipt_id": "cleanup_adapter_1",
                "status": "CLEANED",
                "adapter": "db_sql",
                "table": "orders",
                "identity_column": "id",
                "identity_value": "o-1",
                "mode": "row_delete",
                "ownership_basis": "creation_receipt",
                "rows_deleted": 0,
            }
        ],
    )

    assert receipt["succeeded"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] == "ADAPTER_CLEANUP_CARDINALITY_INVALID"


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


def test_cer_not_required_when_cleanup_status_not_required() -> None:
    receipt = build_cleanup_execution_receipt(
        experiment_id="exp_1",
        proof_id="wrp_1",
        cleanup_plan=[{"adapter": "db_sql", "table": "orders", "identity_column": "id"}],
        steps_out=[],
        cleanup_failures=0,
        cleanup_status="not_required",
        proof=_proof(),
        adapter_cleanup_receipts=[],
    )
    assert receipt["status"] == "NOT_REQUIRED"
    assert receipt["attempted"] is False
    assert receipt["succeeded"] is False
    assert receipt["reason_code"] == "CLEANUP_NOT_REQUIRED"


def test_equivalence_not_applicable_for_honest_not_required_cer() -> None:
    receipt = evaluate_cleanup_equivalence(
        proof=_proof(mode="created_entity_absent"),
        before_observation={"status_code": 200, "body": {"id": "o-1", "status": "PENDING"}},
        after_write_observation={
            "status_code": 200,
            "body": {"id": "o-1", "status": "PENDING"},
        },
        after_cleanup_observation={},
        runtime_bindings={"id": "o-1"},
        cleanup_execution_receipt={
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "status": "NOT_REQUIRED",
            "succeeded": False,
            "attempted": False,
            "status_code": 0,
            "reason_code": "CLEANUP_NOT_REQUIRED",
        },
    )
    assert receipt["equivalence_status"] == "NOT_APPLICABLE"
    assert receipt["reason_code"] == "CLEANUP_NOT_REQUIRED"


def test_equivalence_rejects_malformed_not_required_receipt() -> None:
    receipt = evaluate_cleanup_equivalence(
        proof=_proof(mode="created_entity_absent"),
        before_observation={
            "status_code": 200,
            "body": {"id": "o-1", "status": "PENDING"},
        },
        after_write_observation={
            "status_code": 200,
            "body": {"id": "o-1", "status": "PENDING"},
        },
        after_cleanup_observation={},
        runtime_bindings={"id": "o-1"},
        cleanup_execution_receipt={"status": "NOT_REQUIRED"},
    )

    assert receipt["equivalence_status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "CLEANUP_EXECUTION_RECEIPT_SCHEMA_INVALID"


def test_false_not_required_without_sealed_unchanged_is_indeterminate() -> None:
    """CER NOT_REQUIRED must not waive Finalizer when before≠after is unproven."""
    receipt = evaluate_cleanup_equivalence(
        proof=_proof(mode="business_state_restored"),
        before_observation={"status_code": 200, "body": {"id": "o-1", "status": "SHIPPED"}},
        after_write_observation={
            "status_code": 200,
            "body": {"id": "o-1", "status": "COMPLETED"},
        },
        after_cleanup_observation={},
        runtime_bindings={"id": "o-1"},
        cleanup_execution_receipt={
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "status": "NOT_REQUIRED",
            "succeeded": False,
            "attempted": False,
            "status_code": 0,
            "reason_code": "CLEANUP_NOT_REQUIRED",
        },
    )
    assert receipt["equivalence_status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "CLEANUP_NOT_REQUIRED_UNPROVEN"


def test_false_not_required_detects_nested_business_state_change() -> None:
    receipt = evaluate_cleanup_equivalence(
        proof=_proof(mode="business_state_restored"),
        before_observation={
            "status_code": 200,
            "body": {
                "id": "o-1",
                "items": [{"sku": "A", "quantity": 1}],
            },
        },
        after_write_observation={
            "status_code": 200,
            "body": {
                "id": "o-1",
                "items": [{"sku": "A", "quantity": 2}],
            },
        },
        after_cleanup_observation={},
        runtime_bindings={"id": "o-1"},
        cleanup_execution_receipt={
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "status": "NOT_REQUIRED",
            "reason_code": "CLEANUP_NOT_REQUIRED",
        },
    )

    assert receipt["equivalence_status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "CLEANUP_NOT_REQUIRED_UNPROVEN"


def test_not_required_does_not_trust_self_asserted_equal_fingerprints() -> None:
    receipt = evaluate_cleanup_equivalence(
        proof=_proof(mode="business_state_restored"),
        before_observation={
            "status_code": 200,
            "body": {"id": "o-1", "status": "SHIPPED"},
        },
        after_write_observation={
            "status_code": 200,
            "body": {"id": "o-1", "status": "COMPLETED"},
        },
        after_cleanup_observation={},
        runtime_bindings={"id": "o-1"},
        cleanup_execution_receipt={
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "status": "NOT_REQUIRED",
            "reason_code": "CLEANUP_NOT_REQUIRED",
            "before_state_fingerprint": "self-asserted",
            "after_write_state_fingerprint": "self-asserted",
        },
    )

    assert receipt["equivalence_status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "CLEANUP_NOT_REQUIRED_UNPROVEN"


def test_not_required_treats_concurrency_version_as_business_state() -> None:
    receipt = evaluate_cleanup_equivalence(
        proof=_proof(mode="business_state_restored"),
        before_observation={
            "status_code": 200,
            "body": {"id": "o-1", "status": "SHIPPED", "version": 4},
        },
        after_write_observation={
            "status_code": 200,
            "body": {"id": "o-1", "status": "SHIPPED", "version": 5},
        },
        after_cleanup_observation={},
        runtime_bindings={"id": "o-1"},
        cleanup_execution_receipt={
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "status": "NOT_REQUIRED",
            "reason_code": "CLEANUP_NOT_REQUIRED",
        },
    )

    assert receipt["equivalence_status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "CLEANUP_NOT_REQUIRED_UNPROVEN"


def test_governed_write_rejects_unproven_not_applicable_equivalence() -> None:
    gate, reason = _cleanup_equivalence_gate(
        is_governed_write=True,
        cleanup_equivalence_receipt={
            "equivalence_status": "NOT_APPLICABLE",
            "reason_code": "NO_CLEANUP_PLAN",
        },
    )

    assert gate == "BLOCKED"
    assert reason == "BLOCKED_CLEANUP_EQUIVALENCE_NOT_APPLICABLE_UNPROVEN"


def test_executed_write_cannot_bypass_cleanup_when_safety_flag_is_missing() -> None:
    assert _requires_cleanup_equivalence(
        safety_contract={},
        steps_out=[
            {
                "phase": "treatment",
                "method": "POST",
                "status_code": 201,
                "governance_receipt": {"accepted": True},
            }
        ],
    )


def test_equivalence_still_indeterminate_when_cer_not_attempted() -> None:
    receipt = evaluate_cleanup_equivalence(
        proof=_proof(mode="created_entity_absent"),
        before_observation={"status_code": 200, "body": {"id": "o-1"}},
        after_write_observation={"status_code": 200, "body": {"id": "o-1"}},
        after_cleanup_observation={},
        runtime_bindings={"id": "o-1"},
        cleanup_execution_receipt={
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "status": "NOT_ATTEMPTED",
            "succeeded": False,
            "attempted": False,
            "status_code": 0,
        },
    )
    assert receipt["equivalence_status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "CLEANUP_EXECUTION_FAILED"


def test_equivalence_mode_missing_when_contract_empty() -> None:
    receipt = evaluate_cleanup_equivalence(
        proof={
            "proof_id": "wrp_empty_mode",
            "proof_status": "PROVEN",
            "equivalence_contract": {},
            "identity_contract": {"identity_fields": ["id"]},
        },
        before_observation={"status_code": 200, "body": {"id": "o-1", "status": "PENDING"}},
        after_write_observation={
            "status_code": 200,
            "body": {"id": "o-1", "status": "PAID"},
        },
        after_cleanup_observation={
            "status_code": 200,
            "body": {"id": "o-1", "status": "PENDING"},
        },
        runtime_bindings={"id": "o-1"},
        cleanup_execution_receipt={
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "succeeded": True,
            "attempted": True,
            "status_code": 200,
        },
    )
    assert receipt["equivalence_status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "EQUIVALENCE_MODE_MISSING"


def test_field_restore_business_state_equivalent_with_after_cleanup_proof() -> None:
    """Adapter field_restore must reach EQUIVALENT from real before/after-cleanup bodies.

    No waiver: after_cleanup observation is required and fields must actually match.
    """
    before = {
        "status_code": 200,
        "body": {
            "id": "o-1",
            "status": "PENDING_PAYMENT",
            "total_amount": "10.00",
            "updated_at": "t0",
        },
    }
    after_write = {
        "status_code": 200,
        "body": {
            "id": "o-1",
            "status": "PAID",
            "total_amount": "10.00",
            "updated_at": "t1",
        },
    }
    after_cleanup = {
        "status_code": 200,
        "body": {
            "id": "o-1",
            "status": "PENDING_PAYMENT",
            "total_amount": "10.00",
            "updated_at": "t2",
        },
    }
    receipt = evaluate_cleanup_equivalence(
        proof=_proof(mode="business_state_restored"),
        before_observation=before,
        after_write_observation=after_write,
        after_cleanup_observation=after_cleanup,
        runtime_bindings={"id": "o-1"},
        cleanup_execution_receipt={
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "succeeded": True,
            "attempted": True,
            "status_code": 200,
        },
    )
    assert receipt["equivalence_status"] == "EQUIVALENT"
    assert "status" in receipt["field_comparison"]["matched"]
    assert "updated_at" not in receipt["field_comparison"]["mismatched"]


def test_field_restore_not_equivalent_when_business_field_differs() -> None:
    receipt = evaluate_cleanup_equivalence(
        proof=_proof(mode="business_state_restored"),
        before_observation={
            "status_code": 200,
            "body": {"id": "o-1", "status": "PENDING_PAYMENT"},
        },
        after_write_observation={
            "status_code": 200,
            "body": {"id": "o-1", "status": "PAID"},
        },
        after_cleanup_observation={
            "status_code": 200,
            "body": {"id": "o-1", "status": "PAID"},
        },
        runtime_bindings={"id": "o-1"},
        cleanup_execution_receipt={
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "succeeded": True,
            "attempted": True,
            "status_code": 200,
        },
    )
    assert receipt["equivalence_status"] == "NOT_EQUIVALENT"
    assert receipt["reason_code"] == "BUSINESS_STATE_NOT_RESTORED"


def test_executed_field_restore_overrides_stale_created_entity_absent_mode() -> None:
    """Runtime field_restore must not be judged as created_entity_absent.

    Unlock regression: WRP stayed on created_entity_absent while adapter
    field_restore left the entity present → ENTITY_STILL_PRESENT_AFTER_CLEANUP.
    """
    before = {
        "status_code": 200,
        "body": {"id": "o-1", "status": "PENDING_PAYMENT", "total": "10"},
    }
    after_write = {
        "status_code": 200,
        "body": {"id": "o-1", "status": "SHIPPED", "total": "10"},
    }
    after_cleanup = {
        "status_code": 200,
        "body": {"id": "o-1", "status": "PENDING_PAYMENT", "total": "10"},
    }
    receipt = evaluate_cleanup_equivalence(
        proof=_proof(mode="created_entity_absent"),
        before_observation=before,
        after_write_observation=after_write,
        after_cleanup_observation=after_cleanup,
        runtime_bindings={"id": "o-1"},
        cleanup_execution_receipt={
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "succeeded": True,
            "attempted": True,
            "status_code": 200,
            "cleanup_mode": "field_restore",
            "mode": "field_restore",
        },
    )
    assert receipt["equivalence_status"] == "EQUIVALENT"
    assert receipt["reason_code"] != "ENTITY_STILL_PRESENT_AFTER_CLEANUP"


def test_executed_field_restore_failed_restore_is_business_state_not_entity_present() -> None:
    receipt = evaluate_cleanup_equivalence(
        proof=_proof(mode="created_entity_absent"),
        before_observation={
            "status_code": 200,
            "body": {"id": "o-1", "status": "PENDING_PAYMENT"},
        },
        after_write_observation={
            "status_code": 200,
            "body": {"id": "o-1", "status": "SHIPPED"},
        },
        after_cleanup_observation={
            "status_code": 200,
            "body": {"id": "o-1", "status": "SHIPPED"},
        },
        runtime_bindings={"id": "o-1"},
        cleanup_execution_receipt={
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "succeeded": True,
            "attempted": True,
            "status_code": 200,
            "cleanup_mode": "field_restore",
        },
    )
    assert receipt["equivalence_status"] == "NOT_EQUIVALENT"
    assert receipt["reason_code"] == "BUSINESS_STATE_NOT_RESTORED"


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


def test_post_cleanup_readback_http_is_counted_in_runtime_step(
    monkeypatch,
) -> None:
    """Gateway-correlated after-cleanup GET must enter operational receipts."""
    from ai_test_asset_center import sandbox_write_executor_base as sandbox
    from ai_test_asset_center.experiment_cleanup_executor import (
        seal_after_cleanup_observation,
    )
    from ai_test_asset_center.operational_receipts import (
        build_execution_operational_receipt,
    )

    monkeypatch.setattr(
        sandbox,
        "_http_request",
        lambda method, url, token="", body=None: {
            "status": 404,
            "body": {"error": "not_found"},
            "headers": {},
        },
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_cleanup_executor_core.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )

    steps: list[dict] = [
        {
            "phase": "treatment",
            "method": "POST",
            "path": "/orders",
            "observation_path": "/orders/1",
            "actor_ref": "buyer",
            "governance_receipt": {
                "accepted": True,
                "method": "POST",
                "path": "/orders",
                "observation_path": "/orders/1",
                "http_attempt_count": 3,
                "write_request_attempt_count": 1,
                "production_http_requests": 0,
                "before": {"status": 404},
                "write": {"status": 201, "body": {"id": "1"}},
                "after": {"status": 200, "body": {"id": "1"}},
            },
        }
    ]
    observations: dict = {}
    sealed = seal_after_cleanup_observation(
        steps_out=steps,
        observations=observations,
        actors={"buyer": {"role": "buyer"}},
        tokens={"buyer": "token"},
        base_url="http://localhost:8080",
        root=__import__("pathlib").Path("."),
        project="test_project",
        runtime_contract={"approved_base_url": "http://localhost:8080"},
    )
    assert observations.get("after_cleanup_observation_seal") in (None, {})
    assert sealed.get("source") == "post_cleanup_readback"
    readback_steps = [
        step
        for step in steps
        if step.get("phase") == "cleanup"
        and (step.get("governance_receipt") or {}).get("reason")
        == "post_cleanup_readback"
    ]
    assert len(readback_steps) == 1
    assert readback_steps[0]["governance_receipt"]["http_attempt_count"] == 1

    receipt = build_execution_operational_receipt(
        receipt_id="operational_test_readback",
        execution_status="EXECUTED",
        steps=steps,
        cleanup_failures=0,
    )
    assert receipt["http_request_attempt_count"] == 4
    assert receipt["write_request_attempt_count"] == 1


def test_requires_cleanup_equivalence_tolerates_blocked_write_status() -> None:
    """A barrier-intercepted write step (status="blocked_write") must not
    crash the finalizer and must not demand cleanup equivalence.

    Regression: `_requires_cleanup_equivalence` called int() on the step's
    `status` field, and a sandbox-barrier step carries the legal string
    "blocked_write" (the write never reached transport), crashing every
    experiment finalization that contained such a step.
    """
    # A blocked write (never sent) is not a 2xx write: no equivalence demand.
    steps = [
        {
            "phase": "treatment",
            "method": "POST",
            "status": "blocked_write",
            "status_code": None,
        }
    ]
    assert _requires_cleanup_equivalence(
        safety_contract={}, steps_out=steps
    ) is False

    # A genuine 2xx write still demands equivalence (unchanged semantics).
    steps = [
        {
            "phase": "treatment",
            "method": "POST",
            "status_code": 201,
            "status": "ok",
        }
    ]
    assert _requires_cleanup_equivalence(
        safety_contract={}, steps_out=steps
    ) is True

    # A non-2xx numeric status stays non-demanding.
    steps = [
        {
            "phase": "treatment",
            "method": "POST",
            "status_code": 400,
            "status": "client_error",
        }
    ]
    assert _requires_cleanup_equivalence(
        safety_contract={}, steps_out=steps
    ) is False


def test_adapter_cleanup_from_step_body_when_receipts_argument_empty() -> None:
    """Adapter DB-SQL cleanup steps carry their audit receipt in the step BODY,
    and the after-cleanup observation GET is also phase=cleanup. When the
    ``adapter_cleanup_receipts`` argument is empty (the executor did not populate
    it for this path), the receipt builder must fall back to the step-body
    adapter receipts instead of misreading the adapter write through the
    governed-write branch as ``CLEANUP_GOVERNANCE_AUDIT_RECEIPT_MISSING`` —
    which dropped proven VIOLATION findings as
    ``cleanup_transport_failed:status=200``.
    """
    receipt = build_cleanup_execution_receipt(
        experiment_id="exp_1",
        proof_id="wrp_1",
        cleanup_plan=[{"adapter": "db_sql", "table": "users", "identity_column": "id"}],
        steps_out=[
            # after-cleanup observation GET (phase=cleanup, no governance accept)
            {"phase": "cleanup", "method": "GET", "path": "/api/users/admin/export", "status_code": 200, "governance_receipt": {"accepted": False}},
            # adapter DB-SQL cleanup step: body carries the adapter receipt
            {
                "phase": "cleanup",
                "method": "ADAPTER_DB_SQL",
                "path": "/api/users/admin/export",
                "status_code": 200,
                "governance_receipt": {"accepted": True, "status": "executed", "reason": "adapter_cleanup_cleaned"},
                "body": {
                    "schema_version": "qualibug.cleanup-adapter-execution.v1",
                    "receipt_id": "cleanup_adapter_1",
                    "adapter": "db_sql",
                    "table": "users",
                    "identity_column": "id",
                    "identity_value": "u-1",
                    "status": "CLEANED",
                    "reason_code": "",
                    "rows_deleted": 1,
                    "mode": "row_delete",
                    "ownership_basis": "creation_receipt",
                },
            },
        ],
        cleanup_failures=0,
        cleanup_status="cleaned",
        proof=_proof(),
        adapter_cleanup_receipts=[],
    )
    assert receipt["succeeded"] is True
    assert receipt["status"] == "ACCEPTED"
    assert receipt["source_receipt_ids"] == ["cleanup_adapter_1"]


def test_adapter_row_delete_is_authoritative_when_collection_still_nonempty() -> None:
    """Adapter DB-SQL row_delete proved exactly one row removed. A collection
    read after cleanup is still non-empty (other rows remain), which the old
    created_entity_absent evaluator misread as ``created_entity_not_deleted``
    and dropped the finding. The adapter receipt is the authoritative proof."""
    cleanup_exec = {
        "schema_version": "qualibug.cleanup-execution-receipt.v1",
        "method": "ADAPTER_DB_SQL",
        "cleanup_mode": "row_delete",
        "mode": "row_delete",
        "succeeded": True,
        "status": "ACCEPTED",
        "status_code": 200,
    }
    receipt = evaluate_cleanup_equivalence(
        proof={"proof_id": "wrp_1", "equivalence_contract": {"mode": "created_entity_absent"}, "identity_contract": {"identity_fields": ["id"]}},
        before_observation={"status_code": 200, "body": [{"id": "other-1"}]},
        after_write_observation={"status_code": 200, "body": [{"id": "other-1"}, {"id": "created-id"}]},
        after_cleanup_observation={"status_code": 200, "body": [{"id": "other-1"}]},
        runtime_bindings={"id": "created-id"},
        cleanup_execution_receipt=cleanup_exec,
    )
    assert receipt["equivalence_status"] == "EQUIVALENT"
    assert receipt["detail"] == "adapter_cleanup_receipt_authoritative"


def test_adapter_row_delete_is_authoritative_when_route_not_declared() -> None:
    """Before/after are framework-level 404 (route not declared). The old
    created_entity_absent evaluator misread ``after_write`` 404 as
    ``write_did_not_create_entity_or_mode_mismatch``. The adapter receipt is
    authoritative regardless of HTTP observation shape."""
    cleanup_exec = {
        "schema_version": "qualibug.cleanup-execution-receipt.v1",
        "method": "ADAPTER_DB_SQL",
        "cleanup_mode": "row_delete",
        "mode": "row_delete",
        "succeeded": True,
        "status": "ACCEPTED",
        "status_code": 200,
    }
    receipt = evaluate_cleanup_equivalence(
        proof={"proof_id": "wrp_1", "equivalence_contract": {"mode": "created_entity_absent"}, "identity_contract": {"identity_fields": ["id"]}},
        before_observation={"status_code": 404, "body": {"_raw": "<!DOCTYPE html>"}},
        after_write_observation={"status_code": 404, "body": {"_raw": "<!DOCTYPE html>"}},
        after_cleanup_observation={"status_code": 404, "body": {"_raw": "<!DOCTYPE html>"}},
        runtime_bindings={"id": "created-id"},
        cleanup_execution_receipt=cleanup_exec,
    )
    assert receipt["equivalence_status"] == "EQUIVALENT"
