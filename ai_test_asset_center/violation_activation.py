"""Violation Activation — Oracle PASS trust audit and violation condition reverse-engineering.

Inserted between experiment planning and execution. For each target rule whose
oracle returned PASS, this module:
1. Classifies the PASS (true vs false)
2. Compiles a violation condition from the structured expression
3. Generates reverse mutations that satisfy the violation condition
4. Verifies precondition satisfiability
5. Checks observer input completeness
6. Produces Control/Violation experiment pairs
7. Supports up to MAX_VIOLATION_REFINEMENTS refinement rounds

Fully generic: operates on structured_expression, rule_type, field_semantics,
state_graph, operation_graph, relation_bindings, observer_requirements.
No project-specific or benchmark-specific logic.
"""
from __future__ import annotations

import hashlib
import time
from copy import deepcopy
from typing import Any

# ─── Constants ─────────────────────────────────────────────────────────────────

MAX_VIOLATION_REFINEMENTS = 2

VIOLATION_TRIGGERED = "VIOLATION_TRIGGERED"
TRUE_PASS_CONFIRMED = "TRUE_PASS_CONFIRMED"
VIOLATION_CONDITION_UNSATISFIABLE = "VIOLATION_CONDITION_UNSATISFIABLE"
PRECONDITION_NOT_REACHED = "PRECONDITION_NOT_REACHED"
ORACLE_INPUT_INCOMPLETE = "ORACLE_INPUT_INCOMPLETE"
WRONG_RULE_MODEL = "WRONG_RULE_MODEL"
VIOLATION_NOT_ACTIVATED = "VIOLATION_NOT_ACTIVATED"

# PASS classification types
WEAK_MUTATION_PASS = "WEAK_MUTATION_PASS"
WRONG_PRECONDITION_PASS = "WRONG_PRECONDITION_PASS"
WRONG_OPERATION_SEQUENCE_PASS = "WRONG_OPERATION_SEQUENCE_PASS"
INCOMPLETE_OBSERVATION_PASS = "INCOMPLETE_OBSERVATION_PASS"
WRONG_SCOPE_PASS = "WRONG_SCOPE_PASS"
WRONG_RULE_INSTANCE_PASS = "WRONG_RULE_INSTANCE_PASS"
TRUE_PASS = "TRUE_PASS"

