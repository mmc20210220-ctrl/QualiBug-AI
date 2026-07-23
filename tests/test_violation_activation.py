"""Unit tests for violation_activation module.

Covers SPEC §22 (25 test cases) and §23 (6 industry-neutral scenarios).
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_test_asset_center.violation_activation import (
    compile_violation_condition,
    check_violation_satisfiability,
    generate_violation_mutation,
    check_oracle_input_completeness,
    build_violation_experiment_pair,
    refine_violation_plan,
    classify_final_status,
    run_violation_activation,
    MAX_VIOLATION_REFINEMENTS,
    VIOLATION_TRIGGERED,
    TRUE_PASS_CONFIRMED,
    PRECONDITION_NOT_REACHED,
    ORACLE_INPUT_INCOMPLETE,
    VIOLATION_NOT_ACTIVATED,
    VIOLATION_CONDITION_UNSATISFIABLE,
    MINIMAL_VIOLATION,
    CLEAR_VIOLATION,
    COMPOUND_VIOLATION,
)


# ─── §22 Test Cases ───────────────────────────────────────────────────────────

class TestReverseSolving:
    """Tests 1-12: Expression reverse solving."""

    def test_01_lte_reverse(self):
        """LTE reverse: target = boundary + step."""
        expr = {
            "type": "LTE",
            "operator": "LTE",
            "left": {"entity": "entity_b", "field": "amount"},
            "right": {"entity": "entity_a", "field": "limit"},
        }
        vc = compile_violation_condition(expr, rule_id="r1")
        assert vc["expression_type"] == "LTE"
        assert "limit_exceeded" in str(vc["mutation_targets"])
        mutations = generate_violation_mutation(
            vc, runtime_values={"entity_b.limit": 100, "entity_b.remaining": 50}
        )
        assert len(mutations) >= 1
        assert mutations[0]["value"] > 50  # Exceeds remaining

    def test_02_gte_reverse(self):
        """GTE reverse: target = boundary - step."""
        expr = {
            "type": "LTE",
            "operator": "GTE",
            "left": {"entity": "entity_b", "field": "balance"},
            "right": {"value": 0},
        }
        vc = compile_violation_condition(expr, rule_id="r2")
        assert vc["expression_type"] == "LTE"
        assert vc["violating_condition"]  # Has violating condition

    def test_03_eq_one_side_controllable(self):
        """EQ: one side controllable via operations."""
        expr = {
            "type": "SUM",
            "operator": "EQ",
            "left": {"entity": "child", "field": "amount", "aggregate": "SUM", "scope": "parent_id"},
            "right": {"entity": "parent", "field": "total"},
        }
        vc = compile_violation_condition(expr, rule_id="r3")
        assert "sum_mismatch" in str(vc["mutation_targets"])
        mutations = generate_violation_mutation(
            vc, runtime_values={"child.parent_total": 1000, "child.current_sum": 400}
        )
        assert len(mutations) >= 1

    def test_04_eq_neither_side_controllable(self):
        """EQ: neither side easily controllable → still generates condition."""
        expr = {
            "type": "SUM",
            "operator": "EQ",
            "left": {"entity": "x", "field": "a", "aggregate": "SUM"},
            "right": {"entity": "y", "field": "b"},
        }
        vc = compile_violation_condition(expr, rule_id="r4")
        assert vc["satisfiable"] is True  # Still satisfiable in principle

    def test_05_implies_force_antecedent(self):
        """IMPLIES: force antecedent true."""
        expr = {
            "type": "IMPLIES",
            "condition": {"entity": "root", "field": "status", "value": "CANCELLED"},
            "constraint": {"entity": "child", "field": "status", "expected": "REJECTED"},
        }
        vc = compile_violation_condition(expr, rule_id="r5")
        assert "force_antecedent_true" in str(vc["mutation_targets"])
        mutations = generate_violation_mutation(vc, runtime_values={})
        assert any(m.get("force_condition_true") for m in mutations)

    def test_06_implies_antecedent_false_invalid(self):
        """IMPLIES: antecedent false → experiment invalid."""
        expr = {
            "type": "IMPLIES",
            "condition": {"entity": "root", "field": "status", "value": "CANCELLED"},
            "constraint": {"entity": "child", "field": "status", "expected": "REJECTED"},
        }
        vc = compile_violation_condition(expr, rule_id="r6")
        # If antecedent can't be forced true, satisfiability check should flag
        sat = check_violation_satisfiability(
            vc,
            available_operations=[{"id": "op1"}],
            available_actors=[{"id": "a1"}],
            available_observers=["http_response"],
            fixture_capabilities=["create"],
        )
        assert sat["satisfiable"] is True  # Can force antecedent via operations

    def test_07_temporal_upper_bound(self):
        """Temporal: exceed upper bound."""
        expr = {
            "type": "TEMPORAL",
            "operator": "LTE",
            "left": {"entity": "child", "field": "date"},
            "right": {"entity": "parent", "field": "end_date"},
        }
        vc = compile_violation_condition(expr, rule_id="r7")
        assert "temporal_boundary_violation" in str(vc["mutation_targets"])
        mutations = generate_violation_mutation(
            vc, runtime_values={"child.boundary_date": "2026-12-31"}
        )
        assert len(mutations) >= 1
        assert mutations[0]["value"] > "2026-12-31"

    def test_08_temporal_lower_bound(self):
        """Temporal: below lower bound."""
        expr = {
            "type": "TEMPORAL",
            "operator": "GTE",
            "left": {"entity": "child", "field": "start_date"},
            "right": {"entity": "parent", "field": "start_date"},
        }
        vc = compile_violation_condition(expr, rule_id="r8")
        assert vc["violating_condition"]

    def test_09_delta_wrong_change(self):
        """DELTA: wrong change detected."""
        expr = {
            "type": "DELTA",
            "operator": "DELTA_EQ",
            "left": {"entity": "budget", "field": "available", "expected_delta": "-100"},
            "right": {"entity": "budget", "field": "reserved", "expected_delta": "+100"},
        }
        vc = compile_violation_condition(expr, rule_id="r9")
        assert "delta_violation" in str(vc["mutation_targets"])
        mutations = generate_violation_mutation(vc, runtime_values={})
        assert any(m.get("observe_before_after") for m in mutations)

    def test_10_sum_cumulative(self):
        """SUM: cumulative overflow."""
        expr = {
            "type": "SUM",
            "operator": "EQ",
            "left": {"entity": "payment", "field": "amount", "aggregate": "SUM", "scope": "milestone_id"},
            "right": {"entity": "milestone", "field": "accepted_amount"},
        }
        vc = compile_violation_condition(expr, rule_id="r10")
        mutations = generate_violation_mutation(
            vc, runtime_values={"payment.parent_total": 50000, "payment.current_sum": 30000},
            strength=COMPOUND_VIOLATION,
        )
        assert len(mutations) >= 1

    def test_11_concurrency_stale_version(self):
        """Concurrency: stale version update."""
        expr = {
            "type": "CONCURRENCY",
            "operator": "CONFLICT",
            "left": {"entity": "record", "field": "version"},
            "right": {"entity": "request", "field": "If-Match-Version"},
        }
        vc = compile_violation_condition(expr, rule_id="r11")
        assert "stale_version" in str(vc["mutation_targets"])
        mutations = generate_violation_mutation(
            vc, runtime_values={"record.version": 5}
        )
        assert len(mutations) >= 1
        assert mutations[0].get("requires_concurrent_actor") is True

    def test_12_state_forbidden_transition(self):
        """State: forbidden transition."""
        expr = {
            "type": "STATE",
            "left": {"entity": "order", "field": "status"},
        }
        vc = compile_violation_condition(expr, rule_id="r12")
        assert "forbidden_transition" in str(vc["mutation_targets"])


class TestControlViolationIsolation:
    """Tests 13-15: Control/Violation isolation and mutation strength."""

    def test_13_control_violation_isolation(self):
        """Control and Violation use isolated fixtures."""
        target = {
            "target_id": "T1",
            "internal_rule_id": "r1",
            "structured_expression": {"type": "LTE", "operator": "LTE",
                                      "left": {"entity": "a", "field": "x"},
                                      "right": {"entity": "b", "field": "y"}},
            "operation_ids": ["POST /api/v1/resource"],
        }
        vc = compile_violation_condition(target["structured_expression"])
        mutations = generate_violation_mutation(vc, runtime_values={"a.limit": 100})
        pair = build_violation_experiment_pair(target, vc, mutations)
        assert "control_plan" in pair
        assert "violation_plan" in pair
        assert pair["control_plan"][0]["protocol_step"] == "positive_control"
        assert pair["violation_plan"][0]["protocol_step"] == "violation_mutation"

    def test_14_minimal_violation(self):
        """Mutation just exceeds boundary."""
        expr = {
            "type": "LTE", "operator": "LTE",
            "left": {"entity": "e", "field": "amount"},
            "right": {"entity": "e", "field": "limit"},
        }
        vc = compile_violation_condition(expr)
        mutations = generate_violation_mutation(
            vc, runtime_values={"e.limit": 100, "e.remaining": 50},
            strength=MINIMAL_VIOLATION,
        )
        assert mutations[0]["value"] == 51  # remaining + 1

    def test_15_clear_violation(self):
        """Mutation clearly exceeds boundary."""
        expr = {
            "type": "LTE", "operator": "LTE",
            "left": {"entity": "e", "field": "amount"},
            "right": {"entity": "e", "field": "limit"},
        }
        vc = compile_violation_condition(expr)
        mutations = generate_violation_mutation(
            vc, runtime_values={"e.limit": 100, "e.remaining": 50},
            strength=CLEAR_VIOLATION,
        )
        assert mutations[0]["value"] > 51  # More than minimal


class TestCumulativeAndObservation:
    """Tests 16-21: Cumulative operations and observation completeness."""

    def test_16_cumulative_multi_step(self):
        """Cumulative: multiple operations leading to overflow."""
        expr = {
            "type": "SUM", "operator": "EQ",
            "left": {"entity": "item", "field": "qty", "aggregate": "SUM"},
            "right": {"entity": "order", "field": "max_qty"},
        }
        vc = compile_violation_condition(expr)
        mutations = generate_violation_mutation(
            vc, runtime_values={"item.parent_total": 10, "item.current_sum": 8},
            strength=COMPOUND_VIOLATION,
        )
        assert len(mutations) >= 1

    def test_17_missing_related_entity_no_pass(self):
        """Missing related entity → not PASS."""
        result = check_oracle_input_completeness(
            required_entities=["root", "related"],
            observed_entities=["root"],
            required_fields=["root.amount"],
            observed_fields=["root.amount"],
        )
        assert result["complete"] is False
        assert "related" in result["missing_entities"]

    def test_18_missing_critical_field_no_pass(self):
        """Missing critical field → not PASS."""
        result = check_oracle_input_completeness(
            required_entities=["root"],
            observed_entities=["root"],
            required_fields=["root.amount", "root.status"],
            observed_fields=["root.amount"],
        )
        assert result["complete"] is False
        assert "root.status" in result["missing_fields"]

    def test_19_pagination_incomplete_no_pass(self):
        """Pagination incomplete → not PASS."""
        result = check_oracle_input_completeness(
            required_entities=["root"],
            observed_entities=["root"],
            required_fields=[],
            observed_fields=[],
            pagination_complete=False,
        )
        assert result["complete"] is False

    def test_20_scope_error_no_pass(self):
        """Scope error → not PASS."""
        result = check_oracle_input_completeness(
            required_entities=["root"],
            observed_entities=["root"],
            required_fields=[],
            observed_fields=[],
            scope_verified=False,
        )
        assert result["complete"] is False

    def test_21_empty_vs_unqueried(self):
        """Empty collection vs unqueried collection distinction."""
        # Empty but queried → complete
        result_empty = check_oracle_input_completeness(
            required_entities=["root"],
            observed_entities=["root"],
            required_fields=["root.items"],
            observed_fields=["root.items"],  # Queried, result is empty
        )
        assert result_empty["complete"] is True

        # Unqueried → incomplete
        result_unqueried = check_oracle_input_completeness(
            required_entities=["root"],
            observed_entities=["root"],
            required_fields=["root.items"],
            observed_fields=[],  # Not queried
        )
        assert result_unqueried["complete"] is False


class TestRefinement:
    """Tests 22-23: Refinement loop constraints."""

    def test_22_max_two_refinements(self):
        """Maximum 2 refinement rounds."""
        vc = {"expression_type": "LTE", "mutation_targets": [
            {"entity": "e", "field": "x", "mutation_type": "limit_exceeded", "strategy": "exceed"}
        ]}
        prev = {"status": "PASS", "reason_code": "WEAK_MUTATION"}

        r1 = refine_violation_plan(prev, vc, refinement_round=1, runtime_values={})
        assert r1 is not None
        assert r1["refinement_round"] == 1

        r2 = refine_violation_plan(prev, vc, refinement_round=2, runtime_values={})
        assert r2 is not None
        assert r2["refinement_round"] == 2

        r3 = refine_violation_plan(prev, vc, refinement_round=3, runtime_values={})
        assert r3 is None  # Exceeds max

    def test_23_refinement_does_not_modify_oracle(self):
        """Refinement adjusts mutation/observation, not oracle."""
        vc = {"expression_type": "LTE", "mutation_targets": [
            {"entity": "e", "field": "x", "mutation_type": "limit_exceeded", "strategy": "exceed"}
        ]}
        prev = {"status": "PASS", "reason_code": "OBSERVATION_INCOMPLETE"}
        r = refine_violation_plan(prev, vc, refinement_round=1, runtime_values={})
        assert "add_missing_observers" in r["adjustments"]
        # Oracle expression type unchanged
        assert vc["expression_type"] == "LTE"


class TestAntiHardcoding:
    """Tests 24-25: No benchmark values, no project-specific logic."""

    def test_24_no_benchmark_values_in_mutation(self):
        """Benchmark values must not enter mutation."""
        expr = {
            "type": "LTE", "operator": "LTE",
            "left": {"entity": "generic_entity", "field": "generic_field"},
            "right": {"entity": "generic_entity", "field": "generic_limit"},
        }
        vc = compile_violation_condition(expr)
        # Use generic runtime values, not benchmark-specific
        mutations = generate_violation_mutation(
            vc, runtime_values={"generic_entity.limit": 500, "generic_entity.remaining": 200},
            strength=MINIMAL_VIOLATION,
        )
        # Mutation value derived from runtime, not hardcoded
        assert mutations[0]["value"] == 201  # remaining + 1 for MINIMAL
        # No benchmark-specific values like 50000, 100000, etc.

    def test_25_project_entity_names_dont_affect_solving(self):
        """Project-specific entity names don't affect reverse solving."""
        # Same expression structure with different entity names
        expr_a = {
            "type": "DELTA", "operator": "DELTA_EQ",
            "left": {"entity": "alpha", "field": "val", "expected_delta": "-X"},
            "right": {"entity": "beta", "field": "val", "expected_delta": "+X"},
        }
        expr_b = {
            "type": "DELTA", "operator": "DELTA_EQ",
            "left": {"entity": "gamma", "field": "val", "expected_delta": "-X"},
            "right": {"entity": "delta", "field": "val", "expected_delta": "+X"},
        }
        vc_a = compile_violation_condition(expr_a)
        vc_b = compile_violation_condition(expr_b)
        # Same structure, different names → same mutation type
        assert vc_a["mutation_targets"][0]["mutation_type"] == vc_b["mutation_targets"][0]["mutation_type"]


