"""Regressions for lossless blocked-retry persistence across continuation limits."""
from __future__ import annotations


_RETRY_REASON = "BLOCKED_MISSING_BINDING"
_RETRY_COUNT = 137


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


def _resume_plan(ids: list[str], *, budget: int) -> dict:
    return {
        "schema_version": "qualibug.adaptive-obligation-plan.v1",
        "plan_authority": "obligation",
        "budget": budget,
        "selected": [],
        "pending_next_round": [],
        "pending_count": 0,
        "pending_truncated": 0,
        "blocked_retry_pool": [
            {"obligation_id": oid, "block_reason": _RETRY_REASON}
            for oid in ids
        ],
    }


def test_blocked_retry_resume_preserves_more_than_100_rows_at_round_limit_one() -> None:
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    ids = [f"retry-{index:03d}" for index in range(_RETRY_COUNT)]
    campaign_id = "campaign-blocked-retry-resume-137"
    executor.clear_continuation_retry_receipts(campaign_id)

    _, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan=_resume_plan(ids, budget=1),
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

    assert final_plan["stop_condition"] == "PENDING_NEXT_ROUND_SKIPPED_ROUND_LIMIT_ONE"
    assert final_plan["blocked_retry_pool_count"] == _RETRY_COUNT
    assert [row["obligation_id"] for row in final_plan["blocked_retry_pool"]] == ids
    executor.clear_continuation_retry_receipts(campaign_id)


def test_blocked_retry_terminal_sealing_preserves_more_than_100_rows(monkeypatch) -> None:
    import ai_test_asset_center.adaptive_discovery_planner as planner
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    ids = [f"retry-{index:03d}" for index in range(_RETRY_COUNT)]
    campaign_id = "campaign-blocked-retry-terminal-seal-137"
    executor.clear_continuation_retry_receipts(campaign_id)

    def fake_plan(obligations, **kwargs):
        selected = [
            {
                "obligation_id": row["obligation_id"],
                "risk_family": "validation",
                "experiment_id": f"exp_{row['obligation_id']}",
            }
            for row in obligations
        ]
        return {
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "budget": len(selected),
            "selected": selected,
            "selected_count": len(selected),
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

    def execute_still_blocked(rows, **kwargs):
        return {
            "results": [
                {
                    "obligation_id": row["obligation_id"],
                    "status": "BLOCKED",
                    "reason_code": _RETRY_REASON,
                }
                for row in rows
            ],
            "executed_count": 0,
            "budget_deferred": [],
            "runtime_bindings": {},
        }

    _, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan=_resume_plan(ids, budget=_RETRY_COUNT),
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
        execute_batch=execute_still_blocked,
    )

    assert final_plan["stop_condition"] == "round_limit_reached"
    assert final_plan["round_limit_reached"] is True
    assert final_plan["blocked_retry_pool_count"] == _RETRY_COUNT
    assert [row["obligation_id"] for row in final_plan["blocked_retry_pool"]] == ids
    executor.clear_continuation_retry_receipts(campaign_id)
