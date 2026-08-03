"""First-class join: CONFIRMED business behaviors -> Behavior IR invariants.

Enterprise understanding extracts structured business behaviors with canonical
operation-condition-outcome semantics (``operation-condition-outcome.v1``).
Governed implementation bindings
(``qualibug.business-behavior-implementation-binding.v1``) attach exact source
API identities (``interface_id`` / ``operation_id`` with derivation
``exact_operation_object_source_identity``) to those behaviors.

Before this module existed, Behavior IR construction consumed ``rule_library``
and API operations only: ``business_behaviors`` never entered the IR, so
business understanding produced zero obligations and the fact ledger stayed
reference-only. This module closes that mainline gap.

Rules of engagement (aligned with the binding closure contract):

* Only CONFIRMED, non-candidate behaviors participate.
* Only BOUND + authoritative ``api_operation_bindings`` participate; identity
  resolution is exact (``interface_id`` / ``operation_id`` against the IR
  operation identity index). No text/path/field similarity matching.
* A behavior whose binding does not resolve to exactly one IR operation stays
  visible as a coverage gap; it never becomes an invariant.
* Outcome contracts without an observable field/create effect still compile
  into a postcondition invariant; the obligation compiler then emits the
  visible ``SOURCE_POSTCONDITION_EFFECT_UNBOUND`` gap instead of guessing.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .behavior_ir import (
    BehaviorIRError,
    _content_addressed_id,
    _fact_node,
    _relation_node,
    _source_ref,
    _stable_id,
    validate_behavior_ir,
)

SCHEMA_VERSION = "qualibug.business-behavior-invariant-binding.v1"


class BusinessBehaviorInvariantBindingError(BehaviorIRError):
    """Confirmed business behaviors cannot be converted into a valid IR join."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _understanding_model(asset: dict[str, Any]) -> dict[str, Any]:
    return _dict(asset.get("enterprise_understanding_model"))


