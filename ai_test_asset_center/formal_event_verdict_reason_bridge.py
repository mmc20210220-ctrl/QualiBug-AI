"""Add stable reason codes to existing formal event-delivery verdicts.

The registered evaluator remains the sole pass/fail authority. This wrapper classifies an
already-failed verdict from the same privacy-safe observation summary, so customer evidence can
distinguish missing delivery, duplicate delivery, type mismatch and correlation mismatch.
Incomplete coverage remains INDETERMINATE and is never converted into a violation.
"""
from __future__ import annotations

import functools
from typing import Any

from .formal_event_surface import ASSERTION_KIND, EVIDENCE_KEY

_INSTALL_MARKER = "_qualibug_formal_event_verdict_reason_bridge_installed"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def classify_event_delivery_violation(observation: dict[str, Any]) -> list[str]:
    """Return deterministic reasons from the existing privacy-safe summary."""
    row = _dict(observation)
    minimum = _int(row.get("expected_min_count"))
    maximum = _int(row.get("expected_max_count"))
    correlated = _int(row.get("observed_correlated_count"))
    total = _int(row.get("observed_total_count"))
    mismatched = [value for value in _list(row.get("mismatched_event_types")) if value]
    reasons: list[str] = []
    if mismatched:
        reasons.append("EVENT_DELIVERY_TYPE_MISMATCH")
    if minimum > 0 and correlated == 0 and total > 0:
        reasons.append("EVENT_DELIVERY_CORRELATION_MISMATCH")
    if correlated < minimum:
        reasons.append("EVENT_DELIVERY_COUNT_BELOW_MINIMUM")
    if correlated > maximum:
        reasons.append("EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM")
    return list(dict.fromkeys(reasons))


def install_formal_event_verdict_reason_bridge() -> None:
    """Wrap the registered event evaluator once without changing its verdict."""
    from . import assertion_dsl_base as assertions

    if getattr(assertions, _INSTALL_MARKER, False):
        return
    original = assertions._REGISTERED_ASSERTION_EVALUATORS.get(ASSERTION_KIND)
    if not callable(original):
        raise RuntimeError("formal_event_assertion_evaluator_not_registered")

    @functools.wraps(original)
    def evaluate_with_stable_reason(envelope: dict[str, Any]) -> dict[str, Any]:
        result = dict(original(envelope))
        if result.get("passed") is not False:
            return result
        observation = _dict(_dict(envelope.get("observations")).get(EVIDENCE_KEY))
        reasons = classify_event_delivery_violation(observation)
        result["reason_codes"] = reasons or ["EVENT_DELIVERY_CONTRACT_VIOLATION"]
        if not str(result.get("reason_code") or "").strip():
            result["reason_code"] = result["reason_codes"][0]
        return result

    assertions._REGISTERED_ASSERTION_EVALUATORS[ASSERTION_KIND] = (
        evaluate_with_stable_reason
    )
    setattr(assertions, _INSTALL_MARKER, True)


__all__ = [
    "classify_event_delivery_violation",
    "install_formal_event_verdict_reason_bridge",
]
