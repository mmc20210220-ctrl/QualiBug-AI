"""Continuation scheduling correlates execution outcomes to selected identity."""
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


def test_initial_capture_matches_variant_result_to_selected_obligation() -> None:
    import ai_test_asset_center.experiment_executor as executor
    from ai_test_asset_center.continuation_selected_identity_authority import (
        install_initial_capture_selected_identity_bridge,
    )

    campaign_id = "campaign-selected-identity-initial"
    base = "obl-base"
    variant = f"{base}__v_runtime"
    experiment_id = f"exp_{base}"
    executor.clear_continuation_retry_receipts(campaign_id)
    install_initial_capture_selected_identity_bridge()

    batch = {
        "results": [{
            "selected_obligation_id": base,
            "obligation_id": variant,
            "executed_obligation_id": variant,
            "experiment_id": experiment_id,
            "status": "EXECUTED",
        }],
        "budget_deferred": [],
    }
    executor._capture_continuation_execution_receipts(
        campaign_id=campaign_id,
        selected_rows=[{
            "obligation_id": base,
            "experiment_id": experiment_id,
        }],
        batch=batch,
    )

    captured = executor.consume_continuation_execution_receipts(
        campaign_id,
        allowed_experiment_ids_by_obligation={base: experiment_id},
        close_capture=True,
    )

    assert captured == [{
        "obligation_id": base,
        "experiment_id": experiment_id,
        "status": "EXECUTED",
        "reason_code": "",
        "receipt_kind": "TERMINAL_RESULT",
    }]
    # Capture correlation is a view only; delivery/audit evidence is untouched.
    assert batch["results"][0]["obligation_id"] == variant
    assert batch["results"][0]["executed_obligation_id"] == variant
    executor.clear_continuation_retry_receipts(campaign_id)


def test_follow_on_variant_result_drains_selected_identity_without_false_retry(
    monkeypatch,
) -> None:
    import ai_test_asset_center.adaptive_discovery_planner as planner
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    base = "obl-follow-on-base"
    variant = f"{base}__v_runtime"
    campaign_id = "campaign-selected-identity-follow-on"
    executor.clear_continuation_retry_receipts(campaign_id)

    def fake_plan(obligations, **kwargs):
        ids = [row["obligation_id"] for row in obligations]
        return {
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "budget": len(ids),
            "selected": [
                {
                    "obligation_id": oid,
                    "risk_family": "validation",
                    "experiment_id": f"exp_{oid}",
                }
                for oid in ids
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

    batches, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [],
            "pending_next_round": [{"obligation_id": base}],
            "pending_count": 1,
            "pending_truncated": 0,
            "fresh_pending_pool": [{"obligation_id": base}],
            "fresh_pending_pool_count": 1,
            "blocked_retry_pool": [],
            "blocked_retry_pool_count": 0,
            "budget_deferred_pool": [],
            "budget_deferred_pool_count": 0,
        },
        obligations=[_obl(base)],
        experiments_by_obligation={base: _exp(base)},
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id=campaign_id,
        automatic_round_limit=2,
        execute_batch=lambda rows, **kwargs: {
            "results": [{
                "selected_obligation_id": base,
                "obligation_id": variant,
                "executed_obligation_id": variant,
                "experiment_id": f"exp_{base}",
                "status": "EXECUTED",
            }],
            "executed_count": 1,
            "budget_deferred": [],
            "runtime_bindings": {},
        },
    )

    assert final_plan["stop_condition"] == "PENDING_QUEUE_EMPTY"
    assert final_plan["continuation_outstanding_count"] == 0
    assert final_plan["fresh_pending_pool_count"] == 0
    assert final_plan["blocked_retry_pool_count"] == 0
    assert final_plan["budget_deferred_pool_count"] == 0
    assert final_plan["follow_on_round_receipts"][0][
        "unreceipted_selected_count"
    ] == 0
    # The persisted/auditable batch keeps the executed variant identity.
    assert batches[0]["results"][0]["obligation_id"] == variant
    assert batches[0]["results"][0]["executed_obligation_id"] == variant
    executor.clear_continuation_retry_receipts(campaign_id)


def test_raw_harness_failure_alias_enters_retry_instead_of_terminal_done(
    monkeypatch,
) -> None:
    import ai_test_asset_center.adaptive_discovery_planner as planner
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor
    from ai_test_asset_center.continuation_selected_identity_authority import (
        install_initial_capture_selected_identity_bridge,
    )

    oid = "harness-retry"
    campaign_id = "campaign-harness-failure-alias"
    executor.clear_continuation_retry_receipts(campaign_id)
    install_initial_capture_selected_identity_bridge()

    # This is the raw batch vocabulary emitted by the preserved mechanics.
    executor._capture_continuation_execution_receipts(
        campaign_id=campaign_id,
        selected_rows=[{
            "obligation_id": oid,
            "experiment_id": f"exp_{oid}",
        }],
        batch={
            "results": [{
                "obligation_id": oid,
                "experiment_id": f"exp_{oid}",
                "status": "HARNESS_FAILURE",
                "reason_code": "HARNESS_FAILURE",
            }],
            "budget_deferred": [],
        },
    )

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
    retried: list[str] = []

    _, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [],
            "pending_next_round": [],
            "pending_count": 0,
            "pending_truncated": 0,
            "fresh_pending_pool": [],
            "fresh_pending_pool_count": 0,
            "blocked_retry_pool": [],
            "blocked_retry_pool_count": 0,
            "budget_deferred_pool": [],
            "budget_deferred_pool_count": 0,
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
            retried.extend(row["obligation_id"] for row in rows)
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

    assert retried == [oid]
    assert final_plan["blocked_retry_pool_count"] == 0
    assert final_plan["continuation_outstanding_count"] == 0
    assert final_plan["stop_condition"] == "PENDING_QUEUE_EMPTY"
    executor.clear_continuation_retry_receipts(campaign_id)
