"""Project durable formal-event identities from real observer receipts into evidence graphs.

This projection consumes only typed receipts already returned by the execution authority. It
creates no finding and copies no raw request, response, event payload, event id, correlation
value, credential, secret or database row.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .formal_event_binding_receipt_bridge import EVIDENCE_IDENTITY_KEY
from .formal_event_surface import OBSERVER_ID

SCHEMA_VERSION = "qualibug.formal-event-binding-evidence.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in _list(value) if isinstance(row, dict)]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(prefix: str, value: Any) -> str:
    blob = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"{prefix}_{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:24]}"


def _execution_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _dict(result.get("experiment_execution")).get("results")
    if isinstance(raw, dict):
        return [
            dict(row)
            for _, row in sorted(raw.items(), key=lambda item: str(item[0]))
            if isinstance(row, dict)
        ]
    return _rows(raw)


def _event_identities(row: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    identities: list[tuple[dict[str, Any], str]] = []
    for receipt in _rows(row.get("observer_receipts")):
        if _text(receipt.get("observer_id")) != OBSERVER_ID:
            continue
        identity = copy.deepcopy(
            _dict(_dict(receipt.get("evidence")).get(EVIDENCE_IDENTITY_KEY))
        )
        if _text(identity.get("status")) != "BOUND":
            continue
        identities.append((identity, _text(receipt.get("receipt_id"))))
    return identities


def _identity_nodes(identity: dict[str, Any]) -> list[dict[str, Any]]:
    definitions = (
        ("implementation_binding_ref", "implementation_binding"),
        ("action_surface_binding_ref", "action_surface_binding"),
        ("observer_binding_ref", "observer_binding"),
        ("scenario_ref", "scenario_ir"),
        ("runtime_plan_ref", "runtime_plan"),
        ("runtime_materialization_ref", "runtime_materialization"),
    )
    nodes = [
        {
            "node_id": _text(identity.get(key)),
            "node_type": node_type,
            "binding_authority": _text(identity.get("binding_authority")),
        }
        for key, node_type in definitions
        if _text(identity.get(key))
    ]
    nodes.extend(
        {
            "node_id": ref,
            "node_type": "contract_field_binding",
            "binding_authority": _text(identity.get("binding_authority")),
        }
        for ref in [
            _text(value)
            for value in _list(identity.get("contract_field_binding_refs"))
            if _text(value)
        ]
    )
    nodes.extend(
        {
            "node_id": ref,
            "node_type": "runtime_value_binding",
            "binding_authority": _text(identity.get("binding_authority")),
        }
        for ref in [
            _text(value)
            for value in _list(identity.get("runtime_value_binding_refs"))
            if _text(value)
        ]
    )
    return nodes


def _identity_edges(
    identity: dict[str, Any], receipt_ref: str
) -> list[dict[str, str]]:
    implementation = _text(identity.get("implementation_binding_ref"))
    action = _text(identity.get("action_surface_binding_ref"))
    observer = _text(identity.get("observer_binding_ref"))
    scenario = _text(identity.get("scenario_ref"))
    plan = _text(identity.get("runtime_plan_ref"))
    materialization = _text(identity.get("runtime_materialization_ref"))
    rows = [
        ("receipt_proves_observer_binding", receipt_ref, observer),
        ("implementation_binding_to_action_surface", implementation, action),
        ("implementation_binding_to_observer", implementation, observer),
        ("scenario_to_runtime_plan", scenario, plan),
        ("runtime_plan_to_materialization", plan, materialization),
        ("runtime_plan_uses_action_surface", plan, action),
        ("runtime_plan_uses_observer", plan, observer),
        ("materialization_preserves_observer", materialization, observer),
    ]
    edges = [
        {"edge_type": relation, "from_ref": source, "to_ref": target}
        for relation, source, target in rows
        if source and target
    ]
    for ref in [
        _text(value)
        for value in _list(identity.get("contract_field_binding_refs"))
        if _text(value)
    ]:
        if plan:
            edges.append({
                "edge_type": "runtime_plan_uses_contract_field",
                "from_ref": plan,
                "to_ref": ref,
            })
    for ref in [
        _text(value)
        for value in _list(identity.get("runtime_value_binding_refs"))
        if _text(value)
    ]:
        if materialization:
            edges.append({
                "edge_type": "materialization_uses_runtime_value",
                "from_ref": materialization,
                "to_ref": ref,
            })
    return edges


def project_formal_event_binding_evidence(
    result: dict[str, Any],
) -> dict[str, Any]:
    """Attach identity topology only when a real event receipt proves it."""
    if not isinstance(result, dict):
        raise TypeError("discovery_result_not_object")
    projected = copy.deepcopy(result)
    execution_by_id = {
        _text(row.get("experiment_id")): row
        for row in _execution_rows(projected)
        if _text(row.get("experiment_id"))
    }
    graphs: list[dict[str, Any]] = []
    identity_count = 0
    graph_count = 0
    experiment_ids: set[str] = set()
    for raw_graph in _rows(projected.get("evidence_graphs")):
        graph = dict(raw_graph)
        experiment_id = _text(graph.get("experiment_id"))
        identities = _event_identities(execution_by_id.get(experiment_id) or {})
        nodes = _rows(graph.get("nodes"))
        edges = _rows(graph.get("edges"))
        existing_nodes = {_text(row.get("node_id")) for row in nodes}
        existing_edges = {
            (
                _text(row.get("edge_type")),
                _text(row.get("from_ref")),
                _text(row.get("to_ref")),
            )
            for row in edges
        }
        added = 0
        for identity, receipt_ref in identities:
            for node in _identity_nodes(identity):
                node_id = _text(node.get("node_id"))
                if node_id and node_id not in existing_nodes:
                    nodes.append(node)
                    existing_nodes.add(node_id)
                    added += 1
            for edge in _identity_edges(identity, receipt_ref):
                key = (
                    _text(edge.get("edge_type")),
                    _text(edge.get("from_ref")),
                    _text(edge.get("to_ref")),
                )
                if all(key) and key not in existing_edges:
                    edges.append(edge)
                    existing_edges.add(key)
            identity_count += 1
            experiment_ids.add(experiment_id)
        graph["nodes"] = nodes
        graph["edges"] = edges
        coverage = dict(_dict(graph.get("coverage")))
        coverage["formal_event_binding_identity_count"] = len(identities)
        coverage["durable_binding_identity_node_count"] = added
        graph["coverage"] = coverage
        if identities:
            graph["formal_event_binding_identity_projected"] = True
            graph_count += 1
        graphs.append(graph)
    projected["evidence_graphs"] = graphs

    summaries: list[dict[str, Any]] = []
    for raw in _rows(projected.get("execution_trace_summaries")):
        summary = dict(raw)
        experiment_id = _text(summary.get("experiment_id"))
        count = len(_event_identities(execution_by_id.get(experiment_id) or {}))
        summary["formal_event_binding_identity_count"] = count
        summary["formal_event_binding_identity_closed"] = count > 0
        summaries.append(summary)
    projected["execution_trace_summaries"] = summaries

    receipt = {
        "schema_version": SCHEMA_VERSION,
        "status": "PROJECTED" if identity_count else "NO_EVENT_IDENTITY_RECEIPT",
        "identity_receipt_count": identity_count,
        "evidence_graph_count": graph_count,
        "experiment_count": len(experiment_ids),
        "new_findings_created": 0,
        "raw_payloads_included": False,
        "credentials_included": False,
        "identity_reselection_used": False,
    }
    receipt["receipt_id"] = _stable_id(
        "formal_event_binding_evidence_receipt", receipt
    )
    projected["formal_event_binding_evidence_receipt"] = receipt
    return projected


__all__ = [
    "SCHEMA_VERSION",
    "project_formal_event_binding_evidence",
]
