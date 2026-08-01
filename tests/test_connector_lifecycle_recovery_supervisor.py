from __future__ import annotations

import calendar
import time
from pathlib import Path

import pytest

import ai_test_asset_center.connector_lifecycle_recovery_supervisor as supervisor
import ai_test_asset_center.connector_sync_authority as sync
from ai_test_asset_center.connector_lifecycle_recovery_intent import (
    load_connector_lifecycle_recovery_intent,
    stage_connector_lifecycle_recovery_intent,
)

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"
EPOCH = "sync-recovery-one"
ACTOR = {"name": "tester", "role": "knowledge_admin"}
PENDING_HASH = "a" * 64


def _resources() -> list[dict]:
    return [
        {
            "remote_resource_id": "wiki:space-a:node-a",
            "resource_kind": "feishu-wiki-docx",
            "display_title": "Order document",
            "parent_remote_id": "parent-a",
            "remote_space_id": "space-a",
            "remote_revision": "17",
            "materialization_state": "MATERIALIZABLE",
        }
    ]


def _seed(
    root: Path,
    *,
    run_status: str = "COMPLETE",
    lifecycle_commit_status: str = "",
    with_intent: bool = True,
) -> None:
    sync.register_connector_instance(
        PROJECT,
        connector_instance_id=CONNECTOR,
        connector_type="feishu",
        root=root,
        actor=ACTOR,
        status="ACTIVE",
    )
    registry = sync._load_connector_registry(PROJECT, root)
    instance = sync._instance_by_id(registry, CONNECTOR)
    assert instance is not None
    instance.update(
        {
            "pending_lifecycle_sync_epoch_id": EPOCH,
            "pending_cursor_fingerprint": PENDING_HASH,
            "pending_checkpoint_since_utc": "2026-08-01T00:00:00Z",
            "last_committed_cursor_fingerprint": "b" * 64,
        }
    )
    run = {
        "schema": sync.CONNECTOR_SYNC_RUN_SCHEMA,
        "sync_epoch_id": EPOCH,
        "project_id": PROJECT,
        "connector_instance_id": CONNECTOR,
        "connector_type": "feishu",
        "sync_mode": "FULL",
        "status": run_status,
        "started_at_utc": "2026-08-01T00:00:00Z",
        "completed_at_utc": "2026-08-01T00:01:00Z",
        "cursor_checkpoint_committed": False,
        "cursor_checkpoint_pending_lifecycle_commit": True,
        "pending_cursor_fingerprint": PENDING_HASH,
        "previous_cursor_checkpoint_preserved": True,
    }
    if lifecycle_commit_status:
        run["remote_lifecycle_commit"] = {
            "status": lifecycle_commit_status,
            "transaction_id": "lctx_1234567890abcdef1234567890abcdef",
        }
    path = sync._write_run_receipt(PROJECT, CONNECTOR, EPOCH, root, run)
    sync._run_summary(registry, run, path)
    sync._save_connector_registry(PROJECT, root, registry)
    if with_intent:
        stage_connector_lifecycle_recovery_intent(
            PROJECT,
            CONNECTOR,
            present_resources=_resources(),
            next_cursor_fingerprint=PENDING_HASH,
            root=root,
            actor=ACTOR,
            deletion_policy="RETIRE_MISSING",
            retire_after_complete_snapshots=2,
            max_retire_count=3,
            max_retire_ratio=0.25,
            sync_epoch_id=EPOCH,
        )


def _now() -> float:
    return float(calendar.timegm(time.strptime("2026-08-01T01:00:00Z", "%Y-%m-%dT%H:%M:%SZ")))


