"""Enterprise recursive resource dependency resolver.

Handles deeply nested API dependency chains common in enterprise systems:
  POST /api/orders/:id/cancel
    → needs order_id → POST /api/orders
      → needs user_id → POST /api/users
        → needs role_id → POST /api/roles
          → needs tenant_id → POST /api/tenants
            → ... (up to 15 levels deep)

Builds a topologically-sorted DAG of prerequisite resource creations,
executes them in order, and feeds created resource IDs into dependent
request bodies through response-driven binding.

Schema: qualibug.enterprise-resource-dependency-resolver.v1
"""

from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from typing import Any


SCHEMA_VERSION = "qualibug.enterprise-resource-dependency-resolver.v1"

MAX_DEPENDENCY_DEPTH = 15
MAX_TOTAL_NODES = 50


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}

def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []

def _text(value: Any) -> str:
    return str(value or "").strip()


# ═══════════════════════════════════════════════════════════════════════
# Dependency Node
# ═══════════════════════════════════════════════════════════════════════

class DependencyNode:
    """One resource that must exist before the target operation can execute."""

    __slots__ = (
        "entity_name", "create_operation", "body_schema", "body",
        "fk_fields", "dependencies", "provides_field",
        "depth", "node_id", "resolved_id",
    )

    def __init__(
        self,
        entity_name: str,
        create_operation: dict[str, Any] | None = None,
        body_schema: dict[str, Any] | None = None,
        provides_field: str = "id",
        depth: int = 0,
    ) -> None:
        self.entity_name = entity_name
        self.create_operation = create_operation or {}
        self.body_schema = body_schema or {}
        self.body: dict[str, Any] = {}
        self.fk_fields: list[dict[str, Any]] = []
        self.dependencies: list[DependencyNode] = []
        self.provides_field = provides_field
        self.depth = depth
        self.node_id = _text(
            (create_operation or {}).get("id")
            or hashlib.sha256(entity_name.encode()).hexdigest()[:16]
        )
        self.resolved_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_name": self.entity_name,
            "node_id": self.node_id,
            "operation_path": _text(self.create_operation.get("path", "")),
            "operation_method": _text(self.create_operation.get("method", "")),
            "depth": self.depth,
            "provides_field": self.provides_field,
            "fk_fields": [f["field"] for f in self.fk_fields],
            "dependency_count": len(self.dependencies),
            "resolved_id": self.resolved_id,
        }


# ═══════════════════════════════════════════════════════════════════════
# Dependency Tree Builder
# ═══════════════════════════════════════════════════════════════════════

def _entity_name_from_path(path: str) -> str:
    """Extract entity name from an API path.

    /api/orders/{id}/cancel → orders
    /api/users → users
    /api/product/admin → product
    """
    from .real_id_resolver_base import normalize_path_placeholders

    normalized = normalize_path_placeholders(path).strip("/")
    segments = [s for s in normalized.split("/") if s and s not in ("api", "v1", "v2", "v3") and "{" not in s]
    if not segments:
        return "resource"
    # Prefer the last non-action segment
    action_words = {"create", "update", "delete", "cancel", "confirm", "ship",
                    "approve", "reject", "pay", "validate", "register", "login",
                    "admin", "list", "search", "query"}
    meaningful = [s for s in segments if s.lower() not in action_words]
    return meaningful[-1] if meaningful else segments[-1]


