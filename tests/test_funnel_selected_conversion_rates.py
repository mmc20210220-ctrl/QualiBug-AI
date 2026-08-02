"""selected_to_* rates must use SELECTED-only numerators."""
from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.discovery_funnel import (
    _build_conversion_rates,
    build_funnel_report,
)


def test_selected_to_compiled_uses_selected_compile_success_count() -> None:
    rates = _build_conversion_rates(
        {
            "generated_count": 246,
            "selected_count": 32,
            "selected_compile_success_count": 32,
            "compile_success_count": 109,
            "execution_count": 28,
            "accounted_execution_count": 96,
            "oracle_count": 28,
            "oracle_violation_count": 2,
            "customer_deliverable_finding_count": 2,
        }
    )
    selected_to_compiled = next(
        row for row in rates["rates"] if row["name"] == "selected_to_compiled"
    )
    assert selected_to_compiled["status"] == "MEASURED"
    assert selected_to_compiled["numerator_count"] == 32
    assert selected_to_compiled["denominator_count"] == 32
    assert selected_to_compiled["rate"] == 1.0
    assert rates["status"] == "PASS"


def test_candidate_ledger_replay_no_longer_fails_selected_conversion() -> None:
    path = Path(
        "_funnel_runs/20260802_candidate_binding_compensates/scan_result.json"
    )
    if not path.exists():
        return
    report = build_funnel_report(
        json.loads(path.read_text(encoding="utf-8"))
    )
    selected_to_compiled = next(
        row
        for row in report["conversion_rates"]["rates"]
        if row["name"] == "selected_to_compiled"
    )
    assert selected_to_compiled["status"] == "MEASURED"
    assert selected_to_compiled["rate"] == 1.0
    assert report["conversion_rates"]["status"] == "PASS"
    assert report["conservation"]["status"] == "PASS"
