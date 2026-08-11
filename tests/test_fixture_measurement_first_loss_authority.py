from __future__ import annotations

from ai_test_asset_center.fixture_measurement_finalizer_authority import (
    fixture_measurement_finalizer_hook,
    fixture_measurement_first_loss_receipt,
)


def _blocked_exp() -> dict:
    return {
        "experiment_id": "exp_1",
        "execution_flow_data_materialization_blocked": {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_FLOW_DATA_MATERIALIZATION_INCOMPLETE",
            "detail": "missing_targets:userId",
        },
    }


def test_fixture_measurement_receipt_preserves_exact_sealed_reason() -> None:
    receipt = fixture_measurement_first_loss_receipt(
        _blocked_exp(),
        steps_out=[],
        cleanup_failures=0,
    )
    assert receipt["status"] == "BLOCKED"
    assert receipt["phase"] == "flow_data_materialization"
    assert receipt["reason_code"] == (
        "BLOCKED_FLOW_DATA_MATERIALIZATION_INCOMPLETE"
    )
    assert receipt["measured_business_transport_attempted"] is False
    assert receipt["premeasurement_target_activity_observed"] is False
    assert receipt["harness_failure_claimed"] is False


def test_finalizer_hook_injects_typed_pretransport_reason_and_restores_exact_code() -> None:
    seen: dict = {}

    def next_call(args, kwargs):
        seen.update(kwargs)
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_BINDING",
            "execution_receipt": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
            },
        }

    observations: dict = {}
    result = fixture_measurement_finalizer_hook(
        next_call,
        (),
        {
            "exp": _blocked_exp(),
            "steps_out": [],
            "observations": observations,
            "pre_transport_block_reasons": [],
            "cleanup_failures": 0,
        },
    )
    assert seen["pre_transport_block_reasons"] == [
        "BLOCKED_FLOW_DATA_MATERIALIZATION_INCOMPLETE:missing_targets:userId"
    ]
    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == (
        "BLOCKED_FLOW_DATA_MATERIALIZATION_INCOMPLETE"
    )
    assert result["execution_receipt"]["reason_code"] == (
        "BLOCKED_FLOW_DATA_MATERIALIZATION_INCOMPLETE"
    )
    assert observations["fixture_measurement_first_loss_receipt"]["status"] == (
        "BLOCKED"
    )


def test_cleanup_failure_keeps_priority_over_fixture_measurement_block() -> None:
    seen: dict = {}

    def next_call(args, kwargs):
        seen.update(kwargs)
        return {
            "status": "HARNESS_FAILURE",
            "reason_code": "HARNESS_CLEANUP_EQUIVALENCE_FAILED",
            "execution_receipt": {
                "status": "HARNESS_FAILURE",
                "reason_code": "HARNESS_CLEANUP_EQUIVALENCE_FAILED",
            },
        }

    result = fixture_measurement_finalizer_hook(
        next_call,
        (),
        {
            "exp": _blocked_exp(),
            "steps_out": [{"status_code": 200, "phase": "fixture"}],
            "observations": {},
            "pre_transport_block_reasons": [],
            "cleanup_failures": 1,
        },
    )
    assert seen["pre_transport_block_reasons"] == []
    assert result["reason_code"] == "HARNESS_CLEANUP_EQUIVALENCE_FAILED"
    receipt = result["fixture_measurement_first_loss_receipt"]
    assert receipt["cleanup_failure_has_priority"] is True
    assert receipt["premeasurement_target_activity_observed"] is True
