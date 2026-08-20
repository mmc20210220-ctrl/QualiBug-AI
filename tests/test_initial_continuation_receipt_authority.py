"""Initial execution receipts are resume authority and must never be clipped."""
from __future__ import annotations


def _selected(prefix: str, count: int) -> list[dict]:
    return [
        {
            "obligation_id": f"{prefix}-{index:04d}",
            "experiment_id": f"exp-{prefix}-{index:04d}",
        }
        for index in range(count)
    ]


def _deferred(rows: list[dict]) -> list[dict]:
    return [
        {
            "obligation_id": row["obligation_id"],
            "experiment_id": row["experiment_id"],
            "reason_code": "BUDGET_DEFERRED",
        }
        for row in rows
    ]


def test_initial_receipt_authority_preserves_more_than_4000_identities() -> None:
    import ai_test_asset_center.experiment_executor as executor

    campaign_id = "campaign-initial-receipts-over-4000"
    executor.clear_continuation_retry_receipts(campaign_id)
    rows = _selected("all", 4107)

    executor._capture_continuation_execution_receipts(
        campaign_id=campaign_id,
        selected_rows=rows,
        batch={"results": [], "budget_deferred": _deferred(rows)},
    )
    consumed = executor.consume_continuation_execution_receipts(
        campaign_id,
        allowed_experiment_ids_by_obligation={
            row["obligation_id"]: row["experiment_id"] for row in rows
        },
        close_capture=True,
    )

    assert len(consumed) == 4107
    assert consumed[0]["obligation_id"] == rows[0]["obligation_id"]
    assert consumed[-1]["obligation_id"] == rows[-1]["obligation_id"]
    assert all(row["receipt_kind"] == "BUDGET_DEFERRED" for row in consumed)
    executor.clear_continuation_retry_receipts(campaign_id)


def test_domain_consumption_keeps_other_initial_domain_after_capture_close() -> None:
    import ai_test_asset_center.experiment_executor as executor

    campaign_id = "campaign-initial-receipts-multi-domain"
    executor.clear_continuation_retry_receipts(campaign_id)
    main_rows = _selected("main", 2500)
    expansion_rows = _selected("expansion", 2500)

    executor._capture_continuation_execution_receipts(
        campaign_id=campaign_id,
        selected_rows=main_rows,
        batch={"results": [], "budget_deferred": _deferred(main_rows)},
    )
    executor._capture_continuation_execution_receipts(
        campaign_id=campaign_id,
        selected_rows=expansion_rows,
        batch={"results": [], "budget_deferred": _deferred(expansion_rows)},
    )

    main = executor.consume_continuation_execution_receipts(
        campaign_id,
        allowed_experiment_ids_by_obligation={
            row["obligation_id"]: row["experiment_id"] for row in main_rows
        },
        close_capture=True,
    )
    expansion = executor.consume_continuation_execution_receipts(
        campaign_id,
        allowed_experiment_ids_by_obligation={
            row["obligation_id"]: row["experiment_id"] for row in expansion_rows
        },
        close_capture=True,
    )

    assert len(main) == 2500
    assert len(expansion) == 2500
    assert {row["obligation_id"] for row in main}.isdisjoint(
        {row["obligation_id"] for row in expansion}
    )
    executor.clear_continuation_retry_receipts(campaign_id)
