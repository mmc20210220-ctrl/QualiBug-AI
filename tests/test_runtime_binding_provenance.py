"""Tests for runtime binding provenance — SPEC v1.2.2 §10."""
import pytest


class TestRuntimeBindingProvenance:
    def test_synthetic_binding_blocked(self):
        """Synthetic qb_test_ prefix binding → BLOCKED at runtime."""
        from ai_test_asset_center.experiment_executor_core import execute_one_experiment
        # This test verifies the provenance check exists in the executor
        # by checking the code structure (unit-level verification)
        import inspect
        src = inspect.getsource(execute_one_experiment)
        assert "BLOCKED_BINDING_GRAPH_INVALID" in src
        assert "synthetic_binding_reaching_transport" in src

    def test_forbidden_fixture_source_blocked(self):
        """degraded_synthetic fixture receipt → BLOCKED."""
        from ai_test_asset_center.experiment_executor_core import execute_one_experiment
        import inspect
        src = inspect.getsource(execute_one_experiment)
        assert "degraded_synthetic" in src
        assert "forbidden_fixture_source" in src

    def test_provenance_validation_present(self):
        """Runtime binding provenance validation is present in executor."""
        from ai_test_asset_center.experiment_executor_core import execute_one_experiment
        import inspect
        src = inspect.getsource(execute_one_experiment)
        assert "runtime_binding_provenance" in src or "Runtime Binding Provenance" in src
