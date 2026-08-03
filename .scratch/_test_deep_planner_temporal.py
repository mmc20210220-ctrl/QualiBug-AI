"""Test deep_experiment_planner integration with temporal_experiment_planning.

Verifies that cross-entity temporal rules are correctly handled by the
deep experiment planner using the new temporal planning module.
"""
import sys
sys.path.insert(0, ".")

from ai_test_asset_center.deep_experiment_planner import (
    generate_cross_entity_temporal_mutation,
    generate_temporal_mutation,
    MECHANISM_TEMPORAL,
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


# ─── Test 1: Cross-entity temporal mutation with reference value ───
print("\n=== Test 1: Cross-Entity Temporal Mutation (with reference) ===")

mutations = generate_cross_entity_temporal_mutation(
    internal_rule_id="rule-temporal-cross-1",
    expression={
        "subject_entity": "entity_a",
        "subject_field": "subject_date",
        "reference_entity": "entity_b",
        "reference_field": "reference_date",
        "reference_type": "RELATED_ENTITY_FIELD",
        "operator": "LTE",
        "precision": "DATE",
        "target_operation": "POST /api/v1/entity_a",
        "reference_value": "2026-06-30",
    },
    rule_statement="subject_date must not be later than reference_date",
    operation={"id": "POST /api/v1/entity_a", "request_example": {"subject_date": "2026-06-15"}},
    actor_ref="admin",
)

check("T1.1: Has mutations", len(mutations) >= 4)
control_muts = [m for m in mutations if m.get("case_type") == "CONTROL"]
violation_muts = [m for m in mutations if m.get("case_type") == "VIOLATION"]
check("T1.2: Has control mutations", len(control_muts) >= 2)
check("T1.3: Has violation mutations", len(violation_muts) >= 2)
check("T1.4: Control expected accepted", all(m.get("expected_outcome") == "accepted" for m in control_muts))
check("T1.5: Violation expected rejected", all(m.get("expected_outcome") == "rejected" for m in violation_muts))
check("T1.6: Has temporal_plan_proof", all(m.get("temporal_plan_proof") for m in mutations))


# ─── Test 2: Cross-entity temporal without reference value ───
print("\n=== Test 2: Cross-Entity Temporal (needs resolution) ===")

mutations2 = generate_cross_entity_temporal_mutation(
    internal_rule_id="rule-temporal-cross-2",
    expression={
        "subject_entity": "entity_a",
        "subject_field": "event_time",
        "reference_entity": "entity_b",
        "reference_field": "deadline_time",
        "operator": "LT",
        "precision": "SECOND",
        "target_operation": "POST /api/v1/events",
        # No reference_value - needs runtime resolution
    },
    rule_statement="event_time must be before deadline_time",
    operation={"id": "POST /api/v1/events"},
    actor_ref="admin",
)

check("T2.1: Has marker mutation", len(mutations2) == 1)
check("T2.2: Marker indicates resolution needed", mutations2[0].get("temporal_planning_required") == True)
check("T2.3: Marker has expression", bool(mutations2[0].get("expression")))


# ─── Test 3: Simple range temporal (fallback) ───
print("\n=== Test 3: Simple Range Temporal (fallback) ===")

mutations3 = generate_temporal_mutation(
    date_field="due_date",
    bounds={"start": "2026-01-01", "end": "2026-12-31"},
)

check("T3.1: Has mutations", len(mutations3) >= 2)
check("T3.2: Has before_start", any(m.get("mutation_type") == "before_range_start" for m in mutations3))
check("T3.3: Has after_end", any(m.get("mutation_type") == "after_range_end" for m in mutations3))


# ─── Test 4: No hardcoded values ───
print("\n=== Test 4: No Hardcoded Values ===")

import inspect
from ai_test_asset_center import deep_experiment_planner as dep_module

source = inspect.getsource(dep_module)
check("T4.1: No ContractFlow", "ContractFlow" not in source)
check("T4.2: No CF- prefix", "CF-" not in source)
check("T4.3: No invoice in temporal func", "invoice" not in inspect.getsource(generate_cross_entity_temporal_mutation).lower())


# ─── Results ───
print("\n" + "=" * 60)
print(f"RESULTS: {PASS_COUNT} PASS, {FAIL_COUNT} FAIL")
print("=" * 60)
print(f"DEEP PLANNER INTEGRATION TEST = {'PASS' if FAIL_COUNT == 0 else 'FAIL'}")

sys.exit(0 if FAIL_COUNT == 0 else 1)
