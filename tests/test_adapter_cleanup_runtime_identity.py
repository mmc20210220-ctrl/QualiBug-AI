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
    assert audit["required"] is True
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


def test_non_adapter_experiment_binding_audit_is_not_required() -> None:
    projected, audit = cleanup._project_adapter_cleanup_requirements(
        experiment={
            "cleanup_plan": [
                {
                    "action": "source_declared_compensation",
                    "adapter": "http_api",
                    "operation_ref": "delete_product",
                }
            ]
        },
        steps=[_step()],
    )

    assert projected == [_step()]
    assert audit["required"] is False
    assert audit["complete"] is True
    assert audit["declared_operation_refs"] == []
    assert audit["unbound"] == []


def test_declared_adapter_without_runtime_step_is_explicitly_unbound() -> None:
    projected, audit = cleanup._project_adapter_cleanup_requirements(
        experiment=_experiment(),
        steps=[],
    )

    assert projected == []
    assert audit["required"] is True
    assert audit["complete"] is False
    assert audit["missing_runtime_operation_refs"] == [_OPERATION]
    assert audit["unbound"] == [
        {
            "step_id": "",
            "phase": "",
            "operation_ref": _OPERATION,
            "cleanup_contract_count": 1,
            "status": "UNBOUND",
            "reason_code": "ADAPTER_CLEANUP_RUNTIME_STEP_MISSING",
        }
    ]


def test_conflicting_governance_identity_cannot_activate_adapter_cleanup() -> None:
    step = _step()
    step["governance_receipt"] = {
        **step["governance_receipt"],
        "operation_ref": "different_operation",
    }
    projected, audit = cleanup._project_adapter_cleanup_requirements(
        experiment=_experiment(),
        steps=[step],
    )
    assert audit["complete"] is True

    attempt = cleanup._governed_write_attempts_with_step_identity(projected)[0]
    assert attempt["operation_ref"] == _OPERATION
    assert attempt[cleanup._RUNTIME_IDENTITY_CONFLICTS] == ["operation_ref"]
    assert cleanup._ORIGINAL_GOVERNED_WRITE_CHANGED_STATE(attempt) is False
    assert cleanup._governed_write_changed_state_with_adapter_requirement(attempt) is False


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


def test_adapter_identity_uses_source_step_id_for_multi_write_same_operation() -> None:
    """Control+treatment expands one cleanup template per write step.

    Without source_step_id scoping, two accepted creates for the same
    operation_ref collapse to an empty identity and every cleanup receipt
    fails as CLEANUP_ROW_NOT_CREATED_BY_THIS_RUN / identity_empty.
    """
    control = _step(
        identity="qb_auto_sku_control",
        step_id="control_1",
    )
    control["phase"] = "control"
    treatment = _step(
        identity="qb_auto_sku_treatment",
        step_id="treatment_1",
    )

    control_identity = cleanup._adapter_cleanup_identity_exact(
        {**_CLEANUP, "source_step_id": "control_1"},
        runtime_bindings={},
        steps_out=[control, treatment],
    )
    treatment_identity = cleanup._adapter_cleanup_identity_exact(
        {**_CLEANUP, "source_step_id": "treatment_1"},
        runtime_bindings={},
        steps_out=[control, treatment],
    )

    assert control_identity == "qb_auto_sku_control"
    assert treatment_identity == "qb_auto_sku_treatment"


def test_multi_write_cleanup_contracts_bind_per_source_step() -> None:
    control = _step(identity="qb_auto_sku_control", step_id="control_1")
    control["phase"] = "control"
    treatment = _step(identity="qb_auto_sku_treatment", step_id="treatment_1")
    experiment = {
        "cleanup_plan": [
            {**_CLEANUP, "source_step_id": "treatment_1"},
            {**_CLEANUP, "source_step_id": "control_1"},
        ]
    }

    projected, audit = cleanup._project_adapter_cleanup_requirements(
        experiment=experiment,
        steps=[control, treatment],
    )

    assert audit["complete"] is True
    assert audit["bound_count"] == 2
    assert sorted(audit["bound_step_ids"]) == ["control_1", "treatment_1"]
    markers = [
        step[cleanup._ADAPTER_BINDING_MARKER]
        for step in projected
        if cleanup._ADAPTER_BINDING_MARKER in step
    ]
    assert len(markers) == 2
    assert {row["step_id"] for row in markers} == {"control_1", "treatment_1"}


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


def test_scoped_accepted_write_count_prefers_source_step() -> None:
    writes = [
        {"step_id": "treatment_1", "operation_ref": "create_product"},
        {"step_id": "control_1", "operation_ref": "create_product"},
    ]
    assert (
        cleanup_core._scoped_accepted_write_count_for_cleanup(
            {"source_step_id": "treatment_1"},
            writes,
        )
        == 1
    )
    assert (
        cleanup_core._scoped_accepted_write_count_for_cleanup(
            {"compensates_operation_ref": "create_product"},
            writes,
        )
        == 2
    )
    assert (
        cleanup_core._scoped_accepted_write_count_for_cleanup({}, writes) == 2
    )


