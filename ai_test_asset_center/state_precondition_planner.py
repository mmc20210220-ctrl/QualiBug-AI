"""Plan governed steps that put an entity into a declared source state.

A state obligation says "from A, operation X must reach B". Establishing A is the
experiment's own job. Establishment steps belong in the FIXTURE phase, not the
measured one: ``_main_governed_write_steps`` keeps only ``phase in
{"control", "treatment"}``, so ``before_state`` still snapshots the first
MEASURED write.

This module is now a thin adapter over ``precondition_reachability``. It keeps the
existing compiler-facing contract and source-declared Behavior IR projection, but
it does not own a second BFS implementation or a second path-length authority.
Absent, unresolved, ambiguous-by-input, or unreachable target states still fail
closed with the existing planner reason codes.

SOURCE-DECLARED ONLY
====================
The graph is built exclusively from IR relations of type ``transitions``, the same
relation set that produces state obligations. No path is inferred from a state
name, operation name, or ordering guess. A transition edge that does not name a
real operation is not an executable edge.
"""

from __future__ import annotations

import logging
from typing import Any

from .precondition_reachability import (
    MAX_PRECONDITION_PATH_STEPS,
    PRECONDITION_PATH_TOO_LONG,
    OperationDef,
    PreconditionGoal,
    ReachabilityAnalyzer,
    StateTransition,
)

logger = logging.getLogger(__name__)

STATUS_PLANNED = "PLANNED"
STATUS_BLOCKED = "BLOCKED"

REASON_NO_TARGET_STATE = "precondition_target_state_not_declared"
REASON_TARGET_ABSENT = "precondition_target_state_absent_from_transition_graph"
REASON_NO_ENTRY_STATE = "precondition_no_declared_entry_state"
REASON_UNREACHABLE = "precondition_path_unreachable"
REASON_TOO_LONG = "precondition_path_too_long"
REASON_ACTOR_MISSING = "precondition_step_actor_unresolved"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _blocked(reason_code: str, **detail: Any) -> dict[str, Any]:
    return {
        "status": STATUS_BLOCKED,
        "reason_code": reason_code,
        "steps": [],
        "detail": dict(detail),
    }


