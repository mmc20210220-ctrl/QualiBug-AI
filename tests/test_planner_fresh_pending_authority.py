"""Planner must persist the exact fresh tail before bounding its public preview."""
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


def test_obligation_planner_persists_full_fresh_pool_before_preview_cap(monkeypatch) -> None:
    import ai_test_asset_center.adaptive_discovery_planner as planner

    monkeypatch.setattr(planner, "_ABS_MAX_SLICE_BUDGET", 2)
    ids = ["a", "b", "c", "d"]
    plan = planner.plan_obligation_round(
        [_obl(oid) for oid in ids],
        experiments_by_obligation={oid: _exp(oid) for oid in ids},
        behavior_ir={"operations": []},
        budget=1,
    )

    assert plan["selected_count"] == 1
    assert plan["pending_count"] == 3
    assert plan["pending_truncated"] == 1
    assert [row["obligation_id"] for row in plan["pending_next_round"]] == [
        "b",
        "c",
    ]
    assert plan["fresh_pending_pool_count"] == 3
    assert [row["obligation_id"] for row in plan["fresh_pending_pool"]] == [
        "b",
        "c",
        "d",
    ]


def test_coverage_unit_planner_persists_full_fresh_pool_before_preview_cap(monkeypatch) -> None:
    import ai_test_asset_center.adaptive_discovery_planner as planner

    monkeypatch.setattr(planner, "_ABS_MAX_SLICE_BUDGET", 2)
    ids = ["a", "b", "c", "d"]
    obligations = {oid: _obl(oid) for oid in ids}
    units = [
        {
            "coverage_unit_id": f"unit-{oid}",
            "canonical_obligation_key": f"canonical-{oid}",
            "representative_obligation_id": oid,
            "obligation_ids": [oid],
            "variant_count": 1,
            "actor_variants": [],
        }
        for oid in ids
    ]
    plan = planner.plan_coverage_unit_round(
        units,
        obligations_by_id=obligations,
        experiments_by_obligation={oid: _exp(oid) for oid in ids},
        behavior_ir={"operations": []},
        budget=1,
    )

    assert plan["selected_unit_count"] == 1
    assert plan["pending_count"] == 3
    assert plan["pending_truncated"] == 1
    assert [row["obligation_id"] for row in plan["pending_next_round"]] == [
        "b",
        "c",
    ]
    assert plan["fresh_pending_pool_count"] == 3
    assert [row["obligation_id"] for row in plan["fresh_pending_pool"]] == [
        "b",
        "c",
        "d",
    ]
