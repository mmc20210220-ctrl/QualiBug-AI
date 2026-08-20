"""Legacy retry/deferred resumes must not be mislabelled exact fresh."""
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


def test_legacy_retry_pool_does_not_promote_all_unselected_source_to_exact_fresh() -> None:
    from ai_test_asset_center.initial_fresh_pending_authority import (
        seed_initial_fresh_pending_authority,
    )

    plan = seed_initial_fresh_pending_authority(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "pending_next_round": [{"obligation_id": "visible"}],
            "pending_count": 2,
            "selected": [],
            "blocked_retry_pool": [{
                "obligation_id": "retry",
                "block_reason": "BLOCKED_MISSING_BINDING",
            }],
            "blocked_retry_pool_count": 1,
        },
        obligations=[
            _obl("visible"),
            _obl("retry"),
            # This row could have completed in a prior follow-on round. Legacy
            # state has no exact fresh membership proving that it is still due.
            _obl("historically-completed-unselected"),
        ],
        experiments_by_obligation={
            oid: _exp(oid)
            for oid in (
                "visible",
                "retry",
                "historically-completed-unselected",
            )
        },
        behavior_ir={"operations": []},
    )

    assert "fresh_pending_pool" not in plan
    receipt = plan["fresh_pending_authority_receipt"]
    assert receipt["status"] == "LEGACY_FALLBACK"
    assert receipt["reason"] == (
        "mixed_legacy_resume_pools_without_exact_fresh_membership"
    )
    assert receipt["blocked_retry_pool_count"] == 1


def test_legacy_budget_deferred_pool_also_stays_in_legacy_mode() -> None:
    from ai_test_asset_center.initial_fresh_pending_authority import (
        seed_initial_fresh_pending_authority,
    )

    plan = seed_initial_fresh_pending_authority(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "pending_next_round": [{"obligation_id": "visible"}],
            "pending_count": 2,
            "selected": [{"obligation_id": "deferred"}],
            "budget_deferred_pool": [{"obligation_id": "deferred"}],
            "budget_deferred_pool_count": 1,
        },
        obligations=[_obl("visible"), _obl("deferred"), _obl("extra")],
        experiments_by_obligation={
            oid: _exp(oid) for oid in ("visible", "deferred", "extra")
        },
        behavior_ir={"operations": []},
    )

    assert "fresh_pending_pool" not in plan
    assert plan["fresh_pending_authority_receipt"]["status"] == "LEGACY_FALLBACK"
    assert plan["fresh_pending_authority_receipt"]["budget_deferred_pool_count"] == 1
