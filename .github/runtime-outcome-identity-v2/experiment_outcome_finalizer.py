"""Canonical outcome-aware experiment finalization authority.

Exact process-step scoping remains in the private compatibility module. This facade makes
one additional authority decision: a Contract Oracle candidate becomes a finding only when
one unique violated ``outcome_ref`` survives the complete receipt chain.
"""
from __future__ import annotations

from typing import Any

# Import canonical authorities before the scope mechanics imports the historical core.
# The core uses direct symbol imports; ordering guarantees those symbols are governed.
from . import observer_contracts as _outcome_observers  # noqa: F401
from . import assertion_dsl as _outcome_assertions  # noqa: F401
from . import contract_oracles as _outcome_oracles
from . import _experiment_outcome_finalizer_scope_mechanics as _scope
from ._experiment_outcome_finalizer_scope_mechanics import *  # noqa: F401,F403

_original_finalize_experiment_execution = _scope.finalize_experiment_execution


def __getattr__(name: str) -> Any:
    return getattr(_scope, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stamp_finding_outcome_identity(result: dict[str, Any]) -> dict[str, Any]:
    governed = dict(result)
    oracle = _dict(governed.get("oracle_verdict"))
    if not bool(oracle.get("canonical_outcome_identity_required")):
        return governed
    primary_ref = _text(oracle.get("primary_violation_outcome_ref"))
    finding = _dict(governed.get("finding"))
    if _text(oracle.get("status")) != "VIOLATION":
        if finding:
            governed.update(
                {
                    "finding": None,
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_CANONICAL_OUTCOME_IDENTITY_INCOMPLETE",
                    "detail": "oracle did not produce one canonical violated outcome",
                }
            )
        return governed
    if not primary_ref or not finding:
        governed.update(
            {
                "finding": None,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_CANONICAL_OUTCOME_IDENTITY_INCOMPLETE",
                "detail": "unique violated outcome_ref missing before finding creation",
            }
        )
        return governed

    violations = [
        _dict(row)
        for row in _list(oracle.get("assertions"))
        if isinstance(row, dict)
        and _text(_dict(row).get("status")) == "VIOLATION"
        and _text(_dict(row).get("outcome_ref")) == primary_ref
    ]
    if len(violations) != 1:
        governed.update(
            {
                "finding": None,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_AMBIGUOUS_OUTCOME_FINDING",
                "detail": "one finding requires exactly one violated assertion for outcome_ref",
            }
        )
        return governed
    assertion = violations[0]
    finding = dict(finding)
    finding.update(
        {
            "outcome_ref": primary_ref,
            "oracle_template_ref": _text(assertion.get("oracle_template_ref")),
            "assertion_requirement_ref": _text(
                assertion.get("assertion_requirement_ref")
            ),
            "assertion_receipt_id": _text(assertion.get("receipt_id")),
            "canonical_outcome_identity_bound": True,
        }
    )
    oracle_summary = dict(_dict(finding.get("oracle")))
    oracle_summary.update(
        {
            "outcome_ref": primary_ref,
            "assertion_receipt_id": _text(assertion.get("receipt_id")),
            "canonical_outcome_identity_bound": True,
        }
    )
    finding["oracle"] = oracle_summary
    for key in ("evidence", "raw_evidence"):
        payload = dict(_dict(finding.get(key)))
        payload.update(
            {
                "outcome_ref": primary_ref,
                "assertion_receipt_id": _text(assertion.get("receipt_id")),
            }
        )
        finding[key] = payload
    governed["finding"] = finding
    governed["outcome_ref"] = primary_ref
    governed["assertion_receipt_id"] = _text(assertion.get("receipt_id"))
    return governed


def _normalize_experiment_outcome_identity(exp: dict[str, Any]) -> dict[str, Any]:
    """Activate canonical mode from explicit assertion/observer outcome references.

    No field-name or order guessing is allowed. An observer inherits an outcome only
    when exactly one assertion explicitly names that observer as its requirement.
    """
    governed = dict(_dict(exp))
    assertions = [
        dict(row) for row in _list(governed.get("assertions")) if isinstance(row, dict)
    ]
    explicit_refs = sorted(
        {
            _text(row.get("outcome_ref"))
            for row in assertions
            if row.get("mandatory") is not False and _text(row.get("outcome_ref"))
        }
    )
    if not explicit_refs:
        return governed
    governed["canonical_outcome_identity_required"] = True
    governed["mandatory_outcome_refs"] = explicit_refs
    observer_to_refs: dict[str, set[str]] = {}
    normalized_assertions: list[dict[str, Any]] = []
    for row in assertions:
        assertion = dict(row)
        outcome_ref = _text(assertion.get("outcome_ref"))
        if outcome_ref:
            assertion["canonical_outcome_identity_required"] = True
            assertion.setdefault("semantic_role", "MANDATORY_OUTCOME")
            direct_observer = _text(assertion.get("observer_id"))
            if direct_observer:
                observer_to_refs.setdefault(direct_observer, set()).add(outcome_ref)
            for requirement in _list(assertion.get("observer_requirements")):
                requirement_row = _dict(requirement)
                observer_id = _text(requirement_row.get("observer_id"))
                if observer_id:
                    observer_to_refs.setdefault(observer_id, set()).add(outcome_ref)
        normalized_assertions.append(assertion)
    governed["assertions"] = normalized_assertions
    normalized_observers: list[dict[str, Any]] = []
    for raw in _list(governed.get("observers")):
        if not isinstance(raw, dict):
            continue
        observer = dict(raw)
        observer_id = _text(observer.get("observer_id"))
        refs = observer_to_refs.get(observer_id, set())
        if not _text(observer.get("outcome_ref")) and len(refs) == 1:
            observer["outcome_ref"] = next(iter(refs))
            observer.setdefault("semantic_role", "MANDATORY_OUTCOME")
        normalized_observers.append(observer)
    governed["observers"] = normalized_observers
    return governed


def finalize_experiment_execution(*args: Any, **kwargs: Any) -> dict[str, Any]:
    # Reinstall the exact-scope hooks with the canonical Oracle implementation.
    _scope._core.evaluate_contract_oracle = _outcome_oracles.evaluate_contract_oracle
    call_kwargs = dict(kwargs)
    if isinstance(call_kwargs.get("exp"), dict):
        call_kwargs["exp"] = _normalize_experiment_outcome_identity(
            call_kwargs["exp"]
        )
    result = _original_finalize_experiment_execution(*args, **call_kwargs)
    return _stamp_finding_outcome_identity(_dict(result))


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__")
    and name
    not in {
        "_scope",
        "_outcome_observers",
        "_outcome_assertions",
        "_outcome_oracles",
    }
)
