"""Machine-checkable DERIVATION_FAILED signal in family_coverage_ledger.

When a contract/coverage derivation step crashes (FAILED receipt) the
breadth loss for the families that step feeds must be reported as
DERIVATION_FAILED (a crash), NOT as "no contract in source" (NOT_REQUESTED).
This mirrors the real pipeline where discovery_runtime_planning registers the
open-class families via the formal-surface installers.
"""
from __future__ import annotations

import ai_test_asset_center.discovery_runtime_semantic_binding  # registers open-class families
from ai_test_asset_center.family_coverage_ledger import build_family_coverage_ledger
from ai_test_asset_center.test_obligation import canonical_risk_families


def test_open_class_families_registered() -> None:
    fams = set(canonical_risk_families())
    assert "performance_latency" in fams
    assert "stability_reliability" in fams
    assert "event_delivery_consistency" in fams


def test_derivation_failure_marks_family() -> None:
    ledger = build_family_coverage_ledger(
        {"obligations": []},
        derivation_failures={"performance_latency": "ValueError: boom"},
        coverage_unit_failed=True,
    )
    entries = {e["risk_family"]: e for e in ledger["entries"]}
    perf = entries["performance_latency"]
    assert perf["status"] == "DERIVATION_FAILED"
    assert perf["gap_reason_code"] == "DERIVATION_FAILED"
    assert "boom" in perf["gap_reason"]
    # A family whose derivation did NOT fail stays honestly NOT_REQUESTED.
    assert entries["stability_reliability"]["status"] == "NOT_REQUESTED"
    assert ledger["families_derivation_failed"] == 1
    assert ledger["coverage_unit_derivation_failed"] is True
    assert "DERIVATION_FAILED" in ledger["summary"]
    assert "coverage_unit" in ledger["summary"]


def test_applicability_overrides_derivation_failure() -> None:
    ledger = build_family_coverage_ledger(
        {"obligations": [{"risk_family": "performance_latency"}]},
        derivation_failures={"performance_latency": "ValueError: boom"},
    )
    entries = {e["risk_family"]: e for e in ledger["entries"]}
    assert entries["performance_latency"]["status"] == "APPLIED"
    assert ledger["families_derivation_failed"] == 0


def test_backward_compatible_default() -> None:
    ledger = build_family_coverage_ledger({"obligations": []})
    assert ledger["families_derivation_failed"] == 0
    assert ledger["coverage_unit_derivation_failed"] is False
