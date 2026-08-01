from __future__ import annotations

import pytest

from ai_test_asset_center.customer_delivery_gate_v2 import (
    _cleanup_gate_decision,
    _oracle_harness_reason_detail,
    _reproduction_decision,
    _validate_active_chain,
)
from ai_test_asset_center.assertion_dsl_base import _assertion_receipt


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


def test_oracle_harness_detail_preserves_activation_failure_reasons() -> None:
    detail = _oracle_harness_reason_detail(
        {
            "status": "HARNESS_FAILED",
            "reason_codes": ["ORACLE_REASON"],
            "activation_receipt": {
                "reason_codes": [
                    "CLEANUP_RECEIPT_FAILED:cleanup:cleanup-1",
                    "ORACLE_REASON",
                ],
            },
        }
    )

    assert detail == "ORACLE_REASON,CLEANUP_RECEIPT_FAILED:cleanup:cleanup-1"


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


def test_treatment_only_violation_is_reproduced_when_control_is_not_required() -> None:
    reproduced, reason = _reproduction_decision(
        oracle={
            "status": "VIOLATION",
            "activation_receipt": {
                "required": {"control": [], "treatment": ["treatment-1"]},
            },
        },
        observed_phases={"treatment"},
        observation_count=1,
    )

    assert reproduced is True
    assert reason == ""


def test_missing_required_control_is_not_reported_as_oracle_not_violated() -> None:
    reproduced, reason = _reproduction_decision(
        oracle={
            "status": "VIOLATION",
            "activation_receipt": {
                "required": {
                    "control": ["control-1"],
                    "treatment": ["treatment-1"],
                },
            },
        },
        observed_phases={"treatment"},
        observation_count=1,
    )

    assert reproduced is False
    assert reason == "REPRODUCTION_CONTROL_MISSING"


def test_gate_blocks_actor_sensitive_violation_when_activation_omits_control() -> None:
    assertion = _assertion_receipt(
        assertion_id="assert-authorization",
        kind="owner_tenant_visibility",
        status="VIOLATION",
        reason_code="VISIBILITY_VIOLATION",
        expected=False,
        actual=True,
        error="",
        observer_receipt_ids=[],
        source_refs=[{"kind": "api", "locator": "GET /resources/{id}"}],
        harness_error=False,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )

    decision = _validate_active_chain(
        execution={"observation_receipt_ids": []},
        contracts=[],
        observers=[],
        oracle={
            "status": "VIOLATION",
            "assertions": [assertion],
            "activation_receipt": {
                "status": "ACTIVE",
                "required": {"control": []},
            },
        },
        reproduction={"status": "REPRODUCED", "step_observations": []},
    )

    assert decision == ("BLOCKED", ["ACTOR_SENSITIVE_CONTROL_MISSING"])


@pytest.mark.parametrize(
    ("oracle_status", "expected_reason"),
    [
        ("PROPERTY_HELD", "ORACLE_NOT_VIOLATED"),
        ("BLOCKED", "CONTRACT_ORACLE_BLOCKED"),
        ("HARNESS_FAILED", "CONTRACT_ORACLE_HARNESS_FAILED"),
        ("INDETERMINATE", "ASSERTION_INDETERMINATE"),
    ],
)
def test_reproduction_preserves_non_violation_oracle_reason(
    oracle_status: str,
    expected_reason: str,
) -> None:
    reproduced, reason = _reproduction_decision(
        oracle={"status": oracle_status},
        observed_phases={"treatment"},
        observation_count=1,
    )

    assert reproduced is False
    assert reason == expected_reason