def test_ready_intent_replays_exact_lifecycle_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed(tmp_path)
    monkeypatch.setattr(supervisor, "_recover_transaction_journals", lambda *a, **k: {})
    recorded = []
    monkeypatch.setattr(supervisor, "_record_state", lambda *a, **k: recorded.append(k))
    captured = {}

    def replay(project_id, **kwargs):
        captured.update(kwargs)
        return {
            "status": "COMPLETE",
            "cursor_checkpoint_committed": True,
            "lifecycle_commit_transaction_id": "lctx-one",
        }

    monkeypatch.setattr(
        supervisor,
        "reconcile_connector_remote_lifecycle_with_checkpoint",
        replay,
    )

    inspection = supervisor.inspect_pending_connector_lifecycle_checkpoint(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        now=_now(),
    )
    result = supervisor.recover_pending_connector_lifecycle_checkpoint(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
        now=_now(),
    )

    assert inspection["status"] == "READY_TO_REPLAY_LIFECYCLE"
    assert result["recovery_action"] == (
        "REPLAYED_LIFECYCLE_AND_COMMITTED_CHECKPOINT"
    )
    assert captured["sync_epoch_id"] == EPOCH
    assert captured["present_resources"] == _resources()
    assert captured["deletion_policy"] == "RETIRE_MISSING"
    assert captured["max_retire_count"] == 3
    assert captured["max_retire_ratio"] == 0.25
    assert load_connector_lifecycle_recovery_intent(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    ) == {}
    assert recorded[-1]["state"] == (
        "REPLAYED_LIFECYCLE_AND_COMMITTED_CHECKPOINT"
    )


def test_committed_lifecycle_recovers_only_cursor_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed(tmp_path, lifecycle_commit_status="COMMITTED")
    monkeypatch.setattr(supervisor, "_recover_transaction_journals", lambda *a, **k: {})
    monkeypatch.setattr(supervisor, "_record_state", lambda *a, **k: None)
    calls = []

    def recover(project_id, **kwargs):
        calls.append(kwargs)
        return {"status": "COMMITTED", "cursor_checkpoint_committed": True}

    monkeypatch.setattr(supervisor, "recover_committed_connector_checkpoint", recover)

    result = supervisor.recover_pending_connector_lifecycle_checkpoint(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
        now=_now(),
    )

    assert result["recovery_action"] == "RECOVERED_COMMITTED_CHECKPOINT"
    assert result["cursor_checkpoint_committed"] is True
    assert calls[0]["sync_epoch_id"] == EPOCH
    assert load_connector_lifecycle_recovery_intent(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    ) == {}


def test_missing_intent_blocks_without_guessing_and_records_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed(tmp_path, with_intent=False)
    monkeypatch.setenv(
        "QUALIBUG_CONNECTOR_LIFECYCLE_PENDING_STALE_SECONDS",
        "60",
    )
    monkeypatch.setattr(supervisor, "_recover_transaction_journals", lambda *a, **k: {})
    recorded = []
    monkeypatch.setattr(supervisor, "_record_state", lambda *a, **k: recorded.append(k))

    inspection = supervisor.inspect_pending_connector_lifecycle_checkpoint(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        now=_now(),
    )
    with pytest.raises(
        supervisor.ConnectorLifecycleRecoverySupervisorError,
        match="BLOCKED_RECOVERY_INTENT_MISSING",
    ):
        supervisor.recover_pending_connector_lifecycle_checkpoint(
            PROJECT,
            CONNECTOR,
            root=tmp_path,
            actor=ACTOR,
            now=_now(),
        )

    assert inspection["status"] == "BLOCKED_RECOVERY_INTENT_MISSING"
    assert inspection["attention_required"] is True
    assert len(recorded) == 1
    assert recorded[0]["state"] == "BLOCKED_RECOVERY_INTENT_MISSING"
    assert recorded[0]["error_category"] == "BLOCKED_RECOVERY_INTENT_MISSING"
    assert recorded[0]["increment_failure"] is True


def test_incomplete_snapshot_is_abandoned_without_cursor_advance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed(tmp_path, run_status="FAILED")
    monkeypatch.setattr(supervisor, "_recover_transaction_journals", lambda *a, **k: {})
    monkeypatch.setattr(supervisor, "_record_state", lambda *a, **k: None)
    monkeypatch.setattr(
        supervisor,
        "_abandon_incomplete_pending_checkpoint",
        lambda *a, **k: {
            "status": "ABANDONED_INCOMPLETE_SNAPSHOT",
            "cursor_checkpoint_committed": False,
            "previous_cursor_checkpoint_preserved": True,
        },
    )

    result = supervisor.recover_pending_connector_lifecycle_checkpoint(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
        now=_now(),
    )

    assert result["recovery_action"] == "ABANDONED_INCOMPLETE_SNAPSHOT"
    assert result["cursor_checkpoint_committed"] is False
    assert result["previous_cursor_checkpoint_preserved"] is True
    assert load_connector_lifecycle_recovery_intent(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    ) == {}
