"""Budget-deferred work must survive public-preview and round-limit boundaries."""
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


def _resume_plan(ids: list[str], preview_ids: list[str], *, budget: int) -> dict:
    return {
        "schema_version": "qualibug.adaptive-obligation-plan.v1",
        "plan_authority": "obligation",
        "budget": budget,
        "selected": [
            {"obligation_id": oid, "risk_family": "validation"}
            for oid in ids
        ],
        "pending_next_round": [
            {"obligation_id": oid, "not_in_plan_reason": "BUDGET_DEFERRED"}
            for oid in preview_ids
        ],
        "pending_count": len(ids),
        "pending_truncated": max(0, len(ids) - len(preview_ids)),
        "budget_deferred_pool": [{"obligation_id": oid} for oid in ids],
        "budget_deferred_pool_count": len(ids),
    }


def test_budget_deferred_pool_preserves_selected_tail_beyond_public_cap() -> None:
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor
    from ai_test_asset_center.pipeline_slices import _ABS_MAX_SLICE_BUDGET

    count = _ABS_MAX_SLICE_BUDGET + 37
    ids = [f"budget-deferred-{index:04d}" for index in range(count)]
    preview_ids = ids[:_ABS_MAX_SLICE_BUDGET]
    campaign_id = "campaign-budget-deferred-pool-over-cap"
    executor.clear_continuation_retry_receipts(campaign_id)

    _, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan=_resume_plan(ids, preview_ids, budget=1),
        obligations=[_obl(oid) for oid in ids],
        experiments_by_obligation={oid: _exp(oid) for oid in ids},
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id=campaign_id,
        automatic_round_limit=1,
        execute_batch=lambda rows, **kwargs: (_ for _ in ()).throw(
            AssertionError("round-limit-one resume must not execute")
        ),
    )

    assert final_plan["early_stop_reason"] == (
        "PENDING_NEXT_ROUND_SKIPPED_ROUND_LIMIT_ONE"
    )
    assert final_plan["pending_count"] == count
    assert len(final_plan["pending_next_round"]) == _ABS_MAX_SLICE_BUDGET
    assert final_plan["budget_deferred_pool_count"] == count
    assert [
        row["obligation_id"] for row in final_plan["budget_deferred_pool"]
    ] == ids
    assert final_plan["blocked_retry_pool_count"] == 0
    executor.clear_continuation_retry_receipts(campaign_id)


def test_budget_deferred_pool_restores_tail_to_planner_then_clears(monkeypatch) -> None:
    import ai_test_asset_center.adaptive_discovery_planner as planner
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    ids = [f"budget-resume-{index}" for index in range(5)]
    campaign_id = "campaign-budget-deferred-pool-resume"
    executor.clear_continuation_retry_receipts(campaign_id)
    seen_inputs: list[list[str]] = []

    def fake_plan(obligations, **kwargs):
        round_ids = [row["obligation_id"] for row in obligations]
        seen_inputs.append(round_ids)
        return {
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "budget": len(round_ids),
            "selected": [
                {
                    "obligation_id": oid,
                    "risk_family": "validation",
                    "experiment_id": f"exp_{oid}",
                }
                for oid in round_ids
            ],
            "selected_count": len(round_ids),
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

    def execute_all(rows, **kwargs):
        return {
            "results": [
                {"obligation_id": row["obligation_id"], "status": "EXECUTED"}
                for row in rows
            ],
            "executed_count": len(rows),
            "budget_deferred": [],
            "runtime_bindings": {},
        }

    _, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan=_resume_plan(ids, ids[:1], budget=len(ids)),
        obligations=[_obl(oid) for oid in ids],
        experiments_by_obligation={oid: _exp(oid) for oid in ids},
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id=campaign_id,
        automatic_round_limit=2,
        execute_batch=execute_all,
    )

    assert seen_inputs == [ids]
    assert ids[-1] in seen_inputs[0]
    assert final_plan["pending_count"] == 0
    assert final_plan["budget_deferred_pool_count"] == 0
    assert final_plan["budget_deferred_pool"] == []
    assert final_plan["blocked_retry_pool_count"] == 0
    executor.clear_continuation_retry_receipts(campaign_id)
