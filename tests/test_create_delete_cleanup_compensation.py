"""Create→DELETE cleanup must compile and execute governed DELETE compensation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center.experiment_compiler_base import compile_experiment_for_obligation
from ai_test_asset_center.experiment_executor import execute_one_experiment
from ai_test_asset_center.write_reversibility_contract import build_reversibility_proof


def _create_delete_ir() -> dict:
    request_schema = {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}},
    }
    return {
        "operations": [
            {
                "id": "op-create",
                "operation_id": "create_resource",
                "method": "POST",
                "path": "/resources",
                "read_write": "write",
                "request_example": {"name": "item"},
                "request_schema": request_schema,
                "source_refs": [{"source_id": "api", "locator": "POST /resources"}],
            },
            {
                "id": "op-list",
                "operation_id": "list_resources",
                "method": "GET",
                "path": "/resources",
                "read_write": "read",
                "source_refs": [{"source_id": "api", "locator": "GET /resources"}],
            },
            {
                "id": "op-read",
                "operation_id": "read_resource",
                "method": "GET",
                "path": "/resources/{id}",
                "read_write": "read",
                "source_refs": [{"source_id": "api", "locator": "GET /resources/{id}"}],
            },
            {
                "id": "op-delete",
                "operation_id": "delete_resource",
                "method": "DELETE",
                "path": "/resources/{id}",
                "read_write": "write",
                "source_refs": [{"source_id": "api", "locator": "DELETE /resources/{id}"}],
            },
        ],
        "actors": [
            {
                "id": "actor-owner",
                "role": "owner",
                "credential_secret_ref": "secret_ref:owner",
                "account_status": "active",
            },
        ],
        "relations": [
            {
                "id": "rel-compensate",
                "relation_type": "compensates",
                "from": "op-delete",
                "to": "op-create",
                "operation_ref": "op-delete",
            },
        ],
    }


def test_compile_create_delete_emits_reverse_order_delete_cleanup() -> None:
    obligation = {
        "obligation_id": "obl-create-delete-cleanup",
        "risk_family": "validation",
        "property": {
            "template": "schema_constraint",
            "operation_ref": "op-create",
            "actor_ref": "actor-owner",
        },
        "required_actors": ["actor-owner"],
        "required_operations": ["op-create"],
        "required_fixtures": [],
        "required_observers": ["http_response"],
        "cleanup_requirement": {
            "required": True,
            "operation_ref": "op-delete",
            "mode": "reverse_order",
        },
        "source_refs": [{"source_id": "api", "locator": "POST /resources"}],
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=_create_delete_ir(),
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment["compile_receipt"]
    assert experiment["cleanup_plan"] == [
        {
            "action": "reverse_order_compensation",
            "mode": "delete_created_resource",
            "operation_ref": "op-delete",
            "compensates_operation_ref": "op-create",
            "path": "/resources/{id}",
            "method": "DELETE",
            "runtime_response_binding_required": True,
            "source_step_id": "treatment_1",
        },
        {
            "action": "reverse_order_compensation",
            "mode": "delete_created_resource",
            "operation_ref": "op-delete",
            "compensates_operation_ref": "op-create",
            "path": "/resources/{id}",
            "method": "DELETE",
            "runtime_response_binding_required": True,
            "source_step_id": "control_1",
        },
    ]


def _run_create_delete_experiment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    cleanup_plan: list[dict],
    execution_id: str,
) -> tuple[dict, dict[str, dict[str, object]], list[tuple[str, str]]]:
    resources: dict[str, dict[str, object]] = {}
    seen: list[tuple[str, str]] = []

    def fake_http(method: str, url: str, **kwargs):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        body = kwargs.get("body")
        seen.append((method, path))
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
    monkeypatch.setattr(
        "ai_test_asset_center.sandbox_write_executor_base._http_request",
        fake_http,
    )

    experiment = {
        "schema_version": "qualibug.experiment.v1",
        "experiment_id": execution_id,
        "obligation_id": "obl-create-delete-cleanup",
        "campaign_id": "campaign",
        "control_plan": [{
            "step_id": "control_1",
            "actor_ref": "actor-owner",
            "operation_ref": "op-create",
            "body": {"name": "valid"},
        }],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": "actor-owner",
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
                    "operation_ref": "op-read",
                    "method": "GET",
                    "path": "/resources/{id}",
                }],
            },
        ],
        "cleanup_plan": cleanup_plan,
        "safety_contract": {"environment_type": "test", "governed_write": True},
        "source_refs": [{"source_id": "api", "kind": "api_operation"}],
        "compile_receipt": {"status": "COMPILED", "reason_code": ""},
    }

    # SPEC v1.1: attach write reversibility proof for governed writes.
    _ir = _create_delete_ir()
    _proof = build_reversibility_proof(
        primary_operation_ref="op-create",
        primary_method="POST",
        primary_path="/resources",
        cleanup_plan=cleanup_plan,
        behavior_ir=_ir,
        experiment=experiment,
    )
    experiment["write_reversibility_proof"] = _proof
    experiment["compile_receipt"]["write_reversibility_fingerprint"] = _proof.get(
        "fingerprint", ""
    )

    result = execute_one_experiment(
        experiment,
        behavior_ir=_ir,
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
        execution_id=execution_id,
        actor_tokens={"secret_ref:owner": "owner-token"},
    )
    return result, resources, seen


def test_executor_runs_delete_cleanup_for_accepted_creates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result, resources, seen = _run_create_delete_experiment(
        monkeypatch,
        tmp_path,
        cleanup_plan=[
            {
                "action": "reverse_order_compensation",
                "mode": "delete_created_resource",
                "operation_ref": "op-delete",
                "compensates_operation_ref": "op-create",
                "path": "/resources/{id}",
                "method": "DELETE",
                "runtime_response_binding_required": True,
                "source_step_id": "treatment_1",
            },
            {
                "action": "reverse_order_compensation",
                "mode": "delete_created_resource",
                "operation_ref": "op-delete",
                "compensates_operation_ref": "op-create",
                "path": "/resources/{id}",
                "method": "DELETE",
                "runtime_response_binding_required": True,
                "source_step_id": "control_1",
            },
        ],
        execution_id="execution-create-delete-cleanup",
    )

    delete_http = [item for item in seen if item[0] == "DELETE"]
    delete_steps = [
        step
        for step in (result.get("steps") or [])
        if step.get("phase") == "cleanup" and step.get("method") == "DELETE"
    ]
    assert delete_http, json.dumps(result, default=str, indent=2)
    assert delete_steps, result.get("steps")
    assert all(200 <= int(step.get("status_code") or 0) < 300 for step in delete_steps)
    assert resources == {}


def test_executor_source_declared_delete_cleanup_still_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Defense in depth: legacy source_declared DELETE plans must not no-op."""
    result, resources, seen = _run_create_delete_experiment(
        monkeypatch,
        tmp_path,
        cleanup_plan=[
            {
                "action": "source_declared_compensation",
                "mode": "delete_created_resource",
                "operation_ref": "op-delete",
                "compensates_operation_ref": "op-create",
                "path": "/resources/{id}",
                "method": "DELETE",
                "body_from_original_request": False,
                "runtime_response_binding_required": True,
                "source_step_id": "treatment_1",
            },
            {
                "action": "source_declared_compensation",
                "mode": "delete_created_resource",
                "operation_ref": "op-delete",
                "compensates_operation_ref": "op-create",
                "path": "/resources/{id}",
                "method": "DELETE",
                "body_from_original_request": False,
                "runtime_response_binding_required": True,
                "source_step_id": "control_1",
            },
        ],
        execution_id="execution-legacy-source-declared-delete",
    )

    delete_http = [item for item in seen if item[0] == "DELETE"]
    assert delete_http, {
        "seen": seen,
        "status": result.get("status"),
        "reason_code": result.get("reason_code"),
        "detail": result.get("detail"),
        "cleanup_reason": (result.get("observations") or {}).get("cleanup_reason"),
        "steps": [
            {
                "phase": step.get("phase"),
                "method": step.get("method"),
                "path": step.get("path"),
                "status_code": step.get("status_code"),
            }
            for step in (result.get("steps") or [])
        ],
    }
    assert resources == {}


