"""Regressions for scheduled-vs-terminal initial execution exclusion."""
from __future__ import annotations


def _obl(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "risk_family": "validation",
        "confidence": 0.7,
        "required_operations": [],
        "required_actors": [],
        "required_observers": ["http_response"],
        "property": {"template": "input_boundary_validation"},
    }


def _exp(oid: str, experiment_id: str | None = None) -> dict:
    return {
        "obligation_id": oid,
        "experiment_id": experiment_id or f"exp_{oid}",
        "compile_receipt": {"status": "COMPILED"},
    }


def _seed_initial_outcome(
    monkeypatch,
    *,
    campaign_id: str,
    oid: str,
    experiment_id: str,
    result: dict | None,
    deferred: bool = False,
) -> None:
    import ai_test_asset_center.experiment_executor as executor

    executor.clear_continuation_retry_receipts(campaign_id)

    def fake_core_execute(*args, **kwargs):
        return {
            "results": [dict(result)] if isinstance(result, dict) else [],
            "executed_count": 1 if isinstance(result, dict) and result.get("status") == "EXECUTED" else 0,
            "budget_deferred": (
                [{"obligation_id": oid, "experiment_id": experiment_id}]
                if deferred
                else []
            ),
            "runtime_bindings": {},
        }

    monkeypatch.setattr(executor._core, "execute_selected_experiments", fake_core_execute)
    executor.execute_selected_experiments(
        [{"obligation_id": oid, "experiment_id": experiment_id}],
        campaign_id=campaign_id,
    )


def _install_single_id_planner(monkeypatch, oid: str, experiment_id: str) -> list[list[str]]:
    import ai_test_asset_center.adaptive_discovery_planner as planner

    seen: list[list[str]] = []

    def fake_plan(obligations, **kwargs):
        ids = [row["obligation_id"] for row in obligations]
        seen.append(ids)
        return {
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "budget": 1,
            "selected": [{
                "obligation_id": oid,
                "risk_family": "validation",
                "experiment_id": experiment_id,
            }],
            "selected_count": 1,
            "pending_next_round": [],
            "pending_count": 0,
            "stop_condition": "in_scope_obligations_scheduled",
        }

    monkeypatch.setattr(planner, "plan_obligation_round", fake_plan)
    monkeypatch.setattr(
        planner,
        "build_agent_intent_plan",
        lambda plan, **kwargs: {"intents": [{"obligation_id": oid}]},
    )
    return seen


def test_scheduled_without_receipt_is_not_allowed_to_exclude_continuation(monkeypatch) -> None:
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    campaign_id = "campaign-unreceipted-exclusion"
    oid = "unreceipted"
    experiment_id = "exp_unreceipted"
    _seed_initial_outcome(
        monkeypatch,
        campaign_id=campaign_id,
        oid=oid,
        experiment_id=experiment_id,
        result=None,
    )
    seen = _install_single_id_planner(monkeypatch, oid, experiment_id)

    def execute_success(rows, **kwargs):
        return {
            "results": [{
                "obligation_id": oid,
                "experiment_id": experiment_id,
                "status": "EXECUTED",
            }],
            "executed_count": 1,
            "budget_deferred": [],
            "runtime_bindings": {},
        }

    batches, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [{"obligation_id": oid, "experiment_id": experiment_id}],
            "pending_next_round": [],
            "pending_count": 0,
            "pending_truncated": 0,
        },
        obligations=[_obl(oid)],
        experiments_by_obligation={oid: _exp(oid, experiment_id)},
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id=campaign_id,
        automatic_round_limit=2,
        execute_batch=execute_success,
        exclude_obligation_ids={oid},
    )

    receipt = final_plan["pending_continuation_authority_receipt"]
    assert seen == [[oid]]
    assert len(batches) == 1
    assert receipt["requested_excluded_count"] == 1
    assert receipt["terminal_receipt_excluded_count"] == 0
    assert receipt["unproven_exclusion_rejected_count"] == 1
    assert receipt["initial_unreceipted_or_deferred_requeued_count"] == 1
    executor.clear_continuation_retry_receipts(campaign_id)


def test_real_terminal_receipt_may_exclude_initial_identity(monkeypatch) -> None:
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    campaign_id = "campaign-terminal-exclusion"
    oid = "terminal"
    experiment_id = "exp_terminal"
    _seed_initial_outcome(
        monkeypatch,
        campaign_id=campaign_id,
        oid=oid,
        experiment_id=experiment_id,
        result={
            "obligation_id": oid,
            "experiment_id": experiment_id,
            "status": "EXECUTED",
        },
    )

    batches, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [{"obligation_id": oid, "experiment_id": experiment_id}],
            "pending_next_round": [],
            "pending_count": 0,
            "pending_truncated": 0,
        },
        obligations=[_obl(oid)],
        experiments_by_obligation={oid: _exp(oid, experiment_id)},
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id=campaign_id,
        automatic_round_limit=3,
        execute_batch=lambda rows, **kwargs: (_ for _ in ()).throw(
            AssertionError("terminal identity must not be scheduled again")
        ),
        exclude_obligation_ids={oid},
    )

    receipt = final_plan["pending_continuation_authority_receipt"]
    assert batches == []
    assert receipt["requested_excluded_count"] == 1
    assert receipt["terminal_receipt_excluded_count"] == 1
    assert receipt["unproven_exclusion_rejected_count"] == 0
    executor.clear_continuation_retry_receipts(campaign_id)


def test_budget_deferred_initial_identity_is_requeued_even_if_caller_excludes_it(monkeypatch) -> None:
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    campaign_id = "campaign-deferred-exclusion"
    oid = "deferred"
    experiment_id = "exp_deferred"
    _seed_initial_outcome(
        monkeypatch,
        campaign_id=campaign_id,
        oid=oid,
        experiment_id=experiment_id,
        result=None,
        deferred=True,
    )
    seen = _install_single_id_planner(monkeypatch, oid, experiment_id)

    def execute_success(rows, **kwargs):
        return {
            "results": [{
                "obligation_id": oid,
                "experiment_id": experiment_id,
                "status": "EXECUTED",
            }],
            "executed_count": 1,
            "budget_deferred": [],
            "runtime_bindings": {},
        }

    batches, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [{"obligation_id": oid, "experiment_id": experiment_id}],
            "pending_next_round": [],
            "pending_count": 0,
            "pending_truncated": 0,
        },
        obligations=[_obl(oid)],
        experiments_by_obligation={oid: _exp(oid, experiment_id)},
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id=campaign_id,
        automatic_round_limit=2,
        execute_batch=execute_success,
        exclude_obligation_ids={oid},
    )

    assert seen == [[oid]]
    assert len(batches) == 1
    assert final_plan["pending_continuation_authority_receipt"][
        "initial_unreceipted_or_deferred_requeued_count"
    ] == 1
    executor.clear_continuation_retry_receipts(campaign_id)
