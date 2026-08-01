"""Deep Experiment Planner — generates rich executable experiment plans for
deep business rules that the standard protocol compiler cannot handle.

Inserted after compile_experiments() and before plan_obligation_round().
Consumes compiled obligations + Behavior IR, produces multi-step sequences,
boundary/cumulative/temporal mutations, Control/Violation pairs.

Fully generic: selects mechanisms by rule_type/expression_type/state_graph.
No project-specific or benchmark-specific logic.
"""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any

from ai_test_asset_center.temporal_experiment_planning import (
    TemporalExperimentPlanner,
    TemporalRuleParser,
    BoundaryValueSolver,
    REFERENCE_RELATED_ENTITY_FIELD,
)
from ai_test_asset_center.actor_matrix_planning import (
    plan_actor_matrix,
    build_actor_relation_proof,
    RELATION_SAME_TENANT_OWNER,
    RELATION_SAME_TENANT_ALLOWED_ROLE,
    RELATION_CROSS_TENANT_SAME_ROLE,
)
from ai_test_asset_center.state_path_exploration import (
    explore_state_paths,
    plan_state_path_experiments,
    build_reachability_proof,
)
from ai_test_asset_center.cross_entity_chain_planning import (
    plan_cross_entity_experiments,
    build_chain_proof,
    build_cross_entity_planning_context,
    detect_cross_entity_requirement,
)
from ai_test_asset_center.idempotency_replay_planning import (
    plan_idempotency_replay,
    build_idempotency_proof,
)


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
    raw = "|".join(parts)
    return "deep_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


MECHANISM_BOUNDARY = "BOUNDARY"
MECHANISM_STATE_NEGATIVE = "STATE_NEGATIVE"
MECHANISM_IDEMPOTENCY = "IDEMPOTENCY"
MECHANISM_CUMULATIVE = "CUMULATIVE"
MECHANISM_CAUSAL_SIDE_EFFECT = "CAUSAL_SIDE_EFFECT"
MECHANISM_TEMPORAL = "TEMPORAL"
MECHANISM_ROLE_TENANT = "ROLE_TENANT"
MECHANISM_NEGATIVE_PRECONDITION = "NEGATIVE_PRECONDITION"
MECHANISM_UNIQUENESS_VIOLATION = "UNIQUENESS_VIOLATION"
MECHANISM_FIELD_INVARIANT_VIOLATION = "FIELD_INVARIANT_VIOLATION"
MECHANISM_PRECONDITION_VIOLATION = "PRECONDITION_VIOLATION"
MECHANISM_AUTHORIZATION_MATRIX = "AUTHORIZATION_MATRIX"
MECHANISM_TENANT_ISOLATION_MATRIX = "TENANT_ISOLATION_MATRIX"
MECHANISM_CROSS_ENTITY_PROCESS_GRAPH = "CROSS_ENTITY_PROCESS_GRAPH"

ALL_MECHANISMS = frozenset({
    MECHANISM_BOUNDARY,
    MECHANISM_STATE_NEGATIVE,
    MECHANISM_IDEMPOTENCY,
    MECHANISM_CUMULATIVE,
    MECHANISM_CAUSAL_SIDE_EFFECT,
    MECHANISM_TEMPORAL,
    MECHANISM_ROLE_TENANT,
    MECHANISM_NEGATIVE_PRECONDITION,
    MECHANISM_UNIQUENESS_VIOLATION,
    MECHANISM_FIELD_INVARIANT_VIOLATION,
    MECHANISM_PRECONDITION_VIOLATION,
    MECHANISM_AUTHORIZATION_MATRIX,
    MECHANISM_TENANT_ISOLATION_MATRIX,
    MECHANISM_CROSS_ENTITY_PROCESS_GRAPH,
})

_RULE_TYPE_MECHANISM: dict[str, str] = {
    "UNIQUENESS": MECHANISM_UNIQUENESS_VIOLATION,
    "DUPLICATE_ENTITY": MECHANISM_UNIQUENESS_VIOLATION,
    "UNIQUE_FIELD": MECHANISM_UNIQUENESS_VIOLATION,
    "COMPOSITE_UNIQUE": MECHANISM_UNIQUENESS_VIOLATION,
    "FIELD_INVARIANT": MECHANISM_FIELD_INVARIANT_VIOLATION,
    "NON_NEGATIVE": MECHANISM_FIELD_INVARIANT_VIOLATION,
    "NON_ZERO": MECHANISM_FIELD_INVARIANT_VIOLATION,
    "RANGE_CONSTRAINT": MECHANISM_FIELD_INVARIANT_VIOLATION,
    "ENUM_CONSTRAINT": MECHANISM_FIELD_INVARIANT_VIOLATION,
    "IMMUTABLE_FIELD": MECHANISM_FIELD_INVARIANT_VIOLATION,
    "PRECONDITION": MECHANISM_PRECONDITION_VIOLATION,
    "BUSINESS_PRECONDITION": MECHANISM_PRECONDITION_VIOLATION,
    "CAUSAL_PRECONDITION": MECHANISM_PRECONDITION_VIOLATION,
    "REQUIRES": MECHANISM_PRECONDITION_VIOLATION,
    "STATE_DEPENDENCY": MECHANISM_PRECONDITION_VIOLATION,
    "CROSS_ENTITY_PRECONDITION": MECHANISM_PRECONDITION_VIOLATION,
    "CROSS_ENTITY_OPERATION_CHAIN": MECHANISM_CROSS_ENTITY_PROCESS_GRAPH,
    "CROSS_OBJECT_PROCESS": MECHANISM_CROSS_ENTITY_PROCESS_GRAPH,
    "CROSS_SYSTEM_PROCESS": MECHANISM_CROSS_ENTITY_PROCESS_GRAPH,
    "AUTHORIZATION": MECHANISM_AUTHORIZATION_MATRIX,
    "ROLE_PERMISSION": MECHANISM_AUTHORIZATION_MATRIX,
    "ACTION_PERMISSION": MECHANISM_AUTHORIZATION_MATRIX,
    "RESOURCE_PERMISSION": MECHANISM_AUTHORIZATION_MATRIX,
    "TENANT_ISOLATION": MECHANISM_TENANT_ISOLATION_MATRIX,
    "CROSS_TENANT_ACCESS": MECHANISM_TENANT_ISOLATION_MATRIX,
    "TENANT_SCOPED_RESOURCE": MECHANISM_TENANT_ISOLATION_MATRIX,
    "LIMIT_CONSTRAINT": MECHANISM_BOUNDARY,
    "STATE_TRANSITION": MECHANISM_STATE_NEGATIVE,
    "IDEMPOTENCY": MECHANISM_IDEMPOTENCY,
    "TEMPORAL": MECHANISM_TEMPORAL,
    "DATA_VISIBILITY": MECHANISM_ROLE_TENANT,
    "BOUNDARY": MECHANISM_BOUNDARY,
    "CUMULATIVE": MECHANISM_CUMULATIVE,
}

