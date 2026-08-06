from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.experiment_executor import (
    _cleanup_restores_governed_write,
    execute_one_experiment,
    execute_selected_experiments,
    preflight_experiment_executable,
)
from ai_test_asset_center.discovery_mainline_contract import build_mainline_run_contract
from ai_test_asset_center.obligation_attempt_ledger import (
    build_obligation_attempt_ledger,
    validate_obligation_attempt_ledger,
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


def test_business_key_identity_proves_same_resource() -> None:
    # "sku" carries no meaning to the observer; it proves identity only because
    # the source declared it a unique key and the caller passed it in.
    payload = {"sku": "SKU-1", "state": "active"}
    receipt = observe_authorization_comparison(
        control=_http_observation(status=200, body=payload, phase="control"),
        treatment=_http_observation(status=200, body=payload, phase="treatment"),
        require_same_resource=True,
        identity_keys=["sku"],
    )

    assert receipt["status"] == "OBSERVED"
    assert receipt["evidence"]["same_resource_proven"] is True
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


def test_source_declared_control_fixture_absence_is_observed_no_leak() -> None:
    control = {
        **_http_observation(
            status=200,
            body=[{"id": "r-1", "state": "active"}],
            phase="control",
        ),
        "path": "/resources",
        "actor_ref": "actor-control",
    }
    treatment = {
        **_http_observation(status=200, body=[], phase="treatment"),
        "path": "/resources",
    }

    receipt = observe_authorization_comparison(
        control=control,
        treatment=treatment,
        require_same_resource=True,
        binding_materialization_receipts=[{
            "status": "BOUND",
            "fixture_id": "control_resource",
            "fixture_setup_status": "completed",
            "fixture_cleanup_status": "completed",
            "ownership_proof_status": "OBSERVED",
            "owner_actor_ref": "actor-control",
            "value_fingerprint": hashlib.sha256(b"r-1").hexdigest()[:12],
        }],
    )

    assert receipt["status"] == "OBSERVED"
    assert receipt["evidence"]["same_resource_proven"] is True
    assert receipt["evidence"]["viewer_can_access"] is False
    assert receipt["evidence"]["leak_detected"] is False
    assert (
        receipt["evidence"]["resource_match_basis"]
        == "source_declared_control_fixture_absent_from_treatment_collection"
    )


def test_write_status_pair_requires_business_effect_evidence() -> None:
    control = _http_observation(
        status=200,
        body={"id": "r-1"},
        phase="control",
    )
    treatment = _http_observation(
        # A restricted write that was NOT accepted (500) cannot be proven by
        # status alone; business-effect evidence is still required. (Accepted
        # 2xx writes are covered by the strengthened dual-accepted rule.)
        status=500,
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


def test_dual_accepted_write_is_observed_leak_without_business_effect() -> None:
    """Strengthened contract: once the authorized control write proves the
    operation is real, a restricted actor whose write request is ACCEPTED
    (2xx) has already broken enforcement. A temporary no-op must not convert
    an accepted forbidden request into a passing result, so no business-effect
    readback is required to prove this leak.
    """
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

    assert receipt["status"] == "OBSERVED"
    assert (
        receipt["evidence"]["resource_match_basis"]
        == "dual_accepted_write_status_comparison"
    )
    assert receipt["evidence"]["viewer_can_access"] is True
    assert receipt["evidence"]["leak_detected"] is True


def test_write_success_without_restricted_business_effect_is_observed_leak() -> None:
    """Even when readback shows zero business effect for the restricted actor,
    an ACCEPTED forbidden write request remains an observed enforcement
    violation under the strengthened contract.
    """
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
        business_effect={
            "business_effect_observed": True,
            "control_effect_count": 1,
            "treatment_effect_count": 0,
        },
    )

    assert receipt["status"] == "OBSERVED"
    assert receipt["evidence"]["viewer_can_access"] is True
    assert receipt["evidence"]["leak_detected"] is True


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
                # Canonical spelling: the credential loader registers
                # secret_ref:test_accounts:{role} aliases and governance
                # role_aliases accepts exactly this form; a bare
                # secret_ref:{role} reads as an unresolvable exact secret.
                "credential_secret_ref": "secret_ref:test_accounts:owner",
                "account_status": "active",
            },
            {
                "id": "actor-treatment",
                "role": "restricted",
                "credential_secret_ref": "secret_ref:test_accounts:restricted",
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
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_plan_executor._http_request",
        lambda *_args, **_kwargs: next(responses),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_runtime_support._http_request",
        lambda *_args, **_kwargs: next(responses),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_runtime_credentials._http_request",
        lambda *_args, **_kwargs: next(responses),
    )

    experiment = _authorization_experiment()
    # Owner-partitioned resource: an empty 2xx pair cannot prove the viewer
    # saw the owner's data (it may be the viewer's own empty partition), so
    # the comparison must stay INDETERMINATE and never emit a finding.
    experiment["assertions"][0]["property"] = {
        "comparison_dimension": "OWNERSHIP_RELATION",
    }
    result = execute_one_experiment(
        experiment,
        behavior_ir=_behavior_ir(),
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={
            "environment_type": "test",
            "environment_ref": "test-env",
            "execution_mode": "approved_sandbox_write",
            "approved_base_url": "http://target.invalid",
            "status": "approved",
        },
        campaign_id="campaign",
        execution_id="execution-readable-denial",
        actor_tokens={
            "secret_ref:test_accounts:owner": "owner-token",
            "secret_ref:test_accounts:restricted": "restricted-token",
        },
    )

    assert result["status"] == "BLOCKED", json.dumps(result, default=str, indent=2)
    # An INDETERMINATE observer receipt is intentionally classified separately
    # from a missing observer: it keeps retry eligibility while still blocking
    # finding emission.
    assert result["reason_code"] == "BLOCKED_OBSERVER_RECEIPT_INDETERMINATE"
    assert result["finding"] is None
    comparison = next(
        receipt
        for receipt in result["observer_receipts"]
        if receipt["observer_id"] == "authorization_comparison"
    )
    assert comparison["status"] == "INDETERMINATE"


