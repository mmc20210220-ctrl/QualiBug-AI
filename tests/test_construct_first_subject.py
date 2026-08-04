"""Construct-first / reuse-fallback subject selection (prefer_constructed_data).

A run-constructed subject is disposable: its cleanup is the run's own
responsibility and a failed assertion against it cannot damage anything
the customer depends on. Existing test-system data is the documented
fallback, used only when construction is unavailable or fails — and
flagged non-disposable on the binding receipt. Identity, observed-body,
state-scoped, and internal ``__`` targets keep their dedicated semantics
and never take the construct-first path.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from ai_test_asset_center.experiment_fixture_materializer_core import (
    materialize_experiment_fixtures,
)


def _things_ir(*, with_compensator: bool = True, with_create: bool = True) -> dict:
    operations = [
        {
            "id": "op-list-things",
            "method": "GET",
            "path": "/api/things",
            "source_refs": [
                {
                    "source_id": "api_spec",
                    "locator": "GET /api/things",
                    "kind": "api_operation",
                }
            ],
        },
    ]
    if with_create:
        operations.append({
            "id": "op-create-thing",
            "method": "POST",
            "path": "/api/things",
            "request_example": {"name": "widget"},
            "source_refs": [
                {
                    "source_id": "api_spec",
                    "locator": "POST /api/things",
                    "kind": "api_operation",
                }
            ],
        })
    relations: list[dict] = []
    if with_compensator and with_create:
        operations.append({
            "id": "op-delete-thing",
            "method": "DELETE",
            "path": "/api/things/{id}",
            "source_refs": [
                {
                    "source_id": "api_spec",
                    "locator": "DELETE /api/things/{id}",
                    "kind": "api_operation",
                }
            ],
        })
        relations.append({
            "relation_type": "compensates",
            "from_ref": "op-delete-thing",
            "to_ref": "op-create-thing",
            "operation_ref": "op-delete-thing",
            "status": "accepted",
            "source_refs": [{"source_id": "api_spec"}],
        })
    return {"operations": operations, "actors": [], "relations": relations}


def _binding(**overrides: object) -> dict:
    binding: dict = {
        "target": "thing_id",
        "target_path": "/{thing_id}",
        "status": "runtime_resolvable",
        "resolver_operations": [
            {
                "operation_ref": "op-list-things",
                "method": "GET",
                "path": "/api/things",
            }
        ],
    }
    binding.update(overrides)
    return binding


def _inputs(ir: dict, binding: dict, **overrides: object) -> dict:
    experiment = {
        "experiment_id": "exp_construct_first",
        "obligation_id": "obl_construct_first",
        "environment_type": "test",
        "fixture_dag": {
            "status": "READY",
            "setup_order": ["fix_bind_thing"],
            "nodes": [
                {
                    "node_id": "fix_bind_thing",
                    "kind": "runtime_read_binding",
                    "target": binding["target"],
                    "constructible": True,
                }
            ],
        },
        "binding_plan": [binding],
        "control_plan": [
            {
                "actor_ref": "actor-buyer-a",
                "operation_ref": "op-list-things",
                "path": "/api/things",
            }
        ],
        "treatment_plan": [],
        "observers": [{"observer_id": "http_response", "surface": "http_api"}],
        "assertions": [{"kind": "state_transition"}],
        "safety_contract": {"governed_write": True, "cleanup_not_required": False},
        "compiled_adapters": ["http_api"],
    }
    inputs: dict = {
        "exp": experiment,
        "eid": "exp_construct_first",
        "oid": "obl_construct_first",
        "resolved_campaign_id": "CMP_test",
        "resolved_execution_id": "EXEC_test",
        "started": time.time(),
        "actors": {
            "actor-buyer-a": {
                "id": "actor-buyer-a",
                "role": "buyer",
                "credential_secret_ref": "secret:buyer_a",
            }
        },
        "ops": {row["id"]: row for row in ir["operations"]},
        "tokens": {"secret:buyer_a": "token-a", "buyer": "token-a"},
        "binding_plan": {binding["target"]: dict(binding)},
        "resolver_actor_ref": "actor-buyer-a",
        "resolver_token": "token-a",
        "activation_requirements": {"actor": [], "fixture": [], "cleanup": []},
        "root": Path("."),
        "project": "test-project",
        "base_url": "http://target.test",
        "runtime_contract": {
            "status": "approved",
            "approved_base_url": "http://target.test",
        },
        "campaign_id": "CMP_test",
        "behavior_ir": ir,
    }
    inputs.update(overrides)
    return inputs


def _fake_list_read(calls: list[str], rows: object) -> object:
    def fake_run_http_step(**kwargs: object) -> dict:
        path = str(kwargs.get("path") or "")
        calls.append(f"{kwargs.get('method')} {path}")
        return {
            "method": str(kwargs.get("method") or "GET"),
            "path": path,
            "status_code": 200,
            "body": rows,
            "headers": {},
            "duration_ms": 1,
            "error": "",
            "raw": {},
        }

    return fake_run_http_step


def _fake_governed(calls: list[str], status: int, body: object) -> object:
    def fake_governed_write(**kwargs: object) -> dict:
        calls.append(f"{kwargs.get('method')} {kwargs.get('path')}")
        return {
            "accepted": 200 <= status < 300,
            "write": {
                "status": status,
                "body": body,
                "path": str(kwargs.get("path") or ""),
            },
            "after": {"status": 200, "body": [body] if isinstance(body, dict) else []},
            "after_ref": "after-ref-1",
        }

    return fake_governed_write


def _patch_transport(
    monkeypatch: pytest.MonkeyPatch,
    list_rows: object,
    governed_status: int,
    governed_body: object,
) -> tuple[list[str], list[str]]:
    list_calls: list[str] = []
    governed_calls: list[str] = []
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core._run_http_step",
        _fake_list_read(list_calls, list_rows),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core."
        "execute_governed_control_write",
        _fake_governed(governed_calls, governed_status, governed_body),
    )
    return list_calls, governed_calls


def test_construct_first_preferred_over_existing_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST+DELETE declared and the list has reusable rows: construction
    still wins — the disposable subject can never damage customer data."""
    ir = _things_ir()
    list_calls, governed_calls = _patch_transport(
        monkeypatch,
        [{"id": "existing-1", "name": "someone-elses-widget"}],
        201,
        {"id": "constructed-1", "name": "widget"},
    )
    result = materialize_experiment_fixtures(**_inputs(ir, _binding()))
    assert result["status"] == "ready"
    assert result["runtime_bindings"].get("thing_id") == "constructed-1"
    assert governed_calls == ["POST /api/things"]
    assert not list_calls, "construction must be attempted before any reuse"
    receipt = result["binding_materialization_receipts"][0]
    assert receipt["data_subject_source"] == "run_constructed"
    assert receipt["data_subject_disposable"] is True
    pending = result["pending_fixture_cleanups"]
    assert pending[0]["cleanup"].get("method") == "DELETE"
    assert "accepted_residue" not in pending[0]


