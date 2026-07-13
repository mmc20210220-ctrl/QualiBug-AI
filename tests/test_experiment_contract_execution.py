"""Contract path: experiment execution, assertion DSL, cleanup preservation."""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from ai_test_asset_center.assertion_dsl import evaluate_assertion, materialize_assertion
from ai_test_asset_center.contract_oracles import (
    build_contract_evidence_receipt,
    evaluate_contract_oracle,
)
from ai_test_asset_center.customer_delivery_gate_v2 import _fingerprint
from ai_test_asset_center.discovery_funnel import reconcile_pipeline_health_after_campaign_cleanup
from ai_test_asset_center.discovery_mainline_contract import build_mainline_run_contract
from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.experiment_executor import (
    _cleanup_restores_governed_write,
    _declared_observation_path,
    execute_one_experiment,
    execute_selected_experiments,
    preflight_experiment_executable,
)
from ai_test_asset_center.obligation_attempt_ledger import build_obligation_attempt_ledger
from ai_test_asset_center.observed_product_scan_executor import ObservedProductScanExecutor
from ai_test_asset_center.observer_contracts import build_observer_receipt


def _mainline_run(campaign_id: str = "CMP-1") -> dict:
    return build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="RUN-1",
        campaign_id=campaign_id,
        target_id="TARGET-1",
        environment_id="ENV-1",
        policy_version="POLICY-1",
        evaluation_mode="operational",
    )


def _property_held_oracle_chain(
    *,
    campaign_id: str = "campaign-oracle",
    execution_id: str = "execution-oracle",
    experiment_id: str = "exp-1",
    obligation_id: str = "obl-1",
) -> dict:
    source_refs = [{
        "kind": "api_contract",
        "source_id": "resource-api",
        "locator": "GET /api/resources",
    }]
    observer = build_observer_receipt(
        observer_id="http_response",
        status="OBSERVED",
        evidence={"status_code": 200},
        campaign_id=campaign_id,
        execution_id=execution_id,
    )
    contract_receipts = [
        build_contract_evidence_receipt(
            kind=kind,
            experiment_id=experiment_id,
            obligation_id=obligation_id,
            campaign_id=campaign_id,
            execution_id=execution_id,
            subject_id=subject_id,
            status="OBSERVED",
            evidence=evidence,
        )
        for kind, subject_id, evidence in (
            (
                "control",
                "control-1",
                {
                    "response_observed": True,
                    "status_code": 200,
                    "control_succeeded": True,
                },
            ),
            (
                "treatment",
                "treatment-1",
                {"response_observed": True, "status_code": 200},
            ),
            ("actor", "actor-a", {"role": "public"}),
        )
    ]
    oracle = evaluate_contract_oracle(
        experiment={
            "experiment_id": experiment_id,
            "obligation_id": obligation_id,
            "campaign_id": campaign_id,
            "execution_id": execution_id,
            "source_refs": source_refs,
            "control_plan": [{
                "step_id": "control-1",
                "actor_ref": "actor-a",
                "operation_ref": "read-resource",
            }],
            "treatment_plan": [{
                "step_id": "treatment-1",
                "actor_ref": "actor-a",
                "operation_ref": "read-resource",
            }],
            "fixture_dag": {"nodes": [], "setup_order": []},
            "observers": [{"observer_id": "http_response"}],
            "cleanup_plan": [],
            "assertions": [{
                "assertion_id": "assert-status",
                "kind": "http_status",
                "expected": 200,
            }],
        },
        evidence={
            "status_code": 200,
            "observer_receipts": [observer],
            "contract_evidence_receipts": contract_receipts,
        },
    )
    return {
        "oracle": oracle,
        "observer_receipts": [observer],
        "contract_evidence_receipts": contract_receipts,
        "source_refs": source_refs,
    }


def _property_held_oracle_receipt() -> dict:
    return _property_held_oracle_chain()["oracle"]


def test_state_transition_oracle_does_not_require_control_when_assertion_does_not() -> None:
    campaign_id = "campaign-state-no-control"
    execution_id = "execution-state-no-control"
    experiment_id = "exp-state-no-control"
    obligation_id = "obl-state-no-control"
    source_refs = [{
        "kind": "api_contract",
        "source_id": "order-api",
        "locator": "PATCH /api/orders/{id}/pay",
    }]
    observer_receipts = [
        build_observer_receipt(
            observer_id=observer_id,
            status="OBSERVED",
            evidence={observer_id: state},
            campaign_id=campaign_id,
            execution_id=execution_id,
        )
        for observer_id, state in (
            ("before_state", "PENDING"),
            ("after_state", "PAID"),
            ("http_response", 200),
        )
    ]
    contract_receipts = [
        build_contract_evidence_receipt(
            kind="treatment",
            experiment_id=experiment_id,
            obligation_id=obligation_id,
            campaign_id=campaign_id,
            execution_id=execution_id,
            subject_id="treatment-pay",
            status="OBSERVED",
            evidence={"response_observed": True, "status_code": 200},
        ),
        build_contract_evidence_receipt(
            kind="actor",
            experiment_id=experiment_id,
            obligation_id=obligation_id,
            campaign_id=campaign_id,
            execution_id=execution_id,
            subject_id="buyer",
            status="OBSERVED",
            evidence={"role": "buyer"},
        ),
    ]

    oracle = evaluate_contract_oracle(
        experiment={
            "experiment_id": experiment_id,
            "obligation_id": obligation_id,
            "campaign_id": campaign_id,
            "execution_id": execution_id,
            "source_refs": source_refs,
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "treatment-pay",
                "actor_ref": "buyer",
                "operation_ref": "pay_order",
            }],
            "fixture_dag": {"nodes": [], "setup_order": []},
            "observers": [
                {"observer_id": "before_state"},
                {"observer_id": "after_state"},
                {"observer_id": "http_response"},
            ],
            "cleanup_plan": [],
            "assertions": [{
                "assertion_id": "assert-state-transition",
                "kind": "state_transition",
                "from_state": "PENDING",
                "to_state": "PAID",
                "require_control": False,
            }],
        },
        evidence={
            "before_state": "PENDING",
            "after_state": "PAID",
            "observer_receipts": observer_receipts,
            "contract_evidence_receipts": contract_receipts,
        },
    )

    assert oracle["activation_receipt"]["status"] == "ACTIVE"
    assert "CONTROL_PLAN_MISSING" not in oracle["activation_receipt"]["reason_codes"]
    assert oracle["status"] == "PROPERTY_HELD"


def test_executor_does_not_emit_legacy_canonical_identity_hint() -> None:
    source = inspect.getsource(execute_one_experiment)
    assert "qualibug.canonical-identity-evidence.v1" not in source
    assert '"canonical_identity_evidence"' not in source


def test_json_path_type_and_conservation_sum() -> None:
    typed = evaluate_assertion(
        {"kind": "json_path_type", "path": "$.name", "expected_type": "string"},
        observations={"body": {"name": "ok"}},
    )
    assert typed["passed"] is True
    cons = evaluate_assertion(
        {"kind": "conservation", "equation": {"operator": "unchanged_sum", "terms": ["a", "b"]}},
        observations={"before_values": {"a": 1, "b": 2}, "after_values": {"a": 0, "b": 3}},
    )
    assert cons["passed"] is True
    cons_fail = evaluate_assertion(
        {"kind": "conservation", "equation": {"operator": "unchanged_sum", "terms": ["a", "b"]}},
        observations={"before_values": {"a": 1, "b": 2}, "after_values": {"a": 1, "b": 1}},
    )
    assert cons_fail["passed"] is False


def test_materialize_assertion_maps_family() -> None:
    spec = materialize_assertion({"kind": "authorization", "property": {}})
    assert spec["kind"] == "owner_tenant_visibility"


def test_missing_environment_blocks_compile() -> None:
    blocked = compile_experiment_for_obligation(
        {
            "obligation_id": "o1",
            "risk_family": "authorization",
            "required_operations": ["op1"],
            "required_actors": ["actor_a", "actor_b"],
            "required_observers": ["http_response"],
            "property": {"operation_ref": "op1", "control_actor_ref": "actor_a", "treatment_actor_ref": "actor_b"},
        },
        behavior_ir={
            "operations": [{"id": "op1", "method": "GET", "path": "/api/x", "read_write": "read"}],
            "actors": [
                {"id": "actor_a", "role": "buyer", "credential_secret_ref": "secret_ref:test_accounts:buyer"},
                {"id": "actor_b", "role": "seller", "credential_secret_ref": "secret_ref:test_accounts:seller"},
            ],
        },
        environment_type="",
    )
    assert blocked["compile_receipt"]["status"] == "BLOCKED"


def test_unresolved_fixture_not_compiled() -> None:
    blocked = compile_experiment_for_obligation(
        {
            "obligation_id": "o2",
            "risk_family": "validation",
            "required_operations": ["op1"],
            "required_actors": ["actor_a"],
            "required_fixtures": ["order_fixture"],
            "required_observers": ["http_response"],
            "property": {"operation_ref": "op1"},
        },
        behavior_ir={
            "operations": [{"id": "op1", "method": "GET", "path": "/api/x", "read_write": "read"}],
            "actors": [{"id": "actor_a", "role": "buyer", "credential_secret_ref": "secret_ref:test_accounts:buyer"}],
        },
        environment_type="test",
    )
    assert blocked["compile_receipt"]["status"] == "BLOCKED"
    assert blocked["compile_receipt"]["reason_code"] == "BLOCKED_MISSING_FIXTURE"


