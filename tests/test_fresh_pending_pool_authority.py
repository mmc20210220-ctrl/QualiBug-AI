"""Fresh continuation tail must persist as exact identity authority."""
from __future__ import annotations


def _obl(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "risk_family": "validation",
        "confidence": 0.8,
        "required_operations": ["op-1"],
        "required_actors": [],
        "required_observers": ["http_response"],
        "property": {"template": "input_boundary_validation"},
    }


def _exp(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "experiment_id": f"exp_{oid}",
        "risk_family": "validation",
        "primary_operation_ref": "op-1",
        "compile_receipt": {"status": "COMPILED"},
    }


def test_exact_fresh_pool_beats_compiler_only_reconstruction_guess() -> None:
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    visible = "formal-visible"
    true_tail = "z-formal-tail"
    compiler_only = "a-compiler-only"
    campaign_id = "campaign-exact-fresh-pool"
    executor.clear_continuation_retry_receipts(campaign_id)

    _, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [],
            "pending_next_round": [{"obligation_id": visible}],
            "pending_count": 2,
            "pending_truncated": 1,
            "fresh_pending_pool": [
                {"obligation_id": visible},
                {"obligation_id": true_tail},
            ],
            "fresh_pending_pool_count": 2,
        },
        obligations=[_obl(visible), _obl(true_tail)],
        experiments_by_obligation={
            visible: _exp(visible),
            true_tail: _exp(true_tail),
            compiler_only: _exp(compiler_only),
        },
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id=campaign_id,
        automatic_round_limit=1,
        execute_batch=lambda rows, **kwargs: (_ for _ in ()).throw(
            AssertionError("round-limit-one exact resume must not execute")
        ),
    )

    assert final_plan["fresh_pending_pool_count"] == 2
    assert [row["obligation_id"] for row in final_plan["fresh_pending_pool"]] == [
        visible,
        true_tail,
    ]
    assert compiler_only not in {
        row["obligation_id"] for row in final_plan["fresh_pending_pool"]
    }
    executor.clear_continuation_retry_receipts(campaign_id)


def test_fresh_pool_persists_full_tail_beyond_public_preview_cap() -> None:
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor
    from ai_test_asset_center.pipeline_slices import _ABS_MAX_SLICE_BUDGET

    count = _ABS_MAX_SLICE_BUDGET + 11
    ids = [f"fresh-{index:04d}" for index in range(count)]
    campaign_id = "campaign-fresh-pool-over-cap"
    executor.clear_continuation_retry_receipts(campaign_id)

    _, final_plan = support._consume_pending_obligation_rounds(
        obligation_plan={
            "schema_version": "qualibug.adaptive-obligation-plan.v1",
            "plan_authority": "obligation",
            "budget": 1,
            "selected": [],
            "pending_next_round": [
                {"obligation_id": oid}
                for oid in ids[:_ABS_MAX_SLICE_BUDGET]
            ],
            "pending_count": count,
            "pending_truncated": count - _ABS_MAX_SLICE_BUDGET,
        },
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
            AssertionError("round-limit-one fresh tail must not execute")
        ),
    )

    assert final_plan["pending_count"] == count
    assert len(final_plan["pending_next_round"]) == _ABS_MAX_SLICE_BUDGET
    assert final_plan["fresh_pending_pool_count"] == count
    assert [row["obligation_id"] for row in final_plan["fresh_pending_pool"]] == ids
    executor.clear_continuation_retry_receipts(campaign_id)