# Mutation strength levels
MINIMAL_VIOLATION = "MINIMAL_VIOLATION"
CLEAR_VIOLATION = "CLEAR_VIOLATION"
COMPOUND_VIOLATION = "COMPOUND_VIOLATION"


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _text(v: Any) -> str:
    return str(v or "").strip()


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _stable_id(*parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    return "va_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ─── §8: Violation Condition Compilation ──────────────────────────────────────

def compile_violation_condition(
    structured_expression: dict[str, Any],
    *,
    rule_id: str = "",
    rule_type: str = "",
) -> dict[str, Any]:
    """Compile a violation condition from a structured expression.

    Returns a violation_condition dict per SPEC §8.
    """
    expr = _dict(structured_expression)
    expr_type = _text(expr.get("type") or expr.get("expression_type")).upper()
    operator = _text(expr.get("operator")).upper()
    left = _dict(expr.get("left"))
    right = _dict(expr.get("right"))

    valid_condition = _text(expr.get("description"))
    violating_condition = ""
    mutation_targets: list[dict[str, Any]] = []
    required_preconditions: list[str] = []
    required_operation_sequence: list[str] = []
    required_observations: list[str] = []
    satisfiable = True

    if expr_type == "CONCURRENCY" or operator == "CONFLICT":
        violating_condition = "stale_version_update_accepted_without_409"
        entity = _text(left.get("entity"))
        version_field = _text(left.get("field") or "version")
        mutation_targets = [{
            "entity": entity,
            "field": version_field,
            "mutation_type": "stale_version",
            "strategy": "read_version_then_update_with_old_version",
        }]
        required_preconditions = [
            f"{entity}.status allows update",
            f"{entity}.{version_field} = V (known)",
        ]
        required_operation_sequence = [
            f"GET {entity} (read version V)",
            f"Actor A: PATCH {entity} with version V → success (V+1)",
            f"Actor B: PATCH {entity} with stale version V → expect 409",
        ]
        required_observations = [
            f"{entity}.{version_field} before",
            "actor_a_response_code",
            "actor_b_response_code",
        ]

    elif expr_type == "SUM" or (operator == "EQ" and left.get("aggregate") == "SUM"):
        agg_entity = _text(left.get("entity"))
        agg_field = _text(left.get("field"))
        scope_field = _text(left.get("scope") or "parent_id")
        root_entity = _text(right.get("entity"))
        root_field = _text(right.get("field"))
        violating_condition = (
            f"SUM({agg_entity}.{agg_field}) != {root_entity}.{root_field} "
            f"but operation accepted"
        )
        mutation_targets = [{
            "entity": agg_entity,
            "field": agg_field,
            "mutation_type": "sum_mismatch",
            "strategy": "create_child_with_amount_exceeding_parent_total",
        }]
        required_preconditions = [
            f"{root_entity} exists with known {root_field}",
            f"at least one {agg_entity} exists",
        ]
        required_observations = [
            f"SUM({agg_entity}.{agg_field}) WHERE {scope_field}",
            f"{root_entity}.{root_field}",
            "operation_response_code",
        ]

    elif expr_type == "DELTA" or operator == "DELTA_EQ":
        left_entity = _text(left.get("entity"))
        left_field = _text(left.get("field"))
        left_delta = _text(left.get("expected_delta"))
        right_entity = _text(right.get("entity"))
        right_field = _text(right.get("field"))
        right_delta = _text(right.get("expected_delta"))
        violating_condition = (
            f"after operation: {left_entity}.{left_field} delta != {left_delta} "
            f"OR {right_entity}.{right_field} delta != {right_delta}"
        )
        mutation_targets = [{
            "entity": left_entity,
            "field": left_field,
            "mutation_type": "delta_violation",
            "strategy": "execute_operation_and_verify_cross_entity_delta",
        }]
        required_preconditions = [
            f"{left_entity}.{left_field} observable before operation",
            f"{right_entity}.{right_field} observable before operation",
        ]
        required_observations = [
            f"{left_entity}.{left_field} before",
            f"{left_entity}.{left_field} after",
            f"{right_entity}.{right_field} before",
            f"{right_entity}.{right_field} after",
        ]

    elif expr_type == "IMPLIES":
        condition = _dict(expr.get("condition") or left)
        constraint = _dict(expr.get("constraint") or right)
        cond_entity = _text(condition.get("entity"))
        cond_field = _text(condition.get("field"))
        cond_value = _text(condition.get("value"))
        cons_entity = _text(constraint.get("entity"))
        cons_field = _text(constraint.get("field"))
        cons_expected = _text(constraint.get("expected") or constraint.get("expected_result"))
        violating_condition = (
            f"{cond_entity}.{cond_field}={cond_value} "
            f"BUT {cons_entity}.{cons_field} != {cons_expected}"
        )
        mutation_targets = [{
            "entity": cond_entity,
            "field": cond_field,
            "mutation_type": "force_antecedent_true",
            "strategy": f"set {cond_entity}.{cond_field}={cond_value} then check {cons_entity}",
        }]
        required_preconditions = [
            f"{cond_entity}.{cond_field} can be set to {cond_value}",
            f"{cons_entity} exists and is observable",
        ]
        required_observations = [
            f"{cond_entity}.{cond_field} after operation",
            f"{cons_entity}.{cons_field} after operation",
        ]

    elif expr_type == "LTE" or (operator in ("LTE", "GTE") and expr_type != "TEMPORAL"):
        left_entity = _text(left.get("entity"))
        left_field = _text(left.get("field"))
        right_entity = _text(right.get("entity"))
        right_field = _text(right.get("field"))
        right_value = right.get("value")
        if right_value is not None:
            violating_condition = f"{left_entity}.{left_field} < {right_value} (violates GTE)"
        else:
            violating_condition = (
                f"{left_entity}.{left_field} > {right_entity}.{right_field} "
                f"but operation accepted"
            )
        mutation_targets = [{
            "entity": left_entity,
            "field": left_field,
            "mutation_type": "limit_exceeded",
            "strategy": "set_value_above_boundary",
        }]
        required_preconditions = [
            f"boundary value ({right_entity}.{right_field}) is known",
        ]
        required_observations = [
            f"{left_entity}.{left_field} value",
            f"boundary value",
            "operation_response_code",
        ]

    elif expr_type == "TEMPORAL" or operator in ("LT", "GT"):
        left_entity = _text(left.get("entity"))
        left_field = _text(left.get("field"))
        right_entity = _text(right.get("entity"))
        right_field = _text(right.get("field"))
        op_symbol = {"LTE": "<=", "GTE": ">=", "LT": "<", "GT": ">"}.get(operator, "<=")
        violating_condition = (
            f"{left_entity}.{left_field} > {right_entity}.{right_field} "
            f"(violates {op_symbol}) but operation accepted"
        )
        mutation_targets = [{
            "entity": left_entity,
            "field": left_field,
            "mutation_type": "temporal_boundary_violation",
            "strategy": f"set {left_entity}.{left_field} after {right_entity}.{right_field}",
        }]
        required_preconditions = [
            f"{right_entity}.{right_field} has known value",
            f"{left_entity}.{left_field} is writable",
        ]
        required_observations = [
            f"{left_entity}.{left_field} value",
            f"{right_entity}.{right_field} value",
            "operation_response_code",
        ]

    elif expr_type == "STATE":
        violating_condition = "forbidden state transition accepted"
        mutation_targets = [{
            "entity": _text(left.get("entity") or expr.get("entity")),
            "field": "status",
            "mutation_type": "forbidden_transition",
            "strategy": "execute_operation_from_wrong_state",
        }]
        required_preconditions = ["entity in forbidden source state"]
        required_observations = ["entity.status before", "entity.status after", "response_code"]

    else:
        # Generic fallback
        violating_condition = f"rule violated: {valid_condition}"
        mutation_targets = [{
            "entity": _text(left.get("entity") or "root"),
            "field": _text(left.get("field") or "target"),
            "mutation_type": "generic_violation",
            "strategy": "negate_rule_condition",
        }]
        satisfiable = True

    return {
        "source_rule_id": rule_id,
        "expression_type": expr_type or operator,
        "valid_condition": valid_condition,
        "violating_condition": violating_condition,
        "mutation_targets": mutation_targets,
        "required_preconditions": required_preconditions,
        "required_operation_sequence": required_operation_sequence,
        "required_observations": required_observations,
        "satisfiable": satisfiable,
    }


# ─── §9: Violation Satisfiability Check ───────────────────────────────────────

def check_violation_satisfiability(
    violation_condition: dict[str, Any],
    *,
    available_operations: list[dict[str, Any]],
    available_actors: list[dict[str, Any]],
    available_observers: list[str],
    fixture_capabilities: list[str],
) -> dict[str, Any]:
    """Check whether a violation condition can be satisfied given runtime capabilities."""
    vc = _dict(violation_condition)
    mutation_targets = _list(vc.get("mutation_targets"))
    required_obs = _list(vc.get("required_observations"))
    required_preconds = _list(vc.get("required_preconditions"))

    writable_fields = all(
        _text(mt.get("strategy")) != "not_writable"
        for mt in mutation_targets
    )
    operation_available = len(available_operations) > 0
    actor_available = len(available_actors) > 0
    observer_available = all(
        any(obs_cap in obs for obs_cap in available_observers)
        for obs in required_obs
    ) if required_obs else True
    fixture_available = len(fixture_capabilities) > 0

    satisfiable = all([
        writable_fields,
        operation_available,
        actor_available,
        fixture_available,
    ])

    blocked_reason = ""
    if not writable_fields:
        blocked_reason = "target_field_not_writable"
    elif not operation_available:
        blocked_reason = "target_operation_unavailable"
    elif not actor_available:
        blocked_reason = "no_actor_available"
    elif not fixture_available:
        blocked_reason = "fixture_unavailable"

    return {
        "writable_fields": writable_fields,
        "reachable_preconditions": True,
        "operation_available": operation_available,
        "actor_available": actor_available,
        "fixture_available": fixture_available,
        "observer_available": observer_available,
        "satisfiable": satisfiable,
        "blocked_reason": blocked_reason,
    }


# ─── §10/11: Reverse Mutation Generation ──────────────────────────────────────

def generate_violation_mutation(
    violation_condition: dict[str, Any],
    *,
    runtime_values: dict[str, Any],
    strength: str = CLEAR_VIOLATION,
) -> list[dict[str, Any]]:
    """Generate mutation(s) that satisfy the violation condition.

    Uses runtime_values (observed from the live system) to compute boundary + step.
    Never uses benchmark values or hardcoded project-specific data.
    """
    vc = _dict(violation_condition)
    expr_type = _text(vc.get("expression_type")).upper()
    mutation_targets = _list(vc.get("mutation_targets"))
    mutations: list[dict[str, Any]] = []

    for mt in mutation_targets:
        entity = _text(mt.get("entity"))
        field = _text(mt.get("field"))
        strategy = _text(mt.get("strategy"))
        mt_type = _text(mt.get("mutation_type"))

        if mt_type == "stale_version":
            # Concurrency: use current version from runtime
            current_version = int(_num(runtime_values.get(f"{entity}.{field}", 1)))
            mutations.append({
                "mutation_id": _stable_id("va", entity, field, "stale", strength),
                "entity": entity,
                "field": field,
                "mutation_type": mt_type,
                "strength": strength,
                "value": current_version,  # Use stale version (before concurrent update)
                "headers": {"If-Match-Version": str(current_version)},
                "requires_concurrent_actor": True,
                "expected_outcome": "rejected_409",
            })

        elif mt_type == "sum_mismatch":
            # Conservation SUM: create child with amount that breaks sum
            parent_total = _num(runtime_values.get(f"{entity}.parent_total", 0))
            existing_sum = _num(runtime_values.get(f"{entity}.current_sum", 0))
            if strength == MINIMAL_VIOLATION:
                violation_amount = parent_total - existing_sum + 1
            else:
                violation_amount = parent_total * 0.6  # Clear overflow
            mutations.append({
                "mutation_id": _stable_id("va", entity, field, "sum", strength),
                "entity": entity,
                "field": field,
                "mutation_type": mt_type,
                "strength": strength,
                "value": violation_amount,
                "expected_outcome": "operation_rejected_or_sum_violated",
            })

        elif mt_type == "delta_violation":
            # Delta: observe before/after and verify conservation
            mutations.append({
                "mutation_id": _stable_id("va", entity, field, "delta", strength),
                "entity": entity,
                "field": field,
                "mutation_type": mt_type,
                "strength": strength,
                "value": None,  # No mutation needed - observe delta
                "observe_before_after": True,
                "expected_outcome": "delta_mismatch_detected",
            })

        elif mt_type == "force_antecedent_true":
            # IMPLIES: force condition=true, then check consequence
            cond_value = _text(mt.get("strategy")).split("=")[-1] if "=" in _text(mt.get("strategy")) else ""
            mutations.append({
                "mutation_id": _stable_id("va", entity, field, "implies", strength),
                "entity": entity,
                "field": field,
                "mutation_type": mt_type,
                "strength": strength,
                "value": cond_value,
                "force_condition_true": True,
                "expected_outcome": "consequence_violation",
            })

        elif mt_type == "temporal_boundary_violation":
            # Temporal: set date beyond boundary
            boundary_date = _text(runtime_values.get(f"{entity}.boundary_date", ""))
            if boundary_date:
                # Generate a date clearly after boundary
                violation_date = _compute_date_beyond(boundary_date, strength)
            else:
                violation_date = "2099-12-31"  # Far future as clear violation
            mutations.append({
                "mutation_id": _stable_id("va", entity, field, "temporal", strength),
                "entity": entity,
                "field": field,
                "mutation_type": mt_type,
                "strength": strength,
                "value": violation_date,
                "expected_outcome": "operation_rejected_or_temporal_violated",
            })

        elif mt_type == "limit_exceeded":
            # LTE/GTE: exceed the limit
            limit_value = _num(runtime_values.get(f"{entity}.limit", 0))
            remaining = _num(runtime_values.get(f"{entity}.remaining", limit_value))
            if strength == MINIMAL_VIOLATION:
                violation_value = remaining + 1
            else:
                violation_value = remaining + max(remaining * 0.5, 100)
            mutations.append({
                "mutation_id": _stable_id("va", entity, field, "limit", strength),
                "entity": entity,
                "field": field,
                "mutation_type": mt_type,
                "strength": strength,
                "value": violation_value,
                "expected_outcome": "operation_rejected_or_limit_violated",
            })

        elif mt_type == "forbidden_transition":
            # State: execute from wrong state
            mutations.append({
                "mutation_id": _stable_id("va", entity, field, "state", strength),
                "entity": entity,
                "field": field,
                "mutation_type": mt_type,
                "strength": strength,
                "value": None,
                "execute_from_forbidden_state": True,
                "expected_outcome": "operation_rejected_409",
            })

        else:
            # Generic
            mutations.append({
                "mutation_id": _stable_id("va", entity, field, "generic", strength),
                "entity": entity,
                "field": field,
                "mutation_type": mt_type,
                "strength": strength,
                "value": None,
                "expected_outcome": "violation_detected",
            })

    return mutations


def _compute_date_beyond(boundary_date: str, strength: str) -> str:
    """Compute a date clearly beyond the boundary."""
    try:
        from datetime import datetime, timedelta
        dt = datetime.strptime(boundary_date[:10], "%Y-%m-%d")
        if strength == MINIMAL_VIOLATION:
            dt += timedelta(days=1)
        else:
            dt += timedelta(days=30)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return "2099-12-31"


# ─── §15: Oracle Input Completeness Gate ──────────────────────────────────────

def check_oracle_input_completeness(
    required_entities: list[str],
    observed_entities: list[str],
    required_fields: list[str],
    observed_fields: list[str],
    *,
    pagination_complete: bool = True,
    scope_verified: bool = True,
) -> dict[str, Any]:
    """Check whether oracle input is complete enough to return PASS/FAIL.

    If incomplete, returns complete=False and the oracle must return
    INDETERMINATE or ORACLE_INPUT_INCOMPLETE, never PASS.
    """
    missing_entities = [e for e in required_entities if e not in observed_entities]
    missing_fields = [f for f in required_fields if f not in observed_fields]

    complete = (
        not missing_entities
        and not missing_fields
        and pagination_complete
        and scope_verified
    )

    return {
        "required_entities": required_entities,
        "observed_entities": observed_entities,
        "required_fields": required_fields,
        "observed_fields": observed_fields,
        "missing_entities": missing_entities,
        "missing_fields": missing_fields,
        "pagination_complete": pagination_complete,
        "scope_verified": scope_verified,
        "complete": complete,
    }


# ─── §17: Control/Violation Pair Builder ──────────────────────────────────────

def build_violation_experiment_pair(
    target: dict[str, Any],
    violation_condition: dict[str, Any],
    mutations: list[dict[str, Any]],
    *,
    actor_ref: str = "",
    base_url: str = "",
) -> dict[str, Any]:
    """Build a Control + Violation experiment pair for a target rule.

    Control: valid operation → proves fixture and oracle work.
    Violation: mutated operation → should trigger oracle FAIL.
    """
    target_id = _text(target.get("target_id"))
    rule_id = _text(target.get("internal_rule_id"))
    expr = _dict(target.get("structured_expression"))
    operations = _list(target.get("operation_ids"))
    primary_op = operations[0] if operations else ""

    # Parse operation
    parts = primary_op.split(" ", 1)
    method = parts[0] if parts else "POST"
    path_template = parts[1] if len(parts) > 1 else ""

    control_plan = [{
        "step_id": "control_1",
        "actor_ref": actor_ref,
        "operation_ref": primary_op,
        "method": method,
        "path_template": path_template,
        "intent": "valid_operation_proves_fixture_works",
        "protocol_step": "positive_control",
        "expected_status_class": 2,
    }]

    violation_plan: list[dict[str, Any]] = []
    for i, mut in enumerate(mutations):
        step: dict[str, Any] = {
            "step_id": f"violation_{i + 1}",
            "actor_ref": actor_ref,
            "operation_ref": primary_op,
            "method": method,
            "path_template": path_template,
            "intent": f"violation_{_text(mut.get('mutation_type'))}",
            "protocol_step": "violation_mutation",
            "mutation": mut,
            "expected_outcome": _text(mut.get("expected_outcome")),
        }
        if mut.get("headers"):
            step["headers"] = mut["headers"]
        if mut.get("requires_concurrent_actor"):
            step["requires_concurrent_actor"] = True
        violation_plan.append(step)

    # Observer requirements from violation condition
    vc = _dict(violation_condition)
    required_obs = _list(vc.get("required_observations"))
    observers = [{"observer_id": "http_response"}]
    if any("before" in obs or "after" in obs for obs in required_obs):
        observers.append({"observer_id": "entity_state", "fields": required_obs})
    if any("SUM" in obs or "aggregate" in obs.lower() for obs in required_obs):
        observers.append({"observer_id": "aggregate_scope", "fields": required_obs})
    if len(observers) == 1:
        observers.append({"observer_id": "entity_state"})

    return {
        "experiment_id": _stable_id("va_pair", target_id, rule_id),
        "target_id": target_id,
        "rule_id": rule_id,
        "expression_type": _text(expr.get("type")),
        "control_plan": control_plan,
        "violation_plan": violation_plan,
        "observers": observers,
        "assertion": {
            "kind": "violation_activation_contrast",
            "control_must_succeed": True,
            "violation_expected_fail": True,
        },
        "violation_condition": vc,
        "oracle_input_requirements": {
            "required_observations": required_obs,
            "required_preconditions": _list(vc.get("required_preconditions")),
        },
    }


# ─── §19: Refinement Loop ─────────────────────────────────────────────────────

def refine_violation_plan(
    previous_result: dict[str, Any],
    violation_condition: dict[str, Any],
    *,
    refinement_round: int = 1,
    runtime_values: dict[str, Any],
) -> dict[str, Any] | None:
    """Attempt to refine a violation plan that previously returned PASS.

    Allowed adjustments: mutation strength, repeat count, cumulative sequence,
    state path, observer fields, filter scope.
    Forbidden: rule expectation, oracle operator, benchmark criteria, finding threshold.

    Returns None if max refinements exceeded.
    """
    if refinement_round > MAX_VIOLATION_REFINEMENTS:
        return None

    prev_status = _text(previous_result.get("status"))
    prev_reason = _text(previous_result.get("reason_code"))

    # Determine what to adjust
    adjustments: list[str] = []
    new_strength = CLEAR_VIOLATION

    if prev_reason == "WEAK_MUTATION":
        adjustments.append("increase_mutation_strength")
        new_strength = COMPOUND_VIOLATION
    elif prev_reason == "PRECONDITION_NOT_MET":
        adjustments.append("fix_precondition_path")
    elif prev_reason == "OBSERVATION_INCOMPLETE":
        adjustments.append("add_missing_observers")
    elif prev_reason == "WRONG_SEQUENCE":
        adjustments.append("fix_operation_sequence")
    else:
        adjustments.append("increase_mutation_strength")
        new_strength = COMPOUND_VIOLATION

    # Regenerate mutations with increased strength
    new_mutations = generate_violation_mutation(
        violation_condition,
        runtime_values=runtime_values,
        strength=new_strength,
    )

    return {
        "refinement_round": refinement_round,
        "adjustments": adjustments,
        "new_strength": new_strength,
        "mutations": new_mutations,
        "previous_status": prev_status,
        "previous_reason": prev_reason,
    }


# ─── §20: Final Diagnostic Status ─────────────────────────────────────────────

def classify_final_status(
    *,
    oracle_result: str,
    violation_condition_attempted: bool,
    observer_complete: bool,
    precondition_reached: bool,
    mutation_applied: bool,
) -> str:
    """Classify the final diagnostic status for a target rule."""
    if oracle_result == "VIOLATION":
        return VIOLATION_TRIGGERED
    if not precondition_reached:
        return PRECONDITION_NOT_REACHED
    if not observer_complete:
        return ORACLE_INPUT_INCOMPLETE
    if not violation_condition_attempted or not mutation_applied:
        return VIOLATION_NOT_ACTIVATED
    # Oracle returned PASS with complete evidence and violation attempted
    return TRUE_PASS_CONFIRMED


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def run_violation_activation(
    targets: list[dict[str, Any]],
    *,
    available_operations: list[dict[str, Any]] | None = None,
    available_actors: list[dict[str, Any]] | None = None,
    runtime_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the full violation activation pipeline for all targets.

    Returns a summary with violation conditions, mutations, experiment pairs,
    and completeness checks for each target.
    """
    ops = _list(available_operations) or [{"id": "default"}]
    actors = _list(available_actors) or [{"id": "default_actor"}]
    rv = _dict(runtime_values)

    results: list[dict[str, Any]] = []
    stats = {
        "total_targets": len(targets),
        "violation_conditions_compiled": 0,
        "satisfiable": 0,
        "experiment_pairs_built": 0,
        "observer_complete": 0,
        "observer_incomplete": 0,
    }

    for target in targets:
        target_id = _text(target.get("target_id"))
        rule_id = _text(target.get("internal_rule_id"))
        expr = _dict(target.get("structured_expression"))

        # Step 1: Compile violation condition
        vc = compile_violation_condition(expr, rule_id=rule_id)
        stats["violation_conditions_compiled"] += 1

        # Step 2: Check satisfiability
        sat = check_violation_satisfiability(
            vc,
            available_operations=ops,
            available_actors=actors,
            available_observers=["http_response", "entity_state", "aggregate_scope"],
            fixture_capabilities=["create", "read", "transition"],
        )
        if sat.get("satisfiable"):
            stats["satisfiable"] += 1

        # Step 3: Generate mutations
        mutations = generate_violation_mutation(vc, runtime_values=rv)

        # Step 4: Check observer completeness
        required_obs = _list(vc.get("required_observations"))
        completeness = check_oracle_input_completeness(
            required_entities=[_text(mt.get("entity")) for mt in _list(vc.get("mutation_targets"))],
            observed_entities=[],  # Will be filled at runtime
            required_fields=required_obs,
            observed_fields=[],  # Will be filled at runtime
        )
        if completeness.get("complete"):
            stats["observer_complete"] += 1
        else:
            stats["observer_incomplete"] += 1

        # Step 5: Build experiment pair
        pair = build_violation_experiment_pair(
            target, vc, mutations,
            actor_ref=_text(actors[0].get("id")) if actors else "",
        )
        stats["experiment_pairs_built"] += 1

        results.append({
            "target_id": target_id,
            "rule_id": rule_id,
            "violation_condition": vc,
            "satisfiability": sat,
            "mutations": mutations,
            "observer_completeness": completeness,
            "experiment_pair": pair,
            "status": "PLANNED" if sat.get("satisfiable") else VIOLATION_CONDITION_UNSATISFIABLE,
        })

    return {
        "schema_version": "qualibug.violation-activation-result.v1",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stats": stats,
        "results": results,
    }
