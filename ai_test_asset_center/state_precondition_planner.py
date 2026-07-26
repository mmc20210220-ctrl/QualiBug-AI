"""Plan the governed steps that put an entity into a declared source state.

A state obligation says "from A, operation X must reach B". Establishing A is the
experiment's own job, and today nothing does it: ``experiment_compiler_support`` strips the
``entity_in_state:*`` fixtures a state obligation requests and substitutes the literal
``unknown_state``, which the state_transition evaluator then degrades from "did the declared
transition happen" into "did anything change at all". So the strongest state assertion in the
product silently becomes the weakest one.

This module produces the path. It does not execute it — establishment steps belong in the
FIXTURE phase, not the measured one, for a reason that decides correctness rather than
tidiness: ``_main_governed_write_steps`` keeps only ``phase in {"control", "treatment"}``, so
establishment writes stay outside the measured window and ``before_state`` still snapshots
the first MEASURED write. If establishment ran as a control step, ``before_state`` would
snapshot the state BEFORE the precondition existed, and a correctly-run experiment would
report STATE_PRECONDITION_NOT_MET.

WHY THE BFS IS RE-IMPLEMENTED RATHER THAN IMPORTED
==================================================
``precondition_reachability.py`` has a correct BFS over explicit edges and is imported by
nothing. Its BFS is worth reusing; its ``analyze`` wrapper is not. Verified at
precondition_reachability.py:318-324: when the goal carries no state condition it returns
``reachable=True`` with an empty path and no blocked_reason — a claim that the precondition
is satisfied when nothing was checked. That is the same vacuous-truth shape fixed elsewhere
in this session, and inverting it is a requirement, not a preference: an absent or
unreachable goal state must BLOCK.

SOURCE-DECLARED ONLY
====================
The graph is built exclusively from IR relations of type ``transitions``, the same relation
set that produces state obligations. No path is inferred from a state NAME, an operation
name, or an ordering guess. A transition edge that does not name a real operation is not an
edge.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

# Same bound as precondition_reachability.MAX_PRECONDITION_PATH_STEPS. A path longer than
# this is treated as unreachable rather than attempted: each step is a governed write
# against a customer system, and a long speculative chain is not evidence-gathering.
MAX_PRECONDITION_PATH_STEPS = 12

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
    """Adjacency from declared ``transitions`` relations only.

    Returns ``{from_state_token: [{"to", "operation_ref", "from_ref", "to_ref"}]}``. An edge
    is kept only when its from-state, to-state AND operation all resolve in the IR — the
    same three-way requirement obligation_compiler_base applies before emitting a state
    obligation. A half-resolved edge is not a transition anyone can execute.
    """
    ir = _dict(behavior_ir)
    states_by_id = {
        _text(state.get("id")): _dict(state)
        for state in _list(ir.get("states"))
        if _text(_dict(state).get("id"))
    }
    operation_ids = {
        _text(op.get("id")) for op in _list(ir.get("operations")) if _text(_dict(op).get("id"))
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
        adjacency.setdefault(from_token, []).append({
            "to": to_token,
            "operation_ref": operation_ref,
            "from_ref": from_ref,
            "to_ref": to_ref,
        })
    return adjacency


def _state_token(state: dict[str, Any]) -> str:
    """The comparable token for a state node, matching how the evaluator compares states."""
    from .assertion_dsl_base import _state_token as normalize

    return normalize(_text(state.get("value") or state.get("name") or state.get("id")))


def _entry_states(adjacency: dict[str, list[dict[str, str]]]) -> list[str]:
    """States that no declared transition leads INTO — the graph's creatable entry points.

    Derived from the edges, not from a name like "created" or "new": a state is an entry
    point because nothing transitions into it, which is a structural fact the source
    declares, whereas its name is a guess.
    """
    reachable = {edge["to"] for edges in adjacency.values() for edge in edges}
    return sorted(state for state in adjacency if state not in reachable)


def plan_state_precondition(
    *,
    behavior_ir: dict[str, Any],
    from_state: str,
    actors: "list[str] | None" = None,
    start_state: str = "",
) -> dict[str, Any]:
    """Plan the shortest declared path that establishes ``from_state``.

    Returns ``{"status": PLANNED|BLOCKED, "reason_code": str, "steps": [...], "detail": {}}``.

    Every BLOCKED outcome is explicit and named. In particular an absent or unreachable goal
    state BLOCKS, where the older reachability module would have reported success with an
    empty path.
    """
    from .assertion_dsl_base import _state_token as normalize

    goal = normalize(_text(from_state))
    if not goal or goal == normalize("unknown_state"):
        # The literal substituted when a state could not be resolved. Planning a path to it
        # would be planning a path to nothing.
        return _blocked(REASON_NO_TARGET_STATE, from_state=_text(from_state))

    adjacency = build_transition_graph(behavior_ir)
    if not adjacency:
        return _blocked(REASON_TARGET_ABSENT, goal=goal, declared_transitions=0)

    known_states = set(adjacency) | {edge["to"] for edges in adjacency.values() for edge in edges}
    if goal not in known_states:
        return _blocked(REASON_TARGET_ABSENT, goal=goal, known_states=sorted(known_states))

    starts = [normalize(_text(start_state))] if _text(start_state) else _entry_states(adjacency)
    starts = [state for state in starts if state]
    if not starts:
        # Every state is transitioned into, so the graph declares no creatable entry point.
        return _blocked(REASON_NO_ENTRY_STATE, goal=goal, known_states=sorted(known_states))
    if goal in starts:
        # The entity is created directly in the goal state; no establishment is needed.
        return {
            "status": STATUS_PLANNED,
            "reason_code": "",
            "steps": [],
            "detail": {"goal": goal, "note": "goal is a declared entry state"},
        }

    path = _shortest_path(adjacency, starts, goal)
    if path is None:
        return _blocked(REASON_UNREACHABLE, goal=goal, entry_states=starts)
    if len(path) > MAX_PRECONDITION_PATH_STEPS:
        return _blocked(REASON_TOO_LONG, goal=goal, path_length=len(path))

    actor_refs = [_text(item) for item in _list(actors) if _text(item)]
    if not actor_refs:
        # An establishment step is a governed write and needs an identity. Without one the
        # plan cannot be executed, so it must not be reported as planned.
        return _blocked(REASON_ACTOR_MISSING, goal=goal, path_length=len(path))

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
        "detail": {"goal": goal, "entry_states": starts, "path_length": len(path)},
    }


def _shortest_path(
    adjacency: dict[str, list[dict[str, str]]],
    starts: list[str],
    goal: str,
) -> "list[dict[str, str]] | None":
    """BFS over declared edges. Returns the edge list, or None when no path exists."""
    queue: deque[tuple[str, list[dict[str, str]]]] = deque(
        (state, []) for state in starts
    )
    seen = set(starts)
    while queue:
        state, path = queue.popleft()
        if len(path) > MAX_PRECONDITION_PATH_STEPS:
            continue
        for edge in adjacency.get(state, []):
            target = edge["to"]
            step = {**edge, "from": state}
            if target == goal:
                return [*path, step]
            if target in seen:
                continue
            seen.add(target)
            queue.append((target, [*path, step]))
    return None
