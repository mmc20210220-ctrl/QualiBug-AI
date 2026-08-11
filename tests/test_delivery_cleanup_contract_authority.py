from __future__ import annotations


def test_accepted_write_cannot_self_authorize_cleanup_not_required() -> None:
    from ai_test_asset_center.customer_delivery_gate_v2 import (
        _cleanup_gate_decision,
    )

    status, reasons, adjudication = _cleanup_gate_decision(
        execution={
            "operational_receipt": {
                "accepted_non_cleanup_write_count": 1,
                "cleanup_outcome": {
                    "status": "NOT_REQUIRED",
                    "failure_count": 0,
                },
            }
        },
        contracts=[],
    )

    assert status == "HARNESS_FAILED"
    assert reasons == ["CLEANUP_EVIDENCE_INCOMPLETE"]
    assert adjudication == "INCOMPLETE"


def test_zero_write_execution_may_remain_cleanup_not_required() -> None:
    from ai_test_asset_center.customer_delivery_gate_v2 import (
        _cleanup_gate_decision,
    )

    status, reasons, adjudication = _cleanup_gate_decision(
        execution={
            "operational_receipt": {
                "accepted_non_cleanup_write_count": 0,
                "cleanup_outcome": {
                    "status": "NOT_REQUIRED",
                    "failure_count": 0,
                },
            }
        },
        contracts=[],
    )

    assert status == "DELIVERABLE"
    assert reasons == []
    assert adjudication == "NOT_REQUIRED"


def test_accepted_residue_still_requires_and_uses_typed_cleanup_contract() -> None:
    from ai_test_asset_center.customer_delivery_gate_v2 import (
        _cleanup_gate_decision,
    )

    status, reasons, adjudication = _cleanup_gate_decision(
        execution={
            "operational_receipt": {
                "accepted_non_cleanup_write_count": 1,
                "cleanup_outcome": {
                    "status": "NOT_REQUIRED",
                    "failure_count": 0,
                },
            }
        },
        contracts=[
            {
                "kind": "cleanup",
                "status": "RESIDUE_ACCEPTED",
                "evidence": {
                    "accepted_write_count": 1,
                    "residue": True,
                },
            }
        ],
    )

    assert status == "DELIVERABLE"
    assert reasons == []
    assert adjudication == "RESIDUE_ACCEPTED"