def test_state_unchanged_multi_subject_cleanup_does_not_double_count(
    tmp_path,
) -> None:
    """NOT_REQUIRED control+treatment must attribute accepted writes once.

    Uses HTTP cleanup plans (no db_sql adapter binding) so identical
    before/after snapshots stay on the state-unchanged path.
    """
    http_cleanup = {
        "action": "source_declared_compensation",
        "adapter": "http_api",
        "operation_ref": "noop_compensate",
        "method": "POST",
        "path": "/noop",
    }
    unchanged = {
        "accepted": True,
        "method": "POST",
        "audit_path": "audit/write.json",
        "write": {"status": 200, "body": {"id": "x", "stock": 1}},
        "before": {"status": 200, "body": {"id": "x", "stock": 1}},
        "after": {"status": 200, "body": {"id": "x", "stock": 1}},
    }
    result = cleanup.execute_experiment_cleanup_compensation(
        exp={
            "safety_contract": {"governed_write": True},
            "cleanup_plan": [
                {**http_cleanup, "source_step_id": "treatment_1"},
                {**http_cleanup, "source_step_id": "control_1"},
            ],
            "write_reversibility_proof": {},
        },
        steps_out=[
            {
                "step_id": "treatment_1",
                "phase": "treatment",
                "operation_ref": "mutate",
                "method": "POST",
                "path": "/items",
                "status_code": 200,
                "governance_receipt": {
                    **unchanged,
                    "step_id": "treatment_1",
                    "operation_ref": "mutate",
                },
            },
            {
                "step_id": "control_1",
                "phase": "control",
                "operation_ref": "mutate",
                "method": "POST",
                "path": "/items",
                "status_code": 200,
                "governance_receipt": {
                    **unchanged,
                    "step_id": "control_1",
                    "operation_ref": "mutate",
                },
            },
        ],
        observations={},
        contract_evidence_receipts=[],
        activation_requirements={
            "cleanup": ["cleanup:treatment_1", "cleanup:control_1"]
        },
        pre_transport_block_reasons=[],
        request_bodies_for_cleanup={},
        runtime_bindings={},
        pending_fixture_cleanups=[],
        cleanup_failures=0,
        ops={"noop_compensate": {"method": "POST", "path": "/noop"}},
        actors={},
        tokens={},
        eid="experiment-unchanged-multi",
        oid="obligation-unchanged-multi",
        resolved_campaign_id="campaign-unchanged-multi",
        resolved_execution_id="execution-unchanged-multi",
        campaign_id="campaign-unchanged-multi",
        root=tmp_path,
        project="runtime-cleanup",
        base_url="https://sut.example.test",
        runtime_contract={
            "status": "approved",
            "environment_type": "test",
            "execution_mode": "approved_sandbox_write",
        },
    )
    cleanup_receipts = [
        row
        for row in result["contract_evidence_receipts"]
        if row.get("kind") == "cleanup"
    ]
    assert [row["status"] for row in cleanup_receipts] == [
        "NOT_REQUIRED",
        "NOT_REQUIRED",
    ]
    counts = [
        int(row["evidence"]["accepted_write_count"]) for row in cleanup_receipts
    ]
    assert sum(counts) == 2
    assert counts[0] == 2
    assert counts[1] == 0


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
    assert binding["missing_runtime_operation_refs"] == [_OPERATION]


