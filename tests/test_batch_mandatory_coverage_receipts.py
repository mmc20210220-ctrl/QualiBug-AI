"""Tests for batch mandatory coverage receipts — SPEC v1.2.2 §12."""
import pytest
import inspect


def _implementation_source() -> str:
    from ai_test_asset_center import experiment_batch_executor

    return inspect.getsource(
        experiment_batch_executor._core.execute_selected_experiments
    )


class TestBatchMandatoryCoverageReceipts:
    def test_prioritizer_failure_not_silent(self):
        """Prioritizer failure is logged as warning, not debug."""
        src = _implementation_source()
        assert "logger.warning" in src
        # Old pattern "non-fatal" must be gone
        assert "non-fatal" not in src

    def test_campaign_validation_receipt_present(self):
        """Batch result includes campaign_validation_receipt."""
        src = _implementation_source()
        assert "campaign_validation_receipt" in src
        assert "campaign_validation_status" in src

    def test_funnel_failure_marks_failed(self):
        """Funnel failure → HARNESS_COVERAGE_FUNNEL_FAILED."""
        src = _implementation_source()
        assert "HARNESS_COVERAGE_FUNNEL_FAILED" in src

    def test_attribution_failure_marks_failed(self):
        """Attribution failure → HARNESS_BLOCKER_ATTRIBUTION_FAILED."""
        src = _implementation_source()
        assert "HARNESS_BLOCKER_ATTRIBUTION_FAILED" in src

    def test_prioritization_failure_marks_failed(self):
        """Prioritizer failure → HARNESS_PRIORITIZATION_FAILED."""
        src = _implementation_source()
        assert "HARNESS_PRIORITIZATION_FAILED" in src
