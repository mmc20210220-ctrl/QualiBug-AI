"""P5 Regression Lifecycle Tracking — verify lifecycle state is written into regression_history.json.

Tests:
  1. _lifecycle_for_status maps status → lifecycle correctly
  2. _append_regression_history writes lifecycle field to each item
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def test_lifecycle_for_status_mappings() -> None:
    """Unit test: every known status maps to the correct lifecycle."""
    from ai_test_asset_center.regression_runner import _lifecycle_for_status

    assert _lifecycle_for_status("passed") == "regression_passed"
    assert _lifecycle_for_status("failed") == "regression_failed"
    assert _lifecycle_for_status("needs_review") == "pending_review"
    assert _lifecycle_for_status("skipped") == "lifecycle_skipped"
    assert _lifecycle_for_status("unknown_status") == "lifecycle_unknown"
    assert _lifecycle_for_status("") == "lifecycle_unknown"


def test_append_regression_history_includes_lifecycle(tmp_path: Path) -> None:
    """Integration: _append_regression_history writes lifecycle on every item."""
    from ai_test_asset_center.regression_runner import _append_regression_history, _write_json

    project = "p5_lifecycle_test"
    # Build a minimal regression result with items covering all lifecycle states.
    result: dict[str, Any] = {
        "summary": {
            "generated_at": "2025-01-01T00:00:00Z",
            "suite_mode": "release",
            "suite_mode_label": "Release",
            "total_probe_count": 5,
            "executed_count": 4,
            "passed_count": 2,
            "failed_count": 1,
            "needs_review_count": 1,
            "skipped_count": 1,
        },
        "ci_feedback": {
            "gate_status": "manual_approval_required",
            "ci_message": "review needed",
        },
        "items": [
            {
                "issue_id": "ISS-001",
                "regression_probe_id": "probe_1",
                "title": "Login should not accept empty password",
                "path": "/api/login",
                "method": "POST",
                "severity": "P1",
                "status": "passed",
                "reason": "Status 200 matches expected.",
            },
            {
                "issue_id": "ISS-002",
                "regression_probe_id": "probe_2",
                "title": "Order quantity must be positive",
                "path": "/api/orders",
                "method": "POST",
                "severity": "P0",
                "status": "failed",
                "reason": "Status 201 does not match expected 400.",
            },
            {
                "issue_id": "ISS-003",
                "regression_probe_id": "probe_3",
                "title": "Debug config endpoint",
                "path": "/api/debug/config",
                "method": "GET",
                "severity": "P2",
                "status": "needs_review",
                "reason": "Cannot auto-judge; QA review required.",
            },
            {
                "issue_id": "ISS-004",
                "regression_probe_id": "probe_4",
                "title": "Delete user endpoint",
                "path": "/api/users/1",
                "method": "DELETE",
                "severity": "P2",
                "status": "skipped",
                "reason": "Destructive probe skipped.",
            },
            {
                "issue_id": "ISS-005",
                "regression_probe_id": "probe_5",
                "title": "Admin bypass check",
                "path": "/api/admin/secret",
                "method": "GET",
                "severity": "P1",
                "status": "passed",
                "reason": "Status 403 matches expected.",
            },
        ],
    }

    history = _append_regression_history(project, tmp_path, result)

    # ── Assert history was saved ──
    assert len(history) == 1
    entry = history[0]
    items = entry.get("items") or []
    assert len(items) == 5

    # ── Assert lifecycle field on every item ──
    expected_lifecycles = {
        "probe_1": "regression_passed",
        "probe_2": "regression_failed",
        "probe_3": "pending_review",
        "probe_4": "lifecycle_skipped",
        "probe_5": "regression_passed",
    }
    for item in items:
        probe_id = item.get("regression_probe_id")
        lifecycle = item.get("lifecycle")
        assert lifecycle is not None, f"item {probe_id} missing lifecycle field"
        assert lifecycle == expected_lifecycles.get(probe_id, ""), (
            f"item {probe_id}: expected lifecycle '{expected_lifecycles.get(probe_id)}', "
            f"got '{lifecycle}'"
        )

    # ── Assert the file on disk also has lifecycle ──
    history_path = tmp_path / "platform_outputs" / project / "regression_run" / "regression_run_history.json"
    assert history_path.exists(), f"history file not found at {history_path}"
    on_disk = json.loads(history_path.read_text(encoding="utf-8"))
    assert isinstance(on_disk, list)
    assert len(on_disk) == 1
    for item in on_disk[0].get("items") or []:
        assert "lifecycle" in item, f"on-disk item {item.get('regression_probe_id')} missing lifecycle"

    # ── Assert workspace copy also has lifecycle ──
    ws_path = tmp_path / "platform_workspace" / project / "defect_discovery" / "regression_run_history.json"
    assert ws_path.exists(), f"workspace history file not found at {ws_path}"
    ws_data = json.loads(ws_path.read_text(encoding="utf-8"))
    for item in ws_data[0].get("items") or []:
        assert "lifecycle" in item, f"workspace item {item.get('regression_probe_id')} missing lifecycle"
