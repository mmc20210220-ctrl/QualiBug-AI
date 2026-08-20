"""A missing experiment must not block other fresh continuation work."""
from __future__ import annotations


def _obl(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "risk_family": "validation",
        "confidence": 0.8,
        # This historical marker is exactly why handing the row to the planner
        # without its current experiment is dangerous: planner fallback can
        # still rank it as COMPILED and emit an empty experiment_id.
        "compile_status": "COMPILED",
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


def test_missing_dependency_stays_pending_while_valid_fresh_tail_executes(monkeypatch) -> None:
    import ai_test_asset_center.adaptive_discovery_planner as planner
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    missing = "a-missing-experiment"
    valid = "b-valid-experiment"
    campaign_id = "campaign-mixed-missing-experiment"
    executor.clear_continuation_retry_receipts(campaign_id)
    planner_inputs: list[list[str]] = []

    def fake_plan(obligations, **kwargs):
        ids = [row["obligation_id"] for row in obligations]
        planner_inputs.append(ids)
        assert ids == [valid]
        return {
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "budget": 1,
            "selected": [{
                "obligation_id": valid,
                "risk_family": "validation",
                "experiment_id": f"exp_{valid}",
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

    _, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [],
            "pending_next_round": [
                {"obligation_id": missing},
                {"obligation_id": valid},
            ],
            "pending_count": 2,
            "pending_truncated": 0,
            "fresh_pending_pool": [
                {"obligation_id": missing},
                {"obligation_id": valid},
            ],
            "fresh_pending_pool_count": 2,
        },
        obligations=[_obl(missing), _obl(valid)],
        experiments_by_obligation={valid: _exp(valid)},
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

    assert planner_inputs == [[valid]]
    assert final_plan["continuation_outstanding_count"] == 1
    assert final_plan["fresh_pending_pool"] == [{"obligation_id": missing}]
    assert final_plan["pending_next_round"][0]["obligation_id"] == missing
    assert final_plan["stop_condition"] == "round_limit_reached"
    executor.clear_continuation_retry_receipts(campaign_id)
