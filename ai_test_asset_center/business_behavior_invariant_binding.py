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
* A raw outcome field name is descriptive text, not a canonical field id. The
  field becomes executable only through an explicit canonical ref or the
  governed outcome-ref -> effect-slot -> authoritative database identity ->
  canonical-field chain. Missing or ambiguous joins remain coverage gaps.
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


def _governed_bindings_by_behavior(
    asset: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    bindings = _list(asset.get("behavior_implementation_bindings")) or _list(
        _understanding_model(asset).get("behavior_implementation_bindings")
    )
    for raw in bindings:
        binding = _dict(raw)
        behavior_ref = _text(binding.get("behavior_ref"))
        if behavior_ref:
            index.setdefault(behavior_ref, []).append(binding)
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


def _canonical_fields(
    behavior_ir: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], list[str]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_database_identity: dict[tuple[str, str], list[str]] = {}
    for entity_value in _list(behavior_ir.get("entities")):
        entity = _dict(entity_value)
        for field_value in _list(entity.get("fields")):
            field = _dict(field_value)
            field_ref = _text(field.get("field_id"))
            if not field_ref or not _list(field.get("source_refs")):
                continue
            by_id[field_ref] = field
            for database_value in _list(field.get("database_bindings")):
                database = _dict(database_value)
                table = _text(database.get("table")).casefold()
                column = _text(
                    database.get("column") or database.get("field")
                ).casefold()
                if table and column:
                    by_database_identity.setdefault((table, column), []).append(
                        field_ref
                    )
    for identity, refs in by_database_identity.items():
        by_database_identity[identity] = sorted(set(refs))
    return by_id, by_database_identity


def _outcome_slot_refs(
    outcome: dict[str, Any],
    *,
    implementation_binding: dict[str, Any],
) -> list[str]:
    outcome_ref = _text(outcome.get("outcome_id"))
    rows = [
        row
        for row in (
            _dict(value)
            for value in _list(
                implementation_binding.get("outcome_observer_bindings")
            )
        )
        if _text(row.get("outcome_ref")) == outcome_ref
        and _text(row.get("status")).upper() == "BOUND"
    ]
    refs = sorted(
        {
            _text(ref)
            for row in rows
            for ref in _list(row.get("observer_slot_refs"))
            if _text(ref)
        }
    )
    explicit = _text(
        outcome.get("observer_slot_ref")
        or outcome.get("effect_observer_slot_ref")
    )
    if explicit:
        return [explicit] if refs == [explicit] else []
    return refs


def _canonical_field_for_outcome(
    outcome: dict[str, Any],
    *,
    implementation_bindings: list[dict[str, Any]],
    canonical_fields: dict[str, dict[str, Any]],
    database_field_index: dict[tuple[str, str], list[str]],
) -> dict[str, Any]:
    outcome_ref = _text(outcome.get("outcome_id"))
    explicit_field_ref = _text(
        outcome.get("canonical_field_ref") or outcome.get("canonical_field_id")
    )
    if explicit_field_ref:
        if explicit_field_ref in canonical_fields:
            return {
                "status": "BOUND",
                "outcome_ref": outcome_ref,
                "field_ref": explicit_field_ref,
                "authority": "source_declared_canonical_field_identity",
                "observer_slot_ref": "",
            }
        return {
            "status": "UNRESOLVED",
            "outcome_ref": outcome_ref,
            "candidate_field_refs": [],
            "reason_code": "BUSINESS_BEHAVIOR_OUTCOME_FIELD_IDENTITY_UNRESOLVED",
        }
    if len(implementation_bindings) != 1:
        return {
            "status": "AMBIGUOUS" if implementation_bindings else "UNRESOLVED",
            "outcome_ref": outcome_ref,
            "candidate_field_refs": [],
            "reason_code": (
                "BUSINESS_BEHAVIOR_OUTCOME_FIELD_IDENTITY_AMBIGUOUS"
                if implementation_bindings
                else "BUSINESS_BEHAVIOR_OUTCOME_FIELD_IDENTITY_UNRESOLVED"
            ),
        }
    implementation_binding = implementation_bindings[0]
    slot_refs = _outcome_slot_refs(
        outcome,
        implementation_binding=implementation_binding,
    )
    if len(slot_refs) != 1:
        return {
            "status": "AMBIGUOUS" if len(slot_refs) > 1 else "UNRESOLVED",
            "outcome_ref": outcome_ref,
            "candidate_field_refs": [],
            "reason_code": (
                "BUSINESS_BEHAVIOR_OUTCOME_FIELD_IDENTITY_AMBIGUOUS"
                if len(slot_refs) > 1
                else "BUSINESS_BEHAVIOR_OUTCOME_FIELD_IDENTITY_UNRESOLVED"
            ),
        }
    slot_ref = slot_refs[0]
    slots = [
        row
        for row in (
            _dict(value)
            for value in _list(
                implementation_binding.get("effect_observer_bindings")
            )
        )
        if _text(row.get("slot_ref")) == slot_ref
        and _text(row.get("status")).upper() == "BOUND"
    ]
    if len(slots) != 1:
        return {
            "status": "AMBIGUOUS" if len(slots) > 1 else "UNRESOLVED",
            "outcome_ref": outcome_ref,
            "candidate_field_refs": [],
            "reason_code": (
                "BUSINESS_BEHAVIOR_OUTCOME_FIELD_IDENTITY_AMBIGUOUS"
                if len(slots) > 1
                else "BUSINESS_BEHAVIOR_OUTCOME_FIELD_IDENTITY_UNRESOLVED"
            ),
        }
    slot = slots[0]
    if not (
        slot.get("runtime_observer_available") is True
        and slot.get("object_table_identity_confirmed") is True
    ):
        return {
            "status": "UNRESOLVED",
            "outcome_ref": outcome_ref,
            "candidate_field_refs": [],
            "reason_code": "BUSINESS_BEHAVIOR_OUTCOME_FIELD_IDENTITY_UNRESOLVED",
        }
    source_field = _text(outcome.get("field_ref") or outcome.get("field"))
    slot_field = _text(slot.get("source_field_candidate"))
    if not source_field or source_field.casefold() != slot_field.casefold():
        return {
            "status": "UNRESOLVED",
            "outcome_ref": outcome_ref,
            "candidate_field_refs": [],
            "reason_code": "BUSINESS_BEHAVIOR_OUTCOME_FIELD_IDENTITY_UNRESOLVED",
        }
    candidates: set[str] = set()
    for binding_value in _list(slot.get("bindings")):
        binding = _dict(binding_value)
        if binding.get("authoritative") is not True:
            continue
        direct_ref = _text(binding.get("canonical_field_ref"))
        if direct_ref and direct_ref in canonical_fields:
            candidates.add(direct_ref)
        if _text(binding.get("binding_kind")) != "DATABASE_FIELD":
            continue
        table = _text(binding.get("table")).casefold()
        column = _text(
            binding.get("column") or binding.get("field")
        ).casefold()
        if not table or not column or column != source_field.casefold():
            continue
        candidates.update(database_field_index.get((table, column), []))
    ordered = sorted(candidates)
    if len(ordered) == 1:
        return {
            "status": "BOUND",
            "outcome_ref": outcome_ref,
            "field_ref": ordered[0],
            "authority": "governed_outcome_observer_database_identity",
            "observer_slot_ref": slot_ref,
        }
    return {
        "status": "AMBIGUOUS" if len(ordered) > 1 else "UNRESOLVED",
        "outcome_ref": outcome_ref,
        "candidate_field_refs": ordered,
        "reason_code": (
            "BUSINESS_BEHAVIOR_OUTCOME_FIELD_IDENTITY_AMBIGUOUS"
            if len(ordered) > 1
            else "BUSINESS_BEHAVIOR_OUTCOME_FIELD_IDENTITY_UNRESOLVED"
        ),
    }


def _outcome_operands(
    behavior: dict[str, Any],
    *,
    implementation_bindings: list[dict[str, Any]],
    canonical_fields: dict[str, dict[str, Any]],
    database_field_index: dict[tuple[str, str], list[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    operands: list[dict[str, Any]] = []
    resolutions: list[dict[str, Any]] = []
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
            resolution = _canonical_field_for_outcome(
                outcome,
                implementation_bindings=implementation_bindings,
                canonical_fields=canonical_fields,
                database_field_index=database_field_index,
            )
            resolutions.append(resolution)
            if _text(resolution.get("status")) == "BOUND":
                field_ref = _text(resolution.get("field_ref"))
                operand["field_id"] = field_ref
                observer_slot_ref = _text(
                    resolution.get("observer_slot_ref")
                )
                if observer_slot_ref:
                    operand["observer_slot_ref"] = observer_slot_ref
                operand["field_binding_authority"] = _text(
                    resolution.get("authority")
                )
        to_value = outcome.get("to_value")
        if to_value is not None and _text(to_value):
            operand["expected_value"] = to_value
        outcome_type = _text(outcome.get("outcome_type")).upper()
        if outcome_type in {"ENTITY_CREATED", "CREATE", "CREATION"}:
            operand["must_create"] = True
        if operand:
            operands.append(operand)
    return operands, resolutions


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
    governed_bindings_by_behavior = _governed_bindings_by_behavior(
        knowledge_asset
    )
    unique_operations, ambiguous_operations = _operation_identity_index(enriched)
    canonical_fields, database_field_index = _canonical_fields(enriched)

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
    canonical_field_bound_outcome_refs: set[str] = set()
    canonical_field_unresolved_outcome_refs: set[str] = set()
    canonical_field_ambiguous_outcome_refs: set[str] = set()

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
        operands, field_resolutions = _outcome_operands(
            behavior,
            implementation_bindings=governed_bindings_by_behavior.get(
                behavior_ref, []
            ),
            canonical_fields=canonical_fields,
            database_field_index=database_field_index,
        )
        for resolution in field_resolutions:
            outcome_ref = _text(resolution.get("outcome_ref"))
            resolution_status = _text(resolution.get("status"))
            if resolution_status == "BOUND":
                if outcome_ref:
                    canonical_field_bound_outcome_refs.add(outcome_ref)
                continue
            if resolution_status == "AMBIGUOUS":
                if outcome_ref:
                    canonical_field_ambiguous_outcome_refs.add(outcome_ref)
            elif outcome_ref:
                canonical_field_unresolved_outcome_refs.add(outcome_ref)
            reason_code = _text(resolution.get("reason_code")) or (
                "BUSINESS_BEHAVIOR_OUTCOME_FIELD_IDENTITY_UNRESOLVED"
            )
            enriched["coverage_gaps"].append(_fact_node(
                node_id=_stable_id(
                    "gap",
                    "business_behavior_outcome_field_identity",
                    behavior_ref,
                    outcome_ref,
                    reason_code,
                ),
                typed_fields={
                    "gap_type": "business_behavior_outcome_field_identity",
                    "reason_code": reason_code,
                    "description": (
                        "Business outcome field has no unique governed join "
                        "to a canonical source field; raw field text cannot "
                        "authorize an observer"
                    ),
                    "business_behavior_ref": behavior_ref,
                    "outcome_ref": outcome_ref,
                    "candidate_field_refs": sorted(
                        {
                            _text(value)
                            for value in _list(
                                resolution.get("candidate_field_refs")
                            )
                            if _text(value)
                        }
                    ),
                },
                source_refs=_behavior_source_refs(behavior),
                confidence=1.0,
                derivation="explicit",
                status="unsupported",
            ))
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

        typed_fields: dict[str, Any] = {
            "description": statement[:500] or behavior_ref,
            "expression": expression,
            "operation_refs": list(operation_refs),
            "source_rule_refs": [behavior_ref],
            "business_behavior_ref": behavior_ref,
            "implementation_binding_refs": list(binding_refs),
            "operation_binding_authority": (
                "governed_behavior_implementation_bindings"
            ),
        }
        field_ids = sorted(
            {
                _text(operand.get("field_id"))
                for operand in operands
                if _text(operand.get("field_id")) in canonical_fields
            }
        )
        if field_ids:
            typed_fields["field_ids"] = field_ids
        enriched["invariants"].append(_fact_node(
            node_id=invariant_id,
            typed_fields=typed_fields,
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
            if unbound_behavior_refs
            or ambiguous_identity_count
            or canonical_field_unresolved_outcome_refs
            or canonical_field_ambiguous_outcome_refs
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
        "canonical_field_bound_outcome_count": len(
            canonical_field_bound_outcome_refs
        ),
        "canonical_field_bound_outcome_refs": sorted(
            canonical_field_bound_outcome_refs
        ),
        "canonical_field_unresolved_outcome_count": len(
            canonical_field_unresolved_outcome_refs
        ),
        "canonical_field_unresolved_outcome_refs": sorted(
            canonical_field_unresolved_outcome_refs
        ),
        "canonical_field_ambiguous_outcome_count": len(
            canonical_field_ambiguous_outcome_refs
        ),
        "canonical_field_ambiguous_outcome_refs": sorted(
            canonical_field_ambiguous_outcome_refs
        ),
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
