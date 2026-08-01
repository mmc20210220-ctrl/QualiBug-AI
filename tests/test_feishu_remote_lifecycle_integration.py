from __future__ import annotations

from pathlib import Path

import pytest

import ai_test_asset_center.feishu_connector_capability_sync as sync
from ai_test_asset_center.feishu_connector_adapter import FeishuConnectorError

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"
ACTOR = {"name": "tester", "role": "knowledge_admin"}


def _instance() -> dict:
    return {
        "connector_instance_id": CONNECTOR,
        "connector_type": "feishu",
        "status": "ACTIVE",
        "resource_scope": "wiki-all-accessible",
        "connection_profile_ref": "connection-profile://feishu-prod",
        "last_committed_cursor_fingerprint": "",
    }


def _descriptor(obj_type: str = "mindnote") -> dict:
    return {
        "space_id": "space-a",
        "node_token": "node-a",
        "obj_token": "object-a",
        "obj_type": obj_type,
        "title": "Customer document",
        "parent_node_token": "parent-a",
        "has_child": False,
        "remote_revision": "1",
        "remote_updated_at": "1",
        "remote_resource_id": "wiki:space-a:node-a",
        "resource_kind": f"feishu-wiki-{obj_type}",
    }


def _install_common(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync, "_connector_instance", lambda *args, **kwargs: _instance())
    monkeypatch.setattr(
        sync,
        "_resolve_access_token",
        lambda *args, **kwargs: ("access-token", "internal_app"),
    )
    monkeypatch.setattr(
        sync,
        "connector_snapshot_observation_index",
        lambda *args, **kwargs: {},
    )


def test_requested_retirement_is_not_forwarded_to_ingestion_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_common(monkeypatch)
    monkeypatch.setattr(
        sync,
        "discover_feishu_wiki_resources",
        lambda *args, **kwargs: [_descriptor("mindnote")],
    )
    captured_sync = {}
    captured_lifecycle = {}

    def snapshot_batch(*args, **kwargs):
        captured_sync.update(kwargs)
        return {
            "status": "COMPLETE",
            "sync_epoch_id": kwargs["sync_epoch_id"],
            "cursor_checkpoint_committed": False,
        }

    def reconcile(*args, **kwargs):
        captured_lifecycle.update(kwargs)
        return {
            "status": "COMPLETE",
            "requested_deletion_policy": kwargs["deletion_policy"],
            "effective_deletion_policy": "GUARDED_REMOTE_SCOPE_RETIREMENT",
            "absent_count": 0,
            "unconfirmed_missing_count": 0,
            "retirement_eligible_count": 0,
            "retired_count": 0,
            "renamed_resource_count": 0,
            "moved_resource_count": 0,
            "reappeared_resource_count": 0,
            "remote_deletion_inferred": False,
            "permission_loss_inferred": False,
            "cursor_checkpoint_committed": True,
            "customer_material_mutation_executed": False,
        }

    monkeypatch.setattr(sync, "sync_connector_snapshot_batch", snapshot_batch)
    monkeypatch.setattr(sync, "reconcile_connector_remote_lifecycle", reconcile)

    result = sync.sync_feishu_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        resolve_connection_profile=lambda ref: {"auth_mode": "internal_app"},
        root=tmp_path,
        actor=ACTOR,
        deletion_policy="RETIRE_MISSING",
    )

    assert captured_sync["deletion_policy"] == "RETAIN"
    assert captured_sync["snapshot_complete"] is True
    assert captured_lifecycle["deletion_policy"] == "RETIRE_MISSING"
    assert captured_lifecycle["authoritative_snapshot_complete"] is True
    assert captured_lifecycle["sync_epoch_id"] == captured_sync["sync_epoch_id"]
    assert captured_lifecycle["present_resources"][0]["remote_resource_id"] == (
        "wiki:space-a:node-a"
    )
    assert result["effective_deletion_policy"] == (
        "GUARDED_REMOTE_SCOPE_RETIREMENT"
    )
    assert result["cursor_checkpoint_committed"] is True
    assert result["remote_deletion_inferred"] is False
    assert result["customer_material_mutation_executed"] is False


def test_discovery_permission_failure_does_not_run_lifecycle_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_common(monkeypatch)
    lifecycle_calls = []

    def discover(*args, **kwargs):
        raise FeishuConnectorError("feishu_api_failed:code_99991663")

    monkeypatch.setattr(sync, "discover_feishu_wiki_resources", discover)
    monkeypatch.setattr(
        sync,
        "reconcile_connector_remote_lifecycle",
        lambda *args, **kwargs: lifecycle_calls.append(kwargs),
    )

    with pytest.raises(FeishuConnectorError, match="99991663"):
        sync.sync_feishu_connector(
            PROJECT,
            connector_instance_id=CONNECTOR,
            resolve_connection_profile=lambda ref: {"auth_mode": "internal_app"},
            root=tmp_path,
            actor=ACTOR,
            deletion_policy="RETIRE_MISSING",
        )

    assert lifecycle_calls == []


def test_materialization_failure_does_not_advance_remote_absence_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_common(monkeypatch)
    monkeypatch.setattr(
        sync,
        "discover_feishu_wiki_resources",
        lambda *args, **kwargs: [_descriptor("docx")],
    )
    lifecycle_calls = []

    def materialize(*args, **kwargs):
        raise FeishuConnectorError("feishu_export_poll_exhausted")

    monkeypatch.setattr(sync, "_materialize_changed_resources", materialize)
    monkeypatch.setattr(
        sync,
        "reconcile_connector_remote_lifecycle",
        lambda *args, **kwargs: lifecycle_calls.append(kwargs),
    )

    with pytest.raises(FeishuConnectorError, match="export_poll_exhausted"):
        sync.sync_feishu_connector(
            PROJECT,
            connector_instance_id=CONNECTOR,
            resolve_connection_profile=lambda ref: {"auth_mode": "internal_app"},
            root=tmp_path,
            actor=ACTOR,
            deletion_policy="RETIRE_MISSING",
        )

    assert lifecycle_calls == []


def test_incomplete_supported_sync_skips_lifecycle_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_common(monkeypatch)
    monkeypatch.setattr(
        sync,
        "discover_feishu_wiki_resources",
        lambda *args, **kwargs: [_descriptor("mindnote")],
    )

    def incomplete_snapshot(*args, **kwargs):
        return {
            "status": "FAILED",
            "sync_epoch_id": kwargs["sync_epoch_id"],
            "cursor_checkpoint_committed": False,
        }

    monkeypatch.setattr(sync, "sync_connector_snapshot_batch", incomplete_snapshot)
    lifecycle_calls = []
    monkeypatch.setattr(
        sync,
        "reconcile_connector_remote_lifecycle",
        lambda *args, **kwargs: lifecycle_calls.append(kwargs),
    )

    result = sync.sync_feishu_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        resolve_connection_profile=lambda ref: {"auth_mode": "internal_app"},
        root=tmp_path,
        actor=ACTOR,
        deletion_policy="RETIRE_MISSING",
    )

    assert lifecycle_calls == []
    assert result["remote_lifecycle_status"] == "SKIPPED_SYNC_INCOMPLETE"
    assert result["retired_count"] == 0