def test_executor_emits_role_permission_finding_for_empty_2xx_pair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Role-permission comparisons gate the OPERATION itself: a role gate
    # returns 403 for denied roles regardless of response content, so an
    # empty 2xx pair on the same path proves the gate is absent — mirroring
    # the write branch's dual-accepted-write proof.
    responses = iter([
        {"status": 200, "body": {}, "headers": {"content-type": "application/json"}},
        {"status": 200, "body": {}, "headers": {"content-type": "application/json"}},
    ])
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor._http_request",
        lambda *_args, **_kwargs: next(responses),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_plan_executor._http_request",
        lambda *_args, **_kwargs: next(responses),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_runtime_support._http_request",
        lambda *_args, **_kwargs: next(responses),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_runtime_credentials._http_request",
        lambda *_args, **_kwargs: next(responses),
    )

    experiment = _authorization_experiment()
    experiment["assertions"][0]["property"] = {
        "comparison_dimension": "ROLE_PERMISSION",
    }
    result = execute_one_experiment(
        experiment,
        behavior_ir=_behavior_ir(),
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={
            "environment_type": "test",
            "environment_ref": "test-env",
            "execution_mode": "approved_sandbox_write",
            "approved_base_url": "http://target.invalid",
            "status": "approved",
        },
        campaign_id="campaign",
        execution_id="execution-role-empty-2xx",
        actor_tokens={
            "secret_ref:test_accounts:owner": "owner-token",
            "secret_ref:test_accounts:restricted": "restricted-token",
        },
    )

    assert result["status"] == "EXECUTED", json.dumps(result, default=str, indent=2)
    comparison = next(
        receipt
        for receipt in result["observer_receipts"]
        if receipt["observer_id"] == "authorization_comparison"
    )
    assert comparison["status"] == "OBSERVED"
    assert comparison["evidence"]["leak_detected"] is True
    assert comparison["evidence"]["viewer_can_access"] is True


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
            "secret_ref:test_accounts:owner": "owner-token",
            "secret_ref:test_accounts:restricted": "restricted-token",
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
    # The compile detail names the exact missing observer id(s), not a
    # category label.
    assert experiment["compile_receipt"]["detail"] == "business_effect"