@pytest.mark.parametrize(
    "cleanup_path",
    ["/api/resources/{resourceId}", "/api/resources/:resourceId"],
)
def test_source_declared_cleanup_template_compiles_for_runtime_response_binding(
    cleanup_path: str,
) -> None:
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "o-cleanup-template",
            "risk_family": "validation",
            "required_operations": ["create_resource"],
            "required_actors": ["actor_a"],
            "required_observers": ["http_response"],
            "property": {"operation_ref": "create_resource", "treatment_actor_ref": "actor_a"},
            "cleanup_requirement": {"required": True, "operation_ref": "delete_resource"},
        },
            behavior_ir={
                "operations": [
                    {
                        "id": "list_resources",
                        "method": "GET",
                        "path": "/api/resources",
                        "read_write": "read",
                    },
                    {
                        "id": "create_resource",
                        "method": "POST",
                        "path": "/api/resources",
                        "read_write": "write",
                        "request_schema": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["name"],
                                        "properties": {"name": {"type": "string"}},
                                    },
                                    "example": {"name": "source-declared"},
                                },
                            },
                        },
                    },
                {"id": "delete_resource", "method": "DELETE", "path": cleanup_path, "read_write": "write"},
            ],
            "actors": [{"id": "actor_a", "role": "public"}],
        },
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert experiment["cleanup_plan"][0]["path"] == "/api/resources/{resourceId}"


def test_patch_write_compiles_snapshot_restore_without_delete_compensation() -> None:
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "o-patch-restore",
            "risk_family": "idempotency",
            "required_operations": ["patch_profile"],
            "required_actors": ["actor_a"],
            "required_observers": ["http_response"],
            "property": {
                "operation_ref": "patch_profile",
                "treatment_actor_ref": "actor_a",
            },
            "cleanup_requirement": {"required": True},
        },
        behavior_ir={
            "operations": [
                {
                    "id": "get_profile",
                    "method": "GET",
                    "path": "/api/profile",
                    "read_write": "read",
                },
                {
                    "id": "patch_profile",
                    "method": "PATCH",
                    "path": "/api/profile",
                    "read_write": "write",
                    "request_example": {"balance": 50},
                },
            ],
            "actors": [{"id": "actor_a", "role": "public"}],
        },
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert experiment["cleanup_plan"] == [{
        "action": "restore_before_snapshot",
        "mode": "snapshot_restore",
        "operation_ref": "patch_profile",
        "path": "/api/profile",
        "method": "PATCH",
        "runtime_response_binding_required": False,
    }]


def test_post_field_action_compiles_snapshot_restore_when_body_names_terminal_field() -> None:
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "o-post-status-restore",
            "risk_family": "idempotency",
            "required_operations": ["post_profile_status"],
            "required_actors": ["actor_a"],
            "required_observers": ["http_response"],
            "property": {
                "operation_ref": "post_profile_status",
                "treatment_actor_ref": "actor_a",
            },
            "cleanup_requirement": {"required": True},
        },
        behavior_ir={
            "operations": [
                {
                    "id": "get_profile_status",
                    "method": "GET",
                    "path": "/api/profile/status",
                    "read_write": "read",
                },
                {
                    "id": "post_profile_status",
                    "method": "POST",
                    "path": "/api/profile/status",
                    "read_write": "write",
                    "request_example": {"status": "disabled"},
                },
            ],
            "actors": [{"id": "actor_a", "role": "public"}],
        },
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert experiment["cleanup_plan"][0]["action"] == "restore_before_snapshot"
    assert experiment["cleanup_plan"][0]["method"] == "POST"


def test_post_numeric_delta_action_compiles_inverse_delta_cleanup() -> None:
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "o-post-delta-restore",
            "risk_family": "idempotency",
            "required_operations": ["post_inventory_adjust"],
            "required_actors": ["actor_a"],
            "required_observers": ["http_response"],
            "property": {
                "operation_ref": "post_inventory_adjust",
                "treatment_actor_ref": "actor_a",
            },
            "cleanup_requirement": {"required": True},
        },
        behavior_ir={
            "operations": [
                {
                    "id": "get_inventory",
                    "method": "GET",
                    "path": "/api/inventory/admin",
                    "read_write": "read",
                },
                {
                    "id": "post_inventory_adjust",
                    "method": "POST",
                    "path": "/api/inventory/admin/adjust",
                    "read_write": "write",
                    "request_example": {
                        "sku": "SKU-1",
                        "delta": 10,
                        "reason": "test adjustment",
                    },
                },
            ],
            "actors": [{"id": "actor_a", "role": "public"}],
        },
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert experiment["cleanup_plan"][0]["action"] == "inverse_delta_compensation"
    assert experiment["cleanup_plan"][0]["delta_field"] == "delta"


def test_patch_numeric_delta_action_compiles_inverse_delta_cleanup() -> None:
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "o-patch-delta-restore",
            "risk_family": "idempotency",
            "required_operations": ["patch_user_balance"],
            "required_actors": ["actor_a"],
            "required_observers": ["http_response"],
            "property": {
                "operation_ref": "patch_user_balance",
                "treatment_actor_ref": "actor_a",
            },
            "cleanup_requirement": {"required": True},
        },
        behavior_ir={
            "operations": [
                {
                    "id": "get_users",
                    "method": "GET",
                    "path": "/api/users/admin/search",
                    "read_write": "read",
                },
                {
                    "id": "patch_user_balance",
                    "method": "PATCH",
                    "path": "/api/users/admin/users/{id}/balance",
                    "read_write": "write",
                    "request_example": {
                        "delta": 100,
                        "reason": "test balance adjustment",
                    },
                },
            ],
            "actors": [{"id": "actor_a", "role": "public"}],
        },
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert experiment["cleanup_plan"][0]["action"] == "inverse_delta_compensation"
    assert experiment["cleanup_plan"][0]["method"] == "PATCH"
    assert experiment["cleanup_plan"][0]["delta_field"] == "delta"


