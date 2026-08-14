"""Canonical defect identity with outcome and authorization-causality authority.

Each independently gated outcome occurrence receives its own synthetic attempt
view before canonical identity derivation. Repeated occurrences may collapse
only when every stable causal identity dimension is equal.

Authorization defects additionally consume the already sealed and validated
``authorization_causality_receipt.comparison_dimension``. Concrete actor names
remain breadth evidence, not identity, while ROLE_PERMISSION,
OWNERSHIP_RELATION and TENANT_SCOPE are distinct causal defect dimensions and
must never collapse into one canonical defect merely because they touch the
same operation/assertion surface.
"""
from __future__ import annotations

from typing import Any

from . import _canonical_defect_registry_mechanics as _core
from ._canonical_defect_registry_mechanics import *  # noqa: F401,F403
from .authorization_delivery_gate import (
    AuthorizationDeliveryGateError,
    validate_authorization_causality_receipt,
)
from .obligation_attempt_ledger import delivery_occurrence_views

_original_one_violation = _core._one_violation
_original_derive_canonical_identity_evidence = _core.derive_canonical_identity_evidence

_AUTHORIZATION_COMPARISON_DIMENSIONS = frozenset({
    "ROLE_PERMISSION",
    "OWNERSHIP_RELATION",
    "TENANT_SCOPE",
})


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
    matching = [
        item
        for item in violations
        if _text(item.get("outcome_ref")) == primary
    ]
    if len(violations) != 1 or len(matching) != 1:
        raise _core._ambiguous("one_violated_outcome_per_occurrence_required")
    return matching[0]


def _with_authorization_causal_dimension(
    evidence: dict[str, Any],
    *,
    attempt: dict[str, Any],
) -> dict[str, Any]:
    """Add only receipted authorization causality to canonical identity."""

    bundle = _dict(_dict(attempt).get("delivery_evidence_bundle"))
    finding = _dict(bundle.get("finding"))
    finding_oracle = _dict(finding.get("oracle"))
    raw_receipt = _dict(finding.get("authorization_causality_receipt"))
    referenced_receipt_id = _text(
        finding_oracle.get("authorization_causality_receipt_id")
    )
    claims_proven = finding_oracle.get("authorization_causality_proven") is True

    if not raw_receipt:
        # If mutable finding metadata claims causal proof, absence of the sealed
        # receipt is an identity-integrity failure. Otherwise this is simply a
        # non-authorization defect and the base identity stays byte-compatible.
        if referenced_receipt_id or claims_proven:
            raise _core._incomplete("authorization.causality_receipt")
        return evidence

    try:
        receipt = validate_authorization_causality_receipt(raw_receipt)
    except AuthorizationDeliveryGateError as exc:
        raise _core.CanonicalDefectRegistryError(
            f"CANONICAL_AUTHORIZATION_CAUSALITY_INVALID:{exc}"
        ) from exc

    receipt_status = _text(receipt.get("status")).upper()
    if receipt_status == "NOT_APPLICABLE":
        # No comparison contract: causal delivery is not required, so there is
        # no causal dimension to add to canonical identity. This is a valid,
        # sealed state (validate_authorization_causality_receipt accepts it) —
        # not an incomplete one. A finding that simultaneously claims causal
        # proof is self-contradictory and stays fail-closed.
        if referenced_receipt_id or claims_proven:
            raise _core._incomplete("authorization.causality_reference")
        return evidence
    if receipt_status != "PASSED":
        # INDETERMINATE denotes genuinely incomplete causal proof; keep
        # fail-closed.
        raise _core._incomplete("authorization.causality_status")
    dimension = _text(receipt.get("comparison_dimension")).upper()
    if dimension not in _AUTHORIZATION_COMPARISON_DIMENSIONS:
        raise _core._incomplete("authorization.comparison_dimension")
    if referenced_receipt_id and referenced_receipt_id != _text(
        receipt.get("receipt_id")
    ):
        raise _core._incomplete("authorization.causality_receipt_reference")

    governed = dict(evidence)
    actor_relation = dict(_dict(governed.get("actor_relation")))
    actor_relation["comparison_dimension"] = dimension
    governed["actor_relation"] = actor_relation

    # Proof fields are audit lineage only and are excluded from the stable
    # canonical identity fingerprint by the mechanics module.
    proof = dict(_dict(governed.get("proof")))
    proof["authorization_causality_receipt_id"] = _text(
        receipt.get("receipt_id")
    )
    proof["authorization_comparison_dimension"] = dimension
    governed["proof"] = proof
    return governed


def derive_canonical_identity_evidence(
    attempt: dict[str, Any],
) -> dict[str, Any]:
    evidence = _original_derive_canonical_identity_evidence(attempt)
    governed = _with_authorization_causal_dimension(
        evidence,
        attempt=attempt,
    )

    bundle = _dict(_dict(attempt).get("delivery_evidence_bundle"))
    oracle = _dict(bundle.get("oracle_receipt"))
    if not bool(oracle.get("canonical_outcome_identity_required")):
        return governed
    outcome_ref = _text(oracle.get("primary_violation_outcome_ref"))
    if not outcome_ref:
        raise _core._incomplete("oracle.primary_violation_outcome_ref")
    governed = dict(governed)
    for field in ("property", "observed_outcome", "proof"):
        value = dict(_dict(governed.get(field)))
        value["outcome_ref"] = outcome_ref
        governed[field] = value
    return governed


def _attempt_by_finding(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in _list(ledger.get("attempts")):
        parent = _dict(raw)
        if _text(parent.get("terminal_status")).upper() != "DELIVERABLE":
            continue
        for attempt in delivery_occurrence_views(parent):
            occurrence_id = _text(attempt.get("finding_id"))
            if not occurrence_id or occurrence_id in result:
                raise _core.CanonicalDefectRegistryError(
                    "CANONICAL_OCCURRENCE_IDENTITY_INVALID"
                )
            result[occurrence_id] = attempt
    return result


_core._one_violation = _one_violation
_core._attempt_by_finding = _attempt_by_finding
_core.derive_canonical_identity_evidence = derive_canonical_identity_evidence

__all__ = sorted(
    name for name in globals() if not name.startswith("__") and name != "_core"
)
