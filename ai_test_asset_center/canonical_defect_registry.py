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

Source-rule identity is deliberately stricter than runtime-outcome identity:
expected values come from the sealed assertion contract and therefore retain
exact numeric/source semantics, while actual runtime values continue to use the
core's coarse semantic normalization so repeated manifestations aggregate.

For multi-step treatment protocols, canonical identity additionally consumes the
assertion's sealed exact-step observer lineage. The sequential execution
authority evaluates HTTP-shaped assertions from the final treatment observation;
canonical identity therefore projects that same final treatment only when an
assertion-referenced exact observer receipt proves its step identity. Ambiguous
or missing causal scope fails closed instead of falling back to source order.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import _canonical_defect_registry_mechanics as _core
from ._canonical_defect_registry_mechanics import *  # noqa: F401,F403
from .authorization_delivery_gate import (
    AuthorizationDeliveryGateError,
    validate_authorization_causality_receipt,
)
from .obligation_attempt_ledger import delivery_occurrence_views
from .process_step_receipt_scope import extract_receipt_step_scope

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


def _source_expected_semantic_value(
    value: Any,
    *,
    assertion_kind: str,
    depth: int = 0,
) -> Any:
    """Canonicalize source-declared expectations without erasing rule values."""
    if depth > 4:
        return {"type": "truncated"}
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"type": "number", "value": value}
    if isinstance(value, str):
        normalized = _core._normalized_text(value)
        if (
            assertion_kind in {"http_status", "status_code", "http_status_class"}
            and normalized.isdigit()
            and len(normalized) == 3
        ):
            return {"type": "number", "value": int(normalized)}
        return {
            "type": "string",
            "semantic_digest": _core._semantic_digest(normalized),
        }
    if isinstance(value, list):
        return {
            "type": "list",
            "items": [
                _source_expected_semantic_value(
                    item,
                    assertion_kind=assertion_kind,
                    depth=depth + 1,
                )
                for item in value[:12]
            ],
        }
    if isinstance(value, dict):
        entries = [
            {
                "key_digest": _core._semantic_digest(_core._normalized_text(key)),
                "value": _source_expected_semantic_value(
                    item,
                    assertion_kind=assertion_kind,
                    depth=depth + 1,
                ),
            }
            for key, item in sorted(
                value.items(),
                key=lambda pair: _core._normalized_text(pair[0]),
            )[:24]
        ]
        return {"type": "object", "entries": entries}
    return {"type": type(value).__name__}


def _sealed_source_rule_dimensions(assertion: dict[str, Any]) -> dict[str, str]:
    """Return only assertion identity fields sealed by assertion receipt ID."""
    dimensions: dict[str, str] = {}
    for field in ("oracle_template_ref", "assertion_requirement_ref"):
        value = _text(assertion.get(field))
        if value:
            dimensions[field] = value
    return dimensions


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


