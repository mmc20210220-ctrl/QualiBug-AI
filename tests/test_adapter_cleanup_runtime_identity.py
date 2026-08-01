from __future__ import annotations

from ai_test_asset_center import experiment_cleanup_executor as cleanup
from ai_test_asset_center import experiment_cleanup_executor_core as cleanup_core


_OPERATION = "create_product"
_CLEANUP = {
    "action": "declared_adapter_cleanup",
    "adapter": "db_sql",
    "mode": "row_delete",
    "table": "products",
    "identity_column": "sku",
    "scope": "run_created_only",
    "requires_ownership_proof": True,
    "compensates_operation_ref": _OPERATION,
}


def _governed_write(identity: str) -> dict:
    return {
        "accepted": True,
        "method": "POST",
        "audit_path": "audit/write.json",
        "write": {
            "status": 201,
            "body": {"sku": identity, "name": "runtime product"},
        },
        # The live failure that motivated this regression: collection snapshots
        # may look unchanged even though the accepted create response carries the
        # exact newly-created identity.
        "before": {"status": 200, "body": {"items": []}},
        "after": {"status": 200, "body": {"items": []}},
    }


def _step(
    *,
    operation_ref: str = _OPERATION,
    identity: str = "qb_auto_sku_runtime_1",
    step_id: str = "treatment_1",
) -> dict:
    return {
        "step_id": step_id,
        "phase": "treatment",
        "operation_ref": operation_ref,
        "actor_ref": "actor_admin",
        "method": "POST",
        "path": "/api/products",
        "status_code": 201,
        "body": {"sku": identity, "name": "runtime product"},
        "governance_receipt": _governed_write(identity),
    }


def _experiment() -> dict:
    return {
        "safety_contract": {"governed_write": True},
        "cleanup_plan": [dict(_CLEANUP)],
        "behavior_ir": {"entities": []},
    }


def test_governed_attempt_preserves_exact_runtime_step_identity() -> None:
    source = _step()
    projected, audit = cleanup._project_adapter_cleanup_requirements(
        experiment=_experiment(),
        steps=[source],
    )

    assert cleanup._ADAPTER_BINDING_MARKER not in source
    assert audit["complete"] is True
    assert audit["bound_step_ids"] == ["treatment_1"]

    attempts = cleanup._governed_write_attempts_with_step_identity(projected)
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["step_id"] == "treatment_1"
    assert attempt["operation_ref"] == _OPERATION
    assert attempt["phase"] == "treatment"
    assert attempt[cleanup._ADAPTER_BINDING_MARKER]["cleanup_required"] is True

    # Raw before/after are intentionally equal. Only the exact compiled cleanup
    # contract plus the observed create identity makes this cleanup-required.
    assert cleanup._ORIGINAL_GOVERNED_WRITE_CHANGED_STATE(attempt) is False
    assert cleanup._governed_write_changed_state_with_adapter_requirement(attempt) is True


def test_adapter_identity_never_cross_binds_another_operation() -> None:
    target = _step(
        operation_ref=_OPERATION,
        identity="qb_auto_sku_target",
        step_id="treatment_target",
    )
    unrelated = _step(
        operation_ref="create_order",
        identity="qb_auto_sku_wrong_operation",
        step_id="treatment_other",
    )

    identity = cleanup._adapter_cleanup_identity_exact(
        dict(_CLEANUP),
        runtime_bindings={"sku": "qb_auto_sku_fallback"},
        steps_out=[target, unrelated],
    )

    assert identity == "qb_auto_sku_target"


def test_adapter_identity_refuses_multiple_created_rows_for_one_cleanup_step() -> None:
    first = _step(identity="qb_auto_sku_one", step_id="treatment_1")
    second = _step(identity="qb_auto_sku_two", step_id="treatment_2")

    identity = cleanup._adapter_cleanup_identity_exact(
        dict(_CLEANUP),
        runtime_bindings={},
        steps_out=[first, second],
    )

    assert identity == ""


