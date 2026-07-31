"""Runtime contracts for source-backed process execution graphs.

This module is consumed by the existing ``experiment_plan_executor``.  It is
not a second scheduler or transport implementation: it validates graph
dependencies, resolves exact approved targets, and owns the namespaced binding
ledger used by that executor.

Every cross-node value must be source-declared.  No default ``id`` extraction,
latest-record lookup, field-name guessing, or connector endpoint fallback is
allowed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .target_policy import build_target_policy_decision, normalize_base_url

GRAPH_RUNTIME_SCHEMA = "qualibug.process-graph-runtime.v1"
BINDING_LEDGER_SCHEMA = "qualibug.process-graph-binding-ledger.v1"

GRAPH_RUNTIME_INVALID = "PROCESS_GRAPH_RUNTIME_INVALID"
GRAPH_RUNTIME_WAIT_UNSUPPORTED = "PROCESS_GRAPH_WAIT_RUNTIME_NOT_AVAILABLE"
GRAPH_RUNTIME_ASYNC_UNSUPPORTED = "PROCESS_GRAPH_ASYNC_RUNTIME_NOT_AVAILABLE"
GRAPH_TARGET_NOT_APPROVED = "PROCESS_GRAPH_TARGET_NOT_APPROVED"
GRAPH_TARGET_ACTOR_CREDENTIAL_UNRESOLVED = (
    "PROCESS_GRAPH_TARGET_ACTOR_CREDENTIAL_UNRESOLVED"
)
GRAPH_SECONDARY_WRITE_CLEANUP_UNAVAILABLE = (
    "PROCESS_GRAPH_SECONDARY_WRITE_CLEANUP_NOT_AVAILABLE"
)
GRAPH_NODE_COMPENSATION_UNRESOLVED = (
    "PROCESS_GRAPH_NODE_COMPENSATION_UNRESOLVED"
)
GRAPH_PREDECESSOR_NOT_SUCCEEDED = "PROCESS_GRAPH_PREDECESSOR_NOT_SUCCEEDED"
GRAPH_INPUT_BINDING_UNRESOLVED = "PROCESS_GRAPH_INPUT_BINDING_UNRESOLVED"
GRAPH_INPUT_BINDING_CONFLICT = "PROCESS_GRAPH_INPUT_BINDING_CONFLICT"
GRAPH_OUTPUT_BINDING_UNRESOLVED = "PROCESS_GRAPH_OUTPUT_BINDING_UNRESOLVED"

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_ASYNC_RELATIONS = frozenset(
    {"AWAITS", "NOTIFIES", "TRIGGERS", "MESSAGE", "ASYNC_MESSAGE"}
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_hash(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def extract_execution_graph(
    treatment_plan: list[Any],
) -> tuple[dict[str, Any], str]:
    """Return the single graph embedded on every graph-backed treatment step."""
    graphs = [
        _dict(step.get("_execution_graph"))
        for step in treatment_plan
        if isinstance(step, dict) and _dict(step.get("_execution_graph"))
    ]
    if not graphs:
        return {}, ""
    fingerprints = {_stable_hash(graph) for graph in graphs}
    if len(fingerprints) != 1:
        return {}, "execution_graph_fingerprint_mismatch_between_steps"
    return deepcopy(graphs[0]), ""


def _target_rows(runtime_contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    source = runtime_contract.get("approved_targets")
    rows: dict[str, dict[str, Any]] = {}
    if isinstance(source, dict):
        iterator = source.items()
    elif isinstance(source, list):
        iterator = [
            (
                _text(
                    row.get("system_ref")
                    or row.get("target_id")
                    or row.get("approved_target_ref")
                ),
                row,
            )
            for row in source
            if isinstance(row, dict)
        ]
    else:
        iterator = []
    for key, value in iterator:
        if not isinstance(value, dict):
            continue
        ref = _text(
            key
            or value.get("system_ref")
            or value.get("target_id")
            or value.get("approved_target_ref")
        )
        if ref:
            rows[ref] = dict(value)
    return rows


def _primary_aliases(runtime_contract: dict[str, Any]) -> set[str]:
    return {
        value
        for value in (
            "primary",
            _text(runtime_contract.get("system_ref")),
            _text(runtime_contract.get("target_id")),
            _text(runtime_contract.get("approved_target_ref")),
            _text(runtime_contract.get("environment_ref")),
        )
        if value
    }


def _primary_contract(
    runtime_contract: dict[str, Any],
    *,
    base_url: str,
) -> dict[str, Any]:
    row = dict(runtime_contract)
    approved = _text(row.get("approved_base_url")) or _text(base_url)
    requested = _text(row.get("requested_base_url")) or _text(base_url) or approved
    row["approved_base_url"] = approved
    row["requested_base_url"] = requested
    return row


def _target_contract(
    runtime_contract: dict[str, Any],
    *,
    system_ref: str,
    base_url: str,
) -> tuple[dict[str, Any], bool, str]:
    """Resolve one exact approved target; never reads connector endpoints."""
    aliases = _primary_aliases(runtime_contract)
    if not system_ref or system_ref in aliases:
        return _primary_contract(runtime_contract, base_url=base_url), True, ""
    row = _target_rows(runtime_contract).get(system_ref)
    if not row:
        return {}, False, f"approved_target_missing:{system_ref}"
    approved = _text(row.get("approved_base_url"))
    requested = _text(row.get("requested_base_url")) or approved
    if not approved:
        return {}, False, f"approved_base_url_missing:{system_ref}"
    merged = {
        **runtime_contract,
        **row,
        "approved_base_url": approved,
        "requested_base_url": requested,
        "environment_ref": _text(row.get("environment_ref")) or system_ref,
        "status": _text(row.get("status")) or "blocked",
    }
    return merged, False, ""


def _target_decision(contract: dict[str, Any]) -> dict[str, Any]:
    return build_target_policy_decision(
        requested_base_url=contract.get("requested_base_url"),
        approved_base_url=contract.get("approved_base_url"),
        environment_type=(
            contract.get("environment_type")
            or contract.get("environment_kind")
            or contract.get("target_environment")
        ),
        environment_ref=contract.get("environment_ref"),
        execution_mode=contract.get("execution_mode"),
        runtime_status=contract.get("status"),
    )


def _credential_key(
    target_contract: dict[str, Any],
    *,
    actor_ref: str,
    primary: bool,
) -> tuple[str, str]:
    if primary:
        return "", ""
    mapping = _dict(
        target_contract.get("actor_token_keys")
        or target_contract.get("actor_credential_refs")
    )
    key = _text(mapping.get(actor_ref))
    if not key:
        return "", f"actor_credential_ref_missing:{actor_ref}"
    return key, ""


def _graph_nodes(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("node_id") or row.get("step_id")): dict(row)
        for row in _list(graph.get("nodes"))
        if isinstance(row, dict)
        and _text(row.get("node_id") or row.get("step_id"))
    }


def _predecessors(
    graph: dict[str, Any], node_ids: set[str]
) -> tuple[dict[str, list[str]], str]:
    result = {node_id: [] for node_id in node_ids}
    for index, edge in enumerate(_list(graph.get("edges"))):
        if not isinstance(edge, dict):
            continue
        source = _text(edge.get("source_node_id"))
        target = _text(edge.get("target_node_id"))
        if source not in node_ids or target not in node_ids or source == target:
            return {}, f"edge_{index + 1}_endpoint_invalid:{source}->{target}"
        result[target].append(source)
    return result, ""


def _waves(
    order: list[str], predecessors: dict[str, list[str]]
) -> tuple[dict[str, int], str]:
    wave: dict[str, int] = {}
    for node_id in order:
        missing = [ref for ref in predecessors.get(node_id, []) if ref not in wave]
        if missing:
            return {}, f"topological_order_predecessor_missing:{node_id}:{','.join(missing)}"
        wave[node_id] = (
            0
            if not predecessors.get(node_id)
            else 1 + max(wave[ref] for ref in predecessors[node_id])
        )
    return wave, ""


def _binding_refs_for_node(
    graph: dict[str, Any], node: dict[str, Any]
) -> list[dict[str, Any]]:
    refs = [
        dict(row)
        for row in _list(node.get("input_binding_refs"))
        if isinstance(row, dict)
    ]
    node_id = _text(node.get("node_id") or node.get("step_id"))
    for edge in _list(graph.get("edges")):
        if not isinstance(edge, dict) or _text(edge.get("target_node_id")) != node_id:
            continue
        refs.extend(
            dict(row)
            for row in _list(edge.get("binding_refs"))
            if isinstance(row, dict)
        )
    return refs


def prepare_graph_runtime(
    *,
    graph: dict[str, Any],
    treatment_plan: list[Any],
    ops: dict[str, dict[str, Any]],
    base_url: str,
    runtime_contract: dict[str, Any],
) -> dict[str, Any]:
    """Validate the executable synchronous graph before any transport occurs."""
    nodes = _graph_nodes(graph)
    order = [
        _text(value)
        for value in _list(graph.get("topological_order"))
        if _text(value)
    ]
    if not nodes or set(order) != set(nodes) or len(order) != len(nodes):
        return {
            "status": "BLOCKED",
            "reason_code": GRAPH_RUNTIME_INVALID,
            "detail": "graph_nodes_and_topological_order_mismatch",
        }

    plan_ids = {
        _text(step.get("step_id"))
        for step in treatment_plan
        if isinstance(step, dict) and _text(step.get("step_id"))
    }
    if plan_ids != set(nodes):
        return {
            "status": "BLOCKED",
            "reason_code": GRAPH_RUNTIME_INVALID,
            "detail": (
                f"graph_plan_identity_mismatch:"
                f"missing={sorted(set(nodes) - plan_ids)}:"
                f"unexpected={sorted(plan_ids - set(nodes))}"
            ),
        }

    if _list(graph.get("wait_contracts")):
        return {
            "status": "BLOCKED",
            "reason_code": GRAPH_RUNTIME_WAIT_UNSUPPORTED,
            "detail": "source_declared_wait_contract_requires_observer_scheduler",
        }
    async_edges = [
        _text(edge.get("relation_type")).upper()
        for edge in _list(graph.get("edges"))
        if isinstance(edge, dict)
        and _text(edge.get("relation_type")).upper() in _ASYNC_RELATIONS
    ]
    if async_edges:
        return {
            "status": "BLOCKED",
            "reason_code": GRAPH_RUNTIME_ASYNC_UNSUPPORTED,
            "detail": ",".join(sorted(set(async_edges))),
        }

    predecessors, error = _predecessors(graph, set(nodes))
    if error:
        return {
            "status": "BLOCKED",
            "reason_code": GRAPH_RUNTIME_INVALID,
            "detail": error,
        }
    waves, error = _waves(order, predecessors)
    if error:
        return {
            "status": "BLOCKED",
            "reason_code": GRAPH_RUNTIME_INVALID,
            "detail": error,
        }

    target_contexts: dict[str, dict[str, Any]] = {}
    for node_id in order:
        node = nodes[node_id]
        operation_ref = _text(node.get("operation_ref"))
        operation = _dict(ops.get(operation_ref))
        method = _text(node.get("method") or operation.get("method")).upper()
        if method in _WRITE_METHODS:
            compensation_ref = _text(node.get("compensation_operation_ref"))
            if not compensation_ref or compensation_ref not in ops:
                return {
                    "status": "BLOCKED",
                    "reason_code": GRAPH_NODE_COMPENSATION_UNRESOLVED,
                    "detail": (
                        f"{node_id}:{operation_ref}:"
                        f"{compensation_ref or 'missing_compensation_operation_ref'}"
                    ),
                }
        system_ref = _text(node.get("system_ref"))
        contract, primary, error = _target_contract(
            runtime_contract,
            system_ref=system_ref,
            base_url=base_url,
        )
        if error:
            return {
                "status": "BLOCKED",
                "reason_code": GRAPH_TARGET_NOT_APPROVED,
                "detail": f"{node_id}:{error}",
            }
        decision = _target_decision(contract)
        if not decision.get("read_allowed"):
            return {
                "status": "BLOCKED",
                "reason_code": GRAPH_TARGET_NOT_APPROVED,
                "detail": (
                    f"{node_id}:{system_ref or 'primary'}:"
                    f"{','.join(decision.get('blocking_codes') or [])}"
                ),
            }
        approved_url = normalize_base_url(decision.get("approved_base_url"))
        primary_url = normalize_base_url(
            _text(runtime_contract.get("approved_base_url")) or base_url
        )
        if method in _WRITE_METHODS and approved_url != primary_url:
            return {
                "status": "BLOCKED",
                "reason_code": GRAPH_SECONDARY_WRITE_CLEANUP_UNAVAILABLE,
                "detail": f"{node_id}:{system_ref}:{operation_ref}",
            }
        credential_key, error = _credential_key(
            contract,
            actor_ref=_text(node.get("actor_ref")),
            primary=primary,
        )
        if error:
            return {
                "status": "BLOCKED",
                "reason_code": GRAPH_TARGET_ACTOR_CREDENTIAL_UNRESOLVED,
                "detail": f"{node_id}:{system_ref}:{error}",
            }
        target_contexts[node_id] = {
            "system_ref": system_ref,
            "primary": primary,
            "base_url": approved_url,
            "runtime_contract": contract,
            "target_policy_decision": decision,
            "credential_token_key": credential_key,
        }

    return {
        "schema_version": GRAPH_RUNTIME_SCHEMA,
        "status": "READY",
        "execution_graph_id": _text(
            graph.get("execution_graph_id") or graph.get("process_id")
        ),
        "process_id": _text(graph.get("process_id")),
        "topological_order": order,
        "predecessors": predecessors,
        "wave_by_node": waves,
        "nodes": nodes,
        "target_contexts": target_contexts,
        "node_status": {node_id: "PENDING" for node_id in order},
        "binding_ledger": {
            "schema_version": BINDING_LEDGER_SCHEMA,
            "execution_graph_id": _text(
                graph.get("execution_graph_id") or graph.get("process_id")
            ),
            "outputs_by_node": {},
            "consumptions": [],
            "unresolved": [],
        },
    }


def _lookup_output(runtime: dict[str, Any], producer: str, field: str) -> Any:
    row = _dict(
        _dict(runtime.get("binding_ledger"))
        .get("outputs_by_node", {})
        .get(producer, {})
    ).get(field)
    if isinstance(row, dict) and "value" in row:
        return row["value"]
    return None


def graph_step_context(
    *,
    runtime: dict[str, Any],
    graph: dict[str, Any],
    step: dict[str, Any],
    initial_bindings: dict[str, Any],
) -> dict[str, Any]:
    """Resolve one READY node without exposing another node's flat namespace."""
    node_id = _text(step.get("step_id"))
    predecessors = _list(_dict(runtime.get("predecessors")).get(node_id))
    failed = [
        ref
        for ref in predecessors
        if _text(_dict(runtime.get("node_status")).get(ref)) != "SUCCEEDED"
    ]
    if failed:
        return {
            "status": "BLOCKED",
            "reason_code": GRAPH_PREDECESSOR_NOT_SUCCEEDED,
            "detail": f"{node_id}:{','.join(failed)}",
        }

    node = _dict(_dict(runtime.get("nodes")).get(node_id))
    bindings = dict(initial_bindings)
    consumptions: list[dict[str, Any]] = []
    for index, ref in enumerate(_binding_refs_for_node(graph, node)):
        producer = _text(
            ref.get("producer_node_id")
            or ref.get("source_node_id")
            or ref.get("producer_step_id")
        )
        source_field = _text(
            ref.get("producer_output_field")
            or ref.get("source_field")
            or ref.get("canonical_field_id")
        )
        target = _text(
            ref.get("target")
            or ref.get("consumer_target")
            or ref.get("target_location")
            or source_field
        )
        if not producer or not source_field or not target:
            return {
                "status": "BLOCKED",
                "reason_code": GRAPH_INPUT_BINDING_UNRESOLVED,
                "detail": f"{node_id}:binding_{index + 1}_identity_incomplete",
            }
        value = _lookup_output(runtime, producer, source_field)
        if value in (None, "", [], {}):
            return {
                "status": "BLOCKED",
                "reason_code": GRAPH_INPUT_BINDING_UNRESOLVED,
                "detail": f"{node_id}:{producer}.{source_field}->{target}",
            }
        if target in bindings and bindings[target] != value:
            return {
                "status": "BLOCKED",
                "reason_code": GRAPH_INPUT_BINDING_CONFLICT,
                "detail": f"{node_id}:{target}",
            }
        bindings[target] = value
        consumptions.append(
            {
                "consumer_node_id": node_id,
                "producer_node_id": producer,
                "producer_output_field": source_field,
                "consumer_target": target,
                "value_fingerprint": _stable_hash(value),
            }
        )
    _dict(runtime.get("binding_ledger")).setdefault("consumptions", []).extend(
        consumptions
    )
    return {
        "status": "READY",
        "node_id": node_id,
        "wave_index": int(_dict(runtime.get("wave_by_node")).get(node_id) or 0),
        "bindings": bindings,
        **_dict(_dict(runtime.get("target_contexts")).get(node_id)),
    }


