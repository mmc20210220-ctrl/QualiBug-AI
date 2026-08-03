from __future__ import annotations

import json
from pathlib import Path

import pytest

import ai_test_asset_center.connector_checkpoint_commit_authority as checkpoint
import ai_test_asset_center.connector_sync_authority as sync
from ai_test_asset_center.enterprise_knowledge_center._utils import _write_json

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"
EPOCH = "sync-checkpoint-one"
ACTOR = {"name": "tester", "role": "knowledge_admin"}
PENDING_HASH = "a" * 64
OLD_HASH = "b" * 64
TRANSACTION = "lctx_1234567890abcdef1234567890abcdef"


def _registry_path(root: Path) -> Path:
    return sync._registry_path(PROJECT, root)


def _run_path(root: Path) -> Path:
    return sync._run_path(PROJECT, CONNECTOR, EPOCH, root)


def _seed_registry(root: Path) -> None:
    _write_json(
        _registry_path(root),
        {
            "schema": "qualibug.connector-sync-registry.v1",
            "project_id": PROJECT,
            "connector_instances": [
                {
                    "connector_instance_id": CONNECTOR,
                    "connector_type": "feishu",
                    "status": "ACTIVE",
                    "active_sync_epoch_id": EPOCH,
                    "last_committed_cursor_fingerprint": OLD_HASH,
                    "last_successful_sync_epoch_id": "sync-before",
                    "last_successful_sync_at_utc": "2026-08-01T09:00:00Z",
                }
            ],
            "sync_runs": [],
            "audit_events": [],
            "governance": {},
        },
    )


def _seed_pending_run(root: Path, *, lifecycle_committed: bool = True) -> None:
    _seed_registry(root)
    registry = sync._load_connector_registry(PROJECT, root)
    instance = sync._instance_by_id(registry, CONNECTOR)
    assert instance is not None
    instance.update(
        {
            "active_sync_epoch_id": "",
            "pending_lifecycle_sync_epoch_id": EPOCH,
            "pending_cursor_fingerprint": PENDING_HASH,
            "pending_checkpoint_since_utc": "2026-08-01T10:01:00Z",
        }
    )
    run = {
        "schema": sync.CONNECTOR_SYNC_RUN_SCHEMA,
        "sync_epoch_id": EPOCH,
        "project_id": PROJECT,
        "connector_instance_id": CONNECTOR,
        "connector_type": "feishu",
        "sync_mode": "FULL",
        "status": "COMPLETE",
        "started_at_utc": "2026-08-01T10:00:00Z",
        "completed_at_utc": "2026-08-01T10:01:00Z",
        "success_count": 1,
        "failure_count": 0,
        "cursor_checkpoint_committed": False,
        "committed_cursor_fingerprint": "",
        "previous_cursor_checkpoint_preserved": True,
        "cursor_checkpoint_pending_lifecycle_commit": True,
        "pending_cursor_fingerprint": PENDING_HASH,
        "remote_lifecycle_commit": {
            "schema": "qualibug.connector-lifecycle-commit.v1",
            "transaction_id": TRANSACTION,
            "status": "COMMITTED" if lifecycle_committed else "PREPARED",
        },
    }
    path = sync._write_run_receipt(PROJECT, CONNECTOR, EPOCH, root, run)
    sync._run_summary(registry, run, path)
    sync._save_connector_registry(PROJECT, root, registry)


