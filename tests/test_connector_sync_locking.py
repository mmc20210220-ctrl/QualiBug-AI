from __future__ import annotations

import hashlib

import pytest

from ai_test_asset_center.connector_sync_authority import (
    ConnectorSyncError,
    _instance_by_id,
    _load_connector_registry,
    _lock_path,
    _save_connector_registry,
    _sync_lock,
    _write_run_receipt,
    abort_connector_sync_run,
    register_connector_instance,
    sync_connector_snapshot_batch,
)


ACTOR = {"name": "qa-owner", "role": "qa_lead"}
PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"


def _register(tmp_path) -> None:
    register_connector_instance(
        PROJECT,
        root=tmp_path,
        connector_instance_id=CONNECTOR,
        connector_type="feishu",
        connection_profile_ref="vault-ref://connectors/feishu-prod",
        actor=ACTOR,
    )


def _item() -> dict[str, str]:
    return {
        "remote_resource_id": "doc-order",
        "resource_kind": "document",
        "source_type": "prd",
        "content": "# 订单规则\n订单只能由所属租户查看。",
        "filename": "订单规则.md",
    }


def test_filesystem_lease_blocks_second_process_before_run_creation(tmp_path):
    _register(tmp_path)

    with _sync_lock(PROJECT, CONNECTOR, "sync_held", tmp_path):
        with pytest.raises(ConnectorSyncError, match="connector_sync_lock_held"):
            sync_connector_snapshot_batch(
                PROJECT,
                root=tmp_path,
                connector_instance_id=CONNECTOR,
                items=[_item()],
                next_cursor="cursor-1",
                actor=ACTOR,
            )

        registry = _load_connector_registry(PROJECT, root=tmp_path)
        assert registry["sync_runs"] == []
        assert _instance_by_id(registry, CONNECTOR)["active_sync_epoch_id"] == ""

    assert not _lock_path(PROJECT, CONNECTOR, tmp_path).exists()


def test_abort_reconciles_stranded_registry_and_filesystem_lease(tmp_path):
    _register(tmp_path)
    registry = _load_connector_registry(PROJECT, root=tmp_path)
    instance = _instance_by_id(registry, CONNECTOR)
    instance["active_sync_epoch_id"] = "sync_stranded"
    instance["last_committed_cursor_fingerprint"] = hashlib.sha256(
        b"cursor-1"
    ).hexdigest()
    registry["sync_runs"].append(
        {
            "sync_epoch_id": "sync_stranded",
            "connector_instance_id": CONNECTOR,
            "status": "RUNNING",
        }
    )
    _save_connector_registry(PROJECT, tmp_path, registry)
    _write_run_receipt(
        PROJECT,
        CONNECTOR,
        "sync_stranded",
        tmp_path,
        {
            "sync_epoch_id": "sync_stranded",
            "connector_instance_id": CONNECTOR,
            "status": "RUNNING",
        },
    )
    lock = _lock_path(PROJECT, CONNECTOR, tmp_path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("sync_stranded", encoding="utf-8")

    aborted = abort_connector_sync_run(
        PROJECT,
        connector_instance_id=CONNECTOR,
        reason="worker terminated before completion",
        root=tmp_path,
        actor=ACTOR,
    )

    assert aborted["status"] == "ABORTED"
    assert aborted["cursor_checkpoint_committed"] is False
    assert not lock.exists()
    registry = _load_connector_registry(PROJECT, root=tmp_path)
    instance = _instance_by_id(registry, CONNECTOR)
    assert instance["active_sync_epoch_id"] == ""
    assert instance["last_committed_cursor_fingerprint"] == hashlib.sha256(
        b"cursor-1"
    ).hexdigest()


def test_abort_refuses_lock_registry_epoch_mismatch(tmp_path):
    _register(tmp_path)
    registry = _load_connector_registry(PROJECT, root=tmp_path)
    _instance_by_id(registry, CONNECTOR)["active_sync_epoch_id"] = "sync_registry"
    _save_connector_registry(PROJECT, tmp_path, registry)
    lock = _lock_path(PROJECT, CONNECTOR, tmp_path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("sync_other", encoding="utf-8")

    with pytest.raises(ConnectorSyncError, match="lock_registry_mismatch"):
        abort_connector_sync_run(
            PROJECT,
            connector_instance_id=CONNECTOR,
            reason="operator recovery",
            root=tmp_path,
            actor=ACTOR,
        )

    assert lock.exists()
    registry = _load_connector_registry(PROJECT, root=tmp_path)
    assert _instance_by_id(registry, CONNECTOR)["active_sync_epoch_id"] == "sync_registry"