def test_cleanup_template_uses_successful_write_response_binding(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []

    def governed_write(**kwargs):
        calls.append(dict(kwargs))
        if kwargs["operation_phase"] == "experiment_cleanup":
            return {
                "accepted": True,
                "method": "DELETE",
                "path": kwargs["path"],
                "before": {"status": 200, "body": {"resourceId": "r-1"}},
                "write": {"status": 204, "body": {}},
                "after": {"status": 404, "body": {}},
                "audit_path": "sandbox_write_audit.jsonl",
                "audit_record": {"phase": "cleanup", "path": kwargs["path"]},
                "status": "completed",
            }
        return {
            "accepted": True,
            "method": "POST",
            "path": kwargs["path"],
            "before": {"status": 200, "body": []},
            "write": {"status": 201, "body": {"resourceId": "r-1"}},
            "after": {"status": 200, "body": [{"resourceId": "r-1"}]},
            "audit_path": "sandbox_write_audit.jsonl",
            "audit_record": {"phase": "treatment", "path": kwargs["path"]},
            "status": "completed",
        }

    monkeypatch.setattr("ai_test_asset_center.experiment_executor.sandbox_write_allowed", lambda **_kwargs: (True, ""))
    monkeypatch.setattr("ai_test_asset_center.experiment_executor.execute_governed_control_write", governed_write)
    result = execute_one_experiment(
        {
            "experiment_id": "exp-cleanup-template",
            "obligation_id": "obl-cleanup-template",
            "compile_receipt": {"status": "COMPILED"},
            "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
            "control_plan": [],
            "treatment_plan": [{"step_id": "write-1", "actor_ref": "actor_a", "operation_ref": "create_resource"}],
            "cleanup_plan": [{"operation_ref": "delete_resource", "path": "/api/resources/{resourceId}", "method": "DELETE"}],
            "observers": [{"observer_id": "http_response"}],
            "assertions": [{"kind": "validation", "property": {}}],
            "safety_contract": {"governed_write": True},
            "source_refs": [],
        },
        behavior_ir={
            "operations": [
                {"id": "create_resource", "method": "POST", "path": "/api/resources"},
                {"id": "list_resources", "method": "GET", "path": "/api/resources"},
                {"id": "delete_resource", "method": "DELETE", "path": "/api/resources/{resourceId}"},
            ],
            "actors": [{"id": "actor_a", "role": "public"}],
        },
        root=tmp_path,
        project="generic-project",
        base_url="http://127.0.0.1:8080",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign-1",
        execution_id="execution-runtime-binding",
        actor_tokens={},
    )

    assert [call["path"] for call in calls] == ["/api/resources", "/api/resources/r-1"]
    assert [call["observation_path"] for call in calls] == [
        "/api/resources",
        "/api/resources",
    ]
    assert result["cleanup_failures"] == 0


def test_snapshot_restore_cleanup_uses_governed_before_state(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []

    def governed_write(**kwargs):
        calls.append(dict(kwargs))
        if kwargs["operation_phase"] == "experiment_cleanup":
            assert kwargs["body"] == {"balance": 100}
            return {
                "accepted": True,
                "method": "PATCH",
                "path": kwargs["path"],
                "before": {"status": 200, "body": {"balance": 50}},
                "write": {"status": 200, "body": {"balance": 100}},
                "after": {"status": 200, "body": {"balance": 100}},
                "audit_path": "sandbox_write_audit.jsonl",
                "audit_record": {"phase": "cleanup", "path": kwargs["path"]},
                "status": "completed",
            }
        return {
            "accepted": True,
            "method": "PATCH",
            "path": kwargs["path"],
            "before": {"status": 200, "body": {"balance": 100}},
            "write": {"status": 200, "body": {"balance": 50}},
            "after": {"status": 200, "body": {"balance": 50}},
            "audit_path": "sandbox_write_audit.jsonl",
            "audit_record": {"phase": "treatment", "path": kwargs["path"]},
            "status": "completed",
        }

    monkeypatch.setattr("ai_test_asset_center.experiment_executor.sandbox_write_allowed", lambda **_kwargs: (True, ""))
    monkeypatch.setattr("ai_test_asset_center.experiment_executor.execute_governed_control_write", governed_write)
    result = execute_one_experiment(
        {
            "experiment_id": "exp-patch-restore",
            "obligation_id": "obl-patch-restore",
            "compile_receipt": {"status": "COMPILED"},
            "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
            "control_plan": [],
            "treatment_plan": [{"step_id": "write-1", "actor_ref": "actor_a", "operation_ref": "patch_profile"}],
            "cleanup_plan": [{
                "action": "restore_before_snapshot",
                "mode": "snapshot_restore",
                "operation_ref": "patch_profile",
                "path": "/api/profile",
                "method": "PATCH",
            }],
            "observers": [{"observer_id": "http_response"}],
            "assertions": [{"kind": "validation", "property": {}}],
            "safety_contract": {"governed_write": True},
            "source_refs": [],
        },
        behavior_ir={
            "operations": [
                {"id": "get_profile", "method": "GET", "path": "/api/profile"},
                {"id": "patch_profile", "method": "PATCH", "path": "/api/profile"},
            ],
            "actors": [{"id": "actor_a", "role": "public"}],
        },
        root=tmp_path,
        project="generic-project",
        base_url="http://127.0.0.1:8080",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign-1",
        execution_id="execution-snapshot-restore",
        actor_tokens={},
    )

    assert [call["operation_phase"] for call in calls] == [
        "experiment_treatment",
        "experiment_cleanup",
    ]
    assert result["cleanup_failures"] == 0


def test_post_field_action_snapshot_restore_uses_before_field(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []

    def governed_write(**kwargs):
        calls.append(dict(kwargs))
        if kwargs["operation_phase"] == "experiment_cleanup":
            assert kwargs["body"] == {"status": "active"}
            return {
                "accepted": True,
                "method": "POST",
                "path": kwargs["path"],
                "before": {"status": 200, "body": {"status": "disabled"}},
                "write": {"status": 200, "body": {"status": "active"}},
                "after": {"status": 200, "body": {"status": "active"}},
                "audit_path": "sandbox_write_audit.jsonl",
                "audit_record": {"phase": "cleanup", "path": kwargs["path"]},
                "status": "completed",
            }
        return {
            "accepted": True,
            "method": "POST",
            "path": kwargs["path"],
            "before": {"status": 200, "body": {"status": "active"}},
            "write": {"status": 200, "body": {"status": "disabled"}},
            "after": {"status": 200, "body": {"status": "disabled"}},
            "audit_path": "sandbox_write_audit.jsonl",
            "audit_record": {"phase": "treatment", "path": kwargs["path"]},
            "status": "completed",
        }

    monkeypatch.setattr("ai_test_asset_center.experiment_executor.sandbox_write_allowed", lambda **_kwargs: (True, ""))
    monkeypatch.setattr("ai_test_asset_center.experiment_executor.execute_governed_control_write", governed_write)
    result = execute_one_experiment(
        {
            "experiment_id": "exp-post-status-restore",
            "obligation_id": "obl-post-status-restore",
            "compile_receipt": {"status": "COMPILED"},
            "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
            "control_plan": [],
            "treatment_plan": [{"step_id": "write-1", "actor_ref": "actor_a", "operation_ref": "post_profile_status"}],
            "cleanup_plan": [{
                "action": "restore_before_snapshot",
                "mode": "snapshot_restore",
                "operation_ref": "post_profile_status",
                "path": "/api/profile/status",
                "method": "POST",
            }],
            "observers": [{"observer_id": "http_response"}],
            "assertions": [{"kind": "validation", "property": {}}],
            "safety_contract": {"governed_write": True},
            "source_refs": [],
        },
        behavior_ir={
            "operations": [
                {"id": "get_profile_status", "method": "GET", "path": "/api/profile/status"},
                {"id": "post_profile_status", "method": "POST", "path": "/api/profile/status"},
            ],
            "actors": [{"id": "actor_a", "role": "public"}],
        },
        root=tmp_path,
        project="generic-project",
        base_url="http://127.0.0.1:8080",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign-1",
        execution_id="execution-post-status-restore",
        actor_tokens={},
    )

    assert [call["operation_phase"] for call in calls] == [
        "experiment_treatment",
        "experiment_cleanup",
    ]
    assert result["cleanup_failures"] == 0


def test_inverse_delta_cleanup_uses_actual_request_body(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []

    def governed_write(**kwargs):
        calls.append(dict(kwargs))
        if kwargs["operation_phase"] == "experiment_cleanup":
            assert kwargs["body"] == {
                "sku": "SKU-1",
                "delta": -10,
                "reason": "test adjustment",
            }
            return {
                "accepted": True,
                "method": "POST",
                "path": kwargs["path"],
                "before": {"status": 200, "body": {"sku": "SKU-1", "stock": 110}},
                "write": {"status": 200, "body": {"sku": "SKU-1", "stock": 100}},
                "after": {"status": 200, "body": {"sku": "SKU-1", "stock": 100}},
                "audit_path": "sandbox_write_audit.jsonl",
                "audit_record": {"phase": "cleanup", "path": kwargs["path"]},
                "status": "completed",
            }
        return {
            "accepted": True,
            "method": "POST",
            "path": kwargs["path"],
            "before": {"status": 200, "body": {"sku": "SKU-1", "stock": 100}},
            "write": {"status": 200, "body": {"sku": "SKU-1", "stock": 110}},
            "after": {"status": 200, "body": {"sku": "SKU-1", "stock": 110}},
            "audit_path": "sandbox_write_audit.jsonl",
            "audit_record": {"phase": "treatment", "path": kwargs["path"]},
            "status": "completed",
        }

    monkeypatch.setattr("ai_test_asset_center.experiment_executor.sandbox_write_allowed", lambda **_kwargs: (True, ""))
    monkeypatch.setattr("ai_test_asset_center.experiment_executor.execute_governed_control_write", governed_write)
    result = execute_one_experiment(
        {
            "experiment_id": "exp-delta-restore",
            "obligation_id": "obl-delta-restore",
            "compile_receipt": {"status": "COMPILED"},
            "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "write-1",
                "actor_ref": "actor_a",
                "operation_ref": "post_inventory_adjust",
                "body": {
                    "sku": "SKU-1",
                    "delta": 10,
                    "reason": "test adjustment",
                },
            }],
            "cleanup_plan": [{
                "action": "inverse_delta_compensation",
                "mode": "delta_inverse",
                "operation_ref": "post_inventory_adjust",
                "path": "/api/inventory/admin/adjust",
                "method": "POST",
                "delta_field": "delta",
            }],
            "observers": [{"observer_id": "http_response"}],
            "assertions": [{"kind": "validation", "property": {}}],
            "safety_contract": {"governed_write": True},
            "source_refs": [],
        },
        behavior_ir={
            "operations": [
                {"id": "get_inventory", "method": "GET", "path": "/api/inventory/admin"},
                {"id": "post_inventory_adjust", "method": "POST", "path": "/api/inventory/admin/adjust"},
            ],
            "actors": [{"id": "actor_a", "role": "public"}],
        },
        root=tmp_path,
        project="generic-project",
        base_url="http://127.0.0.1:8080",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign-1",
        execution_id="execution-delta-restore",
        actor_tokens={},
    )

    assert [call["operation_phase"] for call in calls] == [
        "experiment_treatment",
        "experiment_cleanup",
    ]
    assert result["cleanup_failures"] == 0


def test_accepted_write_with_unchanged_observer_does_not_require_cleanup(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []

    def governed_write(**kwargs):
        calls.append(dict(kwargs))
        if kwargs["operation_phase"] == "experiment_cleanup":
            raise AssertionError("unchanged accepted write must not execute cleanup")
        return {
            "accepted": True,
            "method": "POST",
            "path": kwargs["path"],
            "before": {"status": 200, "body": {"id": "u-1", "status": "DISABLED"}},
            "write": {"status": 200, "body": {"id": "u-1", "status": "DISABLED"}},
            "after": {"status": 200, "body": {"id": "u-1", "status": "DISABLED"}},
            "audit_path": "sandbox_write_audit.jsonl",
            "audit_record": {"phase": "treatment", "path": kwargs["path"]},
            "status": "completed",
        }

    monkeypatch.setattr("ai_test_asset_center.experiment_executor.sandbox_write_allowed", lambda **_kwargs: (True, ""))
    monkeypatch.setattr("ai_test_asset_center.experiment_executor.execute_governed_control_write", governed_write)
    result = execute_one_experiment(
        {
            "experiment_id": "exp-unchanged-write",
            "obligation_id": "obl-unchanged-write",
            "compile_receipt": {"status": "COMPILED"},
            "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "write-1",
                "actor_ref": "actor_a",
                "operation_ref": "post_status",
                "body": {"status": "DISABLED"},
            }],
            "cleanup_plan": [{
                "action": "restore_before_snapshot",
                "mode": "snapshot_restore",
                "operation_ref": "post_status",
                "path": "/api/users/u-1/status",
                "method": "POST",
            }],
            "observers": [{"observer_id": "http_response"}],
            "assertions": [{"kind": "validation", "property": {}}],
            "safety_contract": {"governed_write": True},
            "source_refs": [],
        },
        behavior_ir={
            "operations": [
                {"id": "get_status", "method": "GET", "path": "/api/users/u-1/status"},
                {"id": "post_status", "method": "POST", "path": "/api/users/u-1/status"},
            ],
            "actors": [{"id": "actor_a", "role": "public"}],
        },
        root=tmp_path,
        project="generic-project",
        base_url="http://127.0.0.1:8080",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign-1",
        execution_id="execution-unchanged-write",
        actor_tokens={},
    )

    assert [call["operation_phase"] for call in calls] == ["experiment_treatment"]
    assert result["cleanup_failures"] == 0
    cleanup_receipt = next(
        receipt for receipt in result["contract_evidence_receipts"]
        if receipt["kind"] == "cleanup"
    )
    assert cleanup_receipt["status"] == "NOT_REQUIRED"
    assert cleanup_receipt["evidence"]["reason_code"] == "ACCEPTED_WRITE_STATE_UNCHANGED"


def test_accepted_write_with_only_server_managed_change_does_not_require_cleanup(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []

    def governed_write(**kwargs):
        calls.append(dict(kwargs))
        if kwargs["operation_phase"] == "experiment_cleanup":
            raise AssertionError("server-managed-only change must not execute cleanup")
        return {
            "accepted": True,
            "method": "POST",
            "path": kwargs["path"],
            "before": {
                "status": 200,
                "body": {
                    "sku": "SKU-1",
                    "available_qty": 15,
                    "locked_qty": 0,
                    "updated_at": "2026-01-01T00:00:00Z",
                },
            },
            "write": {
                "status": 200,
                "body": {
                    "sku": "SKU-1",
                    "available_qty": 15,
                    "locked_qty": 0,
                    "updated_at": "2026-01-01T00:00:01Z",
                },
            },
            "after": {
                "status": 200,
                "body": {
                    "sku": "SKU-1",
                    "available_qty": 15,
                    "locked_qty": 0,
                    "updated_at": "2026-01-01T00:00:01Z",
                },
            },
            "audit_path": "sandbox_write_audit.jsonl",
            "audit_record": {"phase": "treatment", "path": kwargs["path"]},
            "status": "completed",
        }

    monkeypatch.setattr("ai_test_asset_center.experiment_executor.sandbox_write_allowed", lambda **_kwargs: (True, ""))
    monkeypatch.setattr("ai_test_asset_center.experiment_executor.execute_governed_control_write", governed_write)
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor._declared_observation_path",
        lambda *_args, **_kwargs: "/api/inventory/SKU-1",
    )
    result = execute_one_experiment(
        {
            "experiment_id": "exp-server-managed-only-write",
            "obligation_id": "obl-server-managed-only-write",
            "compile_receipt": {"status": "COMPILED"},
            "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "write-1",
                "actor_ref": "actor_a",
                "operation_ref": "post_inventory_adjust",
                "body": {
                    "sku": "SKU-1",
                    "delta": 0,
                    "reason": "noop",
                },
            }],
            "cleanup_plan": [{
                "action": "inverse_delta_compensation",
                "mode": "delta_inverse",
                "operation_ref": "post_inventory_adjust",
                "path": "/api/inventory/admin/adjust",
                "method": "POST",
                "delta_field": "delta",
            }],
            "observers": [{"observer_id": "http_response"}],
            "assertions": [{"kind": "validation", "property": {}}],
            "safety_contract": {"governed_write": True},
            "source_refs": [],
        },
        behavior_ir={
            "operations": [
                {
                    "id": "get_inventory",
                    "method": "GET",
                    "path": "/api/inventory/{sku}",
                    "read_write": "read",
                },
                {
                    "id": "post_inventory_adjust",
                    "method": "POST",
                    "path": "/api/inventory/admin/adjust",
                    "read_write": "write",
                },
            ],
            "actors": [{"id": "actor_a", "role": "public"}],
        },
        root=tmp_path,
        project="generic-project",
        base_url="http://127.0.0.1:8080",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign-1",
        execution_id="execution-server-managed-only-write",
        actor_tokens={},
    )

    assert [call["operation_phase"] for call in calls] == ["experiment_treatment"]
    assert result["cleanup_failures"] == 0
    cleanup_receipt = next(
        receipt for receipt in result["contract_evidence_receipts"]
        if receipt["kind"] == "cleanup"
    )
    assert cleanup_receipt["status"] == "NOT_REQUIRED"
    assert cleanup_receipt["evidence"]["reason_code"] == "ACCEPTED_WRITE_STATE_UNCHANGED"


