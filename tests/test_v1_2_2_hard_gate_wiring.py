"""Tests for v1.2.2 hard gate wiring — SPEC v1.2.2 §13."""
import pytest
import inspect


class TestHardGateWiring:
    def test_compiler_calls_orchestrator(self):
        """compile_experiment_for_obligation calls prepare_experiment_v12."""
        from ai_test_asset_center.experiment_compiler_obligation_core import compile_experiment_for_obligation
        src = inspect.getsource(compile_experiment_for_obligation)
        assert "prepare_experiment_v12" in src

    def test_compiler_blocks_on_orchestrator_verdict(self):
        """Compiler returns blocked_experiment when orchestrator verdict is BLOCKED."""
        from ai_test_asset_center.experiment_compiler_obligation_core import compile_experiment_for_obligation
        src = inspect.getsource(compile_experiment_for_obligation)
        assert "BLOCKED" in src
        assert "blocked_experiment" in src

    def test_executor_validates_binding_provenance(self):
        """Executor validates runtime binding provenance."""
        from ai_test_asset_center.experiment_executor_core import execute_one_experiment
        src = inspect.getsource(execute_one_experiment)
        assert "BLOCKED_BINDING_GRAPH_INVALID" in src

    def _batch_wiring_src(self):
        """Source of the module that owns batch executor wiring.

        After the facade -> mechanics split, ``execute_selected_experiments`` in
        ``experiment_batch_executor`` is a thin facade that delegates to
        ``execute_selected_experiments_concurrent``. The prioritize / funnel /
        attribution wiring now lives in the concurrent scheduler (and its
        ``_apply_global_budget`` / ``_merge_group_batches`` helpers), not in the
        facade body.
        """
        from ai_test_asset_center import experiment_batch_concurrent_scheduler
        return inspect.getsource(experiment_batch_concurrent_scheduler)

    def test_batch_calls_prioritizer(self):
        """Batch executor calls prioritize_experiments."""
        assert "prioritize_experiments" in self._batch_wiring_src()

    def test_batch_calls_funnel(self):
        """Batch executor calls build_execution_coverage_funnel."""
        assert "build_execution_coverage_funnel" in self._batch_wiring_src()

    def test_batch_calls_attribution(self):
        """Batch executor calls attribute_all_blockers."""
        assert "attribute_all_blockers" in self._batch_wiring_src()

    def test_orchestrator_version_v2(self):
        """Orchestrator outputs v2 schema."""
        from ai_test_asset_center.v12_coverage_recovery_orchestrator import prepare_experiment_v12
        ir = {"operations": [{"id": "op1", "method": "GET", "path": "/x"}], "relations": [], "actors": []}
        exp = {"experiment_id": "e1", "treatment_plan": [], "binding_plan": [],
               "assertions": [], "observers": [], "safety_contract": {}}
        r = prepare_experiment_v12(
            obligation={"obligation_id": "o1"}, behavior_ir=ir,
            compiler_context={"experiment": exp, "primary_operation": ir["operations"][0]},
        )
        assert r["schema_version"] == "qualibug.v12-coverage-recovery-orchestrator.v2"
        assert r["coverage_recovery_version"] == "v1.2.2"