def test_http_cleanup_contracts_are_scoped_to_their_source_write(
    monkeypatch,
    tmp_path,
) -> None:
    """Each source-step cleanup must prove only its own accepted write."""

    def governed_write(*, step_id: str, identity: str) -> dict:
        return {
            "accepted": True,
            "method": "POST",
            "path": "/resources",
            "audit_path": f"audit/{step_id}.jsonl",
            "write": {
                "status": 201,
                "body": {"id": identity},
            },
            "before": {"status": 200, "body": []},
            "after": {"status": 200, "body": [{"id": identity}]},
        }

    source_steps = [
        {
            "step_id": "control_1",
            "phase": "control",
            "operation_ref": "op-create",
            "actor_ref": "actor-public",
            "method": "POST",
            "path": "/resources",
            "status_code": 201,
            "body": {"id": "resource-control"},
            "governance_receipt": governed_write(
                step_id="control_1", identity="resource-control"
            ),
        },
        {
            "step_id": "treatment_1",
            "phase": "treatment",
            "operation_ref": "op-create",
            "actor_ref": "actor-public",
            "method": "POST",
            "path": "/resources",
            "status_code": 201,
            "body": {"id": "resource-treatment"},
            "governance_receipt": governed_write(
                step_id="treatment_1", identity="resource-treatment"
            ),
        },
    ]

    cleanup_plan = [
        {
            "action": "source_declared_compensation",
            "compensates_operation_ref": "op-create",
            "operation_ref": "op-delete",
            "method": "DELETE",
            "path": "/resources/{id}",
            "source_step_id": "treatment_1",
        },
        {
            "action": "source_declared_compensation",
            "compensates_operation_ref": "op-create",
            "operation_ref": "op-delete",
            "method": "DELETE",
            "path": "/resources/{id}",
            "source_step_id": "control_1",
        },
    ]

    cleanup_paths: list[str] = []

    def fake_cleanup_write(**kwargs) -> dict:
        path = kwargs["path"]
        cleanup_paths.append(path)
        identity = path.rsplit("/", 1)[-1]
        return {
            "accepted": True,
            "method": "DELETE",
            "path": path,
            "audit_path": f"audit/cleanup-{identity}.jsonl",
            "audit_record": {"phase": "cleanup", "path": path},
            "before": {"status": 200, "body": [{"id": identity}]},
            "write": {"status": 204, "body": {}},
            "after": {"status": 200, "body": []},
        }

    monkeypatch.setattr(cleanup_core, "execute_governed_control_write", fake_cleanup_write)
    monkeypatch.setattr(cleanup_core, "sandbox_write_allowed", lambda **_: (True, "approved"))
    monkeypatch.setattr(cleanup, "execute_governed_control_write", fake_cleanup_write)
    monkeypatch.setattr(cleanup, "sandbox_write_allowed", lambda **_: (True, "approved"))
    monkeypatch.setattr(cleanup_core, "_declared_observation_path", lambda *_, **__: "/resources")
    monkeypatch.setattr(cleanup_core, "seal_after_cleanup_observation", lambda **_: {})

    result = cleanup.execute_experiment_cleanup_compensation(
        exp={
            "safety_contract": {"governed_write": True},
            "cleanup_plan": cleanup_plan,
            "behavior_ir": {"entities": []},
            "write_reversibility_proof": {
                "proof_id": "proof-http-two-writes",
                "cleanup_authority": {
                    "mode": "identity_delete",
                    "cleanup_operation_ref": "op-delete",
                },
            },
        },
        steps_out=source_steps,
        observations={},
        contract_evidence_receipts=[],
        activation_requirements={
            "cleanup": ["cleanup-treatment", "cleanup-control"]
        },
        pre_transport_block_reasons=[],
        request_bodies_for_cleanup={},
        runtime_bindings={},
        pending_fixture_cleanups=[],
        cleanup_failures=0,
        ops={
            "op-create": {"id": "op-create", "method": "POST", "path": "/resources"},
            "op-delete": {"id": "op-delete", "method": "DELETE", "path": "/resources/{id}"},
        },
        actors={"actor-public": {"id": "actor-public", "role": "public"}},
        tokens={},
        eid="experiment-http-two-writes",
        oid="obligation-http-two-writes",
        resolved_campaign_id="campaign-http-two-writes",
        resolved_execution_id="execution-http-two-writes",
        campaign_id="campaign-http-two-writes",
        root=tmp_path,
        project="runtime-cleanup",
        base_url="https://sut.example.test",
        runtime_contract={
            "status": "approved",
            "environment_type": "test",
            "execution_mode": "approved_sandbox_write",
        },
    )

    cleanup_receipts = [
        receipt
        for receipt in result["contract_evidence_receipts"]
        if receipt.get("kind") == "cleanup"
    ]
    assert [receipt["status"] for receipt in cleanup_receipts] == [
        "COMPLETED",
        "COMPLETED",
    ]
    assert [
        receipt["evidence"]["accepted_write_count"]
        for receipt in cleanup_receipts
    ] == [1, 1]
    assert [
        receipt["evidence"]["cleanup_write_count"]
        for receipt in cleanup_receipts
    ] == [1, 1]
    assert cleanup_paths == [
        "/resources/resource-treatment",
        "/resources/resource-control",
    ]
    assert result["cleanup_failures"] == 0


