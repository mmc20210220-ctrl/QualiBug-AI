"""Build the enterprise business understanding model from governed knowledge facts."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

from .gate import assess_understanding_model
from .lifecycle_builder import build_lifecycles
from .object_graph import build_object_graph
from .schema import (
    ACTOR_SCHEMA,
    MODEL_SCHEMA,
    OBJECT_SCHEMA,
    OPERATION_SCHEMA,
    PROCESS_SCHEMA,
    as_dict,
    as_list,
    dedupe_evidence,
    empty_model,
    evidence_from_fact,
    new_unknown,
    source_evidence,
    stable_id,
    text,
    unique_text,
)

_IDENTITY_FIELD_RE = re.compile(r"(?:^id$|_id$|编号$|单号$|编码$|主键|唯一标识|唯一编号)", re.I)
# Language vocabulary for source-backed process path kinds — not industry rules.
_PARALLEL_MARKER_RE = re.compile(r"同时|并行|同步进行|一并执行|同时进行|并行处理")
_WITHDRAW_OPS = frozenset({"撤回", "撤销"})
_RETURN_OPS = frozenset({"审批退回", "驳回"})
_COMPENSATION_OPS = frozenset({"核销", "退款"})
_LINK_RELATION_TYPES = frozenset({"GENERATES", "CREATES", "COMPENSATES", "DEPENDS_ON"})


def _transition_conditions(transition: dict[str, Any]) -> list[str]:
    return unique_text(as_list(transition.get("conditions")))


def _outcome_discriminator(transition: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    operation = text(transition.get("operation_ref") or transition.get("event"))
    return operation, tuple(sorted(_transition_conditions(transition)))


def _path_kind_for_operation(operation_ref: str) -> str:
    if operation_ref in _WITHDRAW_OPS:
        return "WITHDRAW"
    if operation_ref in _RETURN_OPS:
        return "RETURN"
    if operation_ref in _COMPENSATION_OPS:
        return "COMPENSATION"
    return "OUTCOME"


def _transition_source_text(transition: dict[str, Any]) -> str:
    chunks = [
        *_transition_conditions(transition),
        *[text(as_dict(row).get("quote")) for row in as_list(transition.get("evidence"))],
    ]
    return " ".join(chunk for chunk in chunks if chunk)


def _has_parallel_marker(transition: dict[str, Any]) -> bool:
    return bool(_PARALLEL_MARKER_RE.search(_transition_source_text(transition)))


def _complete_allowed_transitions(lifecycle: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in as_list(lifecycle.get("transitions"))
        if isinstance(row, dict)
        and text(row.get("transition_kind")) == "ALLOWED"
        and text(row.get("completeness")) == "COMPLETE"
    ]


def _lifecycle_graph(
    transitions: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int], set[str]] | None:
    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    indegree: dict[str, int] = defaultdict(int)
    states: set[str] = set()
    for transition in transitions:
        source = text(transition.get("from_state"))
        target = text(transition.get("to_state"))
        if not source or not target:
            return None
        outgoing[source].append(transition)
        indegree[target] += 1
        states.update({source, target})
    return outgoing, indegree, states


def _detect_cycles(
    outgoing: dict[str, list[dict[str, Any]]],
    states: set[str],
) -> list[list[str]]:
    cycles: list[list[str]] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> None:
        if node in visiting:
            if node in stack:
                cycles.append(stack[stack.index(node) :] + [node])
            return
        if node in visited:
            return
        visiting.add(node)
        stack.append(node)
        for transition in outgoing.get(node, []):
            visit(text(transition.get("to_state")))
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for state in sorted(states):
        visit(state)
    deduped: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for cycle in cycles:
        key = tuple(cycle)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cycle)
    return deduped


def _branch_points_distinguished(
    outgoing: dict[str, list[dict[str, Any]]],
) -> tuple[bool, list[str]]:
    """Return whether every multi-outgoing state is source-distinguished."""
    underdetermined: list[str] = []
    for state, rows in outgoing.items():
        if len(rows) <= 1:
            continue
        discriminators = [_outcome_discriminator(row) for row in rows]
        if any(not operation and not conditions for operation, conditions in discriminators):
            underdetermined.append(state)
            continue
        if len(set(discriminators)) != len(discriminators):
            underdetermined.append(state)
    return not underdetermined, underdetermined


def _step_from_transition(transition: dict[str, Any], order: int) -> dict[str, Any]:
    operation_ref = text(transition.get("operation_ref") or transition.get("event"))
    return {
        "order": order,
        "from_state": transition.get("from_state"),
        "operation_ref": transition.get("operation_ref"),
        "event": transition.get("event"),
        "to_state": transition.get("to_state"),
        "conditions": _transition_conditions(transition),
        "transition_id": transition.get("transition_id"),
        "path_kind": _path_kind_for_operation(operation_ref),
    }


def _linear_chain_from(
    start: str,
    outgoing: dict[str, list[dict[str, Any]]],
    *,
    stop_states: set[str] | None = None,
) -> list[dict[str, Any]] | None:
    ordered: list[dict[str, Any]] = []
    current = start
    visited: set[str] = set()
    stops = stop_states or set()
    while current in outgoing and current not in stops:
        if current in visited:
            return None
        rows = outgoing[current]
        if len(rows) != 1:
            break
        visited.add(current)
        transition = rows[0]
        ordered.append(transition)
        current = text(transition.get("to_state"))
    return ordered


def _unique_chain_process(lifecycle: dict[str, Any]) -> dict[str, Any] | None:
    transitions = _complete_allowed_transitions(lifecycle)
    if len(transitions) < 2:
        return None
    graph = _lifecycle_graph(transitions)
    if graph is None:
        return None
    outgoing, indegree, states = graph
    if any(len(rows) > 1 for rows in outgoing.values()) or any(value > 1 for value in indegree.values()):
        return None
    starts = sorted(state for state in states if indegree.get(state, 0) == 0)
    if len(starts) != 1:
        return None
    ordered = _linear_chain_from(starts[0], outgoing)
    if ordered is None or len(ordered) != len(transitions):
        return None
    if _detect_cycles(outgoing, states):
        return None
    object_ref = text(lifecycle.get("object_ref"))
    evidence = dedupe_evidence([row for transition in ordered for row in as_list(transition.get("evidence"))])
    terminal = text(ordered[-1].get("to_state"))
    return {
        "schema": PROCESS_SCHEMA,
        "process_id": stable_id("business_process", object_ref, [row.get("transition_id") for row in ordered]),
        "name": f"{object_ref}生命周期流程",
        "process_type": "LIFECYCLE_UNIQUE_CHAIN",
        "trigger": {"state": starts[0]},
        "inputs": [object_ref],
        "outputs": [{"object_ref": object_ref, "state": terminal}],
        "participants": unique_text([actor for transition in ordered for actor in as_list(transition.get("actor_refs"))]),
        "steps": [_step_from_transition(transition, index + 1) for index, transition in enumerate(ordered)],
        "branches": [],
        "loops": [],
        "parallel_groups": [],
        "exception_paths": [],
        "exceptions": unique_text([value for transition in ordered for value in as_list(transition.get("exceptions"))]),
        "evidence": evidence,
        "status": "UNDERSTOOD",
        "derivation": "unique_source_backed_lifecycle_chain",
    }


def _project_nonlinear_lifecycle_process(
    lifecycle: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Project conditional / loop / withdraw / return / compensation structure.

    Never invents branches from document order. Underdetermined multi-outcome
    states become PARTIAL + visible unknowns instead of silent omission.
    """
    transitions = _complete_allowed_transitions(lifecycle)
    if len(transitions) < 2:
        return None, []
    graph = _lifecycle_graph(transitions)
    if graph is None:
        return None, []
    outgoing, indegree, states = graph
    object_ref = text(lifecycle.get("object_ref"))
    evidence = dedupe_evidence([row for transition in transitions for row in as_list(transition.get("evidence"))])
    unknowns: list[dict[str, Any]] = []

    branch_states = sorted(state for state, rows in outgoing.items() if len(rows) > 1)
    merge_states = sorted(state for state in states if indegree.get(state, 0) > 1)
    cycles = _detect_cycles(outgoing, states)
    starts = sorted(state for state in states if indegree.get(state, 0) == 0)
    distinguished, underdetermined_states = _branch_points_distinguished(outgoing)

    if not branch_states and not cycles and not merge_states:
        return None, []

    branches: list[dict[str, Any]] = []
    parallel_groups: list[dict[str, Any]] = []
    exception_paths: list[dict[str, Any]] = []
    for state in branch_states:
        rows = outgoing[state]
        if state in underdetermined_states:
            unknowns.append(
                new_unknown(
                    "PROCESS_BRANCH_UNDERDETERMINED",
                    f"{object_ref}在状态“{state}”存在多个后续流转，但资料未用可区分的操作或条件说明各分支，不能按文档顺序臆造流程。",
                    related_objects=[object_ref],
                    related_operations=unique_text(
                        [row.get("operation_ref") or row.get("event") for row in rows]
                    ),
                    evidence=dedupe_evidence([item for row in rows for item in as_list(row.get("evidence"))]),
                    severity="P0",
                    blocks_formal_understanding=True,
                    reason_code="PROCESS_BRANCH_UNDERDETERMINED",
                    details={
                        "from_state": state,
                        "candidate_targets": sorted({text(row.get("to_state")) for row in rows}),
                    },
                )
            )
            continue
        parallel_rows = [row for row in rows if _has_parallel_marker(row)]
        if parallel_rows and len(parallel_rows) == len(rows):
            parallel_groups.append(
                {
                    "group_id": stable_id("process_parallel", object_ref, state, [row.get("transition_id") for row in rows]),
                    "from_state": state,
                    "marker": "SOURCE_PARALLEL_MARKER",
                    "members": [
                        {
                            "operation_ref": row.get("operation_ref"),
                            "to_state": row.get("to_state"),
                            "conditions": _transition_conditions(row),
                            "transition_id": row.get("transition_id"),
                        }
                        for row in rows
                    ],
                }
            )
        for row in rows:
            operation_ref = text(row.get("operation_ref") or row.get("event"))
            path_kind = _path_kind_for_operation(operation_ref)
            branch = {
                "branch_id": stable_id("process_branch", object_ref, state, row.get("transition_id")),
                "from_state": state,
                "conditions": _transition_conditions(row),
                "operation_ref": row.get("operation_ref"),
                "event": row.get("event"),
                "to_state": row.get("to_state"),
                "transition_id": row.get("transition_id"),
                "path_kind": path_kind,
                "steps": [_step_from_transition(row, 1)],
            }
            # Extend each branch only along a unique linear continuation.
            continuation = _linear_chain_from(
                text(row.get("to_state")),
                outgoing,
                stop_states=set(branch_states),
            )
            if continuation:
                branch["steps"] = [
                    _step_from_transition(step, index + 1)
                    for index, step in enumerate([row, *continuation])
                ]
                branch["outcome_state"] = text(continuation[-1].get("to_state"))
            else:
                branch["outcome_state"] = text(row.get("to_state"))
            branches.append(branch)
            if path_kind in {"WITHDRAW", "RETURN", "COMPENSATION"}:
                exception_paths.append(
                    {
                        "path_id": stable_id("process_exception", object_ref, path_kind, row.get("transition_id")),
                        "path_kind": path_kind,
                        "from_state": state,
                        "operation_ref": row.get("operation_ref"),
                        "to_state": row.get("to_state"),
                        "conditions": _transition_conditions(row),
                        "transition_id": row.get("transition_id"),
                    }
                )

    # Source-backed cycles are loops; do not invent iteration bounds.
    loops = [
        {
            "loop_id": stable_id("process_loop", object_ref, cycle),
            "states": cycle,
            "derivation": "source_backed_lifecycle_cycle",
        }
        for cycle in cycles
    ]
    for cycle in cycles:
        cycle_ops = unique_text(
            [
                transition.get("operation_ref") or transition.get("event")
                for state in cycle[:-1]
                for transition in outgoing.get(state, [])
                if text(transition.get("to_state")) in cycle
            ]
        )
        for operation_ref in cycle_ops:
            path_kind = _path_kind_for_operation(operation_ref)
            if path_kind in {"WITHDRAW", "RETURN", "COMPENSATION"} and not any(
                row.get("path_kind") == path_kind and row.get("operation_ref") == operation_ref
                for row in exception_paths
            ):
                exception_paths.append(
                    {
                        "path_id": stable_id("process_exception", object_ref, path_kind, operation_ref, cycle),
                        "path_kind": path_kind,
                        "operation_ref": operation_ref,
                        "loop_states": cycle,
                        "conditions": [],
                    }
                )

    shared_prefix: list[dict[str, Any]] = []
    if len(starts) == 1 and starts[0] not in underdetermined_states:
        prefix = _linear_chain_from(starts[0], outgoing, stop_states=set(branch_states))
        if prefix:
            shared_prefix = [_step_from_transition(row, index + 1) for index, row in enumerate(prefix)]

    if underdetermined_states and not branches and not loops and not parallel_groups:
        # Still emit a visible PARTIAL shell so silent "no process" cannot hide the gap.
        process = {
            "schema": PROCESS_SCHEMA,
            "process_id": stable_id("business_process", object_ref, "underdetermined", [row.get("transition_id") for row in transitions]),
            "name": f"{object_ref}生命周期流程",
            "process_type": "LIFECYCLE_PARTIAL",
            "trigger": {"state": starts[0]} if len(starts) == 1 else {},
            "inputs": [object_ref],
            "outputs": [],
            "participants": unique_text(
                [actor for transition in transitions for actor in as_list(transition.get("actor_refs"))]
            ),
            "steps": shared_prefix,
            "branches": [],
            "loops": [],
            "parallel_groups": [],
            "exception_paths": [],
            "exceptions": unique_text(
                [value for transition in transitions for value in as_list(transition.get("exceptions"))]
            ),
            "evidence": evidence,
            "status": "PARTIAL",
            "derivation": "source_backed_lifecycle_underdetermined_branches",
            "underdetermined_branch_states": underdetermined_states,
            "merge_states": merge_states,
        }
        return process, unknowns

    process_types: list[str] = []
    if branches:
        process_types.append("CONDITIONAL")
    if parallel_groups:
        process_types.append("PARALLEL")
    if loops:
        process_types.append("LOOP")
    if exception_paths:
        kinds = unique_text([row.get("path_kind") for row in exception_paths])
        process_types.extend(kinds)
    if not process_types and merge_states:
        # Merge without distinguished branch evidence — visible but incomplete.
        unknowns.append(
            new_unknown(
                "PROCESS_MERGE_UNDERDETERMINED",
                f"{object_ref}存在多个入边汇合状态，但资料未说明汇合前分支如何产生，不能按文档顺序补全流程。",
                related_objects=[object_ref],
                evidence=evidence,
                severity="P1",
                blocks_formal_understanding=False,
                reason_code="PROCESS_MERGE_UNDERDETERMINED",
                details={"merge_states": merge_states},
            )
        )
        process_types.append("PARTIAL")

    if underdetermined_states:
        status = "PARTIAL"
        process_type = "LIFECYCLE_PARTIAL"
    elif parallel_groups and not branches and not loops:
        status = "UNDERSTOOD"
        process_type = "LIFECYCLE_PARALLEL"
    elif loops and not branches:
        status = "UNDERSTOOD"
        process_type = "LIFECYCLE_WITH_LOOP"
    elif branches:
        status = "UNDERSTOOD"
        process_type = "LIFECYCLE_CONDITIONAL" if not loops and not parallel_groups else "LIFECYCLE_NONLINEAR"
    else:
        status = "PARTIAL"
        process_type = "LIFECYCLE_PARTIAL"

    outputs = unique_text(
        [
            *[text(row.get("outcome_state") or row.get("to_state")) for row in branches],
            *[text(transition.get("to_state")) for transition in transitions if indegree.get(text(transition.get("to_state")), 0) and text(transition.get("to_state")) not in outgoing],
        ]
    )
    process = {
        "schema": PROCESS_SCHEMA,
        "process_id": stable_id(
            "business_process",
            object_ref,
            process_type,
            [row.get("transition_id") for row in transitions],
        ),
        "name": f"{object_ref}生命周期流程",
        "process_type": process_type,
        "process_features": unique_text(process_types),
        "trigger": {"state": starts[0]} if len(starts) == 1 else {},
        "inputs": [object_ref],
        "outputs": [{"object_ref": object_ref, "state": state} for state in outputs],
        "participants": unique_text(
            [actor for transition in transitions for actor in as_list(transition.get("actor_refs"))]
        ),
        "steps": shared_prefix,
        "branches": branches,
        "loops": loops,
        "parallel_groups": parallel_groups,
        "exception_paths": exception_paths,
        "exceptions": unique_text(
            [value for transition in transitions for value in as_list(transition.get("exceptions"))]
        ),
        "evidence": evidence,
        "status": status,
        "derivation": "source_backed_lifecycle_nonlinear_projection",
        "underdetermined_branch_states": underdetermined_states,
        "merge_states": merge_states,
        "start_states": starts,
    }
    if len(starts) != 1:
        unknowns.append(
            new_unknown(
                "PROCESS_START_UNDERDETERMINED",
                f"{object_ref}生命周期存在多个或零个起始状态，资料未唯一标明流程入口。",
                related_objects=[object_ref],
                evidence=evidence,
                severity="P1",
                blocks_formal_understanding=False,
                reason_code="PROCESS_START_UNDERDETERMINED",
                details={"start_states": starts},
            )
        )
        process["status"] = "PARTIAL"
    return process, unknowns


