from __future__ import annotations

from pathlib import Path

import ai_test_asset_center.connector_lifecycle_recovery_supervisor as supervisor
import ai_test_asset_center.connector_sync_authority as sync
from ai_test_asset_center.connector_lifecycle_recovery_intent import (
    load_connector_lifecycle_recovery_intent,
    stage_connector_lifecycle_recovery_intent,
)

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"
EPOCH = "sync-completed-orphan"
ACTOR = {"name": "tester", "role": "knowledge_admin"}
PENDING_HASH = "a" * 64


def test_committed_checkpoint_orphan_intent_is_cleared_without_stale_wait(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sync.register_connector_instance(
        PROJECT,
        connector_instance_id=CONNECTOR,
        connector_type="feishu",
        root=tmp_path,
        actor=ACTOR,
    )
    registry = sync._load_connector_registry(PROJECT, tmp_path)
    run = {
        "schema": sync.CONNECTOR_SYNC_RUN_SCHEMA,
        "sync_epoch_id": EPOCH,
        "project_id": PROJECT,
        "connector_instance_id": CONNECTOR,
        "connector_type": "feishu",
        "sync_mode": "FULL",
        "status": "COMPLETE",
        "started_at_utc": "2026-08-01T00:00:00Z",
        "completed_at_utc": "2026-08-01T00:01:00Z",
        "cursor_checkpoint_committed": True,
        "cursor_checkpoint_pending_lifecycle_commit": False,
        "committed_cursor_fingerprint": PENDING_HASH,
        "remote_lifecycle_commit": {
            "status": "COMMITTED",
            "transaction_id": "lctx_1234567890abcdef1234567890abcdef",
        },
    }
    path = sync._write_run_receipt(
        PROJECT,
        CONNECTOR,
        EPOCH,
        tmp_path,
        run,
    )
    sync._run_summary(registry, run, path)
    sync._save_connector_registry(PROJECT, tmp_path, registry)
    stage_connector_lifecycle_recovery_intent(
        PROJECT,
        CONNECTOR,
        present_resources=[
            {
                "remote_resource_id": "wiki:space-a:node-a",
                "resource_kind": "feishu-wiki-docx",
                "display_title": "Order document",
                "parent_remote_id": "parent-a",
                "remote_space_id": "space-a",
                "remote_revision": "17",
                "materialization_state": "MATERIALIZABLE",
            }
        ],
        next_cursor_fingerprint=PENDING_HASH,
        root=tmp_path,
        actor=ACTOR,
        sync_epoch_id=EPOCH,
    )
    recorded = []
    monkeypatch.setattr(
        supervisor,
        "_record_state",
        lambda *args, **kwargs: recorded.append(kwargs),
    )

    inspection = supervisor.inspect_pending_connector_lifecycle_checkpoint(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    )
    result = supervisor.recover_pending_connector_lifecycle_checkpoint(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
    )

    assert inspection["status"] == "COMPLETED_INTENT_CLEANUP"
    assert inspection["stale"] is False
    assert result["recovery_action"] == "CLEARED_COMMITTED_ORPHAN_INTENT"
    assert load_connector_lifecycle_recovery_intent(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    ) == {}
    assert recorded[0]["state"] == "CLEARED_COMMITTED_ORPHAN_INTENT"