_EXPRESSION_HINT_MECHANISM: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(unique|duplicate|same.*number|重复|唯一)"), MECHANISM_UNIQUENESS_VIOLATION),
    (re.compile(r"(?i)(negative|non.?negative|不得为负|非负|positive.*required|must.*positive)"), MECHANISM_FIELD_INVARIANT_VIOLATION),
    (re.compile(r"(?i)(limit|max|min|threshold|cap|quota|ceiling)"), MECHANISM_BOUNDARY),
    (re.compile(r"(?i)(idempoten)"), MECHANISM_IDEMPOTENCY),
    (re.compile(r"(?i)(before|after|date|deadline|expire|window|period|range)"), MECHANISM_TEMPORAL),
    (re.compile(r"(?i)(state|status|transition|phase|stage|lifecycle)"), MECHANISM_STATE_NEGATIVE),
    (re.compile(r"(?i)(precondition|prerequisite|require|must_exist|depend|must.*active|must.*approved|关联.*ACTIVE)"), MECHANISM_PRECONDITION_VIOLATION),
    (re.compile(r"(?i)(tenant|isolation|cross.?tenant|scope|跨租户)"), MECHANISM_TENANT_ISOLATION_MATRIX),
    (re.compile(r"(?i)(role|permission|authoriz|access|forbidden|只有.*可)"), MECHANISM_AUTHORIZATION_MATRIX),
    (re.compile(r"(?i)(cumul|sum|total|aggregate|accru)"), MECHANISM_CUMULATIVE),
]


def select_experiment_mechanism(
    rule_type: str,
    expression: Any,
    *,
    risk_family: str = "",
) -> str:
    """Select the experiment mechanism based on rule type and expression semantics."""
    rt = _text(rule_type).upper()
    if rt in _RULE_TYPE_MECHANISM:
        return _RULE_TYPE_MECHANISM[rt]

    expr_text = ""
    if isinstance(expression, dict):
        expr_text = " ".join(
            _text(v) for v in expression.values() if isinstance(v, str)
        )
    elif isinstance(expression, str):
        expr_text = expression

    for pattern, mechanism in _EXPRESSION_HINT_MECHANISM:
        if pattern.search(expr_text):
            return mechanism

    family_map = {
        "isolation": MECHANISM_ROLE_TENANT,
        "authorization": MECHANISM_ROLE_TENANT,
        "state": MECHANISM_STATE_NEGATIVE,
        "idempotency": MECHANISM_IDEMPOTENCY,
        "temporal": MECHANISM_TEMPORAL,
        "conservation": MECHANISM_CUMULATIVE,
        "visibility": MECHANISM_ROLE_TENANT,
        "cross_entity_chain": MECHANISM_CROSS_ENTITY_PROCESS_GRAPH,
        "cross_object": MECHANISM_CROSS_ENTITY_PROCESS_GRAPH,
        "cross_system": MECHANISM_CROSS_ENTITY_PROCESS_GRAPH,
    }
    if risk_family in family_map:
        return family_map[risk_family]

    return MECHANISM_CAUSAL_SIDE_EFFECT


def plan_state_path(
    behavior_ir: dict[str, Any],
    target_state: str,
    entity_ref: str = "",
) -> list[dict[str, Any]]:
    """Plan a sequence of operations to reach target_state from initial state."""
    ir = _dict(behavior_ir)
    states = _list(ir.get("states"))
    relations = _list(ir.get("relations"))
    operations = _list(ir.get("operations"))

    transitions: list[dict[str, Any]] = []
    ops_by_id = {
        _text(op.get("id")): op
        for op in operations
        if isinstance(op, dict)
    }

    for rel in relations:
        if not isinstance(rel, dict):
            continue
        if _text(rel.get("relation_type")) != "transitions":
            continue
        from_ref = _text(rel.get("from_ref"))
        to_ref = _text(rel.get("to_ref"))
        op_ref = _text(rel.get("operation_ref") or rel.get("via_operation"))
        transitions.append({
            "from_state": from_ref,
            "to_state": to_ref,
            "operation_ref": op_ref,
        })

    if not transitions:
        return []

    all_to_states = {t["to_state"] for t in transitions}
    all_from_states = {t["from_state"] for t in transitions}
    initial_states = all_from_states - all_to_states
    initial = next(iter(initial_states), "") if initial_states else ""

    for st in states:
        if isinstance(st, dict) and st.get("initial"):
            initial = _text(st.get("id") or st.get("state_id"))
            break

    if not initial or not target_state:
        return []

    from collections import deque
    queue: deque[list[dict[str, Any]]] = deque([[{"state": initial, "steps": []}]])
    visited: set[str] = {initial}
    max_depth = 12
    queue = deque()
    queue.append((initial, []))

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


def generate_boundary_mutation(
    field_spec: dict[str, Any],
    expression: Any,
) -> list[dict[str, Any]]:
    """Generate boundary value mutations: at-limit, over-limit, under-limit."""
    mutations: list[dict[str, Any]] = []
    expr = _dict(expression) if isinstance(expression, dict) else {}
    limit_value = None
    limit_field = _text(field_spec.get("field") or field_spec.get("name"))

    for key in ("limit", "max", "max_value", "threshold", "cap", "ceiling"):
        if key in expr:
            limit_value = _num(expr[key])
            break

    if limit_value is None:
        expr_str = _text(expr.get("expression") or expr.get("rule") or expr.get("description"))
        m = re.search(r"(?:max|limit|threshold|cap|<=?|≤)\s*(\d+(?:\.\d+)?)", expr_str, re.IGNORECASE)
        if m:
            limit_value = float(m.group(1))

    if limit_value is None or limit_value <= 0:
        return []

    step = max(1, limit_value * 0.01)
    mutations = [
        {
            "mutation_id": _stable_id("boundary", limit_field, "at_limit"),
            "field": limit_field,
            "value": limit_value,
            "mutation_type": "at_limit",
            "expected_outcome": "accepted",
        },
        {
            "mutation_id": _stable_id("boundary", limit_field, "over_limit"),
            "field": limit_field,
            "value": limit_value + step,
            "mutation_type": "over_limit",
            "expected_outcome": "rejected",
        },
        {
            "mutation_id": _stable_id("boundary", limit_field, "under_limit"),
            "field": limit_field,
            "value": max(0, limit_value - step),
            "mutation_type": "under_limit",
            "expected_outcome": "accepted",
        },
        {
            "mutation_id": _stable_id("boundary", limit_field, "overflow"),
            "field": limit_field,
            "value": limit_value * 2,
            "mutation_type": "overflow",
            "expected_outcome": "rejected",
        },
    ]
    return mutations


