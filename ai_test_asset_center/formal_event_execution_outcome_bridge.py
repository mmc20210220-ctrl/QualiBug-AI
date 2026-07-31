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
import copy
import functools
import hashlib
from typing import Any

from .formal_event_surface import ASSERTION_KIND, RISK_FAMILY

_INSTALL_MARKER = "_qualibug_formal_event_execution_outcome_installed"
_ORIGINAL_MARKER = "_qualibug_original_finalizer_before_event_outcome"
_ORIGINAL_ORACLE_MARKER = "_qualibug_original_oracle_before_event_scope"
_ORIGINAL_EQUIVALENCE_MARKER = "_qualibug_original_equivalence_before_event_scope"
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


def _projection_receipt_id(
    receipt: dict[str, Any],
    *,
    step_id: str,
    projection_kind: str,
) -> tuple[str, str]:
    """Return (id, origin) from a source id or a sealed source fingerprint."""
    source_id = _receipt_id(receipt)
    if source_id:
        return source_id, "source_receipt_id"
    fingerprint = _text(_dict(receipt).get("fingerprint"))
    if not fingerprint or not _text(step_id) or not _text(projection_kind):
        return "", ""
    material = "|".join(
        [
            _text(projection_kind),
            _text(step_id),
            _text(_dict(receipt).get("schema_version")),
            fingerprint,
        ]
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"event_scope_{digest}", "source_fingerprint"


def _scoped_projection(
    receipt: dict[str, Any],
    *,
    step_id: str,
    projection_kind: str,
) -> dict[str, Any]:
    """Repeat exact step scope around one immutable source receipt."""
    row = copy.deepcopy(_dict(receipt))
    rid, id_origin = _projection_receipt_id(
        row,
        step_id=step_id,
        projection_kind=projection_kind,
    )
    if not rid or not _text(step_id):
        return {}
    return {
        "receipt_id": rid,
        "step_id": _text(step_id),
        "scope_projection_kind": _text(projection_kind),
        "scope_receipt_id_origin": id_origin,
        "source_receipt_id": _receipt_id(row),
        "source_fingerprint": _text(row.get("fingerprint")),
        "source_receipt": row,
        "source_schema_version": _text(row.get("schema_version")),
        "source_status": _text(
            row.get("status")
            or row.get("equivalence_status")
            or row.get("final_status")
        ),
    }


def _replace_with_scoped_projections(
    observations: dict[str, Any],
    *,
    target_key: str,
    receipts: list[dict[str, Any]],
    step_id: str,
    projection_kind: str,
) -> None:
    projections: list[dict[str, Any]] = []
    seen: set[str] = set()
    for receipt in receipts:
        projection = _scoped_projection(
            receipt,
            step_id=step_id,
            projection_kind=projection_kind,
        )
        rid = _receipt_id(projection)
        if not rid or rid in seen:
            continue
        seen.add(rid)
        projections.append(projection)
    if projections:
        observations[target_key] = projections


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


def install_formal_event_execution_outcome_bridge() -> None:
    """Compose Event-specific projections with exact-scope Finalizer hooks."""
    from . import experiment_executor as executor
    from . import experiment_outcome_finalizer as finalizer

    if getattr(executor, _INSTALL_MARKER, False):
        return

    # The exact-scope facade calls these module-level underlying authorities on
    # every run. Wrapping its public re-export is ineffective because
    # ``_install_core_hooks`` restores the core functions before finalization.
    original_oracle = getattr(
        finalizer,
        _ORIGINAL_ORACLE_MARKER,
        finalizer._original_evaluate_contract_oracle,
    )
    setattr(finalizer, _ORIGINAL_ORACLE_MARKER, original_oracle)

    @functools.wraps(original_oracle)
    def evaluate_oracle_with_event_scope(
        *,
        experiment: dict[str, Any],
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        verdict = dict(
            original_oracle(experiment=experiment, evidence=evidence)
        )
        step_id = _event_trigger_step_id(experiment)
        if _is_formal_event(experiment) and step_id:
            _append_oracle_scope(
                evidence,
                verdict=verdict,
                step_id=step_id,
            )
        return verdict

    finalizer._original_evaluate_contract_oracle = (
        evaluate_oracle_with_event_scope
    )

    original_equivalence = getattr(
        finalizer,
        _ORIGINAL_EQUIVALENCE_MARKER,
        finalizer._original_evaluate_cleanup_equivalence,
    )
    setattr(finalizer, _ORIGINAL_EQUIVALENCE_MARKER, original_equivalence)

    @functools.wraps(original_equivalence)
    def evaluate_equivalence_with_event_scope(*args: Any, **kwargs: Any) -> dict[str, Any]:
        receipt = dict(original_equivalence(*args, **kwargs))
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

    finalizer._original_evaluate_cleanup_equivalence = (
        evaluate_equivalence_with_event_scope
    )

    original = getattr(
        executor,
        _ORIGINAL_MARKER,
        executor.finalize_experiment_execution,
    )
    setattr(executor, _ORIGINAL_MARKER, original)

    @functools.wraps(original)
    def finalize_with_event_outcome(**kwargs: Any) -> dict[str, Any]:
        experiment = _dict(kwargs.get("exp"))
        observations = _dict(kwargs.get("observations"))
        step_id = _event_trigger_step_id(experiment)
        token = _EVENT_FINALIZER_SCOPE.set(
            (observations, step_id)
            if _is_formal_event(experiment) and step_id
            else None
        )
        try:
            if _is_formal_event(experiment) and step_id:
                _scope_existing_cleanup_execution(
                    observations,
                    step_id=step_id,
                )
            result = dict(original(**kwargs))
        finally:
            _EVENT_FINALIZER_SCOPE.reset(token)

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