def test_deferred_finish_preserves_previous_success_pointer_and_restores_hook(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_registry(tmp_path)
    original_hook = None

    def canonical_finish(project, connector, run, root, actor, next_cursor_hash):
        assert next_cursor_hash == ""
        registry = sync._load_connector_registry(project, root)
        instance = sync._instance_by_id(registry, connector)
        assert instance is not None
        instance["active_sync_epoch_id"] = ""
        instance["last_successful_sync_epoch_id"] = run["sync_epoch_id"]
        instance["last_successful_sync_at_utc"] = run["completed_at_utc"]
        run["cursor_checkpoint_committed"] = False
        run["committed_cursor_fingerprint"] = ""
        run["previous_cursor_checkpoint_preserved"] = True
        path = sync._write_run_receipt(project, connector, run["sync_epoch_id"], root, run)
        sync._run_summary(registry, run, path)
        sync._save_connector_registry(project, root, registry)
        return path

    def fake_batch(project_id, **kwargs):
        run = {
            "schema": sync.CONNECTOR_SYNC_RUN_SCHEMA,
            "sync_epoch_id": EPOCH,
            "project_id": project_id,
            "connector_instance_id": CONNECTOR,
            "connector_type": "feishu",
            "sync_mode": "FULL",
            "status": "COMPLETE",
            "started_at_utc": "2026-08-01T10:00:00Z",
            "completed_at_utc": "2026-08-01T10:01:00Z",
            "success_count": 1,
            "failure_count": 0,
        }
        sync._finish_run(
            project_id,
            CONNECTOR,
            run,
            tmp_path,
            ACTOR,
            PENDING_HASH,
        )
        return run

    monkeypatch.setattr(sync, "_finish_run", canonical_finish)
    original_hook = sync._finish_run
    monkeypatch.setattr(sync, "sync_connector_snapshot_batch", fake_batch)

    result = checkpoint.sync_connector_snapshot_batch_deferred(
        PROJECT,
        connector_instance_id=CONNECTOR,
        items=[],
        root=tmp_path,
        actor=ACTOR,
        sync_mode="FULL",
        next_cursor="snapshot-cursor",
        snapshot_complete=True,
    )

    assert sync._finish_run is original_hook
    assert result["cursor_checkpoint_committed"] is False
    assert result["cursor_checkpoint_pending_lifecycle_commit"] is True
    assert result["pending_cursor_fingerprint"] == PENDING_HASH
    registry = sync._load_connector_registry(PROJECT, tmp_path)
    instance = sync._instance_by_id(registry, CONNECTOR)
    assert instance is not None
    assert instance["last_committed_cursor_fingerprint"] == OLD_HASH
    assert instance["last_successful_sync_epoch_id"] == "sync-before"
    assert instance["pending_lifecycle_sync_epoch_id"] == EPOCH


def test_uncommitted_lifecycle_never_advances_cursor(tmp_path: Path) -> None:
    _seed_pending_run(tmp_path, lifecycle_committed=False)

    result = checkpoint.recover_committed_connector_checkpoint(
        PROJECT,
        connector_instance_id=CONNECTOR,
        sync_epoch_id=EPOCH,
        root=tmp_path,
        actor=ACTOR,
    )

    assert result["status"] == "WAITING_FOR_LIFECYCLE_COMMIT"
    run = sync.load_connector_sync_run(
        PROJECT,
        connector_instance_id=CONNECTOR,
        sync_epoch_id=EPOCH,
        root=tmp_path,
    )
    registry = sync._load_connector_registry(PROJECT, tmp_path)
    instance = sync._instance_by_id(registry, CONNECTOR)
    assert run["cursor_checkpoint_committed"] is False
    assert run["cursor_checkpoint_pending_lifecycle_commit"] is True
    assert instance is not None
    assert instance["last_committed_cursor_fingerprint"] == OLD_HASH


def test_committed_lifecycle_advances_cursor_once_and_is_idempotent(
    tmp_path: Path,
) -> None:
    _seed_pending_run(tmp_path, lifecycle_committed=True)

    first = checkpoint.recover_committed_connector_checkpoint(
        PROJECT,
        connector_instance_id=CONNECTOR,
        sync_epoch_id=EPOCH,
        root=tmp_path,
        actor=ACTOR,
    )
    second = checkpoint.recover_committed_connector_checkpoint(
        PROJECT,
        connector_instance_id=CONNECTOR,
        sync_epoch_id=EPOCH,
        root=tmp_path,
        actor=ACTOR,
    )

    assert first["status"] == "COMMITTED"
    assert second["status"] == "ALREADY_COMMITTED"
    run = sync.load_connector_sync_run(
        PROJECT,
        connector_instance_id=CONNECTOR,
        sync_epoch_id=EPOCH,
        root=tmp_path,
    )
    registry = sync._load_connector_registry(PROJECT, tmp_path)
    instance = sync._instance_by_id(registry, CONNECTOR)
    assert run["cursor_checkpoint_committed"] is True
    assert run["cursor_checkpoint_pending_lifecycle_commit"] is False
    assert run["committed_cursor_fingerprint"] == PENDING_HASH
    assert run["checkpoint_committed_by_lifecycle_transaction_id"] == TRANSACTION
    assert instance is not None
    assert instance["last_committed_cursor_fingerprint"] == PENDING_HASH
    events = [
        row
        for row in registry["audit_events"]
        if row.get("event") == "commit_connector_cursor_after_remote_lifecycle"
    ]
    assert len(events) == 1


def test_recovery_repairs_registry_when_run_commit_was_already_written(
    tmp_path: Path,
) -> None:
    _seed_pending_run(tmp_path, lifecycle_committed=True)
    run = sync.load_connector_sync_run(
        PROJECT,
        connector_instance_id=CONNECTOR,
        sync_epoch_id=EPOCH,
        root=tmp_path,
    )
    run.update(
        {
            "cursor_checkpoint_committed": True,
            "committed_cursor_fingerprint": PENDING_HASH,
            "cursor_checkpoint_pending_lifecycle_commit": False,
            "pending_cursor_fingerprint": "",
            "checkpoint_committed_by_lifecycle_transaction_id": TRANSACTION,
        }
    )
    sync._write_run_receipt(PROJECT, CONNECTOR, EPOCH, tmp_path, run)

    result = checkpoint.recover_committed_connector_checkpoint(
        PROJECT,
        connector_instance_id=CONNECTOR,
        sync_epoch_id=EPOCH,
        root=tmp_path,
        actor=ACTOR,
    )

    assert result["status"] == "ALREADY_COMMITTED"
    assert result["registry_reconciled"] is True
    registry = sync._load_connector_registry(PROJECT, tmp_path)
    instance = sync._instance_by_id(registry, CONNECTOR)
    assert instance is not None
    assert instance["last_committed_cursor_fingerprint"] == PENDING_HASH
    summary = next(row for row in registry["sync_runs"] if row["sync_epoch_id"] == EPOCH)
    assert summary["cursor_checkpoint_committed"] is True
    assert summary["cursor_checkpoint_pending_lifecycle_commit"] is False


def test_mainline_binds_feishu_sync_to_lifecycle_bound_checkpoint() -> None:
    """The retired Feishu facade forwarded sync to the checkpoint authority. The
    mainline recovery supervisor keeps the same lifecycle-bound checkpoint binding,
    and the surviving Feishu core delegates to the canonical snapshot batch."""
    import ai_test_asset_center.connector_lifecycle_recovery_supervisor as supervisor
    import ai_test_asset_center.feishu_connector_capability_sync_core as core

    assert supervisor.reconcile_connector_remote_lifecycle_with_checkpoint is (
        checkpoint.reconcile_connector_remote_lifecycle_with_checkpoint
    )
    assert core.sync_connector_snapshot_batch is sync.sync_connector_snapshot_batch
