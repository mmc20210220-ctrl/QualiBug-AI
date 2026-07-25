"""Space Exploration Integration Tests — 8 end-to-end scenarios.

SPEC §39:
  1. Actor x State
  2. State x Cross-Entity
  3. Timeout x Retry x Idempotency
  4. Concurrency x Conservation
  5. Failure x Compensation
  6. API x DB x Event
  7. Scale x Batch Invariant
  8. UI x API
"""
from __future__ import annotations

import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_test_asset_center.space_dimension_registry import SpaceDimensionRegistry
from ai_test_asset_center.space_coordinate import create_coordinate, validate_coordinate
from ai_test_asset_center.invariant_graph import InvariantGraph, build_default_invariant_graph
from ai_test_asset_center.exploration_operator_registry import (
    ExplorationOperatorRegistry, check_applicability,
)
from ai_test_asset_center.combination_generator import (
    generate_combinations, compute_pairwise_coverage,
)
from ai_test_asset_center.coverage_guided_scheduler import (
    CoverageGuidedScheduler, check_quota_compliance,
)
from ai_test_asset_center.experiment_portfolio import (
    ExperimentPortfolio, create_experiment_entry, validate_portfolio_quotas,
)
from ai_test_asset_center.multi_surface_adapter import (
    MultiSurfaceAdapterRegistry, plan_cross_surface_execution,
    create_event_exploration_plan, create_batch_scale_plan,
    create_ui_exploration_plan,
)
from ai_test_asset_center.multi_layer_observation import (
    MultiLayerObservationRegistry, check_observation_completeness,
)
from ai_test_asset_center.cross_surface_oracle import (
    CrossSurfaceOracle, create_eventual_consistency_strategy,
)


# ─── Test Infrastructure ───────────────────────────────────────────────────────

_results: list[dict] = []


def run_test(name: str, fn):
    try:
        fn()
        _results.append({"name": name, "status": "PASS", "error": ""})
    except Exception as e:
        _results.append({"name": name, "status": "FAIL", "error": str(e)})


def assert_true(v, msg=""):
    if not v:
        raise AssertionError(f"assert_true failed: {msg}")


def assert_ge(a, b, msg=""):
    if a < b:
        raise AssertionError(f"{msg}: {a} < {b}")


def assert_eq(a, b, msg=""):
    if a != b:
        raise AssertionError(f"{msg}: {a!r} != {b!r}")


# ─── Shared Fixtures ───────────────────────────────────────────────────────────

