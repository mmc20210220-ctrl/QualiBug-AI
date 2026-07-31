"""Project understood enterprise processes into governed Business Behavior IR graphs.

This is a projection inside the existing enterprise-understanding mainline.  It
is not an executor, scheduler, registry, or second process authority.  Source-
backed ``model.processes`` remain the semantic authority; this module binds their
steps to existing confirmed behaviors and governed implementation bindings so
the existing protocol registry can consume one graph shape.

No operation, actor, system, edge, join, wait, or compensation is guessed.  Any
missing identity remains visible as PARTIAL plus an Unknown.
"""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from typing import Any, Iterable

from .schema import (
    as_dict,
    as_list,
    dedupe_evidence,
    new_unknown,
    stable_id,
    text,
    unique_text,
)

PROCESS_GRAPH_SCHEMA = "qualibug.enterprise-business-process-graph.v1"
PROCESS_GRAPH_GATE_SCHEMA = "qualibug.enterprise-business-process-graph-gate.v1"


def _formal_behaviors(behaviors: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in behaviors
        if isinstance(row, dict)
        and text(row.get("status")) == "CONFIRMED"
        and row.get("formal_business_rule") is True
        and text(row.get("behavior_id"))
    ]


def _behavior_index(
    behaviors: Iterable[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[tuple[str, str], list[dict[str, Any]]]]:
    by_operation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_operation_object: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for behavior in _formal_behaviors(behaviors):
        operation = text(behavior.get("operation_ref"))
        if not operation:
            continue
        by_operation[operation].append(behavior)
        for object_ref in unique_text(as_list(behavior.get("object_refs"))):
            by_operation_object[(operation, object_ref)].append(behavior)
    return by_operation, by_operation_object


def _binding_index(
    bindings: Iterable[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        behavior_ref = text(binding.get("behavior_ref"))
        if not behavior_ref:
            continue
        for row in as_list(binding.get("api_operation_bindings")):
            if not isinstance(row, dict):
                continue
            if text(row.get("status")) != "BOUND" or row.get("authoritative") is not True:
                continue
            interface_id = text(row.get("interface_id"))
            if not interface_id:
                continue
            result[behavior_ref].append(
                {
                    "interface_id": interface_id,
                    "system_ref": text(
                        row.get("system_ref")
                        or row.get("target_system_ref")
                        or row.get("approved_target_ref")
                        or binding.get("system_ref")
                        or binding.get("target_system_ref")
                        or binding.get("approved_target_ref")
                    ),
                    "method": text(row.get("method")),
                    "path": text(
                        row.get("path")
                        or row.get("raw_path")
                        or row.get("path_template")
                    ),
                    "binding_id": text(binding.get("binding_id")),
                    "evidence": dedupe_evidence(
                        [*as_list(binding.get("evidence")), *as_list(row.get("evidence"))]
                    ),
                }
            )
    return result


def _operation_aliases(model: dict[str, Any]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    conflicts: set[str] = set()
    for operation in as_list(model.get("operations")):
        if not isinstance(operation, dict):
            continue
        canonical = text(
            operation.get("name")
            or operation.get("canonical_name")
            or operation.get("operation_name")
        )
        operation_id = text(operation.get("operation_id") or operation.get("id"))
        names = unique_text(
            [
                canonical,
                operation_id,
                *as_list(operation.get("raw_action_names")),
                *as_list(operation.get("aliases")),
            ]
        )
        target = canonical or operation_id
        if not target:
            continue
        for name in names:
            existing = aliases.get(name)
            if existing and existing != target:
                conflicts.add(name)
            else:
                aliases[name] = target
    for name in conflicts:
        aliases.pop(name, None)
    return aliases


def _step_operation(step: dict[str, Any], aliases: dict[str, str]) -> str:
    raw = text(step.get("operation_ref") or step.get("event") or step.get("operation"))
    return text(aliases.get(raw, raw))


def _step_objects(step: dict[str, Any], process: dict[str, Any]) -> list[str]:
    explicit = unique_text(
        [
            step.get("object_ref"),
            step.get("primary_object_ref"),
            *as_list(step.get("object_refs")),
        ]
    )
    if explicit:
        return explicit
    return unique_text(
        [
            value.get("object_ref") if isinstance(value, dict) else value
            for value in as_list(process.get("inputs"))
        ]
    )


def _candidate_behaviors(
    operation: str,
    object_refs: list[str],
    *,
    by_operation: dict[str, list[dict[str, Any]]],
    by_operation_object: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if object_refs:
        for object_ref in object_refs:
            candidates.extend(by_operation_object.get((operation, object_ref), []))
    else:
        candidates.extend(by_operation.get(operation, []))
    return list(
        {
            text(row.get("behavior_id")): row
            for row in candidates
            if text(row.get("behavior_id"))
        }.values()
    )


def _node_unknown(
    *,
    process: dict[str, Any],
    node_id: str,
    reason_code: str,
    detail: str,
    object_refs: list[str],
    operation: str,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    return new_unknown(
        "BUSINESS_PROCESS_NODE_INCOMPLETE",
        detail,
        related_objects=object_refs,
        related_operations=[operation] if operation else [],
        evidence=evidence,
        severity="P1",
        blocks_formal_understanding=False,
        reason_code=reason_code,
        details={
            "process_id": process.get("process_id"),
            "node_id": node_id,
            "operation": operation,
            "object_refs": object_refs,
        },
    )


def _resolve_node(
    *,
    process: dict[str, Any],
    step: dict[str, Any],
    node_id: str,
    aliases: dict[str, str],
    by_operation: dict[str, list[dict[str, Any]]],
    by_operation_object: dict[tuple[str, str], list[dict[str, Any]]],
    bindings_by_behavior: dict[str, list[dict[str, Any]]],
    cross_system_required: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    operation = _step_operation(step, aliases)
    object_refs = _step_objects(step, process)
    evidence = dedupe_evidence(
        [*as_list(process.get("evidence")), *as_list(step.get("evidence"))]
    )
    unresolved: list[str] = []
    unknowns: list[dict[str, Any]] = []

    candidates = _candidate_behaviors(
        operation,
        object_refs,
        by_operation=by_operation,
        by_operation_object=by_operation_object,
    ) if operation else []
    behavior: dict[str, Any] = {}
    if not operation:
        unresolved.append("PROCESS_NODE_OPERATION_UNRESOLVED")
    elif len(candidates) == 1:
        behavior = candidates[0]
    elif not candidates:
        unresolved.append("PROCESS_NODE_BEHAVIOR_UNRESOLVED")
    else:
        unresolved.append("PROCESS_NODE_BEHAVIOR_AMBIGUOUS")

    implementation: dict[str, Any] = {}
    behavior_ref = text(behavior.get("behavior_id"))
    if behavior_ref:
        bindings = bindings_by_behavior.get(behavior_ref, [])
        if len(bindings) == 1:
            implementation = bindings[0]
        elif not bindings:
            unresolved.append("PROCESS_NODE_IMPLEMENTATION_UNRESOLVED")
        else:
            unresolved.append("PROCESS_NODE_IMPLEMENTATION_AMBIGUOUS")

    actors = unique_text(
        [
            step.get("actor_ref"),
            *as_list(step.get("actor_refs")),
            *as_list(process.get("participants")),
        ]
    )
    actor_ref = actors[0] if len(actors) == 1 else ""
    if len(actors) > 1:
        unresolved.append("PROCESS_NODE_ACTOR_AMBIGUOUS")
    elif not actors:
        unresolved.append("PROCESS_NODE_ACTOR_UNRESOLVED")

    system_ref = text(
        step.get("system_ref")
        or step.get("target_system_ref")
        or implementation.get("system_ref")
    )
    if cross_system_required and not system_ref:
        unresolved.append("PROCESS_NODE_SYSTEM_UNRESOLVED")

    operation_ref = text(implementation.get("interface_id"))
    node = {
        "node_id": node_id,
        "step_id": node_id,
        "process_id": process.get("process_id"),
        "source_step_ref": text(
            step.get("step_id")
            or step.get("transition_id")
            or step.get("process_ref")
        ),
        "behavior_id": behavior_ref,
        "business_operation_ref": operation,
        "operation_ref": operation_ref,
        "object_refs": object_refs,
        "actor_ref": actor_ref,
        "system_ref": system_ref,
        "method": text(implementation.get("method")),
        "path": text(implementation.get("path")),
        "from_state": text(step.get("from_state")),
        "to_state": text(step.get("to_state")),
        "conditions": as_list(step.get("conditions")),
        "path_kind": text(step.get("path_kind")),
        "input_binding_refs": as_list(step.get("input_binding_refs")),
        "output_binding_specs": as_list(step.get("output_binding_specs")),
        "observer_requirements": as_list(step.get("observer_requirements")),
        "compensation_operation_ref": text(
            step.get("compensation_operation_ref")
            or step.get("cleanup_operation_ref")
        ),
        "implementation_binding_id": text(implementation.get("binding_id")),
        "source_refs": unique_text(
            [
                *as_list(process.get("source_refs")),
                *as_list(step.get("source_refs")),
                *as_list(behavior.get("source_refs")),
            ]
        ),
        "evidence": dedupe_evidence(
            [evidence_item for evidence_item in [*evidence, *as_list(behavior.get("evidence")), *as_list(implementation.get("evidence"))] if isinstance(evidence_item, dict)]
        ),
        "unresolved_semantics": unique_text(unresolved),
        "status": "BOUND" if not unresolved else "PARTIAL",
    }

    for reason in unique_text(unresolved):
        messages = {
            "PROCESS_NODE_OPERATION_UNRESOLVED": "流程步骤缺少来源明确的业务操作。",
            "PROCESS_NODE_BEHAVIOR_UNRESOLVED": f"流程操作“{operation}”尚未绑定唯一正式业务行为。",
            "PROCESS_NODE_BEHAVIOR_AMBIGUOUS": f"流程操作“{operation}”匹配多个正式业务行为，不能自动选择。",
            "PROCESS_NODE_IMPLEMENTATION_UNRESOLVED": f"流程操作“{operation}”尚未绑定权威接口实现。",
            "PROCESS_NODE_IMPLEMENTATION_AMBIGUOUS": f"流程操作“{operation}”存在多个权威接口实现，不能自动选择。",
            "PROCESS_NODE_ACTOR_UNRESOLVED": f"流程操作“{operation}”缺少来源明确的参与角色。",
            "PROCESS_NODE_ACTOR_AMBIGUOUS": f"流程操作“{operation}”存在多个参与角色且资料未指明本步骤执行者。",
            "PROCESS_NODE_SYSTEM_UNRESOLVED": f"跨系统流程操作“{operation}”缺少来源明确的目标系统。",
        }
        unknowns.append(
            _node_unknown(
                process=process,
                node_id=node_id,
                reason_code=reason,
                detail=messages.get(reason, reason),
                object_refs=object_refs,
                operation=operation,
                evidence=node["evidence"],
            )
        )
    return node, unknowns


def _sequence_edges(
    process_id: str,
    node_ids: list[str],
    *,
    relation_type: str,
    source_refs: list[Any],
) -> list[dict[str, Any]]:
    return [
        {
            "edge_id": stable_id("process_graph_edge", process_id, node_ids[index], node_ids[index + 1], relation_type),
            "source_node_id": node_ids[index],
            "target_node_id": node_ids[index + 1],
            "relation_type": relation_type,
            "condition": {},
            "binding_refs": [],
            "source_refs": unique_text(source_refs),
        }
        for index in range(len(node_ids) - 1)
    ]


def _graph_shape(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> tuple[list[str], list[str], list[str], list[dict[str, Any]], list[dict[str, Any]], bool]:
    node_ids = [text(row.get("node_id")) for row in nodes if text(row.get("node_id"))]
    indegree = {node_id: 0 for node_id in node_ids}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    incoming: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        source = text(edge.get("source_node_id"))
        target = text(edge.get("target_node_id"))
        if source not in indegree or target not in indegree:
            continue
        outgoing[source].append(target)
        incoming[target].append(source)
        indegree[target] += 1
    queue = deque(sorted(node_id for node_id, count in indegree.items() if count == 0))
    remaining = dict(indegree)
    ordered: list[str] = []
    while queue:
        current = queue.popleft()
        ordered.append(current)
        for target in sorted(outgoing[current]):
            remaining[target] -= 1
            if remaining[target] == 0:
                queue.append(target)
    starts = sorted(node_id for node_id, count in indegree.items() if count == 0)
    terminals = sorted(node_id for node_id, targets in outgoing.items() if not targets)
    forks = [
        {"fork_node_id": node_id, "successor_node_ids": sorted(targets)}
        for node_id, targets in sorted(outgoing.items())
        if len(targets) > 1
    ]
    joins = [
        {"join_node_id": node_id, "predecessor_node_ids": sorted(sources)}
        for node_id, sources in sorted(incoming.items())
        if len(sources) > 1
    ]
    return ordered, starts, terminals, forks, joins, len(ordered) != len(node_ids)


def _atomic_graph(
    process: dict[str, Any],
    *,
    aliases: dict[str, str],
    by_operation: dict[str, list[dict[str, Any]]],
    by_operation_object: dict[tuple[str, str], list[dict[str, Any]]],
    bindings_by_behavior: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    process_id = text(process.get("process_id"))
    features = unique_text(as_list(process.get("process_features")))
    cross_system_required = "CROSS_SYSTEM" in features
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []

    main_node_ids: list[str] = []
    for index, raw_step in enumerate(as_list(process.get("steps"))):
        if not isinstance(raw_step, dict) or text(raw_step.get("process_ref")):
            continue
        node_id = stable_id(
            "process_graph_node",
            process_id,
            "main",
            raw_step.get("transition_id") or raw_step.get("step_id") or index,
        )
        node, node_unknowns = _resolve_node(
            process=process,
            step=raw_step,
            node_id=node_id,
            aliases=aliases,
            by_operation=by_operation,
            by_operation_object=by_operation_object,
            bindings_by_behavior=bindings_by_behavior,
            cross_system_required=cross_system_required,
        )
        nodes.append(node)
        main_node_ids.append(node_id)
        unknowns.extend(node_unknowns)
    edges.extend(
        _sequence_edges(
            process_id,
            main_node_ids,
            relation_type="SOURCE_DECLARED_SEQUENCE",
            source_refs=as_list(process.get("source_refs")),
        )
    )

    for branch_index, branch in enumerate(as_list(process.get("branches"))):
        if not isinstance(branch, dict):
            continue
        branch_node_ids: list[str] = []
        for step_index, raw_step in enumerate(as_list(branch.get("steps"))):
            if not isinstance(raw_step, dict):
                continue
            node_id = stable_id(
                "process_graph_node",
                process_id,
                branch.get("branch_id") or branch_index,
                raw_step.get("transition_id") or raw_step.get("step_id") or step_index,
            )
            node, node_unknowns = _resolve_node(
                process=process,
                step=raw_step,
                node_id=node_id,
                aliases=aliases,
                by_operation=by_operation,
                by_operation_object=by_operation_object,
                bindings_by_behavior=bindings_by_behavior,
                cross_system_required=cross_system_required,
            )
            nodes.append(node)
            branch_node_ids.append(node_id)
            unknowns.extend(node_unknowns)
        edges.extend(
            _sequence_edges(
                process_id,
                branch_node_ids,
                relation_type="SOURCE_DECLARED_BRANCH_SEQUENCE",
                source_refs=as_list(branch.get("source_refs")),
            )
        )
        if branch_node_ids:
            origin = next(
                (
                    row
                    for row in nodes
                    if text(row.get("to_state"))
                    and text(row.get("to_state")) == text(branch.get("from_state"))
                ),
                None,
            )
            if origin is not None:
                edges.append(
                    {
                        "edge_id": stable_id("process_graph_branch_edge", process_id, origin.get("node_id"), branch_node_ids[0]),
                        "source_node_id": origin.get("node_id"),
                        "target_node_id": branch_node_ids[0],
                        "relation_type": "SOURCE_DECLARED_BRANCH",
                        "condition": {"conditions": as_list(branch.get("conditions"))},
                        "binding_refs": [],
                        "source_refs": unique_text(as_list(branch.get("source_refs"))),
                    }
                )
            elif main_node_ids:
                unknowns.append(
                    new_unknown(
                        "PROCESS_BRANCH_ORIGIN_UNRESOLVED",
                        "已理解流程分支，但无法将其来源状态唯一绑定到主流程节点。",
                        related_objects=unique_text(as_list(process.get("inputs"))),
                        evidence=as_list(process.get("evidence")),
                        severity="P1",
                        blocks_formal_understanding=False,
                        reason_code="PROCESS_BRANCH_ORIGIN_UNRESOLVED",
                        details={"process_id": process_id, "branch_id": branch.get("branch_id")},
                    )
                )

    ordered, starts, terminals, forks, joins, cycle = _graph_shape(nodes, edges)
    status = "COMPILED"
    if not nodes or cycle or any(text(row.get("status")) != "BOUND" for row in nodes) or unknowns:
        status = "PARTIAL"
    graph = {
        "schema_version": PROCESS_GRAPH_SCHEMA,
        "execution_graph_id": stable_id("business_process_graph", process_id),
        "process_id": process_id,
        "name": process.get("name"),
        "process_type": process.get("process_type"),
        "process_features": features,
        "nodes": nodes,
        "edges": edges,
        "start_node_refs": starts,
        "terminal_node_refs": terminals,
        "topological_order": ordered,
        "fork_groups": forks,
        "join_groups": joins,
        "wait_contracts": [dict(row) for row in as_list(process.get("waits")) if isinstance(row, dict)],
        "loops": [dict(row) for row in as_list(process.get("loops")) if isinstance(row, dict)],
        "exception_paths": [dict(row) for row in as_list(process.get("exception_paths")) if isinstance(row, dict)],
        "source_refs": unique_text(as_list(process.get("source_refs"))),
        "evidence": dedupe_evidence(as_list(process.get("evidence"))),
        "unresolved_semantics": unique_text(
            [
                "PROCESS_GRAPH_NO_NODES" if not nodes else "",
                "PROCESS_GRAPH_CYCLE" if cycle else "",
                *[
                    reason
                    for node in nodes
                    for reason in as_list(node.get("unresolved_semantics"))
                ],
            ]
        ),
        "status": status,
        "semantic_authority": "enterprise_understanding.processes",
        "runtime_executability_claimed": False,
    }
    return graph, unknowns


def _clone_child_graph(
    child: dict[str, Any], composite_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    mapping: dict[str, str] = {}
    nodes: list[dict[str, Any]] = []
    for node in as_list(child.get("nodes")):
        if not isinstance(node, dict) or not text(node.get("node_id")):
            continue
        old_id = text(node.get("node_id"))
        new_id = stable_id("composite_process_node", composite_id, child.get("process_id"), old_id)
        mapping[old_id] = new_id
        cloned = deepcopy(node)
        cloned["node_id"] = new_id
        cloned["step_id"] = new_id
        cloned["source_child_process_id"] = child.get("process_id")
        nodes.append(cloned)
    edges: list[dict[str, Any]] = []
    for edge in as_list(child.get("edges")):
        if not isinstance(edge, dict):
            continue
        source = mapping.get(text(edge.get("source_node_id")))
        target = mapping.get(text(edge.get("target_node_id")))
        if not source or not target:
            continue
        cloned = deepcopy(edge)
        cloned["edge_id"] = stable_id("composite_process_edge", composite_id, edge.get("edge_id"), source, target)
        cloned["source_node_id"] = source
        cloned["target_node_id"] = target
        cloned["source_child_process_id"] = child.get("process_id")
        edges.append(cloned)
    return nodes, edges, mapping


def _composite_graph(
    process: dict[str, Any],
    child_graphs: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    process_id = text(process.get("process_id"))
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    child_start: dict[str, list[str]] = {}
    child_terminal: dict[str, list[str]] = {}
    object_to_process: dict[str, str] = {}

    for step in as_list(process.get("steps")):
        if not isinstance(step, dict):
            continue
        child_ref = text(step.get("process_ref"))
        object_ref = text(step.get("object_ref"))
        if object_ref and child_ref:
            object_to_process[object_ref] = child_ref
        child = child_graphs.get(child_ref)
        if child is None:
            unknowns.append(
                new_unknown(
                    "MULTI_OBJECT_CHILD_PROCESS_GRAPH_MISSING",
                    f"跨对象流程引用的子流程“{child_ref}”尚未形成可绑定流程图。",
                    related_objects=[object_ref] if object_ref else [],
                    evidence=as_list(process.get("evidence")),
                    severity="P1",
                    blocks_formal_understanding=False,
                    reason_code="MULTI_OBJECT_CHILD_PROCESS_GRAPH_MISSING",
                    details={"process_id": process_id, "child_process_ref": child_ref},
                )
            )
            continue
        cloned_nodes, cloned_edges, mapping = _clone_child_graph(child, process_id)
        nodes.extend(cloned_nodes)
        edges.extend(cloned_edges)
        child_start[child_ref] = [
            mapping[value]
            for value in as_list(child.get("start_node_refs"))
            if value in mapping
        ]
        child_terminal[child_ref] = [
            mapping[value]
            for value in as_list(child.get("terminal_node_refs"))
            if value in mapping
        ]

    for link in as_list(process.get("object_links")):
        if not isinstance(link, dict):
            continue
        source_process = text(link.get("source_process_ref")) or object_to_process.get(text(link.get("source_object_ref")), "")
        target_process = text(link.get("target_process_ref")) or object_to_process.get(text(link.get("target_object_ref")), "")
        sources = child_terminal.get(source_process, [])
        targets = child_start.get(target_process, [])
        if len(sources) != 1 or len(targets) != 1:
            unknowns.append(
                new_unknown(
                    "MULTI_OBJECT_PROCESS_LINK_ENDPOINT_UNRESOLVED",
                    "跨对象流程关系无法唯一绑定到上游终点与下游起点。",
                    related_objects=unique_text([link.get("source_object_ref"), link.get("target_object_ref")]),
                    evidence=as_list(process.get("evidence")),
                    severity="P1",
                    blocks_formal_understanding=False,
                    reason_code="MULTI_OBJECT_PROCESS_LINK_ENDPOINT_UNRESOLVED",
                    details={"process_id": process_id, "relation_id": link.get("relation_id")},
                )
            )
            continue
        edges.append(
            {
                "edge_id": stable_id("multi_object_process_edge", process_id, link.get("relation_id"), sources[0], targets[0]),
                "source_node_id": sources[0],
                "target_node_id": targets[0],
                "relation_type": text(link.get("relation_type")) or "DEPENDS_ON",
                "condition": {"orchestration_markers": as_list(link.get("orchestration_markers"))},
                "binding_refs": as_list(link.get("binding_refs")),
                "source_refs": unique_text(as_list(link.get("source_refs"))),
                "relation_id": link.get("relation_id"),
            }
        )

    waits: list[dict[str, Any]] = []
    for wait in as_list(process.get("waits")):
        if not isinstance(wait, dict):
            continue
        row = deepcopy(wait)
        awaited_process = object_to_process.get(text(wait.get("awaited_object_ref")), "")
        awaiting_process = object_to_process.get(text(wait.get("awaiting_object_ref")), "")
        awaited = child_terminal.get(awaited_process, [])
        awaiting = child_start.get(awaiting_process, [])
        row["awaited_node_refs"] = list(awaited)
        row["awaiting_node_refs"] = list(awaiting)
        if len(awaited) != 1 or len(awaiting) != 1:
            row["status"] = "PARTIAL"
            row["reason_code"] = "PROCESS_WAIT_ENDPOINT_UNRESOLVED"
            unknowns.append(
                new_unknown(
                    "PROCESS_WAIT_ENDPOINT_UNRESOLVED",
                    "流程等待条件无法唯一绑定到等待方与被等待方节点。",
                    related_objects=unique_text([wait.get("awaiting_object_ref"), wait.get("awaited_object_ref")]),
                    evidence=as_list(process.get("evidence")),
                    severity="P1",
                    blocks_formal_understanding=False,
                    reason_code="PROCESS_WAIT_ENDPOINT_UNRESOLVED",
                    details={"process_id": process_id, "wait_id": wait.get("wait_id")},
                )
            )
        else:
            row["status"] = "BOUND"
        waits.append(row)

    ordered, starts, terminals, forks, structural_joins, cycle = _graph_shape(nodes, edges)
    joins = list(structural_joins)
    for join in as_list(process.get("joins")):
        if not isinstance(join, dict):
            continue
        target_process = object_to_process.get(text(join.get("target_object_ref")), "")
        target_nodes = child_start.get(target_process, [])
        predecessor_nodes: list[str] = []
        for object_ref in unique_text(as_list(join.get("incoming_object_refs"))):
            predecessor_nodes.extend(child_terminal.get(object_to_process.get(object_ref, ""), []))
        joins.append(
            {
                "join_id": join.get("join_id"),
                "join_kind": join.get("join_kind"),
                "join_node_id": target_nodes[0] if len(target_nodes) == 1 else "",
                "predecessor_node_ids": unique_text(predecessor_nodes),
                "source_refs": unique_text(as_list(join.get("source_refs"))),
                "status": "BOUND" if len(target_nodes) == 1 and predecessor_nodes else "PARTIAL",
            }
        )

    status = "COMPILED"
    child_partial = any(
        text(child_graphs.get(text(step.get("process_ref")), {}).get("status")) != "COMPILED"
        for step in as_list(process.get("steps"))
        if isinstance(step, dict) and text(step.get("process_ref"))
    )
    if not nodes or cycle or child_partial or unknowns:
        status = "PARTIAL"
    graph = {
        "schema_version": PROCESS_GRAPH_SCHEMA,
        "execution_graph_id": stable_id("business_process_graph", process_id),
        "process_id": process_id,
        "name": process.get("name"),
        "process_type": process.get("process_type"),
        "process_features": unique_text(as_list(process.get("process_features"))),
        "nodes": nodes,
        "edges": edges,
        "start_node_refs": starts,
        "terminal_node_refs": terminals,
        "topological_order": ordered,
        "fork_groups": forks,
        "join_groups": joins,
        "wait_contracts": waits,
        "loops": [dict(row) for row in as_list(process.get("loops")) if isinstance(row, dict)],
        "exception_paths": [dict(row) for row in as_list(process.get("exception_paths")) if isinstance(row, dict)],
        "source_refs": unique_text(as_list(process.get("source_refs"))),
        "evidence": dedupe_evidence(as_list(process.get("evidence"))),
        "unresolved_semantics": unique_text(
            [
                "PROCESS_GRAPH_NO_NODES" if not nodes else "",
                "PROCESS_GRAPH_CYCLE" if cycle else "",
                "PROCESS_GRAPH_CHILD_PARTIAL" if child_partial else "",
                *[text(as_dict(row.get("details")).get("reason_code")) for row in unknowns],
            ]
        ),
        "status": status,
        "semantic_authority": "enterprise_understanding.processes",
        "runtime_executability_claimed": False,
    }
    return graph, unknowns


def _gate(graphs: list[dict[str, Any]], unknowns: list[dict[str, Any]]) -> dict[str, Any]:
    compiled = [row for row in graphs if text(row.get("status")) == "COMPILED"]
    partial = [row for row in graphs if text(row.get("status")) != "COMPILED"]
    traceable = [row for row in graphs if as_list(row.get("evidence")) or as_list(row.get("source_refs"))]
    if partial or unknowns:
        status = "PARTIAL_PROCESS_GRAPH_IR"
    elif compiled:
        status = "PASS"
    else:
        status = "NO_PROCESS_GRAPH_EVIDENCE"
    return {
        "schema": PROCESS_GRAPH_GATE_SCHEMA,
        "status": status,
        "entry_allowed": status == "PASS",
        "metrics": {
            "process_graph_count": len(graphs),
            "compiled_process_graph_count": len(compiled),
            "partial_process_graph_count": len(partial),
            "process_graph_unknown_count": len(unknowns),
            "process_graph_node_count": sum(len(as_list(row.get("nodes"))) for row in graphs),
            "process_graph_edge_count": sum(len(as_list(row.get("edges"))) for row in graphs),
            "process_graph_wait_count": sum(len(as_list(row.get("wait_contracts"))) for row in graphs),
            "process_graph_join_count": sum(len(as_list(row.get("join_groups"))) for row in graphs),
            "source_traceability_rate": round(len(traceable) / len(graphs), 4) if graphs else 1.0,
        },
        "quality_claim": "PROCESS_GRAPH_SEMANTIC_CLOSURE_NOT_RUNTIME_EXECUTABILITY",
        "semantic_authority": "enterprise_understanding.processes",
        "operation_binding_authority": "governed_behavior_implementation_bindings",
        "document_order_inference_allowed": False,
        "automatic_system_inference_allowed": False,
    }


def build_business_process_graphs(
    model: dict[str, Any],
    behaviors: Iterable[dict[str, Any]],
    implementation_bindings: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Return process graphs, Unknowns and a fail-visible semantic gate."""
    processes = [
        row
        for row in as_list(model.get("processes"))
        if isinstance(row, dict) and text(row.get("process_id"))
    ]
    aliases = _operation_aliases(model)
    by_operation, by_operation_object = _behavior_index(behaviors)
    bindings_by_behavior = _binding_index(implementation_bindings)

    atomic_processes = [
        row
        for row in processes
        if text(row.get("process_type")) not in {"MULTI_OBJECT_LINKED", "MULTI_OBJECT_ORCHESTRATION"}
    ]
    composite_processes = [row for row in processes if row not in atomic_processes]

    graphs: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    by_process_id: dict[str, dict[str, Any]] = {}
    for process in atomic_processes:
        graph, graph_unknowns = _atomic_graph(
            process,
            aliases=aliases,
            by_operation=by_operation,
            by_operation_object=by_operation_object,
            bindings_by_behavior=bindings_by_behavior,
        )
        graphs.append(graph)
        by_process_id[text(graph.get("process_id"))] = graph
        unknowns.extend(graph_unknowns)

    for process in composite_processes:
        graph, graph_unknowns = _composite_graph(process, by_process_id)
        graphs.append(graph)
        by_process_id[text(graph.get("process_id"))] = graph
        unknowns.extend(graph_unknowns)

    deduped_unknowns = list(
        {
            text(row.get("unknown_id")): row
            for row in unknowns
            if isinstance(row, dict) and text(row.get("unknown_id"))
        }.values()
    )
    return graphs, deduped_unknowns, _gate(graphs, deduped_unknowns)


__all__ = [
    "PROCESS_GRAPH_SCHEMA",
    "PROCESS_GRAPH_GATE_SCHEMA",
    "build_business_process_graphs",
]
