from __future__ import annotations

import time
from pathlib import Path

import pytest

from ai_test_asset_center import connector_checkpoint_recovery as recovery
from ai_test_asset_center import connector_sync_ownership as ownership


PROJECT = "enterprise-project"
CONNECTOR = "feishu-main"
ACTOR = {"name": "auto", "role": "knowledge_admin"}


def test_sync_ownership_records_heartbeat_epoch_and_stops(tmp_path: Path):
    receipt = ownership.begin_connector_sync_ownership(
        PROJECT,
        CONNECTOR,
        "checkpoint_test",
        root=tmp_path,
        epoch_provider=lambda: "sync_live",
    )
    assert receipt["heartbeat_started"] is True

    ownership.heartbeat_connector_sync_ownership(
        PROJECT,
        CONNECTOR,
        "checkpoint_test",
        root=tmp_path,
        active_epoch_id="sync_live",
    )
    status = ownership.inspect_connector_sync_ownership(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    )
    assert status["state"] == "ACTIVE"
    assert status["owner_alive"] is True
    assert status["active_sync_epoch_id"] == "sync_live"
    assert status["raw_credentials_persisted"] is False
    assert status["source_content_persisted"] is False

    stopped = ownership.stop_connector_sync_ownership(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        expected_attempt_id="checkpoint_test",
    )
    assert stopped["removed"] is True
    assert stopped["thread_alive"] is False
    assert ownership.read_connector_sync_ownership(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    ) == {}


def test_sync_ownership_detects_dead_process_without_using_age_only(
    tmp_path: Path,
):
    path = ownership._path(tmp_path, PROJECT, CONNECTOR)
    path.parent.mkdir(parents=True, exist_ok=True)
    ownership._write_json_object_atomic(
        path,
        {
            "schema": ownership.SYNC_OWNERSHIP_SCHEMA,
            "project_id": PROJECT,
            "connector_instance_id": CONNECTOR,
            "attempt_id": "dead-attempt",
            "state": "ACTIVE",
            "pid": 999_999_999,
            "owner_thread_id": 1,
            "process_start_marker": "old",
            "last_heartbeat_unix": time.time(),
        },
    )
    status = ownership.inspect_connector_sync_ownership(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    )
    assert status["state"] == "DEAD_PROCESS"
    assert status["owner_dead"] is True
    assert status["heartbeat_stale"] is False


def _registry(active_epoch: str) -> dict:
    return {
        "connector_instances": [
            {
                "connector_instance_id": CONNECTOR,
                "active_sync_epoch_id": active_epoch,
                "last_sync_started_at_utc": "2026-07-31T00:00:00Z",
            }
        ],
        "audit_events": [],
    }


def _install_registry_stubs(monkeypatch, active_epoch: str, lock_epoch: str):
    registry = _registry(active_epoch)
    monkeypatch.setattr(
        recovery,
        "_load_connector_registry",
        lambda project, root: registry,
    )
    monkeypatch.setattr(
        recovery,
        "_instance_by_id",
        lambda value, connector: value["connector_instances"][0],
    )
    monkeypatch.setattr(
        recovery,
        "_lock_epoch",
        lambda project, connector, root: lock_epoch,
    )
    return registry


def test_dead_owner_aborts_stranded_running_sync_and_preserves_checkpoint(
    monkeypatch,
    tmp_path: Path,
):
    _install_registry_stubs(monkeypatch, "sync_dead", "sync_dead")
    monkeypatch.setattr(
        recovery,
        "inspect_connector_sync_ownership",
        lambda *a, **k: {
            "state": "DEAD_PROCESS",
            "owner_dead": True,
            "owner_alive": False,
            "attempt_id": "checkpoint_dead",
        },
    )
    aborted: list[dict] = []
    monkeypatch.setattr(
        recovery,
        "abort_connector_sync_run",
        lambda *a, **k: aborted.append(k) or {"status": "ABORTED"},
    )
    monkeypatch.setattr(
        recovery,
        "stop_connector_sync_ownership",
        lambda *a, **k: {"ok": True},
    )

    result = recovery._recover_stale_sync_lifecycle(
        tmp_path,
        PROJECT,
        CONNECTOR,
        actor=ACTOR,
    )
    assert result["action"] == "ABORTED_STRANDED_RUNNING_SYNC"
    assert result["replay_required"] is True
    assert result["previous_snapshots_retained"] is True
    assert result["checkpoint_advanced"] is False
    assert aborted[0]["connector_instance_id"] == CONNECTOR