def generate_cumulative_mutation(
    entity_ref: str,
    limit_field: str,
    expression: Any,
) -> list[dict[str, Any]]:
    """Generate cumulative mutations: two operations where second causes overflow."""
    expr = _dict(expression) if isinstance(expression, dict) else {}
    limit_value = None

    for key in ("limit", "max", "max_value", "threshold", "total_limit"):
        if key in expr:
            limit_value = _num(expr[key])
            break

    if limit_value is None:
        expr_str = _text(expr.get("expression") or expr.get("rule") or expr.get("description"))
        m = re.search(r"(?:max|limit|total|sum|cumulative)\s*(?:of\s+)?(\d+(?:\.\d+)?)", expr_str, re.IGNORECASE)
        if m:
            limit_value = float(m.group(1))

    if limit_value is None or limit_value <= 0:
        return []

    safe_amount = limit_value * 0.6
    overflow_amount = limit_value * 0.6

    return [
        {
            "mutation_id": _stable_id("cumulative", entity_ref, limit_field, "step1"),
            "field": limit_field,
            "value": safe_amount,
            "mutation_type": "cumulative_step_1",
            "expected_outcome": "accepted",
            "step_index": 0,
        },
        {
            "mutation_id": _stable_id("cumulative", entity_ref, limit_field, "step2"),
            "field": limit_field,
            "value": overflow_amount,
            "mutation_type": "cumulative_step_2_overflow",
            "expected_outcome": "rejected",
            "step_index": 1,
        },
    ]


