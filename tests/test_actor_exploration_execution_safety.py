import hashlib
import json
from pathlib import Path

from ai_test_asset_center import experiment_executor
from ai_test_asset_center import _experiment_executor_mainline_mechanics as _executor_mechanics
from ai_test_asset_center.actor_exploration_execution import (
    apply_actor_execution_overlay,
    exploration_execution_policy,
    extract_primary_http_attempt_evidence,
    should_continue_actor_exploration,
)


def _canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sealed_actor_plan(candidate_ids, *, max_attempts=2, source_actor_id="actor-a"):
    """Compiler-sealed top-level actor plan (same contract the compiler emits)."""
    plan = {
        "schema_version": "qualibug.actor-execution-plan.v1",
        "mode": "permission_exploration",
        "source_actor_id": source_actor_id,
        "candidate_ids": list(candidate_ids),
        "authorization_oracle_enabled": False,
        "max_attempts": max_attempts,
        "reason": "",
        "authority": "compiled_actor_execution_plan",
    }
    plan["plan_hash"] = hashlib.sha256(
        _canonical(plan).encode("utf-8")
    ).hexdigest()
    return plan


def _experiment(method="GET", path="/api/items", cleanup=False, owner=True):
    experiment = {
        "experiment_id": "exp-actor-exploration",
        "obligation_id": "obl-actor-exploration",
        "required_actors": ["actor-a"],
        "actor_execution_plan": _sealed_actor_plan(["actor-a", "actor-b"]),
        "property": {
            "operation_ref": "op",
            "actor_ref": "actor-a",
        },
        "control_plan": [
            {
                "phase": "control",
                "operation_ref": "op",
                "actor_ref": "actor-a",
            }
        ],
        "treatment_plan": [
            {
                "phase": "treatment",
                "operation_ref": "op",
                "actor_ref": "actor-a",
            }
        ],
        "binding_plan": (
            [
                {
                    "fixture_owner_actor_ref": "actor-a",
                    "resolver_operations": [
                        {"resolver_actor_ref": "actor-a"}
                    ],
                }
            ]
            if owner
            else []
        ),
        "cleanup_plan": (
            [{"action": "restore", "actor_ref": "actor-a"}]
            if cleanup
            else []
        ),
        "write_reversibility_proof": (
            {
                "proof_status": "PROVEN",
                "proof_kind": "field_snapshot_restore",
                "cleanup_authority": {
                    "kind": "field_snapshot_restore",
                },
            }
            if cleanup
            else {}
        ),
    }
    behavior_ir = {
        "actors": [
            {
                "id": "actor-a",
                "role": "one",
                "credential_secret_ref": "secret_ref:test_accounts:actor-a",
            },
            {
                "id": "actor-b",
                "role": "two",
                "credential_secret_ref": "secret_ref:test_accounts:actor-b",
            },
        ],
        "operations": [
            {"id": "op", "method": method, "path": path}
        ],
    }
    return experiment, behavior_ir


def test_overlay_rebinds_compiled_steps_and_resolvers():
    experiment, _ = _experiment()
    rebound, receipt = apply_actor_execution_overlay(experiment, "actor-b")

    assert rebound["property"]["actor_ref"] == "actor-b"
    assert rebound["required_actors"] == ["actor-b"]
    assert rebound["control_plan"][0]["actor_ref"] == "actor-b"
    assert rebound["treatment_plan"][0]["actor_ref"] == "actor-b"
    assert rebound["binding_plan"][0]["fixture_owner_actor_ref"] == "actor-b"
    assert (
        rebound["binding_plan"][0]["resolver_operations"][0]["resolver_actor_ref"]
        == "actor-b"
    )
    assert receipt["status"] == "APPLIED"


