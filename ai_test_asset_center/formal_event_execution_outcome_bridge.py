"""Close formal Event outcomes on the canonical exact-scope Finalizer.

The bridge keeps two Event-specific responsibilities:

* project real Event Oracle, assertion, cleanup execution and cleanup
  verification receipts onto the unique source-declared trigger step without
  mutating their strict source schemas;
* preserve a measured Event INDETERMINATE result as EXECUTED without creating a
  Bug.

Fixture applicability and Receipt Bundle activation are owned by lifecycle and
the generic exact-scope Finalizer. No receipt, fixture, verdict, cleanup result,
or restoration fact is invented here.
"""
from __future__ import annotations

import contextvars
from typing import Any, Callable

from .formal_event_surface import ASSERTION_KIND, RISK_FAMILY
from .process_step_receipt_scope import (
    build_exact_step_receipt_projection,
    replace_with_exact_step_receipt_projections,
)

_EVENT_FINALIZER_SCOPE: contextvars.ContextVar[
    tuple[dict[str, Any], str] | None
] = contextvars.ContextVar("qualibug_formal_event_finalizer_scope", default=None)


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


def _event_trigger_step_id(experiment: dict[str, Any]) -> str:
    matches = [
        _text(row.get("step_id"))
        for row in _list(_dict(experiment).get("treatment_plan"))
        if isinstance(row, dict)
        and (
            _text(row.get("protocol_step")) == "event_trigger"
            or _text(row.get("intent")) == "trigger_source_declared_event"
        )
        and _text(row.get("step_id"))
    ]
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else ""


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


def _receipt_id(receipt: dict[str, Any]) -> str:
    row = _dict(receipt)
    return _text(
        row.get("receipt_id")
        or row.get("verification_id")
        or row.get("id")
    )


def _scoped_projection(
    receipt: dict[str, Any],
    *,
    step_id: str,
    projection_kind: str,
) -> dict[str, Any]:
    return build_exact_step_receipt_projection(
        receipt,
        step_id=step_id,
        projection_kind=projection_kind,
    )


def _replace_with_scoped_projections(
    observations: dict[str, Any],
    *,
    target_key: str,
    receipts: list[dict[str, Any]],
    step_id: str,
    projection_kind: str,
) -> None:
    replace_with_exact_step_receipt_projections(
        observations,
        target_key=target_key,
        receipts=receipts,
        step_id=step_id,
        projection_kind=projection_kind,
    )


def _scope_existing_cleanup_execution(
    observations: dict[str, Any],
    *,
    step_id: str,
) -> None:
    rows = [
        row
        for row in _list(observations.get("cleanup_execution_receipts"))
        if isinstance(row, dict)
    ]
    singular = _dict(observations.get("cleanup_execution_receipt"))
    if singular and not rows:
        rows = [singular]
    if rows:
        _replace_with_scoped_projections(
            observations,
            target_key="cleanup_execution_receipts",
            receipts=rows,
            step_id=step_id,
            projection_kind="event_cleanup_execution",
        )


def _append_oracle_scope(
    observations: dict[str, Any],
    *,
    verdict: dict[str, Any],
    step_id: str,
) -> None:
    _replace_with_scoped_projections(
        observations,
        target_key="oracle_invocation_receipts",
        receipts=[verdict],
        step_id=step_id,
        projection_kind="event_contract_oracle",
    )
    assertions = [
        row for row in _list(verdict.get("assertions")) if isinstance(row, dict)
    ]
    if assertions:
        _replace_with_scoped_projections(
            observations,
            target_key="oracle_trace_receipts",
            receipts=assertions,
            step_id=step_id,
            projection_kind="event_assertion_trace",
        )


def _evaluate_oracle_with_event_scope(
    next_call: Callable[..., dict[str, Any]],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    verdict = dict(next_call(*args, **kwargs))
    experiment = _dict(kwargs.get("experiment"))
    if not experiment and args and isinstance(args[0], dict):
        experiment = args[0]
    evidence = kwargs.get("evidence")
    step_id = _event_trigger_step_id(experiment)
    if _is_formal_event(experiment) and step_id and isinstance(evidence, dict):
        # PROPERTY_HELD and VIOLATION both mean the protocol was completely
        # evaluated. Assertion truth remains in Oracle status; target_reached
        # expresses execution completion independently.
        scoped_verdict = dict(verdict)
        if _text(verdict.get("status")).upper() in {
            "PROPERTY_HELD",
            "VIOLATION",
        }:
            scoped_verdict["target_reached"] = True
        _append_oracle_scope(
            evidence,
            verdict=scoped_verdict,
            step_id=step_id,
        )
    return verdict


def _evaluate_equivalence_with_event_scope(
    next_call: Callable[..., dict[str, Any]],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    receipt = dict(next_call(*args, **kwargs))
    scope = _EVENT_FINALIZER_SCOPE.get()
    if scope is not None:
        observations, step_id = scope
        _replace_with_scoped_projections(
            observations,
            target_key="cleanup_verification_receipts",
            receipts=[receipt],
            step_id=step_id,
            projection_kind="event_cleanup_verification",
        )
    return receipt


def _finalize_with_event_outcome(
    next_call: Callable[[tuple[Any, ...], dict[str, Any]], dict[str, Any]],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    experiment = _dict(kwargs.get("exp") or kwargs.get("experiment"))
    observations = _dict(kwargs.get("observations"))
    step_id = _event_trigger_step_id(experiment)
    is_event = _is_formal_event(experiment) and bool(step_id)
    token = _EVENT_FINALIZER_SCOPE.set(
        (observations, step_id) if is_event else None
    )
    try:
        if is_event:
            _scope_existing_cleanup_execution(
                observations,
                step_id=step_id,
            )
        result = dict(next_call(args, kwargs))
    finally:
        _EVENT_FINALIZER_SCOPE.reset(token)

    verdict = _dict(result.get("oracle_verdict"))
    if not (
        is_event
        and _text(verdict.get("status")) == "INDETERMINATE"
        and _http_executed(result)
    ):
        return result
    reason = _indeterminate_reason(result)
    result.update(
        {
            "status": "EXECUTED",
            "reason_code": reason,
            "detail": reason,
            "finding": None,
            "finding_created": False,
            "finding_filter_reason": "oracle_indeterminate",
        }
    )
    execution_receipt = dict(_dict(result.get("execution_receipt")))
    execution_receipt.update(
        {
            "status": "EXECUTED",
            "reason_code": reason,
            "detail": reason,
        }
    )
    result["execution_receipt"] = execution_receipt
    return result


def install_formal_event_execution_outcome_bridge() -> None:
    """Register Event projections on the canonical Finalizer composition points."""
    from . import experiment_outcome_finalizer as finalizer

    finalizer.register_contract_oracle_hook(
        "formal_event_oracle_scope",
        _evaluate_oracle_with_event_scope,
    )
    finalizer.register_cleanup_equivalence_hook(
        "formal_event_cleanup_scope",
        _evaluate_equivalence_with_event_scope,
    )
    finalizer.register_finalizer_hook(
        "formal_event_execution_outcome",
        _finalize_with_event_outcome,
    )


__all__ = ["install_formal_event_execution_outcome_bridge"]
