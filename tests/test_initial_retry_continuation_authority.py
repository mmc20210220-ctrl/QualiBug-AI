"""Regressions for initial execution failure -> continuation retry authority."""
from __future__ import annotations


def _obl(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "risk_family": "validation",
        "confidence": 0.8,
        "required_operations": [],
        "required_actors": [],
        "required_observers": ["http_response"],
        "property": {"template": "input_boundary_validation"},
    }


def _exp(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "experiment_id": f"exp_{oid}",
        "compile_receipt": {"status": "COMPILED"},
    }


def _seed_initial_retry(monkeypatch, *, campaign_id: str, oid: str, reason: str) -> None:
    import ai_test_asset_center.experiment_executor as executor

    executor.clear_continuation_retry_receipts(campaign_id)

    def fake_core_execute(*args, **kwargs):
        return {
            "results": [{
                "obligation_id": oid,
                "status": "BLOCKED",
                "reason_code": reason,
            }],
            "executed_count": 0,
            "budget_deferred": [],
            "runtime_bindings": {},
        }

    monkeypatch.setattr(executor._core, "execute_selected_experiments", fake_core_execute)
    executor.execute_selected_experiments([], campaign_id=campaign_id)


def test_initial_selected_retry_receipt_reenters_even_without_pending(monkeypatch) -> None:
    import ai_test_asset_center.adaptive_discovery_planner as planner
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    campaign_id = "campaign-initial-retry"
    oid = "retry-me"
    _seed_initial_retry(
        monkeypatch,
        campaign_id=campaign_id,
        oid=oid,
        reason="BLOCKED_MISSING_BINDING",
    )
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
                "experiment_id": f"exp_{oid}",
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

    def execute_success(rows, **kwargs):
        return {
            "results": [{"obligation_id": oid, "status": "EXECUTED"}],
            "executed_count": 1,
            "budget_deferred": [],
            "runtime_bindings": {},
        }

    batches, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [{
                "obligation_id": oid,
                "experiment_id": f"exp_{oid}",
            }],
            "pending_next_round": [],
            "pending_count": 0,
            "pending_truncated": 0,
        },
        obligations=[_obl(oid)],
        experiments_by_obligation={oid: _exp(oid)},
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id=campaign_id,
        automatic_round_limit=2,
        execute_batch=execute_success,
    )

    assert seen == [[oid]]
    assert len(batches) == 1
    assert final_plan["pending_count"] == 0
    assert final_plan["blocked_retry_pool"] == []
    assert final_plan["early_stop_reason"] == "PENDING_QUEUE_EMPTY"
    executor.clear_continuation_retry_receipts(campaign_id)


def test_initial_retry_reason_survives_round_limit(monkeypatch) -> None:
    import ai_test_asset_center.adaptive_discovery_planner as planner
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    campaign_id = "campaign-retry-reason"
    oid = "retry-reason"
    reason = "BLOCKED_MISSING_OBSERVER"
    _seed_initial_retry(
        monkeypatch,
        campaign_id=campaign_id,
        oid=oid,
        reason=reason,
    )

    monkeypatch.setattr(
        planner,
        "plan_obligation_round",
        lambda obligations, **kwargs: {
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "budget": 1,
            "selected": [{
                "obligation_id": oid,
                "risk_family": "validation",
                "experiment_id": f"exp_{oid}",
            }],
            "selected_count": 1,
            "pending_next_round": [],
            "pending_count": 0,
            "stop_condition": "in_scope_obligations_scheduled",
        },
    )
    monkeypatch.setattr(
        planner,
        "build_agent_intent_plan",
        lambda plan, **kwargs: {"intents": [{"obligation_id": oid}]},
    )

    def execute_still_blocked(rows, **kwargs):
        return {
            "results": [{
                "obligation_id": oid,
                "status": "BLOCKED",
                "reason_code": reason,
            }],
            "executed_count": 0,
            "budget_deferred": [],
            "runtime_bindings": {},
        }

    _, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [{"obligation_id": oid, "experiment_id": f"exp_{oid}"}],
            "pending_next_round": [],
            "pending_count": 0,
            "pending_truncated": 0,
        },
        obligations=[_obl(oid)],
        experiments_by_obligation={oid: _exp(oid)},
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id=campaign_id,
        automatic_round_limit=2,
        execute_batch=execute_still_blocked,
    )

    assert final_plan["pending_count"] == 1
    assert final_plan["blocked_retry_pool"] == [
        {"obligation_id": oid, "block_reason": reason}
    ]
    assert final_plan["stop_condition"] == "round_limit_reached"
    assert final_plan["round_limit_reached"] is True
    executor.clear_continuation_retry_receipts(campaign_id)


def test_capture_closes_before_follow_on_batches(monkeypatch) -> None:
    import ai_test_asset_center.experiment_executor as executor

    campaign_id = "campaign-capture-close"
    executor.clear_continuation_retry_receipts(campaign_id)
    calls = 0

    def fake_core_execute(*args, **kwargs):
        nonlocal calls
        calls += 1
        oid = f"r{calls}"
        return {
            "results": [{
                "obligation_id": oid,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
            }]
        }

    monkeypatch.setattr(executor._core, "execute_selected_experiments", fake_core_execute)
    executor.execute_selected_experiments([], campaign_id=campaign_id)
    first = executor.consume_continuation_retry_receipts(
        campaign_id,
        allowed_obligation_ids={"r1"},
        close_capture=True,
    )
    executor.execute_selected_experiments([], campaign_id=campaign_id)
    second = executor.consume_continuation_retry_receipts(
        campaign_id,
        allowed_obligation_ids={"r2"},
        close_capture=True,
    )

    assert [row["obligation_id"] for row in first] == ["r1"]
    assert second == []
    executor.clear_continuation_retry_receipts(campaign_id)
