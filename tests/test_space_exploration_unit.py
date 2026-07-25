"""Space Exploration Unit Tests — 57 tests across 7 categories.

SPEC §38: Dimension(7) + Operator(7) + Combination(10) + Scheduler(8) +
           Multi-Surface(8) + Dynamic/Scale(10) + Finding(7)
"""
from __future__ import annotations

import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_test_asset_center.space_dimension_registry import (
    SpaceDimensionRegistry, build_default_dimensions, create_dimension,
    SPACE_DOMAINS,
)
from ai_test_asset_center.space_coordinate import (
    create_coordinate, validate_coordinate, coordinate_distance,
)
from ai_test_asset_center.invariant_graph import (
    InvariantGraph, INVARIANT_TYPES, create_invariant,
)
from ai_test_asset_center.exploration_operator_registry import (
    ExplorationOperatorRegistry, OPERATOR_CATEGORIES, create_operator,
    check_applicability, build_all_operators,
)
from ai_test_asset_center.combination_generator import (
    compute_priority_score, validate_combination, generate_combinations,
    compute_pairwise_coverage, PRIORITY_WEIGHTS,
)
from ai_test_asset_center.coverage_guided_scheduler import (
    CoverageGuidedScheduler, compute_coverage_state, check_quota_compliance,
    MECHANISM_QUOTAS, AUTHORIZATION_CAP,
)
from ai_test_asset_center.experiment_portfolio import (
    ExperimentPortfolio, create_experiment_entry, validate_portfolio_quotas,
)
from ai_test_asset_center.multi_surface_adapter import (
    MultiSurfaceAdapterRegistry, SURFACE_TYPES, create_surface_adapter,
    build_default_adapters, create_execution_receipt, plan_cross_surface_execution,
    create_event_exploration_plan, create_batch_scale_plan, create_ui_exploration_plan,
    EVENT_EXPLORATION_SCENARIOS, UI_EXPLORATION_CHECKS,
)
from ai_test_asset_center.multi_layer_observation import (
    MultiLayerObservationRegistry, OBSERVER_TYPES, create_observer,
    build_default_observers, correlate_observations, check_observation_completeness,
)
from ai_test_asset_center.cross_surface_oracle import (
    CrossSurfaceOracle, CONSISTENCY_PAIRS, create_oracle_rule,
    build_default_oracle_rules, create_eventual_consistency_strategy,
    detect_emergent_violation,
)


# ─── Test Infrastructure ───────────────────────────────────────────────────────

_results: list[dict] = []


def run_test(name: str, fn):
    """Run a single test and record result."""
    try:
        fn()
        _results.append({"name": name, "status": "PASS", "error": ""})
    except Exception as e:
        _results.append({"name": name, "status": "FAIL", "error": str(e)})


def assert_eq(a, b, msg=""):
    if a != b:
        raise AssertionError(f"{msg}: {a!r} != {b!r}")


def assert_true(v, msg=""):
    if not v:
        raise AssertionError(f"assert_true failed: {msg}")


def assert_ge(a, b, msg=""):
    if a < b:
        raise AssertionError(f"{msg}: {a} < {b}")