def _response_value(body: Any, path: str) -> Any:
    token = _text(path)
    if not token:
        return None
    if token.startswith("$."):
        parts = [part for part in token[2:].split(".") if part]
    elif token.startswith("/"):
        parts = [
            part.replace("~1", "/").replace("~0", "~")
            for part in token.split("/")[1:]
        ]
    else:
        return None
    current = body
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def record_graph_step_outcome(
    *,
    runtime: dict[str, Any],
    graph: dict[str, Any],
    step: dict[str, Any],
    observation: dict[str, Any] | None = None,
    blocked_reason: str = "",
) -> dict[str, Any]:
    """Record status and source-declared outputs for one node."""
    node_id = _text(step.get("step_id"))
    statuses = _dict(runtime.get("node_status"))
    if blocked_reason:
        statuses[node_id] = "BLOCKED"
        return {"status": "BLOCKED", "node_id": node_id}

    obs = _dict(observation)
    status_code = int(obs.get("status_code") or obs.get("status") or 0)
    succeeded = 200 <= status_code < 300
    statuses[node_id] = "SUCCEEDED" if succeeded else "FAILED"
    if not succeeded:
        return {
            "status": statuses[node_id],
            "node_id": node_id,
            "status_code": status_code,
        }

    node = _dict(_dict(runtime.get("nodes")).get(node_id))
    output_rows: dict[str, dict[str, Any]] = {}
    body = obs.get("body")
    unresolved: list[str] = []
    for index, spec in enumerate(_list(node.get("output_binding_specs"))):
        if not isinstance(spec, dict):
            continue
        canonical = _text(
            spec.get("canonical_field_id")
            or spec.get("output_field")
            or spec.get("field")
        )
        source_path = _text(
            spec.get("json_path")
            or spec.get("source_path")
            or spec.get("response_field")
        )
        if not canonical or not source_path:
            unresolved.append(f"output_{index + 1}_identity_incomplete")
            continue
        value = _response_value(body, source_path)
        if value in (None, "", [], {}):
            unresolved.append(f"{canonical}:{source_path}")
            continue
        output_rows[canonical] = {
            "value": value,
            "producer_node_id": node_id,
            "canonical_field_id": canonical,
            "source_path": source_path,
            "system_ref": _text(node.get("system_ref")),
            "object_refs": _list(node.get("object_refs")),
            "response_fingerprint": _stable_hash(body),
        }

    ledger = _dict(runtime.get("binding_ledger"))
    ledger.setdefault("outputs_by_node", {})[node_id] = output_rows
    if unresolved:
        ledger.setdefault("unresolved", []).append(
            {
                "node_id": node_id,
                "reason_code": GRAPH_OUTPUT_BINDING_UNRESOLVED,
                "details": unresolved,
            }
        )
    return {
        "status": statuses[node_id],
        "node_id": node_id,
        "status_code": status_code,
        "output_binding_count": len(output_rows),
        "output_binding_unresolved": unresolved,
    }


__all__ = [
    "GRAPH_RUNTIME_SCHEMA",
    "BINDING_LEDGER_SCHEMA",
    "GRAPH_RUNTIME_INVALID",
    "GRAPH_RUNTIME_WAIT_UNSUPPORTED",
    "GRAPH_RUNTIME_ASYNC_UNSUPPORTED",
    "GRAPH_TARGET_NOT_APPROVED",
    "GRAPH_TARGET_ACTOR_CREDENTIAL_UNRESOLVED",
    "GRAPH_SECONDARY_WRITE_CLEANUP_UNAVAILABLE",
    "GRAPH_NODE_COMPENSATION_UNRESOLVED",
    "GRAPH_PREDECESSOR_NOT_SUCCEEDED",
    "GRAPH_INPUT_BINDING_UNRESOLVED",
    "GRAPH_INPUT_BINDING_CONFLICT",
    "GRAPH_OUTPUT_BINDING_UNRESOLVED",
    "extract_execution_graph",
    "prepare_graph_runtime",
    "graph_step_context",
    "record_graph_step_outcome",
]
