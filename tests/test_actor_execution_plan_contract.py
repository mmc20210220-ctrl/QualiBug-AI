"""Actor exploration must survive the real Experiment Contract boundary."""
from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import experiment_executor
from ai_test_asset_center.experiment_compiler_obligation import (
    ACTOR_EXECUTION_PLAN_SCHEMA,
    _persist_actor_selection_contract,
    make_experiment,
)


def _compiled_shape() -> dict:
    """Build the same shape emitted by the semantic compiler's final boundary."""

    return make_experiment(
        obligation_id="obl-actor-contract",
        risk_family="validation",
        control_plan=[],
        treatment_plan=[{
            "step_id": "treatment_1",
            "phase": "treatment",
            "operation_ref": "op-read",
            "actor_ref": "actor-a",
        }],
        assertions=[{
            "assertion_id": "assert_validation",
            "kind": "http_status",
            "property": {
                "operation_ref": "op-read",
                "actor_ref": "actor-a",
                "_actor_exploration_plan": {
                    "mode": "permission_exploration",
                    "candidate_ids": ["actor-a", "actor-b"],
                    "authorization_oracle_enabled": False,
                    "max_attempts": 2,
                    "reason": "permits_edge_missing_runtime_exploration",
                },
            },
        }],
        observers=[{
            "observer_id": "http_response",
            "adapter": "http_api",
        }],
        cleanup_plan=[],
        safety_contract={
            "environment_type": "test",
            "governed_write": False,
        },
        compile_receipt={"status": "COMPILED"},
    )


def _behavior_ir() -> dict:
    return {
        "actors": [
            {
                "id": "actor-a",
                "role": "member-a",
                "credential_secret_ref": "secret_ref:test_accounts:member-a",
            },
            {
                "id": "actor-b",
                "role": "member-b",
                "credential_secret_ref": "secret_ref:test_accounts:member-b",
            },
        ],
        "operations": [{
            "id": "op-read",
            "method": "GET",
            "path": "/api/documents",
            "read_write": "read",
        }],
    }


def test_make_experiment_promotes_one_first_class_actor_plan() -> None:
    experiment = _compiled_shape()

    plan = experiment["actor_execution_plan"]
    assert plan["schema_version"] == ACTOR_EXECUTION_PLAN_SCHEMA
    assert plan["source_actor_id"] == "actor-a"
    assert plan["candidate_ids"] == ["actor-a", "actor-b"]
    assert plan["max_attempts"] == 2
    assert len(plan["plan_hash"]) == 64

    # The execution contract has one authority. Assertion property remains
    # semantic and no test-only top-level property/required_actors is needed.
    assert "_actor_exploration_plan" not in experiment["assertions"][0]["property"]
    assert "property" not in experiment
    assert "required_actors" not in experiment
    receipt = experiment["compile_receipt"]
    assert receipt["actor_execution_plan_hash"] == plan["plan_hash"]


def test_actor_selection_contract_allows_only_sealed_candidate_iteration() -> None:
    experiment = _compiled_shape()
    experiment["actor_selection_contract"] = {
        "selection_mode": "source_permitted",
        "substitution_allowed": False,
    }

    governed = _persist_actor_selection_contract(experiment)
    selection = governed["actor_selection_contract"]
    assert selection["selection_mode"] == "permission_exploration"
    assert selection["candidate_iteration_allowed"] is True
    assert selection["candidate_actor_refs"] == ["actor-a", "actor-b"]
    assert selection["substitution_allowed"] is False
    assert selection["actor_execution_plan_hash"] == (
        governed["actor_execution_plan"]["plan_hash"]
    )


def test_executor_switches_actor_without_noncanonical_top_level_property(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def governed(experiment: dict, **_kwargs: object) -> dict:
        actor = experiment["treatment_plan"][0]["actor_ref"]
        calls.append(actor)
        status = 403 if actor == "actor-a" else 200
        return {
            "status": "EXECUTED",
            "finding": None,
            "execution_receipt": {"status": "EXECUTED"},
            "steps": [{
                "step_id": "treatment_1",
                "phase": "treatment",
                "operation_ref": "op-read",
                "actor_ref": actor,
                "status_code": status,
            }],
        }

    monkeypatch.setattr(experiment_executor, "_execute_one_governed", governed)
    monkeypatch.setattr(
        experiment_executor,
        "enforce_oracle_validity_gates",
        lambda **kwargs: kwargs["result"],
    )

    result = experiment_executor.execute_one_experiment(
        _compiled_shape(),
        behavior_ir=_behavior_ir(),
        root=Path("."),
        project="test",
        base_url="http://example.test",
        runtime_contract={},
        campaign_id="campaign",
        execution_id="execution",
    )

    assert calls == ["actor-a", "actor-b"]
    assert result["actor_exploration_summary"]["status"] == "ACTOR_DISCOVERED"
    assert result["actor_exploration_summary"]["selected_actor_id"] == "actor-b"
    assert result["actor_exploration_receipts"][1]["effective_step_actor_id"] == "actor-b"
    assert (
        result["actor_exploration_receipts"][1]["actor_overlay"][
            "source_actor_basis"
        ]
        == "actor_execution_plan"
    )


def test_executor_blocks_tampered_actor_plan_before_transport(monkeypatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(
        experiment_executor,
        "_execute_one_governed",
        lambda *_args, **_kwargs: calls.append(True),
    )
    monkeypatch.setattr(
        experiment_executor,
        "enforce_oracle_validity_gates",
        lambda **kwargs: kwargs["result"],
    )
    experiment = _compiled_shape()
    experiment["actor_execution_plan"]["candidate_ids"].append("actor-c")

    result = experiment_executor.execute_one_experiment(
        experiment,
        behavior_ir=_behavior_ir(),
        root=Path("."),
        project="test",
        base_url="http://example.test",
        runtime_contract={},
        campaign_id="campaign",
        execution_id="execution",
    )

    assert calls == []
    assert result["status"] == "BLOCKED"
    assert result["detail"] == "actor_execution_plan_hash_mismatch"
