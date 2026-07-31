"""Governed runtime facade for source-backed process execution graphs.

The existing synchronous graph core remains the dependency, binding-ledger and
node-outcome authority.  This facade adds only graph-write target authorization:
a write node can run when the final compiler attached a resolved write contract
and the exact target policy grants writes.  Reads delegate unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import process_graph_read_runtime as _core
from .target_policy import normalize_base_url

GRAPH_RUNTIME_SCHEMA = _core.GRAPH_RUNTIME_SCHEMA
BINDING_LEDGER_SCHEMA = _core.BINDING_LEDGER_SCHEMA
GRAPH_RUNTIME_INVALID = _core.GRAPH_RUNTIME_INVALID
GRAPH_RUNTIME_WAIT_UNSUPPORTED = _core.GRAPH_RUNTIME_WAIT_UNSUPPORTED
GRAPH_RUNTIME_ASYNC_UNSUPPORTED = _core.GRAPH_RUNTIME_ASYNC_UNSUPPORTED
GRAPH_WRITE_RUNTIME_UNAVAILABLE = _core.GRAPH_WRITE_RUNTIME_UNAVAILABLE
GRAPH_TARGET_NOT_APPROVED = _core.GRAPH_TARGET_NOT_APPROVED
GRAPH_TARGET_ACTOR_CREDENTIAL_UNRESOLVED = (
    _core.GRAPH_TARGET_ACTOR_CREDENTIAL_UNRESOLVED
)
GRAPH_SECONDARY_WRITE_CLEANUP_UNAVAILABLE = (
    _core.GRAPH_SECONDARY_WRITE_CLEANUP_UNAVAILABLE
)
GRAPH_NODE_COMPENSATION_UNRESOLVED = _core.GRAPH_NODE_COMPENSATION_UNRESOLVED
GRAPH_PREDECESSOR_NOT_SUCCEEDED = _core.GRAPH_PREDECESSOR_NOT_SUCCEEDED
GRAPH_INPUT_BINDING_UNRESOLVED = _core.GRAPH_INPUT_BINDING_UNRESOLVED
GRAPH_INPUT_BINDING_CONFLICT = _core.GRAPH_INPUT_BINDING_CONFLICT
GRAPH_OUTPUT_BINDING_UNRESOLVED = _core.GRAPH_OUTPUT_BINDING_UNRESOLVED
GRAPH_WRITE_CONTRACT_MISSING = "PROCESS_GRAPH_WRITE_CONTRACT_MISSING"

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


extract_execution_graph = _core.extract_execution_graph
graph_step_context = _core.graph_step_context
record_graph_step_outcome = _core.record_graph_step_outcome


def resolve_graph_target_context(
    *,
    runtime_contract: dict[str, Any],
    system_ref: str,
    actor_ref: str,
    base_url: str,
    require_write: bool,
) -> dict[str, Any]:
    """Resolve one exact approved target and its isolated actor credential key."""
    contract, primary, error = _core._target_contract(
        runtime_contract,
        system_ref=system_ref,
        base_url=base_url,
    )
    if error:
        return {
            "status": "BLOCKED",
            "reason_code": GRAPH_TARGET_NOT_APPROVED,
            "detail": error,
        }
    decision = _core._target_decision(contract)
    allowed = (
        decision.get("write_allowed")
        if require_write
        else decision.get("read_allowed")
    )
    if not allowed:
        return {
            "status": "BLOCKED",
            "reason_code": GRAPH_TARGET_NOT_APPROVED,
            "detail": (
                f"{system_ref or 'primary'}:"
                f"{','.join(decision.get('blocking_codes') or [])}"
            ),
            "target_policy_decision": decision,
        }
    credential_key, error = _core._credential_key(
        contract,
        actor_ref=actor_ref,
        primary=primary,
    )
    if error:
        return {
            "status": "BLOCKED",
            "reason_code": GRAPH_TARGET_ACTOR_CREDENTIAL_UNRESOLVED,
            "detail": f"{system_ref}:{error}",
            "target_policy_decision": decision,
        }
    return {
        "status": "READY",
        "system_ref": system_ref,
        "primary": primary,
        "base_url": normalize_base_url(decision.get("approved_base_url")),
        "runtime_contract": contract,
        "target_policy_decision": decision,
        "credential_token_key": credential_key,
        "write_allowed": bool(decision.get("write_allowed")),
        "read_allowed": bool(decision.get("read_allowed")),
    }


def prepare_graph_runtime(
    *,
    graph: dict[str, Any],
    treatment_plan: list[Any],
    ops: dict[str, dict[str, Any]],
    base_url: str,
    runtime_contract: dict[str, Any],
) -> dict[str, Any]:
    """Validate one synchronous graph before any node reaches transport."""
    write_contracts = _dict(graph.get("write_contracts_by_node"))
    if not write_contracts:
        return _core.prepare_graph_runtime(
            graph=graph,
            treatment_plan=treatment_plan,
            ops=ops,
            base_url=base_url,
            runtime_contract=runtime_contract,
        )

    nodes = _core._graph_nodes(graph)
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
                f"graph_plan_identity_mismatch:missing={sorted(set(nodes) - plan_ids)}:"
                f"unexpected={sorted(plan_ids - set(nodes))}"
            ),
        }
    if _list(graph.get("wait_contracts")):
        return {
            "status": "BLOCKED",
            "reason_code": GRAPH_RUNTIME_WAIT_UNSUPPORTED,
            "detail": "source_declared_wait_contract_requires_observer_scheduler",
        }
    async_edges = sorted(
        {
            _text(edge.get("relation_type")).upper()
            for edge in _list(graph.get("edges"))
            if isinstance(edge, dict)
            and _text(edge.get("relation_type")).upper() in _ASYNC_RELATIONS
        }
    )
    if async_edges:
        return {
            "status": "BLOCKED",
            "reason_code": GRAPH_RUNTIME_ASYNC_UNSUPPORTED,
            "detail": ",".join(async_edges),
        }
    predecessors, error = _core._predecessors(graph, set(nodes))
    if error:
        return {
            "status": "BLOCKED",
            "reason_code": GRAPH_RUNTIME_INVALID,
            "detail": error,
        }
    waves, error = _core._waves(order, predecessors)
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
        path = _text(
            node.get("path")
            or operation.get("path")
            or operation.get("raw_path")
        )
        if not operation or not method or not path.startswith("/"):
            return {
                "status": "BLOCKED",
                "reason_code": GRAPH_RUNTIME_INVALID,
                "detail": f"{node_id}:operation_transport_unresolved:{operation_ref}",
            }
        is_write = method in _WRITE_METHODS
        if is_write:
            write_contract = _dict(write_contracts.get(node_id))
            node_contract = _dict(node.get("write_contract"))
            if (
                not write_contract
                or _text(write_contract.get("source_step_id")) != node_id
                or _text(write_contract.get("operation_ref")) != operation_ref
                or write_contract != node_contract
                or len(_list(node.get("effect_observer_operations"))) != 1
                or not _text(write_contract.get("cleanup_step_id"))
            ):
                return {
                    "status": "BLOCKED",
                    "reason_code": GRAPH_WRITE_CONTRACT_MISSING,
                    "detail": f"{node_id}:compiled_write_contract_incomplete",
                }
        context = resolve_graph_target_context(
            runtime_contract=runtime_contract,
            system_ref=_text(node.get("system_ref")),
            actor_ref=_text(node.get("actor_ref")),
            base_url=base_url,
            require_write=is_write,
        )
        if _text(context.get("status")) != "READY":
            return {
                "status": "BLOCKED",
                "reason_code": _text(context.get("reason_code"))
                or GRAPH_TARGET_NOT_APPROVED,
                "detail": f"{node_id}:{_text(context.get('detail'))}",
            }
        target_contexts[node_id] = {
            **context,
            "operation_ref": operation_ref,
            "method": method,
            "path": path,
            "write_node": is_write,
            "write_contract": deepcopy(_dict(write_contracts.get(node_id))),
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


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


__all__ = [
    "GRAPH_RUNTIME_SCHEMA",
    "BINDING_LEDGER_SCHEMA",
    "GRAPH_RUNTIME_INVALID",
    "GRAPH_RUNTIME_WAIT_UNSUPPORTED",
    "GRAPH_RUNTIME_ASYNC_UNSUPPORTED",
    "GRAPH_WRITE_RUNTIME_UNAVAILABLE",
    "GRAPH_WRITE_CONTRACT_MISSING",
    "GRAPH_TARGET_NOT_APPROVED",
    "GRAPH_TARGET_ACTOR_CREDENTIAL_UNRESOLVED",
    "GRAPH_SECONDARY_WRITE_CLEANUP_UNAVAILABLE",
    "GRAPH_NODE_COMPENSATION_UNRESOLVED",
    "GRAPH_PREDECESSOR_NOT_SUCCEEDED",
    "GRAPH_INPUT_BINDING_UNRESOLVED",
    "GRAPH_INPUT_BINDING_CONFLICT",
    "GRAPH_OUTPUT_BINDING_UNRESOLVED",
    "extract_execution_graph",
    "resolve_graph_target_context",
    "prepare_graph_runtime",
    "graph_step_context",
    "record_graph_step_outcome",
]
