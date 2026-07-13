from __future__ import annotations

import pytest


def _governed(*, accepted: bool, http_attempts: int = 3) -> dict:
    return {
        "status": "executed" if accepted else "failed",
        "accepted": accepted,
        "http_attempt_count": http_attempts,
        "production_http_requests": 0,
        "audit_record": {"operation_accepted": accepted},
    }


def test_execution_operational_receipt_counts_only_explicit_request_receipts() -> None:
    from ai_test_asset_center.operational_receipts import (
        build_execution_operational_receipt,
    )

    receipt = build_execution_operational_receipt(
        receipt_id="operational-1",
        execution_status="EXECUTED",
        steps=[
            {"phase": "control", "method": "GET", "path": "/resources", "status_code": 200},
            {
                "phase": "treatment",
                "method": "POST",
                "path": "/resources",
                "status_code": 201,
                "governance_receipt": _governed(accepted=True),
            },
            {
                "phase": "cleanup",
                "method": "DELETE",
                "path": "/resources/1",
                "status_code": 204,
                "governance_receipt": _governed(accepted=True),
            },
            {
                "phase": "treatment",
                "status": "blocked_write",
                "method": "POST",
                "path": "/forbidden",
            },
        ],
        cleanup_failures=0,
    )

    assert receipt["scenario_attempt_count"] == 1
    assert receipt["http_request_attempt_count"] == 7
    assert receipt["production_http_request_count"] == 0
    assert receipt["accepted_write_count"] == 2
    assert receipt["accepted_non_cleanup_write_count"] == 1
    assert receipt["accepted_cleanup_write_count"] == 1
    assert receipt["cleanup_outcome"] == {
        "status": "COMPLETED",
        "attempted_count": 1,
        "completed_count": 1,
        "failure_count": 0,
    }


def test_operational_receipt_does_not_infer_cleanup_from_arbitrary_nested_json() -> None:
    from ai_test_asset_center.operational_receipts import (
        build_execution_operational_receipt,
    )

    receipt = build_execution_operational_receipt(
        receipt_id="operational-2",
        execution_status="BLOCKED",
        steps=[
            {
                "status": "blocked_write",
                "nested": {"cleanup": {"status": "failed"}},
            }
        ],
        cleanup_failures=0,
    )

    assert receipt["http_request_attempt_count"] == 0
    assert receipt["cleanup_outcome"]["status"] == "NOT_REQUIRED"
    assert receipt["cleanup_outcome"]["failure_count"] == 0


def test_aggregate_operational_receipts_preserves_terminal_cleanup_truth() -> None:
    from ai_test_asset_center.operational_receipts import (
        aggregate_execution_operational_receipts,
        build_execution_operational_receipt,
    )

    first = build_execution_operational_receipt(
        receipt_id="operational-1",
        execution_status="EXECUTED",
        steps=[{"method": "GET", "path": "/one", "status_code": 200}],
        cleanup_failures=0,
    )
    second = build_execution_operational_receipt(
        receipt_id="operational-2",
        execution_status="HARNESS_FAILED",
        steps=[
            {
                "phase": "treatment",
                "method": "POST",
                "path": "/two",
                "status_code": 201,
                "governance_receipt": _governed(accepted=True),
            }
        ],
        cleanup_failures=1,
    )

    aggregate = aggregate_execution_operational_receipts([first, second])

    assert aggregate["scenario_attempts"] == 2
    assert aggregate["executed_scenarios"] == 1
    assert aggregate["observed_http_request_count"] == 4
    assert aggregate["accepted_write_count"] == 1
    assert aggregate["cleanup_failures"] == 1
    assert aggregate["execution_success_rate"] == 0.5


def test_operational_receipt_requires_content_fingerprint() -> None:
    from ai_test_asset_center.operational_receipts import (
        OperationalReceiptError,
        validate_execution_operational_receipt,
    )

    with pytest.raises(
        OperationalReceiptError,
        match="operational_receipt_fingerprint_missing",
    ):
        validate_execution_operational_receipt({
            "schema_version": "qualibug.execution-operational-receipt.v1",
            "receipt_id": "operational-unsigned",
            "execution_status": "EXECUTED",
            "scenario_attempt_count": 1,
            "http_request_attempt_count": 1,
            "production_http_request_count": 0,
            "accepted_write_count": 0,
            "accepted_non_cleanup_write_count": 0,
            "accepted_cleanup_write_count": 0,
            "cleanup_outcome": {
                "status": "NOT_REQUIRED",
                "attempted_count": 0,
                "completed_count": 0,
                "failure_count": 0,
            },
        })
