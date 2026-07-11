from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center.closed_loop_feedback import (
    _extract_pattern,
    _finding_operation,
    build_closed_loop_context,
)


def test_closed_loop_pattern_uses_nested_experiment_request_identity() -> None:
    finding = {
        "title": "[ContractOracle] authorization",
        "category": "authorization_access_control",
        "raw_evidence": {
            "request_raw": {"method": "GET", "path": "/api/reports/sales"},
        },
    }

    assert _finding_operation(finding) == ("GET", "/api/reports/sales", "reports")
    pattern = _extract_pattern(finding)
    assert pattern["entity"] == "reports"
    assert pattern["signature"].endswith(":GET:reports")


def test_closed_loop_pattern_handles_missing_path_without_index_error() -> None:
    pattern = _extract_pattern({"title": "source-backed finding", "category": "state"})
    assert pattern["entity"] == "unknown"
    assert "unknown" in pattern["signature"]


def test_closed_loop_corrupt_history_fails_observably(tmp_path: Path) -> None:
    history = tmp_path / "platform_outputs" / "demo" / "closed_loop" / "bug_patterns.json"
    history.parent.mkdir(parents=True)
    history.write_text("{not-json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="closed_loop_history_invalid"):
        build_closed_loop_context("demo", tmp_path, [])
