"""A resumed continuation attempt must not inherit the prior stop receipt."""
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


def test_successful_resume_replaces_prior_round_limit_stop(monkeypatch) -> None:
    import ai_test_asset_center.adaptive_discovery_planner as planner
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    oid = "resume-fresh"
    campaign_id = "campaign-resume-clears-round-limit"
    executor.clear_continuation_retry_receipts(campaign_id)

    def fake_plan(obligations, **kwargs):
        ids = [row["obligation_id"] for row in obligations]
        return {
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "budget": len(ids),
            "selected": [
                {
                    "obligation_id": value,
                    "risk_family": "validation",
                    "experiment_id": f"exp_{value}",
                }
                for value in ids
            ],
            "selected_count": len(ids),
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

    _, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [],
            "pending_next_round": [{"obligation_id": oid}],
            "pending_count": 1,
            "pending_truncated": 0,
            "fresh_pending_pool": [{"obligation_id": oid}],
            "fresh_pending_pool_count": 1,
            "early_stop_reason": "PENDING_NEXT_ROUND_SKIPPED_ROUND_LIMIT_ONE",
            "stop_condition": "PENDING_NEXT_ROUND_SKIPPED_ROUND_LIMIT_ONE",
            "round_limit_reached": True,
            "follow_on_round_limit": 1,
            "follow_on_round_receipts": [{"planning_round": 99}],
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
        execute_batch=lambda rows, **kwargs: {
            "results": [
                {"obligation_id": row["obligation_id"], "status": "EXECUTED"}
                for row in rows
            ],
            "executed_count": len(rows),
            "budget_deferred": [],
            "runtime_bindings": {},
        },
    )

    assert final_plan["stop_condition"] == "PENDING_QUEUE_EMPTY"
    assert final_plan["early_stop_reason"] == "PENDING_QUEUE_EMPTY"
    assert final_plan.get("round_limit_reached") is not True
    assert "follow_on_round_limit" not in final_plan
    assert final_plan["continuation_outstanding_count"] == 0
    assert final_plan["follow_on_round_receipts"] == [
        {
            "planning_round": 2,
            "selected_count": 1,
            "pending_count": 0,
            "fresh_pending_count": 0,
            "retry_pending_count": 0,
            "budget_deferred_pending_count": 0,
            "unreceipted_selected_count": 0,
            "executed_count": 1,
            "budget": 1,
            "round_mode": "fresh",
            "accumulated_bindings_count": 0,
            "continuation_authority": "exact_fresh_retry_deferred_pools",
        }
    ]
    executor.clear_continuation_retry_receipts(campaign_id)