def generate_temporal_mutation(
    date_field: str,
    bounds: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate date boundary mutations: before/after/within range."""
    mutations: list[dict[str, Any]] = []
    start = _text(bounds.get("start") or bounds.get("min") or bounds.get("from"))
    end = _text(bounds.get("end") or bounds.get("max") or bounds.get("to"))

    if not start and not end:
        return []

    if start:
        mutations.append({
            "mutation_id": _stable_id("temporal", date_field, "before_start"),
            "field": date_field,
            "value": f"before:{start}",
            "mutation_type": "before_range_start",
            "expected_outcome": "rejected",
        })
        mutations.append({
            "mutation_id": _stable_id("temporal", date_field, "at_start"),
            "field": date_field,
            "value": start,
            "mutation_type": "at_range_start",
            "expected_outcome": "accepted",
        })

    if end:
        mutations.append({
            "mutation_id": _stable_id("temporal", date_field, "after_end"),
            "field": date_field,
            "value": f"after:{end}",
            "mutation_type": "after_range_end",
            "expected_outcome": "rejected",
        })
        mutations.append({
            "mutation_id": _stable_id("temporal", date_field, "at_end"),
            "field": date_field,
            "value": end,
            "mutation_type": "at_range_end",
            "expected_outcome": "accepted",
        })

    return mutations


def generate_cross_entity_temporal_mutation(
    internal_rule_id: str,
    expression: dict[str, Any],
    rule_statement: str,
    operation: dict[str, Any],
    actor_ref: str,
) -> list[dict[str, Any]]:
    """Generate cross-entity temporal boundary mutations."""
    planner = TemporalExperimentPlanner()
    target_op = _text(
        _dict(expression).get("target_operation")
        or _dict(expression).get("operation_id")
        or operation.get("id")
        or operation.get("operation_ref")
    )
    reference_value = _text(
        _dict(expression).get("reference_value")
        or _dict(expression).get("reference_date")
    )

    if not reference_value:
        return [{
            "mutation_id": _stable_id("temporal_cross", internal_rule_id, "needs_resolution"),
            "field": _text(_dict(expression).get("subject_field") or _dict(expression).get("date_field")),
            "value": "TEMPORAL_REFERENCE_NEEDS_RESOLUTION",
            "mutation_type": "cross_entity_temporal",
            "expected_outcome": "pending_reference_resolution",
            "temporal_planning_required": True,
            "internal_rule_id": internal_rule_id,
            "expression": expression,
            "rule_statement": rule_statement,
            "target_operation": target_op,
        }]

    result = planner.plan_experiments(
        internal_rule_id=internal_rule_id,
        expression=expression,
        rule_statement=rule_statement,
        reference_value=reference_value,
        target_operation=target_op,
        actor=actor_ref,
    )

    if not result.get("complete"):
        return []

    mutations: list[dict[str, Any]] = []
    solution = result.get("boundary_solution", {})
    subject_field = _text(_dict(expression).get("subject_field") or _dict(expression).get("date_field"))

    for case in solution.get("control_cases", []):
        mutations.append({
            "mutation_id": case.get("case_id", _stable_id("temporal_ctrl", internal_rule_id, case.get("subject_value", ""))),
            "field": subject_field,
            "value": case.get("subject_value"),
            "mutation_type": "temporal_control",
            "expected_outcome": "accepted",
            "case_type": "CONTROL",
            "distance_from_boundary": case.get("distance_from_boundary"),
            "temporal_plan_proof": True,
        })

    for case in solution.get("violation_cases", []):
        mutations.append({
            "mutation_id": case.get("case_id", _stable_id("temporal_viol", internal_rule_id, case.get("subject_value", ""))),
            "field": subject_field,
            "value": case.get("subject_value"),
            "mutation_type": "temporal_violation",
            "expected_outcome": "rejected",
            "case_type": "VIOLATION",
            "distance_from_boundary": case.get("distance_from_boundary"),
            "temporal_plan_proof": True,
        })

    return mutations


def generate_uniqueness_mutation(
    entity_ref: str,
    unique_fields: list[str],
    expression: Any,
) -> list[dict[str, Any]]:
    """Generate duplicate-entity mutations: submit same identifying fields twice."""
    expr = _dict(expression) if isinstance(expression, dict) else {}
    fields = unique_fields or []
    if not fields:
        field_text = _text(expr.get("unique_field") or expr.get("field") or expr.get("fields"))
        if field_text:
            fields = [f.strip() for f in field_text.split(",") if f.strip()]
    if not fields:
        fields = ["identifier"]

    return [
        {
            "mutation_id": _stable_id("uniq", entity_ref, "first_create"),
            "field": ",".join(fields),
            "value": "original_value",
            "mutation_type": "uniqueness_first_create",
            "expected_outcome": "accepted",
            "step_index": 0,
        },
        {
            "mutation_id": _stable_id("uniq", entity_ref, "duplicate"),
            "field": ",".join(fields),
            "value": "same_value_duplicate",
            "mutation_type": "uniqueness_duplicate_create",
            "expected_outcome": "rejected",
            "step_index": 1,
        },
    ]


def generate_field_invariant_mutation(
    field_spec: dict[str, Any],
    expression: Any,
) -> list[dict[str, Any]]:
    """Generate field invariant violations: negative, zero, wrong-type values."""
    expr = _dict(expression) if isinstance(expression, dict) else {}
    target_field = _text(
        field_spec.get("field") or field_spec.get("name")
        or expr.get("field") or expr.get("name")
    )
    if not target_field:
        target_field = "amount"

    return [
        {
            "mutation_id": _stable_id("finv", target_field, "valid"),
            "field": target_field,
            "value": 100,
            "mutation_type": "field_invariant_valid",
            "expected_outcome": "accepted",
        },
        {
            "mutation_id": _stable_id("finv", target_field, "negative"),
            "field": target_field,
            "value": -1,
            "mutation_type": "field_invariant_negative",
            "expected_outcome": "rejected",
        },
        {
            "mutation_id": _stable_id("finv", target_field, "zero"),
            "field": target_field,
            "value": 0,
            "mutation_type": "field_invariant_zero",
            "expected_outcome": "rejected",
        },
    ]


def generate_precondition_mutation(
    entity_ref: str,
    precondition_desc: str,
    expression: Any,
) -> list[dict[str, Any]]:
    """Generate precondition violation: attempt operation when precondition unmet."""
    expr = _dict(expression) if isinstance(expression, dict) else {}
    required_state = _text(
        expr.get("required_state") or expr.get("expected_state")
        or expr.get("precondition_state")
    )
    wrong_state = _text(
        expr.get("wrong_state") or expr.get("violation_state")
    )
    if not wrong_state:
        wrong_state = "DRAFT"

    return [
        {
            "mutation_id": _stable_id("precond", entity_ref, "satisfied"),
            "field": "precondition_state",
            "value": required_state or "QUALIFIED",
            "mutation_type": "precondition_satisfied",
            "expected_outcome": "accepted",
            "step_index": 0,
        },
        {
            "mutation_id": _stable_id("precond", entity_ref, "violated"),
            "field": "precondition_state",
            "value": wrong_state,
            "mutation_type": "precondition_violated",
            "expected_outcome": "rejected",
            "step_index": 1,
        },
    ]


def generate_authorization_mutation(
    operation_ref: str,
    expression: Any,
    actors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate authorization matrix: authorized role succeeds, others rejected."""
    expr = _dict(expression) if isinstance(expression, dict) else {}
    authorized_role = _text(
        expr.get("authorized_role") or expr.get("required_role")
        or expr.get("role")
    )

    mutations = [
        {
            "mutation_id": _stable_id("authz", operation_ref, "authorized"),
            "field": "actor_role",
            "value": authorized_role or "authorized_role",
            "mutation_type": "authorization_authorized",
            "expected_outcome": "accepted",
        },
    ]

    unauthorized_count = 0
    for actor in actors:
        if not isinstance(actor, dict):
            continue
        role = _text(actor.get("role"))
        actor_id = _text(actor.get("id"))
        if not role or not actor_id:
            continue
        if authorized_role and role.lower() == authorized_role.lower():
            continue
        unauthorized_count += 1
        mutations.append({
            "mutation_id": _stable_id("authz", operation_ref, f"unauth_{role}"),
            "field": "actor_role",
            "value": role,
            "actor_ref": actor_id,
            "mutation_type": f"authorization_unauthorized_{role.lower()}",
            "expected_outcome": "rejected",
        })
        if unauthorized_count >= 3:
            break

    if unauthorized_count == 0:
        mutations.append({
            "mutation_id": _stable_id("authz", operation_ref, "unauth_generic"),
            "field": "actor_role",
            "value": "unauthorized_role",
            "mutation_type": "authorization_unauthorized",
            "expected_outcome": "rejected",
        })

    return mutations


def generate_tenant_isolation_mutation(
    entity_ref: str,
    expression: Any,
    actors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate tenant isolation matrix: same-tenant access OK, cross-tenant rejected."""
    expr = _dict(expression) if isinstance(expression, dict) else {}
    tenants: dict[str, list[dict[str, Any]]] = {}
    for actor in actors:
        if not isinstance(actor, dict):
            continue
        tenant = _text(actor.get("tenant") or actor.get("tenant_id") or actor.get("org"))
        actor_id = _text(actor.get("id"))
        if tenant and actor_id:
            tenants.setdefault(tenant, []).append(actor)

    mutations = [
        {
            "mutation_id": _stable_id("tenant", entity_ref, "same_tenant"),
            "field": "tenant_access",
            "value": "same_tenant",
            "mutation_type": "tenant_same_access",
            "expected_outcome": "accepted",
        },
    ]

    if len(tenants) >= 2:
        tenant_list = list(tenants.keys())
        cross_tenant = tenant_list[1]
        cross_actor = tenants[cross_tenant][0]
        mutations.append({
            "mutation_id": _stable_id("tenant", entity_ref, f"cross_{cross_tenant}"),
            "field": "tenant_access",
            "value": f"cross_tenant:{cross_tenant}",
            "actor_ref": _text(cross_actor.get("id")),
            "mutation_type": "tenant_cross_access",
            "expected_outcome": "rejected",
        })
    else:
        mutations.append({
            "mutation_id": _stable_id("tenant", entity_ref, "cross_generic"),
            "field": "tenant_access",
            "value": "different_tenant",
            "mutation_type": "tenant_cross_access",
            "expected_outcome": "rejected",
        })

    return mutations


def build_multi_step_sequence(
    fixture_plan: list[dict[str, Any]],
    target_operation: dict[str, Any],
    *,
    max_steps: int = 8,
) -> list[dict[str, Any]]:
    """Build a multi-step execution sequence: fixtures → target operation."""
    steps: list[dict[str, Any]] = []
    for i, fixture in enumerate(fixture_plan[:max_steps - 1]):
        steps.append({
            "step_id": f"fixture_{i + 1}",
            "operation_ref": _text(fixture.get("operation_ref")),
            "intent": _text(fixture.get("intent") or "setup_precondition"),
            "input_sources": fixture.get("input_sources") or [],
            "expected_state": fixture.get("expected_state") or "",
            "body": fixture.get("body"),
            "actor_ref": _text(fixture.get("actor_ref")),
        })

    steps.append({
        "step_id": f"target_{len(steps) + 1}",
        "operation_ref": _text(target_operation.get("id") or target_operation.get("operation_ref")),
        "intent": "execute_target_operation",
        "input_sources": [f"fixture_{i + 1}.response" for i in range(len(steps))],
        "expected_state": _text(target_operation.get("expected_state")),
        "body": target_operation.get("body"),
        "actor_ref": _text(target_operation.get("actor_ref")),
    })
    return steps


def build_control_violation_pair(
    rule: dict[str, Any],
    mechanism: str,
    *,
    operation: dict[str, Any],
    actor_ref: str = "",
    mutations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a Control + Violation experiment pair."""
    rule_id = _text(rule.get("rule_id") or rule.get("invariant_ref") or rule.get("obligation_id"))
    op_ref = _text(operation.get("id") or operation.get("operation_ref"))
    body = operation.get("request_example") or operation.get("body") or {}

    control_plan = [{
        "step_id": "control_1",
        "actor_ref": actor_ref,
        "operation_ref": op_ref,
        "intent": "valid_source_control",
        "protocol_step": "positive_control",
        "body": deepcopy(body) if isinstance(body, dict) else {},
        "expected_status_class": 2,
    }]

    violation_steps: list[dict[str, Any]] = []
    muts = mutations or []
    if muts:
        for i, mut in enumerate(muts):
            mutated_body = deepcopy(body) if isinstance(body, dict) else {}
            field = _text(mut.get("field"))
            if field and isinstance(mutated_body, dict) and field != "actor_relation":
                mutated_body[field] = mut.get("value")
            step_actor = _text(mut.get("actor_ref")) or actor_ref
            violation_steps.append({
                "step_id": f"violation_{i + 1}",
                "actor_ref": step_actor,
                "operation_ref": op_ref,
                "intent": f"{mechanism.lower()}_mutation",
                "protocol_step": "deep_mutation",
                "body": mutated_body,
                "mutation": mut,
                "expected_outcome": _text(mut.get("expected_outcome")),
            })
    else:
        violation_steps.append({
            "step_id": "violation_1",
            "actor_ref": actor_ref,
            "operation_ref": op_ref,
            "intent": f"{mechanism.lower()}_violation",
            "protocol_step": "deep_mutation",
            "body": deepcopy(body) if isinstance(body, dict) else {},
        })

    return {
        "experiment_id": _stable_id("pair", rule_id, mechanism, op_ref),
        "mechanism": mechanism,
        "rule_id": rule_id,
        "control_plan": control_plan,
        "treatment_plan": violation_steps,
        "assertion": {
            "kind": "control_violation_contrast",
            "control_must_succeed": True,
            "violation_expected_reject": True,
        },
        "observers": [
            {"observer_id": "http_response"},
            {"observer_id": "entity_state"},
        ],
    }


def deduplicate_experiments(
    experiments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove semantically duplicate experiments."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []

    for exp in experiments:
        if not isinstance(exp, dict):
            continue
        rule_id = _text(exp.get("rule_id"))
        mechanism = _text(exp.get("mechanism"))

        op_seq = []
        for step in _list(exp.get("treatment_plan")):
            if isinstance(step, dict):
                op_seq.append(_text(step.get("operation_ref")))
        op_sig = ",".join(op_seq)

        mutation_dim = ""
        target_field = ""
        actor_relation = ""
        tenant_relation = ""
        ownership_relation = ""
        for step in _list(exp.get("treatment_plan")):
            if isinstance(step, dict):
                mut = _dict(step.get("mutation"))
                if mut:
                    mutation_dim = _text(mut.get("mutation_type"))
                    target_field = _text(mut.get("field"))
                    actor_relation = _text(mut.get("value")) if target_field == "actor_relation" else ""
                    actor_ref = _text(mut.get("actor_ref"))
                    control_ref = _text(mut.get("control_actor_ref"))
                    if actor_ref:
                        tenant_relation = _text(mut.get("dimension_under_test"))
                        ownership_relation = control_ref
                    break

        sig = f"{rule_id}|{mechanism}|{op_sig}|{mutation_dim}|{target_field}|{actor_relation}|{tenant_relation}|{ownership_relation}"
        if sig in seen:
            continue
        seen.add(sig)
        unique.append(exp)

    return unique


def plan_deep_experiments(
    obligations: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
    *,
    budget: int = 100,
) -> dict[str, Any]:
    """Generate rich executable experiment plans for deep business rules."""
    ir = _dict(behavior_ir)
    cross_entity_context = build_cross_entity_planning_context(ir)
    operations = _list(ir.get("operations"))
    actors = _list(ir.get("actors"))
    states = _list(ir.get("states"))
    relations = _list(ir.get("relations"))
    invariants = _list(ir.get("invariants"))

    ops_by_id = dict(_dict(cross_entity_context.get("operations")))
    actors_by_id = {_text(a.get("id")): a for a in actors if isinstance(a, dict)}
    invariants_by_id = {_text(inv.get("id")): inv for inv in invariants if isinstance(inv, dict)}

    default_actor_ref = ""
    _admin_actor_ref = ""
    for a in actors:
        if not isinstance(a, dict) or not _text(a.get("id")):
            continue
        _role = _text(a.get("role"))
        _secret = _text(a.get("credential_secret_ref") or a.get("secret_ref"))
        if not _role or _role.startswith("{") or not _secret or _secret.startswith("{") or ":{" in _secret:
            continue
        if not default_actor_ref:
            default_actor_ref = _text(a.get("id"))
        if _role.lower() in {"admin", "administrator", "superuser", "root"}:
            _admin_actor_ref = _text(a.get("id"))
    if _admin_actor_ref:
        default_actor_ref = _admin_actor_ref

    deep_experiments: list[dict[str, Any]] = []
    by_obligation: dict[str, dict[str, Any]] = {}
    mechanism_counts: dict[str, int] = {}
    _actor_matrix_meta: dict[str, dict[str, Any]] = {}
    _process_graph_meta: dict[str, dict[str, Any]] = {}
    planning_blockers: list[dict[str, Any]] = []
    skipped = 0

    for obl in obligations:
        if not isinstance(obl, dict):
            continue
        if len(deep_experiments) >= budget:
            skipped += 1
            continue

        oid = _text(obl.get("obligation_id"))
        family = _text(obl.get("risk_family"))
        prop = _dict(obl.get("property"))
        expr = prop.get("expression")
        invariant_ref = _text(prop.get("invariant_ref"))
        operation_ref = _text(prop.get("operation_ref"))
        actor_ref = _text(prop.get("actor_ref")) or default_actor_ref

        inv = _dict(invariants_by_id.get(invariant_ref))
        rule_type = _text(
            inv.get("rule_type")
            or inv.get("invariant_type")
            or _dict(expr).get("rule_type")
            or _dict(expr).get("type")
        )

        existing = _dict(experiments_by_obligation.get(oid))
        existing_mechanism = _text(existing.get("mechanism"))
        if existing_mechanism in ALL_MECHANISMS:
            target_mechanism = _RULE_TYPE_MECHANISM.get(_text(rule_type).upper(), "")
            if not target_mechanism or existing_mechanism == target_mechanism:
                continue

        mechanism = select_experiment_mechanism(
            rule_type, expr, risk_family=family,
        )

        mutations: list[dict[str, Any]] = []
        multi_step_fixture: list[dict[str, Any]] = []
        graph_backed_plan: dict[str, Any] | None = None

        _xce_detection = detect_cross_entity_requirement(
            obl, ir, context=cross_entity_context
        )
        if _xce_detection.get("is_cross_entity"):
            _xce_result = plan_cross_entity_experiments(
                obl,
                ir,
                budget=8,
                context=cross_entity_context,
            )
            if (
                _xce_result.get("status") == "EXPLORED"
                and _xce_result.get("experiments")
            ):
                mechanism = MECHANISM_CROSS_ENTITY_PROCESS_GRAPH
                graph_backed_plan = _dict(_xce_result.get("experiments")[0])
                mutations = [{
                    "mutation_id": _text(graph_backed_plan.get("experiment_id")),
                    "field": "execution_graph",
                    "value": _text(
                        _dict(graph_backed_plan.get("execution_graph")).get(
                            "execution_graph_id"
                        )
                    ),
                    "mutation_type": "source_declared_process_graph",
                    "expected_outcome": _text(
                        graph_backed_plan.get("expected_outcome")
                    ),
                    "cross_entity_chain": True,
                    "cross_system": bool(graph_backed_plan.get("cross_system")),
                }]
                _process_graph_meta[oid] = {
                    "status": "CROSS_ENTITY_PROCESS_GRAPH_COMPILED",
                    "chain_type": _xce_result.get("chain_type"),
                    "chain_proof": _xce_result.get("chain_proof"),
                    "dependency_proof": _xce_result.get("dependency_proof"),
                    "execution_graph_id": _text(
                        _dict(_xce_result.get("execution_graph")).get(
                            "execution_graph_id"
                        )
                    ),
                    "detection_signals": _xce_result.get("detection_signals"),
                }
            else:
                planning_blockers.append({
                    "obligation_id": oid,
                    "mechanism": MECHANISM_CROSS_ENTITY_PROCESS_GRAPH,
                    "reason": _text(_xce_result.get("reason")),
                    "blockers": _list(_xce_result.get("blockers")),
                    "detection_signals": _list(
                        _xce_detection.get("signals")
                    ),
                })
                skipped += 1
                continue

        if graph_backed_plan is not None and not operation_ref:
            operation_ref = _text(graph_backed_plan.get("target_operation"))
        operation = _dict(ops_by_id.get(operation_ref))
        if not operation:
            for ref in _list(obl.get("required_operations")):
                op = _dict(ops_by_id.get(_text(ref)))
                if op:
                    operation = op
                    operation_ref = _text(ref)
                    break
        if not operation and graph_backed_plan is None:
            skipped += 1
            continue

        if graph_backed_plan is not None:
            pass
        elif mechanism == MECHANISM_BOUNDARY:
            field_spec = _dict(expr).get("field_spec") or _dict(expr)
            mutations = generate_boundary_mutation(field_spec, expr)

        elif mechanism == MECHANISM_CUMULATIVE:
            entity_ref = _text(inv.get("entity_ref") or prop.get("entity_ref"))
            limit_field = _text(
                _dict(expr).get("field")
                or _dict(expr).get("limit_field")
                or _dict(expr).get("amount_field")
            )
            mutations = generate_cumulative_mutation(entity_ref, limit_field, expr)

        elif mechanism == MECHANISM_TEMPORAL:
            subject_entity = _text(_dict(expr).get("subject_entity") or _dict(expr).get("left", {}).get("entity"))
            reference_entity = _text(_dict(expr).get("reference_entity") or _dict(expr).get("right", {}).get("entity"))
            has_cross_entity = bool(subject_entity and reference_entity and subject_entity != reference_entity)
            has_operator = bool(_text(_dict(expr).get("operator")))

            if has_cross_entity and has_operator:
                mutations = generate_cross_entity_temporal_mutation(
                    internal_rule_id=invariant_ref or oid,
                    expression=expr,
                    rule_statement=_text(inv.get("description") or prop.get("description") or ""),
                    operation=operation,
                    actor_ref=actor_ref,
                )
            else:
                date_field = _text(
                    _dict(expr).get("date_field")
                    or _dict(expr).get("field")
                    or _dict(expr).get("start_date")
                )
                bounds = _dict(expr).get("bounds") or _dict(expr)
                mutations = generate_temporal_mutation(date_field, bounds)

        elif mechanism == MECHANISM_STATE_NEGATIVE:
            _sp_result = explore_state_paths(obl, ir, budget=8)
            if _sp_result.get("status") == "EXPLORED" and _sp_result.get("experiments"):
                _sp_experiments = _sp_result.get("experiments", [])
                _sp_state_rule = _sp_result.get("state_rule", {})
                for _sp_exp in _sp_experiments:
                    _forbidden_state = _text(_sp_exp.get("forbidden_source_state"))
                    _fixture_steps = _list(_sp_exp.get("fixture_steps"))
                    multi_step_fixture = [
                        {
                            "operation_ref": step.get("operation_ref"),
                            "intent": step.get("intent", f"advance_to_{step.get('expected_state', 'unknown')}"),
                            "expected_state": step.get("expected_state"),
                            "actor_ref": actor_ref,
                        }
                        for step in _fixture_steps
                    ]
                    mutations.append({
                        "mutation_id": _stable_id("state_path", oid, _forbidden_state),
                        "field": _text(_sp_state_rule.get("state_field", "status")),
                        "value": _forbidden_state,
                        "mutation_type": "forbidden_source_state",
                        "expected_outcome": "rejected",
                        "state_path_id": _sp_exp.get("state_path_id"),
                        "state_path_proof": _sp_result.get("proofs", [{}])[0].get("proof_id") if _sp_result.get("proofs") else None,
                    })
                _actor_matrix_meta[oid] = {
                    "status": "STATE_PATH_EXPLORED",
                    "state_rule": _sp_state_rule,
                    "state_paths": _sp_result.get("state_paths", []),
                    "proofs": _sp_result.get("proofs", []),
                    "reachability_proof": build_reachability_proof(_sp_state_rule, _sp_result.get("state_paths", [])),
                }
            else:
                target_state = _text(
                    _dict(expr).get("target_state")
                    or _dict(expr).get("state")
                    or _dict(expr).get("to_state")
                )
                if target_state:
                    path = plan_state_path(ir, target_state)
                    if path:
                        multi_step_fixture = [
                            {
                                "operation_ref": step["operation_ref"],
                                "intent": f"advance_to_{step['to_state']}",
                                "expected_state": step["to_state"],
                                "actor_ref": actor_ref,
                            }
                            for step in path
                        ]
                mutations = [{
                    "mutation_id": _stable_id("state_neg", oid, "wrong_state"),
                    "field": "state",
                    "value": "forbidden_source_state",
                    "mutation_type": "negative_state_transition",
                    "expected_outcome": "rejected",
                }]

        elif mechanism == MECHANISM_IDEMPOTENCY:
            _idem_result = plan_idempotency_replay(obl, ir, budget=8)
            if _idem_result.get("status") == "REPLAY_PLANNED" and _idem_result.get("experiments"):
                _idem_experiments = _idem_result.get("experiments", [])
                _idem_sequence = _list(_idem_result.get("replay_sequence"))
                if _idem_sequence:
                    multi_step_fixture = [
                        {
                            "operation_ref": _text(step.get("operation_ref")),
                            "intent": _text(step.get("intent")),
                            "expected_status": step.get("expected_status"),
                            "role": _text(step.get("role")),
                            "actor_ref": actor_ref,
                        }
                        for step in _idem_sequence
                    ]
                for _idem_exp in _idem_experiments:
                    mutations.append({
                        "mutation_id": _text(_idem_exp.get("experiment_id")),
                        "field": "idempotency_replay",
                        "value": _text(_idem_exp.get("replay_variant")),
                        "mutation_type": f"idempotency_{_idem_exp.get('experiment_type', 'replay').lower()}",
                        "expected_outcome": _text(_idem_exp.get("expected_outcome") or _idem_exp.get("expected_replay")),
                        "idempotency_replay": True,
                        "replay_proof": _idem_result.get("replay_proof", {}).get("proof_id"),
                        "side_effect_proof": _idem_result.get("side_effect_proof", {}).get("proof_id"),
                        "oracle": _idem_exp.get("oracle"),
                    })
                _actor_matrix_meta[oid] = {
                    "status": "IDEMPOTENCY_REPLAY_PLANNED",
                    "replay_proof": _idem_result.get("replay_proof"),
                    "side_effect_proof": _idem_result.get("side_effect_proof"),
                    "operation_identity": _idem_result.get("operation_identity"),
                    "idempotency_key": _idem_result.get("idempotency_key"),
                    "request_fingerprint": _idem_result.get("request_fingerprint"),
                    "resource_scope": _idem_result.get("resource_scope"),
                    "replay_variants": _idem_result.get("replay_variants"),
                }
            else:
                mutations = [
                    {
                        "mutation_id": _stable_id("idem", oid, "first"),
                        "field": "",
                        "value": "first_execution",
                        "mutation_type": "idempotency_first",
                        "expected_outcome": "accepted",
                        "step_index": 0,
                    },
                    {
                        "mutation_id": _stable_id("idem", oid, "repeat"),
                        "field": "",
                        "value": "repeat_execution",
                        "mutation_type": "idempotency_repeat",
                        "expected_outcome": "no_additional_side_effect",
                        "step_index": 1,
                    },
                ]

        elif mechanism == MECHANISM_ROLE_TENANT:
            mutations = [
                {
                    "mutation_id": _stable_id("role", oid, "authorized"),
                    "field": "actor",
                    "value": "authorized_role",
                    "mutation_type": "role_authorized",
                    "expected_outcome": "accepted",
                },
                {
                    "mutation_id": _stable_id("role", oid, "unauthorized"),
                    "field": "actor",
                    "value": "unauthorized_role",
                    "mutation_type": "role_unauthorized",
                    "expected_outcome": "rejected",
                },
                {
                    "mutation_id": _stable_id("role", oid, "cross_tenant"),
                    "field": "tenant",
                    "value": "different_tenant",
                    "mutation_type": "cross_tenant_access",
                    "expected_outcome": "rejected",
                },
            ]

        elif mechanism == MECHANISM_NEGATIVE_PRECONDITION:
            preconditions = _list(_dict(expr).get("preconditions") or _dict(inv.get("causal_chain")).get("preconditions"))
            if preconditions:
                for i, precond in enumerate(preconditions[:3]):
                    mutations.append({
                        "mutation_id": _stable_id("negpre", oid, str(i)),
                        "field": _text(precond.get("field") or precond.get("description") or f"precondition_{i}"),
                        "value": "violated",
                        "mutation_type": f"negative_precondition_{i}",
                        "expected_outcome": "rejected",
                    })
            else:
                mutations = [{
                    "mutation_id": _stable_id("negpre", oid, "missing"),
                    "field": "precondition",
                    "value": "not_satisfied",
                    "mutation_type": "negative_precondition",
                    "expected_outcome": "rejected",
                }]

        elif mechanism == MECHANISM_UNIQUENESS_VIOLATION:
            entity_ref = _text(inv.get("entity_ref") or prop.get("entity_ref"))
            unique_fields = _list(_dict(expr).get("unique_fields") or _dict(expr).get("fields"))
            if isinstance(unique_fields, str):
                unique_fields = [unique_fields]
            unique_fields = [_text(f) for f in unique_fields if _text(f)]
            mutations = generate_uniqueness_mutation(entity_ref, unique_fields, expr)

        elif mechanism == MECHANISM_FIELD_INVARIANT_VIOLATION:
            field_spec = _dict(expr).get("field_spec") or _dict(expr)
            mutations = generate_field_invariant_mutation(field_spec, expr)

        elif mechanism == MECHANISM_PRECONDITION_VIOLATION:
            entity_ref = _text(inv.get("entity_ref") or prop.get("entity_ref"))
            precond_desc = _text(
                _dict(expr).get("description")
                or _dict(expr).get("precondition")
                or inv.get("description")
            )
            mutations = generate_precondition_mutation(
                entity_ref, precond_desc, expr
            )

        elif mechanism == MECHANISM_AUTHORIZATION_MATRIX:
            _am_result = plan_actor_matrix(
                expr, inv, ir, operation,
                max_candidates=8,
            )
            if _am_result.get("status") == "COMPLETE":
                for _pair in _am_result.get("discriminating_pairs", []):
                    _ctrl = _dict(_pair.get("control_actor"))
                    _viol = _dict(_pair.get("violation_actor"))
                    _dim = _text(_pair.get("dimension_under_test"))
                    mutations.append({
                        "mutation_id": _stable_id("authz_mx", operation_ref, _dim, _text(_viol.get("actor_id"))),
                        "field": "actor_relation",
                        "value": _text(_viol.get("relation_type")),
                        "actor_ref": _text(_viol.get("actor_id")),
                        "control_actor_ref": _text(_ctrl.get("actor_id")),
                        "mutation_type": f"authorization_matrix_{_dim}",
                        "expected_outcome": "rejected",
                        "dimension_under_test": _dim,
                        "discrimination_quality": _text(_pair.get("discrimination_quality")),
                        "actor_relation_proof": _pair.get("pair_id"),
                    })
                _actor_matrix_meta[oid] = _am_result
            else:
                mutations = generate_authorization_mutation(operation_ref, expr, actors)

        elif mechanism == MECHANISM_TENANT_ISOLATION_MATRIX:
            entity_ref = _text(inv.get("entity_ref") or prop.get("entity_ref"))
            _am_result = plan_actor_matrix(
                expr, inv, ir, operation,
                max_candidates=8,
            )
            if _am_result.get("status") == "COMPLETE":
                for _pair in _am_result.get("discriminating_pairs", []):
                    _ctrl = _dict(_pair.get("control_actor"))
                    _viol = _dict(_pair.get("violation_actor"))
                    _dim = _text(_pair.get("dimension_under_test"))
                    mutations.append({
                        "mutation_id": _stable_id("tenant_mx", entity_ref, _dim, _text(_viol.get("actor_id"))),
                        "field": "actor_relation",
                        "value": _text(_viol.get("relation_type")),
                        "actor_ref": _text(_viol.get("actor_id")),
                        "control_actor_ref": _text(_ctrl.get("actor_id")),
                        "mutation_type": f"tenant_isolation_matrix_{_dim}",
                        "expected_outcome": "rejected",
                        "dimension_under_test": _dim,
                        "discrimination_quality": _text(_pair.get("discrimination_quality")),
                        "actor_relation_proof": _pair.get("pair_id"),
                    })
                _actor_matrix_meta[oid] = _am_result
            else:
                mutations = generate_tenant_isolation_mutation(entity_ref, expr, actors)

        treatment_plan: list[dict[str, Any]] = []
        if graph_backed_plan is not None:
            treatment_plan = deepcopy(
                _list(graph_backed_plan.get("treatment_plan"))
            )
        elif multi_step_fixture:
            sequence = build_multi_step_sequence(
                multi_step_fixture,
                {"id": operation_ref, "actor_ref": actor_ref},
            )
            treatment_plan = sequence
        else:
            _effective_actor_ref = actor_ref
            if mutations and _text(mutations[0].get("control_actor_ref")):
                _effective_actor_ref = _text(mutations[0].get("control_actor_ref"))
            pair = build_control_violation_pair(
                {"rule_id": invariant_ref or oid, "invariant_ref": invariant_ref, "obligation_id": oid},
                mechanism,
                operation=operation,
                actor_ref=_effective_actor_ref,
                mutations=mutations,
            )
            treatment_plan = pair.get("treatment_plan") or []

        _am_meta = _actor_matrix_meta.get(oid)
        _graph_meta = _process_graph_meta.get(oid)
        _is_actor_matrix = mechanism in {
            MECHANISM_AUTHORIZATION_MATRIX,
            MECHANISM_TENANT_ISOLATION_MATRIX,
        }
        _actor_matrix_expanded = bool(_am_meta and _is_actor_matrix)
        _actor_proofs = (
            _list(_dict(_am_meta).get("proofs"))
            if _actor_matrix_expanded
            else []
        )

        _observers = [
            {"observer_id": "http_response"},
            {"observer_id": "entity_state"},
            {"observer_id": "business_effect"},
        ]
        if _actor_matrix_expanded:
            _observers.append({"observer_id": "rejection_side_effect", "config": {
                "check_root_unchanged": True,
                "check_related_unchanged": True,
                "check_no_async_side_effect": True,
                "separate_authn_vs_authz": True,
            }})

        _graph_control_plan = (
            deepcopy(_list(graph_backed_plan.get("control_plan")))
            if graph_backed_plan is not None
            else None
        )
        _graph_assertion = (
            deepcopy(_dict(graph_backed_plan.get("assertion")))
            if graph_backed_plan is not None
            else None
        )
        _graph_observers = (
            deepcopy(_list(graph_backed_plan.get("observers")))
            if graph_backed_plan is not None
            else None
        )

        deep_exp = {
            "experiment_id": _stable_id("deep", oid, mechanism),
            "obligation_id": oid,
            "mechanism": mechanism,
            "rule_id": invariant_ref or oid,
            "risk_family": family,
            "compile_status": "COMPILED",
            "compile_receipt": {
                "status": "COMPILED",
                "reason_code": "DEEP_PLANNER_COMPILED",
                "mechanism": mechanism,
                "actor_matrix_expanded": _actor_matrix_expanded,
                "process_graph_compiled": bool(_graph_meta),
            },
            "control_plan": _graph_control_plan if _graph_control_plan is not None else [{
                "step_id": "control_1",
                "actor_ref": _text(mutations[0].get("control_actor_ref")) if mutations and mutations[0].get("control_actor_ref") else actor_ref,
                "operation_ref": operation_ref,
                "intent": "valid_source_control",
                "protocol_step": "positive_control",
                "body": operation.get("request_example") or {},
                "expected_status_class": 2,
            }],
            "treatment_plan": treatment_plan,
            "cleanup_plan": (
                deepcopy(_list(graph_backed_plan.get("cleanup_plan")))
                if graph_backed_plan is not None
                else []
            ),
            "execution_graph": (
                deepcopy(_dict(graph_backed_plan.get("execution_graph")))
                if graph_backed_plan is not None
                else {}
            ),
            "observers": _graph_observers if _graph_observers is not None else _observers,
            "assertion": _graph_assertion if _graph_assertion is not None else {
                "kind": "deep_mechanism_contrast",
                "mechanism": mechanism,
                "control_must_succeed": True,
            },
            "source_refs": obl.get("source_refs") or [],
            "deep_planner": True,
            "actor_relation_proofs": _actor_proofs,
            "actor_matrix_result": (
                _am_meta.get("status") if _actor_matrix_expanded else None
            ),
            "process_graph_result": (
                _graph_meta.get("status") if _graph_meta else None
            ),
            "chain_proof": (
                deepcopy(_dict(_graph_meta.get("chain_proof")))
                if _graph_meta
                else {}
            ),
            "dependency_proof": (
                deepcopy(_dict(_graph_meta.get("dependency_proof")))
                if _graph_meta
                else {}
            ),
        }

        deep_experiments.append(deep_exp)
        by_obligation[oid] = deep_exp
        mechanism_counts[mechanism] = mechanism_counts.get(mechanism, 0) + 1

    deep_experiments = deduplicate_experiments(deep_experiments)

    return {
        "schema_version": "qualibug.deep-experiment-plan.v1",
        "deep_experiments": deep_experiments,
        "by_obligation": by_obligation,
        "mechanism_counts": mechanism_counts,
        "planned_count": len(deep_experiments),
        "skipped_count": skipped,
        "planning_blockers": planning_blockers,
        "budget": budget,
    }