def _find_create_operation_for_entity(
    entity_name: str,
    operations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the POST operation that creates the given entity."""
    from .real_id_resolver_base import normalize_path_placeholders
    from .enterprise_test_data_engine import find_create_operation

    entity_lower = entity_name.lower().rstrip("s")  # de-pluralize

    best: dict[str, Any] | None = None
    best_score = -1

    for op in operations:
        if not isinstance(op, dict):
            continue
        if _text(op.get("method")).upper() != "POST":
            continue
        path = normalize_path_placeholders(
            _text(op.get("path") or op.get("raw_path"))
        ).lower()
        op_entity = _entity_name_from_path(path)

        # Direct entity name match
        if op_entity == entity_lower or op_entity.rstrip("s") == entity_lower:
            score = 100
        # Entity appears in path
        elif entity_lower in path:
            score = 50
        # Operation has request_example or schema (can generate body)
        elif op.get("request_example") or op.get("request_schema"):
            score = 10
        else:
            continue

        # Bonus for having body schema
        if _dict(op.get("request_schema", {})).get("properties"):
            score += 20
        if op.get("request_example"):
            score += 10

        # Prefer shorter paths (more likely to be the collection endpoint)
        score -= len(path) // 10

        if score > best_score:
            best_score = score
            best = op

    # Fallback: use find_create_operation with collection path
    if best is None and entity_lower:
        collection = f"/api/{entity_lower}"
        best = find_create_operation(collection, operations)

    return best


def _get_request_schema(operation: dict[str, Any]) -> dict[str, Any]:
    """Extract JSON Schema for the request body from an operation."""
    schema = _dict(
        operation.get("request_schema")
        or operation.get("requestBody")
    )
    if schema.get("properties"):
        return schema
    # Check nested content
    content = _dict(schema.get("content", {}))
    for media_type in ("application/json", "*/*"):
        media = content.get(media_type, {})
        if isinstance(media, dict):
            inner = _dict(media.get("schema", {}))
            if inner.get("properties"):
                return inner
    return schema


def build_dependency_tree(
    target_operation: dict[str, Any],
    operations: list[dict[str, Any]],
    *,
    max_depth: int = MAX_DEPENDENCY_DEPTH,
    max_nodes: int = MAX_TOTAL_NODES,
) -> DependencyNode:
    """Build a complete dependency tree for a target operation.

    Recursively analyzes the request body schema to find all FK fields,
    then finds create operations for each FK entity, and recursively
    resolves THEIR dependencies.

    Args:
        target_operation: The operation to build dependencies for.
        operations: All available API operations (from Behavior IR).
        max_depth: Maximum recursion depth.
        max_nodes: Maximum total nodes in the tree.

    Returns the root DependencyNode with the full dependency tree.
    """
    from .enterprise_test_data_engine import detect_foreign_key_fields, generate_request_body

    visited_entities: set[str] = set()
    total_nodes = [0]  # mutable counter

    def _build_node(
        operation: dict[str, Any],
        entity_name: str,
        depth: int,
    ) -> DependencyNode:
        if total_nodes[0] >= max_nodes or depth > max_depth:
            return DependencyNode(entity_name, operation, depth=depth)

        total_nodes[0] += 1
        entity_key = entity_name.lower().rstrip("s")
        if entity_key in visited_entities:
            # Already being resolved — return leaf node to prevent cycle
            return DependencyNode(entity_name, operation, depth=depth)
        visited_entities.add(entity_key)

        schema = _get_request_schema(operation)
        node = DependencyNode(
            entity_name=entity_name,
            create_operation=operation,
            body_schema=schema,
            depth=depth,
        )

        # Generate body
        if schema.get("properties"):
            node.body = generate_request_body(schema)

        # Detect FK fields
        node.fk_fields = detect_foreign_key_fields(schema)
        if not node.fk_fields:
            return node

        # For each FK field, find the create operation and recurse
        for fk in node.fk_fields:
            fk_entity = fk.get("fk_entity", "")
            if not fk_entity:
                continue

            create_op = _find_create_operation_for_entity(fk_entity, operations)
            if create_op and _text(create_op.get("id")) != _text(operation.get("id")):
                child = _build_node(create_op, fk_entity, depth + 1)
                child.provides_field = "id"  # The child provides its ID
                node.dependencies.append(child)

        return node

    root_entity = _entity_name_from_path(
        _text(target_operation.get("path") or target_operation.get("raw_path"))
    )
    return _build_node(target_operation, root_entity, 0)


# ═══════════════════════════════════════════════════════════════════════
# Topological Sort
# ═══════════════════════════════════════════════════════════════════════

def topological_sort(root: DependencyNode) -> list[DependencyNode]:
    """Topologically sort the dependency tree so dependencies are created first.

    Uses Kahn's algorithm (BFS). The root node is the last to execute.
    """
    # Collect all nodes
    all_nodes: list[DependencyNode] = []
    seen_ids: set[str] = set()

    def collect(node: DependencyNode) -> None:
        if node.node_id in seen_ids:
            return
        seen_ids.add(node.node_id)
        for dep in node.dependencies:
            collect(dep)
        all_nodes.append(node)

    collect(root)

    # Build adjacency and in-degree
    node_map = {n.node_id: n for n in all_nodes}
    in_degree: dict[str, int] = {n.node_id: 0 for n in all_nodes}
    adj: dict[str, list[str]] = defaultdict(list)

    for node in all_nodes:
        for dep in node.dependencies:
            adj[dep.node_id].append(node.node_id)
            in_degree[node.node_id] += 1

    # Kahn's algorithm
    queue: deque[DependencyNode] = deque(
        node_map[nid] for nid, deg in in_degree.items() if deg == 0
    )
    sorted_nodes: list[DependencyNode] = []

    while queue:
        current = queue.popleft()
        sorted_nodes.append(current)
        for neighbor_id in adj.get(current.node_id, []):
            in_degree[neighbor_id] -= 1
            if in_degree[neighbor_id] == 0:
                queue.append(node_map[neighbor_id])

    # If not all nodes are sorted, there's a cycle — fall back to depth order
    if len(sorted_nodes) != len(all_nodes):
        # Reverse depth order: deepest first
        all_nodes.sort(key=lambda n: -n.depth)
        return all_nodes

    return sorted_nodes


# ═══════════════════════════════════════════════════════════════════════
# Dependency Chain Execution Plan
# ═══════════════════════════════════════════════════════════════════════

def build_execution_plan(root: DependencyNode) -> dict[str, Any]:
    """Build an executable plan for the dependency chain.

    Returns:
    {
      "nodes": [...],          # topologically sorted node descriptions
      "total_depth": int,      # max depth in the tree
      "total_nodes": int,      # total resource creations needed
      "execution_order": [...], # ordered list of {entity, path, method, body}
      "bindings": {...},       # field → dependency mapping for data flow
    }
    """
    sorted_nodes = topological_sort(root)

    execution_order: list[dict[str, Any]] = []
    bindings: dict[str, list[str]] = defaultdict(list)

    for node in sorted_nodes:
        plan_step = {
            "entity": node.entity_name,
            "node_id": node.node_id,
            "depth": node.depth,
            "path": _text(node.create_operation.get("path", "")),
            "method": _text(node.create_operation.get("method", "POST")),
            "body": node.body,
            "provides_field": node.provides_field,
            "depends_on": [d.node_id for d in node.dependencies],
        }
        execution_order.append(plan_step)

        # Map which nodes provide values for which FK fields
        for dep in node.dependencies:
            for fk in node.fk_fields:
                fk_entity = fk.get("fk_entity", "")
                if fk_entity.lower().rstrip("s") == dep.entity_name.lower().rstrip("s"):
                    bindings[node.node_id].append(f"{fk['field']}←{dep.node_id}.{dep.provides_field}")

    max_depth = max((n.depth for n in sorted_nodes), default=0)

    return {
        "schema_version": SCHEMA_VERSION,
        "total_depth": max_depth,
        "total_nodes": len(sorted_nodes),
        "execution_order": execution_order,
        "bindings": dict(bindings),
        "nodes": [n.to_dict() for n in sorted_nodes],
    }


# ═══════════════════════════════════════════════════════════════════════
# FK Substitution: inject resolved IDs into dependent bodies
# ═══════════════════════════════════════════════════════════════════════

def substitute_fk_values(
    body: dict[str, Any],
    fk_values: dict[str, Any],
    fk_fields: list[dict[str, Any]],
) -> dict[str, Any]:
    """Substitute resolved FK values into a request body.

    Args:
        body: The generated request body.
        fk_values: Dict of field_name → resolved value.
        fk_fields: FK field descriptors from detect_foreign_key_fields().

    Returns a new body with FK fields replaced by resolved values.
    """
    result = dict(body)
    for fk in fk_fields:
        field = fk["field"]
        if field in fk_values and fk_values[field] is not None:
            result[field] = fk_values[field]
    return result


# ═══════════════════════════════════════════════════════════════════════
# Convenience: full dependency resolution for an operation
# ═══════════════════════════════════════════════════════════════════════

def resolve_operation_dependencies(
    operation: dict[str, Any],
    behavior_ir: dict[str, Any],
    *,
    max_depth: int = MAX_DEPENDENCY_DEPTH,
) -> dict[str, Any]:
    """Full dependency resolution for a target operation.

    Args:
        operation: The target operation (e.g., POST /api/orders/:id/cancel).
        behavior_ir: The Behavior IR model with all operations.

    Returns the execution plan with all prerequisite resource creations.
    """
    all_operations = _list(behavior_ir.get("operations", []))
    if not all_operations:
        return {"schema_version": SCHEMA_VERSION, "total_nodes": 0, "execution_order": []}

    tree = build_dependency_tree(operation, all_operations, max_depth=max_depth)
    return build_execution_plan(tree)
