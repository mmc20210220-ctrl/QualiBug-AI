from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center import evaluation_fixture_controller as module
from ai_test_asset_center.discovery_policy_evaluation_runner import (
    PolicyEvaluationRunnerError,
)
from ai_test_asset_center.evaluation_fixture_controller import (
    GovernedHttpResetFixtureController,
    HTTP_FIXTURE_SCHEMA,
    _read_fixture,
)


def test_evaluation_fixture_reader_accepts_windows_utf8_bom(
    tmp_path: Path,
) -> None:
    fixture_path = tmp_path / "fixture.json"
    fixture = {
        "schema_version": HTTP_FIXTURE_SCHEMA,
        "base_url": "http://127.0.0.1:8080",
        "reset": {"method": "POST", "path": "/reset", "body": {}},
        "observation_path": "/health",
        "clean_state_assertions": {"ok": True},
    }
    fixture_path.write_text(
        "\ufeff" + json.dumps(fixture, ensure_ascii=False),
        encoding="utf-8",
    )
    runtime_view = {
        "target": {
            "target_id": "TARGET-1",
            "runtime": {"fixture_snapshot_ref": str(fixture_path)},
        }
    }

    target, payload = _read_fixture(runtime_view)

    assert target["target_id"] == "TARGET-1"
    assert payload == fixture


def test_http_fixture_reset_failure_reports_transport_statuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(
        json.dumps({
            "schema_version": HTTP_FIXTURE_SCHEMA,
            "base_url": "http://127.0.0.1:8080",
            "reset": {"method": "POST", "path": "/reset", "body": {}},
            "observation_path": "/state",
            "clean_state_assertions": {"record_count": 0},
        }),
        encoding="utf-8",
    )
    runtime_view = {
        "target": {
            "target_id": "TARGET-1",
            "project_id": "generic-project",
            "runtime": {
                "environment_ref": "http://127.0.0.1:8080",
                "environment_type": "test",
                "fixture_snapshot_ref": str(fixture_path),
            },
        }
    }

    def rejected_write(**_: object) -> dict[str, object]:
        return {
            "accepted": False,
            "reason": "control_write_not_accepted",
            "before": {"status": 404},
            "write": {"status": 404, "error": "not found"},
            "after": {"status": 404},
            "audit_path": str(tmp_path / "audit.jsonl"),
        }

    monkeypatch.setattr(module, "execute_governed_control_write", rejected_write)

    with pytest.raises(PolicyEvaluationRunnerError) as error:
        GovernedHttpResetFixtureController(workspace_root=tmp_path).prepare(
            runtime_view=runtime_view,
            campaign_id="CMP-1",
            policy_id="POLICY-1",
            evaluation_mode="replay",
            expected_fixture_fingerprint="",
        )

    message = str(error.value)
    assert "control_write_not_accepted" in message
    assert "write_status=404" in message
    assert "before_status=404" in message
    assert "after_status=404" in message
    assert "audit_path=" in message
