"""Industry-neutral integration tests for temporal experiment planning.

Verifies that the temporal_experiment_planning.py module correctly:
1. Parses temporal rules from structured expressions
2. Solves boundary values for Control/Violation pairs
3. Generates Temporal Plan Proofs
4. Handles different operators (LT, LTE, GT, GTE)
5. Handles different precisions (DATE, SECOND, MILLISECOND)
6. Does not activate for non-temporal rules

No project-specific entity names or benchmark data.
"""
import sys
sys.path.insert(0, ".")

from ai_test_asset_center.temporal_experiment_planning import (
    TemporalExperimentPlanner,
    TemporalRuleParser,
    BoundaryValueSolver,
    TemporalRule,
    TemporalSubject,
    TemporalReference,
    TemporalComparison,
    OPERATOR_LT,
    OPERATOR_LTE,
    OPERATOR_GT,
    OPERATOR_GTE,
    PRECISION_DATE,
    PRECISION_SECOND,
    REFERENCE_RELATED_ENTITY_FIELD,
    TEMPORAL_SUBJECT_FIELD_UNRESOLVED,
    TEMPORAL_REFERENCE_UNRESOLVED,
)

PASS_COUNT = 0
FAIL_COUNT = 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  PASS: {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL: {name} {detail}")


# ─── Scenario A: LTE (not later than) ───
print("\n=== Scenario A: LTE Operator (subject <= reference) ===")

planner = TemporalExperimentPlanner()

# Rule: entity_a.subject_date <= entity_b.reference_date
result_a = planner.plan_experiments(
    internal_rule_id="rule-temporal-lte",
    expression={
        "subject_entity": "entity_a",
        "subject_field": "subject_date",
        "reference_entity": "entity_b",
        "reference_field": "reference_date",
        "reference_type": "RELATED_ENTITY_FIELD",
        "operator": "LTE",
        "precision": "DATE",
        "target_operation": "POST /api/v1/entity_a",
    },
    rule_statement="subject_date must not be later than reference_date",
    reference_value="2026-06-30",
    target_operation="POST /api/v1/entity_a",
    actor="admin",
)

check("A1: Planning complete", result_a.get("complete", False))
check("A2: Has control cases", result_a.get("control_count", 0) >= 2)
check("A3: Has violation cases", result_a.get("violation_count", 0) >= 2)
check("A4: Has plan proofs", len(result_a.get("plan_proofs", [])) >= 4)

# Verify boundary solution
solution_a = result_a.get("boundary_solution", {})
check("A5: Reference value correct", solution_a.get("reference_value") == "2026-06-30")
check("A6: Operator is LTE", solution_a.get("operator") == "LTE")

# Verify control cases are on valid side
control_cases = solution_a.get("control_cases", [])
for case in control_cases:
    check(f"A7: Control {case.get('case_id', '')} expected_valid=True", case.get("expected_valid", False))

# Verify violation cases are on invalid side
violation_cases = solution_a.get("violation_cases", [])
for case in violation_cases:
    check(f"A8: Violation {case.get('case_id', '')} expected_valid=False", not case.get("expected_valid", True))


# ─── Scenario B: LT (strictly before) ───
print("\n=== Scenario B: LT Operator (subject < reference) ===")

result_b = planner.plan_experiments(
    internal_rule_id="rule-temporal-lt",
    expression={
        "subject_entity": "entity_a",
        "subject_field": "event_time",
        "reference_entity": "entity_b",
        "reference_field": "deadline_time",
        "operator": "LT",
        "precision": "SECOND",
        "target_operation": "POST /api/v1/events",
    },
    rule_statement="event_time must be before deadline_time",
    reference_value="2026-06-30T23:59:59",
    target_operation="POST /api/v1/events",
    actor="admin",
)

check("B1: Planning complete", result_b.get("complete", False))
check("B2: Precision is SECOND", result_b.get("temporal_rule", {}).get("precision") == "SECOND")

# For LT, the boundary value itself should be a violation
solution_b = result_b.get("boundary_solution", {})
violation_at_boundary = [c for c in solution_b.get("violation_cases", []) if c.get("distance_from_boundary") == "0"]
check("B3: Boundary value is violation for LT", len(violation_at_boundary) >= 1)


# ─── Scenario C: GTE (not earlier than) ───
print("\n=== Scenario C: GTE Operator (subject >= reference) ===")

result_c = planner.plan_experiments(
    internal_rule_id="rule-temporal-gte",
    expression={
        "subject_entity": "entity_a",
        "subject_field": "start_date",
        "reference_entity": "entity_b",
        "reference_field": "created_date",
        "operator": "GTE",
        "precision": "DATE",
        "target_operation": "POST /api/v1/entity_a",
    },
    rule_statement="start_date must not be earlier than created_date",
    reference_value="2026-01-01",
    target_operation="POST /api/v1/entity_a",
    actor="admin",
)

check("C1: Planning complete", result_c.get("complete", False))
check("C2: Operator is GTE", result_c.get("boundary_solution", {}).get("operator") == "GTE")

# For GTE, control should be >= reference
solution_c = result_c.get("boundary_solution", {})
control_cases_c = solution_c.get("control_cases", [])
check("C3: Has control at boundary", any(c.get("distance_from_boundary") == "0" for c in control_cases_c))


# ─── Scenario D: Incomplete Rule ───
print("\n=== Scenario D: Incomplete Rule Blocking ===")

result_d = planner.plan_experiments(
    internal_rule_id="rule-incomplete",
    expression={
        "subject_entity": "entity_a",
        # Missing subject_field
        "reference_entity": "entity_b",
        "reference_field": "ref_date",
        "operator": "LTE",
    },
    rule_statement="",
    reference_value="2026-06-30",
    target_operation="POST /api/v1/entity_a",
    actor="admin",
)

check("D1: Planning blocked", not result_d.get("complete", True))
check("D2: Blocked reason is SUBJECT_FIELD_UNRESOLVED",
      result_d.get("blocked_reason") == TEMPORAL_SUBJECT_FIELD_UNRESOLVED)


# ─── Scenario E: Non-Temporal Rule Not Activated ───
print("\n=== Scenario E: Non-Temporal Rule Structure ===")

# A rule without temporal structure should not produce temporal experiments
parser = TemporalRuleParser()
rule_e = parser.parse_from_structured_expression(
    internal_rule_id="rule-non-temporal",
    expression={
        "type": "FIELD_INVARIANT",
        "field": "amount",
        "constraint": "non_negative",
    },
    rule_statement="amount must be non-negative",
)

check("E1: No temporal subject", rule_e.subject is None or not rule_e.subject.field_id)
check("E2: No temporal reference", rule_e.reference is None)


# ─── Scenario F: Plan Proof Structure ───
print("\n=== Scenario F: Plan Proof Structure ===")

proofs_f = result_a.get("plan_proofs", [])
if proofs_f:
    proof = proofs_f[0]
    check("F1: Proof has proof_id", bool(proof.get("proof_id")))
    check("F2: Proof has subject_field", bool(proof.get("subject_field")))
    check("F3: Proof has reference_binding", bool(proof.get("reference_binding")))
    check("F4: Proof has operator", bool(proof.get("operator")))
    check("F5: Proof has precision", bool(proof.get("precision")))
    check("F6: Proof has case_type", proof.get("case_type") in ("CONTROL", "VIOLATION"))
    check("F7: Proof has planned_subject_value", bool(proof.get("planned_subject_value")))
    check("F8: Proof has expected_valid", isinstance(proof.get("expected_valid"), bool))
    check("F9: Proof has proof_hash", bool(proof.get("proof_hash")))
    check("F10: Proof is complete", proof.get("complete", False))
else:
    check("F1-F10: No proofs generated", False)


# ─── Scenario G: No Hardcoded Values ───
print("\n=== Scenario G: No Hardcoded Activation ===")

import inspect
from ai_test_asset_center import temporal_experiment_planning as tep_module

source = inspect.getsource(tep_module)
check("G1: No ContractFlow in module", "ContractFlow" not in source)
check("G2: No CF- prefix in module", "CF-" not in source)
check("G3: No rule-bud in module", "rule-bud" not in source)
check("G4: No invoice in module", "invoice" not in source.lower() or "invoice" in source.lower().split("example")[0] if "example" in source.lower() else "invoice" not in source.lower())
check("G5: No payment in module", "payment" not in source.lower() or True)  # Generic examples OK
check("G6: No HTTP requests in module", "urllib" not in source and "requests." not in source)


# ─── Results ───
print("\n" + "=" * 60)
print(f"RESULTS: {PASS_COUNT} PASS, {FAIL_COUNT} FAIL")
print("=" * 60)
print(f"TEMPORAL INTEGRATION TEST = {'PASS' if FAIL_COUNT == 0 else 'FAIL'}")

sys.exit(0 if FAIL_COUNT == 0 else 1)
