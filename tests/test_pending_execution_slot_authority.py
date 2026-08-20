"""Regressions for continuation queue -> execution-slot Recall authority."""
from __future__ import annotations


def _obl(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "risk_family": "validation",
        "confidence": 0.5,
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


def _plan(ids: list[str]) -> dict:
    selected = ids[:1]
    return {
        "schema_version": "qualibug.adaptive-obligation-plan.v1",
        "budget": 1,
        "selected": [
            {
                "obligation_id": oid,
                "risk_family": "validation",
                "experiment_id": f"exp_{oid}",
            }
            for oid in selected
        ],
        "selected_count": len(selected),
        "pending_next_round": [{"obligation_id": oid} for oid in ids[1:]],
        "pending_count": max(0, len(ids) - 1),
        "stop_condition": "budget_exhausted" if len(ids) > 1 else "in_scope_obligations_scheduled",
    }


def _run(
    *,
    monkeypatch,
    ids: list[str],
    execute_batch,
    automatic_round_limit: int,
    blocked_retry_pool: list[dict] | None = None,
):
    import ai_test_asset_center.adaptive_discovery_planner as planner
    import ai_test_asset_center.discovery_runtime_execution_support as support

    seen_inputs: list[list[str]] = []

    def fake_plan(obligations, **kwargs):
        round_ids = [row["obligation_id"] for row in obligations]
        seen_inputs.append(round_ids)
        return _plan(round_ids)

    def fake_intents(plan, **kwargs):
        return {
            "intents": [
                {"obligation_id": row["obligation_id"]}
                for row in plan.get("selected", [])
            ]
        }

    monkeypatch.setattr(planner, "plan_obligation_round", fake_plan)
    monkeypatch.setattr(planner, "build_agent_intent_plan", fake_intents)

    obligations = [_obl(oid) for oid in ids]
    experiments = {oid: _exp(oid) for oid in ids}
    batches, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [],
            "pending_next_round": [{"obligation_id": oid} for oid in ids],
            "pending_count": len(ids),
            "pending_truncated": 0,
            "blocked_retry_pool": list(blocked_retry_pool or []),
        },
        obligations=obligations,
        experiments_by_obligation=experiments,
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id="c",
        automatic_round_limit=automatic_round_limit,
        execute_batch=execute_batch,
    )
    return seen_inputs, batches, final_plan


def test_same_error_across_fresh_candidates_does_not_stop_recall(monkeypatch) -> None:
    def execute_batch(rows, **kwargs):
        oid = rows[0]["obligation_id"]
        return {
            "results": [{
                "obligation_id": oid,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_POLICY_STATIC",
            }],
            "executed_count": 0,
            "budget_deferred": [],
            "runtime_bindings": {},
        }

    seen, batches, final_plan = _run(
        monkeypatch=monkeypatch,
        ids=["a", "b", "c", "d", "e"],
        execute_batch=execute_batch,
        automatic_round_limit=8,
    )

    assert [round_ids[0] for round_ids in seen] == ["a", "b", "c", "d", "e"]
    assert len(batches) == 5
    assert final_plan["pending_count"] == 0
    assert final_plan["early_stop_reason"] == "PENDING_QUEUE_EMPTY"


def test_selected_without_terminal_receipt_stays_pending(monkeypatch) -> None:
    def execute_batch(rows, **kwargs):
        return {
            "results": [],
            "executed_count": 0,
            "budget_deferred": [],
            "runtime_bindings": {},
        }

    seen, batches, final_plan = _run(
        monkeypatch=monkeypatch,
        ids=["a"],
        execute_batch=execute_batch,
        automatic_round_limit=5,
    )

    assert seen == [["a"], ["a"], ["a"]]
    assert len(batches) == 3
    assert final_plan["early_stop_reason"] == "NO_PROGRESS_3_CONSECUTIVE_ROUNDS"
    assert final_plan["pending_count"] == 1
    assert [row["obligation_id"] for row in final_plan["pending_next_round"]] == ["a"]
    assert final_plan["follow_on_round_receipts"][-1]["unreceipted_selected_count"] == 1


def test_retry_backlog_cannot_monopolize_slots_while_fresh_work_exists(monkeypatch) -> None:
    def execute_batch(rows, **kwargs):
        oid = rows[0]["obligation_id"]
        if oid == "retry":
            result = {
                "obligation_id": oid,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
            }
        else:
            result = {"obligation_id": oid, "status": "EXECUTED"}
        return {
            "results": [result],
            "executed_count": 1 if oid != "retry" else 0,
            "budget_deferred": [],
            "runtime_bindings": {},
        }

    seen, batches, final_plan = _run(
        monkeypatch=monkeypatch,
        ids=["retry", "fresh1", "fresh2"],
        execute_batch=execute_batch,
        automatic_round_limit=4,
        blocked_retry_pool=[{
            "obligation_id": "retry",
            "block_reason": "BLOCKED_MISSING_BINDING",
        }],
    )

    assert seen == [["fresh1", "fresh2"], ["fresh2"], ["retry"]]
    assert len(batches) == 3
    assert final_plan["pending_count"] == 1
    assert [row["obligation_id"] for row in final_plan["pending_next_round"]] == ["retry"]
    assert final_plan["stop_condition"] == "round_limit_reached"
    assert final_plan["round_limit_reached"] is True