def _project_lifecycle_process(
    lifecycle: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    unique = _unique_chain_process(lifecycle)
    if unique is not None:
        return unique, []
    return _project_nonlinear_lifecycle_process(lifecycle)


def _project_multi_object_processes(
    processes: list[dict[str, Any]],
    object_relations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Link object processes only along unique source-backed relation chains.

    Relation direction is evidence; document order never sequences objects.
    Branching relation graphs stay unresolved.
    """
    by_object = {
        text(as_list(row.get("inputs"))[0] if as_list(row.get("inputs")) else ""): row
        for row in processes
        if isinstance(row, dict) and as_list(row.get("inputs"))
    }
    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    indegree: dict[str, int] = defaultdict(int)
    for relation in object_relations:
        if not isinstance(relation, dict):
            continue
        relation_type = text(relation.get("relation_type"))
        if relation_type not in _LINK_RELATION_TYPES:
            continue
        source = text(relation.get("source_object_ref"))
        target = text(relation.get("target_object_ref"))
        if not source or not target or source not in by_object or target not in by_object:
            continue
        outgoing[source].append(relation)
        indegree[target] += 1

    linked_objects = set(outgoing) | {text(relation.get("target_object_ref")) for rows in outgoing.values() for relation in rows}
    if not linked_objects:
        return [], []

    if any(len(rows) > 1 for rows in outgoing.values()) or any(
        indegree.get(obj, 0) > 1 for obj in linked_objects
    ):
        evidence = dedupe_evidence(
            [row for relation in object_relations if isinstance(relation, dict) for row in as_list(relation.get("evidence"))]
        )
        return [], [
            new_unknown(
                "MULTI_OBJECT_PROCESS_UNDERDETERMINED",
                "多个业务对象之间存在可关联关系，但关系图存在分叉或汇合，资料未给出唯一跨对象流程顺序。",
                related_objects=sorted(linked_objects),
                evidence=evidence,
                severity="P1",
                blocks_formal_understanding=False,
                reason_code="MULTI_OBJECT_PROCESS_UNDERDETERMINED",
                details={
                    "objects": sorted(linked_objects),
                    "relation_types": sorted(
                        {
                            text(relation.get("relation_type"))
                            for relation in object_relations
                            if isinstance(relation, dict)
                            and text(relation.get("relation_type")) in _LINK_RELATION_TYPES
                        }
                    ),
                },
            )
        ]

    starts = sorted(obj for obj in linked_objects if indegree.get(obj, 0) == 0)
    if len(starts) != 1:
        return [], [
            new_unknown(
                "MULTI_OBJECT_PROCESS_START_UNDERDETERMINED",
                "跨对象流程缺少唯一起始业务对象，不能按文档出现顺序臆造主流程。",
                related_objects=sorted(linked_objects),
                severity="P1",
                blocks_formal_understanding=False,
                reason_code="MULTI_OBJECT_PROCESS_START_UNDERDETERMINED",
                details={"start_objects": starts, "objects": sorted(linked_objects)},
            )
        ]

    ordered_objects: list[str] = []
    link_steps: list[dict[str, Any]] = []
    current = starts[0]
    visited: set[str] = set()
    while current in outgoing:
        if current in visited:
            return [], [
                new_unknown(
                    "MULTI_OBJECT_PROCESS_CYCLE",
                    "跨对象关系形成环路，资料未声明循环边界，跨对象流程保持未决。",
                    related_objects=sorted(linked_objects),
                    severity="P1",
                    blocks_formal_understanding=False,
                    reason_code="MULTI_OBJECT_PROCESS_CYCLE",
                    details={"objects": sorted(linked_objects)},
                )
            ]
        visited.add(current)
        ordered_objects.append(current)
        relation = outgoing[current][0]
        target = text(relation.get("target_object_ref"))
        link_steps.append(
            {
                "order": len(link_steps) + 1,
                "source_object_ref": current,
                "relation_type": relation.get("relation_type"),
                "relation_id": relation.get("relation_id"),
                "target_object_ref": target,
                "source_process_ref": by_object[current].get("process_id"),
                "target_process_ref": by_object[target].get("process_id"),
            }
        )
        current = target
    ordered_objects.append(current)
    if set(ordered_objects) != linked_objects:
        return [], [
            new_unknown(
                "MULTI_OBJECT_PROCESS_INCOMPLETE",
                "跨对象关系未能覆盖全部已关联对象，跨对象流程保持部分未决。",
                related_objects=sorted(linked_objects),
                severity="P1",
                blocks_formal_understanding=False,
                reason_code="MULTI_OBJECT_PROCESS_INCOMPLETE",
                details={"ordered_objects": ordered_objects, "objects": sorted(linked_objects)},
            )
        ]

    evidence = dedupe_evidence(
        [
            *[row for obj in ordered_objects for row in as_list(by_object[obj].get("evidence"))],
            *[
                row
                for step in link_steps
                for relation in object_relations
                if isinstance(relation, dict) and text(relation.get("relation_id")) == text(step.get("relation_id"))
                for row in as_list(relation.get("evidence"))
            ],
        ]
    )
    process = {
        "schema": PROCESS_SCHEMA,
        "process_id": stable_id("business_process", "multi_object", ordered_objects, [step.get("relation_id") for step in link_steps]),
        "name": "→".join(ordered_objects) + "跨对象流程",
        "process_type": "MULTI_OBJECT_LINKED",
        "process_features": ["MULTI_OBJECT"],
        "trigger": {"object_ref": starts[0]},
        "inputs": [starts[0]],
        "outputs": [{"object_ref": ordered_objects[-1]}],
        "participants": unique_text(
            [actor for obj in ordered_objects for actor in as_list(by_object[obj].get("participants"))]
        ),
        "steps": [
            {
                "order": index + 1,
                "object_ref": obj,
                "process_ref": by_object[obj].get("process_id"),
                "object_process_type": by_object[obj].get("process_type"),
            }
            for index, obj in enumerate(ordered_objects)
        ],
        "object_links": link_steps,
        "branches": [],
        "loops": [],
        "parallel_groups": [],
        "exception_paths": [
            row
            for obj in ordered_objects
            for row in as_list(by_object[obj].get("exception_paths"))
            if isinstance(row, dict)
        ],
        "exceptions": unique_text(
            [value for obj in ordered_objects for value in as_list(by_object[obj].get("exceptions"))]
        ),
        "evidence": evidence,
        "status": "PARTIAL" if any(text(by_object[obj].get("status")) == "PARTIAL" for obj in ordered_objects) else "UNDERSTOOD",
        "derivation": "unique_source_backed_object_relation_chain",
    }
    return [process], []


def _normalize_conflicts(asset: dict[str, Any]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for index, raw in enumerate(as_list(asset.get("cross_document_conflicts"))):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row.setdefault("conflict_id", stable_id("business_conflict", row, index))
        row.setdefault("status", "UNRESOLVED")
        row.setdefault("automatic_resolution_allowed", False)
        conflicts.append(row)
    return conflicts


def _pending_fact_unknowns(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unknowns: list[dict[str, Any]] = []
    for fact in facts:
        if not isinstance(fact, dict) or text(fact.get("status")) != "PENDING":
            continue
        ambiguities = unique_text(as_list(fact.get("ambiguities")))
        question = f"语句“{text(fact.get('raw_statement'))}”仍存在未决业务语义：{'、'.join(ambiguities) or '语义未完整解析'}。"
        unknowns.append(
            new_unknown(
                "PENDING_CHINESE_BUSINESS_FACT",
                question,
                related_objects=_fact_object_refs(fact),
                related_operations=[as_dict(fact.get("action")).get("canonical") or as_dict(fact.get("action")).get("raw")],
                evidence=evidence_from_fact(fact),
                severity="P0" if fact.get("critical") else "P1",
                blocks_formal_understanding=bool(fact.get("critical")),
                reason_code=ambiguities[0] if ambiguities else "PENDING_CHINESE_BUSINESS_FACT",
                details={"fact_id": fact.get("fact_id"), "ambiguities": ambiguities},
            )
        )
    return unknowns


def build_enterprise_understanding_model(asset: dict[str, Any]) -> dict[str, Any]:
    """Compile the current governed asset into one enterprise cognition model."""
    model = empty_model()
    ledger = as_dict(asset.get("business_fact_ledger"))
    facts = [row for row in as_list(ledger.get("items")) if isinstance(row, dict)]

    alias_map, alias_conflict_unknowns = _term_alias_map(facts)
    business_objects, alias_to_name, alias_unknowns = _build_business_objects(
        asset, facts, alias_map
    )
    object_names = [text(row.get("name")) for row in business_objects]
    operations, operation_unknowns = _build_operations(facts, alias_to_name)
    actors = _build_actors(asset, facts, operations)
    # Rewrite fact entity refs for relation/lifecycle builders without inventing names.
    rewritten_facts: list[dict[str, Any]] = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        if text(fact.get("kind")) not in {"RULE", "STATE_TRANSITION"}:
            rewritten_facts.append(fact)
            continue
        cloned = dict(fact)
        subject = dict(as_dict(fact.get("subject")))
        object_part = dict(as_dict(fact.get("object")))
        subject["entity_refs"] = _fact_object_refs(fact, alias_to_name)
        object_part["entity_refs"] = unique_text(
            [alias_to_name.get(text(name), text(name)) for name in as_list(object_part.get("entity_refs"))]
        )
        cloned["subject"] = subject
        cloned["object"] = object_part
        rewritten_facts.append(cloned)
    object_relations, relation_unknowns = build_object_graph(asset, rewritten_facts, object_names)
    lifecycles, lifecycle_unknowns = build_lifecycles(asset, rewritten_facts, object_names)
    processes: list[dict[str, Any]] = []
    process_unknowns: list[dict[str, Any]] = []
    for lifecycle in lifecycles:
        process, unknowns = _project_lifecycle_process(lifecycle)
        if process is not None:
            processes.append(process)
        process_unknowns.extend(unknowns)
    multi_object_processes, multi_object_unknowns = _project_multi_object_processes(
        processes, object_relations
    )
    processes.extend(multi_object_processes)
    process_unknowns.extend(multi_object_unknowns)

    operations_by_object: dict[str, list[str]] = defaultdict(list)
    for operation in operations:
        for object_ref in as_list(operation.get("object_refs")):
            operations_by_object[text(object_ref)].append(text(operation.get("operation_id")))
    lifecycle_by_object = {text(row.get("object_ref")): text(row.get("lifecycle_id")) for row in lifecycles}
    relation_by_object: dict[str, list[str]] = defaultdict(list)
    for relation in object_relations:
        relation_id = text(relation.get("relation_id"))
        relation_by_object[text(relation.get("source_object_ref"))].append(relation_id)
        relation_by_object[text(relation.get("target_object_ref"))].append(relation_id)
    for business_object in business_objects:
        name = text(business_object.get("name"))
        business_object["operation_refs"] = unique_text(operations_by_object.get(name, []))
        business_object["lifecycle_refs"] = unique_text([lifecycle_by_object.get(name)])
        business_object["relation_refs"] = unique_text(relation_by_object.get(name, []))

    rules = []
    for fact in _accepted_facts(facts):
        if text(fact.get("kind")) not in {"RULE", "STATE_TRANSITION"}:
            continue
        rules.append(
            {
                "fact_id": fact.get("fact_id"),
                "kind": fact.get("kind"),
                "statement": fact.get("raw_statement"),
                "object_refs": _fact_object_refs(fact, alias_to_name),
                "actor_refs": _fact_actor_refs(fact),
                "action": as_dict(fact.get("action")),
                "conditions": as_list(fact.get("conditions")),
                "condition_combinator": text(fact.get("condition_combinator")),
                "modality": fact.get("modality"),
                "exceptions": as_list(fact.get("exceptions")),
                "evidence": evidence_from_fact(fact),
            }
        )

    unknowns = [
        *_pending_fact_unknowns(facts),
        *alias_conflict_unknowns,
        *alias_unknowns,
        *operation_unknowns,
        *relation_unknowns,
        *lifecycle_unknowns,
        *process_unknowns,
    ]
    if facts and not business_objects:
        unknowns.append(
            new_unknown(
                "NO_BUSINESS_OBJECT_UNDERSTOOD",
                "资料中存在业务事实，但尚未形成任何可追溯业务对象。",
                severity="P0",
                blocks_formal_understanding=True,
                reason_code="NO_BUSINESS_OBJECT_UNDERSTOOD",
            )
        )

    deduped_unknowns = list({text(row.get("unknown_id")): row for row in unknowns if isinstance(row, dict) and text(row.get("unknown_id"))}.values())
    conflicts = _normalize_conflicts(asset)
    evidence_index = dedupe_evidence(
        [
            *[row for item in [*business_objects, *actors, *operations, *object_relations, *lifecycles, *processes, *rules] for row in as_list(item.get("evidence"))],
            *[row for item in deduped_unknowns for row in as_list(item.get("evidence"))],
        ]
    )

    model.update(
        {
            "schema": MODEL_SCHEMA,
            "model_id": stable_id(
                "enterprise_understanding",
                asset.get("asset_id"),
                [row.get("object_id") for row in business_objects],
                [row.get("operation_id") for row in operations],
                [row.get("lifecycle_id") for row in lifecycles],
            ),
            "source_asset_id": asset.get("asset_id"),
            "business_objects": business_objects,
            "actors": actors,
            "operations": operations,
            "object_relations": object_relations,
            "lifecycles": lifecycles,
            "processes": processes,
            "rules": rules,
            "unknowns": deduped_unknowns,
            "conflicts": conflicts,
            "evidence_index": evidence_index,
            "term_resolution": {
                "canonicalization_contract": "SOURCE_EVIDENCE_REQUIRED",
                "alias_to_object": alias_to_name,
                "merge_policy": "source_backed_term_alias_only",
                "automatic_inference_allowed": False,
            },
        }
    )
    gate = assess_understanding_model(model, upstream_gate=as_dict(asset.get("enterprise_comprehension_gate")))
    model["gate"] = gate
    model["metrics"] = dict(gate.get("metrics") or {})
    return model



def _asset_evidence(row: dict[str, Any], asset_ref: str, derivation: str) -> list[dict[str, Any]]:
    evidence_rows: list[dict[str, Any]] = []
    raw_evidence = row.get("evidence")
    if isinstance(raw_evidence, list):
        for item in raw_evidence:
            if not isinstance(item, dict):
                continue
            evidence_rows.append(
                source_evidence(
                    source_id=item.get("source_id") or row.get("source_id"),
                    source_locator=item.get("source_locator") or item.get("locator"),
                    quote=item.get("quote") or item.get("verbatim_quote"),
                    quote_hash=item.get("quote_hash"),
                    fact_id=item.get("fact_id"),
                    asset_ref=asset_ref,
                    derivation=derivation,
                )
            )
    elif isinstance(raw_evidence, dict):
        evidence_rows.append(
            source_evidence(
                source_id=raw_evidence.get("source_id") or row.get("source_id"),
                source_locator=raw_evidence.get("source_locator") or raw_evidence.get("locator"),
                quote=raw_evidence.get("quote") or raw_evidence.get("verbatim_quote"),
                quote_hash=raw_evidence.get("quote_hash"),
                fact_id=raw_evidence.get("fact_id"),
                asset_ref=asset_ref,
                derivation=derivation,
            )
        )
    if not evidence_rows:
        evidence_rows.append(
            source_evidence(
                source_id=row.get("source_id"),
                source_locator=row.get("source_locator"),
                quote=row.get("statement") or row.get("source_excerpt"),
                asset_ref=asset_ref,
                derivation=derivation,
            )
        )
    return dedupe_evidence(evidence_rows)


def _accepted_facts(facts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        fact
        for fact in facts
        if isinstance(fact, dict) and text(fact.get("status")) == "ACCEPTED"
    ]


def _fact_object_refs(fact: dict[str, Any], alias_map: dict[str, str] | None = None) -> list[str]:
    subject = as_dict(fact.get("subject"))
    object_part = as_dict(fact.get("object"))
    refs = unique_text(
        [
            *as_list(subject.get("entity_refs")),
            *as_list(object_part.get("entity_refs")),
        ]
    )
    if not alias_map:
        return refs
    return unique_text([alias_map.get(name, name) for name in refs])


def _fact_actor_refs(fact: dict[str, Any]) -> list[str]:
    return unique_text(as_list(as_dict(fact.get("subject")).get("actor_refs")))


def _term_alias_map(facts: Iterable[dict[str, Any]]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Map source-backed aliases to canonical object names; conflicting aliases stay unresolved."""
    alias_to_canonical: dict[str, str] = {}
    conflicting: dict[str, set[str]] = {}
    evidence_by_alias: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        if text(fact.get("kind")) != "TERM_ALIAS" or text(fact.get("status")) != "ACCEPTED":
            continue
        canonical = text(fact.get("canonical_term"))
        alias = text(fact.get("alias"))
        if not canonical or not alias or canonical == alias:
            continue
        evidence_by_alias[alias].extend(evidence_from_fact(fact))
        existing = alias_to_canonical.get(alias)
        if existing and existing != canonical:
            conflicting.setdefault(alias, {existing}).add(canonical)
            continue
        alias_to_canonical[alias] = canonical
        alias_to_canonical.setdefault(canonical, canonical)
    unknowns: list[dict[str, Any]] = []
    for alias, canons in sorted(conflicting.items()):
        alias_to_canonical.pop(alias, None)
        unknowns.append(
            new_unknown(
                "TERM_ALIAS_IDENTITY_CONFLICT",
                f"别名“{alias}”被不同资料声明为多个规范业务对象：{'、'.join(sorted(canons))}。",
                related_objects=sorted(canons),
                evidence=evidence_by_alias.get(alias, []),
                severity="P0",
                blocks_formal_understanding=True,
                reason_code="TERM_ALIAS_IDENTITY_CONFLICT",
                details={"alias": alias, "canonical_candidates": sorted(canons)},
            )
        )
    return alias_to_canonical, unknowns


def _merge_record(target: dict[str, Any], *, evidence: Iterable[dict[str, Any]], aliases: Iterable[Any] = ()) -> None:
    target["aliases"] = unique_text([*as_list(target.get("aliases")), *aliases])
    target["evidence"] = dedupe_evidence([*as_list(target.get("evidence")), *evidence])


def _build_business_objects(
    asset: dict[str, Any],
    facts: list[dict[str, Any]],
    alias_map: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    objects: dict[str, dict[str, Any]] = {}
    table_id_to_name: dict[str, str] = {}
    alias_map = dict(alias_map or {})
    alias_unknowns: list[dict[str, Any]] = []

    def canonicalize(name: Any) -> tuple[str, str]:
        raw_name = text(name)
        if not raw_name:
            return "", ""
        canonical = text(alias_map.get(raw_name, raw_name))
        return canonical, raw_name

    def ensure(name: Any, evidence: Iterable[dict[str, Any]], *, source_kind: str) -> dict[str, Any] | None:
        object_name, raw_name = canonicalize(name)
        if not object_name:
            return None
        row = objects.setdefault(
            object_name,
            {
                "schema": OBJECT_SCHEMA,
                "object_id": stable_id("business_object", object_name),
                "name": object_name,
                "aliases": [],
                "identity_fields": [],
                "attributes": [],
                "operation_refs": [],
                "lifecycle_refs": [],
                "relation_refs": [],
                "source_kinds": [],
                "evidence": [],
                "status": "UNDERSTOOD",
            },
        )
        row["source_kinds"] = unique_text([*as_list(row.get("source_kinds")), source_kind])
        aliases = [raw_name] if raw_name and raw_name != object_name else []
        _merge_record(row, evidence=evidence, aliases=aliases)
        return row

    for index, raw in enumerate(as_list(asset.get("business_objects"))):
        if not isinstance(raw, dict):
            continue
        name = raw.get("object") or raw.get("name")
        row = ensure(name, _asset_evidence(raw, text(raw.get("object_id")) or f"business_objects[{index}]", "existing_business_object"), source_kind="business_object_asset")
        if row:
            _merge_record(row, evidence=[], aliases=as_list(raw.get("aliases")))

    for index, raw in enumerate(as_list(asset.get("data_tables"))):
        if not isinstance(raw, dict):
            continue
        name = text(raw.get("name"))
        table_id = text(raw.get("table_id"))
        canonical_name, _ = canonicalize(name)
        if table_id and canonical_name:
            table_id_to_name[table_id] = canonical_name
        row = ensure(name, _asset_evidence(raw, table_id or f"data_tables[{index}]", "source_backed_data_table"), source_kind="data_table")
        if row:
            columns = unique_text(as_list(raw.get("columns")))
            row["attributes"] = unique_text([*as_list(row.get("attributes")), *columns])
            row["identity_fields"] = unique_text([*as_list(row.get("identity_fields")), *[column for column in columns if _IDENTITY_FIELD_RE.search(column)]])

    accepted = _accepted_facts(facts)
    for fact in accepted:
        for object_name in _fact_object_refs(fact, alias_map):
            ensure(object_name, evidence_from_fact(fact), source_kind="business_fact")
        # Also ensure from pre-canonical mentions so alias evidence attaches even when
        # the fact still carries only the alias form.
        for object_name in _fact_object_refs(fact):
            ensure(object_name, evidence_from_fact(fact), source_kind="business_fact")

    for fact in accepted:
        if text(fact.get("kind")) != "TERM_ALIAS":
            continue
        canonical = text(fact.get("canonical_term"))
        alias = text(fact.get("alias"))
        if not canonical or not alias:
            continue
        row = ensure(canonical, evidence_from_fact(fact), source_kind="term_alias")
        if row:
            _merge_record(row, evidence=evidence_from_fact(fact), aliases=[alias])
            alias_map[alias] = canonical
            alias_map.setdefault(canonical, canonical)

    for index, raw in enumerate(as_list(asset.get("field_dictionary"))):
        if not isinstance(raw, dict):
            continue
        table_name = text(raw.get("table")) or table_id_to_name.get(text(raw.get("table_id")), "")
        table_name, _ = canonicalize(table_name)
        field_name = text(raw.get("field") or raw.get("name") or raw.get("field_path"))
        if table_name not in objects or not field_name:
            continue
        row = objects[table_name]
        row["attributes"] = unique_text([*as_list(row.get("attributes")), field_name])
        if _IDENTITY_FIELD_RE.search(field_name):
            row["identity_fields"] = unique_text([*as_list(row.get("identity_fields")), field_name])
        row["evidence"] = dedupe_evidence(
            [
                *as_list(row.get("evidence")),
                *_asset_evidence(raw, text(raw.get("field_id")) or f"field_dictionary[{index}]", "source_backed_field_dictionary"),
            ]
        )

    alias_to_name: dict[str, str] = dict(alias_map)
    for name, row in objects.items():
        alias_to_name[name] = name
        for alias in as_list(row.get("aliases")):
            alias_to_name[text(alias)] = name
    return sorted(objects.values(), key=lambda row: text(row.get("name"))), alias_to_name, alias_unknowns


def _effect_texts(fact: dict[str, Any]) -> list[str]:
    effects: list[str] = []
    effects.extend(unique_text(as_list(fact.get("postconditions"))))
    for raw in as_list(fact.get("data_effects")):
        if isinstance(raw, dict):
            value = text(raw.get("statement") or raw.get("effect") or raw.get("raw"))
        else:
            value = text(raw)
        if value:
            effects.append(value)
    for raw in as_list(fact.get("state_effects")):
        effect = as_dict(raw)
        from_state = text(effect.get("from_state"))
        to_state = text(effect.get("to_state"))
        if from_state and to_state:
            effects.append(f"状态从{from_state}变为{to_state}")
        elif to_state:
            effects.append(f"状态进入{to_state}")
    return unique_text(effects)


def _build_operations(
    facts: list[dict[str, Any]],
    alias_map: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    operations: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    unknowns: list[dict[str, Any]] = []
    alias_map = alias_map or {}
    for fact in _accepted_facts(facts):
        action = as_dict(fact.get("action"))
        action_name = text(action.get("canonical") or action.get("raw"))
        if not action_name:
            continue
        object_refs = _fact_object_refs(fact, alias_map)
        actor_refs = _fact_actor_refs(fact)
        evidence = evidence_from_fact(fact)
        conditions = unique_text(as_list(fact.get("conditions")))
        combinator = text(fact.get("condition_combinator"))
        if len(conditions) > 1 and combinator not in {"AND", "OR"}:
            combinator = "UNRESOLVED"
            unknowns.append(
                new_unknown(
                    "CONDITION_COMBINATOR_UNRESOLVED",
                    f"业务动作“{action_name}”存在多个条件，但原文未给出明确的并且/或者组合关系。",
                    related_objects=object_refs,
                    related_operations=[action_name],
                    evidence=evidence,
                    severity="P0" if fact.get("critical") else "P1",
                    blocks_formal_understanding=bool(fact.get("critical")),
                    reason_code="CONDITION_COMBINATOR_UNRESOLVED",
                    details={
                        "fact_id": fact.get("fact_id"),
                        "conditions": conditions,
                        "statement": fact.get("raw_statement"),
                    },
                )
            )
        elif len(conditions) <= 1:
            combinator = "SINGLE_CONDITION" if conditions else ""
        if not object_refs:
            unknowns.append(
                new_unknown(
                    "OPERATION_OBJECT_UNRESOLVED",
                    f"业务动作“{action_name}”无法唯一确定作用对象。",
                    related_operations=[action_name],
                    evidence=evidence,
                    severity="P0" if fact.get("critical") else "P1",
                    blocks_formal_understanding=bool(fact.get("critical")),
                    reason_code="OPERATION_OBJECT_UNRESOLVED",
                    details={"fact_id": fact.get("fact_id"), "statement": fact.get("raw_statement")},
                )
            )
            continue
        key = (action_name, tuple(sorted(object_refs)))
        operation = operations.setdefault(
            key,
            {
                "schema": OPERATION_SCHEMA,
                "operation_id": stable_id("business_operation", action_name, sorted(object_refs)),
                "name": action_name,
                "raw_action_names": [],
                "actor_refs": [],
                "object_refs": sorted(object_refs),
                "preconditions": [],
                "condition_combinator": combinator,
                "effects": [],
                "exceptions": [],
                "temporal_constraints": [],
                "scopes": [],
                "modality_contracts": [],
                "fact_refs": [],
                "evidence": [],
                "status": "UNDERSTOOD",
            },
        )
        operation["raw_action_names"] = unique_text([*as_list(operation.get("raw_action_names")), action.get("raw"), action_name])
        operation["actor_refs"] = unique_text([*as_list(operation.get("actor_refs")), *actor_refs])
        operation["preconditions"] = unique_text([*as_list(operation.get("preconditions")), *conditions])
        existing_combinator = text(operation.get("condition_combinator"))
        merged_preconditions = as_list(operation.get("preconditions"))
        if len(merged_preconditions) > 1:
            if existing_combinator and combinator and existing_combinator != combinator:
                operation["condition_combinator"] = "UNRESOLVED"
            elif combinator:
                operation["condition_combinator"] = combinator
            elif existing_combinator not in {"AND", "OR"}:
                operation["condition_combinator"] = "UNRESOLVED"
        elif merged_preconditions:
            operation["condition_combinator"] = existing_combinator or combinator or "SINGLE_CONDITION"
        else:
            operation["condition_combinator"] = ""
        operation["effects"] = unique_text([*as_list(operation.get("effects")), *_effect_texts(fact)])
        operation["exceptions"] = unique_text([*as_list(operation.get("exceptions")), *as_list(fact.get("exceptions"))])
        operation["temporal_constraints"] = unique_text([*as_list(operation.get("temporal_constraints")), *as_list(fact.get("temporal_constraints"))])
        scope = {key: value for key, value in as_dict(fact.get("scope")).items() if text(value)}
        if scope and scope not in operation["scopes"]:
            operation["scopes"].append(scope)
        modality_contract = {
            "modality": text(fact.get("modality")),
            "polarity": text(fact.get("polarity")),
            "fact_id": text(fact.get("fact_id")),
        }
        if modality_contract not in operation["modality_contracts"]:
            operation["modality_contracts"].append(modality_contract)
        operation["fact_refs"] = unique_text([*as_list(operation.get("fact_refs")), fact.get("fact_id")])
        operation["evidence"] = dedupe_evidence([*as_list(operation.get("evidence")), *evidence])
        if text(operation.get("condition_combinator")) == "UNRESOLVED" and len(as_list(operation.get("preconditions"))) > 1:
            operation["status"] = "PARTIAL"
    return sorted(operations.values(), key=lambda row: (text(row.get("name")), tuple(as_list(row.get("object_refs"))))), unknowns


def _build_actors(
    asset: dict[str, Any],
    facts: list[dict[str, Any]],
    operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    actors: dict[str, dict[str, Any]] = {}

    def ensure(name: Any, evidence: Iterable[dict[str, Any]], source_kind: str) -> dict[str, Any] | None:
        actor_name = text(name)
        if not actor_name:
            return None
        row = actors.setdefault(
            actor_name,
            {
                "schema": ACTOR_SCHEMA,
                "actor_id": stable_id("business_actor", actor_name),
                "name": actor_name,
                "responsibility_operation_refs": [],
                "permissions": [],
                "restrictions": [],
                "source_kinds": [],
                "evidence": [],
                "status": "UNDERSTOOD",
            },
        )
        row["source_kinds"] = unique_text([*as_list(row.get("source_kinds")), source_kind])
        row["evidence"] = dedupe_evidence([*as_list(row.get("evidence")), *evidence])
        return row

    for index, raw in enumerate(as_list(asset.get("roles"))):
        if not isinstance(raw, dict):
            continue
        ensure(raw.get("role") or raw.get("name"), _asset_evidence(raw, text(raw.get("role_id")) or f"roles[{index}]", "source_backed_role"), "role_asset")

    for index, raw in enumerate(as_list(asset.get("permission_matrix"))):
        if not isinstance(raw, dict):
            continue
        name = raw.get("role") or raw.get("actor")
        row = ensure(name, _asset_evidence(raw, text(raw.get("permission_id")) or f"permission_matrix[{index}]", "source_backed_permission"), "permission_matrix")
        if not row:
            continue
        decision = text(raw.get("decision") or raw.get("effect")).lower()
        permission = {
            "resource_refs": unique_text([raw.get("resource"), *as_list(raw.get("resources"))]),
            "actions": unique_text([raw.get("action"), *as_list(raw.get("actions"))]),
            "scope": raw.get("scope") or raw.get("data_scope"),
            "permission_id": raw.get("permission_id"),
        }
        if decision in {"deny", "denied", "forbid", "forbidden"} or as_list(raw.get("denied_actions")):
            permission["actions"] = unique_text([*permission["actions"], *as_list(raw.get("denied_actions"))])
            row["restrictions"].append(permission)
        else:
            row["permissions"].append(permission)

    for fact in _accepted_facts(facts):
        for actor_name in _fact_actor_refs(fact):
            row = ensure(actor_name, evidence_from_fact(fact), "business_fact")
            if not row:
                continue
            contract = {
                "object_refs": _fact_object_refs(fact),
                "action": text(as_dict(fact.get("action")).get("canonical") or as_dict(fact.get("action")).get("raw")),
                "scope": as_dict(fact.get("scope")),
                "fact_id": fact.get("fact_id"),
            }
            if text(fact.get("modality")) == "MUST_NOT":
                if contract not in row["restrictions"]:
                    row["restrictions"].append(contract)
            elif contract["action"]:
                if contract not in row["permissions"]:
                    row["permissions"].append(contract)

    for operation in operations:
        for actor_name in as_list(operation.get("actor_refs")):
            row = ensure(actor_name, as_list(operation.get("evidence")), "operation")
            if row:
                row["responsibility_operation_refs"] = unique_text([*as_list(row.get("responsibility_operation_refs")), operation.get("operation_id")])
    return sorted(actors.values(), key=lambda row: text(row.get("name")))


__all__ = ["build_enterprise_understanding_model"]