def test_executor_blocks_response_only_write_observer_before_create_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    behavior_ir = {
        "operations": [
            {
                "id": "op-create",
                "operation_id": "create_resource",
                "method": "POST",
                "path": "/resources",
                "read_write": "write",
                "request_example": {"name": "valid"},
            },
            {
                "id": "op-read-created",
                "operation_id": "read_created_resource",
                "method": "GET",
                "path": "/resources/{id}",
                "read_write": "read",
            },
            {
                "id": "op-delete",
                "operation_id": "delete_resource",
                "method": "DELETE",
                "path": "/resources/{id}",
                "read_write": "write",
            },
        ],
        "actors": [{
            "id": "actor-control",
            "role": "owner",
            # Canonical alias spelling (see _behavior_ir note).
            "credential_secret_ref": "secret_ref:test_accounts:owner",
            "account_status": "active",
        }],
    }
    experiment = {
        "schema_version": "qualibug.experiment.v1",
        "experiment_id": "exp-response-bound-create",
        "obligation_id": "obl-response-bound-create",
        "control_plan": [{
            "step_id": "control_1",
            "actor_ref": "actor-control",
            "operation_ref": "op-create",
            "body": {"name": "valid"},
        }],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": "actor-control",
            "operation_ref": "op-create",
            "body": {},
        }],
        "binding_plan": [],
        "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
        "assertions": [{
            "assertion_id": "assert-validation",
            "kind": "validation_rejection",
            "expected_class": 4,
            "expected_effect_count": 0,
            "expected_control_effect_min": 1,
        }],
        "observers": [
            {"observer_id": "http_response", "surface": "http_api"},
            {
                "observer_id": "business_effect",
                "surface": "business_effect",
                "resolver_operations": [{
                    "operation_ref": "op-read-created",
                    "method": "GET",
                    "path": "/resources/{id}",
                }],
            },
        ],
        "cleanup_plan": [{
            "action": "reverse_order_compensation",
            "mode": "reverse_order",
            "operation_ref": "op-delete",
            "path": "/resources/{id}",
            "method": "DELETE",
            "runtime_response_binding_required": True,
        }],
        "safety_contract": {"environment_type": "test", "governed_write": True},
        "source_refs": [{"source_id": "api", "kind": "api_operation"}],
        "compile_receipt": {"status": "COMPILED", "reason_code": ""},
    }
    resources: dict[str, dict[str, object]] = {}

    def fake_http(method: str, url: str, **kwargs):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        body = kwargs.get("body")
        if method == "GET" and path == "/resources":
            return {"status": 404, "body": {"error": "not_found"}, "headers": {}}
        if method == "POST" and path == "/resources":
            if isinstance(body, dict) and body.get("name"):
                resources["r-1"] = {"id": "r-1", "name": body["name"]}
                return {"status": 201, "body": dict(resources["r-1"]), "headers": {}}
            return {"status": 422, "body": {"error": "name_required"}, "headers": {}}
        if method == "GET" and path == "/resources/r-1":
            if "r-1" in resources:
                return {"status": 200, "body": dict(resources["r-1"]), "headers": {}}
            return {"status": 404, "body": {"error": "not_found"}, "headers": {}}
        if method == "DELETE" and path == "/resources/r-1":
            resources.pop("r-1", None)
            return {"status": 200, "body": {"deleted": True}, "headers": {}}
        raise AssertionError(f"unexpected request: {method} {path}")

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor._http_request",
        fake_http,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_plan_executor._http_request",
        fake_http,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_runtime_support._http_request",
        fake_http,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_runtime_credentials._http_request",
        fake_http,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.sandbox_write_executor._http_request",
        fake_http,
    )

    result = execute_one_experiment(
        experiment,
        behavior_ir=behavior_ir,
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={
            "environment_type": "test",
            "environment_ref": "test-env",
            "execution_mode": "approved_sandbox_write",
            "approved_base_url": "http://target.invalid",
            "status": "approved",
        },
        campaign_id="campaign",
        execution_id="execution-response-bound-create",
        actor_tokens={"secret_ref:test_accounts:owner": "owner-token"},
    )

    assert result["status"] == "BLOCKED", json.dumps(result, default=str, indent=2)
    assert result["reason_code"] == "BLOCKED_MISSING_OBSERVER"
    # Preflight block means no write reached transport — no steps emitted.
    assert result.get("steps") is None or all(
        step["status"] == "blocked_write" for step in result["steps"]
    )
    assert resources == {}


