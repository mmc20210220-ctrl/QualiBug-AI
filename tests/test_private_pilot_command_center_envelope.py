from __future__ import annotations

from ai_test_asset_center.private_pilot_service import _normalize_command_center_envelope


def _legacy_ready_item() -> dict:
    return {
        "id": "BUG-1",
        "title": "支付金额守恒失败",
        "severity": "P1",
        "bug_status": "reproduced",
        "gate_passed": True,
        "raw_evidence": {
            "has_real_evidence": True,
            "timestamp": "2026-07-06T12:00:00Z",
            "request_raw": {"method": "POST", "path": "/api/payments"},
            "response_raw": {"status_code": 200, "body": {"paid_amount": 1}},
        },
        "reproduction": {
            "method": "POST",
            "path": "/api/payments",
            "is_synthetic": False,
            "har_evidence": {"status_code": 200, "response_body": {"paid_amount": 1}},
        },
    }


def test_private_pilot_command_center_envelope_splits_legacy_risks() -> None:
    ready = _legacy_ready_item()
    clue = _legacy_ready_item()
    clue["id"] = "CLUE-1"
    clue["bug_status"] = "risk_clue"

    payload = _normalize_command_center_envelope({"ok": True, "data": {"risks": [ready, clue]}})
    data = payload["data"]

    assert [item["id"] for item in data["defects"]] == ["BUG-1"]
    assert [item["id"] for item in data["clues"]] == ["CLUE-1"]
    assert data["risks"] == data["defects"]
    assert data["value_metrics"]["ready_bug_count"] == 1
    assert data["value_metrics"]["clue_count"] == 1
    assert data["executive_summary"]["ready_bugs"] == 1
    assert data["executive_summary"]["internal_clues"] == 1


def test_private_pilot_command_center_envelope_preserves_existing_tracks() -> None:
    ready = _legacy_ready_item()
    clue = _legacy_ready_item()
    clue["id"] = "CLUE-1"
    clue["bug_status"] = "risk_clue"

    payload = _normalize_command_center_envelope({"ok": True, "data": {"defects": [ready], "clues": [clue], "risks": [ready, clue]}})
    data = payload["data"]

    assert [item["id"] for item in data["defects"]] == ["BUG-1"]
    assert [item["id"] for item in data["clues"]] == ["CLUE-1"]
    assert data["risks"] == data["defects"]
