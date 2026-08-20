"""Continuation preview metadata must describe the current exact queue."""
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


def _plan(ids: list[str], *, stale_truncated: int) -> dict:
    return {
        "schema_version": "qualibug.adaptive-obligation-plan.v1",
        "plan_authority": "obligation",
        "budget": 1,
        "selected": [],
        "pending_next_round": [{"obligation_id": ids[0]}] if ids else [],
        "pending_count": len(ids),
        "pending_truncated": stale_truncated,
        "pending_truncation_reason": "STALE_REASON" if stale_truncated else "",
        "fresh_pending_pool": [{"obligation_id": oid} for oid in ids],
        "fresh_pending_pool_count": len(ids),
    }


def test_round_limit_recomputes_preview_truncation_from_current_queue(monkeypatch) -> None:
    import ai_test_asset_center.discovery_continuation_authority as authority
    import ai_test_asset_center.experiment_executor as executor
    import ai_test_asset_center.pipeline_slices as slices

    monkeypatch.setattr(slices, "_ABS_MAX_SLICE_BUDGET", 2)
    ids = ["a", "b", "c"]
    campaign_id = "campaign-preview-metadata-cap"
    executor.clear_continuation_retry_receipts(campaign_id)

    _, final_plan = authority._consume_pending_obligation_rounds(
        obligation_plan=_plan(ids, stale_truncated=99),
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
            AssertionError("round-limit-one metadata test must not execute")
        ),
    )

    assert final_plan["pending_count"] == 3
    assert len(final_plan["pending_next_round"]) == 2
    assert final_plan["pending_truncated"] == 1
    assert "3" in final_plan["pending_truncation_reason"]
    assert "2" in final_plan["pending_truncation_reason"]
    executor.clear_continuation_retry_receipts(campaign_id)


def test_round_limit_clears_stale_truncation_when_current_queue_fits() -> None:
    import ai_test_asset_center.discovery_continuation_authority as authority
    import ai_test_asset_center.experiment_executor as executor

    ids = ["only"]
    campaign_id = "campaign-preview-metadata-clear"
    executor.clear_continuation_retry_receipts(campaign_id)

    _, final_plan = authority._consume_pending_obligation_rounds(
        obligation_plan=_plan(ids, stale_truncated=99),
        obligations=[_obl("only")],
        experiments_by_obligation={"only": _exp("only")},
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id=campaign_id,
        automatic_round_limit=1,
        execute_batch=lambda rows, **kwargs: (_ for _ in ()).throw(
            AssertionError("round-limit-one metadata test must not execute")
        ),
    )

    assert final_plan["pending_count"] == 1
    assert [row["obligation_id"] for row in final_plan["pending_next_round"]] == [
        "only"
    ]
    assert final_plan["pending_truncated"] == 0
    assert final_plan["pending_truncation_reason"] == ""
    executor.clear_continuation_retry_receipts(campaign_id)