def test_db_sql_source_scoped_cleanup_not_required_without_accepted_write(
    tmp_path,
    monkeypatch,
) -> None:
    """Duplicate-create treatment with no accepted write must NOT_REQUIRED.

    Live register dual-write: control 201 + id, treatment 500 / no id. The
    treatment db_sql plan must not seal CLEANUP_ROW_IDENTITY_NOT_RESOLVABLE.
    """
    deleted: list[str] = []

    def fake_execute(step, *, identity_value, **_kwargs):
        deleted.append(str(identity_value))
        return {
            "schema_version": "qualibug.cleanup-adapter-execution.v1",
            "receipt_id": f"cleanup_adapter_{identity_value}",
            "adapter": "db_sql",
            "table": "users",
            "identity_column": "id",
            "identity_value": identity_value,
            "status": "CLEANED",
            "reason_code": "",
            "rows_deleted": 1,
            "mode": "row_delete",
            "ownership_basis": "creation_receipt",
        }

    import ai_test_asset_center.cleanup_adapter_ladder as ladder

    monkeypatch.setattr(ladder, "execute_declared_adapter_cleanup", fake_execute)
    monkeypatch.setattr(
        cleanup_core,
        "_project_database_dsn",
        lambda *_a, **_k: ("postgresql://u:p@localhost:5432/db", ""),
    )
    monkeypatch.setattr(
        ladder,
        "build_ordered_delete_plan",
        lambda **kwargs: [
            {
                "adapter": "db_sql",
                "table": kwargs["table"],
                "identity_column": kwargs["identity_column"],
                "mode": "row_delete",
                "requires_ownership_proof": True,
            }
        ],
    )

    result = cleanup.execute_experiment_cleanup_compensation(
        exp={
            "safety_contract": {"governed_write": True},
            "cleanup_plan": [
                {
                    "action": "declared_adapter_cleanup",
                    "adapter": "db_sql",
                    "mode": "row_delete",
                    "table": "users",
                    "identity_column": "id",
                    "scope": "run_created_only",
                    "requires_ownership_proof": True,
                    "compensates_operation_ref": "op-register",
                    "source_step_id": "treatment_1",
                },
                {
                    "action": "declared_adapter_cleanup",
                    "adapter": "db_sql",
                    "mode": "row_delete",
                    "table": "users",
                    "identity_column": "id",
                    "scope": "run_created_only",
                    "requires_ownership_proof": True,
                    "compensates_operation_ref": "op-register",
                    "source_step_id": "control_1",
                },
            ],
            "behavior_ir": {
                "entities": [{"name": "users", "identity_fields": ["id"]}]
            },
        },
        steps_out=[
            {
                "step_id": "control_1",
                "phase": "control",
                "operation_ref": "op-register",
                "method": "POST",
                "path": "/api/auth/register",
                "status_code": 201,
                "governance_receipt": {
                    "accepted": True,
                    "method": "POST",
                    "audit_path": "audit/control.json",
                    "write": {
                        "status": 201,
                        "body": {"id": "user-control", "email": "a@example.com"},
                    },
                    "before": {"status": 200, "body": {}},
                    "after": {
                        "status": 200,
                        "body": {"id": "user-control", "email": "a@example.com"},
                    },
                },
            },
            {
                "step_id": "treatment_1",
                "phase": "treatment",
                "operation_ref": "op-register",
                "method": "POST",
                "path": "/api/auth/register",
                "status_code": 500,
                "governance_receipt": {
                    "accepted": False,
                    "method": "POST",
                    "audit_path": "audit/treatment.json",
                    "write": {
                        "status": 500,
                        "body": {"error": "duplicate users_email_key"},
                    },
                    "before": {"status": 200, "body": {}},
                    "after": {
                        "status": 500,
                        "body": {"error": "duplicate users_email_key"},
                    },
                },
            },
        ],
        observations={},
        contract_evidence_receipts=[],
        activation_requirements={
            "cleanup": ["cleanup:treatment_1", "cleanup:control_1"]
        },
        pre_transport_block_reasons=[],
        request_bodies_for_cleanup={},
        runtime_bindings={},
        pending_fixture_cleanups=[],
        cleanup_failures=0,
        ops={
            "op-register": {
                "id": "op-register",
                "method": "POST",
                "path": "/api/auth/register",
            }
        },
        actors={},
        tokens={},
        eid="exp-register-dup",
        oid="obl-register-dup",
        resolved_campaign_id="camp-register-dup",
        resolved_execution_id="exec-register-dup",
        campaign_id="camp-register-dup",
        root=tmp_path,
        project="register-dup",
        base_url="https://sut.example.test",
        runtime_contract={
            "status": "approved",
            "environment_type": "test",
            "execution_mode": "approved_sandbox_write",
            "approved_base_url": "https://sut.example.test",
        },
    )

    cleanup_receipts = [
        row
        for row in result["contract_evidence_receipts"]
        if row.get("kind") == "cleanup"
    ]
    by_subject = {row["subject_id"]: row for row in cleanup_receipts}
    assert by_subject["cleanup:treatment_1"]["status"] == "NOT_REQUIRED"
    assert by_subject["cleanup:treatment_1"]["evidence"]["reason_code"] == (
        "NO_ACCEPTED_WRITE"
    )
    assert by_subject["cleanup:control_1"]["status"] == "COMPLETED"
    assert deleted == ["user-control"]
    assert result["cleanup_failures"] == 0
    assert "CLEANUP_ROW_IDENTITY_NOT_RESOLVABLE" not in {
        (row.get("evidence") or {}).get("reason_code") for row in cleanup_receipts
    }


