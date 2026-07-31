"""Canonical defect identity with mandatory outcome authority.

The stable registry mechanics remain in the private module. This facade removes the
historical "pick one violation" fallback for canonical Oracle receipts and includes the
validated ``outcome_ref`` in property, observed-outcome, and proof identity dimensions.
"""
from __future__ import annotations

from typing import Any

from . import _canonical_defect_registry_mechanics as _core
from ._canonical_defect_registry_mechanics import *  # noqa: F401,F403

_original_one_violation = _core._one_violation
_original_derive_canonical_identity_evidence = _core.derive_canonical_identity_evidence


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _one_violation(oracle: dict[str, Any]) -> dict[str, Any]:
    row = _dict(oracle)
    if not bool(row.get("canonical_outcome_identity_required")):
        return _original_one_violation(row)
    primary = _text(row.get("primary_violation_outcome_ref"))
    if not primary:
        raise _core._incomplete("oracle.primary_violation_outcome_ref")
    violations = [
        _dict(raw)
        for raw in _list(row.get("assertions"))
        if _text(_dict(raw).get("status")).upper() == "VIOLATION"
    ]
    matching = [item for item in violations if _text(item.get("outcome_ref")) == primary]
    if len(violations) != 1 or len(matching) != 1:
        raise _core._ambiguous("one_violated_outcome_per_occurrence_required")
    return matching[0]


def derive_canonical_identity_evidence(
    attempt: dict[str, Any],
) -> dict[str, Any]:
    evidence = _original_derive_canonical_identity_evidence(attempt)
    bundle = _dict(_dict(attempt).get("delivery_evidence_bundle"))
    oracle = _dict(bundle.get("oracle_receipt"))
    if not bool(oracle.get("canonical_outcome_identity_required")):
        return evidence
    outcome_ref = _text(oracle.get("primary_violation_outcome_ref"))
    if not outcome_ref:
        raise _core._incomplete("oracle.primary_violation_outcome_ref")
    governed = dict(evidence)
    for field in ("property", "observed_outcome", "proof"):
        value = dict(_dict(governed.get(field)))
        value["outcome_ref"] = outcome_ref
        governed[field] = value
    return governed


# The historical derive function resolves this helper from module globals at call time.
_core._one_violation = _one_violation

__all__ = sorted(
    name for name in globals() if not name.startswith("__") and name not in {"_core"}
)