def _causal_identity_attempt(
    attempt: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Project multi-step treatment identity onto the step actually adjudicated.

    The sequential executor's canonical HTTP input is the final treatment
    observation. Exact-scope finalization separately seals observer receipts by
    step. For a multi-step reproduction, require the VIOLATION assertion to
    reference at least one exact observer receipt for that final treatment
    step. No source-order-only fallback is allowed.

    Single-treatment attempts are returned unchanged for byte-compatible
    historical behavior.
    """
    row = _dict(attempt)
    bundle = _dict(row.get("delivery_evidence_bundle"))
    reproduction = _dict(bundle.get("reproduction_receipt"))
    step_observations = _list(reproduction.get("step_observations"))
    treatment_rows = [
        _dict(raw)
        for raw in step_observations
        if _core._normalized_text(_dict(raw).get("phase")) == "treatment"
    ]
    if len(treatment_rows) <= 1:
        return row, ""

    treatment_ids = [_text(step.get("step_id")) for step in treatment_rows]
    if any(not step_id for step_id in treatment_ids):
        raise _core._incomplete("reproduction.treatment_step_id")
    if len(set(treatment_ids)) != len(treatment_ids):
        raise _core._ambiguous("reproduction.treatment_step_id")

    oracle = _dict(bundle.get("oracle_receipt"))
    assertion = _one_violation(oracle)
    referenced_receipt_ids = [
        _text(value)
        for value in _list(assertion.get("observer_receipt_ids"))
        if _text(value)
    ]
    if not referenced_receipt_ids:
        raise _core._incomplete("assertion.observer_receipt_ids")

    observer_by_id = {
        _text(receipt.get("receipt_id")): receipt
        for raw in _list(bundle.get("observer_receipts"))
        for receipt in [_dict(raw)]
        if _text(receipt.get("receipt_id"))
    }
    if any(receipt_id not in observer_by_id for receipt_id in referenced_receipt_ids):
        raise _core._incomplete("assertion.observer_receipt_missing")

    known_treatment_ids = set(treatment_ids)
    exact_treatment_ids: set[str] = set()
    for receipt_id in referenced_receipt_ids:
        scope = extract_receipt_step_scope(
            observer_by_id[receipt_id],
            known_step_ids=treatment_ids,
        )
        if scope.get("status") != "EXACT":
            continue
        step_id = _text(scope.get("step_id"))
        if step_id in known_treatment_ids:
            exact_treatment_ids.add(step_id)

    causal_step_id = treatment_ids[-1]
    if causal_step_id not in exact_treatment_ids:
        raise _core._incomplete("assertion.causal_treatment_step")

    projected = deepcopy(row)
    projected_bundle = _dict(projected.get("delivery_evidence_bundle"))
    projected_reproduction = _dict(projected_bundle.get("reproduction_receipt"))
    projected_reproduction["step_observations"] = [
        raw
        for raw in _list(projected_reproduction.get("step_observations"))
        if (
            _core._normalized_text(_dict(raw).get("phase")) != "treatment"
            or _text(_dict(raw).get("step_id")) == causal_step_id
        )
    ]
    projected_bundle["reproduction_receipt"] = projected_reproduction
    projected["delivery_evidence_bundle"] = projected_bundle
    return projected, causal_step_id


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
        if referenced_receipt_id or claims_proven:
            raise _core._incomplete("authorization.causality_reference")
        return evidence
    if receipt_status != "PASSED":
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
    identity_attempt, causal_treatment_step_id = _causal_identity_attempt(attempt)
    evidence = _original_derive_canonical_identity_evidence(identity_attempt)
    governed = _with_authorization_causal_dimension(
        evidence,
        attempt=attempt,
    )

    bundle = _dict(_dict(attempt).get("delivery_evidence_bundle"))
    oracle = _dict(bundle.get("oracle_receipt"))
    assertion = _one_violation(oracle)
    assertion_kind = _core._normalized_text(assertion.get("kind"))
    if not assertion_kind:
        raise _core._incomplete("assertion.kind")
    exact_expected = _source_expected_semantic_value(
        assertion.get("expected"),
        assertion_kind=assertion_kind,
    )
    rule_dimensions = _sealed_source_rule_dimensions(assertion)

    governed = dict(governed)
    property_identity = dict(_dict(governed.get("property")))
    property_identity["expected_signature"] = exact_expected
    property_identity.update(rule_dimensions)
    governed["property"] = property_identity

    observed_outcome = dict(_dict(governed.get("observed_outcome")))
    observed_outcome["expected_signature"] = exact_expected
    observed_outcome.update(rule_dimensions)
    governed["observed_outcome"] = observed_outcome

    proof = dict(_dict(governed.get("proof")))
    proof.update(rule_dimensions)
    if causal_treatment_step_id:
        proof["causal_treatment_step_id"] = causal_treatment_step_id
    governed["proof"] = proof

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