def test_reuse_fallback_when_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create reaches transport but fails (5xx): fall back to existing data,
    flagged non-disposable with the documented note."""
    ir = _things_ir()
    list_calls, governed_calls = _patch_transport(
        monkeypatch,
        [{"id": "existing-1", "name": "someone-elses-widget"}],
        500,
        {"error": "boom"},
    )
    result = materialize_experiment_fixtures(**_inputs(ir, _binding()))
    assert result["status"] == "ready"
    assert result["runtime_bindings"].get("thing_id") == "existing-1"
    assert governed_calls == ["POST /api/things"]
    assert list_calls == ["GET /api/things"]
    receipt = result["binding_materialization_receipts"][0]
    assert receipt["data_subject_source"] == "existing_test_system_data"
    assert receipt["data_subject_disposable"] is False
    assert "not run-created" in receipt["data_subject_note"]


def test_reuse_first_when_no_construct_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No POST on the collection: the existing reuse path is unchanged and
    the receipt records the existing-data subject."""
    ir = _things_ir(with_create=False, with_compensator=False)
    list_calls, governed_calls = _patch_transport(
        monkeypatch,
        [{"id": "existing-1"}],
        201,
        {"id": "constructed-1"},
    )
    result = materialize_experiment_fixtures(**_inputs(ir, _binding()))
    assert result["status"] == "ready"
    assert result["runtime_bindings"].get("thing_id") == "existing-1"
    assert list_calls == ["GET /api/things"]
    assert not governed_calls
    receipt = result["binding_materialization_receipts"][0]
    assert receipt["data_subject_source"] == "existing_test_system_data"


def test_identity_binding_never_takes_construct_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owner-identity bindings must read the caller's owned rows — creating
    a fixture cannot substitute the consensus semantics."""
    ir = _things_ir()
    list_calls, governed_calls = _patch_transport(
        monkeypatch,
        [{"id": "t1", "user_id": "u-a"}, {"id": "t2", "user_id": "u-a"}],
        201,
        {"id": "constructed-1", "user_id": "u-a"},
    )
    binding = _binding(
        target="user_id",
        target_path="/{user_id}",
        identity_extraction="owner_field_consensus",
        fixture_owner_actor_ref="actor-buyer-a",
    )
    result = materialize_experiment_fixtures(**_inputs(ir, binding))
    assert result["status"] == "ready"
    assert result["runtime_bindings"].get("user_id") == "u-a"
    assert not governed_calls
    assert list_calls == ["GET /api/things"]


def test_state_scoped_binding_never_takes_construct_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """State-scoped selection must find an entity already in the required
    state; a fresh create cannot be assumed to be there."""
    ir = _things_ir()
    list_calls, governed_calls = _patch_transport(
        monkeypatch,
        [
            {"id": "t-open", "status": "OPEN"},
            {"id": "t-closed", "status": "CLOSED"},
        ],
        201,
        {"id": "constructed-1", "status": "OPEN"},
    )
    binding = _binding(
        target_path="@state=closed@/{thing_id}",
        selection_semantics="source_state_precondition",
        required_state="CLOSED",
    )
    result = materialize_experiment_fixtures(**_inputs(ir, binding))
    assert result["status"] == "ready"
    assert result["runtime_bindings"].get("thing_id") == "t-closed"
    assert not governed_calls
    assert list_calls == ["GET /api/things"]