def test_pre_transport_write_block_does_not_count_as_cleanup_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.sandbox_write_allowed",
        lambda **_kwargs: (False, "execution_mode_read_only"),
    )

    result = execute_one_experiment(
        {
            "experiment_id": "exp-read-only-write",
            "obligation_id": "obl-read-only-write",
            "compile_receipt": {"status": "COMPILED"},
            "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
            "control_plan": [{
                "step_id": "control-1",
                "actor_ref": "actor_a",
                "operation_ref": "create_resource",
            }],
            "treatment_plan": [],
            "cleanup_plan": [{
                "operation_ref": "delete_resource",
                "path": "/api/resources/{resourceId}",
                "method": "DELETE",
            }],
            "observers": [{"observer_id": "http_response"}],
            "assertions": [{"kind": "validation", "property": {}}],
            "safety_contract": {"governed_write": True},
            "source_refs": [],
        },
        behavior_ir={
            "operations": [
                {"id": "create_resource", "method": "POST", "path": "/api/resources"},
                {"id": "list_resources", "method": "GET", "path": "/api/resources"},
                {"id": "delete_resource", "method": "DELETE", "path": "/api/resources/{resourceId}"},
            ],
            "actors": [{"id": "actor_a", "role": "public"}],
        },
        root=tmp_path,
        project="generic-project",
        base_url="http://127.0.0.1:8080",
        runtime_contract={"environment_type": "test", "execution_mode": "safe_read_only"},
        campaign_id="campaign-1",
        execution_id="execution-read-only-write",
        actor_tokens={},
    )

    assert result["cleanup_failures"] == 0
    cleanup_receipt = next(
        receipt
        for receipt in result["contract_evidence_receipts"]
        if receipt["kind"] == "cleanup"
    )
    assert cleanup_receipt["status"] == "BLOCKED"
    assert cleanup_receipt["evidence"]["reason_code"] == "NO_WRITE_REACHED_TRANSPORT"
    assert cleanup_receipt["evidence"]["write_block_reasons"] == [
        "execution_mode_read_only"
    ]


def test_cleanup_http_2xx_without_restoration_is_not_completion_proof() -> None:
    original = {
        "accepted": True,
        "method": "POST",
        "path": "/resources",
        "before": {"status": 200, "body": []},
        "write": {"status": 201, "body": {"id": "r-1"}},
        "after": {"status": 200, "body": [{"id": "r-1"}]},
        "audit_path": "sandbox_write_audit.jsonl",
        "audit_record": {"phase": "treatment", "path": "/resources"},
    }
    no_op_cleanup = {
        "accepted": True,
        "method": "POST",
        "path": "/resources/r-1/archive",
        "before": {"status": 200, "body": {"id": "r-1", "active": True}},
        "write": {"status": 204, "body": {}},
        "after": {"status": 200, "body": {"id": "r-1", "active": True}},
        "audit_path": "sandbox_write_audit.jsonl",
        "audit_record": {
            "phase": "cleanup",
            "path": "/resources/r-1/archive",
        },
    }

    assert _cleanup_restores_governed_write(original, no_op_cleanup) is False


def test_identity_bound_action_compensates_owned_created_resource() -> None:
    original = {
        "accepted": True,
        "method": "POST",
        "path": "/resources",
        "before": {"status": 200, "body": []},
        "write": {"status": 201, "body": {"id": "r-1", "status": "OPEN"}},
        "after": {"status": 200, "body": [{"id": "r-1", "status": "OPEN"}]},
        "audit_path": "sandbox_write_audit.jsonl",
        "audit_record": {"phase": "treatment", "path": "/resources"},
    }
    compensated_cleanup = {
        "accepted": True,
        "method": "POST",
        "path": "/resources/r-1/cancel",
        "before": {"status": 200, "body": {"id": "r-1", "status": "OPEN"}},
        "write": {"status": 200, "body": {"id": "r-1", "status": "CANCELLED"}},
        "after": {"status": 200, "body": {"id": "r-1", "status": "CANCELLED"}},
        "audit_path": "sandbox_write_audit.jsonl",
        "audit_record": {
            "phase": "cleanup",
            "path": "/resources/r-1/cancel",
        },
    }

    assert _cleanup_restores_governed_write(original, compensated_cleanup) is True