def build_transition_graph(behavior_ir: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Project source-declared transition relations into comparable state tokens.

    An edge is retained only when from-state, to-state, and operation all resolve
    in the same Behavior IR. The returned adjacency is a projection consumed by
    the shared reachability authority; it is not a second graph-search engine.
    """
    ir = _dict(behavior_ir)
    states_by_id = {
        _text(state.get("id")): _dict(state)
        for state in _list(ir.get("states"))
        if _text(_dict(state).get("id"))
    }
    operation_ids = {
        _text(op.get("id"))
        for op in _list(ir.get("operations"))
        if _text(_dict(op).get("id"))
    }
    adjacency: dict[str, list[dict[str, str]]] = {}
    for raw in _list(ir.get("relations")):
        relation = _dict(raw)
        if _text(relation.get("relation_type")) != "transitions":
            continue
        from_ref = _text(relation.get("from_ref"))
        to_ref = _text(relation.get("to_ref"))
        operation_ref = _text(relation.get("operation_ref"))
        from_state = states_by_id.get(from_ref)
        to_state = states_by_id.get(to_ref)
        if not from_state or not to_state or operation_ref not in operation_ids:
            continue
        from_token = _state_token(from_state)
        to_token = _state_token(to_state)
        if not from_token or not to_token:
            continue
        adjacency.setdefault(from_token, []).append(
            {
                "to": to_token,
                "operation_ref": operation_ref,
                "from_ref": from_ref,
                "to_ref": to_ref,
            }
        )
    return adjacency


def _state_token(state: dict[str, Any]) -> str:
    """Comparable token shared with the assertion evaluator."""
    from .assertion_dsl_base import _state_token as normalize

    return normalize(_text(state.get("value") or state.get("name") or state.get("id")))


def _entry_states(adjacency: dict[str, list[dict[str, str]]]) -> list[str]:
    """States that no declared transition leads into."""
    reachable = {edge["to"] for edges in adjacency.values() for edge in edges}
    return sorted(state for state in adjacency if state not in reachable)


def _shared_reachability_inputs(
    behavior_ir: dict[str, Any],
    adjacency: dict[str, list[dict[str, str]]],
) -> tuple[list[StateTransition], list[OperationDef]]:
    """Adapt Behavior IR nodes to the existing reachability data structures."""
    operations_by_id = {
        _text(op.get("id")): _dict(op)
        for op in _list(_dict(behavior_ir).get("operations"))
        if _text(_dict(op).get("id"))
    }
    transitions: list[StateTransition] = []
    for from_state, edges in adjacency.items():
        for edge in edges:
            operation_id = _text(edge.get("operation_ref"))
            operation = operations_by_id.get(operation_id, {})
            transitions.append(
                StateTransition(
                    from_status=from_state,
                    to_status=_text(edge.get("to")),
                    operation_id=operation_id,
                    operation_method=_text(operation.get("method")).upper(),
                    operation_path=_text(
                        operation.get("path")
                        or operation.get("raw_path")
                        or operation.get("path_template")
                    ),
                    required_role=_text(
                        operation.get("required_role")
                        or operation.get("actor_role")
                    ),
                )
            )
    operation_defs = [
        OperationDef(
            operation_id=operation_id,
            method=_text(operation.get("method")).upper(),
            path=_text(
                operation.get("path")
                or operation.get("raw_path")
                or operation.get("path_template")
            ),
            required_role=_text(
                operation.get("required_role")
                or operation.get("actor_role")
            ),
        )
        for operation_id, operation in operations_by_id.items()
    ]
    return transitions, operation_defs


def _select_shared_path(
    *,
    analyzer: ReachabilityAnalyzer,
    starts: list[str],
    goal: str,
    actor_refs: list[str],
) -> tuple[str, list[dict[str, str]], str]:
    """Select the shortest path returned by the shared reachability authority."""
    goal_contract = PreconditionGoal(
        goal_id=f"state_precondition:{goal}",
        internal_rule_id="state_precondition",
        required_conditions=[
            {
                "condition_id": f"state_{goal}",
                "field_id": "status",
                "expected_expression": goal,
            }
        ],
    )
    available_actors = [
        {"role": actor_ref, "actor_ref": actor_ref}
        for actor_ref in actor_refs
    ]
    candidates: list[tuple[int, str, list[dict[str, str]]]] = []
    blocked_reason = ""
    for start in starts:
        result = analyzer.analyze(
            goal_contract,
            current_state=start,
            available_actors=available_actors,
        )
        if not result.reachable:
            if result.blocked_reason:
                blocked_reason = result.blocked_reason
            continue
        current = start
        projected: list[dict[str, str]] = []
        for next_state, transition in list(result.selected_path or []):
            projected.append(
                {
                    "from": current,
                    "to": _text(next_state),
                    "operation_ref": _text(transition.operation_id),
                }
            )
            current = _text(next_state)
        candidates.append((len(projected), start, projected))
    if not candidates:
        return "", [], blocked_reason
    candidates.sort(key=lambda row: (row[0], row[1]))
    _, selected_start, selected_path = candidates[0]
    return selected_start, selected_path, ""


def plan_state_precondition(
    *,
    behavior_ir: dict[str, Any],
    from_state: str,
    actors: "list[str] | None" = None,
    start_state: str = "",
) -> dict[str, Any]:
    """Plan the shortest source-declared path that establishes ``from_state``."""
    from .assertion_dsl_base import _state_token as normalize

    goal = normalize(_text(from_state))
    if not goal or goal == normalize("unknown_state"):
        return _blocked(REASON_NO_TARGET_STATE, from_state=_text(from_state))

    adjacency = build_transition_graph(behavior_ir)
    if not adjacency:
        return _blocked(REASON_TARGET_ABSENT, goal=goal, declared_transitions=0)

    known_states = set(adjacency) | {
        edge["to"] for edges in adjacency.values() for edge in edges
    }
    if goal not in known_states:
        return _blocked(
            REASON_TARGET_ABSENT,
            goal=goal,
            known_states=sorted(known_states),
        )

    starts = (
        [normalize(_text(start_state))]
        if _text(start_state)
        else _entry_states(adjacency)
    )
    starts = [state for state in starts if state]
    if not starts:
        return _blocked(
            REASON_NO_ENTRY_STATE,
            goal=goal,
            known_states=sorted(known_states),
        )
    if goal in starts:
        return {
            "status": STATUS_PLANNED,
            "reason_code": "",
            "steps": [],
            "detail": {
                "goal": goal,
                "start_state": goal,
                "note": "goal is the observed/declared start state",
                "reachability_authority": "precondition_reachability",
            },
        }

    actor_refs = [_text(item) for item in _list(actors) if _text(item)]
    if not actor_refs:
        return _blocked(REASON_ACTOR_MISSING, goal=goal)

    transitions, operation_defs = _shared_reachability_inputs(
        behavior_ir,
        adjacency,
    )
    analyzer = ReachabilityAnalyzer(transitions, operation_defs)
    selected_start, path, blocked_reason = _select_shared_path(
        analyzer=analyzer,
        starts=starts,
        goal=goal,
        actor_refs=actor_refs,
    )
    if not path:
        if blocked_reason == PRECONDITION_PATH_TOO_LONG:
            return _blocked(
                REASON_TOO_LONG,
                goal=goal,
                max_steps=MAX_PRECONDITION_PATH_STEPS,
            )
        return _blocked(
            REASON_UNREACHABLE,
            goal=goal,
            entry_states=starts,
            shared_reason=blocked_reason,
        )
    if len(path) > MAX_PRECONDITION_PATH_STEPS:
        return _blocked(REASON_TOO_LONG, goal=goal, path_length=len(path))

    steps = [
        {
            "step_id": f"precondition_{index + 1}",
            "phase": "fixture",
            "actor_ref": actor_refs[0],
            "operation_ref": edge["operation_ref"],
            "intent": "state_precondition_establishment",
            "protocol_step": "precondition_write",
            "from_state": edge["from"],
            "to_state": edge["to"],
            "step_ordinal": index + 1,
        }
        for index, edge in enumerate(path)
    ]
    return {
        "status": STATUS_PLANNED,
        "reason_code": "",
        "steps": steps,
        "detail": {
            "goal": goal,
            "entry_states": starts,
            "selected_start_state": selected_start,
            "path_length": len(path),
            "reachability_authority": "precondition_reachability",
        },
    }
