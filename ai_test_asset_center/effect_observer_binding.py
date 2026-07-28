"""Bind write effects to exact source-declared read observers.

Canonical fields already carry API response bindings produced from source
schemas.  This module joins an invariant's exact canonical field ids to those
bindings and emits a formal ``observes`` relation between the bound write and
the declared GET/HEAD operation.  No path, token, or field-name similarity is
used.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .behavior_ir import (
    BehaviorIRError,
    _content_addressed_id,
    _relation_node,
    validate_behavior_ir,
)

SCHEMA_VERSION = "qualibug.effect-observer-binding.v1"
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_READ_METHODS = frozenset({"GET", "HEAD"})


class EffectObserverBindingError(BehaviorIRError):
    """Canonical field evidence cannot be converted into a valid observer join."""


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


def _invariant_field_ids(invariant: dict[str, Any]) -> list[str]:
    refs = [
        _text(value)
        for value in _list(invariant.get("field_ids"))
        if _text(value).startswith("cf_")
    ]
    expression = _dict(invariant.get("expression"))
    for operand in _list(expression.get("operands")):
        row = _dict(operand)
        field_ref = _text(row.get("field_id"))
        if field_ref.startswith("cf_") and field_ref not in refs:
            refs.append(field_ref)
    return refs


def bind_source_effect_observers(
    behavior_ir: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Add exact write→read observer relations from canonical field bindings."""

    if not isinstance(behavior_ir, dict):
        raise EffectObserverBindingError("behavior_ir_not_object")

    enriched = deepcopy(behavior_ir)
    operations = {
        _text(row.get("id")): row
        for row in _list(enriched.get("operations"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    canonical_fields: dict[str, dict[str, Any]] = {}
    for entity in _list(enriched.get("entities")):
        if not isinstance(entity, dict):
            continue
        for field in _list(entity.get("fields")):
            row = _dict(field)
            field_ref = _text(row.get("field_id"))
            if field_ref:
                canonical_fields[field_ref] = {
                    **row,
                    "entity_ref": _text(entity.get("id")),
                    "entity_source_refs": _list(entity.get("source_refs")),
                }

    pair_evidence: dict[tuple[str, str], dict[str, Any]] = {}
    missing_field_ids: set[str] = set()
    fields_without_read_binding: set[str] = set()
    invariants_without_write_binding: set[str] = set()

    for invariant in _list(enriched.get("invariants")):
        if not isinstance(invariant, dict):
            continue
        invariant_ref = _text(invariant.get("id"))
        if not invariant_ref or _text(invariant.get("binding_status")) == "umbrella_rule_excluded":
            continue
        field_ids = _invariant_field_ids(invariant)
        if not field_ids:
            continue
        write_refs = [
            operation_ref
            for operation_ref in (
                _text(value)
                for value in _list(invariant.get("operation_refs"))
            )
            if operation_ref in operations
            and _text(operations[operation_ref].get("method")).upper() in _WRITE_METHODS
        ]
        if not write_refs:
            invariants_without_write_binding.add(invariant_ref)
            continue

        for field_ref in field_ids:
            field = _dict(canonical_fields.get(field_ref))
            if not field:
                missing_field_ids.add(field_ref)
                continue
            read_refs = []
            for binding in _list(field.get("api_response_bindings")):
                operation_ref = _text(_dict(binding).get("operation_id"))
                operation = _dict(operations.get(operation_ref))
                if (
                    operation_ref
                    and _text(operation.get("method")).upper() in _READ_METHODS
                    and operation_ref not in read_refs
                ):
                    read_refs.append(operation_ref)
            if not read_refs:
                fields_without_read_binding.add(field_ref)
                continue

            for write_ref in write_refs:
                for read_ref in read_refs:
                    if write_ref == read_ref:
                        continue
                    pair = pair_evidence.setdefault(
                        (write_ref, read_ref),
                        {
                            "field_refs": set(),
                            "invariant_refs": set(),
                            "source_refs": [],
                        },
                    )
                    pair["field_refs"].add(field_ref)
                    pair["invariant_refs"].add(invariant_ref)
                    for source_ref in (
                        _list(invariant.get("source_refs"))
                        + _list(field.get("source_refs"))
                        + _list(field.get("entity_source_refs"))
                    ):
                        if isinstance(source_ref, dict) and source_ref not in pair["source_refs"]:
                            pair["source_refs"].append(source_ref)

    existing_keys = {
        (
            _text(row.get("relation_type")),
            _text(row.get("from_ref")),
            _text(row.get("to_ref")),
            _text(row.get("operation_ref")),
        )
        for row in _list(enriched.get("relations"))
        if isinstance(row, dict)
    }
    added_relations: list[dict[str, Any]] = []
    for (write_ref, read_ref), evidence in sorted(pair_evidence.items()):
        relation_key = ("observes", write_ref, read_ref, read_ref)
        if relation_key in existing_keys:
            continue
        write_operation = _dict(operations.get(write_ref))
        read_operation = _dict(operations.get(read_ref))
        source_refs = (
            _list(evidence.get("source_refs"))
            + _list(write_operation.get("source_refs"))
            + _list(read_operation.get("source_refs"))
        )[:5]
        relation = _relation_node(
            relation_type="observes",
            from_ref=write_ref,
            to_ref=read_ref,
            operation_ref=read_ref,
            effects=[{
                "effect_source_operation_ref": write_ref,
                "observer_operation_ref": read_ref,
                "canonical_field_refs": sorted(evidence["field_refs"]),
                "invariant_refs": sorted(evidence["invariant_refs"]),
            }],
            source_refs=source_refs,
            confidence=min(
                float(write_operation.get("confidence") or 0.7),
                float(read_operation.get("confidence") or 0.7),
            ),
            derivation="schema-derived",
            source_relationship_ref=(
                "canonical_field_response_binding:"
                + _fingerprint({
                    "write_ref": write_ref,
                    "read_ref": read_ref,
                    "field_refs": sorted(evidence["field_refs"]),
                })[:20]
            ),
        )
        added_relations.append(relation)
        existing_keys.add(relation_key)

    enriched["relations"] = [
        *[
            dict(row)
            for row in _list(enriched.get("relations"))
            if isinstance(row, dict)
        ],
        *added_relations,
    ]
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": (
            "BOUND_WITH_GAPS"
            if missing_field_ids
            or fields_without_read_binding
            or invariants_without_write_binding
            else "BOUND"
            if pair_evidence
            else "NO_CANONICAL_EFFECT_BINDINGS"
        ),
        "binding_authority": "canonical_field_api_response_binding",
        "heuristic_binding_enabled": False,
        "candidate_pair_count": len(pair_evidence),
        "added_relation_count": len(added_relations),
        "missing_field_count": len(missing_field_ids),
        "missing_field_ids": sorted(missing_field_ids),
        "field_without_read_binding_count": len(fields_without_read_binding),
        "field_without_read_binding_ids": sorted(fields_without_read_binding),
        "invariant_without_write_binding_count": len(invariants_without_write_binding),
        "invariant_without_write_binding_ids": sorted(invariants_without_write_binding),
    }
    receipt["receipt_fingerprint"] = _fingerprint(receipt)
    enriched["effect_observer_binding_receipt"] = receipt
    enriched["model_id"] = _content_addressed_id(enriched)

    errors = validate_behavior_ir(enriched)
    if errors:
        raise EffectObserverBindingError(
            "effect_observer_binding_invalid_ir:" + ",".join(errors)
        )
    return enriched, receipt