def test_cleanup_restoration_projects_target_entity_fields_from_collection_snapshot() -> None:
    original = {
        "accepted": True,
        "method": "PATCH",
        "path": "/api/users/u-1/balance",
        "before": {"status": 200, "body": [
            {"id": "u-1", "balance": "100.00", "status": "ACTIVE"},
            {"id": "u-2", "balance": "50.00", "status": "ACTIVE"},
        ]},
        "write": {"status": 200, "body": {"id": "u-1", "balance": "40.00"}},
        "after": {"status": 200, "body": [
            {"id": "u-2", "balance": "50.00", "status": "ACTIVE"},
            {"id": "u-1", "balance": "40.00", "status": "ACTIVE"},
        ]},
        "audit_path": "sandbox_write_audit.jsonl",
        "audit_record": {"phase": "treatment"},
    }
    cleanup = {
        "accepted": True,
        "method": "PATCH",
        "path": "/api/users/u-1/balance",
        "before": {"status": 200, "body": [
            {"id": "u-2", "balance": "50.00", "status": "ACTIVE"},
            {"id": "u-1", "balance": "40.00", "status": "ACTIVE"},
        ]},
        "write": {"status": 200, "body": {"id": "u-1", "balance": "100.00"}},
        "after": {"status": 200, "body": [
            {"id": "u-2", "balance": "50.00", "status": "ACTIVE"},
            {"id": "u-1", "balance": "100.00", "status": "ACTIVE"},
        ]},
        "audit_path": "sandbox_write_audit.jsonl",
        "audit_record": {"phase": "cleanup"},
    }

    assert _cleanup_restores_governed_write(original, cleanup) is True


def test_cleanup_restoration_fails_when_target_field_not_restored() -> None:
    original = {
        "accepted": True,
        "method": "PATCH",
        "path": "/api/users/u-1/balance",
        "before": {"status": 200, "body": [{"id": "u-1", "balance": "100.00"}]},
        "write": {"status": 200, "body": {"id": "u-1", "balance": "40.00"}},
        "after": {"status": 200, "body": [{"id": "u-1", "balance": "40.00"}]},
        "audit_path": "sandbox_write_audit.jsonl",
        "audit_record": {"phase": "treatment"},
    }
    cleanup = {
        "accepted": True,
        "method": "PATCH",
        "path": "/api/users/u-1/balance",
        "before": {"status": 200, "body": [{"id": "u-1", "balance": "40.00"}]},
        "write": {"status": 200, "body": {"id": "u-1", "balance": "90.00"}},
        "after": {"status": 200, "body": [{"id": "u-1", "balance": "90.00"}]},
        "audit_path": "sandbox_write_audit.jsonl",
        "audit_record": {"phase": "cleanup"},
    }

    assert _cleanup_restores_governed_write(original, cleanup) is False


def test_cleanup_restoration_ignores_server_managed_timestamps_for_delta_restore() -> None:
    original = {
        "accepted": True,
        "method": "POST",
        "path": "/api/inventory/admin/adjust",
        "before": {"status": 200, "body": {"sku": "SKU-1", "available_qty": 5, "updated_at": "t1"}},
        "write": {"status": 200, "body": {"sku": "SKU-1", "available_qty": 15, "updated_at": "t2"}},
        "after": {"status": 200, "body": {"sku": "SKU-1", "available_qty": 15, "updated_at": "t2"}},
        "audit_path": "sandbox_write_audit.jsonl",
        "audit_record": {"phase": "treatment"},
    }
    cleanup = {
        "accepted": True,
        "method": "POST",
        "path": "/api/inventory/admin/adjust",
        "before": {"status": 200, "body": {"sku": "SKU-1", "available_qty": 15, "updated_at": "t2"}},
        "write": {"status": 200, "body": {"sku": "SKU-1", "available_qty": 5, "updated_at": "t3"}},
        "after": {"status": 200, "body": {"sku": "SKU-1", "available_qty": 5, "updated_at": "t3"}},
        "audit_path": "sandbox_write_audit.jsonl",
        "audit_record": {"phase": "cleanup"},
    }

    assert _cleanup_restores_governed_write(original, cleanup) is True


def test_runtime_read_binding_materializes_same_resource_before_control_and_treatment(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[tuple[str, str]] = []

    def http_request(method, url, *, token="", body=None):
        path = url.removeprefix("http://127.0.0.1:8080")
        calls.append((method, path))
        if path == "/resources":
            return {"status": 200, "body": [{"id": "r-1"}], "headers": {}}
        if path == "/resources/r-1":
            return {"status": 200, "body": {"id": "r-1"}, "headers": {}}
        raise AssertionError(f"unexpected request: {method} {path}")

    monkeypatch.setattr("ai_test_asset_center.experiment_executor._http_request", http_request)
    result = execute_one_experiment(
        {
            "experiment_id": "exp-runtime-binding",
            "obligation_id": "obl-runtime-binding",
            "compile_receipt": {"status": "COMPILED"},
            "fixture_dag": {
                "status": "READY",
                "nodes": [{
                    "node_id": "bind-id",
                    "kind": "runtime_read_binding",
                    "target": "id",
                    "resolver_operations": [{
                        "operation_ref": "list_resources",
                        "method": "GET",
                        "path": "/resources",
                    }],
                    "constructible": True,
                }],
                "setup_order": ["bind-id"],
            },
            "binding_plan": [{
                "target": "id",
                "status": "runtime_resolvable",
                "source_priority": "same_actor_list_read",
                "resolver_operations": [{
                    "operation_ref": "list_resources",
                    "method": "GET",
                    "path": "/resources",
                }],
            }],
            "control_plan": [{
                "step_id": "control-1",
                "actor_ref": "owner",
                "operation_ref": "get_resource",
            }],
            "treatment_plan": [{
                "step_id": "treatment-1",
                "actor_ref": "viewer",
                "operation_ref": "get_resource",
            }],
            "cleanup_plan": [],
            "observers": [
                {"observer_id": "http_response"},
                {"observer_id": "actor_identity"},
                {"observer_id": "authorization_comparison"},
            ],
            "assertions": [{"kind": "authorization", "property": {}}],
            "safety_contract": {"governed_write": False},
            "source_refs": [{"source_id": "api-contract"}],
        },
        behavior_ir={
            "operations": [
                {"id": "list_resources", "method": "GET", "path": "/resources"},
                {"id": "get_resource", "method": "GET", "path": "/resources/{id}"},
            ],
            "actors": [
                {"id": "owner", "role": "public"},
                {"id": "viewer", "role": "public"},
            ],
        },
        root=tmp_path,
        project="generic-project",
        base_url="http://127.0.0.1:8080",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign-1",
        execution_id="execution-cleanup-template",
        actor_tokens={},
    )

    assert calls == [
        ("GET", "/resources"),
        ("GET", "/resources/r-1"),
        ("GET", "/resources/r-1"),
    ]
    assert result["status"] == "EXECUTED"
    binding_receipt = result["binding_materialization_receipts"][0]
    assert binding_receipt["status"] == "BOUND"
    assert binding_receipt["target"] == "id"
    assert binding_receipt["source_priority"] == "same_actor_list_read"
    assert binding_receipt["resolver_path"] == "/resources"
    assert binding_receipt["value_fingerprint"]
    assert "value" not in binding_receipt
    assert result["finding"]["reproduction"]["path"] == "/resources/r-1"


def test_runtime_read_binding_materializes_request_body_before_governed_write(
    monkeypatch,
    tmp_path,
) -> None:
    governed_bodies: list[dict] = []

    def http_request(method, url, *, token="", body=None):
        del token, body
        path = url.removeprefix("http://127.0.0.1:8080")
        if method == "GET" and path == "/api/orders":
            return {"status": 200, "body": [{"id": "order-1"}], "headers": {}}
        raise AssertionError(f"unexpected request: {method} {path}")

    def governed_write(**kwargs):
        governed_bodies.append(dict(kwargs["body"]))
        unchanged = {"sku": "SKU-1", "available": 5}
        return {
            "accepted": True,
            "method": kwargs["method"],
            "path": kwargs["path"],
            "before": {"status": 200, "body": unchanged},
            "write": {"status": 200, "body": {"accepted": True}},
            "after": {"status": 200, "body": unchanged},
            "audit_path": "sandbox_write_audit.jsonl",
            "audit_record": {"phase": kwargs["operation_phase"]},
            "status": "completed",
        }

    monkeypatch.setattr("ai_test_asset_center.experiment_executor._http_request", http_request)
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.execute_governed_control_write",
        governed_write,
    )

    result = execute_one_experiment(
        {
            "experiment_id": "exp-body-runtime-binding",
            "obligation_id": "obl-body-runtime-binding",
            "compile_receipt": {"status": "COMPILED"},
            "fixture_dag": {
                "status": "READY",
                "nodes": [{
                    "node_id": "bind-order-id",
                    "kind": "runtime_read_binding",
                    "target": "order_id",
                    "resolver_operations": [{
                        "operation_ref": "list_orders",
                        "method": "GET",
                        "path": "/api/orders",
                    }],
                    "constructible": True,
                }],
                "setup_order": ["bind-order-id"],
            },
            "binding_plan": [{
                "target": "order_id",
                "target_path": "/{order_id}",
                "body_template_paths": ["orderId"],
                "status": "runtime_resolvable",
                "source_priority": "same_actor_list_read",
                "resolver_operations": [{
                    "operation_ref": "list_orders",
                    "method": "GET",
                    "path": "/api/orders",
                }],
            }],
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "treatment-1",
                "actor_ref": "operator",
                "operation_ref": "reserve_inventory",
            }],
            "cleanup_plan": [{
                "operation_ref": "release_inventory",
                "method": "POST",
                "path": "/api/inventory/release",
            }],
            "observers": [{"observer_id": "http_response"}],
            "assertions": [{"kind": "validation", "property": {}}],
            "safety_contract": {"governed_write": True},
            "source_refs": [{"source_id": "api-contract"}],
        },
        behavior_ir={
            "operations": [
                {"id": "list_orders", "method": "GET", "path": "/api/orders"},
                {
                    "id": "reserve_inventory",
                    "method": "POST",
                    "path": "/api/inventory/reserve",
                    "request_example": {
                        "sku": "SKU-1",
                        "qty": 1,
                        "orderId": "<order_id>",
                    },
                },
                {
                    "id": "read_inventory",
                    "method": "GET",
                    "path": "/api/inventory/{sku}",
                },
                {
                    "id": "release_inventory",
                    "method": "POST",
                    "path": "/api/inventory/release",
                },
            ],
            "actors": [{"id": "operator", "role": "public"}],
        },
        root=tmp_path,
        project="generic-project",
        base_url="http://127.0.0.1:8080",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign-1",
        execution_id="execution-body-runtime-binding",
        actor_tokens={},
    )

    assert governed_bodies == [{
        "sku": "SKU-1",
        "qty": 1,
        "orderId": "order-1",
    }]
    assert result["binding_materialization_receipts"][0]["status"] == "BOUND"


