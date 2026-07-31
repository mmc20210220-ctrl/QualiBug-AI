from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ai_test_asset_center import connector_auto_sync as auto


PROJECT = "enterprise-project"
CONNECTOR = "feishu-main"


def test_managed_sync_is_the_single_checkpoint_commit_path(monkeypatch, tmp_path: Path):
    observed: dict[str, object] = {}
    monkeypatch.setattr(auto, "load_connector_sync_checkpoint", lambda *a, **k: "old-cursor")

    def validate(project, connector, checkpoint, *, root):
        observed["validated"] = (project, connector, checkpoint, root)

    next_cursor = "feishu-snapshot-v1:" + "a" * 64
    monkeypatch.setattr(auto, "validate_connector_checkpoint", validate)
    monkeypatch.setattr(
        auto,
        "sync_feishu_connector",
        lambda *a, **k: {
            "status": "COMPLETE",
            "sync_epoch_id": "sync_1",
            "next_cursor": next_cursor,
            "committed_cursor_fingerprint": hashlib.sha256(
                next_cursor.encode("utf-8")
            ).hexdigest(),
        },
    )

    def commit(project, connector, checkpoint, **kwargs):
        observed["committed"] = (project, connector, checkpoint, kwargs)
        return {"ok": True}

    monkeypatch.setattr(auto, "commit_connector_sync_checkpoint", commit)
    result = auto.run_managed_feishu_sync(PROJECT, CONNECTOR, root=tmp_path)

    assert result["status"] == "COMPLETE"
    assert observed["validated"][:3] == (PROJECT, CONNECTOR, "old-cursor")
    assert observed["committed"][:3] == (PROJECT, CONNECTOR, next_cursor)


