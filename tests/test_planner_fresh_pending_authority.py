"""Continuation must seal the exact fresh tail before preview reconstruction."""
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


def test_obligation_boundary_seals_full_fresh_pool_after_preview_cap(monkeypatch) -> None:
    import ai_test_asset_center.adaptive_discovery_planner as planner
    from ai_test_asset_center.initial_fresh_pending_authority import (
        seed_initial_fresh_pending_authority,
    )

    monkeypatch.setattr(planner, "_ABS_MAX_SLICE_BUDGET", 2)
    ids = ["a", "b", "c", "d"]
    obligations = [_obl(oid) for oid in ids]
    experiments = {oid: _exp(oid) for oid in ids}
    plan = planner.plan_obligation_round(
        obligations,
        experiments_by_obligation=experiments,
        behavior_ir={"operations": []},
        budget=1,
    )

    assert plan["selected_count"] == 1
    assert plan["pending_count"] == 3
    assert plan["pending_truncated"] == 1
    assert len(plan["pending_next_round"]) == 2
    sealed = seed_initial_fresh_pending_authority(
        obligation_plan=plan,
        obligations=obligations,
        experiments_by_obligation=experiments,
        behavior_ir={"operations": []},
    )
    selected_ids = {row["obligation_id"] for row in plan["selected"]}
    expected = set(ids) - selected_ids
    assert sealed["fresh_pending_pool_count"] == 3
    assert {
        row["obligation_id"] for row in sealed["fresh_pending_pool"]
    } == expected
    assert {
        row["obligation_id"] for row in plan["pending_next_round"]
    }.issubset(expected)


def test_coverage_unit_boundary_seals_full_fresh_pool_after_preview_cap(monkeypatch) -> None:
    import ai_test_asset_center.adaptive_discovery_planner as planner
    from ai_test_asset_center.coverage_unit_registry import (
        attach_canonical_obligation_keys,
        build_coverage_units,
    )
    from ai_test_asset_center.initial_fresh_pending_authority import (
        seed_initial_fresh_pending_authority,
    )

    monkeypatch.setattr(planner, "_ABS_MAX_SLICE_BUDGET", 2)
    ids = ["a", "b", "c", "d"]
    behavior_ir = {"operations": []}
    obligations = attach_canonical_obligation_keys(
        [_obl(oid) for oid in ids],
        behavior_ir=behavior_ir,
    )
    units = build_coverage_units(
        obligations,
        behavior_ir=behavior_ir,
    )["coverage_units"]
    obligations_by_id = {
        row["obligation_id"]: row for row in obligations
    }
    experiments = {oid: _exp(oid) for oid in ids}
    plan = planner.plan_coverage_unit_round(
        units,
        obligations_by_id=obligations_by_id,
        experiments_by_obligation=experiments,
        behavior_ir=behavior_ir,
        budget=1,
    )

    assert plan["selected_unit_count"] == 1
    assert plan["pending_count"] == 3
    assert plan["pending_truncated"] == 1
    assert len(plan["pending_next_round"]) == 2
    sealed = seed_initial_fresh_pending_authority(
        obligation_plan=plan,
        obligations=obligations,
        experiments_by_obligation=experiments,
        behavior_ir=behavior_ir,
    )
    selected_units = {
        row["coverage_unit_id"] for row in plan["selected_units"]
    }
    expected_units = {
        row["coverage_unit_id"] for row in units
    } - selected_units
    assert sealed["fresh_pending_pool_count"] == 3
    assert {
        row["coverage_unit_id"] for row in sealed["fresh_pending_pool"]
    } == expected_units
