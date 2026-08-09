"""Tests for the v12 schema field mapping in the learning report tools.

The dashboard and enhanced-signals tools previously read legacy fields
(``high_value_summary.*``, ``benchmark_metrics.total_probes``,
``probe_execution_result``) that mainline v12 scans never write, so their
metrics were always zero. These tests pin the authoritative-field mapping.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from learning_effectiveness_dashboard import compute_round_metrics  # noqa: E402


def _v12_scan_result() -> dict:
    return {
        "data": {
            "benchmark_metrics": {},
            "high_value_summary": {},
            "formal_count_projection": {
                "formal_customer_deliverable_count": 8,
                "canonical_defect_count": 8,
            },
            "pipeline_health": {
                "executed_obligation_count": 41,
                "blocked_obligation_count": 91,
            },
            "obligation_attempt_ledger": {"execution_count": 41},
        },
        "timestamp": 1786000000,
    }


def test_compute_round_metrics_reads_v12_fields() -> None:
    metrics = compute_round_metrics(_v12_scan_result())
    assert metrics.confirmed_bugs == 8
    assert metrics.total_probes == 41
    assert metrics.failed_probes == 91
    assert metrics.round_num == 1


def test_compute_round_metrics_legacy_fallback_unchanged() -> None:
    result = {
        "data": {
            "benchmark_metrics": {"total_probes": 10, "failed_probes": 2},
            "high_value_summary": {"total_confirmed_bugs": 3},
        },
        "timestamp": 1786000000,
    }
    metrics = compute_round_metrics(result)
    assert metrics.confirmed_bugs == 3
    assert metrics.total_probes == 10
    assert metrics.failed_probes == 2
