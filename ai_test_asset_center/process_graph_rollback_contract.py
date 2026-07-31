"""Dependency contract for process-graph rollback.

The process graph remains the topology authority and the existing graph cleanup
executor remains the compensation authority. This module only freezes which
already-declared write cleanups must complete before an ancestor write may be
compensated.

No rollback path, compensator or binding is discovered here.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .process_graph_read_runtime import _predecessors


SCHEMA_VERSION = "qualibug.process-graph-rollback-contract.v1"
STATUS_FROZEN = "FROZEN"
STATUS_BLOCKED = "BLOCKED"
ROLLBACK_CONTRACT_INVALID = "PROCESS_GRAPH_ROLLBACK_CONTRACT_INVALID"
ROLLBACK_CONTRACT_DRIFT = "PROCESS_GRAPH_ROLLBACK_CONTRACT_DRIFT"

SAFE_ROLLBACK_OUTCOMES = frozenset({"COMPLETED", "NOT_REQUIRED"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def freeze_process_graph_rollback_contract(
    graph: dict[str, Any],
    write_contract: dict[str, Any],
) -> dict[str, Any]:
    """Freeze reverse-dependency gates over the declared write cleanup order."""
    source_graph = _dict(graph)
    source_contract = _dict(write_contract)
    nodes = {
        _text(row.get("node_id") or row.get("step_id"))
        for row in _list(source_graph.get("nodes"))
        if isinstance(row, dict)
        and _text(row.get("node_id") or row.get("step_id"))
    }
    order = [
        _text(value)
        for value in _list(source_graph.get("topological_order"))
        if _text(value)
    ]
    write_step_ids = [
        _text(value)
        for value in _list(source_contract.get("write_step_ids"))
        if _text(value)
    ]
    cleanup_steps = [
        dict(row)
        for row in _list(source_contract.get("cleanup_steps"))
        if isinstance(row, dict)
    ]
    cleanup_order = [
        _text(row.get("source_step_id")) for row in cleanup_steps
    ]

    issues: list[str] = []
    if not nodes or set(order) != nodes or len(order) != len(nodes):
        issues.append("graph_nodes_and_topological_order_mismatch")
    if len(write_step_ids) != len(set(write_step_ids)):
        issues.append("write_step_ids_not_unique")
    if not set(write_step_ids).issubset(nodes):
        issues.append("write_step_outside_graph")
    if any(not value for value in cleanup_order):
        issues.append("cleanup_source_step_missing")
    if cleanup_order != list(reversed(write_step_ids)):
        issues.append("cleanup_order_not_reverse_write_topology")
    if len(cleanup_order) != len(set(cleanup_order)):
        issues.append("cleanup_source_step_not_unique")

    predecessors: dict[str, set[str]] = {}
    if not issues:
        predecessors, error = _predecessors(source_graph, nodes)
        if error:
            issues.append(error)

    ancestors: dict[str, set[str]] = {node_id: set() for node_id in order}
    if not issues:
        for node_id in order:
            for predecessor in predecessors.get(node_id, set()):
                ancestors[node_id].add(predecessor)
                ancestors[node_id].update(ancestors.get(predecessor, set()))

    downstream_by_source: dict[str, list[str]] = {}
    direct_downstream_by_source: dict[str, list[str]] = {}
    if not issues:
        write_set = set(write_step_ids)
        for source_step_id in write_step_ids:
            downstream_by_source[source_step_id] = [
                candidate
                for candidate in cleanup_order
                if candidate in write_set
                and source_step_id in ancestors.get(candidate, set())
            ]
            direct_downstream_by_source[source_step_id] = [
                candidate
                for candidate in cleanup_order
                if candidate in write_set
                and source_step_id in predecessors.get(candidate, set())
            ]

    payload = {
        "execution_graph_id": _text(
            source_graph.get("execution_graph_id")
            or source_graph.get("process_id")
        ),
        "process_graph_write_contract_id": _text(
            source_contract.get("contract_id")
        ),
        "write_step_ids": write_step_ids,
        "cleanup_order": cleanup_order,
        "downstream_write_step_ids_by_source": downstream_by_source,
        "direct_downstream_write_step_ids_by_source": (
            direct_downstream_by_source
        ),
        "safe_prerequisite_outcomes": sorted(SAFE_ROLLBACK_OUTCOMES),
        "topology_fingerprint": _fingerprint(
            {
                "topological_order": order,
                "predecessors": {
                    node_id: sorted(values)
                    for node_id, values in predecessors.items()
                },
            }
        )
        if not issues
        else "",
    }
    fingerprint = _fingerprint(payload)
    if issues:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": STATUS_BLOCKED,
            "reason_code": ROLLBACK_CONTRACT_INVALID,
            "detail": ";".join(issues),
            "issues": issues,
            "contract_fingerprint": fingerprint,
            **payload,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": STATUS_FROZEN,
        "contract_fingerprint": fingerprint,
        **payload,
    }


def validate_process_graph_rollback_contract(
    graph: dict[str, Any],
    write_contract: dict[str, Any],
    rollback_contract: dict[str, Any],
) -> tuple[bool, str]:
    """Recompute the deterministic contract and reject runtime drift."""
    stored = _dict(rollback_contract)
    if _text(stored.get("status")) != STATUS_FROZEN:
        return False, "rollback_contract_not_frozen"
    rebuilt = freeze_process_graph_rollback_contract(graph, write_contract)
    if _text(rebuilt.get("status")) != STATUS_FROZEN:
        return False, _text(rebuilt.get("detail")) or "rollback_contract_rebuild_failed"
    if _text(stored.get("contract_fingerprint")) != _text(
        rebuilt.get("contract_fingerprint")
    ):
        return False, "rollback_contract_fingerprint_mismatch"
    comparable_keys = (
        "execution_graph_id",
        "process_graph_write_contract_id",
        "write_step_ids",
        "cleanup_order",
        "downstream_write_step_ids_by_source",
        "direct_downstream_write_step_ids_by_source",
        "safe_prerequisite_outcomes",
        "topology_fingerprint",
    )
    if any(stored.get(key) != rebuilt.get(key) for key in comparable_keys):
        return False, "rollback_contract_payload_mismatch"
    return True, ""


__all__ = [
    "ROLLBACK_CONTRACT_DRIFT",
    "ROLLBACK_CONTRACT_INVALID",
    "SAFE_ROLLBACK_OUTCOMES",
    "SCHEMA_VERSION",
    "STATUS_BLOCKED",
    "STATUS_FROZEN",
    "freeze_process_graph_rollback_contract",
    "validate_process_graph_rollback_contract",
]