def _confirmed_behaviors(asset: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _list(asset.get("business_behaviors")) or _list(
        _understanding_model(asset).get("business_behaviors")
    )
    confirmed: list[dict[str, Any]] = []
    for raw in rows:
        behavior = _dict(raw)
        if not _text(behavior.get("behavior_id")):
            continue
        if _text(behavior.get("status")).upper() != "CONFIRMED":
            continue
        if bool(behavior.get("candidate_only")):
            continue
        confirmed.append(behavior)
    return confirmed


def _authoritative_api_bindings(asset: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Index exact-source API bindings by behavior_ref."""

    index: dict[str, list[dict[str, Any]]] = {}
    bindings = _list(asset.get("behavior_implementation_bindings")) or _list(
        _understanding_model(asset).get("behavior_implementation_bindings")
    )
    for raw in bindings:
        binding = _dict(raw)
        behavior_ref = _text(binding.get("behavior_ref"))
        if not behavior_ref:
            continue
        for raw_api in _list(binding.get("api_operation_bindings")):
            api_binding = _dict(raw_api)
            if _text(api_binding.get("status")).upper() != "BOUND":
                continue
            if not api_binding.get("authoritative"):
                continue
            index.setdefault(behavior_ref, []).append(api_binding)
    return index


def _operation_identity_index(
    behavior_ir: dict[str, Any],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    candidates: dict[str, set[str]] = {}
    for raw in _list(behavior_ir.get("operations")):
        operation = _dict(raw)
        operation_ref = _text(operation.get("id"))
        if not operation_ref:
            continue
        identities = {
            operation_ref,
            _text(operation.get("operation_id")),
            *(
                _text(value)
                for value in _list(operation.get("source_operation_refs"))
            ),
        }
        for identity in identities:
            if identity:
                candidates.setdefault(identity, set()).add(operation_ref)

    unique: dict[str, str] = {}
    ambiguous: dict[str, list[str]] = {}
    for identity, operation_refs in candidates.items():
        ordered = sorted(operation_refs)
        if len(ordered) == 1:
            unique[identity] = ordered[0]
        else:
            ambiguous[identity] = ordered
    return unique, ambiguous


def _behavior_source_refs(behavior: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for raw in _list(behavior.get("evidence"))[:3]:
        evidence = _dict(raw)
        source_id = _text(evidence.get("source_id"))
        if not source_id:
            continue
        refs.append(
            _source_ref(
                source_id,
                locator=_text(evidence.get("source_locator")),
                quote=_text(evidence.get("quote"))[:200],
                kind="business_behavior_evidence",
            )
        )
    if not refs:
        for raw in _list(behavior.get("source_refs"))[:3]:
            ref = _dict(raw)
            source_id = _text(ref.get("source_id"))
            if source_id:
                refs.append(
                    _source_ref(
                        source_id,
                        locator=_text(ref.get("locator")),
                        kind="business_behavior_source",
                    )
                )
    return refs


def _behavior_statement(behavior: dict[str, Any]) -> str:
    for field in ("normalized_statement", "statement", "description"):
        text = _text(behavior.get(field))
        if text:
            return text
    for raw in _list(behavior.get("outcome_contracts")):
        text = _text(_dict(raw).get("statement"))
        if text:
            return text
    for raw in _list(behavior.get("expected_effects")):
        text = _text(raw)
        if text:
            return text
    return ""


def _outcome_operands(behavior: dict[str, Any]) -> list[dict[str, Any]]:
    operands: list[dict[str, Any]] = []
    for raw in _list(behavior.get("outcome_contracts")):
        outcome = _dict(raw)
        if _text(outcome.get("status")).upper() not in {"", "CONFIRMED"}:
            continue
        field = _text(outcome.get("field_ref") or outcome.get("field"))
        entity = _text(
            outcome.get("entity_ref")
            or (_list(outcome.get("target_object_refs")) or [""])[0]
        )
        operand: dict[str, Any] = {}
        if entity:
            operand["entity_ref"] = entity
        if field:
            operand["field"] = field
            operand["field_id"] = field
        to_value = outcome.get("to_value")
        if to_value is not None and _text(to_value):
            operand["expected_value"] = to_value
        outcome_type = _text(outcome.get("outcome_type")).upper()
        if outcome_type in {"ENTITY_CREATED", "CREATE", "CREATION"}:
            operand["must_create"] = True
        if operand:
            operands.append(operand)
    return operands


def _expression_kind(behavior: dict[str, Any]) -> str:
    for raw in _list(behavior.get("outcome_contracts")):
        if _text(_dict(raw).get("outcome_type")).upper() == "STATE_TRANSITION":
            return "postcondition"
    if _list(behavior.get("state_effects")):
        return "postcondition"
    return "business_rule"


def bind_business_behavior_invariants(
    behavior_ir: dict[str, Any],
    knowledge_asset: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compile confirmed business behaviors into exact-identity IR invariants.

    The returned IR is a deep copy. Each CONFIRMED behavior whose governed
    implementation binding resolves through exact source identities to one or
    more IR operations becomes one invariant (plus explicit ``observes``
    relations). Unresolved behaviors remain visible coverage gaps.
    """

    if not isinstance(behavior_ir, dict):
        raise BusinessBehaviorInvariantBindingError("behavior_ir_not_object")
    if not isinstance(knowledge_asset, dict):
        raise BusinessBehaviorInvariantBindingError("knowledge_asset_not_object")

    enriched = deepcopy(behavior_ir)
    behaviors = _confirmed_behaviors(knowledge_asset)
    api_bindings_by_behavior = _authoritative_api_bindings(knowledge_asset)
    unique_operations, ambiguous_operations = _operation_identity_index(enriched)

    existing_relation_keys = {
        (
            _text(row.get("relation_type")),
            _text(row.get("from_ref")),
            _text(row.get("to_ref")),
            _text(row.get("operation_ref")),
        )
        for row in _list(enriched.get("relations"))
        if isinstance(row, dict)
    }
    existing_invariant_ids = {
        _text(row.get("id"))
        for row in _list(enriched.get("invariants"))
        if isinstance(row, dict)
    }

    added_invariant_count = 0
    added_relation_count = 0
    bound_behavior_refs: list[str] = []
    unbound_behavior_refs: list[str] = []
    ambiguous_identity_count = 0

    for behavior in behaviors:
        behavior_ref = _text(behavior.get("behavior_id"))
        api_bindings = api_bindings_by_behavior.get(behavior_ref, [])

        operation_refs: list[str] = []
        binding_refs: list[str] = []
        for api_binding in api_bindings:
            binding_id = _text(api_binding.get("binding_id"))
            for identity in (
                _text(api_binding.get("interface_id")),
                _text(api_binding.get("operation_id")),
            ):
                if not identity:
                    continue
                if identity in ambiguous_operations:
                    ambiguous_identity_count += 1
                    continue
                operation_ref = unique_operations.get(identity)
                if not operation_ref:
                    continue
                if operation_ref not in operation_refs:
                    operation_refs.append(operation_ref)
                if binding_id and binding_id not in binding_refs:
                    binding_refs.append(binding_id)

        if not operation_refs:
            unbound_behavior_refs.append(behavior_ref)
            enriched["coverage_gaps"].append(_fact_node(
                node_id=_stable_id("gap", "business_behavior_unbound", behavior_ref),
                typed_fields={
                    "gap_type": "business_behavior_operation_unbound",
                    "reason_code": "BUSINESS_BEHAVIOR_API_BINDING_UNRESOLVED",
                    "description": (
                        "Confirmed business behavior has no authoritative API "
                        "binding that resolves to a Behavior IR operation; it "
                        "cannot produce obligations until an exact source "
                        "identity join exists"
                    ),
                    "business_behavior_ref": behavior_ref,
                },
                source_refs=_behavior_source_refs(behavior),
                confidence=1.0,
                derivation="explicit",
                status="unsupported",
            ))
            continue

        statement = _behavior_statement(behavior)
        invariant_id = _stable_id("inv", behavior_ref)
        if invariant_id in existing_invariant_ids:
            bound_behavior_refs.append(behavior_ref)
            continue
        operands = _outcome_operands(behavior)
        kind = _expression_kind(behavior)
        expression: dict[str, Any] = {
            "kind": kind,
            "operator": "outcome_contract",
            "operands": operands,
        }
        if statement:
            expression["raw"] = statement[:500]
        source_refs = _behavior_source_refs(behavior)
        behavior_confidence = float(behavior.get("confidence") or 0.7)
        confidence = max(0.0, min(0.9, behavior_confidence))

        enriched["invariants"].append(_fact_node(
            node_id=invariant_id,
            typed_fields={
                "description": statement[:500] or behavior_ref,
                "expression": expression,
                "operation_refs": list(operation_refs),
                "source_rule_refs": [behavior_ref],
                "business_behavior_ref": behavior_ref,
                "implementation_binding_refs": list(binding_refs),
                "operation_binding_authority": (
                    "governed_behavior_implementation_bindings"
                ),
            },
            source_refs=source_refs,
            confidence=confidence,
            derivation="model-inferred",
        ))
        added_invariant_count += 1
        bound_behavior_refs.append(behavior_ref)

        for operation_ref in operation_refs:
            relation_key = (
                "observes",
                operation_ref,
                invariant_id,
                operation_ref,
            )
            if relation_key in existing_relation_keys:
                continue
            enriched["relations"].append(_relation_node(
                relation_type="observes",
                from_ref=operation_ref,
                to_ref=invariant_id,
                operation_ref=operation_ref,
                source_refs=source_refs,
                confidence=confidence,
                derivation="model-inferred",
                source_relationship_ref=(
                    binding_refs[0] if binding_refs else behavior_ref
                ),
            ))
            existing_relation_keys.add(relation_key)
            added_relation_count += 1

    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": (
            "NO_CONFIRMED_BEHAVIORS"
            if not behaviors
            else "BOUND_WITH_GAPS"
            if unbound_behavior_refs or ambiguous_identity_count
            else "BOUND"
        ),
        "binding_authority": (
            "exact_source_identity_via_governed_implementation_bindings"
        ),
        "heuristic_binding_enabled": False,
        "confirmed_behavior_count": len(behaviors),
        "bound_behavior_count": len(bound_behavior_refs),
        "bound_behavior_refs": sorted(bound_behavior_refs),
        "added_invariant_count": added_invariant_count,
        "added_relation_count": added_relation_count,
        "unbound_behavior_count": len(unbound_behavior_refs),
        "unbound_behavior_refs": sorted(unbound_behavior_refs),
        "ambiguous_identity_count": ambiguous_identity_count,
    }
    receipt["receipt_fingerprint"] = _fingerprint(receipt)
    enriched["business_behavior_invariant_binding_receipt"] = receipt
    enriched["model_id"] = _content_addressed_id(enriched)

    errors = validate_behavior_ir(enriched)
    if errors:
        raise BusinessBehaviorInvariantBindingError(
            "business_behavior_invariant_binding_invalid_ir:" + ",".join(errors)
        )
    return enriched, receipt


__all__ = [
    "SCHEMA_VERSION",
    "BusinessBehaviorInvariantBindingError",
    "bind_business_behavior_invariants",
]