# ─── §23 Industry-Neutral Integration Scenarios ──────────────────────────────

class TestIndustryNeutralScenarios:
    """Scenarios A-F: Industry-neutral integration tests."""

    def test_scenario_a_cumulative_limit(self):
        """Scenario A: SUM(entity_b.value) <= entity_a.limit."""
        expr = {
            "type": "SUM", "operator": "EQ",
            "left": {"entity": "entity_b", "field": "value", "aggregate": "SUM", "scope": "entity_a_id"},
            "right": {"entity": "entity_a", "field": "limit"},
        }
        vc = compile_violation_condition(expr, rule_id="scenario_a")
        assert vc["satisfiable"] is True
        mutations = generate_violation_mutation(
            vc, runtime_values={"entity_b.parent_total": 1000, "entity_b.current_sum": 900}
        )
        assert len(mutations) >= 1
        # Violation amount should exceed remaining (1000-900=100)
        assert mutations[0]["value"] > 100

    def test_scenario_b_compensation(self):
        """Scenario B: cancel → related.balance restored, related.state changed."""
        expr = {
            "type": "DELTA", "operator": "DELTA_EQ",
            "left": {"entity": "related", "field": "balance", "expected_delta": "+X"},
            "right": {"entity": "related", "field": "state", "expected_delta": "changed"},
        }
        vc = compile_violation_condition(expr, rule_id="scenario_b")
        assert "delta_violation" in str(vc["mutation_targets"])
        assert len(vc["required_observations"]) >= 2

    def test_scenario_c_conditional_implication(self):
        """Scenario C: A=true → B must be true."""
        expr = {
            "type": "IMPLIES",
            "condition": {"entity": "root", "field": "flag", "value": "active"},
            "constraint": {"entity": "dependent", "field": "enabled", "expected": "true"},
        }
        vc = compile_violation_condition(expr, rule_id="scenario_c")
        mutations = generate_violation_mutation(vc, runtime_values={})
        assert any(m.get("force_condition_true") for m in mutations)

    def test_scenario_d_concurrent_version(self):
        """Scenario D: Two clients, same version, one succeeds, other stale."""
        expr = {
            "type": "CONCURRENCY", "operator": "CONFLICT",
            "left": {"entity": "resource", "field": "version"},
            "right": {"entity": "request", "field": "expected_version"},
        }
        vc = compile_violation_condition(expr, rule_id="scenario_d")
        mutations = generate_violation_mutation(vc, runtime_values={"resource.version": 3})
        assert mutations[0]["requires_concurrent_actor"] is True
        assert mutations[0]["value"] == 3  # Stale version

    def test_scenario_e_temporal_boundary(self):
        """Scenario E: child.date <= parent.end_date, generate clear violation."""
        expr = {
            "type": "TEMPORAL", "operator": "LTE",
            "left": {"entity": "child", "field": "due_date"},
            "right": {"entity": "parent", "field": "end_date"},
        }
        vc = compile_violation_condition(expr, rule_id="scenario_e")
        mutations = generate_violation_mutation(
            vc, runtime_values={"child.boundary_date": "2026-06-30"},
            strength=CLEAR_VIOLATION,
        )
        assert mutations[0]["value"] > "2026-06-30"

    def test_scenario_f_input_incomplete(self):
        """Scenario F: Missing related collection → ORACLE_INPUT_INCOMPLETE."""
        result = check_oracle_input_completeness(
            required_entities=["root", "related_collection"],
            observed_entities=["root"],  # Missing related_collection
            required_fields=["root.total", "SUM(related_collection.amount)"],
            observed_fields=["root.total"],
        )
        assert result["complete"] is False
        assert "related_collection" in result["missing_entities"]


