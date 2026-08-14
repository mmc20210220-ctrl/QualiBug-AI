"""Permission observations improve selection only inside their exact context."""
from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import experiment_executor
from ai_test_asset_center.actor_exploration import (
    ActorSelectionMode,
    PermissionObservation,
)
from ai_test_asset_center.actor_exploration_runtime import (
    build_executable_candidates,
    build_exploration_plan,
    clear_scan_observations,
    compute_observation_confidence,
    get_scan_observations,
    observation_context_compatible,
    observation_success_counts,
    permission_context_fingerprint,
    record_permission_observation,
)
from ai_test_asset_center.experiment_compiler_obligation import make_experiment


def _actor(actor_id: str, *, tenant: str = "tenant-a") -> dict:
    return {
        "id": actor_id,
        "role": f"role-{actor_id}",
        "account_ref": f"account-{actor_id}",
        "runtime_bound": True,
        "tenant_scope": tenant,
        "credential_secret_ref": f"secret_ref:test_accounts:{actor_id}",
    }


def _operation() -> dict:
    return {
        "id": "op-documents",
        "method": "GET",
        "path": "/api/documents",
        "read_write": "read",
        "resource_type": "document",
    }


def _context(*, owner: str = "", tenant: str = "tenant-a") -> dict:
    return {
        "resource_type": "document",
        "resource_tenant_id": tenant,
        "resource_owner_actor_id": owner,
        "ownership_required": "true" if owner else "false",
    }


def _observation(
    *,
    actor_id: str = "actor-b",
    campaign_id: str = "campaign-a",
    project_id: str = "project-a",
    tenant_id: str = "tenant-a",
    context: dict | None = None,
    confidence: float = 0.85,
    resource_identity: str = "resource-1",
) -> PermissionObservation:
    runtime_context = context if context is not None else _context(tenant=tenant_id)
    return PermissionObservation(
        actor_id=actor_id,
        role_ref=f"role-{actor_id}",
        operation_id="op-documents",
        evidence_ref=f"receipt-{campaign_id}-{actor_id}",
        outcome="OBSERVED_ALLOWED",
        campaign_id=campaign_id,
        project_id=project_id,
        environment_ref="http://target.test",
        context_fingerprint=permission_context_fingerprint(runtime_context),
        resource_identity_fingerprint=resource_identity,
        resource_type="document",
        tenant_id=tenant_id,
        status_code=200,
        confidence=confidence,
    )


def test_observation_store_isolated_by_project_and_campaign() -> None:
    clear_scan_observations()
    record_permission_observation(_observation())

    assert len(get_scan_observations(
        project_id="project-a",
        campaign_id="campaign-a",
    )) == 1
    assert get_scan_observations(
        project_id="project-a",
        campaign_id="campaign-b",
    ) == []
    assert get_scan_observations(
        project_id="project-b",
        campaign_id="campaign-a",
    ) == []
    clear_scan_observations()


def test_contextless_or_cross_tenant_observation_cannot_bias_ranking() -> None:
    runtime_context = _context(tenant="tenant-a")
    contextless = _observation(context={}, tenant_id="")
    other_tenant = _observation(
        context=_context(tenant="tenant-b"),
        tenant_id="tenant-b",
    )

    assert observation_context_compatible(contextless, runtime_context) is False
    assert observation_context_compatible(other_tenant, runtime_context) is False

    candidates = build_executable_candidates(
        {
            "actor-a": _actor("actor-a"),
            "actor-b": _actor("actor-b"),
        },
        operation=_operation(),
        runtime_context=runtime_context,
        permission_observations=[contextless, other_tenant],
    )
    by_id = {candidate.actor_id: candidate for candidate in candidates}
    assert not any(
        reason.startswith("observed_operation_success")
        for reason in by_id["actor-b"].score_reasons
    )


def test_high_confidence_same_context_observation_becomes_observed_mode() -> None:
    runtime_context = _context()
    observation = _observation(context=runtime_context)
    plan = build_exploration_plan(
        operation=_operation(),
        obligation={},
        actors={
            "actor-a": _actor("actor-a"),
            "actor-b": _actor("actor-b"),
        },
        permitted_actor_ids=set(),
        runtime_context=runtime_context,
        permission_observations=[observation],
    )

    assert plan is not None
    assert plan.mode == ActorSelectionMode.OBSERVED_PERMISSION
    assert plan.candidates[0].actor_id == "actor-b"
    assert plan.authorization_oracle_enabled is False
    assert plan.reason == "context_compatible_observed_permission"