def test_db_sql_cleanup_scoped_zero_without_source_step_is_not_required(
    tmp_path,
    monkeypatch,
) -> None:
    """Unscoped db_sql with compensates_op matching no accepted write.

    T120110Z sealed 16× CLEANUP_ROW_IDENTITY_NOT_RESOLVABLE on treatment when
    scoped accepted-write count was 0 but source_step_id was absent — the old
    guard required source_step_id, so the adapter still ran with an empty
    identity. Scope emptiness alone must yield NOT_REQUIRED.
    """
    deleted: list[str] = []

    def fake_execute(step, *, identity_value, **_kwargs):
        deleted.append(str(identity_value))
        return {
            "schema_version": "qualibug.cleanup-adapter-execution.v1",
            "receipt_id": f"cleanup_adapter_{identity_value or 'empty'}",
            "adapter": "db_sql",
            "table": "inventory",
            "identity_column": "id",
            "identity_value": identity_value,
            "status": "REFUSED",
            "reason_code": "CLEANUP_ROW_IDENTITY_NOT_RESOLVABLE",
            "rows_deleted": 0,
            "mode": "row_delete",
            "ownership_basis": "",
        }

    import ai_test_asset_center.cleanup_adapter_ladder as ladder

    monkeypatch.setattr(ladder, "execute_declared_adapter_cleanup", fake_execute)
    monkeypatch.setattr(
        cleanup_core,
        "_project_database_dsn",
        lambda *_a, **_k: ("postgresql://u:p@localhost:5432/db", ""),
    )
    monkeypatch.setattr(
        ladder,
        "execute_declared_adapter_field_restore",
        lambda step, **kwargs: {
            "schema_version": "qualibug.cleanup-adapter-execution.v1",
            "receipt_id": "cleanup_adapter_restore",
            "adapter": "db_sql",
            "table": step.get("table"),
            "identity_column": "id",
            "identity_value": kwargs.get("identity_value"),
            "status": "CLEANED",
            "reason_code": "",
            "rows_deleted": 0,
            "rows_updated": 1,
            "mode": "field_restore",
            "ownership_basis": "governed_mutation_attested",
        },
    )
    monkeypatch.setattr(
        ladder,
        "build_ordered_delete_plan",
        lambda **kwargs: [
            {
                "adapter": "db_sql",
                "table": kwargs["table"],
                "identity_column": kwargs["identity_column"],
                "mode": "row_delete",
                "requires_ownership_proof": True,
            }
        ],
    )

    result = cleanup.execute_experiment_cleanup_compensation(
        exp={
            "safety_contract": {"governed_write": True},
            "cleanup_plan": [
                {
                    "action": "declared_adapter_cleanup",
                    "adapter": "db_sql",
                    "mode": "row_delete",
                    "table": "inventory",
                    "identity_column": "id",
                    "scope": "run_created_only",
                    "requires_ownership_proof": True,
                    # Matches no accepted write — scoped count 0, no source_step.
                    "compensates_operation_ref": "op-other",
                },
                {
                    "action": "declared_adapter_cleanup",
                    "adapter": "db_sql",
                    "mode": "row_delete",
                    "table": "inventory",
                    "identity_column": "id",
                    "scope": "run_created_only",
                    "requires_ownership_proof": True,
                    "compensates_operation_ref": "op-reserve",
                    "source_step_id": "control_1",
                },
            ],
            "behavior_ir": {
                "entities": [{"name": "inventory", "identity_fields": ["id"]}]
            },
        },
        steps_out=[
            {
                "step_id": "control_1",
                "phase": "control",
                "operation_ref": "op-reserve",
                "method": "POST",
                "path": "/api/inventory/reserve",
                "status_code": 200,
                "governance_receipt": {
                    "accepted": True,
                    "method": "POST",
                    "audit_path": "audit/control.json",
                    "write": {
                        "status": 200,
                        "body": {"id": "inv-1", "stock": 9},
                    },
                    "before": {
                        "status": 200,
                        "body": {"id": "inv-1", "stock": 10},
                    },
                    "after": {
                        "status": 200,
                        "body": {"id": "inv-1", "stock": 9},
                    },
                },
            },
            {
                "step_id": "treatment_1",
                "phase": "treatment",
                "operation_ref": "op-reserve",
                "method": "POST",
                "path": "/api/inventory/reserve",
                "status_code": 500,
                "governance_receipt": {
                    "accepted": False,
                    "method": "POST",
                    "audit_path": "audit/treatment.json",
                    "write": {"status": 500, "body": {"error": "bad type"}},
                    "before": {
                        "status": 200,
                        "body": {"id": "inv-1", "stock": 9},
                    },
                    "after": {"status": 500, "body": {"error": "bad type"}},
                },
            },
        ],
        observations={},
        contract_evidence_receipts=[],
        activation_requirements={
            "cleanup": ["cleanup:treatment_1", "cleanup:control_1"]
        },
        pre_transport_block_reasons=[],
        request_bodies_for_cleanup={},
        runtime_bindings={},
        pending_fixture_cleanups=[],
        cleanup_failures=0,
        ops={
            "op-reserve": {
                "id": "op-reserve",
                "method": "POST",
                "path": "/api/inventory/reserve",
            },
            "op-other": {
                "id": "op-other",
                "method": "POST",
                "path": "/api/other",
            },
        },
        actors={},
        tokens={},
        eid="exp-inventory-mismatch",
        oid="obl-inventory-mismatch",
        resolved_campaign_id="camp-inventory-mismatch",
        resolved_execution_id="exec-inventory-mismatch",
        campaign_id="camp-inventory-mismatch",
        root=tmp_path,
        project="inventory-mismatch",
        base_url="https://sut.example.test",
        runtime_contract={
            "status": "approved",
            "environment_type": "test",
            "execution_mode": "approved_sandbox_write",
            "approved_base_url": "https://sut.example.test",
        },
    )

    cleanup_receipts = [
        row
        for row in result["contract_evidence_receipts"]
        if row.get("kind") == "cleanup"
    ]
    by_subject = {row["subject_id"]: row for row in cleanup_receipts}
    assert by_subject["cleanup:treatment_1"]["status"] == "NOT_REQUIRED"
    assert by_subject["cleanup:treatment_1"]["evidence"]["reason_code"] == (
        "NO_ACCEPTED_WRITE"
    )
    assert by_subject["cleanup:control_1"]["status"] == "COMPLETED"
    assert result["cleanup_failures"] == 0
    assert deleted == []
    assert "CLEANUP_ROW_IDENTITY_NOT_RESOLVABLE" not in {
        (row.get("evidence") or {}).get("reason_code") for row in cleanup_receipts
    }