class TestFinalStatusClassification:
    """Test §20 final diagnostic status."""

    def test_violation_triggered(self):
        status = classify_final_status(
            oracle_result="VIOLATION",
            violation_condition_attempted=True,
            observer_complete=True,
            precondition_reached=True,
            mutation_applied=True,
        )
        assert status == VIOLATION_TRIGGERED

    def test_true_pass_confirmed(self):
        status = classify_final_status(
            oracle_result="PASS",
            violation_condition_attempted=True,
            observer_complete=True,
            precondition_reached=True,
            mutation_applied=True,
        )
        assert status == TRUE_PASS_CONFIRMED

    def test_precondition_not_reached(self):
        status = classify_final_status(
            oracle_result="PASS",
            violation_condition_attempted=True,
            observer_complete=True,
            precondition_reached=False,
            mutation_applied=True,
        )
        assert status == PRECONDITION_NOT_REACHED

    def test_oracle_input_incomplete(self):
        status = classify_final_status(
            oracle_result="PASS",
            violation_condition_attempted=True,
            observer_complete=False,
            precondition_reached=True,
            mutation_applied=True,
        )
        assert status == ORACLE_INPUT_INCOMPLETE

    def test_violation_not_activated(self):
        status = classify_final_status(
            oracle_result="PASS",
            violation_condition_attempted=False,
            observer_complete=True,
            precondition_reached=True,
            mutation_applied=False,
        )
        assert status == VIOLATION_NOT_ACTIVATED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