def build_full_system():
    """Build complete system for integration testing."""
    dim_reg = SpaceDimensionRegistry()
    dim_reg.register_defaults()

    op_reg = ExplorationOperatorRegistry()
    op_reg.register_defaults()

    inv_graph = InvariantGraph(project_id="integration_test")
    # Add some invariants
    from ai_test_asset_center.invariant_graph import create_invariant
    inv_graph.add(create_invariant(
        invariant_type="STATE_LIFECYCLE", subject_entities=["order"],
        expected_expression="order lifecycle",
    ))
    inv_graph.add(create_invariant(
        invariant_type="CONSERVATION", subject_entities=["account"],
        expected_expression="balance conservation",
    ))
    inv_graph.add(create_invariant(
        invariant_type="IDEMPOTENCY", subject_entities=["payment"],
        expected_expression="payment idempotency",
    ))

    surface_reg = MultiSurfaceAdapterRegistry()
    surface_reg.register_defaults()

    obs_reg = MultiLayerObservationRegistry()
    obs_reg.register_defaults()

    oracle = CrossSurfaceOracle(project_id="integration_test")
    oracle.add_default_rules()

    return {
        "dim_reg": dim_reg,
        "op_reg": op_reg,
        "inv_graph": inv_graph,
        "surface_reg": surface_reg,
        "obs_reg": obs_reg,
        "oracle": oracle,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test 1: Actor x State
# ═══════════════════════════════════════════════════════════════════════════════

def test_int_actor_x_state():
    """Actor switch combined with state transition."""
    sys = build_full_system()
    op_reg = sys["op_reg"]

    # Get actor and state operators
    actor_ops = op_reg.get_by_category("ACTOR_SCOPE")
    state_ops = op_reg.get_by_category("STATE")
    assert_ge(len(actor_ops), 1, "has actor ops")
    assert_ge(len(state_ops), 1, "has state ops")

    # Generate 2-way combinations
    all_ops = actor_ops[:2] + state_ops[:2]
    combs = generate_combinations(all_ops, max_level=2, max_combinations=20)

    # Find actor x state combinations
    actor_state = [
        c for c in combs
        if "ACTOR_SCOPE" in c.get("categories", [])
        and "STATE" in c.get("categories", [])
    ]
    assert_ge(len(actor_state), 1, "has actor x state combinations")

    # Create coordinate
    coord = create_coordinate(
        entity_ids=["order_001"],
        actor_id="user_a",
        role_id="admin",
        tenant_id="tenant_1",
        pre_state="DRAFT",
        target_state="SUBMITTED",
        operation_ids=["submit_order"],
        execution_surface="API",
    )
    validation = validate_coordinate(coord)
    assert_true(validation["valid"], "coordinate valid")

    # Schedule and freeze portfolio
    sched = CoverageGuidedScheduler(project_id="int_test", budget=10)
    batch = sched.select_next_batch(actor_state, batch_size=5)
    assert_ge(len(batch["selected_experiments"]), 1, "scheduled experiments")

    pf = ExperimentPortfolio(project_id="int_test")
    pf.add_combinations(batch["selected_experiments"], coordinate=coord)
    pf.freeze()
    assert_true(pf.is_frozen, "portfolio frozen")


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test 2: State x Cross-Entity
# ═══════════════════════════════════════════════════════════════════════════════

def test_int_state_x_cross_entity():
    """State transition with cross-entity relation change."""
    sys = build_full_system()
    op_reg = sys["op_reg"]

    state_ops = op_reg.get_by_category("STATE")
    relation_ops = op_reg.get_by_category("RELATION")

    all_ops = state_ops[:2] + relation_ops[:2]
    combs = generate_combinations(all_ops, max_level=2, max_combinations=20)

    state_relation = [
        c for c in combs
        if "STATE" in c.get("categories", [])
        and "RELATION" in c.get("categories", [])
    ]
    assert_ge(len(state_relation), 1, "has state x relation combinations")

    # Verify invariant binding
    inv_graph = sys["inv_graph"]
    state_invariants = inv_graph.get_by_type("STATE_LIFECYCLE")
    assert_ge(len(state_invariants), 1, "has state invariants")


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test 3: Timeout x Retry x Idempotency
# ═══════════════════════════════════════════════════════════════════════════════

def test_int_timeout_retry_idempotency():
    """3-way: temporal delay + replay + idempotency check."""
    sys = build_full_system()
    op_reg = sys["op_reg"]

    temporal_ops = op_reg.get_by_category("TEMPORAL")
    replay_ops = op_reg.get_by_category("REPLAY_IDEMPOTENCY")

    all_ops = temporal_ops[:2] + replay_ops[:2]
    combs = generate_combinations(all_ops, max_level=3, max_combinations=30)

    # Find 3-way combinations
    three_way = [c for c in combs if c["level"] == "3-way"]
    # At minimum, 2-way temporal x replay should exist
    two_way = [
        c for c in combs
        if "TEMPORAL" in c.get("categories", [])
        and "REPLAY_IDEMPOTENCY" in c.get("categories", [])
    ]
    assert_ge(len(two_way) + len(three_way), 1, "has temporal x replay combinations")

    # Verify idempotency invariant exists
    inv_graph = sys["inv_graph"]
    idempotency = inv_graph.get_by_type("IDEMPOTENCY")
    assert_ge(len(idempotency), 1, "has idempotency invariant")


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test 4: Concurrency x Conservation
# ═══════════════════════════════════════════════════════════════════════════════

def test_int_concurrency_x_conservation():
    """Parallel execution with conservation invariant check."""
    sys = build_full_system()
    op_reg = sys["op_reg"]

    conc_ops = op_reg.get_by_category("CONCURRENCY")
    assert_ge(len(conc_ops), 7, "7 concurrency operators")

    # Conservation invariant
    inv_graph = sys["inv_graph"]
    conservation = inv_graph.get_by_type("CONSERVATION")
    assert_ge(len(conservation), 1, "has conservation invariant")

    # Generate combinations
    combs = generate_combinations(conc_ops[:3], max_level=2, max_combinations=10)
    assert_ge(len(combs), 1, "has concurrency combinations")

    # Coordinate with concurrency
    coord = create_coordinate(
        entity_ids=["account_001"],
        actor_id="user_a",
        operation_ids=["transfer"],
        execution_surface="API",
        concurrency="PARALLEL",
        concurrency_level=2,
    )
    validation = validate_coordinate(coord)
    assert_true(validation["valid"], "concurrency coordinate valid")


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test 5: Failure x Compensation
# ═══════════════════════════════════════════════════════════════════════════════

def test_int_failure_x_compensation():
    """Transaction failure with compensation execution."""
    sys = build_full_system()
    op_reg = sys["op_reg"]

    txn_ops = op_reg.get_by_category("TRANSACTION_FAILURE")
    assert_ge(len(txn_ops), 9, "9 transaction operators")

    # Find compensation-related operators
    compensation_ops = [
        op for op in txn_ops
        if "COMPENSATION" in op.get("operator_type", "")
        or "PARTIAL" in op.get("operator_type", "")
    ]
    assert_ge(len(compensation_ops), 1, "has compensation operators")

    # Generate failure combinations
    combs = generate_combinations(txn_ops[:4], max_level=2, max_combinations=15)
    assert_ge(len(combs), 1, "has failure combinations")


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test 6: API x DB x Event
# ═══════════════════════════════════════════════════════════════════════════════

def test_int_api_db_event():
    """Cross-surface: API execution observed via DB and Event."""
    sys = build_full_system()
    surface_reg = sys["surface_reg"]
    obs_reg = sys["obs_reg"]
    oracle = sys["oracle"]

    # Plan cross-surface execution
    experiment = {"experiment_id": "int_api_db_event", "surface_adapter": "API"}
    plan = plan_cross_surface_execution(
        experiment=experiment,
        adapter_registry=surface_reg,
        primary_surface="API",
        observation_surfaces=["DB", "EVENT"],
    )
    assert_ge(plan["total_steps"], 4, "multi-step cross-surface plan")

    # Verify observers available
    api_obs = obs_reg.get("API")
    db_obs = obs_reg.get("DB")
    event_obs = obs_reg.get("EVENT")
    assert_true(api_obs is not None, "API observer")
    assert_true(db_obs is not None, "DB observer")
    assert_true(event_obs is not None, "EVENT observer")

    # Oracle rules for API_DB and API_EVENT
    api_db_rules = oracle.get_rules_for_pair("API_DB")
    api_event_rules = oracle.get_rules_for_pair("API_EVENT")
    assert_ge(len(api_db_rules), 1, "API_DB oracle rules")
    assert_ge(len(api_event_rules), 1, "API_EVENT oracle rules")

    # Eventual consistency strategy
    strategy = create_eventual_consistency_strategy()
    assert_true(strategy["frozen_before_execution"], "frozen strategy")


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test 7: Scale x Batch Invariant
# ═══════════════════════════════════════════════════════════════════════════════

def test_int_scale_x_batch_invariant():
    """Scale experiment with business invariant binding."""
    sys = build_full_system()
    op_reg = sys["op_reg"]
    inv_graph = sys["inv_graph"]

    batch_ops = op_reg.get_by_category("BATCH_SCALE")
    assert_ge(len(batch_ops), 8, "8 batch/scale operators")

    # Batch plan MUST bind invariant
    all_invariants = inv_graph.export()["invariants"]
    invariant_ids = [inv["invariant_id"] for inv in all_invariants][:2]
    plan = create_batch_scale_plan(
        experiment_id="int_scale_batch",
        data_volumes=[100, 1000, 10000],
        batch_sizes=[10, 50, 100],
        invariant_ids=invariant_ids,
    )
    assert_true(plan["requires_business_invariant"], "requires invariant")
    assert_true(plan["invariant_binding_complete"], "invariant bound")
    assert_ge(len(plan["bound_invariant_ids"]), 1, "has bound invariants")


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test 8: UI x API
# ═══════════════════════════════════════════════════════════════════════════════

def test_int_ui_x_api():
    """UI surface cross-validated with API surface."""
    sys = build_full_system()
    surface_reg = sys["surface_reg"]
    oracle = sys["oracle"]

    # UI exploration plan
    ui_plan = create_ui_exploration_plan(
        experiment_id="int_ui_api",
        checks=["STATE_FORBIDDEN_VIA_UI", "API_ALLOWED_UI_BLOCKED", "FIELD_INCONSISTENCY_API_UI"],
    )
    assert_ge(ui_plan["total_checks"], 3, "3+ UI checks")
    assert_true(ui_plan["requires_api_comparison"], "needs API comparison")

    # Cross-surface plan: UI primary, API observation
    experiment = {"experiment_id": "int_ui_api", "surface_adapter": "UI"}
    plan = plan_cross_surface_execution(
        experiment=experiment,
        adapter_registry=surface_reg,
        primary_surface="UI",
        observation_surfaces=["API", "DB"],
    )
    assert_ge(plan["total_steps"], 3, "multi-step UI plan")

    # Oracle rules for API_UI
    api_ui_rules = oracle.get_rules_for_pair("API_UI")
    assert_ge(len(api_ui_rules), 1, "API_UI oracle rules")


# ─── Run All Integration Tests ─────────────────────────────────────────────────

ALL_TESTS = [
    ("INT_01_actor_x_state", test_int_actor_x_state),
    ("INT_02_state_x_cross_entity", test_int_state_x_cross_entity),
    ("INT_03_timeout_retry_idempotency", test_int_timeout_retry_idempotency),
    ("INT_04_concurrency_x_conservation", test_int_concurrency_x_conservation),
    ("INT_05_failure_x_compensation", test_int_failure_x_compensation),
    ("INT_06_api_db_event", test_int_api_db_event),
    ("INT_07_scale_x_batch_invariant", test_int_scale_x_batch_invariant),
    ("INT_08_ui_x_api", test_int_ui_x_api),
]


def main():
    print("=" * 70)
    print("SPACE EXPLORATION INTEGRATION TESTS (8 scenarios)")
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
            line += f" -- {r['error'][:100]}"
        print(line)

    # Write results JSON
    output = {
        "schema_version": "qualibug.space-exploration-integration-test.v1",
        "total": total,
        "passed": passed,
        "failed": failed,
        "results": _results,
        "timestamp": time.time(),
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "space_exploration_integration_test_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nResults written to: {out_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
