"""Source-governed cross-object and cross-system process planning.

This module is an adapter into QualiBug's existing Business Process Graph and
multi-step protocol authorities.  It does not own a second scheduler and it
never guesses domain states, HTTP status codes, entity names, foreign keys, or
system targets.

Authority chain:
    source-backed obligation / enterprise process graph
    -> cross-object / cross-system detection
    -> source graph selection or relation-backed graph projection
    -> existing ``multi_step_protocol_core`` compiler
    -> existing Process Graph runtime / cleanup / evidence pipeline

When a concrete data dependency, actor, operation, or target system is missing,
planning fails visibly with structured blockers instead of emitting a chain that
only looks executable.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any, Iterable

from .multi_step_protocol import compile_multi_step_process_protocol


CROSS_ENTITY_BINDING_UNRESOLVED = "CROSS_ENTITY_RELATION_BINDING_UNRESOLVED"
CROSS_ENTITY_OPERATION_UNRESOLVED = "CROSS_ENTITY_OPERATION_UNRESOLVED"
CROSS_ENTITY_ACTOR_UNRESOLVED = "CROSS_ENTITY_ACTOR_UNRESOLVED"
CROSS_SYSTEM_TARGET_UNRESOLVED = "CROSS_SYSTEM_TARGET_UNRESOLVED"
CROSS_ENTITY_GRAPH_AMBIGUOUS = "CROSS_ENTITY_PROCESS_GRAPH_AMBIGUOUS"
CROSS_ENTITY_GRAPH_MISSING = "CROSS_ENTITY_PROCESS_GRAPH_MISSING"
CROSS_ENTITY_RELATION_SCOPE_AMBIGUOUS = (
    "CROSS_ENTITY_RELATION_SCOPE_AMBIGUOUS"
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(*parts: Any) -> str:
    raw = "|".join(_text(part) for part in parts)
    return "xce_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _unique_text(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _operation_identity(operation: dict[str, Any]) -> str:
    return _text(
        operation.get("id")
        or operation.get("operation_id")
        or operation.get("interface_id")
    )


def _operation_index(ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for key in ("operations_by_id", "ops_by_id"):
        for operation_id, raw in _dict(ir.get(key)).items():
            operation = _dict(raw)
            identity = _operation_identity(operation) or _text(operation_id)
            if identity:
                result[identity] = operation
    for raw in _list(ir.get("operations")):
        operation = _dict(raw)
        identity = _operation_identity(operation)
        if identity:
            result[identity] = operation
    return result


def _relation_index(ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in _list(ir.get("relations")):
        relation = _dict(raw)
        relation_id = _text(relation.get("id") or relation.get("relation_id"))
        if relation_id:
            result[relation_id] = relation
    return result


def build_cross_entity_planning_context(
    ir: dict[str, Any],
) -> dict[str, Any]:
    """Build immutable-for-one-run indexes shared by all obligation plans.

    Deep planning may process hundreds or thousands of obligations against the
    same Behavior IR.  Re-indexing operations, relations, and process graphs
    for every obligation made cross-object planning proportional to
    ``obligations × IR size``.  The context keeps authority in the original IR
    while making lookup proportional to the selected graph and its nodes.
    """
    operations = _operation_index(ir)
    relations = _relation_index(ir)
    process_graphs = [
        raw for raw in _list(ir.get("process_graphs")) if isinstance(raw, dict)
    ]
    graphs_by_ref: dict[str, list[dict[str, Any]]] = {}
    graphs_by_operation_ref: dict[str, list[dict[str, Any]]] = {}
    for graph in process_graphs:
        graph_ref = _text(
            graph.get("process_id") or graph.get("execution_graph_id")
        )
        if graph_ref:
            graphs_by_ref.setdefault(graph_ref, []).append(graph)
        for operation_ref in _graph_operation_refs(graph):
            graphs_by_operation_ref.setdefault(operation_ref, []).append(graph)
    return {
        "behavior_ir": ir,
        "operations": operations,
        "relations": relations,
        "process_graphs": process_graphs,
        "graphs_by_ref": graphs_by_ref,
        "graphs_by_operation_ref": graphs_by_operation_ref,
    }


def _planning_context(
    ir: dict[str, Any], context: dict[str, Any] | None
) -> dict[str, Any]:
    return context if isinstance(context, dict) else build_cross_entity_planning_context(ir)


def _operation_objects(operation: dict[str, Any]) -> list[str]:
    return _unique_text(
        [
            *_list(operation.get("object_refs")),
            operation.get("object_ref"),
            operation.get("primary_object_ref"),
            operation.get("entity_ref"),
            operation.get("entity"),
            operation.get("resource"),
        ]
    )


def _operation_system(operation: dict[str, Any]) -> str:
    return _text(
        operation.get("system_ref")
        or operation.get("target_system_ref")
        or operation.get("approved_target_ref")
    )


def _property_spec(obligation: dict[str, Any]) -> dict[str, Any]:
    return _dict(obligation.get("property") or obligation.get("property_spec"))


def _invariant(obligation: dict[str, Any]) -> dict[str, Any]:
    return _dict(obligation.get("invariant") or obligation.get("source_invariant"))


def _expression(obligation: dict[str, Any]) -> dict[str, Any]:
    prop = _property_spec(obligation)
    inv = _invariant(obligation)
    return _dict(
        prop.get("expression")
        or inv.get("expression")
        or obligation.get("expression")
    )


def _required_operation_refs(obligation: dict[str, Any]) -> list[str]:
    prop = _property_spec(obligation)
    refs = _unique_text(
        [
            *_list(obligation.get("required_operations")),
            *_list(prop.get("required_operations")),
            obligation.get("operation_ref"),
            obligation.get("target_operation"),
            prop.get("operation_ref"),
            prop.get("target_operation"),
        ]
    )
    return refs


def _actor_ref(obligation: dict[str, Any], graph: dict[str, Any] | None = None) -> str:
    prop = _property_spec(obligation)
    explicit = _unique_text(
        [
            prop.get("actor_ref"),
            prop.get("treatment_actor_ref"),
            obligation.get("actor_ref"),
            *_list(obligation.get("required_actors")),
        ]
    )
    if len(explicit) == 1:
        return explicit[0]
    if graph:
        graph_actors = _unique_text(
            node.get("actor_ref")
            for node in _list(graph.get("nodes"))
            if isinstance(node, dict)
        )
        if len(graph_actors) == 1:
            return graph_actors[0]
    return ""


def _graph_nodes(graph: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in _list(graph.get("nodes")) if isinstance(row, dict)]


def _graph_operation_refs(graph: dict[str, Any]) -> set[str]:
    return {
        _text(node.get("operation_ref") or node.get("operation_id"))
        for node in _graph_nodes(graph)
        if _text(node.get("operation_ref") or node.get("operation_id"))
    }


def _graph_objects(graph: dict[str, Any]) -> list[str]:
    return _unique_text(
        value
        for node in _graph_nodes(graph)
        for value in [
            *_list(node.get("object_refs")),
            node.get("object_ref"),
            node.get("primary_object_ref"),
        ]
    )


def _graph_systems(graph: dict[str, Any]) -> list[str]:
    return _unique_text(
        node.get("system_ref") or node.get("target_system_ref")
        for node in _graph_nodes(graph)
    )


def _explicit_graph(obligation: dict[str, Any]) -> dict[str, Any]:
    prop = _property_spec(obligation)
    inv = _invariant(obligation)
    expr = _expression(obligation)
    for value in (
        obligation.get("execution_graph"),
        obligation.get("process_graph"),
        prop.get("execution_graph"),
        prop.get("process_graph"),
        inv.get("execution_graph"),
        inv.get("process_graph"),
        expr.get("execution_graph"),
        expr.get("process_graph"),
    ):
        graph = _dict(value)
        if graph:
            return deepcopy(graph)
    return {}


def _graph_ref(obligation: dict[str, Any]) -> str:
    prop = _property_spec(obligation)
    inv = _invariant(obligation)
    expr = _expression(obligation)
    return _text(
        obligation.get("process_graph_ref")
        or obligation.get("process_id")
        or prop.get("process_graph_ref")
        or prop.get("process_id")
        or inv.get("process_graph_ref")
        or inv.get("process_id")
        or expr.get("process_graph_ref")
        or expr.get("process_id")
    )


def _select_ir_graph(
    obligation: dict[str, Any],
    ir: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    explicit = _explicit_graph(obligation)
    if explicit:
        return explicit, []

    planning_context = _planning_context(ir, context)
    graph_ref = _graph_ref(obligation)
    required_ops = set(_required_operation_refs(obligation))
    if graph_ref:
        candidates = list(
            _list(_dict(planning_context.get("graphs_by_ref")).get(graph_ref))
        )
    elif required_ops:
        candidate_lists = [
            _list(
                _dict(planning_context.get("graphs_by_operation_ref")).get(
                    operation_ref
                )
            )
            for operation_ref in required_ops
        ]
        if not candidate_lists or any(not rows for rows in candidate_lists):
            candidates = []
        else:
            candidate_ids = {
                id(graph) for graph in candidate_lists[0] if isinstance(graph, dict)
            }
            for rows in candidate_lists[1:]:
                candidate_ids.intersection_update(
                    id(graph) for graph in rows if isinstance(graph, dict)
                )
            candidates = [
                graph
                for graph in candidate_lists[0]
                if isinstance(graph, dict) and id(graph) in candidate_ids
            ]
    else:
        candidates = []

    if len(candidates) == 1:
        return deepcopy(candidates[0]), []
    if len(candidates) > 1:
        return {}, [
            {
                "reason_code": CROSS_ENTITY_GRAPH_AMBIGUOUS,
                "detail": f"matching_process_graphs:{len(candidates)}",
            }
        ]
    return {}, []


def _relation_entities(relation: dict[str, Any]) -> tuple[str, str]:
    return (
        _text(
            relation.get("from_entity")
            or relation.get("source_entity")
            or relation.get("parent_entity")
            or relation.get("left_entity")
        ),
        _text(
            relation.get("to_entity")
            or relation.get("target_entity")
            or relation.get("child_entity")
            or relation.get("right_entity")
        ),
    )


def _relation_binding_refs(
    relation: dict[str, Any],
    *,
    producer_node_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return source-declared output specs and consumer binding refs.

    Accepted shapes intentionally mirror Process Graph runtime contracts.  A
    shorthand is accepted only when both source response identity and consumer
    target are explicit; no field name or JSON path is invented.
    """
    output_specs = [
        dict(row)
        for row in _list(
            relation.get("output_binding_specs")
            or relation.get("producer_output_specs")
        )
        if isinstance(row, dict)
    ]
    input_refs = [
        dict(row)
        for row in _list(relation.get("binding_refs") or relation.get("input_binding_refs"))
        if isinstance(row, dict)
    ]
    if output_specs and input_refs:
        for row in input_refs:
            row.setdefault("producer_node_id", producer_node_id)
        return output_specs, input_refs

    canonical = _text(
        relation.get("canonical_field_id")
        or relation.get("producer_output_field")
        or relation.get("source_field")
    )
    source_path = _text(
        relation.get("source_json_path")
        or relation.get("json_path")
        or relation.get("source_path")
        or relation.get("response_field")
    )
    target = _text(
        relation.get("target")
        or relation.get("consumer_target")
        or relation.get("target_location")
        or relation.get("target_field")
        or relation.get("foreign_key")
        or relation.get("relation_key")
    )
    if canonical and source_path and target:
        return (
            [{"canonical_field_id": canonical, "json_path": source_path}],
            [
                {
                    "producer_node_id": producer_node_id,
                    "producer_output_field": canonical,
                    "target": target,
                }
            ],
        )
    return [], []


