from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ai_test_asset_center.discovery_policy_evaluation_runner import (
    FIXTURE_CLEANUP_SCHEMA,
    FIXTURE_PREPARE_SCHEMA,
    PolicyEvaluationRunnerError,
)
from ai_test_asset_center.evaluation_fixture_controller import (
    HTTP_FIXTURE_SCHEMA,
    GovernedHttpResetFixtureController,
)


def _runtime_view(tmp_path: Path) -> tuple[dict, str]:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps({
            "schema_version": HTTP_FIXTURE_SCHEMA,
            "base_url": "http://127.0.0.1:8011",
            "reset": {"method": "POST", "path": "/__reset", "body": {}},
            "observation_path": "/__state",
            "clean_state_assertions": {"record_count": 0},
        }),
        encoding="utf-8",
    )
    return ({
        "target": {
            "target_id": "target-1",
            "project_id": "project-1",
            "runtime": {
                "environment_ref": "http://127.0.0.1:8011",
                "environment_type": "sandbox",
                "fixture_snapshot_ref": str(fixture),
            },
        },
    }, hashlib.sha256(fixture.read_bytes()).hexdigest())


def _governed_receipt(**kwargs):
    return {
        "accepted": True,
        "after": {"status": 200, "body": {"record_count": 0}},
        "before_ref": "before:200",
        "after_ref": "after:200",
        "audit_path": "audit.jsonl",
        "audit_record": {"campaign_id": kwargs["campaign_id"], "phase": kwargs["operation_phase"]},
        "production_http_requests": 0,
    }


def test_controller_returns_governed_prepare_and_cleanup_receipts(monkeypatch, tmp_path: Path) -> None:
    runtime_view, fingerprint = _runtime_view(tmp_path)
    monkeypatch.setattr(
        "ai_test_asset_center.evaluation_fixture_controller.execute_governed_control_write",
        _governed_receipt,
    )
    controller = GovernedHttpResetFixtureController(workspace_root=tmp_path)

    prepared = controller.prepare(
        runtime_view=runtime_view,
        campaign_id="campaign-1",
        policy_id="policy-1",
        evaluation_mode="replay",
        expected_fixture_fingerprint=fingerprint,
    )
    cleaned = controller.cleanup(
        runtime_view=runtime_view,
        campaign_id="campaign-1",
        policy_id="policy-1",
        evaluation_mode="replay",
        preparation_receipt=prepared,
        scan_output={},
    )

    assert prepared["schema_version"] == FIXTURE_PREPARE_SCHEMA
    assert prepared["status"] == "READY"
    assert cleaned["schema_version"] == FIXTURE_CLEANUP_SCHEMA
    assert cleaned["status"] == "SUCCEEDED"
    assert cleaned["dirty_environment"] is False


def test_controller_fails_when_reset_does_not_restore_declared_state(monkeypatch, tmp_path: Path) -> None:
    runtime_view, fingerprint = _runtime_view(tmp_path)

    def dirty_receipt(**kwargs):
        receipt = _governed_receipt(**kwargs)
        receipt["after"]["body"]["record_count"] = 2
        return receipt

    monkeypatch.setattr(
        "ai_test_asset_center.evaluation_fixture_controller.execute_governed_control_write",
        dirty_receipt,
    )
    controller = GovernedHttpResetFixtureController(workspace_root=tmp_path)

    with pytest.raises(PolicyEvaluationRunnerError, match="did not establish declared clean state"):
        controller.prepare(
            runtime_view=runtime_view,
            campaign_id="campaign-1",
            policy_id="policy-1",
            evaluation_mode="replay",
            expected_fixture_fingerprint=fingerprint,
        )
