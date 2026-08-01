"""Regression: delivery gate must not crash on supplementary observer receipts.

The runtime may deliver observer receipts beyond the activation contract
(authorization_comparison, redundant effect observers injected by the
compiler). ``_validate_active_chain`` previously required exact set equality
between the activation's verified observer receipts and EVERY delivered
observer receipt, so a legitimate supplementary observer crashed the whole
scan with ``delivery_observer_activation_reference_mismatch``. The check is
scoped to the observers the activation actually required, mirroring the
subject-scoped contract checks; a missing required observer still blocks.
"""
from __future__ import annotations

import pytest

from ai_test_asset_center._customer_delivery_gate_v2_mechanics import (
    DeliveryGateV2Error,
    _validate_active_chain,
)
from ai_test_asset_center.assertion_dsl_base import _assertion_receipt


def _violation_assertion() -> dict:
    return _assertion_receipt(
        assertion_id="assert-http-status",
        kind="http_status_class",
        status="VIOLATION",
        reason_code="ACCESS_GRANTED",
        expected="denied",
        actual="allowed",
        error="",
        observer_receipt_ids=["obs_required_1"],
        source_refs=[{"kind": "api", "locator": "GET /v1/resources"}],
        harness_error=False,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )


def _activation(
    *,
    observer_verified: list[str],
    required_observers: list[str] | None = None,
) -> dict:
    empty: dict[str, list[str]] = {
        "control": [],
        "treatment": [],
        "actor": [],
        "fixture": [],
        "cleanup": [],
    }
    return {
        "status": "ACTIVE",
        "required": {
            **empty,
            "observer": required_observers or ["obs_required"],
        },
        "verified_receipt_ids": {**empty, "observer": observer_verified},
    }


def _observer(observer_id: str, receipt_id: str) -> dict:
    return {
        "observer_id": observer_id,
        "receipt_id": receipt_id,
        "status": "OBSERVED",
    }


def test_supplementary_observer_does_not_crash_gate() -> None:
    decision = _validate_active_chain(
        execution={
            "observation_receipt_ids": ["obs_required_1", "obs_supplementary_1"],
            "accepted_non_cleanup_write_count": 0,
            "operational_receipt": {
                "cleanup_outcome": {
                    "status": "NOT_REQUIRED",
                    "attempted_count": 0,
                    "completed_count": 0,
                    "failure_count": 0,
                }
            },
        },
        contracts=[],
        observers=[
            _observer("obs_required", "obs_required_1"),
            _observer("authorization_comparison", "obs_supplementary_1"),
        ],
        oracle={
            "status": "VIOLATION",
            "assertions": [_violation_assertion()],
            "activation_receipt": _activation(observer_verified=["obs_required_1"]),
        },
        reproduction={"status": "REPRODUCED", "step_observations": []},
    )

    assert decision == ("DELIVERABLE", [])


def test_missing_required_observer_still_fails_closed() -> None:
    with pytest.raises(DeliveryGateV2Error) as excinfo:
        _validate_active_chain(
            execution={
                "observation_receipt_ids": ["obs_supplementary_1"],
                "accepted_non_cleanup_write_count": 0,
                "operational_receipt": {
                    "cleanup_outcome": {
                        "status": "NOT_REQUIRED",
                        "attempted_count": 0,
                        "completed_count": 0,
                        "failure_count": 0,
                    }
                },
            },
            contracts=[],
            observers=[
                _observer("authorization_comparison", "obs_supplementary_1"),
            ],
            oracle={
                "status": "VIOLATION",
                "assertions": [_violation_assertion()],
                "activation_receipt": _activation(observer_verified=["obs_required_1"]),
            },
            reproduction={"status": "REPRODUCED", "step_observations": []},
        )
    message = str(excinfo.value)
    assert (
        "delivery_observer_activation_reference_mismatch"
        ":missing_required_observers=['obs_required']" in message
    )


def test_soft_activation_subset_verified_observers_is_accepted() -> None:
    # Soft field-oracle activation: required observers are delivered, but only
    # a subset is verified (the rest are INDETERMINATE traces). Reference
    # integrity holds (every verified receipt exists), so the gate must pass.
    decision = _validate_active_chain(
        execution={
            "observation_receipt_ids": [
                "obs_verified_1",
                "obs_indeterminate_1",
                "obs_supplementary_1",
            ],
            "accepted_non_cleanup_write_count": 0,
            "operational_receipt": {
                "cleanup_outcome": {
                    "status": "NOT_REQUIRED",
                    "attempted_count": 0,
                    "completed_count": 0,
                    "failure_count": 0,
                }
            },
        },
        contracts=[],
        observers=[
            _observer("before_state", "obs_verified_1"),
            _observer("after_state", "obs_indeterminate_1"),
            _observer("authorization_comparison", "obs_supplementary_1"),
        ],
        oracle={
            "status": "VIOLATION",
            "assertions": [_violation_assertion()],
            "activation_receipt": _activation(
                observer_verified=["obs_verified_1"],
                required_observers=["before_state", "after_state"],
            ),
        },
        reproduction={"status": "REPRODUCED", "step_observations": []},
    )

    assert decision == ("DELIVERABLE", [])


def test_wrong_required_observer_receipt_still_fails_closed() -> None:
    with pytest.raises(DeliveryGateV2Error):
        _validate_active_chain(
            execution={
                "observation_receipt_ids": ["obs_wrong_1"],
                "accepted_non_cleanup_write_count": 0,
                "operational_receipt": {
                    "cleanup_outcome": {
                        "status": "NOT_REQUIRED",
                        "attempted_count": 0,
                        "completed_count": 0,
                        "failure_count": 0,
                    }
                },
            },
            contracts=[],
            observers=[
                _observer("obs_required", "obs_wrong_1"),
            ],
            oracle={
                "status": "VIOLATION",
                "assertions": [_violation_assertion()],
                "activation_receipt": _activation(observer_verified=["obs_required_1"]),
            },
            reproduction={"status": "REPRODUCED", "step_observations": []},
        )
