"""Preserve measured formal-event INDETERMINATE outcomes after Finalizer packaging.

The generic Finalizer maps every assertion INDETERMINATE without a field-oracle trace to
``BLOCKED_MISSING_OBSERVER``. A formal event observation can be different: the trigger reached
the target, the registered observer returned a valid typed receipt, and the Event assertion ran,
but the bounded window was incomplete. That is an executed measurement with no Bug verdict,
not a pre-execution block.

This bridge changes no assertion or finding authority. It only restores execution status and the
existing assertion reason on an already-INDETERMINATE formal Event result that has real HTTP
steps. It never turns BLOCKED/HARNESS_FAILED activation into execution and never creates a
finding.
"""
from __future__ import annotations

import functools
from typing import Any

from .formal_event_surface import ASSERTION_KIND, RISK_FAMILY

_INSTALL_MARKER = "_qualibug_formal_event_execution_outcome_installed"
_ORIGINAL_MARKER = "_qualibug_original_finalizer_before_event_outcome"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _is_formal_event(experiment: dict[str, Any]) -> bool:
    exp = _dict(experiment)
    if _text(exp.get("risk_family")) != RISK_FAMILY:
        return False
    assertions = [
        row
        for row in _list(exp.get("assertions"))
        if isinstance(row, dict) and _text(row.get("kind")) == ASSERTION_KIND
    ]
    return len(assertions) == 1


def _http_executed(result: dict[str, Any]) -> bool:
    return any(
        isinstance(row, dict) and int(row.get("status_code") or 0) > 0
        for row in _list(_dict(result).get("steps"))
    )


def _indeterminate_reason(result: dict[str, Any]) -> str:
    verdict = _dict(_dict(result).get("oracle_verdict"))
    for assertion in _list(verdict.get("assertions")):
        if not isinstance(assertion, dict):
            continue
        if _text(assertion.get("kind")) != ASSERTION_KIND:
            continue
        reason = _text(assertion.get("reason_code"))
        if reason:
            return reason
    return "EVENT_OBSERVATION_INDETERMINATE"


def install_formal_event_execution_outcome_bridge() -> None:
    """Wrap the Finalizer symbol consumed by the single experiment executor."""
    from . import experiment_executor as executor

    if getattr(executor, _INSTALL_MARKER, False):
        return
    original = getattr(
        executor,
        _ORIGINAL_MARKER,
        executor.finalize_experiment_execution,
    )
    setattr(executor, _ORIGINAL_MARKER, original)

    @functools.wraps(original)
    def finalize_with_event_outcome(**kwargs: Any) -> dict[str, Any]:
        result = dict(original(**kwargs))
        experiment = _dict(kwargs.get("exp"))
        verdict = _dict(result.get("oracle_verdict"))
        if not (
            _is_formal_event(experiment)
            and _text(verdict.get("status")) == "INDETERMINATE"
            and _http_executed(result)
        ):
            return result
        reason = _indeterminate_reason(result)
        result.update({
            "status": "EXECUTED",
            "reason_code": reason,
            "detail": reason,
            "finding": None,
            "finding_created": False,
            "finding_filter_reason": "oracle_indeterminate",
        })
        execution_receipt = dict(_dict(result.get("execution_receipt")))
        execution_receipt.update({
            "status": "EXECUTED",
            "reason_code": reason,
            "detail": reason,
        })
        result["execution_receipt"] = execution_receipt
        return result

    executor.finalize_experiment_execution = finalize_with_event_outcome
    setattr(executor, _INSTALL_MARKER, True)


__all__ = ["install_formal_event_execution_outcome_bridge"]
