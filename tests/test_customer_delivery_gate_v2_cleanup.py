from __future__ import annotations

from ai_test_asset_center.customer_delivery_gate_v2 import _cleanup_gate_decision


def _execution(
    *,
    accepted_writes: int,
    cleanup_status: str,
    attempted: int = 0,
    completed: int = 0,
    failures: int = 0,
) -> dict:
    return {
        "accepted_non_cleanup_write_count": accepted_writes,
        "operational_receipt": {
            "accepted_non_cleanup_write_count": accepted_writes,
            "cleanup_outcome": {
                "status": cleanup_status,
                "attempted_count": attempted,
                "completed_count": completed,
                "failure_count": failures,
            },
        },
    }


def _cleanup_contract(
    *,
    accepted_write_count: int,
    status: str = "COMPLETED",
) -> dict:
    return {
        "kind": "cleanup",
        "status": status,
        "evidence": {
            "accepted_write_count": accepted_write_count,
            "cleanup_write_count": 1 if status == "COMPLETED" else 0,
            "restoration_verified": status == "COMPLETED",
            "state_unchanged": status in {"COMPLETED", "NOT_REQUIRED"},
            "audit_receipt_ids": ["audit-cleanup-1"],
        },
    }


def test_cleanup_failure_is_harness_failure() -> None:
    decision = _cleanup_gate_decision(
        execution=_execution(
            accepted_writes=1,
            cleanup_status="FAILED",
            attempted=1,
            failures=1,
        ),
        contracts=[_cleanup_contract(accepted_write_count=0, status="FAILED")],
    )

    assert decision == ("HARNESS_FAILED", ["CLEANUP_COMPENSATION_FAILED"], "FAILED")


def test_cleanup_receipt_must_cover_every_accepted_write() -> None:
    decision = _cleanup_gate_decision(
        execution=_execution(
            accepted_writes=2,
            cleanup_status="COMPLETED",
            attempted=1,
            completed=1,
        ),
        contracts=[_cleanup_contract(accepted_write_count=1)],
    )

    assert decision == (
        "HARNESS_FAILED",
        ["CLEANUP_WRITE_COVERAGE_MISMATCH"],
        "INCOMPLETE",
    )


def test_cleanup_receipt_is_required_after_an_accepted_write() -> None:
    decision = _cleanup_gate_decision(
        execution=_execution(
            accepted_writes=1,
            cleanup_status="COMPLETED",
            attempted=1,
            completed=1,
        ),
        contracts=[],
    )

    assert decision == (
        "HARNESS_FAILED",
        ["CLEANUP_EVIDENCE_INCOMPLETE"],
        "INCOMPLETE",
    )


def test_complete_cleanup_can_pass_delivery_gate() -> None:
    decision = _cleanup_gate_decision(
        execution=_execution(
            accepted_writes=1,
            cleanup_status="COMPLETED",
            attempted=1,
            completed=1,
        ),
        contracts=[_cleanup_contract(accepted_write_count=1)],
    )

    assert decision == ("DELIVERABLE", [], "COMPLETED")


def test_read_only_execution_requires_not_required_cleanup() -> None:
    decision = _cleanup_gate_decision(
        execution=_execution(
            accepted_writes=0,
            cleanup_status="NOT_REQUIRED",
        ),
        contracts=[],
    )

    assert decision == ("DELIVERABLE", [], "NOT_REQUIRED")
