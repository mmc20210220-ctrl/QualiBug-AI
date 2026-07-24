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
    raw = "|".join(parts)
    return "deep_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ─── Experiment Mechanisms ─────────────────────────────────────────────────────

MECHANISM_BOUNDARY = "BOUNDARY"
MECHANISM_STATE_NEGATIVE = "STATE_NEGATIVE"
MECHANISM_IDEMPOTENCY = "IDEMPOTENCY"
MECHANISM_CUMULATIVE = "CUMULATIVE"
MECHANISM_CAUSAL_SIDE_EFFECT = "CAUSAL_SIDE_EFFECT"
MECHANISM_TEMPORAL = "TEMPORAL"
MECHANISM_ROLE_TENANT = "ROLE_TENANT"
MECHANISM_NEGATIVE_PRECONDITION = "NEGATIVE_PRECONDITION"
# New mechanisms for missing experiment planning (SPEC: Missing Mechanism Planning)
MECHANISM_UNIQUENESS_VIOLATION = "UNIQUENESS_VIOLATION"
MECHANISM_FIELD_INVARIANT_VIOLATION = "FIELD_INVARIANT_VIOLATION"
MECHANISM_PRECONDITION_VIOLATION = "PRECONDITION_VIOLATION"
MECHANISM_AUTHORIZATION_MATRIX = "AUTHORIZATION_MATRIX"
MECHANISM_TENANT_ISOLATION_MATRIX = "TENANT_ISOLATION_MATRIX"

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
})

# Rule type → mechanism mapping (generic, based on expression semantics)
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

# Expression type hints → mechanism
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


# ─── Mechanism Selection ───────────────────────────────────────────────────────

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

    # Try expression text hints
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

    # Fallback by risk_family
    family_map = {
        "isolation": MECHANISM_ROLE_TENANT,
        "authorization": MECHANISM_ROLE_TENANT,
        "state": MECHANISM_STATE_NEGATIVE,
        "idempotency": MECHANISM_IDEMPOTENCY,
        "temporal": MECHANISM_TEMPORAL,
        "conservation": MECHANISM_CUMULATIVE,
        "visibility": MECHANISM_ROLE_TENANT,
    }
    if risk_family in family_map:
        return family_map[risk_family]

    return MECHANISM_CAUSAL_SIDE_EFFECT


# ─── State Path Planning ───────────────────────────────────────────────────────

def plan_state_path(
    behavior_ir: dict[str, Any],
    target_state: str,
    entity_ref: str = "",
) -> list[dict[str, Any]]:
    """Plan a sequence of operations to reach target_state from initial state.

    Uses the Behavior IR state graph. Returns list of steps:
    [{"operation_ref", "from_state", "to_state", "step_index"}]
    """
    ir = _dict(behavior_ir)
    states = _list(ir.get("states"))
    relations = _list(ir.get("relations"))
    operations = _list(ir.get("operations"))

    # Build transition graph from relations
    transitions: list[dict[str, Any]] = []
    ops_by_id = {_text(op.get("id")): op for op in operations if isinstance(op, dict)}

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

    # Find initial state (state with no incoming transitions, or marked initial)
    all_to_states = {t["to_state"] for t in transitions}
    all_from_states = {t["from_state"] for t in transitions}
    initial_states = all_from_states - all_to_states
    initial = next(iter(initial_states), "") if initial_states else ""

    # Also check states list for initial marker
    for st in states:
        if isinstance(st, dict) and st.get("initial"):
            initial = _text(st.get("id") or st.get("state_id"))
            break

    if not initial or not target_state:
        return []

    # BFS to find shortest path
    from collections import deque
    queue: deque[list[dict[str, Any]]] = deque([[{"state": initial, "steps": []}]])
    visited: set[str] = {initial}
    max_depth = 12  # Hard limit per SPEC §10

    # Flatten queue structure
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