def test_source_declared_compensation_reuses_materialized_original_request(
    monkeypatch,
    tmp_path,
) -> None:
    governed_calls: list[dict] = []

    def http_request(method, url, *, token="", body=None):
        del token, body
        path = url.removeprefix("http://127.0.0.1:8080")
        if method == "GET" and path == "/api/resources":
            return {"status": 200, "body": [{"id": "resource-1"}], "headers": {}}
        raise AssertionError(f"unexpected request: {method} {path}")

    def governed_write(**kwargs):
        governed_calls.append(dict(kwargs))
        if kwargs["operation_phase"] == "experiment_control":
            before = {"resourceId": "resource-1", "available": 5}
            after = {"resourceId": "resource-1", "available": 4}
        elif kwargs["operation_phase"] == "experiment_treatment":
            before = {"resourceId": "resource-1", "available": 4}
            after = {"resourceId": "resource-1", "available": 3}
        else:
            assert kwargs["operation_phase"] == "experiment_cleanup"
            cleanup_index = sum(
                call["operation_phase"] == "experiment_cleanup"
                for call in governed_calls
            )
            before = {
                "resourceId": "resource-1",
                "available": 3 if cleanup_index == 1 else 4,
            }
            after = {
                "resourceId": "resource-1",
                "available": 4 if cleanup_index == 1 else 5,
            }
        return {
            "accepted": True,
            "method": kwargs["method"],
            "path": kwargs["path"],
            "before": {"status": 200, "body": before},
            "write": {"status": 200, "body": after},
            "after": {"status": 200, "body": after},
            "audit_path": "sandbox_write_audit.jsonl",
            "audit_record": {"phase": kwargs["operation_phase"]},
            "status": "completed",
        }

    monkeypatch.setattr("ai_test_asset_center.experiment_executor._http_request", http_request)
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.execute_governed_control_write",
        governed_write,
    )
    request_example = {"resourceId": "<resource_id>", "units": 1}
    result = execute_one_experiment(
        {
            "experiment_id": "exp-source-compensation",
            "obligation_id": "obl-source-compensation",
            "compile_receipt": {"status": "COMPILED"},
            "fixture_dag": {
                "status": "READY",
                "nodes": [{
                    "node_id": "bind-resource-id",
                    "kind": "runtime_read_binding",
                    "target": "resource_id",
                    "resolver_operations": [{
                        "operation_ref": "list_resources",
                        "method": "GET",
                        "path": "/api/resources",
                    }],
                    "constructible": True,
                }],
                "setup_order": ["bind-resource-id"],
            },
            "binding_plan": [{
                "target": "resource_id",
                "target_path": "/{resource_id}",
                "body_template_paths": ["resourceId"],
                "status": "runtime_resolvable",
                "source_priority": "same_actor_list_read",
                "resolver_operations": [{
                    "operation_ref": "list_resources",
                    "method": "GET",
                    "path": "/api/resources",
                }],
            }],
            "control_plan": [{
                "step_id": "control-1",
                "actor_ref": "operator",
                "operation_ref": "reserve_capacity",
            }],
            "treatment_plan": [{
                "step_id": "treatment-1",
                "actor_ref": "operator",
                "operation_ref": "reserve_capacity",
            }],
            "cleanup_plan": [{
                "action": "source_declared_compensation",
                "mode": "reverse_order",
                "operation_ref": "release_capacity",
                "compensates_operation_ref": "reserve_capacity",
                "method": "POST",
                "path": "/api/capacity/release",
                "body_from_original_request": True,
            }],
            "observers": [{"observer_id": "http_response"}],
            "assertions": [{"kind": "validation", "property": {}}],
            "safety_contract": {"governed_write": True},
            "source_refs": [{"source_id": "api-contract"}],
        },
        behavior_ir={
            "operations": [
                {"id": "list_resources", "method": "GET", "path": "/api/resources"},
                {
                    "id": "reserve_capacity",
                    "method": "POST",
                    "path": "/api/capacity/reserve",
                    "request_example": request_example,
                },
                {
                    "id": "release_capacity",
                    "method": "POST",
                    "path": "/api/capacity/release",
                    "request_example": request_example,
                },
                {
                    "id": "read_capacity",
                    "method": "GET",
                    "path": "/api/capacity/{resourceId}",
                },
            ],
            "actors": [{"id": "operator", "role": "public"}],
        },
        root=tmp_path,
        project="generic-project",
        base_url="http://127.0.0.1:8080",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign-1",
        execution_id="execution-source-compensation",
        actor_tokens={},
    )

    assert [call["path"] for call in governed_calls] == [
        "/api/capacity/reserve",
        "/api/capacity/reserve",
        "/api/capacity/release",
        "/api/capacity/release",
    ]
    assert [call["body"] for call in governed_calls] == [
        {"resourceId": "resource-1", "units": 1},
        {"resourceId": "resource-1", "units": 1},
        {"resourceId": "resource-1", "units": 1},
        {"resourceId": "resource-1", "units": 1},
    ]
    assert result["cleanup_failures"] == 0


def test_failed_positive_control_cannot_produce_violation_candidate(
    monkeypatch,
    tmp_path,
) -> None:
    def http_request(method, url, *, token="", body=None):
        del method, token, body
        if url.endswith("/control"):
            return {"status": 500, "body": {"error": "failed"}, "headers": {}}
        if url.endswith("/treatment"):
            return {"status": 200, "body": {"accepted": True}, "headers": {}}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor._http_request",
        http_request,
    )
    result = execute_one_experiment(
        {
            "experiment_id": "exp-failed-control",
            "obligation_id": "obl-failed-control",
            "compile_receipt": {"status": "COMPILED"},
            "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
            "control_plan": [{
                "step_id": "control-1",
                "actor_ref": "actor-a",
                "operation_ref": "control-op",
            }],
            "treatment_plan": [{
                "step_id": "treatment-1",
                "actor_ref": "actor-a",
                "operation_ref": "treatment-op",
            }],
            "cleanup_plan": [],
            "observers": [{"observer_id": "http_response"}],
            "assertions": [{
                "assertion_id": "assert-status",
                "kind": "http_status",
                "expected": 403,
            }],
            "safety_contract": {"governed_write": False},
            "source_refs": [{
                "kind": "api_contract",
                "source_id": "generic-api",
                "locator": "GET /treatment",
            }],
        },
        behavior_ir={
            "operations": [
                {"id": "control-op", "method": "GET", "path": "/control"},
                {"id": "treatment-op", "method": "GET", "path": "/treatment"},
            ],
            "actors": [{"id": "actor-a", "role": "public"}],
        },
        root=tmp_path,
        project="generic-project",
        base_url="http://127.0.0.1:8080",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign-1",
        execution_id="execution-failed-control",
        actor_tokens={},
    )

    assert result["status"] == "BLOCKED"
    assert result["finding"] is None
    assert "CONTROL_RECEIPT_BLOCKED:control-1" in result["oracle_verdict"][
        "missing_requirements"
    ]


