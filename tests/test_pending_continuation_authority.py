"""Generic regressions for lossless pending-round continuation authority."""
from __future__ import annotations

from ai_test_asset_center.recall_pending_continuation_authority import (
    complete_pending_continuation_rows,
    consume_pending_obligation_rounds,
)


def _obl(
    oid: str,
    *,
    unit: str = "",
    confidence: float = 0.5,
    executable: bool = True,
) -> dict:
    row = {
        "obligation_id": oid,
        "risk_family": "validation",
        "confidence": confidence,
        "required_operations": [f"op_{oid}"],
        "required_actors": [],
        "required_observers": ["http_response"],
        "property": {"template": "input_boundary_validation"},
    }
    if unit:
        row["coverage_unit_id"] = unit
    if not executable:
        row["pre_transport_executable"] = False
    return row


def _exp(oid: str, status: str = "COMPILED") -> dict:
    return {
        "obligation_id": oid,
        "experiment_id": f"exp_{oid}",
        "compile_receipt": {"status": status},
    }


def test_initial_truncated_coverage_unit_pending_restores_one_executable_rep_per_unit() -> None:
    obligations = [
        _obl("selected", unit="u_selected", confidence=0.9),
        _obl("visible", unit="u_visible", confidence=0.8),
        _obl("u3_low", unit="u3", confidence=0.4),
        _obl("u3_high", unit="u3", confidence=0.8),
        _obl("u4_unready", unit="u4", confidence=1.0, executable=False),
    ]
    experiments = {row["obligation_id"]: _exp(row["obligation_id"]) for row in obligations}
    rows, receipt = complete_pending_continuation_rows(
        obligation_plan={
            "plan_authority": "coverage_unit",
            "selected": [{"obligation_id": "selected", "coverage_unit_id": "u_selected"}],
            "pending_next_round": [{"obligation_id": "visible", "coverage_unit_id": "u_visible"}],
            "pending_count": 3,
            "pending_truncated": 2,
        },
        obligations=obligations,
        experiments_by_obligation=experiments,
    )
    assert [row["obligation_id"] for row in rows] == ["visible", "u3_high"]
    assert receipt["status"] == "REBUILT"
    assert receipt["restored_count"] == 1


def test_obligation_mode_restores_only_compiled_transport_executable_rows() -> None:
    obligations = [
        _obl("selected"),
        _obl("visible"),
        _obl("missing_good"),
        _obl("missing_unready", executable=False),
        _obl("missing_blocked"),
    ]
    experiments = {
        "selected": _exp("selected"),
        "visible": _exp("visible"),
        "missing_good": _exp("missing_good"),
        "missing_unready": _exp("missing_unready"),
        "missing_blocked": _exp("missing_blocked", "BLOCKED"),
    }
    rows, receipt = complete_pending_continuation_rows(
        obligation_plan={
            "plan_authority": "obligation",
            "selected": [{"obligation_id": "selected"}],
            "pending_next_round": [{"obligation_id": "visible"}],
            "pending_count": 4,
            "pending_truncated": 3,
        },
        obligations=obligations,
        experiments_by_obligation=experiments,
    )
    assert [row["obligation_id"] for row in rows] == ["visible", "missing_good"]
    assert receipt["restored_count"] == 1


def test_no_truncation_does_not_widen_continuation_authority() -> None:
    rows, receipt = complete_pending_continuation_rows(
        obligation_plan={
            "plan_authority": "obligation",
            "selected": [],
            "pending_next_round": [{"obligation_id": "visible"}],
            "pending_count": 1,
            "pending_truncated": 0,
        },
        obligations=[_obl("visible"), _obl("extra")],
        experiments_by_obligation={"visible": _exp("visible"), "extra": _exp("extra")},
    )
    assert [row["obligation_id"] for row in rows] == ["visible"]
    assert receipt["status"] == "PASS"
    assert receipt["restored_count"] == 0


def test_selected_budget_deferred_row_remains_pending() -> None:
    rows, _ = complete_pending_continuation_rows(
        obligation_plan={
            "plan_authority": "coverage_unit",
            "selected": [{"obligation_id": "selected", "coverage_unit_id": "u1"}],
            # Caller appended this selected row after the executor deferred it.
            "pending_next_round": [{"obligation_id": "selected", "coverage_unit_id": "u1"}],
            "pending_count": 2,
            "pending_truncated": 1,
        },
        obligations=[_obl("selected", unit="u1"), _obl("other", unit="u2")],
        experiments_by_obligation={"selected": _exp("selected"), "other": _exp("other")},
    )
    assert [row["obligation_id"] for row in rows] == ["selected", "other"]


