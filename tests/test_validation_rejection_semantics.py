"""Regression: validation-rejection assertions classify rejection shape by the
target's own transport semantics, not by a single 4xx expectation.

E2E finding (2026-08-07, benchmark run 3): 24 of 25 validation_rejection
deliveries were false positives because every non-4xx answer counted as a
"validation not enforced" violation:
- 2xx with an explicit business rejection (success:false / reject tokens) is
  the target DECLARING the validation enforced — the malformed write was
  refused at the business layer. Reporting it as a violation marks clean
  systems defective.
- 2xx with a real effect is a genuine validation miss (violation).
- 2xx with zero effect and no rejection signal is ambiguous — silent
  rejection vs silent swallow (indeterminate, never a fabricated violation).
- 5xx refuses the input through the server-error path — not a validation
  miss, not a clean pass (indeterminate).
"""
from __future__ import annotations

from ai_test_asset_center.assertion_dsl_validation_base import evaluate_assertion


def _spec() -> dict:
    return {
        "kind": "validation_rejection",
        "expected_class": 4,
        "expected_effect_count": 0,
        "assertion_id": "assert_validation",
        "business_effect_requirement": "NOT_APPLICABLE",
    }


def _pass_obs(status_code: int) -> dict:
    """4xx/observed-rejection observations with the NOT_APPLICABLE effect
    contract the compiled assertion declares for status-only probes."""
    return {
        "status_code": status_code,
        "treatment_effect_count": 0,
        "business_effect_not_applicable": True,
    }


def test_2xx_with_business_rejection_is_pass() -> None:
    """success:false declares the validation enforced — never a violation."""
    result = evaluate_assertion(_spec(), observations={
        "status_code": 200,
        "treatment_effect_count": 0,
        "business_rejected": True,
        "business_outcome": {"business_rejected": True},
    })
    assert result["status"] == "PASS"
    assert result["reason_code"] == "VALIDATION_BUSINESS_REJECTED"


def test_2xx_with_real_effect_is_violation() -> None:
    """Malformed input accepted with a real effect: the gate is missing."""
    result = evaluate_assertion(_spec(), observations={
        "status_code": 200,
        "treatment_effect_count": 1,
    })
    assert result["status"] == "VIOLATION"
    assert result["reason_code"] == "VALIDATION_REJECTION_NOT_ENFORCED"


def test_2xx_zero_effect_without_rejection_signal_is_indeterminate() -> None:
    result = evaluate_assertion(_spec(), observations={
        "status_code": 201,
        "treatment_effect_count": 0,
        "zero_effect_on_accepted_write": True,
    })
    assert result["status"] == "INDETERMINATE"
    assert result["reason_code"] == "VALIDATION_EFFECT_AMBIGUOUS"


def test_5xx_response_is_indeterminate() -> None:
    """A 5xx refuses the input via the server-error path — not a clean 4xx
    rejection, not an accepted write."""
    result = evaluate_assertion(_spec(), observations={
        "status_code": 500,
        "treatment_effect_count": 0,
    })
    assert result["status"] == "INDETERMINATE"
    assert result["reason_code"] == "VALIDATION_SERVER_ERROR_RESPONSE"


def test_4xx_rejection_is_pass() -> None:
    result = evaluate_assertion(_spec(), observations=_pass_obs(400))
    assert result["status"] == "PASS"


def test_business_rejection_takes_priority_over_zero_effect() -> None:
    """A write that is both explicitly business-rejected and zero-effect is a
    rejection (PASS), not an ambiguous zero-effect."""
    result = evaluate_assertion(_spec(), observations={
        "status_code": 200,
        "treatment_effect_count": 0,
        "business_rejected": True,
        "zero_effect_on_accepted_write": True,
    })
    assert result["status"] == "PASS"
    assert result["reason_code"] == "VALIDATION_BUSINESS_REJECTED"
