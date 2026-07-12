"""Contract path: experiment execution, assertion DSL, cleanup preservation."""
from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center.assertion_dsl import evaluate_assertion, materialize_assertion
from ai_test_asset_center.discovery_funnel import reconcile_pipeline_health_after_campaign_cleanup
from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.experiment_executor import (
    execute_one_experiment,
    execute_selected_experiments,
    preflight_experiment_executable,
)
from ai_test_asset_center.obligation_attempt_ledger import build_obligation_attempt_ledger
from ai_test_asset_center.observed_product_scan_executor import ObservedProductScanExecutor


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


def test_source_declared_cleanup_template_compiles_for_runtime_response_binding() -> None:
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
                {"id": "create_resource", "method": "POST", "path": "/api/resources", "read_write": "write"},
                {"id": "delete_resource", "method": "DELETE", "path": "/api/resources/{resourceId}", "read_write": "write"},
            ],
            "actors": [{"id": "actor_a", "role": "public"}],
        },
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert experiment["cleanup_plan"][0]["path"] == "/api/resources/{resourceId}"


def test_cleanup_template_uses_successful_write_response_binding(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []

    def governed_write(**kwargs):
        calls.append(dict(kwargs))
        if kwargs["operation_phase"] == "experiment_cleanup":
            return {"write": {"status": 204, "body": {}}, "status": "completed"}
        return {"write": {"status": 201, "body": {"resourceId": "r-1"}}, "status": "completed"}

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
                {"id": "get_resource", "method": "GET", "path": "/api/resources/{resourceId}"},
                {"id": "delete_resource", "method": "DELETE", "path": "/api/resources/{resourceId}"},
            ],
            "actors": [{"id": "actor_a", "role": "public"}],
        },
        root=tmp_path,
        project="generic-project",
        base_url="http://127.0.0.1:8080",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign-1",
        actor_tokens={},
    )

    assert [call["path"] for call in calls] == ["/api/resources", "/api/resources/r-1"]
    assert result["cleanup_failures"] == 0


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
            "observers": [{"observer_id": "http_response"}],
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
            return {"write": {"status": 201, "body": {"id": "r-1"}}, "status": "completed"}
        assert kwargs["operation_phase"] == "experiment_fixture_cleanup"
        assert kwargs["path"] == "/api/resources/r-1/archive"
        return {"write": {"status": 204, "body": {}}, "status": "completed"}

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
            "observers": [{"observer_id": "http_response"}],
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
        mainline_run={"run_id": "RUN-1", "campaign_id": "CMP-1"},
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
            campaign_id="CMP-1",
        )


def test_executed_experiment_emits_joinable_observation_oracle_and_gate_receipts(
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
            "steps": [
                {
                    "phase": "treatment",
                    "method": "GET",
                    "path": "/api/resources",
                    "status_code": 200,
                }
            ],
            "oracle_verdict": {"verdict": "executed_clue"},
            "finding": None,
            "cleanup_failures": 0,
            "execution_receipt": {"status": "EXECUTED"},
        },
    )

    selected = [{"obligation_id": "obl-1", "experiment_id": "exp-1"}]
    batch = execute_selected_experiments(
        selected,
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
        campaign_id="CMP-1",
    )

    execution = batch["execution_results"]["obl-1"]
    assert execution["observation_receipt_ids"]
    assert execution["oracle_receipt_id"]
    assert batch["gate_results"]["obl-1"]["status"] == "REJECTED"
    assert batch["gate_results"]["obl-1"]["reason_code"] == "ORACLE_NOT_VIOLATED"

    ledger = build_obligation_attempt_ledger(
        mainline_run={"run_id": "RUN-1", "campaign_id": "CMP-1"},
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
            "oracle_verdict": {"verdict": "executed_clue"},
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
