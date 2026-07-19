"""Unified enterprise test data constructor.

Single entry point for ALL test data construction needs. Handles:

1. Parameter classification — what each field IS (FK/enum/constrained/semantic/free)
2. Dependency tree building — recursive FK resolution up to 15 levels deep
3. Value generation — business-correct values for every category
4. FK substitution — inject resolved IDs into dependent bodies
5. Execution planning — topologically-sorted creation order
6. Cleanup planning — reverse-order teardown
7. Response extraction — get created IDs from API responses

This module is the SOLE authority for test data construction. The experiment
executor calls `plan_prerequisite_data()` and receives a complete, executable
plan. No other module should independently construct test data.

Schema: qualibug.enterprise-test-data-constructor.v1
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict, deque
from typing import Any


SCHEMA_VERSION = "qualibug.enterprise-test-data-constructor.v1"

MAX_DEPTH = 12
MAX_NODES = 40


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}

def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []

def _text(value: Any) -> str:
    return str(value or "").strip()


# ═══════════════════════════════════════════════════════════════════════
# Unified Planning Function
# ═══════════════════════════════════════════════════════════════════════

def plan_prerequisite_data(
    target_operation: dict[str, Any],
    behavior_ir: dict[str, Any],
    *,
    max_depth: int = MAX_DEPTH,
) -> dict[str, Any]:
    """Plan all prerequisite data needed to execute a target operation.

    This is the SINGLE entry point. It:
    1. Classifies every field in the target's request body
    2. Recursively resolves FK dependencies into a tree
    3. Generates valid values for every field in every node
    4. Builds a topologically-sorted execution plan
    5. Plans cleanup in reverse order

    Args:
        target_operation: The operation that needs prerequisite data.
            Must have: method, path, request_schema (or requestBody).
        behavior_ir: The Behavior IR with all available operations.

    Returns:
        {
            "target": {operation info},
            "total_prerequisites": N,
            "max_depth": D,
            "execution_plan": [{step...}],  # ordered, dependencies first
            "cleanup_plan": [{step...}],    # reverse order
            "data_flow": {node_id: {field: source_node_id}},
            "diagnostics": {...}
        }
    """
    from .parameter_value_classifier import (
        classify_request_body,
        generate_classified_body,
    )
    from .real_id_resolver_base import normalize_path_placeholders, infer_path_params

    all_operations = _list(behavior_ir.get("operations", []))
    ops_by_id = {_text(o.get("id")): o for o in all_operations if _text(o.get("id"))}
    ops_by_path: dict[str, dict[str, Any]] = {}
    for o in all_operations:
        key = f"{_text(o.get('method')).upper()}:{normalize_path_placeholders(_text(o.get('path') or o.get('raw_path')))}"
        ops_by_path[key] = o

    target_schema = _get_operation_schema(target_operation)
    target_classification = classify_request_body(target_schema) if target_schema else {}
    target_body = generate_classified_body(target_schema) if target_schema else {}

    # ── Detect path-parameter dependencies ──
    # Path parameters (like :id in /api/orders/:id/cancel) are implicit
    # dependencies — they need the parent resource to exist first.
    target_path = normalize_path_placeholders(
        _text(target_operation.get("path") or target_operation.get("raw_path"))
    )
    path_params = infer_path_params(target_path)
    # For each path param, determine the entity it references
    # e.g., :id on /api/orders/:id/cancel → entity "order"
    #       :userId on /api/users/:userId/activate → entity "user"
    path_param_deps: dict[str, str] = {}
    if path_params:
        parent_entity = _entity_name(target_operation)
        for param in path_params:
            if param.lower() in ("id", "key", "uuid", "code", "ref"):
                # Generic ID param → references the parent collection entity
                path_param_deps[param] = parent_entity
            else:
                # Named param like :userId → references "user"
                clean = param.lower().replace("_id", "").replace("id", "").replace("_", "")
                if clean:
                    path_param_deps[param] = clean

    # Merge path-param deps into classification
    if path_param_deps:
        if not target_classification:
            target_classification = {"fields": {}, "dependency_fields": [], "enum_fields": [], "constrained_fields": [], "summary": {}}
        fields = target_classification.get("fields", {})
        for param, entity in path_param_deps.items():
            if param not in fields:
                fields[param] = {
                    "field": param,
                    "category": "DEPENDENCY",
                    "is_fk": True,
                    "fk_entity": entity,
                    "generator": "path_param_dep",
                    "reason": f"path_param→{entity}",
                }
        target_classification["fields"] = fields
        target_classification["dependency_fields"] = list(
            set(target_classification.get("dependency_fields", []) + list(path_param_deps.keys()))
        )

    # ── Build dependency tree ──
    tree_nodes, tree_edges, diagnostics = _build_full_dependency_tree(
        target_operation, target_classification, all_operations,
        ops_by_id, ops_by_path, max_depth=max_depth,
    )

    # ── Topological sort ──
    sorted_nodes = _topological_sort(tree_nodes, tree_edges)

    # ── Build execution plan ──
    execution_plan, data_flow = _build_execution_steps(
        sorted_nodes, tree_edges, ops_by_id,
    )

    # ── Build cleanup plan (reverse order) ──
    cleanup_plan = _build_cleanup_steps(sorted_nodes, ops_by_id)

    target_entity = _entity_name(target_operation)

    return {
        "schema_version": SCHEMA_VERSION,
        "target": {
            "entity": target_entity,
            "method": _text(target_operation.get("method")),
            "path": _text(target_operation.get("path") or target_operation.get("raw_path")),
            "body": target_body,
            "dependency_fields": target_classification.get("dependency_fields", []),
        },
        "total_prerequisites": len([n for n in sorted_nodes if n["node_id"] != "root"]),
        "max_depth": max((n["depth"] for n in sorted_nodes), default=0),
        "execution_plan": execution_plan,
        "cleanup_plan": cleanup_plan,
        "data_flow": data_flow,
        "diagnostics": diagnostics,
    }


# ═══════════════════════════════════════════════════════════════════════
# Internal: Schema extraction
# ═══════════════════════════════════════════════════════════════════════

def _get_operation_schema(op: dict[str, Any]) -> dict[str, Any]:
    """Extract JSON Schema from an operation's request body definition.

    Handles common OpenAPI patterns:
    - request_schema.properties (flat)
    - request_schema.content.application/json.schema.properties (nested)
    - requestBody (raw OpenAPI requestBody object)
    """
    schema = _dict(op.get("request_schema") or op.get("requestBody"))

    # Direct properties
    if schema.get("properties"):
        return schema

    # Nested: content → application/json → schema
    content = _dict(schema.get("content", {}))
    for mt in ("application/json", "*/*"):
        media = content.get(mt, {})
        if isinstance(media, dict):
            inner = _dict(media.get("schema", {}))
            if inner.get("properties"):
                return inner

    # requestBody-based
    rb = _dict(op.get("requestBody"))
    if rb.get("properties"):
        return rb
    rb_content = _dict(rb.get("content", {}))
    for mt in ("application/json", "*/*"):
        media = rb_content.get(mt, {})
        if isinstance(media, dict):
            inner = _dict(media.get("schema", {}))
            if inner.get("properties"):
                return inner

    # Fallback: return whatever we have
    return schema


# ═══════════════════════════════════════════════════════════════════════
# Internal: Entity name extraction
# ═══════════════════════════════════════════════════════════════════════

def _entity_name(op: dict[str, Any]) -> str:
    """Derive entity name from operation path."""
    from .real_id_resolver_base import normalize_path_placeholders
    path = normalize_path_placeholders(
        _text(op.get("path") or op.get("raw_path"))
    ).strip("/")
    action_words = {"create", "update", "delete", "cancel", "confirm", "ship",
                    "approve", "reject", "pay", "validate", "register", "login",
                    "admin", "list", "search", "query", "add", "remove"}
    segments = [s for s in path.split("/") if s and "{" not in s]
    meaningful = [s for s in segments if s.lower() not in action_words]
    return (meaningful[-1] if meaningful else segments[-1]) if segments else "resource"


# ═══════════════════════════════════════════════════════════════════════
# Internal: Full Dependency Tree Builder
# ═══════════════════════════════════════════════════════════════════════

def _build_full_dependency_tree(
    target_op: dict[str, Any],
    target_classification: dict[str, Any],
    all_operations: list[dict[str, Any]],
    ops_by_id: dict[str, dict[str, Any]],
    ops_by_path: dict[str, dict[str, Any]],
    *,
    max_depth: int = MAX_DEPTH,
) -> tuple[list[dict[str, Any]], list[tuple[str, str]], dict[str, Any]]:
    """Build complete dependency tree with classified values."""

    from .parameter_value_classifier import (
        classify_request_body,
        generate_classified_body,
    )
    from .real_id_resolver_base import normalize_path_placeholders

    nodes: list[dict[str, Any]] = []
    edges: list[tuple[str, str]] = []
    visited: set[str] = set()
    diagnostics: dict[str, Any] = {
        "total_dependencies_found": 0,
        "unresolved_dependencies": [],
        "cycles_detected": [],
        "max_depth_reached": 0,
    }

    # Root node is the target operation
    root_id = "root"
    target_deps = target_classification.get("dependency_fields", [])
    nodes.append({
        "node_id": root_id,
        "entity": _entity_name(target_op),
        "depth": 0,
        "is_target": True,
        "operation": target_op,
        "body": generate_classified_body(_get_operation_schema(target_op)) if _get_operation_schema(target_op) else {},
        "dependency_fields": target_deps,
        "classification": target_classification,
    })

    def _resolve_fk_to_operation(fk_entity: str) -> dict[str, Any] | None:
        """Find the POST operation that creates a given entity."""
        entity_lower = fk_entity.lower().rstrip("s")
        best: dict[str, Any] | None = None
        best_score = -1

        for op in all_operations:
            if _text(op.get("method")).upper() != "POST":
                continue
            path = normalize_path_placeholders(
                _text(op.get("path") or op.get("raw_path"))
            ).lower()
            op_entity = _entity_name(op).lower().rstrip("s")

            if op_entity == entity_lower:
                score = 100
            elif entity_lower in path:
                score = 60
            elif op_entity and entity_lower in op_entity:
                score = 40
            else:
                continue

            # Prefer operations with schemas
            schema = _get_operation_schema(op)
            if schema.get("properties"):
                score += 20

            # Prefer shorter paths
            score -= len(path) // 10

            if score > best_score:
                best_score = score
                best = op

        return best

    # BFS to build tree
    queue: deque[str] = deque([root_id])
    node_map: dict[str, dict[str, Any]] = {root_id: nodes[0]}

    while queue:
        current_id = queue.popleft()
        current = node_map[current_id]
        if current["depth"] >= max_depth:
            diagnostics["max_depth_reached"] = max(diagnostics["max_depth_reached"], current["depth"])
            continue
        if len(nodes) >= MAX_NODES:
            break

        for fk_field in current.get("dependency_fields", []):
            fk_classification = current.get("classification", {}).get("fields", {}).get(fk_field, {})
            fk_entity = fk_classification.get("fk_entity", "")
            if not fk_entity:
                continue

            entity_key = f"{fk_entity}:{fk_field}"
            if entity_key in visited:
                continue
            visited.add(entity_key)

            create_op = _resolve_fk_to_operation(fk_entity)
            if not create_op:
                diagnostics["unresolved_dependencies"].append({
                    "field": fk_field,
                    "entity": fk_entity,
                    "parent_node": current_id,
                })
                continue

            create_schema = _get_operation_schema(create_op)
            create_classification = classify_request_body(create_schema) if create_schema else {}
            create_body = generate_classified_body(create_schema) if create_schema else {}

            child_id = f"node_{len(nodes)}"
            child = {
                "node_id": child_id,
                "entity": fk_entity,
                "depth": current["depth"] + 1,
                "is_target": False,
                "operation": create_op,
                "body": create_body,
                "dependency_fields": create_classification.get("dependency_fields", []),
                "classification": create_classification,
                "provides_field": "id",
                "parent_field": fk_field,
                "parent_node": current_id,
            }
            nodes.append(child)
            node_map[child_id] = child
            edges.append((child_id, current_id))  # child → parent (child must be created first)
            queue.append(child_id)
            diagnostics["total_dependencies_found"] += 1

    return nodes, edges, diagnostics


# ═══════════════════════════════════════════════════════════════════════
# Internal: Topological Sort
# ═══════════════════════════════════════════════════════════════════════

def _topological_sort(
    nodes: list[dict[str, Any]],
    edges: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Sort nodes so dependencies are created before dependents."""
    node_map = {n["node_id"]: n for n in nodes}
    in_degree: dict[str, int] = {n["node_id"]: 0 for n in nodes}
    adj: dict[str, list[str]] = defaultdict(list)

    for from_id, to_id in edges:
        adj[from_id].append(to_id)
        in_degree[to_id] += 1

    # Kahn's algorithm
    q: deque[str] = deque(nid for nid, deg in in_degree.items() if deg == 0)
    result: list[dict[str, Any]] = []

    while q:
        nid = q.popleft()
        result.append(node_map[nid])
        for neighbor in adj.get(nid, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                q.append(neighbor)

    # Add any remaining (cycles) sorted by depth
    remaining = [n for n in nodes if n["node_id"] not in {r["node_id"] for r in result}]
    remaining.sort(key=lambda n: -n["depth"])
    result.extend(remaining)

    return result


# ═══════════════════════════════════════════════════════════════════════
# Internal: Build Execution Steps
# ═══════════════════════════════════════════════════════════════════════

def _build_execution_steps(
    sorted_nodes: list[dict[str, Any]],
    edges: list[tuple[str, str]],
    ops_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """Build executable steps with FK substitution data flow."""

    from .real_id_resolver_base import normalize_path_placeholders

    steps: list[dict[str, Any]] = []
    data_flow: dict[str, dict[str, str]] = {}
    resolved_ids: dict[str, str] = {}

    # Build parent map for FK substitution
    parent_map: dict[str, str] = {}
    for from_id, to_id in edges:
        parent_map[to_id] = from_id  # to_id depends on from_id

    for node in sorted_nodes:
        if node["is_target"]:
            # Target operation — substitute all resolved FKs
            body = dict(node["body"])
            for fk_field in node.get("dependency_fields", []):
                fk_class = node.get("classification", {}).get("fields", {}).get(fk_field, {})
                fk_entity = fk_class.get("fk_entity", "")
                if fk_entity and fk_entity in resolved_ids:
                    body[fk_field] = resolved_ids[fk_entity]
            steps.append({
                "step": "target",
                "entity": node["entity"],
                "method": _text(node["operation"].get("method")),
                "path": _text(node["operation"].get("path") or node["operation"].get("raw_path")),
                "body": body,
                "extract_response_field": node.get("provides_field", "id"),
            })
            continue

        op = node["operation"]
        op_path = normalize_path_placeholders(_text(op.get("path") or op.get("raw_path")))

        # Substitute FK values from already-resolved dependencies
        body = dict(node["body"])
        for fk_field in node.get("dependency_fields", []):
            fk_class = node.get("classification", {}).get("fields", {}).get(fk_field, {})
            fk_entity = fk_class.get("fk_entity", "")
            if fk_entity and fk_entity in resolved_ids:
                body[fk_field] = resolved_ids[fk_entity]

        steps.append({
            "step": f"create_{node['entity']}",
            "entity": node["entity"],
            "depth": node["depth"],
            "method": "POST",
            "path": op_path,
            "body": body,
            "extract_response_field": "id",
            "store_as": node["entity"],
        })

        # Data flow: this node's parent will receive its ID
        if node.get("parent_node"):
            parent_field = node.get("parent_field", "id")
            if node["parent_node"] not in data_flow:
                data_flow[node["parent_node"]] = {}
            data_flow[node["parent_node"]][parent_field] = node["entity"]

        # Pre-register a placeholder ID (will be replaced by actual response)
        resolved_ids[node["entity"]] = f"<{node['entity']}_id>"

    return steps, data_flow


# ═══════════════════════════════════════════════════════════════════════
# Internal: Build Cleanup Steps (reverse order)
# ═══════════════════════════════════════════════════════════════════════

def _build_cleanup_steps(
    sorted_nodes: list[dict[str, Any]],
    ops_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build cleanup steps in reverse creation order."""
    from .real_id_resolver_base import normalize_path_placeholders

    cleanup: list[dict[str, Any]] = []
    for node in reversed(sorted_nodes):
        if node["is_target"]:
            continue
        op = node["operation"]
        op_path = normalize_path_placeholders(
            _text(op.get("path") or op.get("raw_path"))
        )
        # Try to find a DELETE for this entity
        delete_op = _find_delete_operation(node["entity"], ops_by_id)
        if delete_op:
            cleanup.append({
                "step": f"cleanup_{node['entity']}",
                "entity": node["entity"],
                "method": "DELETE",
                "path": normalize_path_placeholders(
                    _text(delete_op.get("path") or delete_op.get("raw_path"))
                ),
                "body": {},
                "requires_resource_id": True,
            })
        else:
            cleanup.append({
                "step": f"cleanup_{node['entity']}",
                "entity": node["entity"],
                "method": "NONE",
                "path": "",
                "body": {},
                "note": "no_delete_endpoint_available_db_reset_will_handle",
            })
    return cleanup


def _find_delete_operation(
    entity_name: str,
    ops_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Find a DELETE operation for an entity."""
    from .real_id_resolver_base import normalize_path_placeholders

    entity_lower = entity_name.lower().rstrip("s")
    best: dict[str, Any] | None = None
    best_score = -1

    for op in ops_by_id.values():
        if _text(op.get("method")).upper() != "DELETE":
            continue
        path = normalize_path_placeholders(
            _text(op.get("path") or op.get("raw_path"))
        ).lower()
        if entity_lower in path:
            score = len(entity_lower) / max(1, len(path))
            if score > best_score:
                best_score = score
                best = op

    return best


# ═══════════════════════════════════════════════════════════════════════
# Runtime: Execute the plan (called by fixture materializer)
# ═══════════════════════════════════════════════════════════════════════

def execute_prerequisite_plan(
    plan: dict[str, Any],
    *,
    execute_write: Any,  # callable(base_url, method, path, body, token) → response_dict
    base_url: str = "",
    token: str = "",
) -> dict[str, Any]:
    """Execute a prerequisite data plan produced by plan_prerequisite_data().

    Args:
        plan: Output from plan_prerequisite_data().
        execute_write: Function to execute an HTTP write.
        base_url: Target base URL.
        token: Auth token.

    Returns:
        {
            "created_resources": {entity_name: resource_id},
            "steps_executed": N,
            "steps_failed": N,
            "target_body": {final body with all FKs resolved},
            "target_path": resolved path,
        }
    """
    resolved: dict[str, Any] = {}
    executed = 0
    failed = 0

    for step in plan.get("execution_plan", []):
        if step["step"] == "target":
            continue  # handled last

        body = dict(step.get("body", {}))
        # Substitute already-resolved FKs
        for key, val in list(body.items()):
            if isinstance(val, str) and val.startswith("<") and val.endswith("_id>"):
                entity = val[1:-4]  # strip < and _id>
                if entity in resolved:
                    body[key] = resolved[entity]

        try:
            result = execute_write(
                base_url=base_url,
                method=step["method"],
                path=step["path"],
                body=body,
                token=token,
            )
        except Exception:
            failed += 1
            continue

        status = int(_dict(result).get("status") or _dict(result).get("status_code") or 0)
        if 200 <= status < 300:
            executed += 1
            # Extract resource ID from response
            from .enterprise_test_data_engine import extract_resource_id
            resp_body = _dict(result.get("body") or result.get("response_body"))
            rid = extract_resource_id(resp_body, step["entity"])
            if rid:
                resolved[step["entity"]] = rid
        else:
            failed += 1

    # Build final target body with all resolved FKs
    target_step = next(
        (s for s in plan.get("execution_plan", []) if s["step"] == "target"),
        {},
    )
    target_body = dict(target_step.get("body", {}))
    target_path = target_step.get("path", "")
    for key, val in list(target_body.items()):
        if isinstance(val, str) and val.startswith("<") and val.endswith("_id>"):
            entity = val[1:-4]
            if entity in resolved:
                target_body[key] = resolved[entity]
                target_path = target_path.replace(
                    "{" + key + "}", str(resolved[entity])
                ).replace(":" + key, str(resolved[entity]))

    return {
        "created_resources": resolved,
        "steps_executed": executed,
        "steps_failed": failed,
        "target_body": target_body,
        "target_path": target_path,
    }