def test_no_governed_write_attempt_is_not_cleanup_transport_failure(
    tmp_path,
) -> None:
    """Read-only / zero-write arms must NOT_REQUIRED cleanup, never HF."""
    result = cleanup.execute_experiment_cleanup_compensation(
        exp={
            "safety_contract": {"governed_write": True},
            "cleanup_plan": [
                {
                    "action": "source_declared_compensation",
                    "compensates_operation_ref": "op-create",
                    "operation_ref": "op-delete",
                    "method": "DELETE",
                    "path": "/resources/{id}",
                    "source_step_id": "treatment_1",
                }
            ],
            "behavior_ir": {"entities": []},
        },
        steps_out=[
            {
                "step_id": "treatment_1",
                "phase": "treatment",
                "operation_ref": "op-create",
                "method": "GET",
                "path": "/resources",
                "status_code": 403,
            }
        ],
        observations={},
        contract_evidence_receipts=[],
        activation_requirements={"cleanup": ["cleanup:treatment_1"]},
        pre_transport_block_reasons=[],
        request_bodies_for_cleanup={},
        runtime_bindings={},
        pending_fixture_cleanups=[],
        cleanup_failures=0,
        ops={
            "op-create": {"id": "op-create", "method": "POST", "path": "/resources"},
            "op-delete": {
                "id": "op-delete",
                "method": "DELETE",
                "path": "/resources/{id}",
            },
        },
        actors={},
        tokens={},
        eid="experiment-no-write",
        oid="obligation-no-write",
        resolved_campaign_id="campaign-no-write",
        resolved_execution_id="execution-no-write",
        campaign_id="campaign-no-write",
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
    assert result["observations"]["cleanup_reason"] == "no_write_reached_transport"
    cleanup_receipts = [
        row
        for row in result["contract_evidence_receipts"]
        if row.get("kind") == "cleanup"
    ]
    assert len(cleanup_receipts) == 1
    assert cleanup_receipts[0]["status"] == "NOT_REQUIRED"
    assert cleanup_receipts[0]["evidence"]["reason_code"] == (
        "NO_WRITE_REACHED_TRANSPORT"
    )


def test_zero_transport_governance_receipt_is_not_cleanup_transport_failure(
    tmp_path,
) -> None:
    """Before-GET + blocked write must not seal HARNESS_CLEANUP_TRANSPORT_FAILED.

    Reproduces T120110Z/T123504Z: governance receipts exist with
    write_request_attempt_count=0, before observed, after={}, so
    `_rejected_writes_left_state_unchanged` is False and falsely increments
    cleanup_failures even though no write left the harness.
    """
    from ai_test_asset_center.experiment_cleanup import (
        _governed_write_reached_transport,
    )

    blocked_receipt = {
        "accepted": False,
        "status": "blocked",
        "reason": "identity_binding_unresolved",
        "method": "POST",
        "path": "/resources",
        "audit_path": "audit/blocked.json",
        "before": {"status": 200, "body": {"items": []}},
        "write": {"status": 0, "body": "", "headers": {}, "error": "blocked"},
        "after": {},
        "http_attempt_count": 1,
        "write_request_attempt_count": 0,
        "production_http_requests": 0,
    }
    assert _governed_write_reached_transport(blocked_receipt) is False

    result = cleanup.execute_experiment_cleanup_compensation(
        exp={
            "safety_contract": {"governed_write": True},
            "cleanup_plan": [
                {
                    "action": "source_declared_compensation",
                    "compensates_operation_ref": "op-create",
                    "operation_ref": "op-delete",
                    "method": "DELETE",
                    "path": "/resources/{id}",
                    "source_step_id": "treatment_1",
                }
            ],
            "behavior_ir": {"entities": []},
        },
        steps_out=[
            {
                "step_id": "treatment_1",
                "phase": "treatment",
                "operation_ref": "op-create",
                "method": "POST",
                "path": "/resources",
                "status_code": 0,
                "governance_receipt": blocked_receipt,
            }
        ],
        observations={},
        contract_evidence_receipts=[],
        activation_requirements={"cleanup": ["cleanup:treatment_1"]},
        pre_transport_block_reasons=[],
        request_bodies_for_cleanup={},
        runtime_bindings={},
        pending_fixture_cleanups=[],
        cleanup_failures=0,
        ops={
            "op-create": {"id": "op-create", "method": "POST", "path": "/resources"},
            "op-delete": {
                "id": "op-delete",
                "method": "DELETE",
                "path": "/resources/{id}",
            },
        },
        actors={},
        tokens={},
        eid="experiment-zero-transport",
        oid="obligation-zero-transport",
        resolved_campaign_id="campaign-zero-transport",
        resolved_execution_id="execution-zero-transport",
        campaign_id="campaign-zero-transport",
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
    assert result["observations"]["cleanup_reason"] == "no_write_reached_transport"
    cleanup_receipts = [
        row
        for row in result["contract_evidence_receipts"]
        if row.get("kind") == "cleanup"
    ]
    assert len(cleanup_receipts) == 1
    assert cleanup_receipts[0]["status"] == "NOT_REQUIRED"
    evidence = cleanup_receipts[0]["evidence"]
    assert evidence["reason_code"] == "NO_WRITE_REACHED_TRANSPORT"
    assert evidence["write_reached_transport"] is False


def test_rejected_transport_write_unproven_state_still_fails_cleanup(
    tmp_path,
) -> None:
    """A write that reached transport but cannot prove unchanged still fails."""
    rejected_transport = {
        "accepted": False,
        "status": "failed",
        "reason": "control_write_not_accepted",
        "method": "POST",
        "path": "/resources",
        "audit_path": "audit/rejected.json",
        "before": {"status": 200, "body": {"items": []}},
        "write": {"status": 500, "body": {"error": "boom"}},
        "after": {"status": 200, "body": {"items": [{"id": "maybe-leaked"}]}},
        "http_attempt_count": 3,
        "write_request_attempt_count": 1,
        "production_http_requests": 0,
    }
    result = cleanup.execute_experiment_cleanup_compensation(
        exp={
            "safety_contract": {"governed_write": True},
            "cleanup_plan": [
                {
                    "action": "source_declared_compensation",
                    "compensates_operation_ref": "op-create",
                    "operation_ref": "op-delete",
                    "method": "DELETE",
                    "path": "/resources/{id}",
                    "source_step_id": "treatment_1",
                }
            ],
            "behavior_ir": {"entities": []},
        },
        steps_out=[
            {
                "step_id": "treatment_1",
                "phase": "treatment",
                "operation_ref": "op-create",
                "method": "POST",
                "path": "/resources",
                "status_code": 500,
                "governance_receipt": rejected_transport,
            }
        ],
        observations={},
        contract_evidence_receipts=[],
        activation_requirements={"cleanup": ["cleanup:treatment_1"]},
        pre_transport_block_reasons=[],
        request_bodies_for_cleanup={},
        runtime_bindings={},
        pending_fixture_cleanups=[],
        cleanup_failures=0,
        ops={
            "op-create": {"id": "op-create", "method": "POST", "path": "/resources"},
            "op-delete": {
                "id": "op-delete",
                "method": "DELETE",
                "path": "/resources/{id}",
            },
        },
        actors={},
        tokens={},
        eid="experiment-rejected-transport",
        oid="obligation-rejected-transport",
        resolved_campaign_id="campaign-rejected-transport",
        resolved_execution_id="execution-rejected-transport",
        campaign_id="campaign-rejected-transport",
        root=tmp_path,
        project="runtime-cleanup",
        base_url="https://sut.example.test",
        runtime_contract={
            "status": "approved",
            "environment_type": "test",
            "execution_mode": "approved_sandbox_write",
        },
    )
    assert result["cleanup_failures"] == 1
    assert result["observations"]["cleanup_status"] == "failed"
    cleanup_receipts = [
        row
        for row in result["contract_evidence_receipts"]
        if row.get("kind") == "cleanup"
    ]
    assert len(cleanup_receipts) == 1
    assert cleanup_receipts[0]["status"] == "FAILED"
    assert cleanup_receipts[0]["evidence"]["reason_code"] == (
        "REJECTED_WRITE_STATE_NOT_PROVEN_UNCHANGED"
    )
    assert cleanup_receipts[0]["evidence"]["write_reached_transport"] is True


def test_identity_bound_treatment_cleanup_completes_when_collection_unchanged(
    monkeypatch,
    tmp_path,
) -> None:
    """Identity-bound DELETE must COMPLETE even when snapshots look empty.

    Reproduces T113119Z CLEANUP_RECEIPT_FAILED:treatment where cleanup HTTP
    returned 2xx but aggregation sealed FAILED because projected-step
    cleanup-need detection dropped the identity binding.
    """

    def identity_create(identity: str) -> dict:
        return {
            "accepted": True,
            "method": "POST",
            "path": "/resources",
            "audit_path": f"audit/{identity}.jsonl",
            "write": {"status": 201, "body": {"id": identity}},
            "before": {"status": 200, "body": []},
            "after": {"status": 200, "body": []},
        }

    steps = [
        {
            "step_id": "control_1",
            "phase": "control",
            "operation_ref": "op-create",
            "actor_ref": "actor-public",
            "method": "POST",
            "path": "/resources",
            "status_code": 201,
            "body": {"id": "resource-control"},
            "governance_receipt": identity_create("resource-control"),
        },
        {
            "step_id": "treatment_1",
            "phase": "treatment",
            "operation_ref": "op-create",
            "actor_ref": "actor-public",
            "method": "POST",
            "path": "/resources",
            "status_code": 201,
            "body": {"id": "resource-treatment"},
            "governance_receipt": identity_create("resource-treatment"),
        },
    ]

    def fake_cleanup_write(**kwargs) -> dict:
        path = kwargs["path"]
        identity = path.rsplit("/", 1)[-1]
        return {
            "accepted": True,
            "method": "DELETE",
            "path": path,
            "audit_path": f"audit/cleanup-{identity}.jsonl",
            "before": {"status": 200, "body": [{"id": identity}]},
            "write": {"status": 200, "body": {}},
            "after": {"status": 404, "body": {}},
        }

    monkeypatch.setattr(cleanup_core, "execute_governed_control_write", fake_cleanup_write)
    monkeypatch.setattr(cleanup_core, "sandbox_write_allowed", lambda **_: (True, "approved"))
    monkeypatch.setattr(cleanup, "execute_governed_control_write", fake_cleanup_write)
    monkeypatch.setattr(cleanup, "sandbox_write_allowed", lambda **_: (True, "approved"))
    monkeypatch.setattr(
        cleanup_core, "_declared_observation_path", lambda *_, **__: "/resources/{id}"
    )
    monkeypatch.setattr(cleanup_core, "seal_after_cleanup_observation", lambda **_: {})

    result = cleanup.execute_experiment_cleanup_compensation(
        exp={
            "safety_contract": {"governed_write": True},
            "cleanup_plan": [
                {
                    "action": "source_declared_compensation",
                    "compensates_operation_ref": "op-create",
                    "operation_ref": "op-delete",
                    "method": "DELETE",
                    "path": "/resources/{id}",
                    "source_step_id": "treatment_1",
                },
                {
                    "action": "source_declared_compensation",
                    "compensates_operation_ref": "op-create",
                    "operation_ref": "op-delete",
                    "method": "DELETE",
                    "path": "/resources/{id}",
                    "source_step_id": "control_1",
                },
            ],
            "behavior_ir": {"entities": []},
            "write_reversibility_proof": {
                "proof_id": "proof-identity-bound",
                "cleanup_authority": {
                    "mode": "identity_delete",
                    "cleanup_operation_ref": "op-delete",
                },
            },
        },
        steps_out=steps,
        observations={},
        contract_evidence_receipts=[],
        activation_requirements={
            "cleanup": ["cleanup:treatment_1", "cleanup:control_1"]
        },
        pre_transport_block_reasons=[],
        request_bodies_for_cleanup={},
        runtime_bindings={},
        pending_fixture_cleanups=[],
        cleanup_failures=0,
        ops={
            "op-create": {"id": "op-create", "method": "POST", "path": "/resources"},
            "op-delete": {
                "id": "op-delete",
                "method": "DELETE",
                "path": "/resources/{id}",
            },
        },
        actors={"actor-public": {"id": "actor-public", "role": "public"}},
        tokens={},
        eid="experiment-identity-bound",
        oid="obligation-identity-bound",
        resolved_campaign_id="campaign-identity-bound",
        resolved_execution_id="execution-identity-bound",
        campaign_id="campaign-identity-bound",
        root=tmp_path,
        project="runtime-cleanup",
        base_url="https://sut.example.test",
        runtime_contract={
            "status": "approved",
            "environment_type": "test",
            "execution_mode": "approved_sandbox_write",
        },
    )

    cleanup_receipts = [
        row
        for row in result["contract_evidence_receipts"]
        if row.get("kind") == "cleanup"
    ]
    assert [row["status"] for row in cleanup_receipts] == [
        "COMPLETED",
        "COMPLETED",
    ]
    assert all(row["evidence"]["restoration_verified"] for row in cleanup_receipts)
    assert result["cleanup_failures"] == 0
