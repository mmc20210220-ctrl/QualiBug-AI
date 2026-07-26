"""Planning the governed steps that establish a declared source state.

A state obligation says "from A, operation X must reach B". Establishing A is the
experiment's own job, and nothing did it: ``experiment_compiler_support`` strips the
``entity_in_state:*`` fixtures a state obligation requests and substitutes the literal
``unknown_state``, which the state_transition evaluator then degrades from "did the declared
transition happen" into "did anything change at all". The strongest state assertion in the
product silently became the weakest one.

The BFS here is re-implemented rather than imported from ``precondition_reachability.py``,
and the reason is verified, not stylistic: that module's ``analyze`` returns
``reachable=True`` with an empty path whenever the goal carries no state condition
(precondition_reachability.py:318-324) — a claim that the precondition holds when nothing was
checked. The tests below pin the inversion: an absent, unresolved or unreachable goal BLOCKS.
"""

from __future__ import annotations

from typing import Any

import pytest

from ai_test_asset_center.state_precondition_planner import (
    MAX_PRECONDITION_PATH_STEPS,
    REASON_ACTOR_MISSING,
    REASON_NO_ENTRY_STATE,
    REASON_NO_TARGET_STATE,
    REASON_TARGET_ABSENT,
    REASON_TOO_LONG,
    REASON_UNREACHABLE,
    STATUS_BLOCKED,
    STATUS_PLANNED,
    build_transition_graph,
    plan_state_precondition,
)


def _ir(edges: list[tuple[str, str, str]], *, operations: list[str] | None = None) -> dict[str, Any]:
    states: dict[str, dict[str, str]] = {}
    relations: list[dict[str, str]] = []
    for source, target, operation in edges:
        states[source] = {"id": f"st_{source}", "value": source}
        states[target] = {"id": f"st_{target}", "value": target}
        relations.append({
            "relation_type": "transitions",
            "from_ref": f"st_{source}",
            "to_ref": f"st_{target}",
            "operation_ref": operation,
        })
    declared_ops = operations if operations is not None else sorted({e[2] for e in edges})
    return {
        "schema_version": "qualibug.behavior-ir.v2",
        "states": list(states.values()),
        "operations": [{"id": op} for op in declared_ops],
        "relations": relations,
        "actors": [], "entities": [], "invariants": [],
    }


LINEAR = [("CREATED", "PAID", "op-pay"), ("PAID", "SHIPPED", "op-ship"),
          ("SHIPPED", "CLOSED", "op-close")]


# ── graph construction ──────────────────────────────────────────────────────

def test_graph_is_built_only_from_declared_transitions() -> None:
    graph = build_transition_graph(_ir(LINEAR))
    assert set(graph) == {"created", "paid", "shipped"}
    assert graph["created"][0]["operation_ref"] == "op-pay"


def test_relation_types_other_than_transitions_are_ignored() -> None:
    behavior_ir = _ir(LINEAR)
    behavior_ir["relations"].append({
        "relation_type": "observes", "from_ref": "st_CREATED",
        "to_ref": "st_CLOSED", "operation_ref": "op-pay",
    })
    graph = build_transition_graph(behavior_ir)
    assert all(edge["to"] != "closed" for edge in graph.get("created", []))


def test_edge_naming_an_unknown_operation_is_not_an_edge() -> None:
    """A half-resolved transition is not one anyone can execute.

    Same three-way requirement obligation_compiler_base applies before emitting a state
    obligation: from-state, to-state and operation must all resolve.
    """
    behavior_ir = _ir([("A", "B", "ghost-op")], operations=[])
    assert build_transition_graph(behavior_ir) == {}


def test_edge_naming_an_unknown_state_is_not_an_edge() -> None:
    behavior_ir = _ir(LINEAR)
    behavior_ir["relations"].append({
        "relation_type": "transitions", "from_ref": "st_GHOST",
        "to_ref": "st_CLOSED", "operation_ref": "op-pay",
    })
    graph = build_transition_graph(behavior_ir)
    assert "ghost" not in graph


# ── planning ────────────────────────────────────────────────────────────────

def test_shortest_declared_path_is_planned() -> None:
    result = plan_state_precondition(
        behavior_ir=_ir(LINEAR), from_state="SHIPPED", actors=["actor-1"]
    )
    assert result["status"] == STATUS_PLANNED
    assert [step["operation_ref"] for step in result["steps"]] == ["op-pay", "op-ship"]
    assert [step["step_id"] for step in result["steps"]] == ["precondition_1", "precondition_2"]


def test_steps_are_fixture_phase_not_measured() -> None:
    """Establishment must stay OUTSIDE the measured window.

    _main_governed_write_steps keeps only phase in {control, treatment}. If establishment ran
    as a control step, before_state would snapshot the state BEFORE the precondition existed
    and a correctly-run experiment would report STATE_PRECONDITION_NOT_MET.
    """
    result = plan_state_precondition(
        behavior_ir=_ir(LINEAR), from_state="SHIPPED", actors=["actor-1"]
    )
    assert {step["phase"] for step in result["steps"]} == {"fixture"}
    assert {step["intent"] for step in result["steps"]} == {"state_precondition_establishment"}


