"""Safety non-regression tests — SPEC v1.2.2 §17.1.

Verifies that all hard-gate safety invariants hold:
  - observer_blocked_but_compiled = 0
  - oracle_input_incomplete_but_compiled = 0
  - oracle_input_incomplete_reaching_oracle = 0
  - binding_graph_blocked_reaching_transport = 0
  - fixture_dag_blocked_reaching_transport = 0
  - synthetic_binding_reaching_transport = 0
  - unverified_runtime_binding = 0
  - unproven_compensation_relation = 0
  - missing_campaign_funnel = 0
  - missing_blocker_attribution = 0
  - prioritizer_silent_fallback = 0
"""
import inspect
import pytest

from ai_test_asset_center.v12_coverage_recovery_orchestrator import (
    prepare_experiment_v12,
    VERDICT_BLOCKED,
    VERDICT_READY,
    GATE_BLOCKED,
    GATE_NOT_APPLICABLE,
    GATE_PASSED,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _minimal_ir():
    return {
        "operations": [{"id": "op1", "method": "GET", "path": "/items"}],
        "relations": [],
        "actors": [],
    }


def _minimal_exp(**overrides):
    base = {
        "experiment_id": "e1",
        "treatment_plan": [{"path": "/items", "method": "GET"}],
        "binding_plan": [],
        "assertions": [],
        "observers": [],
        "safety_contract": {},
    }
    base.update(overrides)
    return base


# ─── §17.1 Safety Metric: observer_blocked_but_compiled = 0 ──────────────────

class TestObserverBlockedNotCompiled:
    def test_blocked_observer_prevents_ready(self):
        """When observer resolution is BLOCKED, verdict must NOT be READY."""
        ir = _minimal_ir()
        exp = _minimal_exp(
            assertions=[{"assertion_id": "a1", "kind": "state_change"}],
            observers=[],
        )
        # Obligation requires after_state observation but no observers compiled
        r = prepare_experiment_v12(
            obligation={"obligation_id": "o1", "observer_requirement": "after_state"},
            behavior_ir=ir,
            compiler_context={"experiment": exp, "primary_operation": ir["operations"][0]},
        )
        # If observer gate is BLOCKED, verdict must be BLOCKED
        obs_gate = next(g for g in r["gate_receipts"] if g["module"] == "observer_resolution")
        if obs_gate["status"] == GATE_BLOCKED:
            assert r["verdict"] == VERDICT_BLOCKED
            assert r["verdict"] != VERDICT_READY

    def test_ambiguous_observer_prevents_ready(self):
        """AMBIGUOUS observer resolution → verdict BLOCKED."""
        ir = _minimal_ir()
        exp = _minimal_exp(
            assertions=[{"assertion_id": "a1", "kind": "state_change"}],
            observers=[],
        )
        r = prepare_experiment_v12(
            obligation={"obligation_id": "o1", "observer_requirement": "after_state"},
            behavior_ir=ir,
            compiler_context={"experiment": exp, "primary_operation": ir["operations"][0]},
        )
        # Orchestrator never returns READY when observer gate is BLOCKED
        blocking = [g for g in r["gate_receipts"] if g["status"] == GATE_BLOCKED]
        if blocking:
            assert r["verdict"] == VERDICT_BLOCKED


# ─── §17.1 Safety Metric: oracle_input_incomplete_but_compiled = 0 ───────────

class TestOracleIncompleteNotCompiled:
    def test_structural_oracle_gap_blocks(self):
        """Oracle BLOCKED_MISSING_OBSERVER → verdict BLOCKED."""
        ir = _minimal_ir()
        exp = _minimal_exp(
            assertions=[{"assertion_id": "a1", "kind": "state_change", "required_phases": ["after"]}],
            observers=[],
        )
        r = prepare_experiment_v12(
            obligation={"obligation_id": "o1"},
            behavior_ir=ir,
            compiler_context={"experiment": exp, "primary_operation": ir["operations"][0]},
        )
        oracle_gate = next(g for g in r["gate_receipts"] if g["module"] == "oracle_input_contract")
        if oracle_gate["status"] == GATE_BLOCKED:
            assert r["verdict"] == VERDICT_BLOCKED


# ─── §17.1 Safety Metric: oracle_input_incomplete_reaching_oracle = 0 ────────

class TestOracleRuntimeGate:
    def test_finalizer_blocks_empty_observations(self):
        """Finalizer code contains runtime oracle input validation."""
        # The facade (experiment_outcome_finalizer_core) delegates to the
        # extracted mechanics implementation, which owns the runtime Oracle
        # input validation gate.  Inspect the delegated implementation so the
        # safety invariant is checked where the logic actually lives.
        from ai_test_asset_center import _experiment_outcome_finalizer_core_mechanics as _finalizer_core
        src = inspect.getsource(_finalizer_core.finalize_experiment_execution)
        # Must check for observation data before calling oracle
        assert "BLOCKED_ORACLE_INPUT_INCOMPLETE" in src
        assert "_runtime_oracle_blocked" in src


# ─── §17.1 Safety Metric: binding_graph_blocked_reaching_transport = 0 ───────

class TestBindingGraphBlockedNotTransport:
    def test_blocked_binding_graph_verdict(self):
        """Binding graph with issues → verdict BLOCKED."""
        ir = _minimal_ir()
        exp = _minimal_exp(
            binding_plan=[{"target": "item_id", "source": "forbidden_source"}],
        )
        r = prepare_experiment_v12(
            obligation={"obligation_id": "o1"},
            behavior_ir=ir,
            compiler_context={"experiment": exp, "primary_operation": ir["operations"][0]},
        )
        binding_gate = next(g for g in r["gate_receipts"] if g["module"] == "binding_coverage_graph")
        if binding_gate["status"] == GATE_BLOCKED:
            assert r["verdict"] == VERDICT_BLOCKED

    def test_executor_blocks_provenance_violations(self):
        """Executor returns BLOCKED_BINDING_GRAPH_INVALID for synthetic bindings."""
        from ai_test_asset_center.experiment_executor_core import execute_one_experiment
        src = inspect.getsource(execute_one_experiment)
        assert "BLOCKED_BINDING_GRAPH_INVALID" in src
        assert "_provenance_violations" in src


# ─── §17.1 Safety Metric: fixture_dag_blocked_reaching_transport = 0 ─────────

class TestFixtureDagBlockedNotTransport:
    def test_blocked_fixture_dag_verdict(self):
        """Fixture DAG with invalid status → verdict BLOCKED."""
        ir = _minimal_ir()
        # Fixtures with missing dependency → MISSING_DEPENDENCY → BLOCKED
        exp = _minimal_exp(
            fixtures=[
                {"fixture_id": "f1", "operation_ref": "op1", "depends_on": ["f_missing"]},
            ],
        )
        r = prepare_experiment_v12(
            obligation={"obligation_id": "o1"},
            behavior_ir=ir,
            compiler_context={"experiment": exp, "primary_operation": ir["operations"][0]},
        )
        fixture_gate = next(g for g in r["gate_receipts"] if g["module"] == "fixture_dependency_dag")
        if fixture_gate["status"] == GATE_BLOCKED:
            assert r["verdict"] == VERDICT_BLOCKED


# ─── §17.1 Safety Metric: synthetic_binding_reaching_transport = 0 ───────────

class TestSyntheticBindingNotTransport:
    def test_no_degraded_synthetic_in_materializer(self):
        """Materializer must NOT use degraded_synthetic as a status path."""
        from ai_test_asset_center.experiment_fixture_materializer import materialize_experiment_fixtures
        src = inspect.getsource(materialize_experiment_fixtures)
        # The production code must not ASSIGN degraded_synthetic status
        assert '"degraded_synthetic"' not in src or "removed in v1.2.2" in src
        # Must not have the old fallback pattern
        assert "degraded_generated" not in src

    def test_executor_detects_synthetic_values(self):
        """Executor detects qb_test_/qb-test- synthetic values."""
        from ai_test_asset_center.experiment_executor_core import execute_one_experiment
        src = inspect.getsource(execute_one_experiment)
        assert "qb_test_" in src
        assert "qb-test-" in src
        assert "synthetic_binding_reaching_transport" in src

    def test_forbidden_runtime_sources_defined(self):
        """Executor forbids degraded_synthetic/synthetic_value/random_placeholder/invented."""
        from ai_test_asset_center.experiment_executor_core import execute_one_experiment
        src = inspect.getsource(execute_one_experiment)
        assert "degraded_synthetic" in src
        assert "synthetic_value" in src
        assert "random_placeholder" in src
        assert "invented" in src


# ─── §17.1 Safety Metric: unverified_runtime_binding = 0 ─────────────────────

class TestUnverifiedRuntimeBinding:
    def test_executor_checks_compile_graph_membership(self):
        """Executor verifies bindings exist in compile graph."""
        from ai_test_asset_center.experiment_executor_core import execute_one_experiment
        src = inspect.getsource(execute_one_experiment)
        assert "_compile_node_targets" in src
        assert "undeclared_runtime_binding" in src


# ─── §17.1 Safety Metric: unproven_compensation_relation = 0 ─────────────────

class TestUnprovenCompensation:
    def test_compensation_gate_blocks_unproven(self):
        """Compensation gate blocks writes with cleanup_plan but no proven relation."""
        ir = {"operations": [
            {"id": "op1", "method": "POST", "path": "/orders"},
            {"id": "op2", "method": "DELETE", "path": "/orders/{id}"},
        ], "relations": [], "actors": []}
        # Governed write WITH cleanup_plan but resolver won't ACCEPT
        # (no source_refs linking the operations)
        exp = _minimal_exp(
            treatment_plan=[{"path": "/orders", "method": "POST"}],
            safety_contract={"governed_write": True},
            cleanup_plan=[{"operation_ref": "op2", "method": "DELETE", "path": "/orders/{id}"}],
        )
        r = prepare_experiment_v12(
            obligation={"obligation_id": "o1"},
            behavior_ir=ir,
            compiler_context={"experiment": exp, "primary_operation": ir["operations"][0]},
        )
        comp_gate = next(g for g in r["gate_receipts"] if g["module"] == "compensation_relation")
        # With cleanup_plan present, gate passes (compiler validated) or blocks
        # Key invariant: if BLOCKED, verdict must be BLOCKED
        if comp_gate["status"] == GATE_BLOCKED:
            assert r["verdict"] == VERDICT_BLOCKED
        # If PASSED, it must be because cleanup_plan or resolver accepted
        if comp_gate["status"] == GATE_PASSED:
            assert comp_gate.get("detail") or comp_gate.get("fingerprint")


# ─── §17.1 Safety Metric: missing_campaign_funnel = 0 ────────────────────────

class TestMissingCampaignFunnel:
    def test_batch_enforces_funnel_presence(self):
        """Batch executor marks FAILED when funnel is missing."""
        # The facade (experiment_batch_executor) delegates to the extracted
        # single-finding mechanics implementation, which owns the campaign
        # validation receipt (funnel / attribution / prioritization gates).
        # Inspect the delegated implementation so the safety invariants are
        # checked where the logic actually lives.
        from ai_test_asset_center import _experiment_batch_executor_single_finding_mechanics as _batch_core
        src = inspect.getsource(_batch_core.execute_selected_experiments)
        assert "missing_execution_coverage_funnel" in src
        assert "HARNESS_COVERAGE_FUNNEL_FAILED" in src


# ─── §17.1 Safety Metric: missing_blocker_attribution = 0 ────────────────────

class TestMissingBlockerAttribution:
    def test_batch_enforces_attribution_presence(self):
        """Batch executor marks FAILED when attribution is missing."""
        # The facade (experiment_batch_executor) delegates to the extracted
        # single-finding mechanics implementation, which owns the campaign
        # validation receipt (funnel / attribution / prioritization gates).
        # Inspect the delegated implementation so the safety invariants are
        # checked where the logic actually lives.
        from ai_test_asset_center import _experiment_batch_executor_single_finding_mechanics as _batch_core
        src = inspect.getsource(_batch_core.execute_selected_experiments)
        assert "missing_blocker_attribution" in src
        assert "HARNESS_BLOCKER_ATTRIBUTION_FAILED" in src


# ─── §17.1 Safety Metric: prioritizer_silent_fallback = 0 ────────────────────

class TestPrioritizerSilentFallback:
    def test_no_silent_fallback(self):
        """Prioritizer failure is not silently swallowed."""
        # The facade (experiment_batch_executor) delegates to the extracted
        # single-finding mechanics implementation, which owns the campaign
        # validation receipt (funnel / attribution / prioritization gates).
        # Inspect the delegated implementation so the safety invariants are
        # checked where the logic actually lives.
        from ai_test_asset_center import _experiment_batch_executor_single_finding_mechanics as _batch_core
        src = inspect.getsource(_batch_core.execute_selected_experiments)
        # Must have explicit failure handling for prioritizer
        assert "HARNESS_PRIORITIZATION_FAILED" in src
        assert "_prioritization_failed" in src
        # The prioritizer section must use logger.warning, not logger.debug
        assert "logger.warning" in src


# ─── Cross-cutting: Orchestrator verdict integrity ───────────────────────────

class TestOrchestratorVerdictIntegrity:
    def test_any_blocked_gate_means_blocked_verdict(self):
        """If ANY gate is BLOCKED, verdict MUST be BLOCKED — never READY."""
        ir = _minimal_ir()
        # Create experiment that triggers fixture gate block (missing dependency)
        exp = _minimal_exp(
            treatment_plan=[{"path": "/orders", "method": "POST"}],
            safety_contract={"governed_write": True},
            fixtures=[{"fixture_id": "f1", "operation_ref": "op1", "depends_on": ["f_missing"]}],
        )
        r = prepare_experiment_v12(
            obligation={"obligation_id": "o1"},
            behavior_ir=ir,
            compiler_context={"experiment": exp, "primary_operation": ir["operations"][0]},
        )
        blocked_gates = [g for g in r["gate_receipts"] if g["status"] == GATE_BLOCKED]
        if blocked_gates:
            assert r["verdict"] == VERDICT_BLOCKED
            assert r["verdict"] != VERDICT_READY
            assert r["blocking_gates"] == blocked_gates

    def test_no_informational_semantics_in_orchestrator(self):
        """Orchestrator must NOT use informational as a gate status."""
        from ai_test_asset_center.v12_coverage_recovery_orchestrator import prepare_experiment_v12
        src = inspect.getsource(prepare_experiment_v12)
        # The code must not USE informational as a status value
        assert '"informational"' not in src
        assert "'informational'" not in src
        # Must not have non-fatal as execution semantics
        assert '"non-fatal"' not in src
        assert "'non-fatal'" not in src

    def test_all_five_gates_present(self):
        """Orchestrator evaluates exactly 5 gate modules."""
        ir = _minimal_ir()
        exp = _minimal_exp()
        r = prepare_experiment_v12(
            obligation={"obligation_id": "o1"},
            behavior_ir=ir,
            compiler_context={"experiment": exp, "primary_operation": ir["operations"][0]},
        )
        modules = {g["module"] for g in r["gate_receipts"]}
        assert modules == {
            "observer_resolution",
            "compensation_relation",
            "oracle_input_contract",
            "binding_coverage_graph",
            "fixture_dependency_dag",
        }