def _relation_graph(
    obligation: dict[str, Any],
    ir: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    planning_context = _planning_context(ir, context)
    operations = _dict(planning_context.get("operations"))
    relation_id = _text(
        obligation.get("relation_id")
        or _property_spec(obligation).get("relation_id")
        or _expression(obligation).get("relation_id")
    )
    relation = (
        _dict(_dict(planning_context.get("relations")).get(relation_id))
        if relation_id
        else {}
    )
    operation_refs = _required_operation_refs(obligation)
    blockers: list[dict[str, Any]] = []
    if len(operation_refs) < 2:
        return {}, blockers
    if not relation:
        blockers.append(
            {
                "reason_code": CROSS_ENTITY_GRAPH_MISSING,
                "detail": "ordered cross-object operations require a source relation or process graph",
            }
        )
        return {}, blockers
    if len(operation_refs) != 2:
        blockers.append(
            {
                "reason_code": CROSS_ENTITY_RELATION_SCOPE_AMBIGUOUS,
                "detail": (
                    "one relation can project exactly two operations; "
                    "multi-edge processes require a source process graph"
                ),
            }
        )
        return {}, blockers

    actor_ref = _actor_ref(obligation)

    nodes: list[dict[str, Any]] = []
    for index, operation_ref in enumerate(operation_refs):
        operation = _dict(operations.get(operation_ref))
        if not operation:
            blockers.append(
                {
                    "reason_code": CROSS_ENTITY_OPERATION_UNRESOLVED,
                    "detail": f"operation_ref:{operation_ref}",
                }
            )
            continue
        node_id = _stable_id("relation_node", relation_id, operation_ref, index)
        nodes.append(
            {
                "node_id": node_id,
                "step_id": node_id,
                "operation_ref": operation_ref,
                "actor_ref": _text(operation.get("actor_ref")) or actor_ref,
                "system_ref": _operation_system(operation),
                "object_refs": _operation_objects(operation),
                "method": _text(operation.get("method")),
                "path": _text(
                    operation.get("path")
                    or operation.get("raw_path")
                    or operation.get("path_template")
                ),
                "input_binding_refs": [],
                "output_binding_specs": [],
                "compensation_operation_ref": _text(
                    operation.get("compensation_operation_ref")
                    or operation.get("cleanup_operation_ref")
                ),
                "source_refs": deepcopy(_list(obligation.get("source_refs"))),
            }
        )

    if len(nodes) != len(operation_refs):
        return {}, blockers

    missing_actor_nodes = [
        _text(node.get("node_id"))
        for node in nodes
        if not _text(node.get("actor_ref"))
    ]
    if missing_actor_nodes:
        blockers.append(
            {
                "reason_code": CROSS_ENTITY_ACTOR_UNRESOLVED,
                "detail": ",".join(missing_actor_nodes),
            }
        )

    edges: list[dict[str, Any]] = []
    for index in range(len(nodes) - 1):
        source = nodes[index]
        target = nodes[index + 1]
        edge = {
            "edge_id": _stable_id("relation_edge", relation_id, source["node_id"], target["node_id"]),
            "source_node_id": source["node_id"],
            "target_node_id": target["node_id"],
            "relation_type": _text(
                relation.get("relation_type") or relation.get("type")
            )
            or "SOURCE_DECLARED_SEQUENCE",
            "binding_refs": [],
            "source_refs": deepcopy(_list(obligation.get("source_refs"))),
        }
        if index == 0 and relation:
            output_specs, input_refs = _relation_binding_refs(
                relation, producer_node_id=source["node_id"]
            )
            source["output_binding_specs"] = output_specs
            target["input_binding_refs"] = input_refs
            edge["binding_refs"] = deepcopy(input_refs)

            left_entity, right_entity = _relation_entities(relation)
            relation_requires_binding = bool(
                left_entity
                and right_entity
                and left_entity != right_entity
                and _text(relation.get("type") or relation.get("relation_type")).upper()
                not in {"SEQUENCE", "SOURCE_DECLARED_SEQUENCE"}
            )
            if relation_requires_binding and not (output_specs and input_refs):
                blockers.append(
                    {
                        "reason_code": CROSS_ENTITY_BINDING_UNRESOLVED,
                        "detail": f"relation_id:{relation_id}",
                    }
                )
        edges.append(edge)

    systems = _unique_text(node.get("system_ref") for node in nodes)
    declares_cross_system = bool(
        _text(obligation.get("process_type")).upper() == "CROSS_SYSTEM"
        or "CROSS_SYSTEM" in {
            _text(value).upper()
            for value in _list(obligation.get("process_features"))
        }
        or len(systems) > 1
    )
    if declares_cross_system:
        missing_system_nodes = [
            _text(node.get("node_id"))
            for node in nodes
            if not _text(node.get("system_ref"))
        ]
        if missing_system_nodes:
            blockers.append(
                {
                    "reason_code": CROSS_SYSTEM_TARGET_UNRESOLVED,
                    "detail": ",".join(missing_system_nodes),
                }
            )

    process_id = _text(obligation.get("process_id")) or _stable_id(
        "relation_process", obligation.get("obligation_id"), relation_id
    )
    return (
        {
            "schema_version": "qualibug.execution-graph.v1",
            "execution_graph_id": _stable_id("relation_graph", process_id),
            "process_id": process_id,
            "nodes": nodes,
            "edges": edges,
            "topological_order": [node["node_id"] for node in nodes],
            "start_node_refs": [nodes[0]["node_id"]],
            "terminal_node_refs": [nodes[-1]["node_id"]],
            "wait_contracts": [],
            "source_refs": deepcopy(_list(obligation.get("source_refs"))),
            "source_kind": "OBLIGATION_REQUIRED_OPERATIONS_AND_RELATION",
            "semantic_authority": "source_declared_obligation_and_behavior_ir_relation",
        },
        blockers,
    )


def detect_cross_entity_requirement(
    obligation: dict[str, Any],
    ir: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect cross-object/cross-system work from structured source evidence."""
    planning_context = _planning_context(ir, context)
    operations = _dict(planning_context.get("operations"))
    relation_id = _text(
        obligation.get("relation_id")
        or _property_spec(obligation).get("relation_id")
        or _expression(obligation).get("relation_id")
    )
    relation = (
        _dict(_dict(planning_context.get("relations")).get(relation_id))
        if relation_id
        else {}
    )
    graph, graph_blockers = _select_ir_graph(
        obligation, ir, context=planning_context
    )
    required_ops = _required_operation_refs(obligation)

    objects = _unique_text(
        [
            obligation.get("entity"),
            obligation.get("entity_ref"),
            obligation.get("target_entity"),
            obligation.get("dependent_entity"),
            obligation.get("reference_entity"),
            *_list(obligation.get("required_entities")),
            *_graph_objects(graph),
            *(
                value
                for operation_ref in required_ops
                for value in _operation_objects(_dict(operations.get(operation_ref)))
            ),
            *_relation_entities(relation),
        ]
    )
    systems = _unique_text(
        [
            *_list(obligation.get("system_refs")),
            *_graph_systems(graph),
            *(
                _operation_system(_dict(operations.get(operation_ref)))
                for operation_ref in required_ops
            ),
        ]
    )

    explicit_mechanism = _text(
        obligation.get("mechanism")
        or obligation.get("risk_family")
        or _invariant(obligation).get("rule_type")
        or _expression(obligation).get("rule_type")
    ).upper()
    signals: list[str] = []
    if graph:
        signals.append("source_process_graph")
    if relation:
        signals.append(f"relation_id={relation_id}")
    if len(objects) > 1:
        signals.append(f"object_count={len(objects)}")
    if len(systems) > 1:
        signals.append(f"system_count={len(systems)}")
    if "CROSS_ENTITY" in explicit_mechanism or "CROSS_OBJECT" in explicit_mechanism:
        signals.append(f"mechanism={explicit_mechanism}")
    if "CROSS_SYSTEM" in explicit_mechanism:
        signals.append(f"mechanism={explicit_mechanism}")
    if len(_list(obligation.get("required_entities"))) > 1:
        signals.append("required_entities")
    if _dict(obligation.get("entity_roles")):
        signals.append("entity_roles")

    operation_ref = required_ops[-1] if required_ops else ""
    operation = _dict(operations.get(operation_ref))
    request_schema = _dict(
        operation.get("request_schema") or operation.get("request_body")
    )
    properties = _dict(request_schema.get("properties"))
    identity_fields = [
        name
        for name, schema in properties.items()
        if isinstance(schema, dict)
        and (
            _text(schema.get("format")).lower() in {"uuid", "uri-reference"}
            or _text(schema.get("x-entity-ref"))
            or _text(schema.get("x-object-ref"))
        )
    ]
    if len(identity_fields) >= 2 and len(objects) <= 1:
        signals.append(f"self_reference_identity_fields={len(identity_fields)}")

    if len(systems) > 1:
        chain_type = "CROSS_SYSTEM_PROCESS"
    elif graph:
        chain_type = "PROCESS_GRAPH"
    elif relation and len(objects) > 1:
        chain_type = "CROSS_ENTITY_RELATION"
    elif len(identity_fields) >= 2:
        chain_type = "SELF_REFERENCE"
    elif len(objects) > 1 or len(required_ops) > 1:
        chain_type = "MULTI_OBJECT_PROCESS"
    else:
        chain_type = ""

    return {
        "is_cross_entity": bool(chain_type and signals),
        "chain_type": chain_type,
        "required_entities": [
            {"role": f"object_{index + 1}", "entity": entity}
            for index, entity in enumerate(objects)
        ],
        "signals": signals,
        "objects": objects,
        "systems": systems,
        "operation_ref": operation_ref,
        "required_operations": required_ops,
        "relation_id": relation_id,
        "graph_selection_blockers": graph_blockers,
        "source_graph": graph,
    }


def _validate_source_graph(
    graph: dict[str, Any],
    detection: dict[str, Any],
    obligation: dict[str, Any],
    operations: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    nodes = _graph_nodes(graph)
    default_actor = _actor_ref(obligation, graph)
    for node in nodes:
        node_id = _text(node.get("node_id") or node.get("step_id"))
        operation_ref = _text(
            node.get("operation_ref") or node.get("operation_id")
        )
        if not operation_ref:
            blockers.append(
                {
                    "reason_code": CROSS_ENTITY_OPERATION_UNRESOLVED,
                    "detail": f"node_id:{node_id}",
                }
            )
        elif operation_ref not in operations:
            blockers.append(
                {
                    "reason_code": CROSS_ENTITY_OPERATION_UNRESOLVED,
                    "detail": f"node_id:{node_id}:operation_ref:{operation_ref}",
                }
            )
        if not (_text(node.get("actor_ref")) or default_actor):
            blockers.append(
                {
                    "reason_code": CROSS_ENTITY_ACTOR_UNRESOLVED,
                    "detail": f"node_id:{node_id}",
                }
            )
    if detection.get("chain_type") == "CROSS_SYSTEM_PROCESS":
        missing = [
            _text(node.get("node_id") or node.get("step_id"))
            for node in nodes
            if not _text(node.get("system_ref") or node.get("target_system_ref"))
        ]
        if missing:
            blockers.append(
                {
                    "reason_code": CROSS_SYSTEM_TARGET_UNRESOLVED,
                    "detail": ",".join(missing),
                }
            )
    graph_operations = _graph_operation_refs(graph)
    missing_required_operations = [
        operation_ref
        for operation_ref in _required_operation_refs(obligation)
        if operation_ref not in graph_operations
    ]
    if missing_required_operations:
        blockers.append(
            {
                "reason_code": CROSS_ENTITY_OPERATION_UNRESOLVED,
                "detail": (
                    "required_operations_not_in_graph:"
                    + ",".join(missing_required_operations)
                ),
            }
        )
    return blockers


def _entity_chains(graph: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for node in _graph_nodes(graph):
        objects = _operation_objects(node) or ["unscoped"]
        for object_ref in objects:
            result.setdefault(object_ref, []).append(
                {
                    "operation_ref": _text(node.get("operation_ref")),
                    "intent": _text(node.get("intent")) or "business_process_step",
                    "entity_role": object_ref,
                    "system_ref": _text(node.get("system_ref")),
                    "actor_ref": _text(node.get("actor_ref")),
                    "node_id": _text(node.get("node_id")),
                }
            )
    return result


def build_cross_entity_chain(
    detection: dict[str, Any],
    obligation: dict[str, Any],
    ir: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile one source-governed graph through the existing protocol authority."""
    if not detection.get("is_cross_entity"):
        return {
            "status": "INSUFFICIENT_INFO",
            "reason": "not_cross_object_or_cross_system",
            "blockers": [],
        }

    planning_context = _planning_context(ir, context)
    graph = deepcopy(_dict(detection.get("source_graph")))
    blockers = [
        dict(row)
        for row in _list(detection.get("graph_selection_blockers"))
        if isinstance(row, dict)
    ]
    if not graph and not blockers:
        graph, relation_blockers = _relation_graph(
            obligation, ir, context=planning_context
        )
        blockers.extend(relation_blockers)
    if not graph:
        blockers.append(
            {
                "reason_code": CROSS_ENTITY_GRAPH_MISSING,
                "detail": "no unique source process graph and no source-declared relation chain",
            }
        )
        return {
            "status": "INSUFFICIENT_INFO",
            "reason": blockers[0]["reason_code"],
            "blockers": blockers,
        }

    blockers.extend(
        _validate_source_graph(
            graph,
            detection,
            obligation,
            _dict(planning_context.get("operations")),
        )
    )
    if blockers:
        return {
            "status": "INSUFFICIENT_INFO",
            "reason": blockers[0]["reason_code"],
            "blockers": blockers,
            "execution_graph": graph,
        }

    operation_refs = _required_operation_refs(obligation)
    target_operation = (
        operation_refs[-1]
        if operation_refs
        else next(
            (
                _text(node.get("operation_ref"))
                for node in reversed(_graph_nodes(graph))
                if _text(node.get("operation_ref"))
            ),
            "",
        )
    )
    actor_ref = _actor_ref(obligation, graph)
    prop = deepcopy(_property_spec(obligation))
    prop["process_graph"] = graph
    prop.setdefault("source_refs", deepcopy(_list(obligation.get("source_refs"))))
    expected_order = _list(obligation.get("expected_order"))
    if expected_order:
        prop["expected_order"] = deepcopy(expected_order)

    compiled = compile_multi_step_process_protocol(
        {
            "risk_family": _text(obligation.get("risk_family")) or "cross_entity_chain",
            "operation_ref": target_operation,
            "treatment_actor_ref": actor_ref,
            "property_spec": prop,
            "behavior_ir": ir,
        }
    )
    if _text(compiled.get("status")) != "COMPILED":
        reason_code = _text(compiled.get("reason_code")) or CROSS_ENTITY_GRAPH_MISSING
        return {
            "status": "INSUFFICIENT_INFO",
            "reason": reason_code,
            "blockers": [
                {
                    "reason_code": reason_code,
                    "detail": _text(compiled.get("detail")),
                }
            ],
            "execution_graph": _dict(compiled.get("execution_graph")) or graph,
        }

    execution_graph = deepcopy(_dict(compiled.get("execution_graph")))
    object_refs = _graph_objects(execution_graph)
    system_refs = _graph_systems(execution_graph)
    proof = {
        "proof_id": _stable_id(
            "chain_proof",
            obligation.get("obligation_id"),
            execution_graph.get("execution_graph_id"),
        ),
        "proof_type": "CROSS_OBJECT_SYSTEM_PROCESS_GRAPH_PROOF",
        "chain_type": detection.get("chain_type"),
        "execution_graph_id": execution_graph.get("execution_graph_id"),
        "process_id": execution_graph.get("process_id"),
        "node_count": len(_graph_nodes(execution_graph)),
        "edge_count": len(_list(execution_graph.get("edges"))),
        "object_refs": object_refs,
        "system_refs": system_refs,
        "source_kind": execution_graph.get("source_kind"),
        "semantic_authority": graph.get("semantic_authority")
        or "source_declared_process_graph",
        "execution_ready": True,
    }
    return {
        "status": "BUILT",
        "chain_type": detection.get("chain_type"),
        "execution_graph": execution_graph,
        "treatment_plan": deepcopy(_list(compiled.get("treatment_plan"))),
        "control_plan": deepcopy(_list(compiled.get("control_plan"))),
        "cleanup_plan": deepcopy(_list(compiled.get("cleanup_plan"))),
        "assertion": deepcopy(_dict(compiled.get("assertion"))),
        "observers": deepcopy(_list(compiled.get("observers"))),
        "source_refs": deepcopy(_list(compiled.get("source_refs"))),
        "entity_chains": _entity_chains(execution_graph),
        "target_operation": target_operation,
        "chain_proof": proof,
        "blockers": [],
    }


def _build_dependency_proof(
    detection: dict[str, Any],
    chain_result: dict[str, Any],
    obligation: dict[str, Any],
) -> dict[str, Any]:
    graph = _dict(chain_result.get("execution_graph"))
    binding_edges = [
        {
            "source_node_id": edge.get("source_node_id"),
            "target_node_id": edge.get("target_node_id"),
            "relation_type": edge.get("relation_type"),
            "binding_ref_count": len(_list(edge.get("binding_refs"))),
        }
        for edge in _list(graph.get("edges"))
        if isinstance(edge, dict)
    ]
    return {
        "proof_id": _stable_id(
            "dependency_proof",
            obligation.get("obligation_id"),
            graph.get("execution_graph_id"),
        ),
        "proof_type": "PROCESS_GRAPH_DEPENDENCY_PROOF",
        "execution_graph_id": graph.get("execution_graph_id"),
        "chain_type": detection.get("chain_type"),
        "objects": deepcopy(_list(detection.get("objects"))),
        "systems": deepcopy(_list(detection.get("systems"))),
        "edges": binding_edges,
        "source_refs": deepcopy(_list(obligation.get("source_refs"))),
        "chain_status": chain_result.get("status"),
    }


def _generate_experiments_from_chain(
    chain_result: dict[str, Any],
    detection: dict[str, Any],
    obligation: dict[str, Any],
    ir: dict[str, Any],
    budget: int,
) -> list[dict[str, Any]]:
    del ir
    graph = deepcopy(_dict(chain_result.get("execution_graph")))
    treatment = deepcopy(_list(chain_result.get("treatment_plan")))
    assertion = deepcopy(_dict(chain_result.get("assertion")))
    prop = _property_spec(obligation)
    expected_outcome = _text(
        obligation.get("expected_outcome")
        or prop.get("expected_outcome")
        or assertion.get("kind")
    )
    experiment = {
        "experiment_id": _stable_id(
            "process_graph_experiment",
            obligation.get("obligation_id"),
            graph.get("execution_graph_id"),
        ),
        "experiment_type": "SOURCE_DECLARED_PROCESS",
        "description": _text(obligation.get("description") or prop.get("description")),
        "execution_graph": graph,
        "control_plan": deepcopy(_list(chain_result.get("control_plan"))),
        "treatment_plan": treatment,
        "cleanup_plan": deepcopy(_list(chain_result.get("cleanup_plan"))),
        "setup_chain": deepcopy(treatment[:-1]),
        "target_operation": chain_result.get("target_operation"),
        "expected_outcome": expected_outcome,
        "assertion": assertion,
        "observers": deepcopy(_list(chain_result.get("observers"))),
        "source_refs": deepcopy(_list(chain_result.get("source_refs"))),
        "chain_proof": deepcopy(_dict(chain_result.get("chain_proof"))),
        "cross_system": len(_graph_systems(graph)) > 1,
    }
    return [experiment][: max(0, budget)]


def plan_cross_entity_experiments(
    obligation: dict[str, Any],
    ir: dict[str, Any],
    budget: int = 8,
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Plan graph-backed experiments or return an explicit fail-closed result."""
    planning_context = _planning_context(ir, context)
    detection = detect_cross_entity_requirement(
        obligation, ir, context=planning_context
    )
    if not detection.get("is_cross_entity"):
        return {
            "status": "NOT_CROSS_ENTITY",
            "reason": "No structured cross-object or cross-system evidence",
            "signals": detection.get("signals", []),
            "direct_db_write": False,
        }

    chain_result = build_cross_entity_chain(
        detection, obligation, ir, context=planning_context
    )
    if chain_result.get("status") != "BUILT":
        return {
            "status": "INSUFFICIENT_INFO",
            "reason": chain_result.get("reason"),
            "blockers": deepcopy(_list(chain_result.get("blockers"))),
            "detection": detection,
            "execution_graph": deepcopy(_dict(chain_result.get("execution_graph"))),
            "direct_db_write": False,
        }

    experiments = _generate_experiments_from_chain(
        chain_result, detection, obligation, ir, budget
    )
    dependency_proof = _build_dependency_proof(
        detection, chain_result, obligation
    )
    return {
        "status": "EXPLORED",
        "chain_type": detection.get("chain_type"),
        "experiments": experiments,
        "execution_graph": deepcopy(_dict(chain_result.get("execution_graph"))),
        "treatment_plan": deepcopy(_list(chain_result.get("treatment_plan"))),
        "cleanup_plan": deepcopy(_list(chain_result.get("cleanup_plan"))),
        "assertion": deepcopy(_dict(chain_result.get("assertion"))),
        "observers": deepcopy(_list(chain_result.get("observers"))),
        "chain_proof": deepcopy(_dict(chain_result.get("chain_proof"))),
        "dependency_proof": dependency_proof,
        "entity_chains": deepcopy(_dict(chain_result.get("entity_chains"))),
        "detection_signals": deepcopy(_list(detection.get("signals"))),
        "direct_db_write": False,
    }


def build_chain_proof(
    chain_result: dict[str, Any],
    obligation: dict[str, Any],
) -> dict[str, Any]:
    """Return a stable public proof projection for compatibility callers."""
    proof = deepcopy(_dict(chain_result.get("chain_proof")))
    status = _text(chain_result.get("status"))
    return {
        **proof,
        "obligation_id": _text(
            obligation.get("obligation_id") or obligation.get("id")
        ),
        "rule_id": _text(
            _invariant(obligation).get("rule_id")
            or _invariant(obligation).get("id")
            or obligation.get("invariant_id")
        ),
        "verdict": "CHAIN_BUILT" if status == "BUILT" else "CHAIN_FAILED",
        "blockers": deepcopy(_list(chain_result.get("blockers"))),
    }


__all__ = [
    "CROSS_ENTITY_BINDING_UNRESOLVED",
    "CROSS_ENTITY_OPERATION_UNRESOLVED",
    "CROSS_ENTITY_ACTOR_UNRESOLVED",
    "CROSS_SYSTEM_TARGET_UNRESOLVED",
    "CROSS_ENTITY_GRAPH_AMBIGUOUS",
    "CROSS_ENTITY_GRAPH_MISSING",
    "CROSS_ENTITY_RELATION_SCOPE_AMBIGUOUS",
    "build_cross_entity_planning_context",
    "detect_cross_entity_requirement",
    "build_cross_entity_chain",
    "plan_cross_entity_experiments",
    "build_chain_proof",
]
