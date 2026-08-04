"""Unified Binding Builder — constructs all 10 binding types from Behavior IR.

This module is the primary binding construction authority. It reads the
Behavior IR and produces binding edges for all 10 dimensions, inserting
them into the unified BindingLedger.

Industry-neutral: consumes only Behavior IR structures. No project-specific
names, values, or control flow.

Schema: qualibug.binding-builder.v1
"""
from __future__ import annotations

import re
from typing import Any

from .binding_ledger import (
    BindingLedger,
    BindingStatus,
    create_binding_edge,
)
from .binding_evidence import (
    collect_entity_context_evidence,
    collect_operation_context_evidence,
    collect_schema_relation_evidence,
    collect_semantic_name_evidence,
    compute_composite_confidence,
    create_evidence,
    evaluate_binding_evidence,
)
from .real_id_resolver import normalize_path_placeholders, path_has_placeholders


SCHEMA_VERSION = "qualibug.binding-builder.v1"

# Field type classification patterns (industry-neutral)
# Order matters: more specific patterns first
_FIELD_TYPE_PATTERNS: dict[str, list[str]] = {
    "FOREIGN_KEY": ["_id", "id_", "_ref", "ref_", "_key", "parent_id", "owner_id"],
    "OWNER_ID": ["owner", "creator", "created_by", "user_id", "author", "assigned_to"],
    "IDENTITY": ["uuid", "identifier", "pk"],
    "STATE": ["status", "state", "phase", "stage", "lifecycle"],
    "MONEY": ["price", "cost", "fee", "total", "subtotal", "tax", "discount", "revenue", "amount"],
    "QUANTITY_BALANCE": ["qty", "quantity", "balance", "count", "stock", "inventory"],
    "TEMPORAL": ["date", "time", "at", "start", "end", "deadline", "due", "scheduled"],
    "ENUM_STATUS": ["type", "category", "kind", "level", "priority", "mode"],
    "UNIQUE_CODE": ["code", "sku", "number", "serial", "barcode", "reference"],
    "BOOLEAN_FLAG": ["is_", "has_", "can_", "enabled", "active", "deleted", "visible"],
    "TEXT_NAME": ["name", "title", "description", "label", "summary", "comment", "note"],
    "REFERENCE_CODE": ["ref", "external", "source", "origin"],
    "COMPOSITE": ["address", "location", "coordinates", "metadata"],
    "NESTED_OBJECT": ["details", "config", "settings", "properties", "attributes"],
    "ARRAY_COLLECTION": ["items", "lines", "entries", "records", "tags", "attachments"],
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def classify_field_type(field_name: str, field_schema: dict[str, Any] | None = None) -> str:
    """Classify a field into one of 15 types based on name and schema."""
    name_lower = field_name.lower().strip()
    name_norm = re.sub(r"[^a-z0-9]+", "", name_lower)

    # Special case: exact "id" or ends with "id" but not a compound FK pattern
    if name_lower == "id" or name_lower == "uuid" or name_lower == "key":
        return "IDENTITY"

    # Check patterns in priority order
    for field_type, patterns in _FIELD_TYPE_PATTERNS.items():
        for pattern in patterns:
            if pattern in name_lower or pattern in name_norm:
                return field_type

    # Schema-based inference
    if field_schema:
        schema_type = _text(field_schema.get("type")).lower()
        if schema_type == "boolean":
            return "BOOLEAN_FLAG"
        if schema_type == "array":
            return "ARRAY_COLLECTION"
        if schema_type == "object":
            return "NESTED_OBJECT"
        if schema_type in ("integer", "number"):
            if field_schema.get("enum"):
                return "ENUM_STATUS"
            return "QUANTITY_BALANCE"
        if field_schema.get("enum"):
            return "ENUM_STATUS"
        if field_schema.get("format") in ("date", "date-time", "time"):
            return "TEMPORAL"

    return "TEXT_NAME"  # Default fallback


# ─── Main Builder ─────────────────────────────────────────────────────────────

def build_all_bindings(
    behavior_ir: dict[str, Any],
    ledger: BindingLedger,
    *,
    source_module: str = "binding_builder",
) -> dict[str, Any]:
    """Build all 10 binding types from Behavior IR into the ledger.

    Returns a summary of bindings created per type.
    """
    ir = _dict(behavior_ir)
    summary: dict[str, int] = {}

    summary["entity"] = _build_entity_bindings(ir, ledger, source_module)
    summary["operation"] = _build_operation_bindings(ir, ledger, source_module)
    summary["field"] = _build_field_bindings(ir, ledger, source_module)
    summary["relation"] = _build_relation_bindings(ir, ledger, source_module)
    summary["state"] = _build_state_bindings(ir, ledger, source_module)
    summary["actor"] = _build_actor_bindings(ir, ledger, source_module)
    summary["scope"] = _build_scope_bindings(ir, ledger, source_module)
    summary["fixture"] = _build_fixture_bindings(ir, ledger, source_module)
    summary["observer"] = _build_observer_bindings(ir, ledger, source_module)
    summary["oracle_input"] = _build_oracle_input_bindings(ir, ledger, source_module)

    return {
        "schema_version": SCHEMA_VERSION,
        "total_created": sum(summary.values()),
        "per_type": summary,
        "ledger_size": ledger.size,
    }


# ─── 1. Entity Binding ────────────────────────────────────────────────────────

def _build_entity_bindings(ir: dict, ledger: BindingLedger, module: str) -> int:
    """Bind entities to their runtime collection paths and operations."""
    count = 0
    entities = _list(ir.get("entities"))
    operations = _list(ir.get("operations"))
    relations = _list(ir.get("relations"))

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_id = _text(entity.get("id"))
        entity_name = _text(entity.get("name"))
        collection_path = _text(entity.get("collection_path"))
        if not entity_id:
            continue

        # Find create operation (POST to collection)
        create_op = _find_operation_for_entity(operations, collection_path, "POST")
        read_op = _find_operation_for_entity(operations, collection_path, "GET")

        # Build evidence
        evidence = []
        if collection_path:
            evidence.append(collect_semantic_name_evidence(
                ir_node_name=entity_name,
                runtime_target_name=collection_path.rstrip("/").rsplit("/", 1)[-1],
            ))
            evidence.append(collect_entity_context_evidence(
                entity_collection_path=collection_path,
                operation_path=collection_path,
            ))

        # Schema relation evidence
        for rel in relations:
            if not isinstance(rel, dict):
                continue
            if _text(rel.get("from_ref")) == entity_id or _text(rel.get("to_ref")) == entity_id:
                evidence.append(collect_schema_relation_evidence(
                    relation_type=_text(rel.get("relation_type")),
                    from_ref=_text(rel.get("from_ref")),
                    to_ref=_text(rel.get("to_ref")),
                    binding_source_id=entity_id,
                    binding_target_key=collection_path or entity_name,
                ))
                break

        confidence = compute_composite_confidence(evidence)

        metadata = {
            "entity_name": entity_name,
            "collection_path": collection_path,
            "alternate_paths": _list(entity.get("alternate_paths")),
            "create_operation_ref": _text(create_op.get("id")) if create_op else "",
            "read_operation_ref": _text(read_op.get("id")) if read_op else "",
            "runtime_id_path": f"{collection_path}/{{id}}" if collection_path else "",
        }

        binding = ledger.propose(
            binding_type="entity",
            source_node_id=entity_id,
            target_key=collection_path or entity_name,
            source_module=module,
            evidence=evidence,
            metadata=metadata,
        )

        # Auto-promote based on confidence
        _auto_promote(ledger, binding, confidence)
        count += 1

    return count


# ─── 2. Operation Binding ─────────────────────────────────────────────────────

def _build_operation_bindings(ir: dict, ledger: BindingLedger, module: str) -> int:
    """Bind operations to their runtime endpoints."""
    count = 0
    operations = _list(ir.get("operations"))

    for op in operations:
        if not isinstance(op, dict):
            continue
        op_id = _text(op.get("id"))
        method = _text(op.get("method")).upper()
        path = normalize_path_placeholders(_text(op.get("path") or op.get("raw_path")))
        if not op_id or not path:
            continue

        evidence = [
            collect_operation_context_evidence(
                operation_method=method,
                operation_path=path,
                binding_target_path=path,
            ),
            create_evidence(
                dimension="source_consistency",
                score=0.9 if _list(op.get("source_refs")) else 0.4,
                detail=f"source_refs_count:{len(_list(op.get('source_refs')))}",
            ),
        ]

        confidence = compute_composite_confidence(evidence)

        metadata = {
            "method": method,
            "endpoint_path": path,
            "read_write": _text(op.get("read_write")),
            "has_placeholders": path_has_placeholders(path),
            "actor_requirements": _list(op.get("required_roles") or op.get("actor_requirements")),
        }

        binding = ledger.propose(
            binding_type="operation",
            source_node_id=op_id,
            target_key=f"{method} {path}",
            source_module=module,
            evidence=evidence,
            metadata=metadata,
        )
        # Source-declared operations are path identities, not probe targets.
        _auto_promote(
            ledger,
            binding,
            confidence,
            executable_without_probe=True,
        )
        count += 1

    return count


# ─── 3. Field Binding ─────────────────────────────────────────────────────────

def _build_field_bindings(ir: dict, ledger: BindingLedger, module: str) -> int:
    """Bind fields with type classification from operation schemas."""
    count = 0
    operations = _list(ir.get("operations"))
    entities = _list(ir.get("entities"))
    entity_names = {_text(e.get("id")): _text(e.get("name")) for e in entities if isinstance(e, dict)}

    seen_fields: set[str] = set()

    for op in operations:
        if not isinstance(op, dict):
            continue
        op_id = _text(op.get("id"))
        op_path = normalize_path_placeholders(_text(op.get("path") or op.get("raw_path")))

        # Extract fields from request schema
        request_schema = _dict(op.get("request_schema"))
        props = _dict(request_schema.get("properties"))
        for field_name, field_schema in props.items():
            field_key = f"{op_path}:{field_name}"
            if field_key in seen_fields:
                continue
            seen_fields.add(field_key)

            field_type = classify_field_type(field_name, field_schema if isinstance(field_schema, dict) else None)

            evidence = [
                collect_semantic_name_evidence(
                    ir_node_name=field_name,
                    runtime_target_name=field_name,
                ),
                create_evidence(
                    dimension="data_type",
                    score=0.8 if isinstance(field_schema, dict) and field_schema.get("type") else 0.4,
                    detail=f"schema_type:{_text(_dict(field_schema).get('type'))}",
                ),
            ]

            confidence = compute_composite_confidence(evidence)

            metadata = {
                "field_name": field_name,
                "field_type_classification": field_type,
                "operation_ref": op_id,
                "request_path": field_name,
                "schema_type": _text(_dict(field_schema).get("type")),
                "has_enum": bool(_dict(field_schema).get("enum")),
            }

            binding = ledger.propose(
                binding_type="field",
                source_node_id=op_id,
                target_key=field_key,
                source_module=module,
                evidence=evidence,
                metadata=metadata,
            )
            _auto_promote(ledger, binding, confidence)
            count += 1

    return count


# ─── 4. Relation Binding ──────────────────────────────────────────────────────

def _build_relation_bindings(ir: dict, ledger: BindingLedger, module: str) -> int:
    """Bind relations to their correlation keys and materialization operations."""
    count = 0
    relations = _list(ir.get("relations"))
    operations = _list(ir.get("operations"))

    for rel in relations:
        if not isinstance(rel, dict):
            continue
        rel_id = _text(rel.get("id"))
        rel_type = _text(rel.get("relation_type"))
        from_ref = _text(rel.get("from_ref"))
        to_ref = _text(rel.get("to_ref"))
        if not rel_id or not from_ref or not to_ref:
            continue

        correlation_key = _declared_correlation_key(rel)

        # Find materialization operation
        op_ref = _text(rel.get("operation_ref"))
        mat_op = None
        if op_ref:
            mat_op = next((o for o in operations if isinstance(o, dict) and _text(o.get("id")) == op_ref), None)

        evidence = [
            collect_schema_relation_evidence(
                relation_type=rel_type,
                from_ref=from_ref,
                to_ref=to_ref,
                binding_source_id=from_ref,
                binding_target_key=to_ref,
            ),
            create_evidence(
                dimension="source_consistency",
                score=0.9 if _list(rel.get("source_refs")) else 0.5,
                detail=f"relation_type:{rel_type}",
            ),
        ]

        confidence = compute_composite_confidence(evidence)

        metadata = {
            "relation_type": rel_type,
            "source_entity_ref": from_ref,
            "target_entity_ref": to_ref,
            "correlation_key": correlation_key,
            "materialization_operation_ref": _text(mat_op.get("id")) if mat_op else "",
            "cardinality": _text(rel.get("cardinality")),
        }

        binding = ledger.propose(
            binding_type="relation",
            source_node_id=rel_id,
            target_key=f"{from_ref}->{to_ref}:{rel_type}",
            source_module=module,
            evidence=evidence,
            metadata=metadata,
        )
        _auto_promote(ledger, binding, confidence)
        count += 1

    return count


# ─── 5. State Binding ─────────────────────────────────────────────────────────

def _build_state_bindings(ir: dict, ledger: BindingLedger, module: str) -> int:
    """Bind states to their runtime fields and transition operations."""
    count = 0
    states = _list(ir.get("states"))
    relations = _list(ir.get("relations"))
    operations = _list(ir.get("operations"))

    for state in states:
        if not isinstance(state, dict):
            continue
        state_id = _text(state.get("id"))
        entity_ref = _text(state.get("entity_ref"))
        state_field = _text(state.get("field") or state.get("state_field") or "status")
        if not state_id:
            continue

        # Find transition operations for this state
        transition_ops = []
        for rel in relations:
            if not isinstance(rel, dict):
                continue
            if _text(rel.get("relation_type")) == "transitions" and _text(rel.get("from_ref")) == state_id:
                op_ref = _text(rel.get("operation_ref"))
                if op_ref:
                    transition_ops.append(op_ref)

        # Extract raw values
        raw_values = _list(state.get("values") or state.get("raw_values"))
        state_name = _text(state.get("name") or state.get("semantic_name"))
        source_refs = _list(state.get("source_refs"))

        # Source locators of the form "<entity>:<value>" declare the runtime
        # anchor of this state value. An exact entity:value anchor match is
        # source-grounded evidence; comparing a state value name against the
        # field name ("PAID" vs "status") is meaningless and would drag the
        # composite confidence below every promotion threshold.
        locator_values: list[str] = []
        for row in source_refs:
            if not isinstance(row, dict):
                continue
            locator = _text(row.get("locator"))
            if ":" in locator:
                locator_values.append(locator.split(":", 1)[1].strip())
        anchor_value = next(
            (
                value
                for value in locator_values
                if state_name and value.casefold() == state_name.casefold()
            ),
            "",
        )

        evidence = [
            collect_semantic_name_evidence(
                ir_node_name=state_name or state_field,
                runtime_target_name=anchor_value or state_field,
            ),
            create_evidence(
                dimension="source_consistency",
                score=0.95 if anchor_value else (0.9 if source_refs else 0.4),
                detail=f"source_refs_count:{len(source_refs)} anchor:{anchor_value or 'none'}",
            ),
            create_evidence(
                dimension="entity_context",
                score=0.8 if entity_ref else 0.3,
                detail=f"entity_ref:{entity_ref}",
            ),
        ]
        if anchor_value:
            evidence.append(
                create_evidence(
                    dimension="schema_relation",
                    score=0.9,
                    detail=f"source_locator_anchor:{entity_ref}:{anchor_value}",
                )
            )

        confidence = compute_composite_confidence(evidence)

        metadata = {
            "state_name": state_name,
            "state_field_name": state_field,
            "entity_ref": entity_ref,
            "raw_values": raw_values,
            "transition_operations": transition_ops,
            "initial_value": _text(state.get("initial") or state.get("initial_value")),
            "terminal_values": _list(state.get("terminal") or state.get("terminal_values")),
        }

        # The state value itself must be part of the binding identity: state
        # obligations reference states by their declared value name
        # (from_state="CANCELLED"), and a per-field target_key
        # ("order:status") collapses every lifecycle value into one row that
        # no name-form reference can ever resolve.
        target_key = (
            f"{entity_ref}:{state_field}:{state_name}"
            if state_name
            else f"{entity_ref}:{state_field}"
        )

        binding = ledger.propose(
            binding_type="state",
            source_node_id=state_id,
            target_key=target_key,
            source_module=module,
            evidence=evidence,
            metadata=metadata,
        )
        # Source-declared state values are declared identities, not probe
        # targets (same rationale as source-declared operation bindings).
        # Inferred states stay at their evidence-driven confidence.
        _auto_promote(
            ledger,
            binding,
            confidence,
            executable_without_probe=bool(source_refs),
        )
        count += 1

    return count


# ─── 6. Actor Binding ─────────────────────────────────────────────────────────

def _build_actor_bindings(ir: dict, ledger: BindingLedger, module: str) -> int:
    """Bind actors to their runtime credentials and roles."""
    count = 0
    actors = _list(ir.get("actors"))

    for actor in actors:
        if not isinstance(actor, dict):
            continue
        actor_id = _text(actor.get("id"))
        role = _text(actor.get("role"))
        secret_ref = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
        if not actor_id:
            continue

        evidence = [
            create_evidence(
                dimension="source_consistency",
                score=0.9 if secret_ref else 0.3,
                detail=f"credential_secret_ref:{'present' if secret_ref else 'missing'}",
            ),
            create_evidence(
                dimension="semantic_name",
                score=0.8 if role else 0.4,
                detail=f"role:{role}",
            ),
        ]

        # Runtime bound actors get higher confidence
        if actor.get("runtime_bound") is True:
            evidence.append(create_evidence(
                dimension="runtime_behavior",
                score=0.9,
                detail="runtime_bound:true",
            ))

        confidence = compute_composite_confidence(evidence)

        metadata = {
            "actor_ref": actor_id,
            "role": role,
            "credential_secret_ref": secret_ref,
            "account_ref": _text(actor.get("account_ref")),
            "tenant_scope": _text(actor.get("tenant_scope")),
            "organization_scope": _text(actor.get("organization_scope")),
            "allowed_resources": _list(actor.get("allowed_resources")),
            "allowed_actions": _list(actor.get("allowed_actions")),
        }

        binding = ledger.propose(
            binding_type="actor",
            source_node_id=actor_id,
            target_key=f"actor:{actor_id}:{role}",
            source_module=module,
            evidence=evidence,
            metadata=metadata,
        )
        # A runtime-bound actor already has configured credentials. That is the
        # confirmation probe; leaving it at HIGH_CONFIDENCE blocks every write.
        _auto_promote(
            ledger,
            binding,
            confidence,
            executable_without_probe=actor.get("runtime_bound") is True,
        )
        count += 1

    return count


# ─── 7. Scope Binding ─────────────────────────────────────────────────────────

def _build_scope_bindings(ir: dict, ledger: BindingLedger, module: str) -> int:
    """Bind scope dimensions (tenant, organization, resource scope)."""
    count = 0
    actors = _list(ir.get("actors"))
    relations = _list(ir.get("relations"))

    # Extract scope from actors
    scope_fields_seen: set[str] = set()
    for actor in actors:
        if not isinstance(actor, dict):
            continue
        actor_id = _text(actor.get("id"))
        tenant = _text(actor.get("tenant_scope"))
        org = _text(actor.get("organization_scope"))

        for scope_type, scope_value in [("tenant", tenant), ("organization", org)]:
            if not scope_value or scope_value in scope_fields_seen:
                continue
            scope_fields_seen.add(scope_value)

            evidence = [
                create_evidence(
                    dimension="entity_context",
                    score=0.7,
                    detail=f"scope_from_actor:{actor_id}",
                ),
                create_evidence(
                    dimension="source_consistency",
                    score=0.8,
                    detail=f"scope_type:{scope_type}",
                ),
                create_evidence(
                    dimension="semantic_name",
                    score=0.8,
                    detail=f"declared_actor_scope:{scope_type}",
                ),
            ]

            confidence = compute_composite_confidence(evidence)

            metadata = {
                "scope_type": scope_type,
                "scope_field": scope_value,
                "scope_value_source": f"actor:{actor_id}",
                "isolation_level": "strict",
            }

            binding = ledger.propose(
                binding_type="scope",
                source_node_id=actor_id,
                target_key=f"scope:{scope_type}:{scope_value}",
                source_module=module,
                evidence=evidence,
                metadata=metadata,
            )
            # A scope declared on a source actor is a declared isolation
            # coordinate, not a probe target (same rationale as source-declared
            # operation bindings). Undeclared scopes keep evidence-driven
            # promotion.
            _auto_promote(ledger, binding, confidence, executable_without_probe=True)
            count += 1

    # Extract scope from relations (scopes type)
    for rel in relations:
        if not isinstance(rel, dict):
            continue
        if _text(rel.get("relation_type")) != "scopes":
            continue
        rel_id = _text(rel.get("id"))
        from_ref = _text(rel.get("from_ref"))
        to_ref = _text(rel.get("to_ref"))
        if not rel_id:
            continue

        evidence = [
            collect_schema_relation_evidence(
                relation_type="scopes",
                from_ref=from_ref,
                to_ref=to_ref,
                binding_source_id=from_ref,
                binding_target_key=to_ref,
            ),
        ]
        confidence = compute_composite_confidence(evidence)

        metadata = {
            "scope_type": "relation_scopes",
            "scope_field": to_ref,
            "scope_value_source": f"relation:{rel_id}",
        }

        binding = ledger.propose(
            binding_type="scope",
            source_node_id=rel_id,
            target_key=f"scope:relation:{from_ref}->{to_ref}",
            source_module=module,
            evidence=evidence,
            metadata=metadata,
        )
        _auto_promote(ledger, binding, confidence)
        count += 1

    return count


# ─── 8. Fixture Binding ───────────────────────────────────────────────────────

def _build_fixture_bindings(ir: dict, ledger: BindingLedger, module: str) -> int:
    """Bind fixture requirements to create operations and body templates."""
    count = 0
    entities = _list(ir.get("entities"))
    operations = _list(ir.get("operations"))

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_id = _text(entity.get("id"))
        entity_name = _text(entity.get("name"))
        collection_path = _text(entity.get("collection_path"))
        if not entity_id:
            continue
        if not collection_path:
            # No IR builder emits ``collection_path``; derive the collection
            # from the entity's source-declared create operation (a collection
            # POST without path placeholders whose route vocabulary matches
            # the entity name) so fixture bindings actually reach the ledger.
            create_op = _find_entity_create_operation(operations, entity_name)
            if create_op:
                collection_path = normalize_path_placeholders(
                    _text(create_op.get("path") or create_op.get("raw_path"))
                )
        if not collection_path:
            continue

        # Find POST create operation
        create_op = _find_operation_for_entity(operations, collection_path, "POST")
        if not create_op:
            continue

        create_id = _text(create_op.get("id"))
        body_template = _dict(create_op.get("request_example"))

        # Find cleanup operations
        cleanup_ops = _find_cleanup_operations(operations, collection_path)

        evidence = [
            collect_entity_context_evidence(
                entity_collection_path=collection_path,
                operation_path=normalize_path_placeholders(_text(create_op.get("path") or create_op.get("raw_path"))),
            ),
            create_evidence(
                dimension="operation_context",
                score=0.9 if body_template else 0.4,
                detail=f"body_template:{'present' if body_template else 'missing'}",
            ),
        ]

        confidence = compute_composite_confidence(evidence)

        metadata = {
            "create_operation_ref": create_id,
            "create_path": normalize_path_placeholders(_text(create_op.get("path") or create_op.get("raw_path"))),
            "body_template": body_template,
            "cleanup_operations": cleanup_ops,
            "entity_ref": entity_id,
            "entity_name": entity_name,
        }

        binding = ledger.propose(
            binding_type="fixture",
            source_node_id=entity_id,
            target_key=f"fixture:{entity_id}:{create_id}",
            source_module=module,
            evidence=evidence,
            metadata=metadata,
        )
        # A fixture whose create operation and body template are both
        # source-declared is a declared creation path, not a probe target.
        # Fixtures without a source-declared create stay evidence-driven.
        _auto_promote(
            ledger,
            binding,
            confidence,
            executable_without_probe=bool(_list(create_op.get("source_refs"))),
        )
        count += 1

    return count


# ─── 9. Observer Binding ──────────────────────────────────────────────────────

def _build_observer_bindings(ir: dict, ledger: BindingLedger, module: str) -> int:
    """Bind observers to read operations and observed fields."""
    count = 0
    entities = _list(ir.get("entities"))
    operations = _list(ir.get("operations"))

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_id = _text(entity.get("id"))
        entity_name = _text(entity.get("name"))
        collection_path = _text(entity.get("collection_path"))
        if not entity_id:
            continue

        # Find GET read operations for this entity
        read_ops = [
            op for op in operations
            if isinstance(op, dict)
            and _text(op.get("method")).upper() in ("GET", "HEAD")
            and _operation_matches_entity(op, collection_path, entity_name)
        ]

        for read_op in read_ops[:3]:  # Max 3 observers per entity
            read_id = _text(read_op.get("id"))
            read_path = normalize_path_placeholders(_text(read_op.get("path") or read_op.get("raw_path")))
            if not read_id:
                continue

            # Extract observable fields from response schema
            response_schema = _dict(read_op.get("response_schema"))
            observed_fields = _extract_schema_field_names(response_schema)

            evidence = [
                collect_entity_context_evidence(
                    entity_collection_path=collection_path or entity_name,
                    operation_path=read_path,
                ),
                create_evidence(
                    dimension="operation_context",
                    score=0.9,
                    detail=f"read_operation:{read_id}",
                ),
            ]

            confidence = compute_composite_confidence(evidence)

            metadata = {
                "read_operation_ref": read_id,
                "read_path": read_path,
                "observed_fields": observed_fields,
                "entity_ref": entity_id,
                "entity_name": entity_name,
                "cardinality": "MANY" if not path_has_placeholders(read_path) else "ONE",
            }

            binding = ledger.propose(
                binding_type="observer",
                source_node_id=entity_id,
                target_key=f"observer:{entity_id}:{read_id}",
                source_module=module,
                evidence=evidence,
                metadata=metadata,
            )
            _auto_promote(ledger, binding, confidence)
            count += 1

    return count


# ─── 10. Oracle Input Binding ─────────────────────────────────────────────────

def _build_oracle_input_bindings(ir: dict, ledger: BindingLedger, module: str) -> int:
    """Bind oracle inputs to explicit field sources."""
    count = 0
    invariants = _list(ir.get("invariants"))
    operations = _list(ir.get("operations"))

    for inv in invariants:
        if not isinstance(inv, dict):
            continue
        inv_id = _text(inv.get("id"))
        inv_type = _text(inv.get("invariant_type") or inv.get("type"))
        if not inv_id:
            continue

        # Extract field references from invariant
        field_refs = _extract_invariant_field_refs(inv)
        if not field_refs:
            continue

        # Find observer operations that can provide these fields
        observer_ops = []
        for op in operations:
            if not isinstance(op, dict):
                continue
            if _text(op.get("method")).upper() not in ("GET", "HEAD"):
                continue
            response_fields = _extract_schema_field_names(_dict(op.get("response_schema")))
            if any(f in response_fields for f in field_refs):
                observer_ops.append(_text(op.get("id")))

        evidence = [
            create_evidence(
                dimension="source_consistency",
                score=0.8 if _list(inv.get("source_refs")) else 0.4,
                detail=f"invariant_type:{inv_type}",
            ),
            create_evidence(
                dimension="operation_context",
                score=0.7 if observer_ops else 0.2,
                detail=f"observer_ops_available:{len(observer_ops)}",
            ),
        ]

        confidence = compute_composite_confidence(evidence)

        metadata = {
            "oracle_ref": inv_id,
            "invariant_type": inv_type,
            "input_field_bindings": field_refs,
            "source_observer_refs": observer_ops[:3],
            "comparator": _text(inv.get("comparator") or inv.get("operator")),
        }

        binding = ledger.propose(
            binding_type="oracle_input",
            source_node_id=inv_id,
            target_key=f"oracle_input:{inv_id}",
            source_module=module,
            evidence=evidence,
            metadata=metadata,
        )
        _auto_promote(ledger, binding, confidence)
        count += 1

    return count


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _auto_promote(
    ledger: BindingLedger,
    binding: dict[str, Any],
    confidence: float,
    *,
    executable_without_probe: bool = False,
) -> None:
    """Auto-promote binding based on confidence score."""
    binding_id = binding.get("binding_id", "")
    current_status = binding.get("status", "")

    if current_status != BindingStatus.CANDIDATE.value:
        return  # Already promoted

    if confidence >= 0.90:
        try:
            ledger.promote(
                binding_id, BindingStatus.HIGH_CONFIDENCE,
                reason="auto_promote:high_confidence",
                confidence=confidence,
            )
            # Very high confidence, or a binding whose confirmation is already
            # present in source/runtime facts (runtime-bound actor, declared op).
            if confidence >= 0.95 or executable_without_probe:
                ledger.promote(
                    binding_id, BindingStatus.EXECUTABLE,
                    reason=(
                        "auto_promote:source_or_runtime_confirmed"
                        if executable_without_probe
                        else "auto_promote:very_high_confidence"
                    ),
                )
        except ValueError:
            pass
    elif confidence >= 0.70:
        try:
            ledger.promote(
                binding_id, BindingStatus.HIGH_CONFIDENCE,
                reason="auto_promote:moderate_confidence",
                confidence=confidence,
            )
            if executable_without_probe:
                ledger.promote(
                    binding_id, BindingStatus.EXECUTABLE,
                    reason="auto_promote:source_or_runtime_confirmed",
                )
        except ValueError:
            pass


def _find_entity_create_operation(
    operations: list, entity_name: str
) -> dict[str, Any] | None:
    """Find a collection POST create for an entity by route vocabulary.

    Used when an entity carries no declared collection path: the create
    operation is the collection POST whose normalized path matches the
    entity name segment and has no path placeholders (a pure collection
    create). Route-vocabulary matching is generic, never name similarity
    against state or field vocabulary.
    """
    if not entity_name:
        return None
    for op in operations:
        if not isinstance(op, dict):
            continue
        if _text(op.get("method")).upper() != "POST":
            continue
        op_path = normalize_path_placeholders(
            _text(op.get("path") or op.get("raw_path"))
        )
        if path_has_placeholders(op_path):
            continue
        if _operation_matches_entity(op, "", entity_name):
            return op
    return None


def _find_operation_for_entity(
    operations: list, collection_path: str, method: str
) -> dict[str, Any] | None:
    """Find an operation matching entity collection path and method."""
    if not collection_path:
        return None
    target = normalize_path_placeholders(collection_path).rstrip("/").lower()
    for op in operations:
        if not isinstance(op, dict):
            continue
        if _text(op.get("method")).upper() != method.upper():
            continue
        op_path = normalize_path_placeholders(_text(op.get("path") or op.get("raw_path"))).rstrip("/").lower()
        if op_path == target and not path_has_placeholders(op_path):
            return op
    return None


def _find_cleanup_operations(operations: list, collection_path: str) -> list[str]:
    """Find DELETE/cleanup operations for a collection."""
    if not collection_path:
        return []
    target = normalize_path_placeholders(collection_path).rstrip("/").lower()
    cleanup_re = re.compile(
        r"(?:cancel|close|void|disable|archive|reject|delete|deactivate|remove)$", re.I
    )
    results = []
    for op in operations:
        if not isinstance(op, dict):
            continue
        method = _text(op.get("method")).upper()
        op_path = normalize_path_placeholders(_text(op.get("path") or op.get("raw_path")))
        if method == "DELETE" and op_path.rstrip("/").lower().startswith(target):
            results.append(_text(op.get("id")))
        elif method in ("POST", "PUT", "PATCH") and cleanup_re.search(op_path.rstrip("/")):
            if op_path.rstrip("/").lower().startswith(target):
                results.append(_text(op.get("id")))
    return results[:3]


def _operation_matches_entity(op: dict, collection_path: str, entity_name: str) -> bool:
    """Check if an operation path matches an entity."""
    op_path = normalize_path_placeholders(_text(op.get("path") or op.get("raw_path"))).lower()
    if collection_path:
        target = normalize_path_placeholders(collection_path).rstrip("/").lower()
        if op_path.startswith(target):
            return True
    if entity_name:
        entity_norm = re.sub(r"[^a-z0-9]+", "", entity_name.lower())
        path_norm = re.sub(r"[^a-z0-9]+", "", op_path)
        if entity_norm and entity_norm in path_norm:
            return True
    return False


def _extract_schema_field_names(schema: dict[str, Any]) -> list[str]:
    """Extract field names from a JSON schema."""
    fields: list[str] = []
    if not schema:
        return fields
    props = _dict(schema.get("properties"))
    fields.extend(props.keys())
    # Handle array items
    if schema.get("type") == "array":
        items = _dict(schema.get("items"))
        fields.extend(_extract_schema_field_names(items))
    # Handle nested wrappers
    for wrapper in ("data", "items", "results", "records"):
        if wrapper in props:
            nested = _dict(props[wrapper])
            if nested.get("type") == "array":
                fields.extend(_extract_schema_field_names(_dict(nested.get("items"))))
    return fields


def _declared_correlation_key(relation: dict[str, Any]) -> str:
    """Return one exact source-declared correlation key, never a guessed field."""
    direct = _text(relation.get("correlation_key"))
    if direct:
        return direct
    declared = [
        _text(value)
        for value in _list(relation.get("correlation_keys"))
        if isinstance(value, str) and _text(value)
    ]
    return declared[0] if len(declared) == 1 else ""


def _extract_invariant_field_refs(invariant: dict[str, Any]) -> list[str]:
    """Extract field references from an invariant node."""
    fields: list[str] = []
    # Check terms (conservation invariants)
    for term in _list(invariant.get("terms")):
        if isinstance(term, dict):
            field = _text(term.get("field"))
            if field:
                fields.append(field)
    # Check assertion fields
    for key in ("field", "from_field", "to_field", "state_field", "source_field", "target_field"):
        field = _text(invariant.get(key))
        if field:
            fields.append(field)
    # Check expression references
    expr = _text(invariant.get("expression") or invariant.get("condition"))
    if expr:
        # Extract field-like tokens from expression
        tokens = re.findall(r"\b([a-z_][a-z0-9_]*)\b", expr.lower())
        for token in tokens:
            if token not in ("and", "or", "not", "true", "false", "null", "if", "then", "else"):
                fields.append(token)
    return list(dict.fromkeys(fields))[:10]