def test_empty_runtime_read_uses_governed_fixture_and_cleans_it_after_experiment(
    monkeypatch,
    tmp_path,
) -> None:
    read_calls: list[tuple[str, str]] = []
    write_calls: list[dict] = []

    def http_request(method, url, *, token="", body=None):
        path = url.removeprefix("http://127.0.0.1:8080")
        read_calls.append((method, path))
        if path == "/api/resources":
            return {"status": 200, "body": [], "headers": {}}
        if path == "/api/owners":
            return {"status": 200, "body": [{"id": "owner-1"}], "headers": {}}
        if path == "/api/projections/resource/r-1":
            return {"status": 200, "body": {"id": "r-1"}, "headers": {}}
        raise AssertionError(f"unexpected request: {method} {path}")

    def governed_write(**kwargs):
        write_calls.append(dict(kwargs))
        if kwargs["operation_phase"] == "experiment_fixture_setup":
            assert kwargs["path"] == "/api/resources"
            assert kwargs["body"] == {"ownerId": "owner-1", "name": "source-name"}
            assert kwargs["actor_identity"] == "fixture_creator"
            assert kwargs["actor_token"] == "fixture-token"
            return {
                "accepted": True,
                "method": "POST",
                "path": kwargs["path"],
                "before": {"status": 200, "body": []},
                "write": {
                    "status": 201,
                    "body": {"items": [{"sku": "unrelated-sku"}], "id": "r-1"},
                },
                "after": {"status": 200, "body": [{"id": "r-1"}]},
                "audit_path": "sandbox_write_audit.jsonl",
                "audit_record": {"phase": "fixture_setup", "path": kwargs["path"]},
                "status": "completed",
            }
        assert kwargs["operation_phase"] == "experiment_fixture_cleanup"
        assert kwargs["path"] == "/api/resources/r-1/archive"
        return {
            "accepted": True,
            "method": "POST",
            "path": kwargs["path"],
            "before": {"status": 200, "body": {"id": "r-1"}},
            "write": {"status": 204, "body": {}},
            "after": {"status": 404, "body": {}},
            "audit_path": "sandbox_write_audit.jsonl",
            "audit_record": {"phase": "fixture_cleanup", "path": kwargs["path"]},
            "status": "completed",
        }

    monkeypatch.setattr("ai_test_asset_center.experiment_executor._http_request", http_request)
    monkeypatch.setattr("ai_test_asset_center.experiment_executor.sandbox_write_allowed", lambda **_kwargs: (True, ""))
    monkeypatch.setattr("ai_test_asset_center.experiment_executor.execute_governed_control_write", governed_write)
    result = execute_one_experiment(
        {
            "experiment_id": "exp-fixture-binding",
            "obligation_id": "obl-fixture-binding",
            "compile_receipt": {"status": "COMPILED"},
            "fixture_dag": {
                "status": "READY",
                "nodes": [{
                    "node_id": "bind-resource",
                    "kind": "runtime_read_binding",
                    "target": "resourceId",
                    "resolver_operations": [{
                        "operation_ref": "list_resources",
                        "method": "GET",
                        "path": "/api/resources",
                    }],
                    "constructible": True,
                }],
                "setup_order": ["bind-resource"],
            },
            "binding_plan": [{
                "target": "resourceId",
                "target_path": "/api/projections/resource/{resourceId}",
                "status": "runtime_resolvable",
                "source_priority": "same_actor_list_read",
                "resolver_operations": [{
                    "operation_ref": "list_resources",
                    "method": "GET",
                    "path": "/api/resources",
                }],
                "fixture_setup": {
                    "operation_ref": "create_resource",
                    "method": "POST",
                    "path": "/api/resources",
                    "actor_refs": ["fixture_actor"],
                    "body_template": {"ownerId": "<owner_id>", "name": "source-name"},
                    "body_bindings": [{
                        "target": "ownerId",
                        "template_token": "owner_id",
                        "resolver_operations": [{
                            "operation_ref": "list_owners",
                            "method": "GET",
                            "path": "/api/owners",
                        }],
                    }],
                    "cleanup_operations": [{
                        "operation_ref": "archive_resource",
                        "method": "POST",
                        "path": "/api/resources/{id}/archive",
                    }],
                },
            }],
            "control_plan": [{
                "step_id": "control-1",
                "actor_ref": "owner",
                "operation_ref": "read_projection",
            }],
            "treatment_plan": [{
                "step_id": "treatment-1",
                "actor_ref": "viewer",
                "operation_ref": "read_projection",
            }],
            "cleanup_plan": [],
            "observers": [
                {"observer_id": "http_response"},
                {"observer_id": "actor_identity"},
                {"observer_id": "authorization_comparison"},
            ],
            "assertions": [{"kind": "authorization", "property": {}}],
            "safety_contract": {"governed_write": False},
            "source_refs": [{"source_id": "api-contract"}],
        },
        behavior_ir={
            "operations": [
                {"id": "list_resources", "method": "GET", "path": "/api/resources"},
                {"id": "list_owners", "method": "GET", "path": "/api/owners"},
                {"id": "create_resource", "method": "POST", "path": "/api/resources"},
                {"id": "archive_resource", "method": "POST", "path": "/api/resources/{id}/archive"},
                {"id": "read_projection", "method": "GET", "path": "/api/projections/resource/{resourceId}"},
            ],
            "actors": [
                {"id": "owner", "role": "public"},
                {"id": "viewer", "role": "public"},
                {"id": "fixture_actor", "role": "fixture_creator"},
            ],
        },
        root=tmp_path,
        project="generic-project",
        base_url="http://127.0.0.1:8080",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign-1",
        execution_id="execution-fixture-binding",
        actor_tokens={"fixture_creator": "fixture-token"},
    )

    assert read_calls == [
        ("GET", "/api/resources"),
        ("GET", "/api/owners"),
        ("GET", "/api/projections/resource/r-1"),
        ("GET", "/api/projections/resource/r-1"),
    ]
    assert [call["operation_phase"] for call in write_calls] == [
        "experiment_fixture_setup",
        "experiment_fixture_cleanup",
    ]
    assert [step["phase"] for step in result["steps"]] == [
        "binding_materialization",
        "binding_materialization_dependency",
        "fixture_setup",
        "control",
        "treatment",
        "fixture_cleanup",
    ]
    assert result["status"] == "EXECUTED"
    assert result["cleanup_failures"] == 0
    receipt = result["binding_materialization_receipts"][0]
    assert receipt["status"] == "BOUND"
    assert receipt["source_priority"] == "experiment_setup_response"
    assert receipt["fixture_setup_status"] == "completed"
    assert receipt["fixture_cleanup_status"] == "completed"
    assert "value" not in receipt


def test_campaign_reset_preserves_cleanup_failures() -> None:
    out = ObservedProductScanExecutor(
        workspace_root=Path("."),
        operational_metrics_collector=lambda **kwargs: {},
    ).finalize_after_cleanup(
        scan_output={
            "findings": [],
            "candidates": [],
            "operational_metrics": {"cleanup_failures": 3, "dirty_test_environments": 1},
            "pipeline_health": {"status": "DEGRADED", "cleanup_failure_count": 3},
        },
        cleanup_receipt={
            "status": "SUCCEEDED",
            "dirty_environment": False,
            "audit_receipt_id": "a1",
            "after_cleanup_observation_ref": "state:clean",
        },
    )
    assert out["operational_metrics"]["cleanup_failures"] == 3
    assert out["operational_metrics"]["environment_restored"] is True
    assert out["campaign_cleanup_finalization"]["cleanup_failures_preserved"] is True
    assert out["pipeline_health"]["cleanup_failure_count"] == 3
    assert out["pipeline_health"]["environment_restored"] is True


def test_reconcile_does_not_clear_cleanup_on_restore() -> None:
    health = reconcile_pipeline_health_after_campaign_cleanup(
        {"status": "DEGRADED", "cleanup_failure_count": 2},
        findings=[],
        environment_restored=True,
        original_cleanup_failures=2,
    )
    assert health["cleanup_failure_count"] == 2
    assert health["environment_restored"] is True
    assert health.get("campaign_cleanup_recovered") is False


def test_selected_experiments_always_emit_receipts() -> None:
    batch = execute_selected_experiments(
        [{"obligation_id": "missing"}],
        experiments_by_obligation={},
        behavior_ir={"operations": [], "actors": []},
        root=Path("."),
        project="benchmark_mall",
        base_url="http://localhost:8080",
        runtime_contract={"approved_base_url": "http://localhost:8080", "environment_kind": "test"},
        mainline_run=_mainline_run(),
        campaign_id="CMP-1",
    )
    assert batch["blocked_count"] == 1
    assert batch["every_experiment_has_receipt"] is True
    assert batch["results"][0]["status"] == "BLOCKED"
    compile_receipt = batch["compile_results"]["missing"]
    assert compile_receipt["status"] == "BLOCKED"
    assert compile_receipt["reason_code"] == "BLOCKED_MISSING_OPERATION"
    assert compile_receipt["experiment_id"] == ""
    assert compile_receipt["receipt_id"]
    assert batch["execution_results"] == {}
    assert batch["gate_results"] == {}

    ledger = build_obligation_attempt_ledger(
        mainline_run=_mainline_run(),
        selected=[{"obligation_id": "missing"}],
        compile_results=batch["compile_results"],
        execution_results=batch["execution_results"],
        gate_results=batch["gate_results"],
    )
    assert ledger["complete"] is True
    assert ledger["attempts"][0]["terminal_status"] == "BLOCKED"


@pytest.mark.parametrize(
    "selected",
    [
        [{}],
        [{"obligation_id": "obl-1"}, {"obligation_id": "obl-1"}],
    ],
)
def test_executor_rejects_missing_or_duplicate_selected_obligation_identity(
    selected,
) -> None:
    with pytest.raises(ValueError, match="selected_obligation_identity_invalid"):
        execute_selected_experiments(
            selected,
            experiments_by_obligation={},
            behavior_ir={"operations": [], "actors": []},
            root=Path("."),
            project="generic-project",
            base_url="http://127.0.0.1:8080",
            runtime_contract={"environment_type": "test"},
            mainline_run=_mainline_run(),
            campaign_id="CMP-1",
        )


