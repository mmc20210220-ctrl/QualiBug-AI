"""Accepted-residue fixture construction (non-production degradation).

Disposable fixture creates used to be held hostage by cleanup guarantees:
no source-declared compensator -> the fixture setup was refused and the
experiment blocked, even on declared non-production targets where the
cleanup ladder already accepts declared residue. The construction path now
honors the same ladder: the auto-fixture creator attaches an explicit
``accepted_residue`` marker only when the environment gate allows it, the
validator preserves the marker instead of refusing, and the cleanup phase
emits a RESIDUE_ACCEPTED receipt — the leftover resource stays visible,
never disguised as a real cleanup. Production/undeclared environments
stay fail-closed at every layer.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from ai_test_asset_center.experiment_cleanup_executor_core import (
    execute_experiment_cleanup_compensation,
)
from ai_test_asset_center.experiment_fixture_materializer_core import (
    _auto_fixture_create_for_binding_target,
    materialize_experiment_fixtures,
)
from ai_test_asset_center.runtime_binding_materializer import (
    validated_fixture_setup,
)


def _things_ir(*, with_compensator: bool = False) -> dict:
    operations = [
        {
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
        },
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
    relations: list[dict] = []
    if with_compensator:
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


def _things_ops(ir: dict) -> dict:
    return {row["id"]: row for row in ir["operations"]}


def _actors() -> dict:
    return {
        "actor-buyer-a": {
            "id": "actor-buyer-a",
            "role": "buyer",
            "credential_secret_ref": "secret:buyer_a",
        }
    }


def _binding() -> dict:
    return {
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


# ── Auto-create: environment-gated residue marker ───────────────────────


def test_auto_create_without_compensator_refused_when_residue_not_allowed() -> None:
    ir = _things_ir()
    auto = _auto_fixture_create_for_binding_target(
        "thing_id",
        _binding(),
        _things_ops(ir),
        {},
        actors=_actors(),
        behavior_ir=ir,
        accepted_residue_allowed=False,
    )
    assert auto is None


def test_auto_create_without_compensator_marks_residue_when_allowed() -> None:
    ir = _things_ir()
    auto = _auto_fixture_create_for_binding_target(
        "thing_id",
        _binding(),
        _things_ops(ir),
        {},
        actors=_actors(),
        behavior_ir=ir,
        accepted_residue_allowed=True,
    )
    assert auto is not None
    setup = auto["fixture_setup"]
    assert setup["operation_ref"] == "op-create-thing"
    assert setup["cleanup_operations"] == []
    assert setup["accepted_residue"] == {
        "mode": "accepted_residue_no_cleanup",
        "residue_notice": "no_source_compensator:op-create-thing",
    }


def test_auto_create_with_compensator_never_marks_residue() -> None:
    ir = _things_ir(with_compensator=True)
    auto = _auto_fixture_create_for_binding_target(
        "thing_id",
        _binding(),
        _things_ops(ir),
        {},
        actors=_actors(),
        behavior_ir=ir,
        accepted_residue_allowed=True,
    )
    assert auto is not None
    setup = auto["fixture_setup"]
    assert "accepted_residue" not in setup
    assert any(
        row.get("method") == "DELETE" for row in setup["cleanup_operations"]
    )


# ── Validator: marker preservation, fail-closed default ─────────────────


def _setup_entry(**overrides: object) -> dict:
    setup: dict = {
        "fixture_setup": {
            "operation_ref": "op-create-thing",
            "method": "POST",
            "path": "/api/things",
            "actor_refs": ["actor-buyer-a"],
            "cleanup_operations": [],
        }
    }
    setup.update(overrides)
    return setup


def test_validator_refuses_missing_cleanup_without_marker() -> None:
    ir = _things_ir()
    assert validated_fixture_setup(
        _setup_entry(), _things_ops(ir), _actors()
    ) == {}


def test_validator_preserves_residue_marker() -> None:
    ir = _things_ir()
    validated = validated_fixture_setup(
        _setup_entry(
            fixture_setup={
                "operation_ref": "op-create-thing",
                "method": "POST",
                "path": "/api/things",
                "actor_refs": ["actor-buyer-a"],
                "cleanup_operations": [],
                "accepted_residue": {
                    "mode": "accepted_residue_no_cleanup",
                    "residue_notice": "no_source_compensator:op-create-thing",
                },
            }
        ),
        _things_ops(ir),
        _actors(),
    )
    assert validated["cleanup_operations"] == []
    assert validated["accepted_residue"]["mode"] == "accepted_residue_no_cleanup"
    assert (
        validated["accepted_residue"]["residue_notice"]
        == "no_source_compensator:op-create-thing"
    )
    assert validated["actor_refs"] == ["actor-buyer-a"]


def test_validator_refuses_unknown_residue_mode() -> None:
    ir = _things_ir()
    assert validated_fixture_setup(
        _setup_entry(
            fixture_setup={
                "operation_ref": "op-create-thing",
                "method": "POST",
                "path": "/api/things",
                "actor_refs": ["actor-buyer-a"],
                "cleanup_operations": [],
                "accepted_residue": {"mode": "pretend_cleanup"},
            }
        ),
        _things_ops(ir),
        _actors(),
    ) == {}


# ── Materializer: residue fixture end-to-end ────────────────────────────


def _residue_experiment(environment_type: str) -> dict:
    return {
        "experiment_id": "exp_residue_fixture",
        "obligation_id": "obl_residue_fixture",
        "environment_type": environment_type,
        "fixture_dag": {
            "status": "READY",
            "setup_order": ["fix_bind_thing"],
            "nodes": [
                {
                    "node_id": "fix_bind_thing",
                    "kind": "runtime_read_binding",
                    "target": "thing_id",
                    "constructible": True,
                }
            ],
        },
        "binding_plan": [_binding()],
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


def _residue_inputs(environment_type: str = "test", **overrides: object) -> dict:
    ir = _things_ir()
    inputs: dict = {
        "exp": _residue_experiment(environment_type),
        "eid": "exp_residue_fixture",
        "oid": "obl_residue_fixture",
        "resolved_campaign_id": "CMP_test",
        "resolved_execution_id": "EXEC_test",
        "started": time.time(),
        "actors": _actors(),
        "ops": _things_ops(ir),
        "tokens": {"secret:buyer_a": "token-a", "buyer": "token-a"},
        "binding_plan": {"thing_id": _binding()},
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


def _empty_list_step(**kwargs: object) -> dict:
    return {
        "method": str(kwargs.get("method") or "GET"),
        "path": str(kwargs.get("path") or ""),
        "status_code": 200,
        "body": [],
        "headers": {},
        "duration_ms": 1,
        "error": "",
        "raw": {},
    }


def _created_thing_governed_write(calls: list[dict]) -> object:
    def fake_governed_write(**kwargs: object) -> dict:
        calls.append(dict(kwargs))
        return {
            "accepted": True,
            "write": {
                "status": 201,
                "body": {"id": "thing-1", "name": "widget"},
                "path": str(kwargs.get("path") or ""),
            },
            "after": {"status": 200, "body": [{"id": "thing-1"}]},
            "after_ref": "after-ref-1",
        }

    return fake_governed_write


def test_materializer_constructs_residue_fixture_on_nonproduction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core._run_http_step",
        _empty_list_step,
    )
    governed_calls: list[dict] = []
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core."
        "execute_governed_control_write",
        _created_thing_governed_write(governed_calls),
    )
    result = materialize_experiment_fixtures(**_residue_inputs("test"))
    assert result["status"] == "ready"
    assert result["runtime_bindings"].get("thing_id") == "thing-1"
    assert len(governed_calls) == 1
    assert governed_calls[0].get("method") == "POST"
    pending = result["pending_fixture_cleanups"]
    assert len(pending) == 1
    assert pending[0]["accepted_residue"] == {
        "mode": "accepted_residue_no_cleanup",
        "residue_notice": "no_source_compensator:op-create-thing",
    }
    assert pending[0]["cleanup"] == {}


def test_materializer_stays_blocked_without_declared_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core._run_http_step",
        _empty_list_step,
    )
    governed_calls: list[dict] = []
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core."
        "execute_governed_control_write",
        _created_thing_governed_write(governed_calls),
    )
    result = materialize_experiment_fixtures(**_residue_inputs(""))
    assert result["status"] == "terminal"
    assert result["result"]["status"] == "BLOCKED"
    assert not governed_calls, "undeclared environment must not create fixtures"


# ── Cleanup phase: RESIDUE_ACCEPTED receipt, no transport ───────────────


def test_cleanup_phase_emits_residue_receipt_without_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_transport(**kwargs: object) -> dict:
        raise AssertionError(
            "residue fixture must not fire a cleanup write at an empty path"
        )

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_cleanup_executor_core."
        "execute_governed_control_write",
        forbidden_transport,
    )
    steps_out: list[dict] = []
    observations: dict = {}
    receipts: list[dict] = []
    pending_receipt: dict = {"status": "BOUND"}
    pending = [
        {
            "target": "thing_id",
            "value": "thing-1",
            "observation_path": "/api/things",
            "cleanup": {},
            "receipt": pending_receipt,
            "actor_ref": "actor-buyer-a",
            "actor_identity": "buyer",
            "actor_token": "token-a",
            "governed_setup": {"accepted": True},
            "accepted_residue": {
                "mode": "accepted_residue_no_cleanup",
                "residue_notice": "no_source_compensator:op-create-thing",
            },
        }
    ]
    result = execute_experiment_cleanup_compensation(
        exp={"safety_contract": {}},
        steps_out=steps_out,
        observations=observations,
        contract_evidence_receipts=receipts,
        activation_requirements={"actor": [], "fixture": [], "cleanup": []},
        pre_transport_block_reasons=[],
        request_bodies_for_cleanup={},
        runtime_bindings={},
        pending_fixture_cleanups=pending,
        cleanup_failures=0,
        ops={},
        actors=_actors(),
        tokens={},
        eid="exp_residue_fixture",
        oid="obl_residue_fixture",
        resolved_campaign_id="CMP_test",
        resolved_execution_id="EXEC_test",
        campaign_id="CMP_test",
        root=Path("."),
        project="test-project",
        base_url="http://target.test",
        runtime_contract={"status": "approved"},
    )
    residue_receipts = [
        row
        for row in receipts
        if row.get("kind") == "cleanup"
        and str(row.get("status")).upper() == "RESIDUE_ACCEPTED"
    ]
    assert len(residue_receipts) == 1
    evidence = residue_receipts[0]["evidence"]
    assert evidence["residue"] is True
    assert evidence["cleanup_write_count"] == 0
    assert evidence["accepted_write_count"] == 1
    assert evidence["residue_notice"] == "no_source_compensator:op-create-thing"
    assert residue_receipts[0]["subject_id"] == "fixture_cleanup:thing_id"
    assert pending_receipt["fixture_cleanup_status"] == "residue_accepted"
    assert observations["cleanup_status"] == "residue_accepted"
    assert observations["cleanup_residue"] is True
    assert result["cleanup_failures"] == 0
    residue_steps = [
        step
        for step in steps_out
        if step.get("cleanup_mode") == "accepted_residue_no_cleanup"
    ]
    assert len(residue_steps) == 1
