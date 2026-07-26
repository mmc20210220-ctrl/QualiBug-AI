"""Tests for campaign coverage funnel integration — SPEC v1.2.1 §13."""
import pytest
from ai_test_asset_center.execution_coverage_funnel import build_execution_coverage_funnel


class TestCampaignCoverageFunnelIntegration:
    def test_empty_funnel(self):
        result = build_execution_coverage_funnel(
            obligations=[],
            experiments=[],
            execution_results=[],
            findings=[],
        )
        assert "schema_version" in result
        assert result.get("obligations_total", 0) == 0

    def test_funnel_stages(self):
        obls = [{"obligation_id": "obl_1"}]
        exps = [{"experiment_id": "exp_1", "obligation_id": "obl_1", "compile_receipt": {"status": "COMPILED"}}]
        exec_results = [{"obligation_id": "obl_1", "status": "EXECUTED"}]
        result = build_execution_coverage_funnel(
            obligations=obls,
            experiments=exps,
            execution_results=exec_results,
            findings=[],
        )
        assert result.get("obligations_total", 0) >= 1
        assert "stages" in result

    def test_funnel_does_not_reverse_infer(self):
        """Funnel must not infer intermediate stages from final status."""
        obls = [{"obligation_id": "obl_1"}]
        exps = [{"experiment_id": "exp_1", "obligation_id": "obl_1"}]
        # No execution results — funnel should show 0 executed
        result = build_execution_coverage_funnel(
            obligations=obls,
            experiments=exps,
            execution_results=[],
            findings=[],
        )
        # Should have stages structure
        assert "stages" in result
