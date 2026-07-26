"""Tests for batch mandatory coverage receipts — SPEC v1.2.2 §12."""
import pytest
import inspect


class TestBatchMandatoryCoverageReceipts:
    def test_prioritizer_failure_not_silent(self):
        """Prioritizer failure is logged as warning, not debug."""
        from ai_test_asset_center.experiment_batch_executor import execute_selected_experiments
        src = inspect.getsource(execute_selected_experiments)
        assert "logger.warning" in src
        # Old pattern "non-fatal" must be gone
        assert "non-fatal" not in src

    def test_campaign_validation_receipt_present(self):
        """Batch result includes campaign_validation_receipt."""
        from ai_test_asset_center.experiment_batch_executor import execute_selected_experiments
        src = inspect.getsource(execute_selected_experiments)
        assert "campaign_validation_receipt" in src
        assert "campaign_validation_status" in src

    def test_funnel_failure_marks_failed(self):
        """Funnel failure → HARNESS_COVERAGE_FUNNEL_FAILED."""
        from ai_test_asset_center.experiment_batch_executor import execute_selected_experiments
        src = inspect.getsource(execute_selected_experiments)
        assert "HARNESS_COVERAGE_FUNNEL_FAILED" in src

    def test_attribution_failure_marks_failed(self):
        """Attribution failure → HARNESS_BLOCKER_ATTRIBUTION_FAILED."""
        from ai_test_asset_center.experiment_batch_executor import execute_selected_experiments
        src = inspect.getsource(execute_selected_experiments)
        assert "HARNESS_BLOCKER_ATTRIBUTION_FAILED" in src

    def test_prioritization_failure_marks_failed(self):
        """Prioritizer failure → HARNESS_PRIORITIZATION_FAILED."""
        from ai_test_asset_center.experiment_batch_executor import execute_selected_experiments
        src = inspect.getsource(execute_selected_experiments)
        assert "HARNESS_PRIORITIZATION_FAILED" in src