def test_cleanup_uses_each_write_actor_for_actor_scoped_collections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cross-actor creates must DELETE under the creating actor's credentials."""
    carts: dict[str, dict[str, dict[str, object]]] = {
        "owner-token": {},
        "viewer-token": {},
    }
    cleanup_tokens: list[str] = []

    def fake_http(method: str, url: str, **kwargs):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        token = str(kwargs.get("token") or "")
        body = kwargs.get("body")
        bucket = carts.setdefault(token, {})
        if method == "GET" and path == "/carts":
            return {
                "status": 200,
                "body": {"items": list(bucket.values())},
                "headers": {},
            }
        if method == "POST" and path == "/carts":
            item_id = f"{token}-item"
            row = {"id": item_id, "name": (body or {}).get("name")}
            bucket[item_id] = dict(row)
            return {"status": 201, "body": dict(row), "headers": {}}
        if method == "DELETE" and path.startswith("/carts/"):
            cleanup_tokens.append(token)
            item_id = path.rsplit("/", 1)[-1]
            if item_id not in bucket:
                return {"status": 404, "body": {"error": "missing"}, "headers": {}}
            bucket.pop(item_id, None)
            return {"status": 200, "body": {"deleted": True}, "headers": {}}
        raise AssertionError(f"unexpected request: {method} {path} token={token}")

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
    monkeypatch.setattr(
        "ai_test_asset_center.sandbox_write_executor_base._http_request",
        fake_http,
    )

    behavior_ir = {
        "operations": [
            {
                "id": "op-create",
                "method": "POST",
                "path": "/carts",
                "read_write": "write",
                "request_example": {"name": "item"},
                "source_refs": [{"source_id": "api", "locator": "POST /carts"}],
            },
            {
                "id": "op-list",
                "method": "GET",
                "path": "/carts",
                "read_write": "read",
                "source_refs": [{"source_id": "api", "locator": "GET /carts"}],
            },
            {
                "id": "op-delete",
                "method": "DELETE",
                "path": "/carts/{id}",
                "read_write": "write",
                "source_refs": [{"source_id": "api", "locator": "DELETE /carts/{id}"}],
            },
        ],
        "actors": [
            {
                "id": "actor-owner",
                "role": "owner",
                "credential_secret_ref": "secret_ref:owner",
                "account_status": "active",
            },
            {
                "id": "actor-viewer",
                "role": "viewer",
                "credential_secret_ref": "secret_ref:viewer",
                "account_status": "active",
            },
        ],
    }
    experiment = {
        "schema_version": "qualibug.experiment.v1",
        "experiment_id": "exp-cross-actor-cleanup",
        "obligation_id": "obl-cross-actor-cleanup",
        "campaign_id": "campaign",
        "control_plan": [{
            "step_id": "control_1",
            "actor_ref": "actor-owner",
            "operation_ref": "op-create",
            "body": {"name": "control"},
        }],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": "actor-viewer",
            "operation_ref": "op-create",
            "body": {"name": "treatment"},
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
                    "operation_ref": "op-list",
                    "method": "GET",
                    "path": "/carts",
                }],
            },
        ],
        "cleanup_plan": [
            {
                "action": "reverse_order_compensation",
                "mode": "delete_created_resource",
                "operation_ref": "op-delete",
                "compensates_operation_ref": "op-create",
                "path": "/carts/{id}",
                "method": "DELETE",
                "runtime_response_binding_required": True,
                "source_step_id": "treatment_1",
            },
            {
                "action": "reverse_order_compensation",
                "mode": "delete_created_resource",
                "operation_ref": "op-delete",
                "compensates_operation_ref": "op-create",
                "path": "/carts/{id}",
                "method": "DELETE",
                "runtime_response_binding_required": True,
                "source_step_id": "control_1",
            },
        ],
        "safety_contract": {"environment_type": "test", "governed_write": True},
        "source_refs": [{"source_id": "api", "kind": "api_operation"}],
        "compile_receipt": {"status": "COMPILED", "reason_code": ""},
    }

    # SPEC v1.1: attach write reversibility proof for governed writes.
    _proof_cross = build_reversibility_proof(
        primary_operation_ref="op-create",
        primary_method="POST",
        primary_path="/carts",
        cleanup_plan=experiment["cleanup_plan"],
        behavior_ir=behavior_ir,
        experiment=experiment,
    )
    experiment["write_reversibility_proof"] = _proof_cross
    experiment["compile_receipt"]["write_reversibility_fingerprint"] = _proof_cross.get(
        "fingerprint", ""
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
        execution_id="execution-cross-actor-cleanup",
        actor_tokens={
            "secret_ref:owner": "owner-token",
            "secret_ref:viewer": "viewer-token",
        },
    )

    cleanup_steps = [
        step
        for step in (result.get("steps") or [])
        if step.get("phase") == "cleanup" and step.get("method") == "DELETE"
    ]
    assert len(cleanup_steps) >= 2, result.get("steps")
    assert {step.get("actor_ref") for step in cleanup_steps} == {
        "actor-owner",
        "actor-viewer",
    }
    assert set(cleanup_tokens) == {"owner-token", "viewer-token"}, cleanup_tokens
    assert carts["owner-token"] == {}
    assert carts["viewer-token"] == {}
    assert all(200 <= int(step.get("status_code") or 0) < 300 for step in cleanup_steps)