def test_http_status_is_read_from_target_step_not_lifecycle_status():
    evidence = extract_primary_http_attempt_evidence(
        {
            "status": "EXECUTED",
            "execution_receipt": {"status": "EXECUTED"},
            "steps": [
                {
                    "phase": "control",
                    "operation_ref": "op",
                    "actor_ref": "actor-a",
                    "status_code": 403,
                },
                {
                    "phase": "treatment",
                    "operation_ref": "op",
                    "actor_ref": "actor-b",
                    "status_code": 200,
                },
            ],
        },
        "op",
    )

    assert evidence.status_code == 200
    assert evidence.actor_ref == "actor-b"
    assert evidence.phase == "treatment"


def test_lifecycle_string_without_http_evidence_is_unknown():
    evidence = extract_primary_http_attempt_evidence(
        {"execution_receipt": {"status": "EXECUTED"}},
        "op",
    )
    assert evidence.status_code == 0
    assert evidence.source == "missing_http_evidence"


def test_write_unknown_side_effect_never_retries_another_actor():
    assert should_continue_actor_exploration(
        method="PATCH",
        outcome="infrastructure_failed",
        status_code=0,
    ) == (False, "write_side_effect_unknown")
    assert should_continue_actor_exploration(
        method="POST",
        outcome="infrastructure_failed",
        status_code=500,
    ) == (False, "write_side_effect_unknown")


def test_runtime_policy_blocks_uncompensated_or_unowned_writes():
    assert exploration_execution_policy(
        operation={"method": "PATCH", "path": "/api/items/{id}"},
        experiment={},
        requested_max_attempts=2,
    ) == (False, 0, "write_without_cleanup_proof")

    assert exploration_execution_policy(
        operation={"method": "PATCH", "path": "/api/items/{id}"},
        experiment={"cleanup_plan": [{"action": "restore"}]},
        requested_max_attempts=2,
    ) == (False, 0, "write_reversibility_proof_missing")

    assert exploration_execution_policy(
        operation={"method": "POST", "path": "/api/orders/{id}/submit"},
        experiment={
            "cleanup_plan": [{"action": "restore"}],
            "write_reversibility_proof": {
                "proof_status": "PROVEN",
                "proof_kind": "field_snapshot_restore",
                "cleanup_authority": {
                    "kind": "field_snapshot_restore",
                },
            },
        },
        requested_max_attempts=2,
    ) == (False, 0, "state_transition_owner_unproven")


def test_runtime_policy_rejects_accepted_residue_as_reversibility():
    assert exploration_execution_policy(
        operation={"method": "POST", "path": "/api/items"},
        experiment={
            "cleanup_plan": [
                {
                    "action": "accepted_residue",
                    "mode": "accepted_residue_no_cleanup",
                    "residue": True,
                }
            ],
            "write_reversibility_proof": {
                "proof_status": "PROVEN",
                "proof_kind": "accepted_residue",
                "reversibility": "none",
                "cleanup_authority": {
                    "kind": "accepted_residue",
                },
            },
        },
        requested_max_attempts=2,
    ) == (False, 0, "accepted_residue_is_not_reversible")


def test_single_candidate_accepted_residue_is_one_governed_attempt():
    """One candidate cannot be repeated under several actors.

    With a single ranked candidate the exploration degenerates to exactly
    one governed attempt — the same risk profile the scan's execution
    policy already authorizes for the compiled experiment under accepted
    residue. The gate exists to stop multi-actor repetition.
    """
    residue_experiment = {
        "cleanup_plan": [
            {
                "action": "accepted_residue",
                "mode": "accepted_residue_no_cleanup",
                "residue": True,
            }
        ],
        "write_reversibility_proof": {
            "proof_status": "PROVEN",
            "proof_kind": "accepted_residue",
            "reversibility": "none",
            "cleanup_authority": {"kind": "accepted_residue"},
        },
    }
    assert exploration_execution_policy(
        operation={"method": "POST", "path": "/api/items"},
        experiment=residue_experiment,
        requested_max_attempts=1,
    ) == (True, 1, "single_candidate_accepted_residue_write")


