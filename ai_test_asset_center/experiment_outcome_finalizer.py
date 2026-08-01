"""Canonical outcome-aware experiment finalization authority.

Exact process-step scoping remains in the private compatibility module. One execution may
prove several independent outcome violations; this facade fans them out into deterministic
finding occurrences while preserving the aggregate Oracle receipt for audit.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

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


def _finding_for_assertion(
    base_finding: dict[str, Any],
    assertion: dict[str, Any],
    *,
    outcome_ref: str,
    oracle_receipt: dict[str, Any],
    occurrence_index: int,
    occurrence_count: int,
) -> dict[str, Any]:
    finding = deepcopy(base_finding)
    kind = _text(assertion.get("kind") or "contract")
    title = _text(finding.get("title"))
    suffix = title.split(":", 1)[1].strip() if ":" in title else title
    reason = _text(assertion.get("error") or assertion.get("reason_code"))
    description = (
        reason
        or f"mandatory outcome {outcome_ref} violated typed assertion {kind}"
    )
    finding.update(
        {
            "title": f"[ContractOracle] {kind}: {suffix or outcome_ref}",
            "description": description,
            "category": kind,
            "risk_family": (
                "authorization" if kind == "owner_tenant_visibility" else kind
            ),
            "outcome_ref": outcome_ref,
            "oracle_template_ref": _text(assertion.get("oracle_template_ref")),
            "assertion_requirement_ref": _text(
                assertion.get("assertion_requirement_ref")
            ),
            "assertion_receipt_id": _text(assertion.get("receipt_id")),
            "canonical_outcome_identity_bound": True,
            "outcome_occurrence_index": occurrence_index,
            "outcome_occurrence_count": occurrence_count,
            "expected": assertion.get("expected"),
            "actual": assertion.get("actual"),
            "failed_assertions": [dict(assertion)],
        }
    )
    oracle_summary = dict(_dict(finding.get("oracle")))
    oracle_summary.update(
        {
            "outcome_ref": outcome_ref,
            "assertion_receipt_id": _text(assertion.get("receipt_id")),
            "receipt_id": _text(oracle_receipt.get("receipt_id")),
            "activation_receipt_id": _text(
                oracle_receipt.get("activation_receipt_id")
            ),
            "canonical_outcome_identity_bound": True,
            "parent_oracle_receipt_id": _text(
                oracle_receipt.get("parent_oracle_receipt_id")
            ),
        }
    )
    finding["oracle"] = oracle_summary
    finding["oracle_receipt_id"] = _text(oracle_receipt.get("receipt_id"))
    finding["activation_receipt_id"] = _text(
        oracle_receipt.get("activation_receipt_id")
    )

    evidence = dict(_dict(finding.get("evidence")))
    evidence.update(
        {
            "outcome_ref": outcome_ref,
            "assertion_receipt_id": _text(assertion.get("receipt_id")),
            "assertion": dict(assertion),
            "oracle_receipt_id": _text(oracle_receipt.get("receipt_id")),
        }
    )
    finding["evidence"] = evidence

    raw_evidence = dict(_dict(finding.get("raw_evidence")))
    raw_evidence.update(
        {
            "outcome_ref": outcome_ref,
            "assertion_receipt_id": _text(assertion.get("receipt_id")),
            "oracle_receipt_id": _text(oracle_receipt.get("receipt_id")),
        }
    )
    db_snapshot = dict(_dict(raw_evidence.get("db_snapshot")))
    db_snapshot["assertion"] = dict(assertion)
    raw_evidence["db_snapshot"] = db_snapshot
    finding["raw_evidence"] = raw_evidence
    return finding


def _fanout_finding_outcomes(result: dict[str, Any]) -> dict[str, Any]:
    governed = dict(result)
    aggregate = _dict(governed.get("oracle_verdict"))
    base_finding = _dict(governed.get("finding"))
    if not bool(aggregate.get("canonical_outcome_identity_required")):
        governed["findings"] = [dict(base_finding)] if base_finding else []
        return governed

    if _text(aggregate.get("status")) != "VIOLATION":
        governed["finding"] = None
        governed["findings"] = []
        return governed

    violation_refs = sorted(
        {
            _text(value)
            for value in _list(aggregate.get("violation_outcome_refs"))
            if _text(value)
        }
    )
    if not violation_refs or not base_finding:
        governed.update(
            {
                "finding": None,
                "findings": [],
                "status": "BLOCKED",
                "reason_code": "BLOCKED_CANONICAL_OUTCOME_IDENTITY_INCOMPLETE",
                "detail": "violated outcome refs or finding template missing",
            }
        )
        return governed

    assertions = [
        _dict(row)
        for row in _list(aggregate.get("assertions"))
        if isinstance(row, dict) and _text(_dict(row).get("status")) == "VIOLATION"
    ]
    oracle_receipts: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for index, outcome_ref in enumerate(violation_refs, start=1):
        matches = [
            row for row in assertions if _text(row.get("outcome_ref")) == outcome_ref
        ]
        if len(matches) != 1:
            governed.update(
                {
                    "finding": None,
                    "findings": [],
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_AMBIGUOUS_OUTCOME_FINDING",
                    "detail": (
                        "each violated outcome requires exactly one assertion receipt"
                    ),
                }
            )
            return governed
        oracle = _outcome_oracles.project_contract_oracle_for_outcome(
            aggregate, outcome_ref
        )
        oracle_receipts.append(oracle)
        findings.append(
            _finding_for_assertion(
                base_finding,
                matches[0],
                outcome_ref=outcome_ref,
                oracle_receipt=oracle,
                occurrence_index=index,
                occurrence_count=len(violation_refs),
            )
        )

    governed["aggregate_oracle_verdict"] = aggregate
    governed["outcome_oracle_receipts"] = oracle_receipts
    governed["oracle_verdict"] = oracle_receipts[0]
    governed["findings"] = findings
    governed["finding"] = findings[0]
    governed["outcome_fanout"] = {
        "status": "FANNED_OUT" if len(findings) > 1 else "SINGLE",
        "occurrence_count": len(findings),
        "outcome_refs": violation_refs,
        "aggregate_oracle_receipt_id": _text(aggregate.get("receipt_id")),
        "oracle_receipt_ids": [
            _text(row.get("receipt_id")) for row in oracle_receipts
        ],
        "legacy_finding_is_projection": True,
    }
    return governed


def _normalize_experiment_outcome_identity(exp: dict[str, Any]) -> dict[str, Any]:
    """Activate canonical mode from explicit assertion/observer outcome references."""
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
    _scope._core.evaluate_contract_oracle = _outcome_oracles.evaluate_contract_oracle
    call_kwargs = dict(kwargs)
    if isinstance(call_kwargs.get("exp"), dict):
        call_kwargs["exp"] = _normalize_experiment_outcome_identity(call_kwargs["exp"])
    result = _original_finalize_experiment_execution(*args, **call_kwargs)
    return _fanout_finding_outcomes(_dict(result))


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
