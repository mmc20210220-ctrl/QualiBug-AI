"""Tests for no synthetic binding at runtime — SPEC v1.2.2 §9."""
import pytest
import inspect


class TestNoSyntheticBindingRuntime:
    def test_degraded_synthetic_removed_from_materializer(self):
        """degraded_synthetic fallback is removed from fixture materializer."""
        from ai_test_asset_center.experiment_fixture_materializer import materialize_experiment_fixtures
        src = inspect.getsource(materialize_experiment_fixtures)
        # The old fallback code "proceed with it and let target respond naturally" must be gone
        assert "proceed with it and let target respond naturally" not in src

    def test_synthetic_value_not_written_to_runtime_bindings(self):
        """synthetic_value is NOT written to runtime_bindings."""
        from ai_test_asset_center.experiment_fixture_materializer import materialize_experiment_fixtures
        src = inspect.getsource(materialize_experiment_fixtures)
        # The old pattern runtime_bindings[target] = str(_synth_val) must be gone
        assert "runtime_bindings[target] = str(_synth_val)" not in src

    def test_blocked_status_for_no_source(self):
        """Binding without verified source gets blocked status."""
        # The blocking logic now lives in the fixture-materializer core after the
        # facade split; the public facade delegates to it.
        from ai_test_asset_center.experiment_fixture_materializer_core import (
            materialize_experiment_fixtures,
        )
        src = inspect.getsource(materialize_experiment_fixtures)
        assert "binding_has_no_verified_runtime_source" in src

    def test_degraded_synthetic_not_in_preferred_statuses(self):
        """degraded_synthetic is not in preferred fixture statuses."""
        from ai_test_asset_center import experiment_fixture_materializer
        src = inspect.getsource(experiment_fixture_materializer)
        # Check that degraded_synthetic is NOT in the preferred statuses set
        assert '"degraded_synthetic", "degraded_generated", "bound"' not in src
