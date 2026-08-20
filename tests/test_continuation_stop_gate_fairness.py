"""Recall regressions for continuation stop-gate fairness."""
from __future__ import annotations


def _obl(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "risk_family": "validation",
        "confidence": 0.5,
        "required_operations": [f"op_{oid}"],
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


def _install_single_slot_planner(monkeypatch, seen_inputs: list[list[str]]) -> None:
    import ai_test_asset_center.adaptive_discovery_planner as planner

    def fake_plan(obligations, **kwargs):
        ids = [row["obligation_id"] for row in obligations]
        seen_inputs.append(ids)
        selected = ids[:1]
        return {
            "budget": 1,
            "selected": [{"obligation_id": oid, "risk_family": "validation"} for oid in selected],
            "selected_count": len(selected),
            "pending_next_round": [{"obligation_id": oid} for oid in ids[1:]],
            "pending_count": max(0, len(ids) - 1),
            "stop_condition": "budget_exhausted" if len(ids) > 1 else "in_scope_units_scheduled",
        }

    def fake_intents(plan, **kwargs):
        return {
            "intents": [
                {"obligation_id": row["obligation_id"]}
                for row in plan.get("selected", [])
            ]
        }

    monkeypatch.setattr(planner, "plan_obligation_round", fake_plan)
    monkeypatch.setattr(planner, "build_agent_intent_plan", fake_intents)


def test_unreceipted_fresh_candidate_is_demoted_so_tail_advances(monkeypatch) -> None:
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    seen_inputs: list[list[str]] = []
    _install_single_slot_planner(monkeypatch, seen_inputs)
    monkeypatch.setattr(
        executor,
        "consume_continuation_execution_receipts",
        lambda *args, **kwargs: [],
    )

    # Every fresh scheduling attempt gets no receipt. The important invariant is
    # that a/b/c/d each receive a slot before retry-only no-progress guards fire.
    def no_receipt_execute(rows, **kwargs):
        return {
            "results": [],
            "executed_count": 0,
            "budget_deferred": [],
            "runtime_bindings": {},
        }

    ids = ["a", "b", "c", "d"]
    obligations = [_obl(oid) for oid in ids]
    experiments = {oid: _exp(oid) for oid in ids}
    batches, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [],
            "pending_next_round": [{"obligation_id": oid} for oid in ids],
            "pending_count": len(ids),
            "pending_truncated": 0,
        },
        obligations=obligations,
        experiments_by_obligation=experiments,
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id="fresh-tail",
        automatic_round_limit=5,
        execute_batch=no_receipt_execute,
    )

    assert len(batches) == 4
    assert seen_inputs[:4] == [
        ["a", "b", "c", "d"],
        ["b", "c", "d"],
        ["c", "d"],
        ["d"],
    ]
    assert not str(final_plan.get("early_stop_reason") or "").startswith("NO_PROGRESS")
    retry_rows = final_plan.get("blocked_retry_pool") or []
    assert {row["obligation_id"] for row in retry_rows} == set(ids)
    assert all(row["block_reason"] == "UNRECEIPTED_SELECTED" for row in retry_rows)


def test_initial_unreceipted_selection_is_retry_not_fresh(monkeypatch) -> None:
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    seen_inputs: list[list[str]] = []
    _install_single_slot_planner(monkeypatch, seen_inputs)
    monkeypatch.setattr(
        executor,
        "consume_continuation_execution_receipts",
        lambda *args, **kwargs: [{
            "obligation_id": "a",
            "experiment_id": "exp_a",
            "status": "UNRECEIPTED",
            "receipt_kind": "UNRECEIPTED_SELECTED",
        }],
    )

    def terminal_execute(rows, **kwargs):
        return {
            "results": [
                {"obligation_id": row["obligation_id"], "status": "EXECUTED"}
                for row in rows
            ],
            "executed_count": len(rows),
            "budget_deferred": [],
            "runtime_bindings": {},
        }

    ids = ["a", "b", "c"]
    obligations = [_obl(oid) for oid in ids]
    experiments = {oid: _exp(oid) for oid in ids}
    _, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [{"obligation_id": "a"}],
            "pending_next_round": [
                {"obligation_id": "b"},
                {"obligation_id": "c"},
            ],
            "pending_count": 2,
            "pending_truncated": 0,
        },
        obligations=obligations,
        experiments_by_obligation=experiments,
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id="initial-unreceipted",
        automatic_round_limit=3,
        execute_batch=terminal_execute,
    )

    # a already consumed an initial scheduling opportunity, so unseen b/c must
    # be planned before a receives a retry slot.
    assert seen_inputs[0] == ["b", "c"]
    retry_rows = final_plan.get("blocked_retry_pool") or []
    assert any(
        row.get("obligation_id") == "a"
        and row.get("block_reason") == "UNRECEIPTED_SELECTED"
        for row in retry_rows
    )