def test_shortest_path_is_chosen_when_several_exist() -> None:
    behavior_ir = _ir([
        ("NEW", "A", "op-a"), ("A", "GOAL", "op-a2"),
        ("NEW", "B", "op-b"), ("B", "C", "op-b2"), ("C", "GOAL", "op-b3"),
    ])
    result = plan_state_precondition(
        behavior_ir=behavior_ir, from_state="GOAL", actors=["actor-1"]
    )
    assert result["status"] == STATUS_PLANNED
    assert len(result["steps"]) == 2


def test_goal_that_is_already_an_entry_state_needs_no_establishment() -> None:
    result = plan_state_precondition(
        behavior_ir=_ir(LINEAR), from_state="CREATED", actors=["actor-1"]
    )
    assert result["status"] == STATUS_PLANNED
    assert result["steps"] == []


def test_explicit_start_state_is_honoured() -> None:
    result = plan_state_precondition(
        behavior_ir=_ir(LINEAR), from_state="SHIPPED", actors=["actor-1"], start_state="PAID"
    )
    assert result["status"] == STATUS_PLANNED
    assert [step["operation_ref"] for step in result["steps"]] == ["op-ship"]


# ── every refusal is explicit ───────────────────────────────────────────────

def test_goal_absent_from_the_graph_blocks() -> None:
    """The inversion. The older module would have reported success with an empty path."""
    result = plan_state_precondition(
        behavior_ir=_ir(LINEAR), from_state="REFUNDED", actors=["actor-1"]
    )
    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == REASON_TARGET_ABSENT


def test_unknown_state_placeholder_blocks() -> None:
    """unknown_state is the literal substituted when a state could not be resolved.

    Planning a path to it would be planning a path to nothing.
    """
    result = plan_state_precondition(
        behavior_ir=_ir(LINEAR), from_state="unknown_state", actors=["actor-1"]
    )
    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == REASON_NO_TARGET_STATE


def test_empty_from_state_blocks() -> None:
    result = plan_state_precondition(
        behavior_ir=_ir(LINEAR), from_state="", actors=["actor-1"]
    )
    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == REASON_NO_TARGET_STATE


def test_unreachable_goal_blocks() -> None:
    behavior_ir = _ir([("NEW", "A", "op-a"), ("ISLAND", "GOAL", "op-g")])
    result = plan_state_precondition(
        behavior_ir=behavior_ir, from_state="GOAL", actors=["actor-1"], start_state="NEW"
    )
    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == REASON_UNREACHABLE


def test_graph_with_no_entry_state_blocks() -> None:
    """Every state transitioned into means the source declares no creatable entry point."""
    result = plan_state_precondition(
        behavior_ir=_ir([("A", "B", "op1"), ("B", "A", "op2")]),
        from_state="B", actors=["actor-1"],
    )
    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == REASON_NO_ENTRY_STATE


def test_missing_actor_blocks_rather_than_planning_an_unexecutable_path() -> None:
    """Each step is a governed write and needs an identity."""
    result = plan_state_precondition(
        behavior_ir=_ir(LINEAR), from_state="SHIPPED", actors=[]
    )
    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == REASON_ACTOR_MISSING


def test_empty_graph_blocks() -> None:
    result = plan_state_precondition(
        behavior_ir={"states": [], "operations": [], "relations": []},
        from_state="ANY", actors=["actor-1"],
    )
    assert result["status"] == STATUS_BLOCKED


def test_path_longer_than_the_bound_blocks() -> None:
    """A long speculative chain of governed writes is not evidence-gathering."""
    length = MAX_PRECONDITION_PATH_STEPS + 3
    edges = [(f"S{i}", f"S{i + 1}", f"op-{i}") for i in range(length)]
    result = plan_state_precondition(
        behavior_ir=_ir(edges), from_state=f"S{length}", actors=["actor-1"]
    )
    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] in {REASON_TOO_LONG, REASON_UNREACHABLE}


def test_no_outcome_is_silently_successful() -> None:
    """Every BLOCKED result names its reason. A blank refusal is not actionable."""
    for kwargs in (
        {"from_state": "REFUNDED", "actors": ["a"]},
        {"from_state": "unknown_state", "actors": ["a"]},
        {"from_state": "SHIPPED", "actors": []},
    ):
        result = plan_state_precondition(behavior_ir=_ir(LINEAR), **kwargs)
        assert result["status"] == STATUS_BLOCKED
        assert result["reason_code"]
        assert result["steps"] == []


@pytest.mark.parametrize("state", ["SHIPPED", "shipped", "Shipped", " shipped "])
def test_goal_matching_uses_the_evaluator_state_token(state: str) -> None:
    """The planner must compare states the way the assertion evaluator does.

    Otherwise a path planned for 'SHIPPED' would establish a state the evaluator then reads
    as different.
    """
    result = plan_state_precondition(
        behavior_ir=_ir(LINEAR), from_state=state, actors=["actor-1"]
    )
    assert result["status"] == STATUS_PLANNED, state
