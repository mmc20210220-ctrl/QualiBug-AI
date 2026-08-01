"""Project explicit role inheritance contracts onto the existing permission matrix."""
from __future__ import annotations

from typing import Any, Iterable

from .role_inheritance_contract_authority import (
    RECEIPT_SCHEMA,
    _ALL_SCOPES,
    _canonical_scope,
    _dict,
    _list,
    _norm,
    _stable_id,
    _text,
)


def _scope_intersection(permission_scope: Any, inheritance_scopes: Iterable[Any]) -> str | None:
    current = _canonical_scope(permission_scope)
    for raw_scope in inheritance_scopes:
        scope = _canonical_scope(raw_scope)
        if scope == "unspecified":
            continue
        if current == "unspecified":
            current = scope
            continue
        if current == scope:
            continue
        if current in _ALL_SCOPES:
            current = scope
            continue
        if scope in _ALL_SCOPES:
            continue
        return None
    return current


def _cycle_nodes(graph: dict[str, list[str]]) -> set[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    cycles: set[str] = set()

    def visit(node: str, path: list[str]) -> None:
        if node in visiting:
            index = path.index(node) if node in path else 0
            cycles.update(path[index:])
            return
        if node in visited:
            return
        visiting.add(node)
        path.append(node)
        for parent in graph.get(node, []):
            visit(parent, path)
        path.pop()
        visiting.remove(node)
        visited.add(node)

    for node in sorted(graph):
        visit(node, [])
    return cycles


def apply_role_inheritance_permissions(asset: dict[str, Any]) -> dict[str, Any]:
    """Expand role permissions through explicit acyclic hierarchy contracts."""
    contracts = [
        dict(row)
        for row in _list(asset.get("role_inheritance_contracts"))
        if isinstance(row, dict)
        and _text(row.get("status")).upper() == "RESOLVED"
        and row.get("source_backed") is True
    ]
    rows = [
        dict(row) for row in _list(asset.get("permission_matrix")) if isinstance(row, dict)
    ]
    direct_rows = [
        row
        for row in rows
        if _text(row.get("authorization_kind"))
        not in {"delegated_permission", "role_inherited_permission"}
    ]
    delegated_rows = [
        row for row in rows if _text(row.get("authorization_kind")) == "delegated_permission"
    ]
    graph: dict[str, list[str]] = {}
    edge_contracts: dict[tuple[str, str], list[dict[str, Any]]] = {}
    display_roles: dict[str, str] = {}
    for contract in contracts:
        child = _text(contract.get("inheriting_role"))
        parent = _text(contract.get("inherited_role"))
        child_key, parent_key = _norm(child), _norm(parent)
        if not child_key or not parent_key or child_key == parent_key:
            continue
        display_roles.setdefault(child_key, child)
        display_roles.setdefault(parent_key, parent)
        graph.setdefault(child_key, []).append(parent_key)
        edge_contracts.setdefault((child_key, parent_key), []).append(contract)
    graph = {key: sorted(set(values)) for key, values in graph.items()}
    cycles = _cycle_nodes(graph)

    gaps = [
        dict(row) for row in _list(asset.get("coverage_gaps")) if isinstance(row, dict)
    ]
    if cycles:
        gaps.append(
            {
                "kind": "role_inheritance_cycle_detected",
                "gap_type": "role_inheritance_cycle_detected",
                "roles": sorted(display_roles.get(role, role) for role in cycles),
                "automatic_resolution_allowed": False,
            }
        )

    path_rows: list[tuple[str, str, list[str], list[dict[str, Any]]]] = []

    def walk(child: str, current: str, path: list[str], path_contracts: list[dict[str, Any]]) -> None:
        for parent in graph.get(current, []):
            if child in cycles or parent in cycles or parent in path:
                continue
            contracts_for_edge = sorted(
                edge_contracts.get((current, parent), []),
                key=lambda row: _text(row.get("contract_id")),
            )
            if not contracts_for_edge:
                continue
            selected = contracts_for_edge[0]
            next_path = [*path, parent]
            next_contracts = [*path_contracts, selected]
            path_rows.append((child, parent, next_path, next_contracts))
            walk(child, parent, next_path, next_contracts)

    for child in sorted(graph):
        walk(child, child, [child], [])

    generated: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    for child_key, ancestor_key, path, path_contracts in path_rows:
        child = display_roles.get(child_key, child_key)
        ancestor = display_roles.get(ancestor_key, ancestor_key)
        for source_row in direct_rows:
            if _norm(source_row.get("role")) != ancestor_key:
                continue
            decision = _text(source_row.get("decision") or source_row.get("effect")).lower()
            if decision not in {"allow", "deny"}:
                continue
            effective_scope = _scope_intersection(
                source_row.get("scope"),
                [contract.get("scope") for contract in path_contracts],
            )
            if effective_scope is None:
                gaps.append(
                    {
                        "kind": "role_inheritance_scope_disjoint",
                        "gap_type": "role_inheritance_scope_disjoint",
                        "inheriting_role": child,
                        "inherited_role": ancestor,
                        "source_permission_id": _text(source_row.get("permission_id")),
                        "inheritance_contract_ids": [
                            _text(row.get("contract_id")) for row in path_contracts
                        ],
                    }
                )
                continue
            permission_id = _stable_id(
                "role_inherited_permission",
                child,
                ancestor,
                _text(source_row.get("permission_id")),
                effective_scope,
                *(_text(row.get("contract_id")) for row in path_contracts),
            )
            inherited = {
                **source_row,
                "permission_id": permission_id,
                "role": child,
                "scope": effective_scope,
                "authorization_kind": "role_inherited_permission",
                "authorization_derivation": "explicit_role_inheritance",
                "inherited_from_role": ancestor,
                "inheritance_path": [display_roles.get(role, role) for role in path],
                "inheritance_contract_ids": [
                    _text(row.get("contract_id")) for row in path_contracts
                ],
                "source_permission_id": _text(source_row.get("permission_id")),
                "source_backed": True,
            }
            generated.append(inherited)
            receipt_payload = {
                "schema_version": RECEIPT_SCHEMA,
                "permission_id": permission_id,
                "inheriting_role": child,
                "inherited_role": ancestor,
                "inheritance_path": inherited["inheritance_path"],
                "inheritance_contract_ids": inherited["inheritance_contract_ids"],
                "source_permission_id": inherited["source_permission_id"],
                "effective_scope": effective_scope,
                "decision": decision.upper(),
            }
            receipts.append(
                {
                    **receipt_payload,
                    "receipt_id": _stable_id(
                        "role_inheritance_receipt",
                        permission_id,
                        effective_scope,
                        *receipt_payload["inheritance_contract_ids"],
                    ),
                }
            )

    generated_by_id = {
        _text(row.get("permission_id")): row
        for row in generated
        if _text(row.get("permission_id"))
    }
    receipt_by_id = {
        _text(row.get("receipt_id")): row
        for row in receipts
        if _text(row.get("receipt_id"))
    }
    asset["permission_matrix"] = [
        *direct_rows,
        *delegated_rows,
        *sorted(generated_by_id.values(), key=lambda row: _text(row.get("permission_id"))),
    ]
    asset["role_inheritance_receipts"] = sorted(
        receipt_by_id.values(), key=lambda row: _text(row.get("receipt_id"))
    )
    asset["coverage_gaps"] = gaps
    summary = _dict(asset.get("summary"))
    summary["role_inheritance_contract_count"] = len(contracts)
    summary["role_inherited_permission_count"] = len(generated_by_id)
    summary["role_inheritance_cycle_count"] = len(cycles)
    asset["summary"] = summary
    governance = _dict(asset.get("governance"))
    governance.update(
        {
            "role_inheritance_requires_explicit_source_authority": True,
            "role_inheritance_name_or_seniority_inference_allowed": False,
            "role_inheritance_never_widens_scope": True,
            "delegated_permission_is_not_role_inheritable": True,
            "role_inheritance_cycles_fail_closed": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["apply_role_inheritance_permissions"]
