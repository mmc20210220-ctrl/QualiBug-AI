from __future__ import annotations


def test_zero_transport_governance_block_becomes_pretransport_binding_loss() -> None:
    from ai_test_asset_center.experiment_plan_lifecycle_adapter import (
        _seal_pre_transport_request_blocks,
    )

    result, receipt = _seal_pre_transport_request_blocks(
        {
            "steps": [
                {
                    "phase": "treatment",
                    "step_id": "treatment_1",
                    "operation_ref": "op-1",
                    "actor_ref": "actor-2",
                    "method": "POST",
                    "path": "/api/orders/{orderId}",
                    "status_code": 0,
                    "governance_receipt": {
                        "status": "blocked",
                        "reason": "governed_control_write_path_placeholder_unresolved",
                        "write_request_attempt_count": 0,
                        "before": {},
                        "write": {
                            "status": 0,
                            "error": "governed_control_write_path_placeholder_unresolved",
                        },
                    },
                }
            ],
            "pre_transport_block_reasons": [],
        }
    )

    assert result["pre_transport_block_reasons"] == [
        "governed_control_write_path_placeholder_unresolved"
    ]
    assert receipt["status"] == "BLOCKED"
    assert receipt["row_count"] == 1
    assert receipt["rows"][0]["category"] == (
        "BINDING_OR_REQUEST_MATERIALIZATION"
    )
    assert receipt["rows"][0]["request_reached_transport"] is False
    assert receipt["harness_failure_claimed"] is False


def test_explicit_skipped_request_build_reason_is_preserved() -> None:
    from ai_test_asset_center.experiment_plan_lifecycle_adapter import (
        _seal_pre_transport_request_blocks,
    )

    result, receipt = _seal_pre_transport_request_blocks(
        {
            "steps": [
                {
                    "phase": "control",
                    "subject_id": "control_1",
                    "method": "POST",
                    "path": "/api/orders",
                    "status": "blocked_write",
                    "status_code": 0,
                    "skipped_reason": "BLOCKED_MISSING_REQUIRED_BODY_FIELDS:sku",
                }
            ],
            "pre_transport_block_reasons": [],
        }
    )

    assert result["pre_transport_block_reasons"] == [
        "BLOCKED_MISSING_REQUIRED_BODY_FIELDS:sku"
    ]
    assert receipt["by_category"] == {
        "BINDING_OR_REQUEST_MATERIALIZATION": 1
    }


def test_connection_failure_is_not_relabelled_as_pretransport_block() -> None:
    from ai_test_asset_center.experiment_plan_lifecycle_adapter import (
        _seal_pre_transport_request_blocks,
    )

    result, receipt = _seal_pre_transport_request_blocks(
        {
            "steps": [
                {
                    "phase": "control",
                    "step_id": "control_1",
                    "status_code": 0,
                    "governance_receipt": {
                        "status": "failed",
                        "reason": "connection reset by peer",
                        "write_request_attempt_count": 0,
                        "write": {
                            "status": 0,
                            "error": "connection reset by peer",
                        },
                    },
                }
            ],
            "pre_transport_block_reasons": [],
        }
    )

    assert result["pre_transport_block_reasons"] == []
    assert receipt["status"] == "NOT_APPLICABLE"
    assert receipt["row_count"] == 0
