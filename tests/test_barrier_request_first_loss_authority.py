from __future__ import annotations

from ai_test_asset_center.experiment_barrier_request_authority import (
    seal_barrier_request_first_loss,
)


def _zero_transport_step(reason: str) -> dict:
    return {
        "phase": "treatment",
        "step_id": "treatment_1",
        "operation_ref": "op_1",
        "actor_ref": "actor_1",
        "method": "POST",
        "path": "/resource",
        "status_code": 0,
        "error": reason,
        "governance_receipt": {
            "status": "blocked",
            "reason": reason,
            "write_request_attempt_count": 0,
            "write": {
                "status": 0,
                "error": reason,
            },
            "before": {},
        },
    }


def test_barrier_zero_transport_reason_becomes_pre_transport_block() -> None:
    observations: dict = {}
    result = seal_barrier_request_first_loss(
        {
            "steps": [
                _zero_transport_step(
                    "governed_control_write_path_placeholder_unresolved"
                )
            ],
            "pre_transport_block_reasons": [],
        },
        observations=observations,
    )
    assert result["pre_transport_block_reasons"] == [
        "governed_control_write_path_placeholder_unresolved"
    ]
    receipt = result["barrier_request_build_first_loss_receipt"]
    assert receipt["status"] == "BLOCKED"
    assert receipt["row_count"] == 1
    assert receipt["transport_attempted"] is False
    assert receipt["harness_failure_claimed"] is False
    assert observations["barrier_request_build_first_loss_receipt"] == receipt


def test_barrier_transport_error_remains_harness_eligible() -> None:
    result = seal_barrier_request_first_loss(
        {
            "steps": [_zero_transport_step("connection timeout")],
            "pre_transport_block_reasons": [],
        }
    )
    assert result["pre_transport_block_reasons"] == []
    assert "barrier_request_build_first_loss_receipt" not in result


def test_existing_barrier_pretransport_reason_is_preserved_once() -> None:
    reason = "BLOCKED_MISSING_OBSERVER"
    result = seal_barrier_request_first_loss(
        {
            "steps": [
                {
                    "phase": "control",
                    "step_id": "control_1",
                    "method": "POST",
                    "path": "/resource",
                    "status": "blocked_write",
                    "status_code": 0,
                    "reason": reason,
                }
            ],
            "pre_transport_block_reasons": [reason],
        }
    )
    assert result["pre_transport_block_reasons"] == [reason]
    assert result["barrier_request_build_first_loss_receipt"]["row_count"] == 1
