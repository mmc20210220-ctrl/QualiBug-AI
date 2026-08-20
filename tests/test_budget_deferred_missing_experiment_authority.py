"""Persisted deferred identity remains authoritative when its experiment is absent."""
from __future__ import annotations


def test_budget_deferred_pool_is_not_dropped_when_experiment_map_is_missing() -> None:
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    oid = "selected-budget-deferred-missing-experiment"
    campaign_id = "campaign-budget-deferred-missing-experiment"
    executor.clear_continuation_retry_receipts(campaign_id)

    _, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [{"obligation_id": oid, "risk_family": "validation"}],
            "pending_next_round": [],
            "pending_count": 0,
            "budget_deferred_pool": [{"obligation_id": oid}],
            "budget_deferred_pool_count": 1,
        },
        obligations=[{
            "obligation_id": oid,
            "risk_family": "validation",
            "confidence": 0.8,
            "required_operations": [],
            "required_actors": [],
            "required_observers": ["http_response"],
            "property": {"template": "input_boundary_validation"},
        }],
        experiments_by_obligation={},
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id=campaign_id,
        automatic_round_limit=1,
        execute_batch=lambda rows, **kwargs: (_ for _ in ()).throw(
            AssertionError("round-limit-one missing experiment must not execute")
        ),
    )

    assert final_plan["early_stop_reason"] == (
        "PENDING_NEXT_ROUND_SKIPPED_ROUND_LIMIT_ONE"
    )
    assert final_plan["budget_deferred_pool_count"] == 1
    assert final_plan["budget_deferred_pool"] == [{"obligation_id": oid}]
    executor.clear_continuation_retry_receipts(campaign_id)
