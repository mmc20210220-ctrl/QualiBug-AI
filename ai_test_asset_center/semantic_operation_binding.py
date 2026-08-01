"""Exact semantic rule-to-operation binding for the formal discovery mainline.

The agent semantic linker is allowed to select only existing rule and interface
identities. This module turns those accepted identities into Behavior IR
operation joins. It never matches text, paths, fields, states, or business
vocabulary; unresolved or ambiguous identities remain visible in the receipt.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .enterprise_knowledge_center._linking import _relationship_is_authoritative

from .behavior_ir import (
    BehaviorIRError,
    _content_addressed_id,
    _invariant_relation_type,
    _relation_node,
    _source_ref,
    validate_behavior_ir,
)

SCHEMA_VERSION = "qualibug.semantic-operation-binding.v1"


class SemanticOperationBindingError(BehaviorIRError):
    """Accepted semantic identities cannot be converted into a valid IR join."""


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


def _accepted_rule_interface_edges(asset: dict[str, Any]) -> list[dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    for raw in _list(asset.get("relationships")):
        edge = _dict(raw)
        relation = _text(edge.get("relation") or edge.get("relation_type")).lower()
        rule_ref = _text(edge.get("from") or edge.get("from_ref"))
        interface_ref = _text(edge.get("to") or edge.get("to_ref"))
        if (
            relation != "rule_to_interface"
            or not _relationship_is_authoritative(edge)
            or not rule_ref
            or not interface_ref
        ):
            continue
        accepted.append({
            **edge,
            "rule_ref": rule_ref,
            "interface_ref": interface_ref,
            "edge_ref": _text(edge.get("edge_id") or edge.get("id"))
            or "semantic_edge:" + _fingerprint({
                "rule_ref": rule_ref,
                "interface_ref": interface_ref,
                "derivation": _text(edge.get("derivation")),
            })[:20],
        })
    return accepted


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


def bind_accepted_semantic_operations(
    behavior_ir: dict[str, Any],
    knowledge_asset: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind accepted rule/interface identities to invariant/operation IR nodes.

    The returned IR is a deep copy. Existing explicit operation references are
    preserved. An accepted edge advances only when its exact interface identity
    resolves to one and only one Behavior IR operation.
    """

    if not isinstance(behavior_ir, dict):
        raise SemanticOperationBindingError("behavior_ir_not_object")
    if not isinstance(knowledge_asset, dict):
        raise SemanticOperationBindingError("knowledge_asset_not_object")

    enriched = deepcopy(behavior_ir)
    edges = _accepted_rule_interface_edges(knowledge_asset)
    unique_operations, ambiguous_operations = _operation_identity_index(enriched)
    operations = {
        _text(row.get("id")): row
        for row in _list(enriched.get("operations"))
        if isinstance(row, dict) and _text(row.get("id"))
    }

    bindings_by_rule: dict[str, list[dict[str, Any]]] = {}
    unresolved_interface_refs: set[str] = set()
    ambiguous_interface_refs: dict[str, list[str]] = {}
    for edge in edges:
        interface_ref = edge["interface_ref"]
        if interface_ref in ambiguous_operations:
            ambiguous_interface_refs[interface_ref] = ambiguous_operations[interface_ref]
            continue
        operation_ref = unique_operations.get(interface_ref)
        if not operation_ref:
            unresolved_interface_refs.add(interface_ref)
            continue
        bindings_by_rule.setdefault(edge["rule_ref"], []).append({
            "operation_ref": operation_ref,
            "edge": edge,
        })

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
    added_relations: list[dict[str, Any]] = []
    bound_invariant_ids: set[str] = set()
    bound_rule_ids: set[str] = set()
    accepted_binding_count = 0

    for raw_invariant in _list(enriched.get("invariants")):
        invariant = _dict(raw_invariant)
        invariant_ref = _text(invariant.get("id"))
        if not invariant_ref:
            continue
        if _text(invariant.get("binding_status")) == "umbrella_rule_excluded":
            continue
        source_rule_refs = {
            _text(value)
            for value in _list(invariant.get("source_rule_refs"))
            if _text(value)
        }
        exact_bindings = [
            binding
            for rule_ref in sorted(source_rule_refs)
            for binding in bindings_by_rule.get(rule_ref, [])
        ]
        if not exact_bindings:
            continue

        operation_refs = [
            _text(value)
            for value in _list(invariant.get("operation_refs"))
            if _text(value)
        ]
        semantic_refs = [
            _text(value)
            for value in _list(invariant.get("semantic_operation_binding_refs"))
            if _text(value)
        ]
        relation_type = _invariant_relation_type(invariant)

        for binding in exact_bindings:
            operation_ref = binding["operation_ref"]
            edge = binding["edge"]
            edge_ref = edge["edge_ref"]
            if operation_ref not in operation_refs:
                operation_refs.append(operation_ref)
            if edge_ref not in semantic_refs:
                semantic_refs.append(edge_ref)
            relation_key = (
                relation_type,
                operation_ref,
                invariant_ref,
                operation_ref,
            )
            if relation_key not in existing_relation_keys:
                operation = _dict(operations.get(operation_ref))
                edge_source_ref = _source_ref(
                    _text(edge.get("source_id")) or "agent_semantic_linker",
                    locator=edge_ref,
                    kind="accepted_rule_to_interface_identity",
                )
                relation = _relation_node(
                    relation_type=relation_type,
                    from_ref=operation_ref,
                    to_ref=invariant_ref,
                    operation_ref=operation_ref,
                    source_refs=(
                        [edge_source_ref]
                        + _list(operation.get("source_refs"))
                        + _list(invariant.get("source_refs"))
                    )[:5],
                    confidence=min(
                        float(edge.get("confidence") or 0.7),
                        float(operation.get("confidence") or 0.7),
                        float(invariant.get("confidence") or 0.7),
                    ),
                    derivation=(
                        "model-inferred"
                        if _text(edge.get("derivation")) == "agent_semantic_mapping"
                        else "explicit"
                    ),
                    source_relationship_ref=edge_ref,
                )
                added_relations.append(relation)
                existing_relation_keys.add(relation_key)
            accepted_binding_count += 1
            bound_rule_ids.update(source_rule_refs.intersection(bindings_by_rule))

        invariant["operation_refs"] = operation_refs
        invariant["semantic_operation_binding_refs"] = semantic_refs
        invariant["operation_binding_authority"] = (
            "exact_source_or_accepted_agent_semantic_identity"
        )
        bound_invariant_ids.add(invariant_ref)

    enriched["relations"] = [
        *[
            dict(row)
            for row in _list(enriched.get("relations"))
            if isinstance(row, dict)
        ],
        *added_relations,
    ]

    linked_rule_ids = {edge["rule_ref"] for edge in edges}
    unbound_linked_rule_ids = sorted(linked_rule_ids - bound_rule_ids)
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": (
            "NO_ACCEPTED_LINKS"
            if not edges
            else "BOUND_WITH_GAPS"
            if unresolved_interface_refs
            or ambiguous_interface_refs
            or unbound_linked_rule_ids
            else "BOUND"
        ),
        "binding_authority": "exact_source_or_accepted_agent_semantic_identity",
        "heuristic_binding_enabled": False,
        "accepted_edge_count": len(edges),
        "accepted_binding_count": accepted_binding_count,
        "bound_rule_count": len(bound_rule_ids),
        "bound_rule_ids": sorted(bound_rule_ids),
        "bound_invariant_count": len(bound_invariant_ids),
        "bound_invariant_ids": sorted(bound_invariant_ids),
        "added_relation_count": len(added_relations),
        "unresolved_interface_count": len(unresolved_interface_refs),
        "unresolved_interface_refs": sorted(unresolved_interface_refs),
        "ambiguous_interface_count": len(ambiguous_interface_refs),
        "ambiguous_interface_refs": ambiguous_interface_refs,
        "unbound_linked_rule_count": len(unbound_linked_rule_ids),
        "unbound_linked_rule_ids": unbound_linked_rule_ids,
    }
    receipt["receipt_fingerprint"] = _fingerprint(receipt)
    enriched["semantic_operation_binding_receipt"] = receipt
    enriched["model_id"] = _content_addressed_id(enriched)

    errors = validate_behavior_ir(enriched)
    if errors:
        raise SemanticOperationBindingError(
            "semantic_operation_binding_invalid_ir:" + ",".join(errors)
        )
    return enriched, receipt
