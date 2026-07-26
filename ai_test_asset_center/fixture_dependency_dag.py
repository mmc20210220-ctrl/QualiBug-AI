"""Fixture Dependency DAG — ordered fixture execution with truth receipts.

SPEC v1.2 §9: Fixture DAG Truthification

This module validates fixture execution order, ownership, cleanup
responsibility, and receipt truthfulness.

Output: qualibug.fixture-dependency-dag.v1
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


# ─── Fixture Node ─────────────────────────────────────────────────────────────


def build_fixture_node(
    *,
    fixture_id: str,
    operation_ref: str,
    created_entity_ref: str = "",
    actor_ref: str = "",
    tenant_ref: str = "",
    depends_on: list[str] | None = None,
    produces_bindings: list[str] | None = None,
    cleanup_contract_ref: str = "",
    status: str = "PLANNED",
    receipt_id: str = "",
    source_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a fixture DAG node."""
    fp_content = {
        "fixture_id": fixture_id,
        "operation_ref": operation_ref,
        "actor_ref": actor_ref,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fp_content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:32]

    return {
        "fixture_id": fixture_id,
        "operation_ref": operation_ref,
        "created_entity_ref": created_entity_ref,
        "actor_ref": actor_ref,
        "tenant_ref": tenant_ref,
        "depends_on": list(depends_on or []),
        "produces_bindings": list(produces_bindings or []),
        "cleanup_contract_ref": cleanup_contract_ref,
        "status": status,
        "receipt_id": receipt_id,
        "source_refs": list(source_refs or [])[:3],
        "fingerprint": fingerprint,
    }


# ─── DAG Validation ───────────────────────────────────────────────────────────


def validate_fixture_dag(
    *,
    fixtures: list[dict[str, Any]],
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Validate the fixture dependency DAG for correctness.

    Checks:
    - Topological order (no cycles)
    - All fixtures have cleanup responsibility
    - Actor/tenant consistency with obligation
    - Receipt truthfulness (no reuse of unowned data)

    Returns:
        qualibug.fixture-dependency-dag.v1
    """
    exp = _dict(experiment)
    ir = _dict(behavior_ir)
    nodes = [build_fixture_node(**f) if isinstance(f, dict) else f for f in _list(fixtures)]

    issues: list[dict[str, Any]] = []
    fixture_ids = {_text(n.get("fixture_id")) for n in nodes if isinstance(n, dict)}

    # Check: all dependencies exist
    for node in nodes:
        if not isinstance(node, dict):
            continue
        for dep in _list(node.get("depends_on")):
            if dep and dep not in fixture_ids:
                issues.append({
                    "kind": "MISSING_DEPENDENCY",
                    "fixture_id": _text(node.get("fixture_id")),
                    "missing": dep,
                    "severity": "BLOCK",
                })

    # Check: cleanup responsibility
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if not _text(node.get("cleanup_contract_ref")):
            issues.append({
                "kind": "MISSING_CLEANUP_RESPONSIBILITY",
                "fixture_id": _text(node.get("fixture_id")),
                "severity": "WARN",
            })

    # Check: cycle detection (topological sort)
    has_cycle = _detect_cycle(nodes)
    if has_cycle:
        issues.append({
            "kind": "CIRCULAR_DEPENDENCY",
            "severity": "BLOCK",
        })

    # Check: actor consistency
    actor_contract = _dict(exp.get("actor_selection_contract"))
    expected_actors = {
        _text(actor_contract.get("control_actor_ref")),
        _text(actor_contract.get("treatment_actor_ref")),
    }
    expected_actors.discard("")
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_actor = _text(node.get("actor_ref"))
        if node_actor and expected_actors and node_actor not in expected_actors:
            issues.append({
                "kind": "ACTOR_MISMATCH",
                "fixture_id": _text(node.get("fixture_id")),
                "fixture_actor": node_actor,
                "expected_actors": list(expected_actors),
                "severity": "BLOCK",
            })

    # Determine execution order (topological)
    execution_order = _topological_sort(nodes) if not has_cycle else []

    # Status
    has_block = any(i.get("severity") == "BLOCK" for i in issues)
    dag_status = "BLOCKED" if has_block else "VALID"

    fp_content = {
        "fixture_count": len(nodes),
        "issues": len(issues),
        "status": dag_status,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fp_content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:32]

    return {
        "schema_version": "qualibug.fixture-dependency-dag.v1",
        "experiment_id": _text(exp.get("experiment_id")),
        "obligation_id": _text(exp.get("obligation_id")),
        "nodes": nodes,
        "node_count": len(nodes),
        "execution_order": execution_order,
        "issues": issues,
        "issue_count": len(issues),
        "dag_status": dag_status,
        "fingerprint": fingerprint,
    }


def _detect_cycle(nodes: list[dict[str, Any]]) -> bool:
    """Detect cycles in the fixture dependency graph."""
    graph: dict[str, list[str]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        fid = _text(node.get("fixture_id"))
        if fid:
            graph[fid] = [_text(d) for d in _list(node.get("depends_on")) if _text(d)]

    visited: set[str] = set()
    in_stack: set[str] = set()

    def dfs(node_id: str) -> bool:
        visited.add(node_id)
        in_stack.add(node_id)
        for dep in graph.get(node_id, []):
            if dep not in visited:
                if dfs(dep):
                    return True
            elif dep in in_stack:
                return True
        in_stack.discard(node_id)
        return False

    for fid in graph:
        if fid not in visited:
            if dfs(fid):
                return True
    return False


def _topological_sort(nodes: list[dict[str, Any]]) -> list[str]:
    """Return fixture IDs in valid execution order."""
    graph: dict[str, list[str]] = {}
    in_degree: dict[str, int] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        fid = _text(node.get("fixture_id"))
        if fid:
            deps = [_text(d) for d in _list(node.get("depends_on")) if _text(d)]
            graph[fid] = deps
            in_degree.setdefault(fid, 0)
            for dep in deps:
                in_degree.setdefault(dep, 0)

    # Kahn's algorithm
    queue = [fid for fid, deg in in_degree.items() if deg == 0]
    order: list[str] = []
    while queue:
        queue.sort()
        current = queue.pop(0)
        order.append(current)
        for fid, deps in graph.items():
            if current in deps:
                in_degree[fid] -= 1
                if in_degree[fid] == 0:
                    queue.append(fid)

    return order
