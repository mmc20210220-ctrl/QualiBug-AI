"""Exact continuation pools fail closed when persisted cardinality is corrupt."""
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


def test_fresh_pool_count_mismatch_stops_before_execution() -> None:
    import ai_test_asset_center.discovery_runtime_execution_support as support

    called = False

    def execute_batch(rows, **kwargs):
        nonlocal called
        called = True
        return {}

    _, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "budget": 1,
            "fresh_pending_pool": [{"obligation_id": "a"}],
            "fresh_pending_pool_count": 2,
            "blocked_retry_pool": [],
            "blocked_retry_pool_count": 0,
            "budget_deferred_pool": [],
            "budget_deferred_pool_count": 0,
        },
        obligations=[_obl("a"), _obl("missing")],
        experiments_by_obligation={
            "a": _exp("a"),
            "missing": _exp("missing"),
        },
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id="campaign-corrupt-fresh-count",
        automatic_round_limit=16,
        execute_batch=execute_batch,
    )

    assert called is False
    assert final_plan["continuation_authority_corrupt"] is True
    assert final_plan["stop_condition"] == (
        "CONTINUATION_AUTHORITY_COUNT_MISMATCH:fresh_pending_pool"
    )
    receipt = final_plan["continuation_pool_integrity_receipt"]
    assert receipt["status"] == "FAIL"
    assert receipt["failures"][0]["declared_count"] == 2
    assert receipt["failures"][0]["actual_unique_count"] == 1


def test_retry_pool_count_mismatch_is_not_downgraded_to_known_subset() -> None:
    from ai_test_asset_center.continuation_pool_integrity import (
        validate_continuation_pool_integrity,
    )

    plan, ok = validate_continuation_pool_integrity({
        "fresh_pending_pool": [],
        "fresh_pending_pool_count": 0,
        "blocked_retry_pool": [{
            "obligation_id": "retry-known",
            "block_reason": "BLOCKED_MISSING_BINDING",
        }],
        "blocked_retry_pool_count": 137,
        "budget_deferred_pool": [],
        "budget_deferred_pool_count": 0,
    })

    assert ok is False
    assert plan["continuation_authority_corrupt"] is True
    assert plan["stop_condition"] == (
        "CONTINUATION_AUTHORITY_COUNT_MISMATCH:blocked_retry_pool"
    )
    assert plan["blocked_retry_pool_count"] == 137
    assert len(plan["blocked_retry_pool"]) == 1


def test_legacy_pool_without_count_is_safely_normalized() -> None:
    from ai_test_asset_center.continuation_pool_integrity import (
        validate_continuation_pool_integrity,
    )

    plan, ok = validate_continuation_pool_integrity({
        "fresh_pending_pool": [
            {"obligation_id": "a"},
            {"obligation_id": "b"},
        ],
        "blocked_retry_pool": [],
        "budget_deferred_pool": [],
    })

    assert ok is True
    assert plan["fresh_pending_pool_count"] == 2
    assert plan["blocked_retry_pool_count"] == 0
    assert plan["budget_deferred_pool_count"] == 0
    checks = plan["continuation_pool_integrity_receipt"]["checks"]
    assert all(row["status"] == "NORMALIZED_LEGACY_COUNT" for row in checks)
