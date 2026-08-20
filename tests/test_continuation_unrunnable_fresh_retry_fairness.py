"""Unavailable fresh authority must not block planner-runnable retry work."""
from __future__ import annotations


def _obl(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "risk_family": "validation",
        "confidence": 0.8,
        "compile_status": "COMPILED",
        "required_operations": [],
        "required_actors": [],
        "required_observers": ["http_response"],
        "property": {"template": "input_boundary_validation"},
    }


def test_missing_fresh_experiment_does_not_starve_compiled_retry(monkeypatch) -> None:
    import ai_test_asset_center.adaptive_discovery_planner as planner
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    missing_fresh = "fresh-missing-experiment"
    retry = "retry-ready"
    campaign_id = "campaign-fresh-missing-retry-ready"
    executor.clear_continuation_retry_receipts(campaign_id)
    planner_inputs: list[list[str]] = []

    def fake_plan(obligations, **kwargs):
        ids = [row["obligation_id"] for row in obligations]
        planner_inputs.append(ids)
        assert ids == [retry]
        return {
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "budget": 1,
            "selected": [{
                "obligation_id": retry,
                "experiment_id": f"exp_{retry}",
                "risk_family": "validation",
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
        lambda plan, **kwargs: {
            "intents": [
                {"obligation_id": row["obligation_id"]}
                for row in plan["selected"]
            ]
        },
    )

    executed: list[str] = []

    def execute_batch(rows, **kwargs):
        ids = [row["obligation_id"] for row in rows]
        executed.extend(ids)
        return {
            "results": [
                {"obligation_id": oid, "status": "EXECUTED"}
                for oid in ids
            ],
            "executed_count": len(ids),
            "budget_deferred": [],
            "runtime_bindings": {},
        }

    _, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [],
            "pending_next_round": [
                {"obligation_id": missing_fresh},
                {
                    "obligation_id": retry,
                    "not_in_plan_reason": "CONTINUATION_RETRY_PENDING",
                },
            ],
            "pending_count": 2,
            "pending_truncated": 0,
            "fresh_pending_pool": [{"obligation_id": missing_fresh}],
            "fresh_pending_pool_count": 1,
            "blocked_retry_pool": [{
                "obligation_id": retry,
                "block_reason": "BLOCKED_MISSING_BINDING",
            }],
            "blocked_retry_pool_count": 1,
            "budget_deferred_pool": [],
            "budget_deferred_pool_count": 0,
        },
        obligations=[_obl(missing_fresh), _obl(retry)],
        experiments_by_obligation={
            # Retry reason is execution-level; its experiment remains COMPILED,
            # which is what the real adaptive planner can actually schedule.
            retry: {
                "obligation_id": retry,
                "experiment_id": f"exp_{retry}",
                "compile_receipt": {"status": "COMPILED"},
            }
        },
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id=campaign_id,
        automatic_round_limit=2,
        execute_batch=execute_batch,
    )

    assert planner_inputs == [[retry]]
    assert executed == [retry]
    assert final_plan["blocked_retry_pool_count"] == 0
    assert final_plan["fresh_pending_pool"] == [
        {"obligation_id": missing_fresh}
    ]
    assert final_plan["fresh_pending_pool_count"] == 1
    assert final_plan["continuation_outstanding_count"] == 1
    assert final_plan["stop_condition"] == "NO_CONTINUATION_EXPERIMENTS"
    assert final_plan["early_stop_reason"] == "NO_CONTINUATION_EXPERIMENTS"
    assert final_plan["held_unrunnable_fresh_count"] == 1
    executor.clear_continuation_retry_receipts(campaign_id)


def test_blocked_retry_experiment_does_not_trigger_fresh_bypass() -> None:
    from ai_test_asset_center.continuation_runnable_fresh_authority import (
        hold_unrunnable_fresh,
    )

    fresh = "fresh-missing"
    retry = "retry-compile-blocked"
    plan = {
        "fresh_pending_pool": [{"obligation_id": fresh}],
        "fresh_pending_pool_count": 1,
        "blocked_retry_pool": [{
            "obligation_id": retry,
            "block_reason": "BLOCKED_MISSING_BINDING",
        }],
        "blocked_retry_pool_count": 1,
        "budget_deferred_pool": [],
        "budget_deferred_pool_count": 0,
    }

    active, held = hold_unrunnable_fresh(
        plan,
        {
            retry: {
                "obligation_id": retry,
                "experiment_id": f"exp_{retry}",
                "compile_receipt": {"status": "BLOCKED_MISSING_BINDING"},
            }
        },
    )

    assert held == []
    assert active["fresh_pending_pool"] == [{"obligation_id": fresh}]
    assert "held_unrunnable_fresh_count" not in active