def test_executed_experiment_emits_joinable_observation_oracle_and_gate_receipts(
    monkeypatch,
) -> None:
    def executed_property_held(*_args, **kwargs):
        chain = _property_held_oracle_chain(
            campaign_id=kwargs["campaign_id"],
            execution_id=kwargs["execution_id"],
        )
        observation_receipt_id = chain["observer_receipts"][0]["receipt_id"]
        request_body_fingerprint = _fingerprint(None)
        request_semantics_fingerprint = _fingerprint({
            "operation_ref": "read-resource",
            "method": "GET",
            "path_template": "/api/resources",
            "mutation_class": "read_request",
            "mutation_selector": "",
            "mutation_operator": "",
            "request_body_fingerprint": request_body_fingerprint,
        })
        return {
            "schema_version": "qualibug.experiment-execution.v1",
            "experiment_id": "exp-1",
            "obligation_id": "obl-1",
            "status": "EXECUTED",
            "reason_code": "",
            "elapsed_ms": 7,
            "steps": [
                {
                    "phase": "control",
                    "step_id": "control-1",
                    "actor_ref": "actor-a",
                    "operation_ref": "read-resource",
                    "method": "GET",
                    "path": "/api/resources",
                    "path_template": "/api/resources",
                    "status_code": 200,
                    "observation_receipt_id": observation_receipt_id,
                    "request_body_fingerprint": request_body_fingerprint,
                    "request_semantics_fingerprint": request_semantics_fingerprint,
                    "mutation_class": "read_request",
                    "body": {"items": []},
                },
                {
                    "phase": "treatment",
                    "step_id": "treatment-1",
                    "actor_ref": "actor-a",
                    "operation_ref": "read-resource",
                    "method": "GET",
                    "path": "/api/resources",
                    "path_template": "/api/resources",
                    "status_code": 200,
                    "observation_receipt_id": observation_receipt_id,
                    "request_body_fingerprint": request_body_fingerprint,
                    "request_semantics_fingerprint": request_semantics_fingerprint,
                    "mutation_class": "read_request",
                    "body": {"items": []},
                },
            ],
            "observer_receipts": chain["observer_receipts"],
            "contract_evidence_receipts": chain[
                "contract_evidence_receipts"
            ],
            "oracle_verdict": chain["oracle"],
            "finding": None,
            "cleanup_failures": 0,
            "execution_receipt": {"status": "EXECUTED"},
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.load_actor_tokens",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.execute_one_experiment",
        executed_property_held,
    )

    selected = [{"obligation_id": "obl-1", "experiment_id": "exp-1"}]
    batch = execute_selected_experiments(
        selected,
        experiments_by_obligation={
            "obl-1": {
                "obligation_id": "obl-1",
                "experiment_id": "exp-1",
                "compile_receipt": {"status": "COMPILED"},
                "source_refs": _property_held_oracle_chain()["source_refs"],
            }
        },
        behavior_ir={"operations": [], "actors": []},
        root=Path("."),
        project="generic-project",
        base_url="http://127.0.0.1:8080",
        runtime_contract={"environment_type": "test"},
        mainline_run=_mainline_run(),
        campaign_id="CMP-1",
    )

    execution = batch["execution_results"]["obl-1"]
    assert execution["observation_receipt_ids"]
    assert execution["oracle_receipt_id"]
    assert batch["gate_results"]["obl-1"]["status"] == "REJECTED"
    assert batch["gate_results"]["obl-1"]["reason_code"] == "ORACLE_NOT_VIOLATED"

    ledger = build_obligation_attempt_ledger(
        mainline_run=_mainline_run(),
        selected=selected,
        compile_results=batch["compile_results"],
        execution_results=batch["execution_results"],
        gate_results=batch["gate_results"],
    )
    assert ledger["complete"] is True
    assert ledger["attempts"][0]["terminal_status"] == "REJECTED"


def test_cleanup_failure_is_terminal_harness_failure_not_rejected_finding(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.load_actor_tokens",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.execute_one_experiment",
        lambda *_args, **_kwargs: {
            "schema_version": "qualibug.experiment-execution.v1",
            "experiment_id": "exp-1",
            "obligation_id": "obl-1",
            "status": "EXECUTED",
            "reason_code": "",
            "elapsed_ms": 7,
            "steps": [],
            "oracle_verdict": _property_held_oracle_receipt(),
            "finding": None,
            "cleanup_failures": 1,
            "execution_receipt": {"status": "EXECUTED", "cleanup_failures": 1},
        },
    )

    batch = execute_selected_experiments(
        [{"obligation_id": "obl-1", "experiment_id": "exp-1"}],
        experiments_by_obligation={
            "obl-1": {
                "obligation_id": "obl-1",
                "experiment_id": "exp-1",
                "compile_receipt": {"status": "COMPILED"},
            }
        },
        behavior_ir={"operations": [], "actors": []},
        root=Path("."),
        project="generic-project",
        base_url="http://127.0.0.1:8080",
        runtime_contract={"environment_type": "test"},
        mainline_run=_mainline_run(),
        campaign_id="CMP-1",
    )

    assert batch["executed_count"] == 0
    assert batch["harness_failure_count"] == 1
    assert batch["execution_results"]["obl-1"]["status"] == "HARNESS_FAILED"
    assert (
        batch["execution_results"]["obl-1"]["reason_code"]
        == "CLEANUP_COMPENSATION_FAILED"
    )
    assert batch["gate_results"] == {}


def test_unknown_execution_status_fails_fast(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.load_actor_tokens",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.execute_one_experiment",
        lambda *_args, **_kwargs: {
            "experiment_id": "exp-1",
            "obligation_id": "obl-1",
            "status": "MYSTERY",
            "finding": None,
            "cleanup_failures": 0,
            "execution_receipt": {"status": "MYSTERY"},
        },
    )

    with pytest.raises(ValueError, match="experiment_execution_status_invalid:MYSTERY"):
        execute_selected_experiments(
            [{"obligation_id": "obl-1", "experiment_id": "exp-1"}],
            experiments_by_obligation={
                "obl-1": {
                    "obligation_id": "obl-1",
                    "experiment_id": "exp-1",
                    "compile_receipt": {"status": "COMPILED"},
                }
            },
            behavior_ir={"operations": [], "actors": []},
            root=Path("."),
            project="generic-project",
            base_url="http://127.0.0.1:8080",
            runtime_contract={"environment_type": "test"},
            mainline_run=_mainline_run(),
            campaign_id="CMP-1",
        )


def test_preflight_blocks_unresolved_actor_token() -> None:
    ok, code, _ = preflight_experiment_executable(
        {
            "compile_receipt": {"status": "COMPILED"},
            "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
            "control_plan": [],
            "treatment_plan": [{"actor_ref": "a1", "operation_ref": "op1"}],
            "observers": [{"observer_id": "http_response"}],
            "cleanup_plan": [],
            "safety_contract": {"governed_write": False},
        },
        behavior_ir={
            "actors": [{"id": "a1", "role": "buyer", "credential_secret_ref": "secret_ref:test_accounts:buyer"}],
            "operations": [{"id": "op1", "method": "GET", "path": "/api/x"}],
        },
        actor_tokens={},
    )
    assert ok is False
    assert code == "BLOCKED_MISSING_ACTOR"


def test_declared_observation_path_materializes_body_field_read_observer() -> None:
    operations = {
        "adjust-inventory": {
            "id": "adjust-inventory",
            "method": "POST",
            "path": "/api/inventory/admin/adjust",
            "request_schema": {
                "content": {
                    "application/json": {
                        "example": {
                            "sku": "SKU-PHONE-001",
                            "delta": 10,
                        },
                    },
                },
            },
        },
        "read-inventory": {
            "id": "read-inventory",
            "method": "GET",
            "path": "/api/inventory/{sku}",
        },
    }

    assert _declared_observation_path(
        "/api/inventory/admin/adjust",
        operations,
        request_body={"sku": "SKU-PHONE-001", "delta": 999},
    ) == "/api/inventory/SKU-PHONE-001"


def test_experiment_compiler_blocks_placeholder_actor_secret_before_selection() -> None:
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl-placeholder-actor",
            "risk_family": "authorization",
            "required_operations": ["op1"],
            "required_actors": ["actor-placeholder"],
            "required_observers": ["http_response"],
            "property": {
                "template": "authorization_control_treatment",
                "operation_ref": "op1",
                "control_actor_ref": "actor-placeholder",
                "treatment_actor_ref": "actor-placeholder",
            },
            "cleanup_requirement": {"required": False},
            "source_refs": [],
        },
        behavior_ir={
            "operations": [{"id": "op1", "method": "GET", "path": "/api/x", "read_write": "read"}],
            "actors": [{
                "id": "actor-placeholder",
                "role": "operator",
                "credential_secret_ref": "secret_ref:actor:operator",
            }],
            "relations": [],
        },
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "BLOCKED"
    assert experiment["compile_receipt"]["reason_code"] == "BLOCKED_MISSING_ACTOR"
    assert experiment["compile_receipt"]["detail"] == "unresolved_secret_ref:actor-placeholder"
