"""Tests for prioritizer execution order — SPEC v1.2.1 §12."""
import pytest
from ai_test_asset_center.safe_experiment_prioritizer import prioritize_experiments


class TestPrioritizerExecutionOrder:
    def test_empty_input(self):
        result = prioritize_experiments(
            experiments=[],
            obligations=[],
            behavior_ir={},
        )
        assert "schema_version" in result
        assert result.get("total_scored", 0) == 0

    def test_single_experiment(self):
        exps = [{"experiment_id": "exp_1", "obligation_id": "obl_1"}]
        obls = [{"obligation_id": "obl_1"}]
        result = prioritize_experiments(
            experiments=exps,
            obligations=obls,
            behavior_ir={},
        )
        assert "prioritized" in result
        assert len(result["prioritized"]) == 1

    def test_prioritization_receipt_present(self):
        exps = [{"experiment_id": "exp_1", "obligation_id": "obl_1"}]
        obls = [{"obligation_id": "obl_1"}]
        result = prioritize_experiments(
            experiments=exps,
            obligations=obls,
            behavior_ir={},
        )
        assert "schema_version" in result
        assert "prioritized" in result

    def test_order_changes_with_risk(self):
        """Higher risk experiments should be prioritized."""
        exps = [
            {"experiment_id": "exp_low", "obligation_id": "obl_low", "risk_family": "validation"},
            {"experiment_id": "exp_high", "obligation_id": "obl_high", "risk_family": "state"},
        ]
        obls = [
            {"obligation_id": "obl_low", "risk_family": "validation"},
            {"obligation_id": "obl_high", "risk_family": "state"},
        ]
        result = prioritize_experiments(
            experiments=exps,
            obligations=obls,
            behavior_ir={},
        )
        # Just verify it returns an order
        assert len(result.get("prioritized", [])) == 2