# ─── Boundary Mutation ─────────────────────────────────────────────────────────

def generate_boundary_mutation(
    field_spec: dict[str, Any],
    expression: Any,
) -> list[dict[str, Any]]:
    """Generate boundary value mutations: at-limit, over-limit, under-limit.

    Returns list of mutation descriptors:
    [{"mutation_id", "field", "value", "mutation_type", "expected_outcome"}]
    """
    mutations: list[dict[str, Any]] = []
    expr = _dict(expression) if isinstance(expression, dict) else {}

    # Extract limit from expression
    limit_value = None
    limit_field = _text(field_spec.get("field") or field_spec.get("name"))

    for key in ("limit", "max", "max_value", "threshold", "cap", "ceiling"):
        if key in expr:
            limit_value = _num(expr[key])
            break

    if limit_value is None:
        # Try to extract from expression text
        expr_str = _text(expr.get("expression") or expr.get("rule") or expr.get("description"))
        m = re.search(r"(?:max|limit|threshold|cap|<=?|≤)\s*(\d+(?:\.\d+)?)", expr_str, re.IGNORECASE)
        if m:
            limit_value = float(m.group(1))

    if limit_value is None or limit_value <= 0:
        return []

    step = max(1, limit_value * 0.01)  # 1% step
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


# ─── Cumulative Mutation ───────────────────────────────────────────────────────

def generate_cumulative_mutation(
    entity_ref: str,
    limit_field: str,
    expression: Any,
) -> list[dict[str, Any]]:
    """Generate cumulative mutations: two operations where second causes overflow.

    Returns mutation descriptors for a two-step cumulative test.
    """
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

    # First operation: 60% of limit (safe)
    # Second operation: 60% of limit (causes cumulative overflow at 120%)
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


# ─── Temporal Mutation ─────────────────────────────────────────────────────────

