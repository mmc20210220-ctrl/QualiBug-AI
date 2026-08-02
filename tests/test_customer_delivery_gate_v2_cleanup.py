from __future__ import annotations

import pytest

import ai_test_asset_center.customer_delivery_gate_v2 as delivery_gate_v2
from ai_test_asset_center import _customer_delivery_gate_v2_mechanics as gate_mechanics
from ai_test_asset_center.customer_delivery_gate_v2 import (
    DeliveryGateV2Error,
    _cleanup_gate_decision,
    _oracle_harness_reason_detail,
    _reproduction_decision,
    _validate_active_chain,
    validate_customer_delivery_gate_receipt_v2,
)
from ai_test_asset_center.assertion_dsl_base import _assertion_receipt


def _synthetic_harness_gate(*, reason_detail: str = "") -> dict:
    identity = {
        "run_id": "run-1",
        "campaign_id": "campaign-1",
        "target_id": "target-1",
        "environment_id": "environment-1",
        "mainline_contract_fingerprint": "contract-1",
        "candidate_id": "candidate-1",
        "slice_id": "slice-1",
        "obligation_id": "obligation-1",
        "experiment_id": "experiment-1",
        "execution_id": "execution-1",
        "evidence_id": "evidence-1",
        "finding_id": "",
    }
    receipt_refs = {
        "execution": {"receipt_id": "execution-1", "fingerprint": "e" * 64},
        "actors": [],
        "fixtures": [],
        "controls": [],
        "treatments": [],
        "observers": [],
        "assertions": [],
        "oracle": {"receipt_id": "oracle-1", "fingerprint": "o" * 64},
        "reproduction": {"receipt_id": "reproduction-1", "fingerprint": "r" * 64},
        "cleanup": [],
        "lineage": {"receipt_id": "lineage-1", "fingerprint": "l" * 64},
    }
    payload = {
        "schema_version": "qualibug.customer-delivery-gate-receipt.v2",
        "status": "HARNESS_FAILED",
        "reason_code": "CONTRACT_ORACLE_HARNESS_FAILED",
        "reason_codes": ["CONTRACT_ORACLE_HARNESS_FAILED"],
        "identity": identity,
        "finding_payload_fingerprint": "",
        "receipt_refs": receipt_refs,
        "adjudication": {
            "execution": "EXECUTED",
            "activation": "HARNESS_FAILED",
            "assertion": "PASS",
            "oracle": "HARNESS_FAILED",
            "reproduction": "NOT_REPRODUCED",
            "cleanup": "FAILED",
            "lineage": "CONSISTENT",
        },
        "cost_coverage_status": "UNKNOWN",
        "input_fingerprint": gate_mechanics._fingerprint({
            "identity": identity,
            "finding_payload_fingerprint": "",
            "receipt_refs": receipt_refs,
        }),
    }
    if reason_detail:
        payload["reason_detail"] = reason_detail
    return gate_mechanics._seal(
        payload,
        prefix="gate_",
        id_field="gate_receipt_id",
        fingerprint_field="output_fingerprint",
    )


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


def test_harness_reason_detail_is_optional_and_old_bundle_stays_valid(
    monkeypatch,
) -> None:
    enriched = _synthetic_harness_gate(
        reason_detail="CLEANUP_RECEIPT_FAILED:cleanup:cleanup-1"
    )
    assert validate_customer_delivery_gate_receipt_v2(enriched) == enriched

    legacy = _synthetic_harness_gate()
    assert validate_customer_delivery_gate_receipt_v2(legacy) == legacy

    monkeypatch.setattr(
        delivery_gate_v2,
        "build_customer_delivery_gate_receipt_v2",
        lambda **_: enriched,
    )
    assert delivery_gate_v2.validate_customer_delivery_gate_bundle(
        legacy,
        finding=None,
        execution_receipt={},
        contract_evidence_receipts=[],
        observer_receipts=[],
        oracle_receipt={},
        reproduction_receipt={},
    ) == legacy


def test_harness_reason_detail_cannot_appear_on_another_gate_status() -> None:
    gate = _synthetic_harness_gate(reason_detail="diagnostic")
    payload = {
        key: value
        for key, value in gate.items()
        if key not in {"gate_receipt_id", "output_fingerprint"}
    }
    payload["status"] = "BLOCKED"
    blocked = gate_mechanics._seal(
        payload,
        prefix="gate_",
        id_field="gate_receipt_id",
        fingerprint_field="output_fingerprint",
    )
    with pytest.raises(
        DeliveryGateV2Error,
        match="delivery_gate_reason_detail_status_invalid",
    ):
        validate_customer_delivery_gate_receipt_v2(blocked)


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


def test_state_unchanged_cleanup_subjects_must_not_double_count_coverage() -> None:
    """Control+treatment NOT_REQUIRED receipts must not each stamp full write N.

    Live observed runs hit CLEANUP_WRITE_COVERAGE_MISMATCH when both subjects
    carried accepted_write_count=2 against accepted_non_cleanup=2 (covered=4).
    """
    decision = _cleanup_gate_decision(
        execution=_execution(
            accepted_writes=2,
            cleanup_status="NOT_REQUIRED",
            attempted=0,
            completed=0,
        ),
        contracts=[
            _cleanup_contract(accepted_write_count=2, status="NOT_REQUIRED"),
            _cleanup_contract(accepted_write_count=0, status="NOT_REQUIRED"),
        ],
    )

    assert decision == ("DELIVERABLE", [], "NOT_REQUIRED")

    # Same cardinality once + fixture cleanup covering the remaining write.
    mixed = _cleanup_gate_decision(
        execution=_execution(
            accepted_writes=3,
            cleanup_status="COMPLETED",
            attempted=1,
            completed=1,
        ),
        contracts=[
            _cleanup_contract(accepted_write_count=2, status="NOT_REQUIRED"),
            _cleanup_contract(accepted_write_count=0, status="NOT_REQUIRED"),
            _cleanup_contract(accepted_write_count=1, status="COMPLETED"),
        ],
    )
    assert mixed == ("DELIVERABLE", [], "COMPLETED")


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