def test_cleanup_compensation_uses_response_bound_created_state() -> None:
    original = {
        "accepted": True,
        "method": "POST",
        "path": "/resources",
        "before": {"status": 404, "body": {"error": "not_found"}},
        "after": {"status": 404, "body": {"error": "not_found"}},
        "response_bound_after": {
            "status": 200,
            "body": {"id": "r-1", "state": "PENDING"},
        },
        "write": {"status": 201, "body": {"id": "r-1", "state": "PENDING"}},
        "before_ref": "control_before:/resources:404",
        "after_ref": "control_after:/resources:404",
        "audit_path": "audit.jsonl",
        "audit_record": {"operation_phase": "experiment_control"},
    }
    cleanup = {
        "accepted": True,
        "method": "POST",
        "path": "/resources/r-1/reject",
        "before": {"status": 200, "body": {"id": "r-1", "state": "PENDING"}},
        "after": {"status": 200, "body": {"id": "r-1", "state": "REJECTED"}},
        "write": {"status": 200, "body": {"id": "r-1", "state": "REJECTED"}},
        "before_ref": "control_before:/resources/r-1:200",
        "after_ref": "control_after:/resources/r-1:200",
        "audit_path": "audit.jsonl",
        "audit_record": {"operation_phase": "experiment_cleanup"},
    }

    assert _cleanup_restores_governed_write(original, cleanup) is True


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
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_plan_executor._http_request",
        lambda *_args, **_kwargs: next(responses),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_runtime_support._http_request",
        lambda *_args, **_kwargs: next(responses),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_runtime_credentials._http_request",
        lambda *_args, **_kwargs: next(responses),
    )

    result = execute_one_experiment(
        _authorization_experiment(),
        behavior_ir=_behavior_ir(),
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test", "environment_ref": "test-env"},
        campaign_id="campaign",
        execution_id="execution-same-resource",
        actor_tokens={
            "secret_ref:test_accounts:owner": "owner-token",
            "secret_ref:test_accounts:restricted": "restricted-token",
        },
    )

    assert result["status"] == "EXECUTED", json.dumps(result, default=str, indent=2)
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
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_plan_executor._http_request",
        lambda *_args, **_kwargs: next(responses),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_runtime_support._http_request",
        lambda *_args, **_kwargs: next(responses),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_runtime_credentials._http_request",
        lambda *_args, **_kwargs: next(responses),
    )

    result = execute_one_experiment(
        _authorization_experiment(),
        behavior_ir=_behavior_ir(),
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test", "environment_ref": "test-env"},
        campaign_id="campaign",
        execution_id="execution-different-resource",
        actor_tokens={
            "secret_ref:test_accounts:owner": "owner-token",
            "secret_ref:test_accounts:restricted": "restricted-token",
        },
    )

    assert result["status"] == "EXECUTED", json.dumps(result, default=str, indent=2)
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
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_plan_executor._http_request",
        lambda *_args, **_kwargs: next(responses),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_runtime_support._http_request",
        lambda *_args, **_kwargs: next(responses),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_runtime_credentials._http_request",
        lambda *_args, **_kwargs: next(responses),
    )

    batch = execute_selected_experiments(
        [{"obligation_id": "obl-auth", "experiment_id": "exp-auth"}],
        experiments_by_obligation={"obl-auth": _authorization_experiment()},
        behavior_ir=_behavior_ir(),
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test", "environment_ref": "test-env"},
        mainline_run=build_mainline_run_contract(
            mainline_authority="experiment_candidate",
            run_id="run",
            campaign_id="campaign",
            target_id="target",
            environment_id="environment",
            policy_version="policy",
            evaluation_mode="operational",
        ),
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


def test_batch_preserves_exact_variant_obligation_lineage(
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
    for module in (
        "ai_test_asset_center.experiment_executor",
        "ai_test_asset_center.experiment_plan_executor",
        "ai_test_asset_center.experiment_runtime_support",
    ):
        monkeypatch.setattr(
            f"{module}._http_request",
            lambda *_args, **_kwargs: next(responses),
        )
    experiment = _authorization_experiment()
    variant_id = "obl-auth__v_abcd"
    experiment["obligation_id"] = variant_id

    batch = execute_selected_experiments(
        [{"obligation_id": "obl-auth", "experiment_id": "exp-auth"}],
        experiments_by_obligation={"obl-auth": experiment},
        behavior_ir=_behavior_ir(),
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test", "environment_ref": "test-env"},
        mainline_run=build_mainline_run_contract(
            mainline_authority="experiment_candidate",
            run_id="run",
            campaign_id="campaign",
            target_id="target",
            environment_id="environment",
            policy_version="policy",
            evaluation_mode="operational",
        ),
        campaign_id="campaign",
    )

    outcome = batch["results"][0]
    assert outcome["selected_obligation_id"] == "obl-auth"
    assert outcome["obligation_id"] == variant_id
    assert outcome["oracle_verdict"]["obligation_id"] == variant_id
    assert outcome["reproduction_receipt"]["obligation_id"] == variant_id
    execution_receipt = batch["execution_results"]["obl-auth"]
    assert execution_receipt["selected_obligation_id"] == "obl-auth"
    assert execution_receipt["executed_obligation_id"] == variant_id
    ledger = build_obligation_attempt_ledger(
        mainline_run=build_mainline_run_contract(
            mainline_authority="experiment_candidate",
            run_id="run",
            campaign_id="campaign",
            target_id="target",
            environment_id="environment",
            policy_version="policy",
            evaluation_mode="operational",
        ),
        selected=[{"obligation_id": "obl-auth", "experiment_id": "exp-auth"}],
        compile_results=batch["compile_results"],
        execution_results=batch["execution_results"],
        gate_results=batch["gate_results"],
    )
    validate_obligation_attempt_ledger(ledger)
    assert ledger["attempts"][0]["obligation_id"] == "obl-auth"
    assert ledger["attempts"][0]["executed_obligation_id"] == variant_id


@pytest.mark.parametrize(
    ("execution_status", "reason_code", "terminal_status"),
    [
        ("BLOCKED", "BLOCKED_MISSING_OBSERVER", "BLOCKED"),
        (
            "HARNESS_FAILURE",
            "CONTRACT_ORACLE_HARNESS_FAILED",
            "HARNESS_FAILED",
        ),
    ],
)
def test_batch_preserves_variant_identity_for_non_deliverable_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    execution_status: str,
    reason_code: str,
    terminal_status: str,
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
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.execute_one_experiment",
        lambda *_args, **_kwargs: {
            "schema_version": "qualibug.experiment-execution.v1",
            "experiment_id": "exp-auth",
            "status": execution_status,
            "reason_code": reason_code,
            "steps": [{
                "phase": "treatment",
                "method": "GET",
                "path": "/resources/r-1",
                "status_code": 200,
            }],
            "observer_receipts": [],
            "contract_evidence_receipts": [],
            "oracle_verdict": {},
            "execution_receipt": {},
            "finding": None,
        },
    )
    experiment = _authorization_experiment()
    variant_id = "obl-auth__v_blocked"
    experiment["obligation_id"] = variant_id
    mainline = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="run",
        campaign_id="campaign",
        target_id="target",
        environment_id="environment",
        policy_version="policy",
        evaluation_mode="operational",
    )

    batch = execute_selected_experiments(
        [{"obligation_id": "obl-auth", "experiment_id": "exp-auth"}],
        experiments_by_obligation={"obl-auth": experiment},
        behavior_ir=_behavior_ir(),
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test", "environment_ref": "test-env"},
        mainline_run=mainline,
        campaign_id="campaign",
    )

    execution_receipt = batch["execution_results"]["obl-auth"]
    assert execution_receipt["status"] == terminal_status
    assert execution_receipt["selected_obligation_id"] == "obl-auth"
    assert execution_receipt["executed_obligation_id"] == variant_id
    ledger = build_obligation_attempt_ledger(
        mainline_run=mainline,
        selected=[{"obligation_id": "obl-auth", "experiment_id": "exp-auth"}],
        compile_results=batch["compile_results"],
        execution_results=batch["execution_results"],
        gate_results=batch["gate_results"],
    )
    validate_obligation_attempt_ledger(ledger)
    assert ledger["attempts"][0]["terminal_status"] == terminal_status
    assert ledger["attempts"][0]["executed_obligation_id"] == variant_id
