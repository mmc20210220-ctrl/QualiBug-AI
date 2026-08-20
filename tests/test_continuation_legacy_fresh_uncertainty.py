"""Inferred legacy fresh membership must never be promoted to exact authority."""
from __future__ import annotations


def _obl(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "risk_family": "validation",
        "confidence": 0.8,
        "pre_transport_executable": True,
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


def test_mixed_legacy_resume_never_seals_reconstructed_fresh_as_exact() -> None:
    import ai_test_asset_center.discovery_runtime_execution_support as support
    import ai_test_asset_center.experiment_executor as executor

    visible = "fresh-visible"
    hidden = "fresh-hidden"
    retry = "legacy-retry"
    campaign_id = "campaign-legacy-fresh-uncertain"
    executor.clear_continuation_retry_receipts(campaign_id)
    obligations = [_obl(visible), _obl(hidden), _obl(retry)]
    experiments = {oid: _exp(oid) for oid in (visible, hidden, retry)}

    first_plan = {
        "schema_version": "qualibug.adaptive-obligation-plan.v1",
        "plan_authority": "obligation",
        "budget": 1,
        "selected": [],
        "pending_next_round": [{"obligation_id": visible}],
        "pending_count": 2,
        "pending_truncated": 1,
        # The retired consumer could persist this generic retry category but no
        # exact fresh pool. Therefore hidden fresh identity is not provable.
        "blocked_retry_pool": [{
            "obligation_id": retry,
            "block_reason": "RETRY_ELIGIBLE",
        }],
    }

    _, after_first = support._consume_pending_obligation_rounds(
        obligation_plan=first_plan,
        obligations=obligations,
        experiments_by_obligation=experiments,
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id=campaign_id,
        automatic_round_limit=1,
        execute_batch=lambda rows, **kwargs: {},
    )

    assert after_first["legacy_fresh_membership_uncertain"] is True
    assert "fresh_pending_pool" not in after_first
    assert "fresh_pending_pool_count" not in after_first
    assert after_first["fresh_pending_authority_receipt"]["status"] == (
        "LEGACY_FALLBACK"
    )
    assert after_first["fresh_pending_authority_receipt"][
        "exact_promotion_prevented"
    ] is True
    assert after_first["blocked_retry_pool"][0]["block_reason"] == (
        "RETRY_ELIGIBLE"
    )

    # Even after the retry category disappears, the old fresh membership does
    # not become more knowable. Sticky uncertainty must prevent a later source
    # derivation from reclassifying the compatibility reconstruction as exact.
    second_input = {
        **after_first,
        "blocked_retry_pool": [],
        "blocked_retry_pool_count": 0,
    }
    _, after_second = support._consume_pending_obligation_rounds(
        obligation_plan=second_input,
        obligations=obligations,
        experiments_by_obligation=experiments,
        behavior_ir={"operations": []},
        root=".",
        project="p",
        base_url="http://example.invalid",
        runtime_contract={},
        mainline_run={},
        campaign_id=campaign_id,
        automatic_round_limit=1,
        execute_batch=lambda rows, **kwargs: {},
    )

    assert after_second["legacy_fresh_membership_uncertain"] is True
    assert "fresh_pending_pool" not in after_second
    assert "fresh_pending_pool_count" not in after_second
    assert after_second["fresh_pending_authority_receipt"]["status"] == (
        "LEGACY_FALLBACK"
    )
    assert after_second["fresh_pending_authority_receipt"][
        "exact_promotion_prevented"
    ] is True
    executor.clear_continuation_retry_receipts(campaign_id)
