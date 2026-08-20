"""Initial exact fresh authority must fail closed when membership is unproven."""
from __future__ import annotations


def _obl(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "risk_family": "validation",
        "confidence": 0.8,
        "pre_transport_executable": True,
    }


def _exp(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "experiment_id": f"exp_{oid}",
        "compile_receipt": {"status": "COMPILED"},
    }


def test_initial_fresh_count_mismatch_falls_back_instead_of_sealing_wrong_pool() -> None:
    from ai_test_asset_center.initial_fresh_pending_authority import (
        seed_initial_fresh_pending_authority,
    )

    ids = ["a", "b"]
    plan = seed_initial_fresh_pending_authority(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [],
            "pending_next_round": [{"obligation_id": "a"}],
            # Deliberately inconsistent with the two executable unselected
            # source obligations. The boundary cannot prove which membership
            # the planner intended, so it must remain in legacy mode.
            "pending_count": 1,
            "pending_truncated": 0,
        },
        obligations=[_obl(oid) for oid in ids],
        experiments_by_obligation={oid: _exp(oid) for oid in ids},
        behavior_ir={"operations": []},
    )

    assert "fresh_pending_pool" not in plan
    assert "fresh_pending_pool_count" not in plan
    receipt = plan["fresh_pending_authority_receipt"]
    assert receipt["status"] == "LEGACY_FALLBACK"
    assert receipt["reason"] == "fresh_membership_count_mismatch"
    assert receipt["derived_fresh_count"] == 2
    assert receipt["planner_pending_count"] == 1


def test_existing_exact_fresh_pool_is_never_rederived_from_changed_universe() -> None:
    from ai_test_asset_center.initial_fresh_pending_authority import (
        seed_initial_fresh_pending_authority,
    )

    persisted = {
        "schema_version": "qualibug.adaptive-obligation-plan.v1",
        "plan_authority": "obligation",
        "budget": 1,
        "selected": [],
        "pending_next_round": [{"obligation_id": "kept"}],
        "pending_count": 1,
        "fresh_pending_pool": [{"obligation_id": "kept"}],
        "fresh_pending_pool_count": 1,
    }
    sealed = seed_initial_fresh_pending_authority(
        obligation_plan=persisted,
        obligations=[_obl("kept"), _obl("new-candidate")],
        experiments_by_obligation={
            "kept": _exp("kept"),
            "new-candidate": _exp("new-candidate"),
        },
        behavior_ir={"operations": []},
    )

    assert sealed["fresh_pending_pool"] == [{"obligation_id": "kept"}]
    assert sealed["fresh_pending_pool_count"] == 1
    assert "new-candidate" not in {
        row["obligation_id"] for row in sealed["fresh_pending_pool"]
    }
