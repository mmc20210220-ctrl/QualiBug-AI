"""Legacy RETRY_ELIGIBLE pools remain lossless under the exact engine."""
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
        "compile_receipt": {"status": "BLOCKED_MISSING_BINDING"},
    }


def test_legacy_retry_reason_is_restored_when_round_limit_prevents_retry() -> None:
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    oid = "legacy-retry-held"
    campaign_id = "campaign-legacy-retry-held"
    executor.clear_continuation_retry_receipts(campaign_id)
    called = False

    def execute_batch(rows, **kwargs):
        nonlocal called
        called = True
        return {}

    _, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "budget": 1,
            "pending_next_round": [],
            "pending_count": 0,
            # This is the exact shape emitted by the retired consumer.
            "blocked_retry_pool": [{
                "obligation_id": oid,
                "block_reason": "RETRY_ELIGIBLE",
            }],
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
        automatic_round_limit=1,
        execute_batch=execute_batch,
    )

    assert called is False
    assert final_plan["blocked_retry_pool_count"] == 1
    assert final_plan["blocked_retry_pool"] == [{
        "obligation_id": oid,
        "block_reason": "RETRY_ELIGIBLE",
    }]
    receipt = final_plan["legacy_retry_authority_receipt"]
    assert receipt["status"] == "RESTORED"
    assert receipt["restored_legacy_retry_count"] == 1
    assert final_plan["early_stop_reason"] == (
        "PENDING_NEXT_ROUND_SKIPPED_ROUND_LIMIT_ONE"
    )
    executor.clear_continuation_retry_receipts(campaign_id)


def test_legacy_retry_identity_is_executed_instead_of_silently_dropped(
    monkeypatch,
) -> None:
    import ai_test_asset_center.adaptive_discovery_planner as planner
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    oid = "legacy-retry-executed"
    campaign_id = "campaign-legacy-retry-executed"
    executor.clear_continuation_retry_receipts(campaign_id)

    def fake_plan(obligations, **kwargs):
        ids = [row["obligation_id"] for row in obligations]
        assert ids == [oid]
        return {
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "budget": 1,
            "selected": [{
                "obligation_id": oid,
                "experiment_id": f"exp_{oid}",
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
        lambda plan, **kwargs: {"intents": [{"obligation_id": oid}]},
    )
    executed: list[str] = []

    _, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "budget": 1,
            "pending_next_round": [],
            "pending_count": 0,
            "blocked_retry_pool": [{
                "obligation_id": oid,
                "block_reason": "RETRY_ELIGIBLE",
            }],
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
        execute_batch=lambda rows, **kwargs: (
            executed.extend(row["obligation_id"] for row in rows)
            or {
                "results": [
                    {"obligation_id": row["obligation_id"], "status": "EXECUTED"}
                    for row in rows
                ],
                "executed_count": len(rows),
                "budget_deferred": [],
                "runtime_bindings": {},
            }
        ),
    )

    assert executed == [oid]
    assert final_plan["blocked_retry_pool_count"] == 0
    assert final_plan["continuation_outstanding_count"] == 0
    assert final_plan["stop_condition"] == "PENDING_QUEUE_EMPTY"
    receipt = final_plan["legacy_retry_authority_receipt"]
    assert receipt["status"] == "CONSUMED_OR_RECLASSIFIED"
    assert receipt["restored_legacy_retry_count"] == 0
    executor.clear_continuation_retry_receipts(campaign_id)