def test_managed_sync_never_advances_checkpoint_for_incomplete_run(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(auto, "load_connector_sync_checkpoint", lambda *a, **k: "")
    monkeypatch.setattr(auto, "validate_connector_checkpoint", lambda *a, **k: None)
    monkeypatch.setattr(
        auto,
        "sync_feishu_connector",
        lambda *a, **k: {"status": "PARTIAL", "sync_epoch_id": "sync_partial"},
    )
    monkeypatch.setattr(
        auto,
        "commit_connector_sync_checkpoint",
        lambda *a, **k: pytest.fail("checkpoint must not advance"),
    )

    result = auto.run_managed_feishu_sync(PROJECT, CONNECTOR, root=tmp_path)
    assert result["status"] == "PARTIAL"


def test_due_policy_hides_scheduler_decisions_from_users():
    now = 100_000.0
    instance = {
        "status": "ACTIVE",
        "connector_type": "feishu",
        "active_sync_epoch_id": "",
        "last_successful_sync_at_utc": auto._utc(now - 7 * 60 * 60),
        "last_failed_sync_at_utc": "",
    }
    assert auto._due(instance, {}, now=now, refresh_seconds=6 * 60 * 60)
    assert not auto._due(
        instance,
        {"next_attempt_unix": now + 60},
        now=now,
        refresh_seconds=6 * 60 * 60,
    )
    instance["status"] = "PAUSED"
    assert not auto._due(instance, {}, now=now, refresh_seconds=6 * 60 * 60)


def test_sweep_retries_with_exponential_backoff_without_new_registry(
    monkeypatch,
    tmp_path: Path,
):
    auto._ATTEMPTS.clear()
    monkeypatch.setattr(auto, "_project_ids", lambda root: [PROJECT])
    monkeypatch.setattr(auto, "_configured_profiles", lambda project, root: {CONNECTOR})
    monkeypatch.setattr(
        auto,
        "list_connector_instances",
        lambda *a, **k: {
            "connector_instances": [
                {
                    "connector_instance_id": CONNECTOR,
                    "connector_type": "feishu",
                    "status": "ACTIVE",
                    "active_sync_epoch_id": "",
                    "last_successful_sync_at_utc": "",
                    "last_failed_sync_at_utc": "",
                }
            ]
        },
    )
    monkeypatch.setattr(
        auto,
        "_policy",
        lambda: {
            "refresh_seconds": 3600,
            "sweep_seconds": 10,
            "initial_delay_seconds": 0,
            "retry_base_seconds": 60,
            "retry_max_seconds": 3600,
        },
    )

    calls: list[float] = []

    def fail(*args, **kwargs):
        calls.append(1.0)
        raise RuntimeError("temporary remote failure")

    first = auto.run_connector_auto_sync_sweep(tmp_path, now=1_000.0, sync_runner=fail)
    blocked = auto.run_connector_auto_sync_sweep(tmp_path, now=1_030.0, sync_runner=fail)
    second = auto.run_connector_auto_sync_sweep(tmp_path, now=1_061.0, sync_runner=fail)

    assert first == {
        "enabled": True,
        "attempted": 1,
        "succeeded": 0,
        "failed": 1,
        "skipped": 0,
        "completed_at_utc": auto._utc(1_000.0),
        "new_registry_created": False,
    }
    assert blocked["attempted"] == 0
    assert blocked["skipped"] == 1
    assert second["attempted"] == 1
    assert len(calls) == 2
    status = auto.connector_auto_sync_status(tmp_path, PROJECT, CONNECTOR)
    assert status["failure_count"] == 2
    assert status["raw_error_returned"] is False
    assert status["message"] == "更新暂时中断，系统会自动重试"


def test_success_resets_retry_state(monkeypatch, tmp_path: Path):
    auto._ATTEMPTS.clear()
    monkeypatch.setattr(auto, "_project_ids", lambda root: [PROJECT])
    monkeypatch.setattr(auto, "_configured_profiles", lambda project, root: {CONNECTOR})
    monkeypatch.setattr(
        auto,
        "list_connector_instances",
        lambda *a, **k: {
            "connector_instances": [
                {
                    "connector_instance_id": CONNECTOR,
                    "connector_type": "feishu",
                    "status": "ACTIVE",
                    "active_sync_epoch_id": "",
                    "last_successful_sync_at_utc": "",
                    "last_failed_sync_at_utc": "",
                }
            ]
        },
    )
    monkeypatch.setattr(
        auto,
        "_policy",
        lambda: {
            "refresh_seconds": 3600,
            "sweep_seconds": 10,
            "initial_delay_seconds": 0,
            "retry_base_seconds": 60,
            "retry_max_seconds": 3600,
        },
    )

    result = auto.run_connector_auto_sync_sweep(
        tmp_path,
        now=2_000.0,
        sync_runner=lambda *a, **k: {
            "status": "COMPLETE",
            "sync_epoch_id": "sync_ok",
        },
    )
    status = auto.connector_auto_sync_status(tmp_path, PROJECT, CONNECTOR)
    assert result["succeeded"] == 1
    assert status["state"] == "healthy"
    assert status["failure_count"] == 0
    assert status["attention"] == ""


def test_supervisor_is_idempotent_and_can_be_disabled(monkeypatch, tmp_path: Path):
    auto.stop_all_connector_auto_sync_supervisors()
    monkeypatch.setenv("QUALIBUG_CONNECTOR_AUTO_SYNC_ENABLED", "0")
    assert auto.ensure_connector_auto_sync_supervisor(tmp_path)["enabled"] is False

    monkeypatch.setenv("QUALIBUG_CONNECTOR_AUTO_SYNC_ENABLED", "1")
    monkeypatch.setenv("QUALIBUG_CONNECTOR_AUTO_SYNC_INITIAL_DELAY_SECONDS", "600")
    first = auto.ensure_connector_auto_sync_supervisor(tmp_path)
    second = auto.ensure_connector_auto_sync_supervisor(tmp_path)
    stopped = auto.stop_connector_auto_sync_supervisor(tmp_path)
    assert first["started"] is True
    assert second["already_running"] is True
    assert stopped["stopped"] is True
    assert stopped["thread_alive"] is False


def test_auto_sync_authority_creates_no_parallel_registry():
    source = Path(auto.__file__).read_text(encoding="utf-8")
    assert "connector_connection_profiles.json" in source
    assert "connector_sync_registry" not in source
    assert "source_registry" not in source
    assert "run_managed_feishu_sync" in source