def generate_temporal_mutation(
    date_field: str,
    bounds: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate date boundary mutations: before/after/within range.

    Returns mutation descriptors for temporal boundary testing.
    """
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
    """Generate cross-entity temporal boundary mutations.

    Uses temporal_experiment_planning module to generate Control/Violation
    pairs for rules like "field A must not be later than field B" where
    A and B are from different entities.

    Returns mutation descriptors compatible with build_control_violation_pair.
    """
    planner = TemporalExperimentPlanner()

    # Extract target operation ID
    target_op = _text(
        _dict(expression).get("target_operation")
        or _dict(expression).get("operation_id")
        or operation.get("id")
        or operation.get("operation_ref")
    )

    # For cross-entity temporal rules, we need a reference value.
    # In production, this comes from fixture receipt or observer.
    # For planning, we use a placeholder that will be resolved at execution time.
    reference_value = _text(
        _dict(expression).get("reference_value")
        or _dict(expression).get("reference_date")
    )

    # If no reference value provided, we cannot generate concrete boundary values
    # Return empty to indicate this needs runtime resolution
    if not reference_value:
        # Return a marker mutation indicating temporal planning is needed
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

    # Plan experiments using the temporal module
    result = planner.plan_experiments(
        internal_rule_id=internal_rule_id,
        expression=expression,
        rule_statement=rule_statement,
        reference_value=reference_value,
        target_operation=target_op,
        actor=actor_ref,
    )

    if not result.get("complete"):
        # Planning blocked - return empty
        return []

    # Convert boundary solution to mutation format
    mutations: list[dict[str, Any]] = []
    solution = result.get("boundary_solution", {})
    subject_field = _text(_dict(expression).get("subject_field") or _dict(expression).get("date_field"))

    # Control cases
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

    # Violation cases
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


# ─── Uniqueness Violation Mutation ─────────────────────────────────────────────

def generate_uniqueness_mutation(
    entity_ref: str,
    unique_fields: list[str],
    expression: Any,
) -> list[dict[str, Any]]:
    """Generate duplicate-entity mutations: submit same identifying fields twice.

    Control: first creation succeeds.
    Violation: second creation with same unique fields must be rejected.
    """
    expr = _dict(expression) if isinstance(expression, dict) else {}
    fields = unique_fields or []
    if not fields:
        # Try to extract from expression
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


# ─── Field Invariant Violation Mutation ────────────────────────────────────────

def generate_field_invariant_mutation(
    field_spec: dict[str, Any],
    expression: Any,
) -> list[dict[str, Any]]:
    """Generate field invariant violations: negative, zero, wrong-type values.

    For constraints like 'amount must not be negative' or 'field must be positive'.
    """
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


# ─── Precondition Violation Mutation ──────────────────────────────────────────

def generate_precondition_mutation(
    entity_ref: str,
    precondition_desc: str,
    expression: Any,
) -> list[dict[str, Any]]:
    """Generate precondition violation: attempt operation when precondition unmet.

    Control: operation with precondition satisfied → accepted.
    Violation: operation with precondition NOT satisfied → rejected.
    """
    expr = _dict(expression) if isinstance(expression, dict) else {}
    required_state = _text(
        expr.get("required_state") or expr.get("expected_state")
        or expr.get("precondition_state")
    )
    wrong_state = _text(
        expr.get("wrong_state") or expr.get("violation_state")
    )
    if not wrong_state:
        wrong_state = "DRAFT"  # Generic non-qualifying state

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


# ─── Authorization Matrix Mutation ────────────────────────────────────────────

def generate_authorization_mutation(
    operation_ref: str,
    expression: Any,
    actors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate authorization matrix: authorized role succeeds, others rejected.

    Uses real actor references from Behavior IR when available.
    """
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

    # Generate unauthorized attempts for each non-authorized actor
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

    # Fallback if no actors available
    if unauthorized_count == 0:
        mutations.append({
            "mutation_id": _stable_id("authz", operation_ref, "unauth_generic"),
            "field": "actor_role",
            "value": "unauthorized_role",
            "mutation_type": "authorization_unauthorized",
            "expected_outcome": "rejected",
        })

    return mutations


# ─── Tenant Isolation Matrix Mutation ─────────────────────────────────────────

def generate_tenant_isolation_mutation(
    entity_ref: str,
    expression: Any,
    actors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate tenant isolation matrix: same-tenant access OK, cross-tenant rejected.

    Uses real actor references from different tenants when available.
    """
    expr = _dict(expression) if isinstance(expression, dict) else {}

    # Group actors by tenant
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
        # Use actors from different tenants for cross-tenant test
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


# ─── Multi-Step Sequence Builder ──────────────────────────────────────────────

def build_multi_step_sequence(
    fixture_plan: list[dict[str, Any]],
    target_operation: dict[str, Any],
    *,
    max_steps: int = 8,
) -> list[dict[str, Any]]:
    """Build a multi-step execution sequence: fixtures → target operation.

    Each step: {"step_id", "operation_ref", "intent", "input_sources", "expected_state"}
    """
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

    # Final step: the target operation
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


# ─── Control/Violation Pair Builder ───────────────────────────────────────────

def build_control_violation_pair(
    rule: dict[str, Any],
    mechanism: str,
    *,
    operation: dict[str, Any],
    actor_ref: str = "",
    mutations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a Control + Violation experiment pair.

    Control: proves fixture and operation work (valid input → success).
    Violation: changes ONE critical variable (invalid input → expected failure).
    Control failure blocks Violation finding.
    """
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
            # Use per-mutation actor_ref for actor matrix experiments (SPEC §16 fixture isolation)
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


# ─── Semantic Deduplication ────────────────────────────────────────────────────

def deduplicate_experiments(
    experiments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove semantically duplicate experiments.

    Signature: (rule_id, operation_sequence, mutation_dimension, target_field,
                actor_relation, tenant_relation, ownership_relation)
    Same mechanism + same rule + same actor relation dimensions = duplicate.
    Preserves tenant, owner, and assignment differences (SPEC §23).
    """
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []

    for exp in experiments:
        if not isinstance(exp, dict):
            continue
        rule_id = _text(exp.get("rule_id"))
        mechanism = _text(exp.get("mechanism"))

        # Build operation sequence signature
        op_seq = []
        for step in _list(exp.get("treatment_plan")):
            if isinstance(step, dict):
                op_seq.append(_text(step.get("operation_ref")))
        op_sig = ",".join(op_seq)

        # Mutation dimension
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
                    # Include actor relation dimensions in signature (SPEC §23)
                    actor_relation = _text(mut.get("value")) if target_field == "actor_relation" else ""
                    actor_ref = _text(mut.get("actor_ref"))
                    control_ref = _text(mut.get("control_actor_ref"))
                    if actor_ref:
                        tenant_relation = _text(mut.get("dimension_under_test"))
                        ownership_relation = control_ref  # Distinguishes owner vs non-owner
                    break

        sig = f"{rule_id}|{mechanism}|{op_sig}|{mutation_dim}|{target_field}|{actor_relation}|{tenant_relation}|{ownership_relation}"
        if sig in seen:
            continue
        seen.add(sig)
        unique.append(exp)

    return unique


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def plan_deep_experiments(
    obligations: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
    *,
    budget: int = 100,
) -> dict[str, Any]:
    """Generate rich executable experiment plans for deep business rules.

    Consumes compiled obligations and Behavior IR. For obligations that are
    blocked or shallow (single-step only), generates deep experiments using
    appropriate mechanisms (boundary, state, temporal, cumulative, etc.).

    Returns:
        {
            "deep_experiments": [...],
            "by_obligation": {oid: experiment},
            "mechanism_counts": {mechanism: count},
            "planned_count": int,
            "skipped_count": int,
        }
    """
    ir = _dict(behavior_ir)
    operations = _list(ir.get("operations"))
    actors = _list(ir.get("actors"))
    states = _list(ir.get("states"))
    relations = _list(ir.get("relations"))
    invariants = _list(ir.get("invariants"))

    ops_by_id = {_text(op.get("id")): op for op in operations if isinstance(op, dict)}
    actors_by_id = {_text(a.get("id")): a for a in actors if isinstance(a, dict)}
    invariants_by_id = {_text(inv.get("id")): inv for inv in invariants if isinstance(inv, dict)}

    # Find executable actor: prefer actors with real credentials (non-template)
    default_actor_ref = ""
    _admin_actor_ref = ""
    for a in actors:
        if not isinstance(a, dict) or not _text(a.get("id")):
            continue
        _role = _text(a.get("role"))
        _secret = _text(a.get("credential_secret_ref") or a.get("secret_ref"))
        # Skip template actors (role/secret contains { or is empty)
        if not _role or _role.startswith("{") or not _secret or _secret.startswith("{") or ":{" in _secret:
            continue
        if not default_actor_ref:
            default_actor_ref = _text(a.get("id"))
        if _role.lower() in {"admin", "administrator", "superuser", "root"}:
            _admin_actor_ref = _text(a.get("id"))
    # Prefer admin if available
    if _admin_actor_ref:
        default_actor_ref = _admin_actor_ref

    deep_experiments: list[dict[str, Any]] = []
    by_obligation: dict[str, dict[str, Any]] = {}
    mechanism_counts: dict[str, int] = {}
    _actor_matrix_meta: dict[str, dict[str, Any]] = {}
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

        # Determine rule type from invariant or expression (moved before skip check)
        inv = _dict(invariants_by_id.get(invariant_ref))
        rule_type = _text(
            inv.get("rule_type")
            or inv.get("invariant_type")
            or _dict(expr).get("rule_type")
            or _dict(expr).get("type")
        )

        # Check if already has a correct deep experiment for this rule type
        existing = _dict(experiments_by_obligation.get(oid))
        existing_mechanism = _text(existing.get("mechanism"))
        if existing_mechanism in ALL_MECHANISMS:
            # Only skip if existing mechanism matches what this rule type needs
            target_mechanism = _RULE_TYPE_MECHANISM.get(_text(rule_type).upper(), "")
            if not target_mechanism or existing_mechanism == target_mechanism:
                continue  # Correctly planned already
            # Otherwise: wrong mechanism was used, allow re-planning

        # Select mechanism
        mechanism = select_experiment_mechanism(
            rule_type, expr, risk_family=family,
        )

        # Resolve operation
        operation = _dict(ops_by_id.get(operation_ref))
        if not operation:
            # Try to find from required_operations
            for ref in _list(obl.get("required_operations")):
                op = _dict(ops_by_id.get(_text(ref)))
                if op:
                    operation = op
                    operation_ref = _text(ref)
                    break

        if not operation:
            skipped += 1
            continue

        # Generate mutations based on mechanism
        mutations: list[dict[str, Any]] = []
        multi_step_fixture: list[dict[str, Any]] = []

        if mechanism == MECHANISM_BOUNDARY:
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
            # Check for cross-entity temporal structure
            subject_entity = _text(_dict(expr).get("subject_entity") or _dict(expr).get("left", {}).get("entity"))
            reference_entity = _text(_dict(expr).get("reference_entity") or _dict(expr).get("right", {}).get("entity"))
            has_cross_entity = bool(subject_entity and reference_entity and subject_entity != reference_entity)
            has_operator = bool(_text(_dict(expr).get("operator")))

            if has_cross_entity and has_operator:
                # Use new temporal experiment planning for cross-entity rules
                mutations = generate_cross_entity_temporal_mutation(
                    internal_rule_id=invariant_ref or oid,
                    expression=expr,
                    rule_statement=_text(inv.get("description") or prop.get("description") or ""),
                    operation=operation,
                    actor_ref=actor_ref,
                )
            else:
                # Fall back to simple range boundary for single-entity rules
                date_field = _text(
                    _dict(expr).get("date_field")
                    or _dict(expr).get("field")
                    or _dict(expr).get("start_date")
                )
                bounds = _dict(expr).get("bounds") or _dict(expr)
                mutations = generate_temporal_mutation(date_field, bounds)

        elif mechanism == MECHANISM_STATE_NEGATIVE:
            # STATE_PATH_NOT_EXPLORED fix: Use state_path_exploration for forbidden source states
            _sp_result = explore_state_paths(obl, ir, budget=8)
            if _sp_result.get("status") == "EXPLORED" and _sp_result.get("experiments"):
                # Use state path exploration results
                _sp_experiments = _sp_result.get("experiments", [])
                _sp_state_rule = _sp_result.get("state_rule", {})
                for _sp_exp in _sp_experiments:
                    _forbidden_state = _text(_sp_exp.get("forbidden_source_state"))
                    _fixture_steps = _list(_sp_exp.get("fixture_steps"))
                    # Build multi-step fixture from state path
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
                # Store state path metadata
                _actor_matrix_meta[oid] = {
                    "status": "STATE_PATH_EXPLORED",
                    "state_rule": _sp_state_rule,
                    "state_paths": _sp_result.get("state_paths", []),
                    "proofs": _sp_result.get("proofs", []),
                    "reachability_proof": build_reachability_proof(_sp_state_rule, _sp_result.get("state_paths", [])),
                }
            else:
                # Fallback to legacy behavior
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
                # Negative: attempt operation from wrong state
                mutations = [{
                    "mutation_id": _stable_id("state_neg", oid, "wrong_state"),
                    "field": "state",
                    "value": "forbidden_source_state",
                    "mutation_type": "negative_state_transition",
                    "expected_outcome": "rejected",
                }]

        elif mechanism == MECHANISM_IDEMPOTENCY:
            # Repeat same operation, compare side effects
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
            # Different role/tenant combinations
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
            # Break one precondition at a time
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
                _dict(expr).get("description") or _dict(expr).get("precondition")
                or inv.get("description")
            )
            mutations = generate_precondition_mutation(entity_ref, precond_desc, expr)

        elif mechanism == MECHANISM_AUTHORIZATION_MATRIX:
            # Use actor matrix planning for discriminating actor combinations
            _am_result = plan_actor_matrix(
                expr, inv, ir, operation,
                max_candidates=8,
            )
            if _am_result.get("status") == "COMPLETE":
                # Generate mutations from discriminating pairs
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
                # Store matrix metadata on obligation for downstream use
                _actor_matrix_meta[oid] = _am_result
            else:
                # Fallback to legacy fixed-role mutations
                mutations = generate_authorization_mutation(operation_ref, expr, actors)

        elif mechanism == MECHANISM_TENANT_ISOLATION_MATRIX:
            # Use actor matrix planning for tenant isolation
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
                # Fallback to legacy tenant mutations
                mutations = generate_tenant_isolation_mutation(entity_ref, expr, actors)

        # Build multi-step sequence if fixtures needed
        treatment_plan: list[dict[str, Any]] = []
        if multi_step_fixture:
            sequence = build_multi_step_sequence(
                multi_step_fixture,
                {"id": operation_ref, "actor_ref": actor_ref},
            )
            treatment_plan = sequence
        else:
            # Build Control/Violation pair
            # For actor matrix experiments, use control_actor_ref from mutation
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

        # Assemble deep experiment
        # Include actor matrix metadata if available
        _am_meta = _actor_matrix_meta.get(oid)
        _actor_proofs = _list(_dict(_am_meta).get("proofs")) if _am_meta else []

        # Enhanced observers for actor matrix experiments (SPEC §19, §20)
        _observers = [
            {"observer_id": "http_response"},
            {"observer_id": "entity_state"},
            {"observer_id": "business_effect"},
        ]
        if _am_meta:
            # Actor matrix experiments need side-effect verification on rejection
            _observers.append({"observer_id": "rejection_side_effect", "config": {
                "check_root_unchanged": True,
                "check_related_unchanged": True,
                "check_no_async_side_effect": True,
                "separate_authn_vs_authz": True,
            }})

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
                "actor_matrix_expanded": bool(_am_meta),
            },
            "control_plan": [{
                "step_id": "control_1",
                "actor_ref": _text(mutations[0].get("control_actor_ref")) if mutations and mutations[0].get("control_actor_ref") else actor_ref,
                "operation_ref": operation_ref,
                "intent": "valid_source_control",
                "protocol_step": "positive_control",
                "body": operation.get("request_example") or {},
                "expected_status_class": 2,
            }],
            "treatment_plan": treatment_plan,
            "observers": _observers,
            "assertion": {
                "kind": "deep_mechanism_contrast",
                "mechanism": mechanism,
                "control_must_succeed": True,
            },
            "source_refs": obl.get("source_refs") or [],
            "deep_planner": True,
            "actor_relation_proofs": _actor_proofs,
            "actor_matrix_result": _am_meta.get("status") if _am_meta else None,
        }

        deep_experiments.append(deep_exp)
        by_obligation[oid] = deep_exp
        mechanism_counts[mechanism] = mechanism_counts.get(mechanism, 0) + 1

    # Deduplicate
    deep_experiments = deduplicate_experiments(deep_experiments)

    return {
        "schema_version": "qualibug.deep-experiment-plan.v1",
        "deep_experiments": deep_experiments,
        "by_obligation": by_obligation,
        "mechanism_counts": mechanism_counts,
        "planned_count": len(deep_experiments),
        "skipped_count": skipped,
        "budget": budget,
    }