def test_follow_on_round_keeps_ids_omitted_from_planner_pending_preview(monkeypatch) -> None:
    import ai_test_asset_center.adaptive_discovery_planner as planner

    seen_inputs: list[list[str]] = []

    def fake_plan(obligations, **kwargs):
        ids = [row["obligation_id"] for row in obligations]
        seen_inputs.append(ids)
        selected = ids[:1]
        # Deliberately simulate an aggressively truncated public preview: only
        # one of the remaining ids is returned even when more exist.
        preview = ids[1:2]
        return {
            "budget": 1,
            "selected": [{"obligation_id": oid, "risk_family": "validation"} for oid in selected],
            "selected_count": len(selected),
            "pending_next_round": [{"obligation_id": oid} for oid in preview],
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

    def fake_execute(rows, **kwargs):
        results = [
            {"obligation_id": row["obligation_id"], "status": "EXECUTED"}
            for row in rows
        ]
        return {
            "results": results,
            "executed_count": len(results),
            "budget_deferred": [],
            "runtime_bindings": {},
        }

    obligations = [_obl(oid) for oid in ("a", "b", "c", "d")]
    experiments = {row["obligation_id"]: _exp(row["obligation_id"]) for row in obligations}
    batches, final_plan = consume_pending_obligation_rounds(
        obligation_plan={
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [],
            "pending_next_round": [{"obligation_id": oid} for oid in ("a", "b", "c", "d")],
            "pending_count": 4,
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
        campaign_id="c",
        automatic_round_limit=3,
        execute_batch=fake_execute,
    )
    assert len(batches) == 2
    assert seen_inputs[0] == ["a", "b", "c", "d"]
    # b/c/d survive even though fake_plan exposed only b in its public pending preview.
    assert seen_inputs[1] == ["b", "c", "d"]
    assert final_plan["pending_count"] == 2
    assert [row["obligation_id"] for row in final_plan["pending_next_round"]] == ["c", "d"]


def test_compiled_only_deferred_candidate_survives_into_follow_on_planner(monkeypatch) -> None:
    import ai_test_asset_center.adaptive_discovery_planner as planner
    import ai_test_asset_center.discovery_runtime_execution_support as support

    compiled_only_id = "base__v_deadbeef"
    compiled_only = _exp(compiled_only_id)
    compiled_only.update({
        "risk_family": "validation",
        "source_refs": [{"source_id": "spec", "locator": "field-rule"}],
        "expanded_from_obligation_id": "base",
    })
    seen_inputs: list[list[str]] = []

    def fake_plan(obligations, **kwargs):
        ids = [row["obligation_id"] for row in obligations]
        seen_inputs.append(ids)
        exp = kwargs["experiments_by_obligation"][compiled_only_id]
        return {
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "budget": 1,
            "selected": [{
                "obligation_id": compiled_only_id,
                "risk_family": "validation",
                "experiment_id": exp["experiment_id"],
            }],
            "selected_count": 1,
            "pending_next_round": [],
            "pending_count": 0,
            "stop_condition": "in_scope_obligations_scheduled",
        }

    def fake_intents(plan, **kwargs):
        return {"intents": [{"obligation_id": compiled_only_id}]}

    monkeypatch.setattr(planner, "plan_obligation_round", fake_plan)
    monkeypatch.setattr(planner, "build_agent_intent_plan", fake_intents)

    def fake_execute(rows, **kwargs):
        return {
            "results": [{"obligation_id": compiled_only_id, "status": "EXECUTED"}],
            "executed_count": 1,
            "budget_deferred": [],
            "runtime_bindings": {},
        }

    batches, _ = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [],
            "pending_next_round": [{"obligation_id": compiled_only_id}],
            "pending_count": 1,
            "pending_truncated": 0,
        },
        obligations=[_obl("base", confidence=0.73)],
        experiments_by_obligation={compiled_only_id: compiled_only},
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id="c",
        automatic_round_limit=2,
        execute_batch=fake_execute,
    )

    assert seen_inputs == [[compiled_only_id]]
    assert len(batches) == 1


def test_compiled_only_candidate_preserves_coverage_unit_and_parent_semantics() -> None:
    from ai_test_asset_center.discovery_runtime_execution_support import (
        _continuation_obligation_universe,
    )

    compiled_only_id = "base__v_cafebabe"
    compiled_only = _exp(compiled_only_id)
    compiled_only.update({
        "risk_family": "validation",
        "source_refs": [{"source_id": "spec", "locator": "constraint"}],
        "expanded_from_obligation_id": "base",
    })
    merged = _continuation_obligation_universe(
        obligations=[_obl("base", unit="unit-42", confidence=0.81)],
        experiments_by_obligation={compiled_only_id: compiled_only},
        obligation_plan={
            "plan_authority": "coverage_unit",
            "selected": [{
                "obligation_id": compiled_only_id,
                "coverage_unit_id": "unit-42",
            }],
            "pending_next_round": [{
                "obligation_id": compiled_only_id,
                "coverage_unit_id": "unit-42",
            }],
        },
    )
    row = next(item for item in merged if item["obligation_id"] == compiled_only_id)
    assert row["compiled_variant_view"] is True
    assert row["coverage_unit_id"] == "unit-42"
    assert row["confidence"] == 0.81
