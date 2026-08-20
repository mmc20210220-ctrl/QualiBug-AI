"""Continuation receipt consumption must not cross experiment domains."""
from __future__ import annotations


def test_empty_experiment_receipt_is_not_wildcard_for_exact_domain() -> None:
    import ai_test_asset_center.experiment_executor as executor

    campaign_id = "campaign-receipt-domain-empty-is-not-wildcard"
    oid = "same-obligation"
    executor.clear_continuation_retry_receipts(campaign_id)

    # Unbound executor-side result: real outcome, but no experiment lineage.
    executor._capture_continuation_execution_receipts(
        campaign_id=campaign_id,
        selected_rows=[],
        batch={
            "results": [{
                "obligation_id": oid,
                "status": "EXECUTED",
                "experiment_id": "",
            }],
            "budget_deferred": [],
        },
    )
    # Properly bound main-domain selected receipt for the same formal id.
    executor._capture_continuation_execution_receipts(
        campaign_id=campaign_id,
        selected_rows=[{
            "obligation_id": oid,
            "experiment_id": "exp-main",
        }],
        batch={
            "results": [],
            "budget_deferred": [{
                "obligation_id": oid,
                "experiment_id": "exp-main",
                "reason_code": "BUDGET_DEFERRED",
            }],
        },
    )

    main = executor.consume_continuation_execution_receipts(
        campaign_id,
        allowed_experiment_ids_by_obligation={oid: "exp-main"},
        close_capture=True,
    )
    assert len(main) == 1
    assert main[0]["experiment_id"] == "exp-main"
    assert main[0]["receipt_kind"] == "BUDGET_DEFERRED"

    # The unbound receipt remains available only to an explicitly legacy/
    # wildcard consumer; it was not stolen by the exact main domain.
    remaining = executor.consume_continuation_execution_receipts(
        campaign_id,
        allowed_experiment_ids_by_obligation={oid: ""},
        close_capture=True,
    )
    assert len(remaining) == 1
    assert remaining[0]["experiment_id"] == ""
    assert remaining[0]["receipt_kind"] == "TERMINAL_RESULT"
    executor.clear_continuation_retry_receipts(campaign_id)


def test_same_obligation_two_exact_experiment_domains_are_consumed_separately() -> None:
    import ai_test_asset_center.experiment_executor as executor

    campaign_id = "campaign-receipt-domain-same-obligation"
    oid = "same-obligation"
    executor.clear_continuation_retry_receipts(campaign_id)

    for experiment_id in ("exp-main", "exp-expansion"):
        executor._capture_continuation_execution_receipts(
            campaign_id=campaign_id,
            selected_rows=[{
                "obligation_id": oid,
                "experiment_id": experiment_id,
            }],
            batch={
                "results": [],
                "budget_deferred": [{
                    "obligation_id": oid,
                    "experiment_id": experiment_id,
                    "reason_code": "BUDGET_DEFERRED",
                }],
            },
        )

    main = executor.consume_continuation_execution_receipts(
        campaign_id,
        allowed_experiment_ids_by_obligation={oid: "exp-main"},
        close_capture=True,
    )
    expansion = executor.consume_continuation_execution_receipts(
        campaign_id,
        allowed_experiment_ids_by_obligation={oid: "exp-expansion"},
        close_capture=True,
    )

    assert [row["experiment_id"] for row in main] == ["exp-main"]
    assert [row["experiment_id"] for row in expansion] == ["exp-expansion"]
    executor.clear_continuation_retry_receipts(campaign_id)
