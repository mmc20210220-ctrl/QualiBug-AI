"""Source-backed multi-object process protocol compiler.

This module extends the existing ``experiment_protocol_registry``; it does not
create another orchestration registry or execution mainline.  The compile-time
authority is an execution graph.  A legacy treatment plan is emitted only when
that graph has one deterministic, currently executable linear projection.

The compiler never selects the first transition, never orders nodes by document
appearance, and never treats the original write operation as its own cleanup.
Unsupported graph runtime features remain visible as BLOCKED together with the
compiled graph so later layers do not silently flatten them.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Any

logger = logging.getLogger(__name__)

TEMPLATE_MULTI_STEP_PROCESS = "multi_step_business_process"
TEMPLATE_STATE_CHAIN_PROCESS = "state_chain_process"
TEMPLATE_SEQUENCE_VERIFICATION = "sequence_verification"

EXECUTION_GRAPH_SCHEMA = "qualibug.process-execution-graph.v1"

MULTI_STEP_PROTOCOL_NOT_RESOLVED = "MULTI_STEP_PROTOCOL_NOT_RESOLVED"
MULTI_STEP_IDENTITY_INVALID = "MULTI_STEP_IDENTITY_INVALID"
MULTI_STEP_ACTOR_NOT_BOUND = "MULTI_STEP_ACTOR_NOT_BOUND"
MULTI_STEP_OPERATION_NOT_BOUND = "MULTI_STEP_OPERATION_NOT_BOUND"
MULTI_STEP_PROCESS_GRAPH_AMBIGUOUS = "MULTI_STEP_PROCESS_GRAPH_AMBIGUOUS"
MULTI_STEP_PROCESS_GRAPH_INVALID = "MULTI_STEP_PROCESS_GRAPH_INVALID"
MULTI_STEP_PROCESS_GRAPH_CYCLE = "MULTI_STEP_PROCESS_GRAPH_CYCLE"
MULTI_STEP_GRAPH_RUNTIME_NOT_AVAILABLE = "MULTI_STEP_GRAPH_RUNTIME_NOT_AVAILABLE"

_ASYNC_RELATIONS = frozenset({"AWAITS", "NOTIFIES", "TRIGGERS", "MESSAGE", "ASYNC_MESSAGE"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique_text(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = _text(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _blocked(reason_code: str, detail: str, graph: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "BLOCKED",
        "reason_code": reason_code,
        "detail": detail,
    }
    if graph:
        result["execution_graph"] = graph
    return result


def _operation_index(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("id") or row.get("operation_id")): row
        for row in _list(behavior_ir.get("operations"))
        if isinstance(row, dict) and _text(row.get("id") or row.get("operation_id"))
    }


def _linear_graph_from_steps(
    process_steps: list[Any],
    *,
    source_refs: list[Any],
    process_id: str = "",
) -> dict[str, Any]:
    nodes = [dict(row) for row in process_steps if isinstance(row, dict)]
    edges: list[dict[str, Any]] = []
    for index in range(len(nodes) - 1):
        source_id = _text(nodes[index].get("step_id") or nodes[index].get("node_id"))
        target_id = _text(nodes[index + 1].get("step_id") or nodes[index + 1].get("node_id"))
        edges.append(
            {
                "edge_id": f"edge_{index + 1}",
                "source_node_id": source_id,
                "target_node_id": target_id,
                "relation_type": "SOURCE_DECLARED_SEQUENCE",
                "source_refs": list(source_refs),
            }
        )
    return {
        "schema_version": EXECUTION_GRAPH_SCHEMA,
        "process_id": process_id or "source_declared_process_steps",
        "nodes": nodes,
        "edges": edges,
        "wait_contracts": [],
        "compensation_edges": [],
        "source_refs": list(source_refs),
        "source_kind": "PROPERTY_PROCESS_STEPS",
    }


def _graph_from_transitions(
    behavior_ir: dict[str, Any],
    operation_ref: str,
) -> tuple[dict[str, Any] | None, str]:
    operations = _operation_index(behavior_ir)
    transitions: list[dict[str, Any]] = []
    for index, relation in enumerate(_list(behavior_ir.get("relations"))):
        if not isinstance(relation, dict):
            continue
        if _text(relation.get("relation_type") or relation.get("kind")).lower() != "transitions":
            continue
        op_ref = _text(relation.get("operation_ref"))
        from_ref = _text(relation.get("from_ref") or relation.get("from_state_ref"))
        to_ref = _text(relation.get("to_ref") or relation.get("to_state_ref"))
        if not op_ref or not from_ref or not to_ref or op_ref not in operations:
            continue
        node_id = _text(relation.get("step_id") or relation.get("relation_id"))
        if not node_id:
            node_id = f"transition_{index + 1}_{op_ref}"
        transitions.append(
            {
                "node_id": node_id,
                "step_id": node_id,
                "operation_ref": op_ref,
                "from_state": from_ref,
                "to_state": to_ref,
                "source_refs": _list(relation.get("source_refs")),
                "relation_id": _text(relation.get("relation_id")),
            }
        )
    if not transitions:
        return None, "no_source_declared_transitions"

    primary_nodes = [row for row in transitions if _text(row.get("operation_ref")) == operation_ref]
    if len(primary_nodes) != 1:
        return None, f"primary_transition_count:{len(primary_nodes)}"

    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in transitions:
        for target in transitions:
            if source is target:
                continue
            if _text(source.get("to_state")) == _text(target.get("from_state")):
                adjacency[_text(source.get("node_id"))].append(target)

    reachable: dict[str, dict[str, Any]] = {}
    queue: deque[dict[str, Any]] = deque(primary_nodes)
    while queue:
        node = queue.popleft()
        node_id = _text(node.get("node_id"))
        if node_id in reachable:
            continue
        reachable[node_id] = node
        queue.extend(adjacency.get(node_id, []))

    nodes = list(reachable.values())
    edges: list[dict[str, Any]] = []
    for source in nodes:
        source_id = _text(source.get("node_id"))
        for target in adjacency.get(source_id, []):
            target_id = _text(target.get("node_id"))
            if target_id not in reachable:
                continue
            edges.append(
                {
                    "edge_id": f"transition_edge_{len(edges) + 1}",
                    "source_node_id": source_id,
                    "target_node_id": target_id,
                    "relation_type": "STATE_TRANSITION_SEQUENCE",
                    "source_refs": _unique_text(
                        [*_list(source.get("source_refs")), *_list(target.get("source_refs"))]
                    ),
                }
            )
    return (
        {
            "schema_version": EXECUTION_GRAPH_SCHEMA,
            "process_id": f"state_chain:{operation_ref}",
            "nodes": nodes,
            "edges": edges,
            "wait_contracts": [],
            "compensation_edges": [],
            "source_refs": _unique_text(
                [ref for row in nodes for ref in _list(row.get("source_refs"))]
            ),
            "source_kind": "BEHAVIOR_IR_TRANSITIONS",
        },
        "",
    )


def _select_source_graph(
    *,
    prop: dict[str, Any],
    behavior_ir: dict[str, Any],
    operation_ref: str,
) -> tuple[dict[str, Any] | None, str, str]:
    explicit = _dict(prop.get("execution_graph") or prop.get("process_graph"))
    if explicit:
        return explicit, "", "PROPERTY_PROCESS_GRAPH"

    graph_ref = _text(prop.get("process_graph_ref") or prop.get("process_id"))
    candidates: list[dict[str, Any]] = []
    for graph in _list(behavior_ir.get("process_graphs")):
        if not isinstance(graph, dict):
            continue
        process_id = _text(graph.get("process_id") or graph.get("execution_graph_id"))
        nodes = [row for row in _list(graph.get("nodes")) if isinstance(row, dict)]
        operation_refs = {
            _text(row.get("operation_ref") or row.get("operation_id"))
            for row in nodes
            if _text(row.get("operation_ref") or row.get("operation_id"))
        }
        if graph_ref and process_id == graph_ref:
            candidates.append(graph)
        elif not graph_ref and operation_ref and operation_ref in operation_refs:
            candidates.append(graph)
    if len(candidates) == 1:
        return dict(candidates[0]), "", "BUSINESS_BEHAVIOR_IR_PROCESS_GRAPH"
    if len(candidates) > 1:
        return None, f"matching_process_graphs:{len(candidates)}", ""

    process_steps = _list(prop.get("process_steps"))
    if process_steps:
        return (
            _linear_graph_from_steps(
                process_steps,
                source_refs=_list(prop.get("source_refs")),
                process_id=graph_ref,
            ),
            "",
            "PROPERTY_PROCESS_STEPS",
        )

    graph, detail = _graph_from_transitions(behavior_ir, operation_ref)
    return graph, detail, "BEHAVIOR_IR_TRANSITIONS" if graph else ""


def _node_aliases(node: dict[str, Any], node_id: str) -> list[str]:
    return _unique_text(
        [
            node_id,
            node.get("step_id"),
            node.get("process_step_id"),
            node.get("process_ref"),
            node.get("behavior_id"),
            node.get("object_ref"),
            node.get("primary_object_ref"),
        ]
    )


def _normalize_execution_graph(
    raw_graph: dict[str, Any],
    *,
    actor_ref: str,
    operation_ref: str,
    behavior_ir: dict[str, Any],
    source_kind: str,
) -> tuple[dict[str, Any] | None, str, str]:
    operations = _operation_index(behavior_ir)
    raw_nodes = [
        dict(row)
        for row in (_list(raw_graph.get("nodes")) or _list(raw_graph.get("steps")))
        if isinstance(row, dict)
    ]
    if not raw_nodes:
        return None, MULTI_STEP_PROTOCOL_NOT_RESOLVED, "process_graph_has_no_nodes"

    nodes: list[dict[str, Any]] = []
    aliases: dict[str, str] = {}
    seen_node_ids: set[str] = set()
    for index, raw_node in enumerate(raw_nodes):
        node_id = _text(
            raw_node.get("node_id")
            or raw_node.get("step_id")
            or raw_node.get("process_step_id")
            or raw_node.get("behavior_id")
        )
        if not node_id:
            return None, MULTI_STEP_IDENTITY_INVALID, f"node_{index + 1}_missing_identity"
        if node_id in seen_node_ids:
            return None, MULTI_STEP_IDENTITY_INVALID, f"duplicate_step_id:{node_id}"
        seen_node_ids.add(node_id)

        op_ref = _text(
            raw_node.get("operation_ref")
            or raw_node.get("operation_id")
            or raw_node.get("op_ref")
        )
        if not op_ref and len(raw_nodes) == 1:
            op_ref = operation_ref
        if not op_ref:
            return None, MULTI_STEP_OPERATION_NOT_BOUND, f"step_{node_id}_missing_operation_ref"

        node_actor = _text(raw_node.get("actor_ref")) or actor_ref
        if not node_actor:
            return None, MULTI_STEP_ACTOR_NOT_BOUND, f"step_{node_id}_missing_actor_ref"

        operation = _dict(operations.get(op_ref))
        object_refs = _unique_text(
            [
                *_list(raw_node.get("object_refs")),
                raw_node.get("object_ref"),
                raw_node.get("primary_object_ref"),
            ]
        )
        system_ref = _text(
            raw_node.get("system_ref")
            or raw_node.get("target_system_ref")
            or raw_node.get("approved_target_ref")
        )
        compensation_ref = _text(
            raw_node.get("compensation_operation_ref")
            or raw_node.get("cleanup_operation_ref")
            or raw_node.get("compensates_operation_ref")
        )
        node = {
            "node_id": node_id,
            "step_id": node_id,
            "operation_ref": op_ref,
            "actor_ref": node_actor,
            "system_ref": system_ref,
            "object_refs": object_refs,
            "method": _text(raw_node.get("method") or operation.get("method")) or "POST",
            "path": _text(
                raw_node.get("path")
                or operation.get("path")
                or operation.get("raw_path")
                or operation.get("path_template")
            ),
            "intent": _text(raw_node.get("intent")) or "business_process_step",
            "from_state": _text(raw_node.get("from_state") or raw_node.get("from_state_ref")),
            "to_state": _text(raw_node.get("to_state") or raw_node.get("to_state_ref")),
            "input_binding_refs": _list(raw_node.get("input_binding_refs")),
            "output_binding_specs": _list(raw_node.get("output_binding_specs")),
            "observer_requirements": _list(raw_node.get("observer_requirements")),
            "compensation_operation_ref": compensation_ref,
            "source_refs": _list(raw_node.get("source_refs")),
        }
        nodes.append(node)
        for alias in _node_aliases(raw_node, node_id):
            previous = aliases.get(alias)
            if previous and previous != node_id:
                return None, MULTI_STEP_IDENTITY_INVALID, f"ambiguous_node_alias:{alias}"
            aliases[alias] = node_id

    compensation_by_node: dict[str, str] = {}
    for row in _list(raw_graph.get("compensation_edges")):
        if not isinstance(row, dict):
            continue
        source_alias = _text(
            row.get("source_node_id")
            or row.get("mutation_node_id")
            or row.get("source")
        )
        source_node = aliases.get(source_alias, source_alias)
        compensation_ref = _text(
            row.get("compensation_operation_ref")
            or row.get("operation_ref")
            or row.get("target_operation_ref")
        )
        if source_node and compensation_ref:
            compensation_by_node[source_node] = compensation_ref
    for node in nodes:
        if not _text(node.get("compensation_operation_ref")):
            node["compensation_operation_ref"] = compensation_by_node.get(
                _text(node.get("node_id")), ""
            )

    raw_edges = [row for row in _list(raw_graph.get("edges")) if isinstance(row, dict)]
    edges: list[dict[str, Any]] = []
    for index, raw_edge in enumerate(raw_edges):
        source_alias = _text(
            raw_edge.get("source_node_id")
            or raw_edge.get("from_node_id")
            or raw_edge.get("predecessor_node_id")
            or raw_edge.get("source_process_ref")
            or raw_edge.get("source")
        )
        target_alias = _text(
            raw_edge.get("target_node_id")
            or raw_edge.get("to_node_id")
            or raw_edge.get("successor_node_id")
            or raw_edge.get("target_process_ref")
            or raw_edge.get("target")
        )
        source_node = aliases.get(source_alias, source_alias)
        target_node = aliases.get(target_alias, target_alias)
        if source_node not in seen_node_ids or target_node not in seen_node_ids:
            return (
                None,
                MULTI_STEP_PROCESS_GRAPH_INVALID,
                f"edge_{index + 1}_endpoint_not_node:{source_alias}->{target_alias}",
            )
        if source_node == target_node:
            return None, MULTI_STEP_PROCESS_GRAPH_INVALID, f"self_edge:{source_node}"
        edges.append(
            {
                "edge_id": _text(raw_edge.get("edge_id")) or f"edge_{index + 1}",
                "source_node_id": source_node,
                "target_node_id": target_node,
                "relation_type": _text(raw_edge.get("relation_type") or raw_edge.get("kind"))
                or "DEPENDS_ON",
                "condition": _dict(raw_edge.get("condition")),
                "binding_refs": _list(raw_edge.get("binding_refs")),
                "source_refs": _list(raw_edge.get("source_refs")),
            }
        )

    if not edges and len(nodes) > 1 and source_kind == "PROPERTY_PROCESS_STEPS":
        for index in range(len(nodes) - 1):
            edges.append(
                {
                    "edge_id": f"edge_{index + 1}",
                    "source_node_id": nodes[index]["node_id"],
                    "target_node_id": nodes[index + 1]["node_id"],
                    "relation_type": "SOURCE_DECLARED_SEQUENCE",
                    "condition": {},
                    "binding_refs": [],
                    "source_refs": _list(raw_graph.get("source_refs")),
                }
            )

    indegree: dict[str, int] = {node["node_id"]: 0 for node in nodes}
    outgoing: dict[str, list[str]] = {node["node_id"]: [] for node in nodes}
    for edge in edges:
        source_node = edge["source_node_id"]
        target_node = edge["target_node_id"]
        outgoing[source_node].append(target_node)
        indegree[target_node] += 1

    queue = deque([node["node_id"] for node in nodes if indegree[node["node_id"]] == 0])
    topological_order: list[str] = []
    mutable_indegree = dict(indegree)
    while queue:
        current = queue.popleft()
        topological_order.append(current)
        for target in outgoing[current]:
            mutable_indegree[target] -= 1
            if mutable_indegree[target] == 0:
                queue.append(target)
    if len(topological_order) != len(nodes):
        return None, MULTI_STEP_PROCESS_GRAPH_CYCLE, "process_graph_contains_cycle"

    starts = [node_id for node_id, count in indegree.items() if count == 0]
    terminals = [node_id for node_id, targets in outgoing.items() if not targets]
    fork_groups = [
        {"fork_node_id": node_id, "successor_node_ids": list(targets)}
        for node_id, targets in outgoing.items()
        if len(targets) > 1
    ]
    incoming: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        incoming[edge["target_node_id"]].append(edge["source_node_id"])
    join_groups = [
        {"join_node_id": node_id, "predecessor_node_ids": list(sources)}
        for node_id, sources in incoming.items()
        if len(sources) > 1
    ]

    wait_contracts = [
        dict(row)
        for row in [*_list(raw_graph.get("wait_contracts")), *_list(raw_graph.get("waits"))]
        if isinstance(row, dict)
    ]
    graph = {
        "schema_version": EXECUTION_GRAPH_SCHEMA,
        "execution_graph_id": _text(
            raw_graph.get("execution_graph_id") or raw_graph.get("process_id")
        )
        or "compiled_process_graph",
        "process_id": _text(raw_graph.get("process_id")) or "compiled_process_graph",
        "nodes": nodes,
        "edges": edges,
        "start_node_refs": starts,
        "terminal_node_refs": terminals,
        "topological_order": topological_order,
        "fork_groups": fork_groups,
        "join_groups": join_groups,
        "wait_contracts": wait_contracts,
        "source_refs": _list(raw_graph.get("source_refs")),
        "source_kind": source_kind,
        "status": "COMPILED",
    }
    return graph, "", ""


def _runtime_gap(graph: dict[str, Any]) -> str:
    systems = {
        _text(node.get("system_ref"))
        for node in _list(graph.get("nodes"))
        if isinstance(node, dict) and _text(node.get("system_ref"))
    }
    reasons: list[str] = []
    if len(systems) > 1:
        reasons.append("cross_system_target_dispatch")
    if _list(graph.get("fork_groups")):
        reasons.append("fork_scheduler")
    if _list(graph.get("join_groups")):
        reasons.append("join_scheduler")
    if _list(graph.get("wait_contracts")):
        reasons.append("wait_observer_scheduler")
    if any(
        _text(edge.get("relation_type")).upper() in _ASYNC_RELATIONS
        for edge in _list(graph.get("edges"))
        if isinstance(edge, dict)
    ):
        reasons.append("async_edge_scheduler")
    if len(_list(graph.get("start_node_refs"))) != 1:
        reasons.append("multiple_start_nodes")
    return ",".join(_unique_text(reasons))


def _linear_treatment_plan(graph: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {
        _text(row.get("node_id")): row
        for row in _list(graph.get("nodes"))
        if isinstance(row, dict) and _text(row.get("node_id"))
    }
    plan: list[dict[str, Any]] = []
    for index, node_id in enumerate(_list(graph.get("topological_order"))):
        node = _dict(by_id.get(_text(node_id)))
        plan.append(
            {
                "step_id": _text(node.get("node_id")),
                "step_ordinal": index + 1,
                "operation_ref": _text(node.get("operation_ref")),
                "actor_ref": _text(node.get("actor_ref")),
                "system_ref": _text(node.get("system_ref")),
                "object_refs": _list(node.get("object_refs")),
                "method": _text(node.get("method")) or "POST",
                "path": _text(node.get("path")),
                "intent": _text(node.get("intent")) or "business_process_step",
                "protocol_step": "multi_step_treatment",
                "from_state": _text(node.get("from_state")),
                "to_state": _text(node.get("to_state")),
                "input_binding_refs": _list(node.get("input_binding_refs")),
                "output_binding_specs": _list(node.get("output_binding_specs")),
                "compensation_operation_ref": _text(
                    node.get("compensation_operation_ref")
                ),
            }
        )
    return plan


def _explicit_cleanup_plan(graph: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {
        _text(row.get("node_id")): row
        for row in _list(graph.get("nodes"))
        if isinstance(row, dict) and _text(row.get("node_id"))
    }
    cleanup: list[dict[str, Any]] = []
    for source_node_id in reversed(
        [_text(value) for value in _list(graph.get("topological_order")) if _text(value)]
    ):
        node = _dict(by_id.get(source_node_id))
        compensation_ref = _text(node.get("compensation_operation_ref"))
        if not compensation_ref:
            continue
        cleanup.append(
            {
                "step_id": f"cleanup_{source_node_id}",
                "source_step_id": source_node_id,
                "operation_ref": compensation_ref,
                "actor_ref": _text(node.get("actor_ref")),
                "system_ref": _text(node.get("system_ref")),
                "mode": "reverse_topological_compensation",
                "source_declared": True,
            }
        )
    return cleanup


def compile_multi_step_process_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    """Compile a source-backed process into one execution-graph authority."""
    family = _text(envelope.get("risk_family"))
    operation_ref = _text(envelope.get("operation_ref"))
    control_actor = _text(envelope.get("control_actor_ref"))
    treatment_actor = _text(envelope.get("treatment_actor_ref"))
    prop = _dict(envelope.get("property_spec"))
    behavior_ir = _dict(envelope.get("behavior_ir"))

    actor_ref = treatment_actor or control_actor
    if not actor_ref:
        return _blocked(MULTI_STEP_ACTOR_NOT_BOUND, "no_actor_for_multi_step_protocol")

    raw_graph, selection_detail, source_kind = _select_source_graph(
        prop=prop,
        behavior_ir=behavior_ir,
        operation_ref=operation_ref,
    )
    if raw_graph is None:
        reason = (
            MULTI_STEP_PROCESS_GRAPH_AMBIGUOUS
            if selection_detail.startswith("matching_process_graphs")
            or selection_detail.startswith("primary_transition_count")
            else MULTI_STEP_PROTOCOL_NOT_RESOLVED
        )
        return _blocked(reason, selection_detail or "no_source_declared_process_graph")

    graph, reason_code, detail = _normalize_execution_graph(
        raw_graph,
        actor_ref=actor_ref,
        operation_ref=operation_ref,
        behavior_ir=behavior_ir,
        source_kind=source_kind,
    )
    if graph is None:
        return _blocked(reason_code or MULTI_STEP_PROCESS_GRAPH_INVALID, detail)

    if len(_list(graph.get("nodes"))) < 2:
        return _blocked(
            MULTI_STEP_PROTOCOL_NOT_RESOLVED,
            f"insufficient_steps:{len(_list(graph.get('nodes')))}",
            graph,
        )

    runtime_gap = _runtime_gap(graph)
    if runtime_gap:
        graph["status"] = "BLOCKED_RUNTIME_CAPABILITY"
        graph["runtime_blockers"] = runtime_gap.split(",")
        return _blocked(MULTI_STEP_GRAPH_RUNTIME_NOT_AVAILABLE, runtime_gap, graph)

    treatment_plan = _linear_treatment_plan(graph)
    cleanup_plan = _explicit_cleanup_plan(graph)
    expected_order = _list(prop.get("expected_order"))
    if not expected_order and source_kind in {
        "PROPERTY_PROCESS_STEPS",
        "BEHAVIOR_IR_TRANSITIONS",
        "BUSINESS_BEHAVIOR_IR_PROCESS_GRAPH",
    }:
        expected_order = list(graph.get("topological_order") or [])

    source_refs = _list(prop.get("source_refs")) or _list(graph.get("source_refs"))
    return {
        "status": "COMPILED",
        "execution_graph": graph,
        "control_plan": [],
        "treatment_plan": treatment_plan,
        "cleanup_plan": cleanup_plan,
        "assertion": {
            "kind": "process_completion",
            "expected_steps": list(graph.get("topological_order") or []),
            "expected_order": expected_order,
            "execution_graph_id": _text(graph.get("execution_graph_id")),
        },
        "observers": [
            {"observer_id": "http_response"},
            {"observer_id": "after_state"},
        ],
        "per_step_evidence": True,
        "requires_state_precondition": bool(prop.get("from_state")),
        "expected_order": expected_order,
        "source_refs": source_refs,
        "_registry_protocol_id": f"{family}:{TEMPLATE_MULTI_STEP_PROCESS}",
    }


def compile_state_chain_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    result = compile_multi_step_process_protocol(envelope)
    if result.get("status") != "COMPILED":
        return result
    result["assertion"] = {
        **_dict(result.get("assertion")),
        "kind": "step_sequence_order",
    }
    result["_registry_protocol_id"] = f"state:{TEMPLATE_STATE_CHAIN_PROCESS}"
    return result


def compile_sequence_verification_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    result = compile_multi_step_process_protocol(envelope)
    if result.get("status") != "COMPILED":
        return result
    result["assertion"] = {
        **_dict(result.get("assertion")),
        "kind": "step_sequence_order",
    }
    result["_registry_protocol_id"] = f"process:{TEMPLATE_SEQUENCE_VERIFICATION}"
    return result


def register_v150_multi_step_protocols() -> list[str]:
    """Idempotently register graph compilers in the existing registry.

    Registration errors are deliberately not swallowed.  A missing observer or
    assertion surface is a startup contract failure, not a debug-only event.
    """
    from .experiment_protocol_registry import register_family_protocol
    from .process_step_observer import install_process_step_surface

    install_process_step_surface()
    registered = [
        register_family_protocol(
            "process",
            TEMPLATE_MULTI_STEP_PROCESS,
            compiler=compile_multi_step_process_protocol,
            observers=("http_response", "after_state"),
            assertion_kind="process_completion",
            emits_control=False,
            per_step_evidence=True,
        ),
        register_family_protocol(
            "state",
            TEMPLATE_STATE_CHAIN_PROCESS,
            compiler=compile_state_chain_protocol,
            observers=("http_response", "after_state"),
            assertion_kind="step_sequence_order",
            emits_control=False,
            per_step_evidence=True,
        ),
        register_family_protocol(
            "process",
            TEMPLATE_SEQUENCE_VERIFICATION,
            compiler=compile_sequence_verification_protocol,
            observers=("http_response", "after_state"),
            assertion_kind="step_sequence_order",
            emits_control=False,
            per_step_evidence=True,
        ),
    ]
    logger.debug("registered source-backed process protocols: %s", registered)
    return registered
