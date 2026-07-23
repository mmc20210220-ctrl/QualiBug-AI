"""Related Entity Observer Binder.

Discovers LIST/READ operations for related entities from the Operation Catalog,
binds filter parameters (relation_key, tenant_scope), and produces a bound
observer plan ready for execution.

This module is industry-generic: it uses entity_id, entity_alias, relation
bindings, field semantics, and operation schema — never project-specific names.
"""
from __future__ import annotations

import re
from typing import Any

from .real_id_resolver_base import (
    normalize_path_placeholders,
    collection_path,
    infer_path_params,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


# ─── Operation Discovery ─────────────────────────────────────────────────────


def discover_read_operations(
    entity_name: str,
    operations: list[dict[str, Any]],
    *,
    required_fields: list[str] | None = None,
    relation_key: str = "",
    scope_fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Discover LIST/READ operations for an entity from the operation catalog.

    Returns scored operations sorted by relevance. Each result includes:
    - operation: the original operation dict
    - score: relevance score (higher is better)
    - score_breakdown: detailed scoring components
    - parameter_bindings: suggested query parameter bindings
    """
    required_fields = required_fields or []
    scope_fields = scope_fields or []
    entity_lower = entity_name.lower().replace("_", "").replace("-", "")

    candidates: list[dict[str, Any]] = []

    for op in operations:
        if not isinstance(op, dict):
            continue
        method = _text(op.get("method")).upper()
        if method not in ("GET", "HEAD"):
            continue

        op_path = normalize_path_placeholders(
            _text(op.get("path") or op.get("raw_path"))
        )
        if not op_path:
            continue

        # Score this operation
        score, breakdown, param_bindings = _score_operation(
            op,
            op_path=op_path,
            entity_name=entity_name,
            entity_lower=entity_lower,
            required_fields=required_fields,
            relation_key=relation_key,
            scope_fields=scope_fields,
        )

        if score > 0:
            candidates.append({
                "operation": op,
                "operation_id": _text(op.get("id")),
                "path": op_path,
                "score": score,
                "score_breakdown": breakdown,
                "parameter_bindings": param_bindings,
            })

    # Sort by score descending
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


def _score_operation(
    op: dict[str, Any],
    *,
    op_path: str,
    entity_name: str,
    entity_lower: str,
    required_fields: list[str],
    relation_key: str,
    scope_fields: list[str],
) -> tuple[float, dict[str, float], list[dict[str, Any]]]:
    """Score an operation for reading a related entity collection.

    Returns (total_score, breakdown, parameter_bindings).
    """
    breakdown: dict[str, float] = {}
    param_bindings: list[dict[str, Any]] = []

    # 1. Entity match: does the path reference this entity?
    path_lower = op_path.lower().replace("_", "").replace("-", "")
    entity_score = 0.0
    if entity_lower and entity_lower in path_lower:
        entity_score = 30.0
    else:
        # Check path segments for partial match
        segments = [s for s in op_path.lower().split("/") if s and not s.startswith("{")]
        for seg in segments:
            seg_clean = seg.replace("_", "").replace("-", "")
            if entity_lower and (entity_lower in seg_clean or seg_clean in entity_lower):
                entity_score = 15.0
                break
    breakdown["entity_match"] = entity_score

    # 2. Collection vs detail: prefer collection endpoints (no path params at end)
    collection_score = 0.0
    path_params = infer_path_params(op_path)
    normalized = normalize_path_placeholders(op_path).rstrip("/")
    coll_path = collection_path(normalized)
    if coll_path and normalized == coll_path:
        collection_score = 20.0  # Pure collection endpoint
    elif not path_params:
        collection_score = 15.0  # No path params, likely collection
    elif len(path_params) == 1:
        collection_score = 5.0  # Single path param, might be detail
    breakdown["collection_endpoint"] = collection_score

    # 3. Field coverage: does response schema cover required fields?
    field_score = 0.0
    response_schema = _dict(op.get("response_schema") or op.get("responseSchema"))
    schema_fields = _extract_schema_fields(response_schema)
    if required_fields and schema_fields:
        covered = sum(1 for f in required_fields if f.lower() in {sf.lower() for sf in schema_fields})
        field_score = min(20.0, 20.0 * covered / len(required_fields))
    elif required_fields:
        field_score = 5.0  # No schema info, give minimal credit
    breakdown["field_coverage"] = field_score

    # 4. Relation filter support: can we filter by relation_key?
    relation_score = 0.0
    if relation_key:
        query_params = _extract_query_params(op)
        param_names = {p.get("name", "").lower() for p in query_params}
        # Check if any param matches relation_key patterns
        relation_key_lower = relation_key.lower().replace("_", "")
        for pname in param_names:
            pname_clean = pname.replace("_", "")
            if relation_key_lower in pname_clean or pname_clean in relation_key_lower:
                relation_score = 25.0
                param_bindings.append({
                    "parameter_name": pname,
                    "canonical_field_id": relation_key,
                    "value_source": {"entity_alias": "root", "field_id": "id"},
                    "confidence": 0.9,
                })
                break
        # Also check for generic filter patterns
        if relation_score == 0:
            for pname in param_names:
                if any(tok in pname for tok in ("filter", "query", "search", "where")):
                    relation_score = 10.0
                    break
    breakdown["relation_filter_support"] = relation_score

    # 5. Tenant scope support: can we filter by tenant?
    scope_score = 0.0
    if scope_fields:
        query_params = _extract_query_params(op)
        param_names = {p.get("name", "").lower() for p in query_params}
        for sf in scope_fields:
            sf_lower = sf.lower()
            for pname in param_names:
                if sf_lower in pname or pname in sf_lower:
                    scope_score = 15.0
                    param_bindings.append({
                        "parameter_name": pname,
                        "canonical_field_id": sf,
                        "value_source": {"entity_alias": "root", "field_id": sf},
                        "confidence": 0.8,
                    })
                    break
            if scope_score > 0:
                break
    breakdown["tenant_filter_support"] = scope_score

    # 6. Read-only safety
    read_write = _text(op.get("read_write")).lower()
    safety_score = 10.0 if read_write == "read" or _text(op.get("method")).upper() == "GET" else 0.0
    breakdown["read_only_safety"] = safety_score

    total = sum(breakdown.values())
    return total, breakdown, param_bindings


def _extract_schema_fields(schema: dict[str, Any]) -> list[str]:
    """Extract field names from a response schema."""
    fields: list[str] = []
    if not schema:
        return fields

    # Handle array items
    if schema.get("type") == "array":
        items = _dict(schema.get("items"))
        return _extract_schema_fields(items)

    # Handle object properties
    props = _dict(schema.get("properties"))
    for name in props:
        fields.append(name)

    # Handle nested data/records/items wrapper
    for wrapper in ("data", "records", "items", "results", "list", "rows"):
        if wrapper in props:
            nested = _dict(props[wrapper])
            if nested.get("type") == "array":
                fields.extend(_extract_schema_fields(_dict(nested.get("items"))))

    return fields


def _extract_query_params(op: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract query parameters from an operation."""
    params: list[dict[str, Any]] = []

    # From parameters array
    for param in _list(op.get("parameters")):
        if not isinstance(param, dict):
            continue
        if _text(param.get("in")).lower() == "query":
            params.append(param)

    # From query_schema
    query_schema = _dict(op.get("query_schema") or op.get("querySchema"))
    if query_schema:
        props = _dict(query_schema.get("properties"))
        for name, prop in props.items():
            params.append({
                "name": name,
                "in": "query",
                "schema": prop if isinstance(prop, dict) else {},
            })

    return params


# ─── Observer Plan Binding ───────────────────────────────────────────────────


def bind_observer_plan(
    observer_requirements: list[dict[str, Any]],
    behavior_ir: dict[str, Any],
    *,
    root_entity_id: str = "",
    root_identity_value: Any = None,
    tenant_scope_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind observer requirements to concrete operations and parameters.

    Returns a bound observer plan with:
    - root_observer: bound root entity observer (if cardinality ONE)
    - related_observers: list of bound related entity observers
    - blockers: list of blocking reasons if binding failed
    """
    operations = _list(behavior_ir.get("operations"))
    entities = {
        _text(e.get("name") or e.get("id")): e
        for e in _list(behavior_ir.get("entities"))
        if isinstance(e, dict)
    }
    relations = _list(behavior_ir.get("relations"))

    result: dict[str, Any] = {
        "root_observer": None,
        "related_observers": [],
        "blockers": [],
        "trace": [],
    }

    tenant_scope_values = tenant_scope_values or {}

    for req in observer_requirements:
        if not isinstance(req, dict):
            continue

        entity_alias = _text(req.get("entity_alias"))
        entity_name = _text(req.get("entity_name"))
        cardinality = _text(req.get("cardinality")).upper()
        relation_key = _text(req.get("relation_key"))
        required_fields = _list(req.get("required_fields"))
        scope_fields = _list(req.get("scope_fields"))
        collection_reqs = _dict(req.get("collection_requirements"))

        if not entity_name:
            result["blockers"].append({
                "entity_alias": entity_alias,
                "reason": "OBSERVER_REQUIREMENT_NOT_COMPILED",
                "detail": "missing entity_name",
            })
            continue

        # Discover read operations
        candidates = discover_read_operations(
            entity_name,
            operations,
            required_fields=required_fields,
            relation_key=relation_key,
            scope_fields=scope_fields,
        )

        trace_entry: dict[str, Any] = {
            "entity_alias": entity_alias,
            "entity_name": entity_name,
            "cardinality": cardinality,
            "candidates_found": len(candidates),
        }

        if not candidates:
            result["blockers"].append({
                "entity_alias": entity_alias,
                "entity_name": entity_name,
                "reason": "RELATED_ENTITY_OPERATION_NOT_FOUND",
                "detail": f"no GET/LIST operation found for {entity_name}",
            })
            trace_entry["status"] = "BLOCKED"
            result["trace"].append(trace_entry)
            continue

        # Select best candidate
        best = candidates[0]
        best_op = best["operation"]
        param_bindings = best["parameter_bindings"]

        # Bind relation key parameter
        relation_bound = False
        if relation_key and root_identity_value is not None:
            for binding in param_bindings:
                if binding.get("canonical_field_id") == relation_key:
                    binding["bound_value"] = root_identity_value
                    relation_bound = True
                    break

            # If not bound via schema, try common patterns
            if not relation_bound:
                query_params = _extract_query_params(best_op)
                for param in query_params:
                    pname = _text(param.get("name")).lower()
                    # Match patterns like budget_id, parent_id, {entity}_id
                    if relation_key.lower().replace("_", "") in pname.replace("_", ""):
                        param_bindings.append({
                            "parameter_name": _text(param.get("name")),
                            "canonical_field_id": relation_key,
                            "bound_value": root_identity_value,
                            "value_source": {"entity_alias": "root", "field_id": "id"},
                            "confidence": 0.7,
                        })
                        relation_bound = True
                        break

        # Bind tenant scope parameters
        scope_bound = False
        if scope_fields and tenant_scope_values:
            for sf in scope_fields:
                if sf in tenant_scope_values:
                    for binding in param_bindings:
                        if binding.get("canonical_field_id") == sf:
                            binding["bound_value"] = tenant_scope_values[sf]
                            scope_bound = True
                            break

        # Build observer entry
        observer_entry: dict[str, Any] = {
            "entity_alias": entity_alias,
            "entity_name": entity_name,
            "entity_id": _text(req.get("entity_id")),
            "cardinality": cardinality,
            "operation_id": best["operation_id"],
            "operation_path": best["path"],
            "operation_method": _text(best_op.get("method")).upper(),
            "score": best["score"],
            "score_breakdown": best["score_breakdown"],
            "parameter_bindings": param_bindings,
            "required_fields": required_fields,
            "scope_fields": scope_fields,
            "identity_fields": _list(req.get("identity_fields")),
            "collection_requirements": collection_reqs,
            "relation_key": relation_key,
            "relation_bound": relation_bound,
            "scope_bound": scope_bound,
            "snapshot": _text(req.get("snapshot")),
        }

        # Check for blocking conditions
        # P7-fix: Allow execution even when relation_key is not bound to query parameter.
        # The executor will filter results client-side by relation_key.
        requires_client_filter = False
        if cardinality == "MANY" and relation_key and not relation_bound:
            # Don't block - allow execution with client-side filtering
            requires_client_filter = True
            observer_entry["requires_client_side_filter"] = True
            observer_entry["client_filter_field"] = relation_key
            observer_entry["client_filter_value"] = root_identity_value
            observer_entry["status"] = "BOUND"
        else:
            observer_entry["status"] = "BOUND"

        if cardinality == "ONE":
            result["root_observer"] = observer_entry
        else:
            result["related_observers"].append(observer_entry)

        trace_entry["status"] = observer_entry["status"]
        trace_entry["selected_operation"] = best["operation_id"]
        trace_entry["score"] = best["score"]
        result["trace"].append(trace_entry)

    return result


# ─── Relation Path Resolution ────────────────────────────────────────────────


def resolve_relation_path(
    from_entity: str,
    to_entity: str,
    relations: list[dict[str, Any]],
    entities: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve relation path between two entities using Behavior IR relations.

    Returns a list of relation hops, or empty list if no path found.
    Supports up to 2 hops (root → related_a → related_b).
    """
    from_lower = from_entity.lower()
    to_lower = to_entity.lower()

    # Direct relation
    for rel in relations:
        if not isinstance(rel, dict):
            continue
        from_ref = _text(rel.get("from_ref")).lower()
        to_ref = _text(rel.get("to_ref")).lower()
        rel_type = _text(rel.get("relation_type"))

        # Check both directions
        if (from_lower in from_ref and to_lower in to_ref) or \
           (to_lower in from_ref and from_lower in to_ref):
            return [{
                "from_entity": from_entity,
                "to_entity": to_entity,
                "relation_type": rel_type,
                "relation_id": _text(rel.get("id")),
                "hop": 1,
            }]

    # Two-hop relation (via intermediate entity)
    for rel1 in relations:
        if not isinstance(rel1, dict):
            continue
        from_ref1 = _text(rel1.get("from_ref")).lower()
        to_ref1 = _text(rel1.get("to_ref")).lower()

        if from_lower not in from_ref1 and from_lower not in to_ref1:
            continue

        # Find intermediate entity
        intermediate = to_ref1 if from_lower in from_ref1 else from_ref1

        for rel2 in relations:
            if not isinstance(rel2, dict):
                continue
            from_ref2 = _text(rel2.get("from_ref")).lower()
            to_ref2 = _text(rel2.get("to_ref")).lower()

            if (intermediate in from_ref2 and to_lower in to_ref2) or \
               (intermediate in to_ref2 and to_lower in from_ref2):
                return [
                    {
                        "from_entity": from_entity,
                        "to_entity": intermediate,
                        "relation_type": _text(rel1.get("relation_type")),
                        "relation_id": _text(rel1.get("id")),
                        "hop": 1,
                    },
                    {
                        "from_entity": intermediate,
                        "to_entity": to_entity,
                        "relation_type": _text(rel2.get("relation_type")),
                        "relation_id": _text(rel2.get("id")),
                        "hop": 2,
                    },
                ]

    return []


# ─── Scope Validation ────────────────────────────────────────────────────────


def validate_collection_scope(
    records: list[dict[str, Any]],
    *,
    scope_fields: list[str],
    expected_scope_values: dict[str, Any],
    entity_alias: str = "",
) -> dict[str, Any]:
    """Validate that all records in a collection match the expected scope.

    Returns validation result with:
    - valid: bool
    - mismatched_records: list of records that don't match scope
    - reason: blocking reason if invalid
    """
    if not scope_fields or not expected_scope_values:
        return {"valid": True, "mismatched_records": [], "reason": ""}

    mismatched: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        for sf in scope_fields:
            expected = expected_scope_values.get(sf)
            if expected is None:
                continue
            actual = record.get(sf)
            if actual is not None and str(actual) != str(expected):
                mismatched.append({
                    "record_id": record.get("id") or record.get("uuid") or "?",
                    "field": sf,
                    "expected": expected,
                    "actual": actual,
                })
                break

    if mismatched:
        return {
            "valid": False,
            "mismatched_records": mismatched[:10],  # Limit for trace size
            "reason": "OBSERVER_SCOPE_MISMATCH",
            "detail": f"{len(mismatched)} records have scope mismatch",
        }

    return {"valid": True, "mismatched_records": [], "reason": ""}
