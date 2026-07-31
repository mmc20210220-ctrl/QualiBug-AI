from __future__ import annotations

from ai_test_asset_center import assertion_dsl_base
from ai_test_asset_center.formal_event_surface import (
    ASSERTION_KIND,
    EVIDENCE_KEY,
    install_formal_event_surface,
)
from ai_test_asset_center.formal_event_verdict_reason_bridge import (
    classify_event_delivery_violation,
    install_formal_event_verdict_reason_bridge,
)


def _observation(**overrides: object) -> dict:
    row = {
        "expected_event_type": "OrderCreated",
        "expected_min_count": 1,
        "expected_max_count": 1,
        "observed_total_count": 1,
        "observed_correlated_count": 1,
        "observed_event_types": ["OrderCreated"],
        "mismatched_event_types": [],
        "observation_window_completed": True,
        "coverage_complete": True,
    }
    row.update(overrides)
    return row


def test_event_violation_classifier_distinguishes_missing_duplicate_type_and_correlation() -> None:
    assert classify_event_delivery_violation(
        _observation(observed_total_count=0, observed_correlated_count=0)
    ) == ["EVENT_DELIVERY_COUNT_BELOW_MINIMUM"]
    assert classify_event_delivery_violation(
        _observation(observed_total_count=2, observed_correlated_count=2)
    ) == ["EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM"]
    assert classify_event_delivery_violation(
        _observation(
            observed_event_types=["OrderRejected"],
            mismatched_event_types=["OrderRejected"],
        )
    ) == ["EVENT_DELIVERY_TYPE_MISMATCH"]
    assert classify_event_delivery_violation(
        _observation(observed_total_count=1, observed_correlated_count=0)
    ) == [
        "EVENT_DELIVERY_CORRELATION_MISMATCH",
        "EVENT_DELIVERY_COUNT_BELOW_MINIMUM",
    ]


def test_registered_event_evaluator_emits_stable_primary_reason() -> None:
    install_formal_event_surface()
    install_formal_event_verdict_reason_bridge()
    evaluator = assertion_dsl_base._REGISTERED_ASSERTION_EVALUATORS[
        ASSERTION_KIND
    ]

    result = evaluator(
        {
            "observations": {
                EVIDENCE_KEY: _observation(
                    observed_total_count=1,
                    observed_correlated_count=0,
                )
            }
        }
    )

    assert result["passed"] is False
    assert result["reason_code"] == "EVENT_DELIVERY_CORRELATION_MISMATCH"
    assert result["reason_codes"] == [
        "EVENT_DELIVERY_CORRELATION_MISMATCH",
        "EVENT_DELIVERY_COUNT_BELOW_MINIMUM",
    ]


def test_incomplete_event_coverage_remains_indeterminate() -> None:
    install_formal_event_surface()
    install_formal_event_verdict_reason_bridge()
    evaluator = assertion_dsl_base._REGISTERED_ASSERTION_EVALUATORS[
        ASSERTION_KIND
    ]

    result = evaluator(
        {
            "observations": {
                EVIDENCE_KEY: _observation(
                    observation_window_completed=False,
                    coverage_complete=False,
                )
            }
        }
    )

    assert result["passed"] is None
    assert result["reason_code"] == "EVENT_OBSERVATION_COVERAGE_INCOMPLETE"
    assert "reason_codes" not in result
