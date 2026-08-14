from __future__ import annotations

import pytest

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.experiment_executor import execute_one_experiment
from ai_test_asset_center.fixture_dag import build_fixture_dag_for_experiment
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir
from tests.test_enterprise_understanding_segregation_of_duties import _runtime_actors, _understood_from_chinese


def _patch_governed_write(monkeypatch: pytest.MonkeyPatch, governed_write) -> None:
    for module in (
        "ai_test_asset_center.experiment_executor",
        "ai_test_asset_center.experiment_fixture_materializer",
        "ai_test_asset_center.experiment_fixture_materializer_core",
        "ai_test_asset_center.experiment_barrier_executor",
        "ai_test_asset_center.experiment_plan_executor",
        "ai_test_asset_center.experiment_plan_step_executor_core",
        "ai_test_asset_center.experiment_cleanup_executor",
        "ai_test_asset_center.experiment_cleanup_executor_core",
    ):
        monkeypatch.setattr(f"{module}.execute_governed_control_write", governed_write)


def _patch_http(monkeypatch: pytest.MonkeyPatch, http_request) -> None:
    for module in (
        "ai_test_asset_center.experiment_executor",
        "ai_test_asset_center.experiment_runtime_support",
        "ai_test_asset_center.experiment_plan_executor",
        "ai_test_asset_center.experiment_plan_step_executor_core",
    ):
        monkeypatch.setattr(f"{module}._http_request", http_request)


def _compiled():
    ir = build_behavior_ir_from_knowledge_asset(_understood_from_chinese(), runtime_actors=_runtime_actors())
    result = compile_obligations_from_behavior_ir(ir)
    obligation = next(row for row in result["obligations"] if row.get("property", {}).get("sod_policy_id"))
    experiment = compile_experiment_for_obligation(obligation, behavior_ir=ir, environment_type="test")
    experiment["fixture_dag"] = build_fixture_dag_for_experiment(experiment, behavior_ir=ir)
    actors = {row["id"]: row for row in ir["actors"]}
    tokens = {
        actor["credential_secret_ref"]: (
            "independent-token"
            if actor.get("account_ref") == "independent@example.test"
            else "shared-token"
        )
        for actor in actors.values()
        if actor.get("account_ref")
    }
    return ir, experiment, tokens


@pytest.mark.parametrize(
    ("treatment_status", "expected_oracle", "expect_finding"),
    [(200, "VIOLATION", True), (403, "PROPERTY_HELD", False)],
)
def test_sod_real_executor_detects_same_credential_approval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    treatment_status: int,
    expected_oracle: str,
    expect_finding: bool,
) -> None:
    ir, experiment, tokens = _compiled()
    phases: list[tuple[str, str, str]] = []

    def governed_write(**kwargs):
        phase = kwargs["operation_phase"]
        token = kwargs.get("actor_token", "")
        phases.append((phase, token, kwargs["path"]))
        if phase == "experiment_fixture_setup":
            status = 201
            body = {"id": "order-1", "status": "pending", "createdBy": "shared@example.test"}
        elif phase == "experiment_control":
            assert token == "independent-token"
            status = 200
            body = {"id": "order-1", "status": "approved"}
        elif phase == "experiment_treatment":
            assert token == "shared-token"
            status = treatment_status
            body = (
                {"id": "order-1", "status": "approved"}
                if status == 200
                else {"error": "maker_checker_violation"}
            )
        else:
            status = 204
            body = {}
        cleanup = phase in {"experiment_fixture_cleanup", "experiment_cleanup"}
        return {
            "accepted": 200 <= status < 300,
            "status": "executed",
            "method": kwargs["method"],
            "path": kwargs["path"],
            "before": {"status": 200, "body": {"id": "order-1", "status": "pending"}},
            "write": {"status": status, "body": body},
            "write_request_attempt_count": 1,
            "after": {"status": 404 if cleanup else 200, "body": {} if cleanup else body},
            "audit_path": "sandbox_write_audit.jsonl",
            "audit_record": {"phase": phase, "actor_token": token},
        }

    def http_request(method: str, url: str, **_kwargs):
        path = "/" + url.split("://", 1)[-1].split("/", 1)[1]
        if method == "GET" and path == "/api/orders":
            return {"status": 200, "body": [{"id": "order-1", "status": "pending"}], "headers": {}}
        if method == "GET" and path == "/api/orders/order-1":
            return {"status": 200, "body": {"id": "order-1", "status": "pending"}, "headers": {}}
        return {"status": 404, "body": {"error": "no route"}, "headers": {}}

    _patch_governed_write(monkeypatch, governed_write)
    _patch_http(monkeypatch, http_request)

    result = execute_one_experiment(
        experiment,
        behavior_ir=ir,
        root=tmp_path,
        project="sod-project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test", "environment_ref": "sod-test", "execution_mode": "approved_sandbox_write", "status": "approved", "approved_base_url": "http://target.invalid", "requested_base_url": "http://target.invalid"},
        campaign_id="campaign-sod",
        execution_id=f"execution-sod-{treatment_status}",
        actor_tokens=tokens,
    )

    assert result["oracle_verdict"]["status"] == expected_oracle, result
    assert bool(result.get("finding")) is expect_finding, result
    causal = result["authorization_causality_receipt"]
    assert causal["status"] == ("PASSED" if expect_finding else "INDETERMINATE"), result
    if not expect_finding:
        assert causal["reason_codes"] == ["AUTHORIZATION_CAUSAL_VIOLATION_NOT_OBSERVED"]
    assert causal["same_resource_proven"] is True
    assert len(causal["runtime_resource_identity_fingerprint"]) == 64
    assert any(phase == "experiment_fixture_setup" and token == "shared-token" for phase, token, _ in phases)
    assert any(phase == "experiment_control" and token == "independent-token" for phase, token, _ in phases)
    assert any(phase == "experiment_treatment" and token == "shared-token" for phase, token, _ in phases)
