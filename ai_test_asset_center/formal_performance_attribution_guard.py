"""Keep functional HTTP failures out of latency-budget verdicts.

The first performance surface measures successful read latency only. A 4xx/5xx
sample is evidence of a functional, authorization or availability problem; its
duration cannot be cleanly attributed to the successful business-read path the
source SLO names. Therefore:

* the source contract must declare expected_status_class=2;
* max_error_rate must be zero for this first increment;
* any non-2xx or transport status makes the observer INDETERMINATE;
* no error-rate violation is emitted as a performance finding.
"""
from __future__ import annotations

import copy
from typing import Any

from . import formal_performance_surface as _surface

_INSTALL_MARKER = "_qualibug_performance_attribution_guard_installed"
_ORIGINAL_VALIDATOR = "_qualibug_original_performance_validator"
_ORIGINAL_HANDLER = "_qualibug_original_performance_handler"
_ORIGINAL_ASSERTION = "_qualibug_original_performance_assertion"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def install_formal_performance_attribution_guard() -> None:
    from . import observer_contracts_base as _observers
    from . import assertion_dsl_base as _assertions

    if getattr(_surface, _INSTALL_MARKER, False):
        return

    validator = getattr(
        _surface,
        _ORIGINAL_VALIDATOR,
        _surface._validated_contract,
    )
    handler = getattr(
        _surface,
        _ORIGINAL_HANDLER,
        _surface._performance_observer_handler,
    )
    assertion = getattr(
        _surface,
        _ORIGINAL_ASSERTION,
        _surface._evaluate_latency_budget,
    )
    setattr(_surface, _ORIGINAL_VALIDATOR, validator)
    setattr(_surface, _ORIGINAL_HANDLER, handler)
    setattr(_surface, _ORIGINAL_ASSERTION, assertion)

    def strict_validator(
        contract: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        normalized, reason = validator(contract)
        if normalized is None:
            return None, reason
        if int(normalized.get("expected_status_class") or 0) != 2:
            return None, "PERFORMANCE_SUCCESS_STATUS_CLASS_REQUIRED"
        if float(normalized.get("max_error_rate") or 0.0) != 0.0:
            return None, "PERFORMANCE_ZERO_ERROR_RATE_REQUIRED"
        return normalized, ""

    def handler_without_functional_failures(
        envelope: dict[str, Any],
    ) -> dict[str, Any]:
        receipt = copy.deepcopy(_dict(handler(envelope)))
        if receipt.get("status") != "OBSERVED":
            return receipt
        evidence = dict(_dict(receipt.get("evidence")))
        series = dict(_dict(evidence.get(_surface.EVIDENCE_KEY)))
        status_counts = _dict(series.get("status_class_counts"))
        non_success_count = sum(
            int(value or 0)
            for key, value in status_counts.items()
            if str(key) != "2"
        )
        if non_success_count:
            return _observers._receipt(
                observer_id=_surface.OBSERVER_ID,
                status="INDETERMINATE",
                reason_code="PERFORMANCE_FUNCTIONAL_RESPONSE_INVALID",
                evidence={
                    _surface.EVIDENCE_KEY: {
                        "expected_sample_count": series.get(
                            "expected_sample_count"
                        ),
                        "observed_sample_count": series.get(
                            "observed_sample_count"
                        ),
                        "non_success_sample_count": non_success_count,
                        "status_class_counts": status_counts,
                        "coverage_complete": False,
                        "latency_verdict_suppressed": True,
                        "raw_response_payloads_included": False,
                        "headers_included": False,
                    }
                },
            )
        series["latency_verdict_scope"] = "successful_2xx_reads_only"
        series["functional_error_verdict_included"] = False
        evidence[_surface.EVIDENCE_KEY] = series
        receipt["evidence"] = evidence
        return receipt

    def assertion_without_error_budget(
        envelope: dict[str, Any],
    ) -> dict[str, Any]:
        result = copy.deepcopy(_dict(assertion(envelope)))
        actual = dict(_dict(result.get("actual")))
        expected = dict(_dict(result.get("expected")))
        actual.pop("error_rate_budget_exceeded", None)
        actual.pop("observed_error_rate", None)
        expected.pop("max_error_rate", None)
        result["actual"] = actual
        result["expected"] = expected
        return result

    _surface._validated_contract = strict_validator
    _surface._performance_observer_handler = handler_without_functional_failures
    _surface._evaluate_latency_budget = assertion_without_error_budget

    if _surface.OBSERVER_ID in _observers._REGISTERED_OBSERVER_HANDLERS:
        _observers._REGISTERED_OBSERVER_HANDLERS[_surface.OBSERVER_ID] = (
            handler_without_functional_failures
        )
    if _surface.ASSERTION_KIND in _assertions._REGISTERED_ASSERTION_EVALUATORS:
        _assertions._REGISTERED_ASSERTION_EVALUATORS[
            _surface.ASSERTION_KIND
        ] = assertion_without_error_budget
    setattr(_surface, _INSTALL_MARKER, True)


__all__ = ["install_formal_performance_attribution_guard"]
