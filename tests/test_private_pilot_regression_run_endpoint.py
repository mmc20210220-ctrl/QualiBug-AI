from __future__ import annotations

import os

os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")

import ai_test_asset_center.regression_runner as regression_runner
from ai_test_asset_center.private_pilot_service import PrivatePilotHandler


def test_private_pilot_regression_run_endpoint_returns_summary(monkeypatch, tmp_path) -> None:
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    captured: dict[str, object] = {}

    def fake_json(payload, status=200, extra_headers=None):  # type: ignore[no-untyped-def]
        captured["payload"] = payload
        captured["status"] = status
        captured["headers"] = extra_headers
        return payload

    monkeypatch.setattr(handler, "_json", fake_json)
    monkeypatch.setattr(handler, "_require_known_project", lambda project, root: True)
    monkeypatch.setattr(handler, "_build_command_center", lambda project, root: {
        "data": {
            "regression_summary": {
                "covered_defect_count": 3,
                "failed_defect_count": 1,
                "pending_defect_count": 1,
                "latest_run": {"gate_status": "failed"},
            }
        }
    })
    monkeypatch.setattr(regression_runner, "run_regression_suite", lambda project_id, root=None, options=None: {
        "summary": {
            "suite_mode": "release",
            "failed_count": 1,
            "passed_count": 2,
        },
        "ci_feedback": {
            "gate_status": "failed",
            "ci_message": "P0/P1 回归失败，建议阻断发布。",
        },
        "failures": [{"issue_id": "ISSUE-1", "status": "failed"}],
        "regression_suite_ref": "platform_outputs/demo/regression_suite/regression_suite.json",
    })

    handler._handle_regression_run("enterprise-project", tmp_path, {"mode": "release"})

    payload = captured["payload"]
    assert captured["status"] == 200
    assert isinstance(payload, dict)
    assert payload["ok"] is True
    assert payload["project_id"] == "enterprise-project"
    assert payload["summary"]["suite_mode"] == "release"
    assert payload["ci_feedback"]["gate_status"] == "failed"
    assert payload["regression_summary"]["covered_defect_count"] == 3
    assert payload["artifacts"]["run_result_ref"] == "platform_outputs/enterprise-project/regression_run/regression_run_result.json"
    assert payload["governance"]["safe_by_default"] is True


def test_private_pilot_regression_run_endpoint_rejects_unknown_mode(monkeypatch, tmp_path) -> None:
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    captured: dict[str, object] = {}

    def fake_json(payload, status=200, extra_headers=None):  # type: ignore[no-untyped-def]
        captured["payload"] = payload
        captured["status"] = status
        captured["headers"] = extra_headers
        return payload

    monkeypatch.setattr(handler, "_json", fake_json)
    monkeypatch.setattr(handler, "_require_known_project", lambda project, root: True)

    handler._handle_regression_run("enterprise-project", tmp_path, {"mode": "unsafe"})

    payload = captured["payload"]
    assert captured["status"] == 400
    assert isinstance(payload, dict)
    assert payload["ok"] is False
    assert payload["error"] == "BAD_REGRESSION_MODE"
