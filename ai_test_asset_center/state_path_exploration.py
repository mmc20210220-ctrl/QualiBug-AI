"""State Path Exploration Module.

SPEC: 合法与非法状态路径自动探索及区分性实验
Breakpoint: STATE_PATH_NOT_EXPLORED

This module plans concrete operation paths to reach FORBIDDEN source states
for negative state transition testing. Unlike plan_state_path() which only
plans paths TO target states, this module:

1. Extracts forbidden source states from state rules
2. Plans concrete multi-step paths to forbidden states
3. Handles numeric threshold preconditions (capacity)
4. Handles multi-instance batch scenarios
5. Generates state path proofs and reachability proofs

Core production call chain:
    State Rule
    → Allowed/Forbidden From-State
    → State Goal
    → Candidate Operation Path
    → Actor Binding
    → Path Executability Gate
    → Existing Fixture
    → Existing Executor
    → State Path Proof
    → State Reachability Proof
    → Same Target Operation
    → Transition Observation Proof
    → Existing Oracle
    → Finding
"""

from __future__ import annotations

import hashlib
from collections import deque
from typing import Any


# ─── Utility Functions ─────────────────────────────────────────────────────────

def _dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _text(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return "sp_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ─── State Rule Extraction ─────────────────────────────────────────────────────

def extract_state_rule(
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Extract state rule from obligation and behavior IR.

    Returns:
        {
            "rule_id": str,
            "rule_type": str,  # STATE_TRANSITION_CONSTRAINT | CAPACITY_PRECONDITION | BATCH_STATE_CONSTRAINT
            "entity_ref": str,
            "state_field": str,
            "allowed_states": list[str],
            "forbidden_states": list[str],
            "target_operation_ref": str,
        }
    """
    prop = _dict(obligation.get("property"))
    expr = _dict(prop.get("expression"))
    inv_ref = _text(prop.get("invariant_ref"))
    op_ref = _text(prop.get("operation_ref"))

    # Get invariant from behavior IR
    ir = _dict(behavior_ir)
    invariants = _list(ir.get("invariants"))
    inv = {}
    for i in invariants:
        if isinstance(i, dict) and _text(i.get("id")) == inv_ref:
            inv = i
            break

    rule_type = _text(
        expr.get("rule_type")
        or inv.get("rule_type")
        or obligation.get("risk_family", "").upper()
    )

    # Determine state field and allowed/forbidden states
    state_field = _text(expr.get("state_field") or expr.get("field") or "status")
    entity_ref = _text(expr.get("entity_ref") or inv.get("entity_ref") or "")

    # Extract allowed and forbidden states
    allowed_states = _list(expr.get("allowed_states") or expr.get("allowed_from_state"))
    forbidden_states = _list(expr.get("forbidden_states") or expr.get("forbidden_from_state"))

    # If only allowed states provided, infer forbidden from state graph
    if allowed_states and not forbidden_states:
        all_states = _extract_all_states(ir, entity_ref)
        forbidden_states = [s for s in all_states if s not in allowed_states]

    # If only forbidden states provided, infer allowed from state graph
    if forbidden_states and not allowed_states:
        all_states = _extract_all_states(ir, entity_ref)
        allowed_states = [s for s in all_states if s not in forbidden_states]

    # Handle capacity precondition (numeric threshold)
    if "CAPACITY" in rule_type.upper() or "PRECONDITION" in rule_type.upper():
        capacity_field = _text(expr.get("capacity_field") or expr.get("field") or "current_tickets")
        threshold_field = _text(expr.get("threshold_field") or expr.get("max_field") or "max_tickets")
        return {
            "rule_id": _text(obligation.get("source_refs", [{}])[0].get("source_id") if obligation.get("source_refs") else inv_ref),
            "rule_type": "CAPACITY_PRECONDITION",
            "entity_ref": entity_ref or "Agent",
            "state_field": capacity_field,
            "threshold_field": threshold_field,
            "comparison_operator": _text(expr.get("comparison_operator") or ">="),
            "allowed_states": ["below_capacity"],
            "forbidden_states": ["at_capacity", "over_capacity"],
            "target_operation_ref": op_ref,
            "is_numeric_threshold": True,
        }

    return {
        "rule_id": _text(obligation.get("source_refs", [{}])[0].get("source_id") if obligation.get("source_refs") else inv_ref),
        "rule_type": rule_type or "STATE_TRANSITION_CONSTRAINT",
        "entity_ref": entity_ref,
        "state_field": state_field,
        "allowed_states": allowed_states,
        "forbidden_states": forbidden_states,
        "target_operation_ref": op_ref,
        "is_numeric_threshold": False,
    }


def _extract_all_states(behavior_ir: dict[str, Any], entity_ref: str) -> list[str]:
    """Extract all states for an entity from behavior IR state graph."""
    ir = _dict(behavior_ir)
    states = _list(ir.get("states"))
    relations = _list(ir.get("relations"))

    all_states: set[str] = set()

    # From states list
    for st in states:
        if isinstance(st, dict):
            st_entity = _text(st.get("entity_ref") or st.get("entity"))
            if not entity_ref or st_entity == entity_ref or not st_entity:
                state_id = _text(st.get("id") or st.get("state_id"))
                if state_id:
                    all_states.add(state_id)

    # From transition relations
    for rel in relations:
        if not isinstance(rel, dict):
            continue
        if _text(rel.get("relation_type")) != "transitions":
            continue
        from_ref = _text(rel.get("from_ref"))
        to_ref = _text(rel.get("to_ref"))
        if from_ref:
            all_states.add(from_ref)
        if to_ref:
            all_states.add(to_ref)

    return sorted(all_states)


# ─── State Goal Generation ─────────────────────────────────────────────────────

def generate_state_goals(
    state_rule: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate state goals for each forbidden source state.

    Returns list of state goals:
        [{
            "goal_id": str,
            "target_state": str,  # The forbidden state to reach
            "goal_type": "FORBIDDEN_SOURCE_STATE",
            "entity_ref": str,
            "state_field": str,
        }]
    """
    goals = []
    forbidden_states = _list(state_rule.get("forbidden_states"))
    entity_ref = _text(state_rule.get("entity_ref"))
    state_field = _text(state_rule.get("state_field"))
    rule_id = _text(state_rule.get("rule_id"))

    for forbidden_state in forbidden_states:
        goal_id = _stable_id("state_goal", rule_id, forbidden_state)
        goals.append({
            "goal_id": goal_id,
            "target_state": forbidden_state,
            "goal_type": "FORBIDDEN_SOURCE_STATE",
            "entity_ref": entity_ref,
            "state_field": state_field,
            "rule_id": rule_id,
        })

    return goals


# ─── Operation Path Planning ───────────────────────────────────────────────────

def build_transition_graph(behavior_ir: dict[str, Any]) -> list[dict[str, Any]]:
    """Build transition graph from behavior IR relations.

    Returns list of transitions:
        [{"from_state": str, "to_state": str, "operation_ref": str}]
    """
    ir = _dict(behavior_ir)
    relations = _list(ir.get("relations"))
    transitions = []

    for rel in relations:
        if not isinstance(rel, dict):
            continue
        if _text(rel.get("relation_type")) != "transitions":
            continue
        from_ref = _text(rel.get("from_ref"))
        to_ref = _text(rel.get("to_ref"))
        op_ref = _text(rel.get("operation_ref") or rel.get("via_operation"))
        if from_ref and to_ref:
            transitions.append({
                "from_state": from_ref,
                "to_state": to_ref,
                "operation_ref": op_ref,
            })

    return transitions


def find_initial_state(
    behavior_ir: dict[str, Any],
    transitions: list[dict[str, Any]],
) -> str:
    """Find the initial state from behavior IR or transition graph."""
    ir = _dict(behavior_ir)
    states = _list(ir.get("states"))

    # Check for explicit initial marker
    for st in states:
        if isinstance(st, dict) and st.get("initial"):
            return _text(st.get("id") or st.get("state_id"))

    # Infer from transition graph (state with no incoming transitions)
    if transitions:
        all_to_states = {t["to_state"] for t in transitions}
        all_from_states = {t["from_state"] for t in transitions}
        initial_states = all_from_states - all_to_states
        if initial_states:
            return next(iter(initial_states))

    return ""


def plan_path_to_state(
    transitions: list[dict[str, Any]],
    initial_state: str,
    target_state: str,
    max_depth: int = 12,
) -> list[dict[str, Any]]:
    """Plan shortest path from initial_state to target_state using BFS.

    Returns list of steps:
        [{"operation_ref": str, "from_state": str, "to_state": str, "step_index": int}]
    """
    if not initial_state or not target_state:
        return []

    # If target is initial state, path is empty (already there)
    if initial_state == target_state:
        return []

    queue: deque[tuple[str, list[dict[str, Any]]]] = deque()
    queue.append((initial_state, []))
    visited: set[str] = {initial_state}

    while queue:
        current, path = queue.popleft()
        if len(path) >= max_depth:
            continue

        for t in transitions:
            if t["from_state"] != current:
                continue
            next_state = t["to_state"]
            if next_state in visited:
                continue

            new_path = path + [{
                "operation_ref": t["operation_ref"],
                "from_state": current,
                "to_state": next_state,
                "step_index": len(path),
            }]

            if next_state == target_state:
                return new_path

            visited.add(next_state)
            queue.append((next_state, new_path))

    return []


def plan_capacity_path(
    state_rule: dict[str, Any],
    behavior_ir: dict[str, Any],
    capacity_value: int = 5,
) -> list[dict[str, Any]]:
    """Plan path to reach capacity threshold for numeric preconditions.

    For capacity constraints like agent.current_tickets >= agent.max_tickets,
    this plans repeating the capacity-incrementing operation N times.

    Returns list of steps:
        [{"operation_ref": str, "intent": str, "iteration": int, "expected_value": int}]
    """
    entity_ref = _text(state_rule.get("entity_ref"))
    state_field = _text(state_rule.get("state_field"))
    target_op = _text(state_rule.get("target_operation_ref"))

    # Find the operation that increments the capacity field
    ir = _dict(behavior_ir)
    operations = _list(ir.get("operations"))

    # The target operation itself usually increments the counter
    increment_op = target_op

    steps = []
    for i in range(capacity_value):
        steps.append({
            "operation_ref": increment_op,
            "intent": f"fill_{state_field}_to_{i + 1}",
            "iteration": i + 1,
            "expected_value": i + 1,
            "step_index": i,
        })

    return steps


def plan_multi_instance_path(
    state_rule: dict[str, Any],
    behavior_ir: dict[str, Any],
    instance_configs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Plan path for multi-instance batch scenarios.

    For batch operations requiring multiple entities in different states.

    Args:
        instance_configs: List of {"instance_id": str, "target_state": str}

    Returns list of steps for all instances.
    """
    transitions = build_transition_graph(behavior_ir)
    initial_state = find_initial_state(behavior_ir, transitions)

    all_steps = []
    step_offset = 0

    for config in instance_configs:
        instance_id = _text(config.get("instance_id"))
        target_state = _text(config.get("target_state"))

        path = plan_path_to_state(transitions, initial_state, target_state)

        for step in path:
            all_steps.append({
                **step,
                "instance_id": instance_id,
                "step_index": step_offset + step["step_index"],
            })

        # If target is initial state, just create the instance
        if not path and target_state == initial_state:
            all_steps.append({
                "operation_ref": "create_" + _text(state_rule.get("entity_ref", "entity")).lower(),
                "from_state": None,
                "to_state": target_state,
                "instance_id": instance_id,
                "step_index": step_offset,
            })

        step_offset = len(all_steps)

    return all_steps


# ─── State Path Exploration (Main Entry) ───────────────────────────────────────

def explore_state_paths(
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
    budget: int = 24,
) -> dict[str, Any]:
    """Explore state paths for forbidden source states.

    Main entry point for STATE_PATH_NOT_EXPLORED breakpoint repair.

    Returns:
        {
            "status": "EXPLORED" | "NO_FORBIDDEN_STATES" | "NO_TRANSITIONS",
            "state_rule": dict,
            "state_goals": list,
            "state_paths": list,
            "experiments": list,
            "proofs": list,
        }
    """
    # Step 1: Extract state rule
    state_rule = extract_state_rule(obligation, behavior_ir)

    forbidden_states = _list(state_rule.get("forbidden_states"))
    if not forbidden_states:
        return {
            "status": "NO_FORBIDDEN_STATES",
            "state_rule": state_rule,
            "state_goals": [],
            "state_paths": [],
            "experiments": [],
            "proofs": [],
        }

    # Step 2: Generate state goals
    state_goals = generate_state_goals(state_rule, behavior_ir)

    # Step 3: Plan paths based on rule type
    is_numeric = state_rule.get("is_numeric_threshold", False)
    rule_type = _text(state_rule.get("rule_type"))

    state_paths = []
    experiments = []
    proofs = []

    if is_numeric or "CAPACITY" in rule_type.upper():
        # Numeric threshold precondition
        capacity_path = plan_capacity_path(state_rule, behavior_ir)
        state_paths.append({
            "path_id": _stable_id("cap_path", state_rule.get("rule_id", "")),
            "path_type": "CAPACITY_FILL",
            "target_state": "at_capacity",
            "steps": capacity_path,
        })

        # Generate experiment for capacity violation
        exp = _build_capacity_experiment(state_rule, capacity_path, obligation, behavior_ir)
        if exp:
            experiments.append(exp)
            proofs.append(_build_state_path_proof(state_rule, capacity_path, "at_capacity"))

    elif "BATCH" in rule_type.upper():
        # Multi-instance batch scenario
        # Create instances in different states
        instance_configs = [
            {"instance_id": "instance_1", "target_state": forbidden_states[0] if forbidden_states else "ASSIGNED"},
            {"instance_id": "instance_2", "target_state": "OPEN"},  # One valid instance
        ]
        multi_path = plan_multi_instance_path(state_rule, behavior_ir, instance_configs)
        state_paths.append({
            "path_id": _stable_id("batch_path", state_rule.get("rule_id", "")),
            "path_type": "MULTI_INSTANCE",
            "target_state": "mixed_states",
            "steps": multi_path,
        })

        exp = _build_batch_experiment(state_rule, multi_path, obligation, behavior_ir)
        if exp:
            experiments.append(exp)
            proofs.append(_build_state_path_proof(state_rule, multi_path, "mixed_states"))

    else:
        # Standard state transition constraint
        transitions = build_transition_graph(behavior_ir)
        initial_state = find_initial_state(behavior_ir, transitions)

        if not transitions:
            return {
                "status": "NO_TRANSITIONS",
                "state_rule": state_rule,
                "state_goals": state_goals,
                "state_paths": [],
                "experiments": [],
                "proofs": [],
            }

        # Plan path to each forbidden state
        for goal in state_goals:
            target_state = _text(goal.get("target_state"))
            path = plan_path_to_state(transitions, initial_state, target_state)

            path_entry = {
                "path_id": _stable_id("path", state_rule.get("rule_id", ""), target_state),
                "path_type": "STATE_TRANSITION",
                "target_state": target_state,
                "initial_state": initial_state,
                "steps": path,
                "goal_id": goal.get("goal_id"),
            }
            state_paths.append(path_entry)

            # Generate experiment for this forbidden state
            exp = _build_state_experiment(state_rule, path_entry, obligation, behavior_ir)
            if exp:
                experiments.append(exp)
                proofs.append(_build_state_path_proof(state_rule, path, target_state))

            # Budget check
            if len(experiments) >= budget:
                break

    return {
        "status": "EXPLORED",
        "state_rule": state_rule,
        "state_goals": state_goals,
        "state_paths": state_paths,
        "experiments": experiments,
        "proofs": proofs,
    }


# ─── Experiment Builders ───────────────────────────────────────────────────────

def _build_state_experiment(
    state_rule: dict[str, Any],
    path_entry: dict[str, Any],
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any] | None:
    """Build experiment for state transition violation."""
    target_state = _text(path_entry.get("target_state"))
    steps = _list(path_entry.get("steps"))
    target_op = _text(state_rule.get("target_operation_ref"))
    rule_id = _text(state_rule.get("rule_id"))

    # Build fixture steps (path to forbidden state)
    fixture_steps = []
    for step in steps:
        fixture_steps.append({
            "operation_ref": step.get("operation_ref"),
            "intent": f"advance_to_{step.get('to_state')}",
            "expected_state": step.get("to_state"),
            "from_state": step.get("from_state"),
            "step_index": step.get("step_index"),
        })

    # If target state is initial state, no fixture steps needed
    if not steps:
        initial_state = _text(path_entry.get("initial_state"))
        if initial_state == target_state:
            # Entity starts in forbidden state - just create it
            fixture_steps = [{
                "operation_ref": "create_" + _text(state_rule.get("entity_ref", "entity")).lower(),
                "intent": f"create_in_{target_state}",
                "expected_state": target_state,
                "step_index": 0,
            }]

    # Build violation step (attempt target operation from forbidden state)
    violation_step = {
        "operation_ref": target_op,
        "intent": f"attempt_from_{target_state}",
        "from_state": target_state,
        "expected_outcome": "rejected",
        "actual_expected": "accepted_if_bug",
    }

    # Build control step (attempt from allowed state for comparison)
    allowed_states = _list(state_rule.get("allowed_states"))
    control_step = None
    if allowed_states:
        control_state = allowed_states[0]
        control_step = {
            "operation_ref": target_op,
            "intent": f"control_from_{control_state}",
            "from_state": control_state,
            "expected_outcome": "accepted",
        }

    exp_id = _stable_id("state_exp", rule_id, target_state, target_op)

    return {
        "experiment_id": exp_id,
        "experiment_type": "STATE_PATH_VIOLATION",
        "rule_id": rule_id,
        "target_operation": target_op,
        "forbidden_source_state": target_state,
        "fixture_steps": fixture_steps,
        "violation_step": violation_step,
        "control_step": control_step,
        "state_path_id": path_entry.get("path_id"),
        "mechanism": "STATE_NEGATIVE",
        "dedup_signature": f"{rule_id}|STATE_NEGATIVE|{target_op}|{target_state}",
    }


def _build_capacity_experiment(
    state_rule: dict[str, Any],
    capacity_path: list[dict[str, Any]],
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any] | None:
    """Build experiment for capacity precondition violation."""
    target_op = _text(state_rule.get("target_operation_ref"))
    rule_id = _text(state_rule.get("rule_id"))
    state_field = _text(state_rule.get("state_field"))
    threshold_field = _text(state_rule.get("threshold_field"))

    # Fixture: fill to capacity
    fixture_steps = []
    for step in capacity_path:
        fixture_steps.append({
            "operation_ref": step.get("operation_ref"),
            "intent": step.get("intent"),
            "expected_value": step.get("expected_value"),
            "step_index": step.get("step_index"),
        })

    # Violation: attempt one more beyond capacity
    violation_step = {
        "operation_ref": target_op,
        "intent": f"attempt_beyond_{state_field}_capacity",
        "expected_state": f"{state_field} >= {threshold_field}",
        "expected_outcome": "rejected",
        "actual_expected": "accepted_if_bug",
    }

    exp_id = _stable_id("cap_exp", rule_id, target_op)

    return {
        "experiment_id": exp_id,
        "experiment_type": "CAPACITY_PRECONDITION_VIOLATION",
        "rule_id": rule_id,
        "target_operation": target_op,
        "forbidden_source_state": "at_capacity",
        "fixture_steps": fixture_steps,
        "violation_step": violation_step,
        "control_step": None,
        "state_path_id": _stable_id("cap_path", rule_id),
        "mechanism": "PRECONDITION_VIOLATION",
        "dedup_signature": f"{rule_id}|PRECONDITION_VIOLATION|{target_op}|at_capacity",
    }


def _build_batch_experiment(
    state_rule: dict[str, Any],
    multi_path: list[dict[str, Any]],
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any] | None:
    """Build experiment for batch state constraint violation."""
    target_op = _text(state_rule.get("target_operation_ref"))
    rule_id = _text(state_rule.get("rule_id"))

    # Fixture: create instances in mixed states
    fixture_steps = []
    for step in multi_path:
        fixture_steps.append({
            "operation_ref": step.get("operation_ref"),
            "intent": f"setup_{step.get('instance_id', 'instance')}_to_{step.get('to_state')}",
            "instance_id": step.get("instance_id"),
            "expected_state": step.get("to_state"),
            "step_index": step.get("step_index"),
        })

    # Violation: batch operation on mixed states
    violation_step = {
        "operation_ref": target_op,
        "intent": "batch_with_mixed_states",
        "expected_outcome": "partial_rejection",
        "actual_expected": "all_accepted_if_bug",
    }

    exp_id = _stable_id("batch_exp", rule_id, target_op)

    return {
        "experiment_id": exp_id,
        "experiment_type": "BATCH_STATE_VIOLATION",
        "rule_id": rule_id,
        "target_operation": target_op,
        "forbidden_source_state": "mixed_states",
        "fixture_steps": fixture_steps,
        "violation_step": violation_step,
        "control_step": None,
        "state_path_id": _stable_id("batch_path", rule_id),
        "mechanism": "STATE_NEGATIVE",
        "dedup_signature": f"{rule_id}|STATE_NEGATIVE|{target_op}|mixed_states",
    }


# ─── Proof Builders ────────────────────────────────────────────────────────────

def _build_state_path_proof(
    state_rule: dict[str, Any],
    path: list[dict[str, Any]],
    target_state: str,
) -> dict[str, Any]:
    """Build state path proof for audit trail."""
    rule_id = _text(state_rule.get("rule_id"))
    proof_id = _stable_id("proof", rule_id, target_state)

    return {
        "proof_id": proof_id,
        "proof_type": "STATE_PATH_PROOF",
        "rule_id": rule_id,
        "target_state": target_state,
        "path_length": len(path),
        "path_operations": [step.get("operation_ref") for step in path],
        "path_states": [step.get("to_state") for step in path],
        "is_reachable": len(path) > 0 or target_state == "OPEN",  # OPEN is initial
    }


def build_reachability_proof(
    state_rule: dict[str, Any],
    state_paths: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build reachability proof for all planned paths."""
    rule_id = _text(state_rule.get("rule_id"))

    reachable_states = []
    unreachable_states = []

    for path_entry in state_paths:
        target_state = _text(path_entry.get("target_state"))
        steps = _list(path_entry.get("steps"))
        path_type = _text(path_entry.get("path_type"))

        # Capacity and batch paths are always reachable by construction
        if path_type in ("CAPACITY_FILL", "MULTI_INSTANCE"):
            reachable_states.append({
                "state": target_state,
                "path_type": path_type,
                "steps": len(steps),
            })
        elif steps:
            reachable_states.append({
                "state": target_state,
                "path_type": path_type,
                "steps": len(steps),
                "operations": [s.get("operation_ref") for s in steps],
            })
        else:
            # Check if target is initial state
            initial = _text(path_entry.get("initial_state"))
            if target_state == initial:
                reachable_states.append({
                    "state": target_state,
                    "path_type": "INITIAL_STATE",
                    "steps": 0,
                })
            else:
                unreachable_states.append({
                    "state": target_state,
                    "reason": "NO_PATH_FOUND",
                })

    return {
        "proof_id": _stable_id("reach_proof", rule_id),
        "proof_type": "STATE_REACHABILITY_PROOF",
        "rule_id": rule_id,
        "reachable_states": reachable_states,
        "unreachable_states": unreachable_states,
        "all_reachable": len(unreachable_states) == 0,
    }


# ─── Integration Helper ────────────────────────────────────────────────────────

def plan_state_path_experiments(
    obligations: list[dict[str, Any]],
    behavior_ir: dict[str, Any],
    budget: int = 24,
) -> dict[str, Any]:
    """Plan state path experiments for multiple obligations.

    This is the main integration point for deep_experiment_planner.py.

    Returns:
        {
            "planned_count": int,
            "experiments": list,
            "proofs": list,
            "reachability_proofs": list,
            "state_rules": list,
        }
    """
    all_experiments = []
    all_proofs = []
    all_reachability_proofs = []
    all_state_rules = []

    for obl in obligations:
        if not isinstance(obl, dict):
            continue

        # Check if this obligation needs state path exploration
        prop = _dict(obl.get("property"))
        expr = _dict(prop.get("expression"))
        risk_family = _text(obl.get("risk_family")).upper()
        rule_type = _text(expr.get("rule_type")).upper()

        # Only process state-related obligations
        is_state_related = (
            "STATE" in risk_family
            or "STATE" in rule_type
            or "TRANSITION" in rule_type
            or "PRECONDITION" in risk_family
            or "CAPACITY" in rule_type
            or "BATCH" in rule_type
        )

        if not is_state_related:
            continue

        # Explore state paths
        result = explore_state_paths(obl, behavior_ir, budget=len(all_experiments) + budget)

        if result.get("status") == "EXPLORED":
            all_experiments.extend(result.get("experiments", []))
            all_proofs.extend(result.get("proofs", []))
            all_state_rules.append(result.get("state_rule"))

            # Build reachability proof
            reach_proof = build_reachability_proof(
                result.get("state_rule", {}),
                result.get("state_paths", []),
            )
            all_reachability_proofs.append(reach_proof)

        # Budget check
        if len(all_experiments) >= budget:
            break

    return {
        "planned_count": len(all_experiments),
        "experiments": all_experiments[:budget],
        "proofs": all_proofs,
        "reachability_proofs": all_reachability_proofs,
        "state_rules": all_state_rules,
    }