def test_live_owner_is_never_aborted_even_when_heartbeat_is_old(
    monkeypatch,
    tmp_path: Path,
):
    _install_registry_stubs(monkeypatch, "sync_live", "sync_live")
    monkeypatch.setattr(
        recovery,
        "inspect_connector_sync_ownership",
        lambda *a, **k: {
            "state": "STALE_HEARTBEAT_OWNER_ALIVE",
            "owner_dead": False,
            "owner_alive": True,
            "attempt_id": "checkpoint_live",
        },
    )
    monkeypatch.setattr(
        recovery,
        "abort_connector_sync_run",
        lambda *a, **k: pytest.fail("live synchronization must not be aborted"),
    )

    with pytest.raises(
        recovery.ConnectorCheckpointRecoveryError,
        match="owner_active",
    ):
        recovery._recover_stale_sync_lifecycle(
            tmp_path,
            PROJECT,
            CONNECTOR,
            actor=ACTOR,
        )


def test_dead_owner_cleans_mismatched_orphan_lock_before_abort(
    monkeypatch,
    tmp_path: Path,
):
    _install_registry_stubs(monkeypatch, "sync_registry", "sync_orphan")
    monkeypatch.setattr(
        recovery,
        "inspect_connector_sync_ownership",
        lambda *a, **k: {
            "state": "DEAD_PROCESS",
            "owner_dead": True,
            "owner_alive": False,
            "attempt_id": "checkpoint_dead",
        },
    )
    removed: list[str] = []
    monkeypatch.setattr(
        recovery,
        "_remove_sync_lock",
        lambda project, connector, epoch, root: removed.append(epoch),
    )
    monkeypatch.setattr(
        recovery,
        "abort_connector_sync_run",
        lambda *a, **k: {"status": "ABORTED"},
    )
    monkeypatch.setattr(
        recovery,
        "stop_connector_sync_ownership",
        lambda *a, **k: {"ok": True},
    )

    result = recovery._recover_stale_sync_lifecycle(
        tmp_path,
        PROJECT,
        CONNECTOR,
        actor=ACTOR,
    )
    assert removed == ["sync_orphan"]
    assert result["action"] == "ABORTED_STRANDED_RUNNING_SYNC"


def test_legacy_missing_owner_requires_safe_age_before_recovery(
    monkeypatch,
    tmp_path: Path,
):
    _install_registry_stubs(monkeypatch, "sync_legacy", "sync_legacy")
    monkeypatch.setattr(
        recovery,
        "inspect_connector_sync_ownership",
        lambda *a, **k: {
            "state": "MISSING",
            "owner_dead": False,
            "owner_alive": None,
        },
    )
    monkeypatch.setattr(recovery, "_legacy_stale_seconds", lambda: 1800)
    monkeypatch.setattr(recovery, "_active_age_seconds", lambda *a, **k: 120)

    with pytest.raises(
        recovery.ConnectorCheckpointRecoveryError,
        match="owner_unverified",
    ):
        recovery._recover_stale_sync_lifecycle(
            tmp_path,
            PROJECT,
            CONNECTOR,
            actor=ACTOR,
        )


def test_checkpoint_journal_starts_and_stops_the_same_owner(
    tmp_path: Path,
):
    begun = recovery.begin_connector_checkpoint_commit(
        PROJECT,
        CONNECTOR,
        "",
        root=tmp_path,
        actor=ACTOR,
    )
    attempt = begun["attempt_id"]
    journal = recovery._read(tmp_path, PROJECT, CONNECTOR)
    owner = ownership.read_connector_sync_ownership(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    )
    assert journal["attempt_id"] == attempt
    assert owner["attempt_id"] == attempt
    assert begun["sync_owner_recorded"] is True
    assert begun["heartbeat_started"] is True

    cleared = recovery.clear_connector_checkpoint_journal(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
        expected_attempt_id=attempt,
    )
    assert cleared["heartbeat_stopped"] is True
    assert recovery._read(tmp_path, PROJECT, CONNECTOR) == {}
    assert ownership.read_connector_sync_ownership(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    ) == {}


def test_ownership_sidecar_is_not_a_second_connector_registry():
    source = Path(ownership.__file__).read_text(encoding="utf-8")
    assert "connector_sync_ownership" in source
    assert "connector_sync_registry.json" not in source
    assert "source_registry.json" not in source
    assert "raw_credentials_persisted" in source
    assert "source_content_persisted" in source
