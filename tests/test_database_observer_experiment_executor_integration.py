from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import experiment_executor_core as executor


def _experiment() -> dict:
    return {
        "experiment_id": "experiment:orders",
        "obligation_id": "obligation:orders",
        "control_plan": [],
        "treatment_plan": [{"step_id": "treatment:1"}],
        "safety_contract": {"governed_write": False},
        "binding_plan": [],
        "assertions": [],
    }


def _install_base(monkeypatch, order: list[str]) -> None:
    monkeypatch.setattr(
        executor,
        "preflight_experiment_executable",
        lambda *_args, **_kwargs: (True, "", ""),
    )
    monkeypatch.setattr(executor, "load_actor_tokens", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(executor, "contract_activation_requirements", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        executor,
        "materialize_experiment_fixtures",
        lambda **_kwargs: {
            "status": "continue",
            "steps_out": [],
            "fixture_receipts": [],
            "binding_materialization_receipts": [],
            "runtime_bindings": {"id": "o-1"},
            "pending_fixture_cleanups": [{"fixture_id": "fixture:1"}],
            "cleanup_failures": 0,
            "contract_evidence_receipts": [],
        },
    )
    monkeypatch.setattr(
        executor,
        "execute_barrier_plans",
        lambda **_kwargs: order.append("barrier") or {
            "steps": [{"phase": "treatment", "response": {"body": {"id": "o-1"}}}],
            "contract_evidence_receipts": [],
            "request_bodies_for_cleanup": {},
            "pre_transport_block_reasons": [],
            "consumed_barrier_steps": set(),
        },
    )
    monkeypatch.setattr(
        executor,
        "execute_non_barrier_plans",
        lambda **_kwargs: order.append("plan") or {
            "steps": [{"phase": "treatment", "response": {"body": {"id": "o-1"}}}],
            "contract_evidence_receipts": [],
            "request_bodies_for_cleanup": {},
            "pre_transport_block_reasons": [],
            "cleanup_failures": 0,
        },
    )
    monkeypatch.setattr(
        executor,
        "execute_experiment_cleanup_compensation",
        lambda **kwargs: order.append("cleanup") or {
            "steps_out": kwargs["steps_out"],
            "observations": kwargs["observations"],
            "contract_evidence_receipts": kwargs["contract_evidence_receipts"],
            "cleanup_failures": 0,
        },
    )
    monkeypatch.setattr(
        executor,
        "finalize_experiment_execution",
        lambda **kwargs: order.append("finalize") or {
            "status": "BLOCKED" if kwargs["pre_transport_block_reasons"] else "COMPLETED",
            "reason_code": (
                kwargs["pre_transport_block_reasons"][0]
                if kwargs["pre_transport_block_reasons"]
                else ""
            ),
            "observations": kwargs["observations"],
        },
    )


def test_before_and_after_wrap_all_transport_before_cleanup(monkeypatch, tmp_path: Path) -> None:
    order: list[str] = []
    _install_base(monkeypatch, order)

    def phase(_exp, *, phase, observations, **_kwargs):
        order.append(phase.lower())
        observations.setdefault("database_phases", []).append(phase)
        return {
            "phase": phase,
            "status": "OBSERVED",
            "blocked": False,
            "reason_code": "",
        }

    monkeypatch.setattr(executor, "execute_database_observer_phase", phase)

    result = executor.execute_one_experiment(
        _experiment(),
        behavior_ir={"actors": [], "operations": []},
        root=tmp_path,
        project="project",
        base_url="http://127.0.0.1:1",
        runtime_contract={},
        campaign_id="campaign",
        execution_id="execution",
        actor_tokens={},
    )

    assert result["status"] == "COMPLETED"
    assert order == ["before", "barrier", "plan", "after", "cleanup", "finalize"]
    assert result["observations"]["database_phases"] == ["BEFORE", "AFTER"]


def test_failed_before_blocks_all_transport_but_still_runs_cleanup(monkeypatch, tmp_path: Path) -> None:
    order: list[str] = []
    _install_base(monkeypatch, order)

    def phase(_exp, *, phase, observations, **_kwargs):
        order.append(phase.lower())
        observations.setdefault("database_phases", []).append(phase)
        return {
            "phase": phase,
            "status": "INDETERMINATE",
            "blocked": phase == "BEFORE",
            "reason_code": "DATABASE_OBSERVER_BEFORE_PHASE_INCOMPLETE",
        }

    monkeypatch.setattr(executor, "execute_database_observer_phase", phase)

    result = executor.execute_one_experiment(
        _experiment(),
        behavior_ir={"actors": [], "operations": []},
        root=tmp_path,
        project="project",
        base_url="http://127.0.0.1:1",
        runtime_contract={},
        campaign_id="campaign",
        execution_id="execution",
        actor_tokens={},
    )

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "DATABASE_OBSERVER_BEFORE_PHASE_INCOMPLETE"
    assert "barrier" not in order
    assert "plan" not in order
    assert "after" not in order
    assert order == ["before", "cleanup", "finalize"]
    assert result["observations"]["database_observer_transport_blocked"] is True


def test_failed_after_is_recorded_before_cleanup_and_never_retried(monkeypatch, tmp_path: Path) -> None:
    order: list[str] = []
    _install_base(monkeypatch, order)

    def phase(_exp, *, phase, observations, **_kwargs):
        order.append(phase.lower())
        observations.setdefault("database_phase_calls", []).append(phase)
        if phase == "AFTER":
            return {
                "phase": phase,
                "status": "INDETERMINATE",
                "blocked": False,
                "reason_code": "DATABASE_OBSERVER_AFTER_PHASE_INCOMPLETE",
            }
        return {"phase": phase, "status": "OBSERVED", "blocked": False, "reason_code": ""}

    monkeypatch.setattr(executor, "execute_database_observer_phase", phase)

    result = executor.execute_one_experiment(
        _experiment(),
        behavior_ir={"actors": [], "operations": []},
        root=tmp_path,
        project="project",
        base_url="http://127.0.0.1:1",
        runtime_contract={},
        campaign_id="campaign",
        execution_id="execution",
        actor_tokens={},
    )

    assert result["status"] == "BLOCKED"
    assert order == ["before", "barrier", "plan", "after", "cleanup", "finalize"]
    assert result["observations"]["database_phase_calls"] == ["BEFORE", "AFTER"]
    assert result["observations"]["database_observer_after_phase_incomplete"] is True