def test_single_candidate_without_cleanup_still_blocked():
    # The single-candidate exception only covers the accepted-residue form;
    # a write with NO cleanup plan at all keeps the strict gate.
    assert exploration_execution_policy(
        operation={"method": "POST", "path": "/api/items"},
        experiment={},
        requested_max_attempts=1,
    ) == (False, 0, "write_without_cleanup_proof")


def test_executor_really_switches_step_actor(monkeypatch):
    calls = []

    def governed(exp, **kwargs):
        actor = exp["treatment_plan"][0]["actor_ref"]
        calls.append(actor)
        status = 403 if actor == "actor-a" else 200
        return {
            "status": "EXECUTED",
            "finding": None,
            "execution_receipt": {"status": "EXECUTED"},
            "steps": [
                {
                    "phase": "treatment",
                    "operation_ref": "op",
                    "actor_ref": actor,
                    "status_code": status,
                }
            ],
        }

    monkeypatch.setattr(_executor_mechanics, "_execute_one_governed", governed)
    monkeypatch.setattr(
        _executor_mechanics,
        "enforce_oracle_validity_gates",
        lambda **kwargs: {**kwargs["result"], "validity_gate_ran": True},
    )

    experiment, behavior_ir = _experiment()
    result = experiment_executor.execute_one_experiment(
        experiment,
        behavior_ir=behavior_ir,
        root=Path("."),
        project="test",
        base_url="http://example.test",
        runtime_contract={},
        campaign_id="campaign",
        execution_id="execution",
    )

    assert calls == ["actor-a", "actor-b"]
    assert (
        result["actor_exploration_receipts"][1]["effective_step_actor_id"]
        == "actor-b"
    )
    assert result["validity_gate_ran"] is True


def test_executor_stops_write_after_5xx(monkeypatch):
    calls = []

    def governed(exp, **kwargs):
        actor = exp["treatment_plan"][0]["actor_ref"]
        calls.append(actor)
        return {
            "status": "EXECUTED",
            "finding": None,
            "execution_receipt": {"status": "EXECUTED"},
            "steps": [
                {
                    "phase": "treatment",
                    "operation_ref": "op",
                    "actor_ref": actor,
                    "status_code": 500,
                }
            ],
        }

    monkeypatch.setattr(_executor_mechanics, "_execute_one_governed", governed)
    monkeypatch.setattr(
        _executor_mechanics,
        "enforce_oracle_validity_gates",
        lambda **kwargs: kwargs["result"],
    )

    experiment, behavior_ir = _experiment(
        method="PATCH",
        path="/api/items/{id}",
        cleanup=True,
    )
    result = experiment_executor.execute_one_experiment(
        experiment,
        behavior_ir=behavior_ir,
        root=Path("."),
        project="test",
        base_url="http://example.test",
        runtime_contract={},
        campaign_id="campaign",
        execution_id="execution",
    )

    assert calls == ["actor-a"]
    assert "write_side_effect_unknown" in result["detail"]
    assert result["actor_exploration_receipts"][0]["continued"] is False


def test_state_transition_without_owner_blocks_before_transport(monkeypatch):
    calls = []
    monkeypatch.setattr(
        _executor_mechanics,
        "_execute_one_governed",
        lambda *args, **kwargs: calls.append(True),
    )
    monkeypatch.setattr(
        _executor_mechanics,
        "enforce_oracle_validity_gates",
        lambda **kwargs: kwargs["result"],
    )

    experiment, behavior_ir = _experiment(
        method="POST",
        path="/api/orders/{id}/submit",
        cleanup=True,
        owner=False,
    )
    result = experiment_executor.execute_one_experiment(
        experiment,
        behavior_ir=behavior_ir,
        root=Path("."),
        project="test",
        base_url="http://example.test",
        runtime_contract={},
        campaign_id="campaign",
        execution_id="execution",
    )

    assert calls == []
    assert result["status"] == "BLOCKED"
    assert "state_transition_owner_unproven" in result["detail"]
