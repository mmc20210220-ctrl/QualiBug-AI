from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.experiment_executor import (
    execute_one_experiment,
    execute_selected_experiments,
    preflight_experiment_executable,
)
from ai_test_asset_center.observer_contracts import observe_authorization_comparison


def _http_observation(*, status: int, body: object, phase: str) -> dict:
    return {
        "method": "GET",
        "path": "/resources/r-1",
        "status_code": status,
        "body": body,
        "headers": {"content-type": "application/json"},
        "phase": phase,
        "actor_ref": f"actor-{phase}",
        "operation_ref": "op-read",
    }


@pytest.mark.parametrize("body", [{}, []])
def test_empty_success_payload_is_indeterminate(body: object) -> None:
    receipt = observe_authorization_comparison(
        control=_http_observation(status=200, body=body, phase="control"),
        treatment=_http_observation(status=200, body=body, phase="treatment"),
        require_same_resource=True,
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "CONTROL_RESOURCE_EVIDENCE_MISSING"
    assert receipt["evidence"]["leak_detected"] is None


def test_same_nonempty_resource_is_observed_authorization_violation() -> None:
    payload = {"id": "r-1", "state": "active"}
    receipt = observe_authorization_comparison(
        control=_http_observation(status=200, body=payload, phase="control"),
        treatment=_http_observation(status=200, body=payload, phase="treatment"),
        require_same_resource=True,
    )

    assert receipt["status"] == "OBSERVED"
    assert receipt["evidence"]["same_resource_proven"] is True
    assert receipt["evidence"]["viewer_can_access"] is True
    assert receipt["evidence"]["leak_detected"] is True


def test_shared_foreign_key_does_not_prove_same_resource() -> None:
    receipt = observe_authorization_comparison(
        control=_http_observation(
            status=200,
            body={"id": "r-1", "owner_id": "owner-1"},
            phase="control",
        ),
        treatment=_http_observation(
            status=200,
            body={"id": "r-2", "owner_id": "owner-1"},
            phase="treatment",
        ),
        require_same_resource=True,
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "SAME_RESOURCE_NOT_PROVEN"


def test_explicit_treatment_rejection_is_observed_property_held() -> None:
    receipt = observe_authorization_comparison(
        control=_http_observation(
            status=200,
            body={"id": "r-1", "state": "active"},
            phase="control",
        ),
        treatment=_http_observation(
            status=403,
            body={"error": "forbidden"},
            phase="treatment",
        ),
        require_same_resource=True,
    )

    assert receipt["status"] == "OBSERVED"
    assert receipt["evidence"]["same_resource_proven"] is True
    assert receipt["evidence"]["viewer_can_access"] is False
    assert receipt["evidence"]["leak_detected"] is False


def test_html_success_payload_is_not_protected_resource_evidence() -> None:
    receipt = observe_authorization_comparison(
        control=_http_observation(
            status=200,
            body={"id": "r-1"},
            phase="control",
        ),
        treatment={
            **_http_observation(status=200, body="<html>login</html>", phase="treatment"),
            "headers": {"content-type": "text/html"},
        },
        require_same_resource=True,
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "TREATMENT_RESOURCE_EVIDENCE_MISSING"


def test_write_status_pair_requires_business_effect_evidence() -> None:
    control = _http_observation(
        status=200,
        body={"id": "r-1"},
        phase="control",
    )
    treatment = _http_observation(
        status=200,
        body={"id": "r-1"},
        phase="treatment",
    )
    control["method"] = "POST"
    treatment["method"] = "POST"

    receipt = observe_authorization_comparison(
        control=control,
        treatment=treatment,
        require_same_resource=True,
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "WRITE_EFFECT_EVIDENCE_REQUIRED"


def _behavior_ir() -> dict:
    return {
        "operations": [{
            "id": "op-read",
            "operation_id": "read_resource",
            "method": "GET",
            "path": "/resources/r-1",
            "read_write": "read",
        }],
        "actors": [
            {
                "id": "actor-control",
                "role": "owner",
                "credential_secret_ref": "secret_ref:owner",
                "account_status": "active",
            },
            {
                "id": "actor-treatment",
                "role": "restricted",
                "credential_secret_ref": "secret_ref:restricted",
                "account_status": "active",
            },
        ],
    }


def _authorization_experiment() -> dict:
    return {
        "schema_version": "qualibug.experiment.v1",
        "experiment_id": "exp-auth",
        "obligation_id": "obl-auth",
        "control_plan": [{
            "step_id": "control_1",
            "actor_ref": "actor-control",
            "operation_ref": "op-read",
        }],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": "actor-treatment",
            "operation_ref": "op-read",
        }],
        "binding_plan": [],
        "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
        "assertions": [{"assertion_id": "assert-auth", "kind": "authorization"}],
        "observers": [
            {"observer_id": "http_response", "surface": "http_api"},
            {"observer_id": "actor_identity", "surface": "identity_context"},
            {"observer_id": "authorization_comparison", "surface": "http_api"},
        ],
        "cleanup_plan": [],
        "safety_contract": {"environment_type": "test", "governed_write": False},
        "source_refs": [{"source_id": "permission-source", "kind": "permission_matrix"}],
        "compile_receipt": {"status": "COMPILED", "reason_code": ""},
    }


def test_executor_does_not_emit_finding_for_empty_2xx_pair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    responses = iter([
        {"status": 200, "body": {}, "headers": {"content-type": "application/json"}},
        {"status": 200, "body": {}, "headers": {"content-type": "application/json"}},
    ])
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor._http_request",
        lambda *_args, **_kwargs: next(responses),
    )

    result = execute_one_experiment(
        _authorization_experiment(),
        behavior_ir=_behavior_ir(),
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-readable-denial",
        actor_tokens={
            "secret_ref:owner": "owner-token",
            "secret_ref:restricted": "restricted-token",
        },
    )

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_MISSING_OBSERVER"
    assert result["finding"] is None
    comparison = next(
        receipt
        for receipt in result["observer_receipts"]
        if receipt["observer_id"] == "authorization_comparison"
    )
    assert comparison["status"] == "INDETERMINATE"


def test_runtime_preflight_rejects_authorization_without_comparison_observer() -> None:
    experiment = _authorization_experiment()
    experiment["observers"] = [
        observer
        for observer in experiment["observers"]
        if observer["observer_id"] != "authorization_comparison"
    ]

    ok, reason_code, detail = preflight_experiment_executable(
        experiment,
        behavior_ir=_behavior_ir(),
        actor_tokens={
            "secret_ref:owner": "owner-token",
            "secret_ref:restricted": "restricted-token",
        },
    )

    assert ok is False
    assert reason_code == "BLOCKED_MISSING_OBSERVER"
    assert detail == "authorization_comparison"


def test_compiler_blocks_unimplemented_observer() -> None:
    obligation = {
        "obligation_id": "obl-unsupported-observer",
        "risk_family": "validation",
        "property": {"operation_ref": "op-read"},
        "required_operations": ["op-read"],
        "required_actors": ["actor-control"],
        "required_observers": ["imaginary_surface"],
        "cleanup_requirement": {"required": False},
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=_behavior_ir(),
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "BLOCKED"
    assert experiment["compile_receipt"]["reason_code"] == "BLOCKED_MISSING_OBSERVER"
    assert experiment["compile_receipt"]["detail"] == "imaginary_surface"


def test_authorization_compiler_requires_typed_comparison_observer() -> None:
    obligation = {
        "obligation_id": "obl-auth-compile",
        "risk_family": "authorization",
        "property": {
            "operation_ref": "op-read",
            "control_actor_ref": "actor-control",
            "treatment_actor_ref": "actor-treatment",
            "require_same_resource": True,
        },
        "required_operations": ["op-read"],
        "required_actors": ["actor-control", "actor-treatment"],
        "required_observers": ["http_response", "actor_identity"],
        "cleanup_requirement": {"required": False},
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=_behavior_ir(),
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert [row["observer_id"] for row in experiment["observers"]] == [
        "http_response",
        "actor_identity",
        "authorization_comparison",
    ]


def test_authorization_write_blocks_without_business_effect_observer() -> None:
    behavior_ir = _behavior_ir()
    behavior_ir["operations"] = [
        {
            "id": "op-create",
            "operation_id": "create_resource",
            "method": "POST",
            "path": "/resources",
            "read_write": "write",
        },
        {
            "id": "op-delete",
            "operation_id": "delete_resource",
            "method": "DELETE",
            "path": "/resources/{id}",
            "read_write": "write",
        },
    ]
    obligation = {
        "obligation_id": "obl-auth-write",
        "risk_family": "authorization",
        "property": {
            "operation_ref": "op-create",
            "control_actor_ref": "actor-control",
            "treatment_actor_ref": "actor-treatment",
            "require_same_resource": True,
        },
        "required_operations": ["op-create"],
        "required_actors": ["actor-control", "actor-treatment"],
        "required_observers": ["http_response", "actor_identity"],
        "cleanup_requirement": {
            "required": True,
            "operation_ref": "op-delete",
            "mode": "reverse_order",
        },
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=behavior_ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "BLOCKED"
    assert experiment["compile_receipt"]["reason_code"] == "BLOCKED_MISSING_OBSERVER"
    assert experiment["compile_receipt"]["detail"] == "write_observer"


def test_executor_uses_same_resource_receipt_for_violation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = {"id": "r-1", "state": "active"}
    responses = iter([
        {"status": 200, "body": payload, "headers": {"content-type": "application/json"}},
        {"status": 200, "body": payload, "headers": {"content-type": "application/json"}},
    ])
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor._http_request",
        lambda *_args, **_kwargs: next(responses),
    )

    result = execute_one_experiment(
        _authorization_experiment(),
        behavior_ir=_behavior_ir(),
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-same-resource",
        actor_tokens={
            "secret_ref:owner": "owner-token",
            "secret_ref:restricted": "restricted-token",
        },
    )

    assert result["status"] == "EXECUTED"
    assert result["finding"] is not None
    assert result["oracle_verdict"]["status"] == "VIOLATION"
    assert result["oracle_verdict"]["activation_receipt"]["status"] == "ACTIVE"
    assert {
        receipt["kind"] for receipt in result["contract_evidence_receipts"]
    }.issuperset({"actor", "control", "treatment"})
    assert result["finding"]["gate_passed"] is False
    assert result["finding"]["confirmation_status"] == "candidate"
    assert result["finding"]["customer_delivery_status"] == "candidate"
    assert result["finding"]["oracle"]["receipt_id"] == result[
        "oracle_verdict"
    ]["receipt_id"]
    assert result["finding"]["failed_assertions"][0]["status"] == "VIOLATION"
    assert result["finding"].get("evidence_quality", {}).get("level") != "validated"
    comparison = next(
        receipt
        for receipt in result["observer_receipts"]
        if receipt["observer_id"] == "authorization_comparison"
    )
    assert comparison["status"] == "OBSERVED"
    assert comparison["evidence"]["same_resource_proven"] is True


def test_executor_treatment_rejection_does_not_emit_finding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    responses = iter([
        {
            "status": 200,
            "body": {"id": "r-1", "state": "active"},
            "headers": {"content-type": "application/json"},
        },
        {
            "status": 403,
            "body": {"error": "forbidden"},
            "headers": {"content-type": "application/json"},
        },
    ])
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor._http_request",
        lambda *_args, **_kwargs: next(responses),
    )

    result = execute_one_experiment(
        _authorization_experiment(),
        behavior_ir=_behavior_ir(),
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-different-resource",
        actor_tokens={
            "secret_ref:owner": "owner-token",
            "secret_ref:restricted": "restricted-token",
        },
    )

    assert result["status"] == "EXECUTED"
    assert result["finding"] is None
    assert result["oracle_verdict"]["verdict"] == "property_held"


def test_batch_lineage_includes_typed_observer_receipt_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    account_dir = tmp_path / "platform_inputs" / "project"
    account_dir.mkdir(parents=True)
    (account_dir / "test_accounts.json").write_text(
        json.dumps({
            "accounts": [
                {"role": "owner", "token": "owner-token"},
                {"role": "restricted", "token": "restricted-token"},
            ],
        }),
        encoding="utf-8",
    )
    payload = {"id": "r-1", "state": "active"}
    responses = iter([
        {"status": 200, "body": payload, "headers": {"content-type": "application/json"}},
        {"status": 200, "body": payload, "headers": {"content-type": "application/json"}},
    ])
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor._http_request",
        lambda *_args, **_kwargs: next(responses),
    )

    batch = execute_selected_experiments(
        [{"obligation_id": "obl-auth", "experiment_id": "exp-auth"}],
        experiments_by_obligation={"obl-auth": _authorization_experiment()},
        behavior_ir=_behavior_ir(),
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
    )

    typed_receipt_ids = {
        receipt["receipt_id"]
        for receipt in batch["results"][0]["observer_receipts"]
    }
    recorded_ids = set(
        batch["execution_results"]["obl-auth"]["observation_receipt_ids"]
    )
    assert typed_receipt_ids.issubset(recorded_ids)