def test_exact_create_identity_enters_real_adapter_cleanup_loop(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[dict] = []

    def fake_adapter_cleanup(
        cleanup_step,
        *,
        root,
        project,
        runtime_bindings,
        steps_out,
        runtime_contract=None,
        behavior_ir=None,
        creation_receipts=None,
    ):
        identity = cleanup_core._adapter_cleanup_identity(
            cleanup_step,
            runtime_bindings=runtime_bindings,
            steps_out=steps_out,
        )
        calls.append(
            {
                "operation_ref": cleanup_step.get("compensates_operation_ref"),
                "identity": identity,
            }
        )
        return {
            "schema_version": "qualibug.cleanup-adapter-execution.v1",
            "receipt_id": "cleanup_adapter_runtime_identity_1",
            "adapter": "db_sql",
            "table": "products",
            "identity_column": "sku",
            "identity_value": identity,
            "status": "CLEANED",
            "reason_code": "",
            "rows_deleted": 1,
            "rows_updated": 0,
            "mode": "row_delete",
            "ownership_basis": "creation_receipt",
        }

    monkeypatch.setattr(
        cleanup_core,
        "_execute_adapter_cleanup_step",
        fake_adapter_cleanup,
    )
    monkeypatch.setattr(
        cleanup_core,
        "seal_after_cleanup_observation",
        lambda **_: {},
    )
    monkeypatch.setattr(
        cleanup_core,
        "_append_adapter_cleanup_runtime_step",
        lambda **_: None,
    )

    observations: dict = {}
    result = cleanup.execute_experiment_cleanup_compensation(
        exp=_experiment(),
        steps_out=[_step()],
        observations=observations,
        contract_evidence_receipts=[],
        activation_requirements={"cleanup": ["cleanup:products"]},
        pre_transport_block_reasons=[],
        request_bodies_for_cleanup={},
        runtime_bindings={},
        pending_fixture_cleanups=[],
        cleanup_failures=0,
        ops={},
        actors={},
        tokens={},
        eid="experiment-runtime-cleanup",
        oid="obligation-runtime-cleanup",
        resolved_campaign_id="campaign-runtime-cleanup",
        resolved_execution_id="execution-runtime-cleanup",
        campaign_id="campaign-runtime-cleanup",
        root=tmp_path,
        project="runtime-cleanup",
        base_url="https://sut.example.test",
        runtime_contract={
            "status": "approved",
            "environment_type": "test",
            "execution_mode": "approved_sandbox_write",
        },
    )

    assert calls == [
        {
            "operation_ref": _OPERATION,
            "identity": "qb_auto_sku_runtime_1",
        }
    ]
    assert result["cleanup_failures"] == 0
    assert result["observations"]["cleanup_status"] == "cleaned"
    binding = result["observations"][
        "declared_adapter_cleanup_runtime_binding"
    ]
    assert binding["complete"] is True
    assert binding["bound_step_ids"] == ["treatment_1"]
    assert binding["runtime_marker_persisted"] is False
    assert all(
        cleanup._ADAPTER_BINDING_MARKER not in row
        for row in result["steps_out"]
    )


def test_mismatched_operation_does_not_enter_adapter_cleanup_loop(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        cleanup_core,
        "_execute_adapter_cleanup_step",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("mismatched operation must not reach cleanup")
        ),
    )

    result = cleanup.execute_experiment_cleanup_compensation(
        exp=_experiment(),
        steps_out=[_step(operation_ref="create_order")],
        observations={},
        contract_evidence_receipts=[],
        activation_requirements={"cleanup": ["cleanup:products"]},
        pre_transport_block_reasons=[],
        request_bodies_for_cleanup={},
        runtime_bindings={},
        pending_fixture_cleanups=[],
        cleanup_failures=0,
        ops={},
        actors={},
        tokens={},
        eid="experiment-runtime-cleanup-mismatch",
        oid="obligation-runtime-cleanup-mismatch",
        resolved_campaign_id="campaign-runtime-cleanup",
        resolved_execution_id="execution-runtime-cleanup-mismatch",
        campaign_id="campaign-runtime-cleanup",
        root=tmp_path,
        project="runtime-cleanup",
        base_url="https://sut.example.test",
        runtime_contract={
            "status": "approved",
            "environment_type": "test",
            "execution_mode": "approved_sandbox_write",
        },
    )

    assert result["cleanup_failures"] == 0
    assert result["observations"]["cleanup_status"] == "not_required"
    assert result["observations"]["cleanup_reason"] == (
        "accepted_write_state_unchanged"
    )
    binding = result["observations"][
        "declared_adapter_cleanup_runtime_binding"
    ]
    assert binding["complete"] is False
    assert binding["bound_step_ids"] == []