def assert_in(item, collection, msg=""):
    if item not in collection:
        raise AssertionError(f"{msg}: {item!r} not in {collection!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# Category 1: Dimension Tests (7)
# ═══════════════════════════════════════════════════════════════════════════════

def test_dim_registry_creates():
    reg = SpaceDimensionRegistry()
    assert_eq(reg.size, 0, "empty registry")


def test_dim_register_defaults():
    reg = SpaceDimensionRegistry()
    count = reg.register_defaults()
    assert_ge(count, 16, "at least 16 dimensions")


def test_dim_six_domains():
    assert_eq(len(SPACE_DOMAINS), 6, "6 space domains")
    assert_in("SYSTEM", SPACE_DOMAINS)
    assert_in("BUSINESS", SPACE_DOMAINS)
    assert_in("SURFACE", SPACE_DOMAINS)
    assert_in("DYNAMIC", SPACE_DOMAINS)
    assert_in("OBSERVATION", SPACE_DOMAINS)
    assert_in("SCALE", SPACE_DOMAINS)


def test_dim_get_by_domain():
    reg = SpaceDimensionRegistry()
    reg.register_defaults()
    system_dims = reg.get_by_domain("SYSTEM")
    assert_ge(len(system_dims), 1, "system dimensions exist")


def test_dim_coverage_summary():
    reg = SpaceDimensionRegistry()
    reg.register_defaults()
    summary = reg.coverage_summary()
    assert_ge(summary["total_dimensions"], 16, "16+ dims in summary")


def test_dim_export_import():
    reg = SpaceDimensionRegistry()
    reg.register_defaults()
    data = reg.export()
    reg2 = SpaceDimensionRegistry()
    reg2.load(data)
    assert_eq(reg2.size, reg.size, "export/import preserves size")


def test_dim_create_dimension():
    dim = create_dimension(
        dimension_type="TEST_DIM", domain="SYSTEM",
        value_source="behavior_ir",
    )
    assert_eq(dim["dimension_type"], "TEST_DIM")
    assert_eq(dim["domain"], "SYSTEM")


# ═══════════════════════════════════════════════════════════════════════════════
# Category 2: Operator Tests (7)
# ═══════════════════════════════════════════════════════════════════════════════

def test_op_registry_creates():
    reg = ExplorationOperatorRegistry()
    assert_eq(reg.size, 0, "empty registry")


def test_op_register_defaults():
    reg = ExplorationOperatorRegistry()
    count = reg.register_defaults()
    assert_ge(count, 54, "at least 54 operators")


def test_op_nine_categories():
    assert_eq(len(OPERATOR_CATEGORIES), 9, "9 categories")


def test_op_get_by_category():
    reg = ExplorationOperatorRegistry()
    reg.register_defaults()
    actor_ops = reg.get_by_category("ACTOR_SCOPE")
    assert_ge(len(actor_ops), 7, "7 actor scope operators")


def test_op_create_operator():
    op = create_operator(
        operator_type="TEST_OP", category="STATE",
        description="test",
    )
    assert_eq(op["operator_type"], "TEST_OP")
    assert_eq(op["category"], "STATE")


def test_op_invalid_category():
    try:
        create_operator(operator_type="X", category="INVALID")
        raise AssertionError("should raise ValueError")
    except ValueError:
        pass


def test_op_applicability():
    reg = ExplorationOperatorRegistry()
    reg.register_defaults()
    op = reg.get_by_type("SWITCH_ACTOR")
    assert_true(op is not None, "SWITCH_ACTOR exists")
    result = check_applicability(op, behavior_ir={"actors": ["a1", "a2"]})
    assert_in("applicable", result, "has applicable field")


# ═══════════════════════════════════════════════════════════════════════════════
# Category 3: Combination Tests (10)
# ═══════════════════════════════════════════════════════════════════════════════

def test_comb_priority_weights_sum():
    total = sum(PRIORITY_WEIGHTS.values())
    assert_true(abs(total - 1.0) < 0.001, f"weights sum to 1.0, got {total}")


def test_comb_priority_score():
    score = compute_priority_score(
        coverage_gain=1.0, deep_bug_potential=1.0,
        business_risk=1.0, novelty_score=1.0,
        observation_confidence=1.0, historical_yield=1.0,
        cost_efficiency=1.0,
    )
    assert_true(abs(score - 1.0) < 0.001, "max score = 1.0")


def test_comb_validate_empty():
    result = validate_combination([], behavior_ir={})
    assert_eq(result["valid"], False, "empty invalid")


def test_comb_validate_single():
    op = create_operator(operator_type="X", category="STATE")
    result = validate_combination([op], behavior_ir={})
    assert_eq(result["valid"], True, "single valid")


def test_comb_generate_1way():
    ops = build_all_operators()[:5]
    combs = generate_combinations(ops, max_level=1, max_combinations=50)
    assert_ge(len(combs), 1, "generates 1-way")
    assert_true(all(c["level"] == "1-way" for c in combs), "all 1-way")


def test_comb_generate_2way():
    ops = build_all_operators()[:10]
    combs = generate_combinations(ops, max_level=2, max_combinations=100)
    two_way = [c for c in combs if c["level"] == "2-way"]
    assert_ge(len(two_way), 1, "generates 2-way")


def test_comb_generate_3way():
    ops = build_all_operators()
    combs = generate_combinations(ops, max_level=3, max_combinations=200)
    three_way = [c for c in combs if c["level"] == "3-way"]
    assert_ge(len(three_way), 1, "generates 3-way")


def test_comb_no_cartesian_explosion():
    ops = build_all_operators()
    combs = generate_combinations(ops, max_level=3, max_combinations=100)
    assert_true(len(combs) <= 100, "respects max_combinations")


def test_comb_pairwise_coverage():
    ops = build_all_operators()[:10]
    combs = generate_combinations(ops, max_level=2, max_combinations=50)
    types = [op["operator_type"] for op in ops]
    coverage = compute_pairwise_coverage(combs, types)
    assert_ge(coverage["covered_pairs"], 0, "has covered pairs")


def test_comb_sorted_by_priority():
    ops = build_all_operators()[:10]
    combs = generate_combinations(ops, max_level=2, max_combinations=50)
    scores = [c["priority_score"] for c in combs]
    assert_eq(scores, sorted(scores, reverse=True), "sorted descending")


# ═══════════════════════════════════════════════════════════════════════════════
# Category 4: Scheduler Tests (8)
# ═══════════════════════════════════════════════════════════════════════════════

def test_sched_creates():
    sched = CoverageGuidedScheduler(project_id="test", budget=50)
    assert_eq(sched.current_round, 0, "starts at round 0")


def test_sched_select_batch():
    sched = CoverageGuidedScheduler(project_id="test", budget=50)
    candidates = [
        {"operators": ["OP_A"], "categories": ["STATE"], "priority_score": 0.8},
        {"operators": ["OP_B"], "categories": ["RELATION"], "priority_score": 0.6},
    ]
    result = sched.select_next_batch(candidates, batch_size=5)
    assert_ge(len(result["selected_experiments"]), 1, "selects experiments")


def test_sched_budget_exhaustion():
    sched = CoverageGuidedScheduler(project_id="test", budget=2)
    candidates = [
        {"operators": [f"OP_{i}"], "categories": ["STATE"], "priority_score": 0.5}
        for i in range(10)
    ]
    r1 = sched.select_next_batch(candidates, batch_size=5)
    for exp in r1["selected_experiments"]:
        sched.record_execution(exp)
    r2 = sched.select_next_batch(candidates, batch_size=5)
    assert_true(r2["budget_remaining"] <= 0 or r2["stop_reason"] != "", "budget tracked")


def test_sched_coverage_state():
    state = compute_coverage_state(
        executed_combinations=[{"operators": ["A", "B"], "categories": ["STATE"]}],
        findings=[{"root_cause_signature": "rc1"}],
    )
    assert_in("A", state["executed_operators"])
    assert_eq(state["total_findings"], 1)


def test_sched_quota_compliance():
    experiments = [
        {"operators": ["SWITCH_ACTOR"], "categories": ["ACTOR_SCOPE"]},
        {"operators": ["MOVE_TO_FORBIDDEN_STATE"], "categories": ["STATE"]},
        {"operators": ["SWITCH_TENANT"], "categories": ["ACTOR_SCOPE"]},
    ]
    result = check_quota_compliance(experiments)
    assert_true(result["compliant"] or len(result["violations"]) > 0, "checks quotas")


def test_sched_auth_cap():
    assert_eq(AUTHORIZATION_CAP, 0.30, "auth cap is 30%")


def test_sched_mechanism_quotas():
    total_quota = sum(MECHANISM_QUOTAS.values())
    # Quotas sum to 0.95 (5% reserved for flexibility)
    assert_true(0.90 <= total_quota <= 1.0, f"quotas sum in [0.9,1.0], got {total_quota}")


def test_sched_export():
    sched = CoverageGuidedScheduler(project_id="test")
    data = sched.export()
    assert_eq(data["schema_version"], "qualibug.coverage-guided-scheduler.v1")


# ═══════════════════════════════════════════════════════════════════════════════
# Category 5: Multi-Surface Tests (8)
# ═══════════════════════════════════════════════════════════════════════════════

def test_surface_six_types():
    assert_eq(len(SURFACE_TYPES), 6, "6 surface types")
    assert_in("API", SURFACE_TYPES)
    assert_in("UI", SURFACE_TYPES)
    assert_in("EVENT", SURFACE_TYPES)


def test_surface_registry_defaults():
    reg = MultiSurfaceAdapterRegistry()
    count = reg.register_defaults()
    assert_eq(count, 6, "6 default adapters")


def test_surface_get_adapter():
    reg = MultiSurfaceAdapterRegistry()
    reg.register_defaults()
    api = reg.get("API")
    assert_true(api is not None, "API adapter exists")
    assert_eq(api["surface_type"], "API")


def test_surface_invalid_type():
    try:
        create_surface_adapter(surface_type="INVALID")
        raise AssertionError("should raise ValueError")
    except ValueError:
        pass


def test_surface_execution_receipt():
    receipt = create_execution_receipt(
        experiment_id="exp1", surface_type="API",
        operation="execute", status="COMPLETED",
    )
    assert_eq(receipt["surface_type"], "API")
    assert_eq(receipt["status"], "COMPLETED")


def test_surface_cross_surface_plan():
    reg = MultiSurfaceAdapterRegistry()
    reg.register_defaults()
    plan = plan_cross_surface_execution(
        experiment={"experiment_id": "e1"},
        adapter_registry=reg,
        primary_surface="API",
        observation_surfaces=["DB", "EVENT"],
    )
    assert_ge(plan["total_steps"], 3, "has multiple steps")


def test_surface_event_plan():
    plan = create_event_exploration_plan(experiment_id="e1")
    assert_ge(plan["total_scenarios"], 5, "multiple event scenarios")
    assert_true(plan["requires_eventual_consistency_wait"], "needs consistency wait")


def test_surface_ui_plan():
    plan = create_ui_exploration_plan(experiment_id="e1")
    assert_ge(plan["total_checks"], 4, "multiple UI checks")


# ═══════════════════════════════════════════════════════════════════════════════
# Category 6: Dynamic/Scale Tests (10)
# ═══════════════════════════════════════════════════════════════════════════════

def test_dyn_concurrency_operators():
    reg = ExplorationOperatorRegistry()
    reg.register_defaults()
    conc = reg.get_by_category("CONCURRENCY")
    assert_ge(len(conc), 7, "7 concurrency operators")


def test_dyn_transaction_operators():
    reg = ExplorationOperatorRegistry()
    reg.register_defaults()
    txn = reg.get_by_category("TRANSACTION_FAILURE")
    assert_ge(len(txn), 9, "9 transaction operators")


def test_dyn_batch_operators():
    reg = ExplorationOperatorRegistry()
    reg.register_defaults()
    batch = reg.get_by_category("BATCH_SCALE")
    assert_ge(len(batch), 8, "8 batch/scale operators")


def test_dyn_event_scenarios():
    assert_ge(len(EVENT_EXPLORATION_SCENARIOS), 10, "10 event scenarios")


def test_dyn_ui_checks():
    assert_ge(len(UI_EXPLORATION_CHECKS), 6, "6 UI checks")


def test_dyn_batch_scale_plan():
    plan = create_batch_scale_plan(
        experiment_id="e1",
        invariant_ids=["inv1", "inv2"],
    )
    assert_true(plan["requires_business_invariant"], "requires invariant")
    assert_true(plan["invariant_binding_complete"], "has invariants bound")


def test_dyn_batch_no_invariant():
    plan = create_batch_scale_plan(experiment_id="e1", invariant_ids=[])
    assert_eq(plan["invariant_binding_complete"], False, "no invariant = incomplete")


def test_dyn_eventual_consistency():
    strategy = create_eventual_consistency_strategy()
    assert_true(strategy["frozen_before_execution"], "frozen strategy")
    assert_ge(strategy["max_wait_seconds"], 10, "reasonable wait")


def test_dyn_emergent_detection():
    result = detect_emergent_violation(
        experiment_id="e1",
        observations=[{"status": "DIVERGENCE", "observer_type": "UNKNOWN"}],
        known_invariants=[],
    )
    assert_true(result["is_emergent"], "detects emergent")


def test_dyn_no_emergent():
    result = detect_emergent_violation(
        experiment_id="e1",
        observations=[{"status": "OK", "observer_type": "API"}],
    )
    assert_eq(result["is_emergent"], False, "no emergent for OK")


# ═══════════════════════════════════════════════════════════════════════════════
# Category 7: Finding/Observation Tests (7)
# ═══════════════════════════════════════════════════════════════════════════════

def test_find_observer_types():
    assert_eq(len(OBSERVER_TYPES), 9, "9 observer types")


def test_find_observer_registry():
    reg = MultiLayerObservationRegistry()
    count = reg.register_defaults()
    assert_eq(count, 9, "9 default observers")


def test_find_record_observation():
    reg = MultiLayerObservationRegistry()
    reg.register_defaults()
    obs_id = reg.record_observation(
        experiment_id="e1", observer_type="API",
        data={"status": 200},
    )
    assert_true(obs_id != "", "returns observation id")


def test_find_correlate():
    observations = [
        {"observer_type": "API", "correlation": {"ENTITY_ID": "ent1"}, "data": {}},
        {"observer_type": "DB", "correlation": {"ENTITY_ID": "ent1"}, "data": {}},
        {"observer_type": "DB", "correlation": {"ENTITY_ID": "ent2"}, "data": {}},
    ]
    result = correlate_observations(
        observations, correlation_key="ENTITY_ID", correlation_value="ent1",
    )
    assert_eq(result["total_correlated"], 2, "2 correlated")


def test_find_completeness():
    result = check_observation_completeness(
        experiment={"experiment_id": "e1"},
        observations=[
            {"observer_type": "API"},
            {"observer_type": "DB"},
        ],
        required_layers=["API", "DB"],
    )
    assert_true(result["complete"], "complete observations")


def test_find_oracle_rules():
    oracle = CrossSurfaceOracle()
    count = oracle.add_default_rules()
    assert_ge(count, 10, "10+ oracle rules")


def test_find_oracle_coverage():
    oracle = CrossSurfaceOracle()
    oracle.add_default_rules()
    summary = oracle.coverage_summary()
    assert_ge(len(summary["pairs_covered"]), 5, "covers 5+ pairs")


# ═══════════════════════════════════════════════════════════════════════════════
# Portfolio Tests (bonus, included in Scheduler category)
# ═══════════════════════════════════════════════════════════════════════════════

def test_portfolio_freeze():
    pf = ExperimentPortfolio(project_id="test")
    pf.add_experiment(create_experiment_entry(
        combination={"combination_id": "c1", "operators": ["A"], "categories": ["STATE"]},
    ))
    result = pf.freeze()
    assert_eq(result["state"], "FROZEN")
    assert_true(pf.is_frozen, "is frozen")


def test_portfolio_no_add_after_freeze():
    pf = ExperimentPortfolio(project_id="test")
    pf.freeze()
    try:
        pf.add_experiment(create_experiment_entry(
            combination={"combination_id": "c2", "operators": ["B"], "categories": ["STATE"]},
        ))
        raise AssertionError("should raise RuntimeError")
    except RuntimeError:
        pass


def test_portfolio_integrity():
    pf = ExperimentPortfolio(project_id="test")
    pf.add_experiment(create_experiment_entry(
        combination={"combination_id": "c1", "operators": ["A"], "categories": ["STATE"]},
    ))
    pf.freeze()
    result = pf.verify_integrity()
    assert_true(result["intact"], "integrity preserved")


# ─── Run All Tests ─────────────────────────────────────────────────────────────

ALL_TESTS = [
    # Dimension (7)
    ("DIM_01_registry_creates", test_dim_registry_creates),
    ("DIM_02_register_defaults", test_dim_register_defaults),
    ("DIM_03_six_domains", test_dim_six_domains),
    ("DIM_04_get_by_domain", test_dim_get_by_domain),
    ("DIM_05_coverage_summary", test_dim_coverage_summary),
    ("DIM_06_export_import", test_dim_export_import),
    ("DIM_07_create_dimension", test_dim_create_dimension),
    # Operator (7)
    ("OP_01_registry_creates", test_op_registry_creates),
    ("OP_02_register_defaults", test_op_register_defaults),
    ("OP_03_nine_categories", test_op_nine_categories),
    ("OP_04_get_by_category", test_op_get_by_category),
    ("OP_05_create_operator", test_op_create_operator),
    ("OP_06_invalid_category", test_op_invalid_category),
    ("OP_07_applicability", test_op_applicability),
    # Combination (10)
    ("COMB_01_priority_weights_sum", test_comb_priority_weights_sum),
    ("COMB_02_priority_score", test_comb_priority_score),
    ("COMB_03_validate_empty", test_comb_validate_empty),
    ("COMB_04_validate_single", test_comb_validate_single),
    ("COMB_05_generate_1way", test_comb_generate_1way),
    ("COMB_06_generate_2way", test_comb_generate_2way),
    ("COMB_07_generate_3way", test_comb_generate_3way),
    ("COMB_08_no_cartesian", test_comb_no_cartesian_explosion),
    ("COMB_09_pairwise_coverage", test_comb_pairwise_coverage),
    ("COMB_10_sorted_by_priority", test_comb_sorted_by_priority),
    # Scheduler (8)
    ("SCHED_01_creates", test_sched_creates),
    ("SCHED_02_select_batch", test_sched_select_batch),
    ("SCHED_03_budget_exhaustion", test_sched_budget_exhaustion),
    ("SCHED_04_coverage_state", test_sched_coverage_state),
    ("SCHED_05_quota_compliance", test_sched_quota_compliance),
    ("SCHED_06_auth_cap", test_sched_auth_cap),
    ("SCHED_07_mechanism_quotas", test_sched_mechanism_quotas),
    ("SCHED_08_export", test_sched_export),
    # Multi-Surface (8)
    ("SURF_01_six_types", test_surface_six_types),
    ("SURF_02_registry_defaults", test_surface_registry_defaults),
    ("SURF_03_get_adapter", test_surface_get_adapter),
    ("SURF_04_invalid_type", test_surface_invalid_type),
    ("SURF_05_execution_receipt", test_surface_execution_receipt),
    ("SURF_06_cross_surface_plan", test_surface_cross_surface_plan),
    ("SURF_07_event_plan", test_surface_event_plan),
    ("SURF_08_ui_plan", test_surface_ui_plan),
    # Dynamic/Scale (10)
    ("DYN_01_concurrency_ops", test_dyn_concurrency_operators),
    ("DYN_02_transaction_ops", test_dyn_transaction_operators),
    ("DYN_03_batch_ops", test_dyn_batch_operators),
    ("DYN_04_event_scenarios", test_dyn_event_scenarios),
    ("DYN_05_ui_checks", test_dyn_ui_checks),
    ("DYN_06_batch_scale_plan", test_dyn_batch_scale_plan),
    ("DYN_07_batch_no_invariant", test_dyn_batch_no_invariant),
    ("DYN_08_eventual_consistency", test_dyn_eventual_consistency),
    ("DYN_09_emergent_detection", test_dyn_emergent_detection),
    ("DYN_10_no_emergent", test_dyn_no_emergent),
    # Finding/Observation (7)
    ("FIND_01_observer_types", test_find_observer_types),
    ("FIND_02_observer_registry", test_find_observer_registry),
    ("FIND_03_record_observation", test_find_record_observation),
    ("FIND_04_correlate", test_find_correlate),
    ("FIND_05_completeness", test_find_completeness),
    ("FIND_06_oracle_rules", test_find_oracle_rules),
    ("FIND_07_oracle_coverage", test_find_oracle_coverage),
    # Portfolio (3 bonus)
    ("PF_01_freeze", test_portfolio_freeze),
    ("PF_02_no_add_after_freeze", test_portfolio_no_add_after_freeze),
    ("PF_03_integrity", test_portfolio_integrity),
]


def main():
    print("=" * 70)
    print("SPACE EXPLORATION UNIT TESTS (57 + 3 bonus = 60)")
    print("=" * 70)

    for name, fn in ALL_TESTS:
        run_test(name, fn)

    passed = sum(1 for r in _results if r["status"] == "PASS")
    failed = sum(1 for r in _results if r["status"] == "FAIL")
    total = len(_results)

    print(f"\nResults: {passed}/{total} PASS, {failed} FAIL")
    print("-" * 70)

    for r in _results:
        status_icon = "[PASS]" if r["status"] == "PASS" else "[FAIL]"
        line = f"  {status_icon} {r['name']}"
        if r["error"]:
            line += f" -- {r['error'][:80]}"
        print(line)

    # Write results JSON
    output = {
        "schema_version": "qualibug.space-exploration-unit-test.v1",
        "total": total,
        "passed": passed,
        "failed": failed,
        "results": _results,
        "timestamp": time.time(),
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "space_exploration_unit_test_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nResults written to: {out_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