def test_compile_seals_candidate_pool_but_preserves_attempt_budget() -> None:
    actors = {
        f"actor-{index:02d}": _actor(f"actor-{index:02d}")
        for index in range(20)
    }
    plan = build_exploration_plan(
        operation=_operation(),
        obligation={},
        actors=actors,
        permitted_actor_ids=set(),
    )

    assert plan is not None
    assert plan.max_attempts == 3
    assert len(plan.candidates) == 16
    assert len(plan.candidates) > plan.max_attempts


def test_confidence_progresses_only_with_exact_context_and_distinct_identity() -> None:
    runtime_context = _context()
    context_fingerprint = permission_context_fingerprint(runtime_context)
    first = _observation(
        context=runtime_context,
        confidence=0.60,
        resource_identity="resource-1",
    )
    second = _observation(
        context=runtime_context,
        confidence=0.75,
        resource_identity="resource-2",
    )
    incompatible = _observation(
        context=_context(tenant="tenant-b"),
        tenant_id="tenant-b",
        resource_identity="resource-3",
    )

    same_context, different_instances = observation_success_counts(
        observations=[first, second, incompatible],
        actor_id="actor-b",
        operation_id="op-documents",
        context_fingerprint=context_fingerprint,
        resource_identity_fingerprint="resource-3",
    )
    assert same_context == 2
    assert different_instances == 2
    assert compute_observation_confidence(
        outcome="OBSERVED_ALLOWED",
        same_context_successes=same_context + 1,
        different_instance_successes=different_instances,
    ) == 0.85


def _compiled_experiment_with_tail_owner() -> dict:
    candidate_ids = ["actor-a", "actor-b", "actor-c", "actor-d"]
    return make_experiment(
        obligation_id="obl-tail-owner",
        risk_family="validation",
        control_plan=[],
        treatment_plan=[{
            "step_id": "treatment_1",
            "phase": "treatment",
            "operation_ref": "op-documents",
            "actor_ref": "actor-a",
        }],
        assertions=[{
            "assertion_id": "assert-read",
            "kind": "http_status",
            "property": {
                "operation_ref": "op-documents",
                "actor_ref": "actor-a",
                "owner_actor_ref": "actor-d",
                "_actor_exploration_plan": {
                    "mode": "permission_exploration",
                    "candidate_ids": candidate_ids,
                    "authorization_oracle_enabled": False,
                    "max_attempts": 3,
                    "reason": "permits_edge_missing_runtime_exploration",
                },
            },
        }],
        observers=[{"observer_id": "http_response", "adapter": "http_api"}],
        cleanup_plan=[],
        safety_contract={"environment_type": "test", "governed_write": False},
        compile_receipt={"status": "COMPILED"},
    )


def test_runtime_context_reranks_owner_outside_compile_attempt_prefix(
    monkeypatch,
) -> None:
    clear_scan_observations()
    calls: list[str] = []

    def governed(experiment: dict, **_kwargs: object) -> dict:
        actor_id = experiment["treatment_plan"][0]["actor_ref"]
        calls.append(actor_id)
        return {
            "status": "EXECUTED",
            "finding": None,
            "execution_receipt": {"status": "EXECUTED"},
            "steps": [{
                "step_id": "treatment_1",
                "phase": "treatment",
                "operation_ref": "op-documents",
                "actor_ref": actor_id,
                "status_code": 200,
            }],
        }

    monkeypatch.setattr(
        experiment_executor._core, "_execute_one_governed", governed
    )
    monkeypatch.setattr(
        experiment_executor._core,
        "enforce_oracle_validity_gates",
        lambda **kwargs: kwargs["result"],
    )
    behavior_ir = {
        "actors": [_actor(actor_id) for actor_id in (
            "actor-a", "actor-b", "actor-c", "actor-d"
        )],
        "operations": [_operation()],
    }

    result = experiment_executor.execute_one_experiment(
        _compiled_experiment_with_tail_owner(),
        behavior_ir=behavior_ir,
        root=Path("."),
        project="project-owner",
        base_url="http://target.test",
        runtime_contract={},
        campaign_id="campaign-owner",
        execution_id="execution-owner",
    )

    assert calls == ["actor-d"]
    assert result["actor_exploration_summary"]["selected_actor_id"] == "actor-d"
    ranking = result["actor_exploration_summary"]["runtime_candidate_ranking"]
    assert ranking[0]["actor_id"] == "actor-d"
    assert "resource_owner" in ranking[0]["score_reasons"]
    stored = get_scan_observations(
        project_id="project-owner",
        campaign_id="campaign-owner",
        actor_id="actor-d",
        operation_id="op-documents",
    )
    assert len(stored) == 1
    assert stored[0].confidence == 0.60
    clear_scan_observations()
