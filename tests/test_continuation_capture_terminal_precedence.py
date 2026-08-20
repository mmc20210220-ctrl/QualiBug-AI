"""A real initial attempt cannot be downgraded by a later non-attempt stage."""
from __future__ import annotations


def _capture(executor, *, campaign_id: str, oid: str, experiment_id: str, batch: dict) -> None:
    executor._capture_continuation_execution_receipts(
        campaign_id=campaign_id,
        selected_rows=[{
            "obligation_id": oid,
            "experiment_id": experiment_id,
        }],
        batch=batch,
    )


def test_later_budget_deferred_cannot_overwrite_prior_terminal_result() -> None:
    import ai_test_asset_center.experiment_executor as executor
    from ai_test_asset_center.continuation_selected_identity_authority import (
        install_initial_capture_selected_identity_bridge,
    )

    campaign_id = "campaign-terminal-before-deferred"
    oid = "same-obligation"
    experiment_id = "exp_same"
    executor.clear_continuation_retry_receipts(campaign_id)
    install_initial_capture_selected_identity_bridge()

    _capture(
        executor,
        campaign_id=campaign_id,
        oid=oid,
        experiment_id=experiment_id,
        batch={
            "results": [{
                "obligation_id": oid,
                "experiment_id": experiment_id,
                "status": "EXECUTED",
            }],
            "budget_deferred": [],
        },
    )
    # A later expansion stage selected the same stable experiment but did not
    # reach transport because its local batch budget was already exhausted.
    _capture(
        executor,
        campaign_id=campaign_id,
        oid=oid,
        experiment_id=experiment_id,
        batch={
            "results": [],
            "budget_deferred": [{
                "obligation_id": oid,
                "experiment_id": experiment_id,
                "reason_code": "BUDGET_DEFERRED",
            }],
        },
    )

    captured = executor.consume_continuation_execution_receipts(
        campaign_id,
        allowed_experiment_ids_by_obligation={oid: experiment_id},
        close_capture=True,
    )
    assert captured == [{
        "obligation_id": oid,
        "experiment_id": experiment_id,
        "status": "EXECUTED",
        "reason_code": "",
        "receipt_kind": "TERMINAL_RESULT",
    }]
    executor.clear_continuation_retry_receipts(campaign_id)


def test_later_terminal_result_still_overwrites_prior_deferred_state() -> None:
    import ai_test_asset_center.experiment_executor as executor
    from ai_test_asset_center.continuation_selected_identity_authority import (
        install_initial_capture_selected_identity_bridge,
    )

    campaign_id = "campaign-deferred-before-terminal"
    oid = "same-obligation"
    experiment_id = "exp_same"
    executor.clear_continuation_retry_receipts(campaign_id)
    install_initial_capture_selected_identity_bridge()

    _capture(
        executor,
        campaign_id=campaign_id,
        oid=oid,
        experiment_id=experiment_id,
        batch={
            "results": [],
            "budget_deferred": [{
                "obligation_id": oid,
                "experiment_id": experiment_id,
                "reason_code": "BUDGET_DEFERRED",
            }],
        },
    )
    _capture(
        executor,
        campaign_id=campaign_id,
        oid=oid,
        experiment_id=experiment_id,
        batch={
            "results": [{
                "obligation_id": oid,
                "experiment_id": experiment_id,
                "status": "EXECUTED",
            }],
            "budget_deferred": [],
        },
    )

    captured = executor.consume_continuation_execution_receipts(
        campaign_id,
        allowed_experiment_ids_by_obligation={oid: experiment_id},
        close_capture=True,
    )
    assert captured == [{
        "obligation_id": oid,
        "experiment_id": experiment_id,
        "status": "EXECUTED",
        "reason_code": "",
        "receipt_kind": "TERMINAL_RESULT",
    }]
    executor.clear_continuation_retry_receipts(campaign_id)
