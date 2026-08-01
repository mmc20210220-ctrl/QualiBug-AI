from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ai_test_asset_center.connector_lifecycle_recovery_intent import (
    load_connector_lifecycle_recovery_intent,
)
from ai_test_asset_center.feishu_lifecycle_recovery_runtime import (
    FeishuLifecycleRecoveryRuntimeError,
    current_feishu_recovery_intent,
    discover_feishu_resources_with_recovery_intent,
    feishu_lifecycle_recovery_scope,
    reconcile_feishu_lifecycle_with_recovery_intent,
    sync_feishu_snapshot_with_recovery_intent,
)

PROJECT = "enterprise-project"
ACTOR = {"name": "tester", "role": "knowledge_admin"}


def _descriptor(connector: str) -> dict:
    return {
        "remote_resource_id": f"wiki:space:{connector}",
        "resource_kind": "feishu-wiki-docx",
        "title": f"Document {connector}",
        "parent_node_token": "parent",
        "space_id": "space",
        "remote_revision": "1",
    }


def _resource(descriptor, capability) -> dict:
    return {
        "remote_resource_id": descriptor["remote_resource_id"],
        "resource_kind": descriptor["resource_kind"],
        "display_title": descriptor["title"],
        "parent_remote_id": descriptor["parent_node_token"],
        "remote_space_id": descriptor["space_id"],
        "remote_revision": descriptor["remote_revision"],
        "materialization_state": "MATERIALIZABLE",
    }


def _scope(
    tmp_path: Path,
    connector: str,
    *,
    snapshot_delegate,
    lifecycle_delegate,
):
    descriptor = _descriptor(connector)
    return feishu_lifecycle_recovery_scope(
        PROJECT,
        connector,
        root=tmp_path,
        actor=ACTOR,
        deletion_policy="RETIRE_MISSING",
        retire_after_complete_snapshots=2,
        max_retire_count=5,
        max_retire_ratio=0.25,
        discovery_delegate=lambda *args, **kwargs: [descriptor],
        snapshot_delegate=snapshot_delegate,
        lifecycle_delegate=lifecycle_delegate,
        classifier=lambda value: object(),
        lifecycle_resource_builder=_resource,
        snapshot_cursor_builder=lambda values: f"cursor:{connector}",
    )


def test_runtime_binds_discovery_snapshot_and_lifecycle_to_one_epoch(
    tmp_path: Path,
) -> None:
    observed = {}

    def snapshot(project_id, **kwargs):
        observed["snapshot_epoch"] = kwargs["sync_epoch_id"]
        observed["snapshot_items"] = kwargs["items"]
        return {
            "status": "COMPLETE",
            "sync_epoch_id": kwargs["sync_epoch_id"],
        }

    def lifecycle(project_id, **kwargs):
        observed["lifecycle_epoch"] = kwargs["sync_epoch_id"]
        observed["resources"] = kwargs["present_resources"]
        return {
            "status": "COMPLETE",
            "cursor_checkpoint_committed": True,
        }

    with _scope(
        tmp_path,
        "feishu-a",
        snapshot_delegate=snapshot,
        lifecycle_delegate=lifecycle,
    ):
        descriptors = discover_feishu_resources_with_recovery_intent(
            "token",
            "scope",
        )
        intent = current_feishu_recovery_intent()
        assert intent["present_resource_count"] == 1
        assert intent["source_content_persisted"] is False
        run = sync_feishu_snapshot_with_recovery_intent(
            PROJECT,
            connector_instance_id="feishu-a",
            items=[],
        )
        result = reconcile_feishu_lifecycle_with_recovery_intent(
            PROJECT,
            connector_instance_id="feishu-a",
            present_resources=[_resource(descriptors[0], object())],
            sync_epoch_id=run["sync_epoch_id"],
        )

    assert result["cursor_checkpoint_committed"] is True
    assert observed["snapshot_epoch"] == observed["lifecycle_epoch"]
    assert observed["resources"][0]["remote_resource_id"] == (
        "wiki:space:feishu-a"
    )
    assert load_connector_lifecycle_recovery_intent(
        PROJECT,
        "feishu-a",
        root=tmp_path,
    ) == {}


def test_snapshot_delegate_cannot_change_recovery_epoch(tmp_path: Path) -> None:
    with _scope(
        tmp_path,
        "feishu-mismatch",
        snapshot_delegate=lambda *args, **kwargs: {
            "status": "COMPLETE",
            "sync_epoch_id": "different-epoch",
        },
        lifecycle_delegate=lambda *args, **kwargs: {},
    ):
        discover_feishu_resources_with_recovery_intent("token", "scope")
        with pytest.raises(
            FeishuLifecycleRecoveryRuntimeError,
            match="snapshot_epoch_mismatch",
        ):
            sync_feishu_snapshot_with_recovery_intent(
                PROJECT,
                connector_instance_id="feishu-mismatch",
                items=[],
            )

    assert load_connector_lifecycle_recovery_intent(
        PROJECT,
        "feishu-mismatch",
        root=tmp_path,
    ) == {}


def test_contexts_isolate_concurrent_connector_epochs(tmp_path: Path) -> None:
    def execute(connector: str) -> tuple[str, str]:
        observed = {}

        def snapshot(project_id, **kwargs):
            observed["epoch"] = kwargs["sync_epoch_id"]
            return {
                "status": "COMPLETE",
                "sync_epoch_id": kwargs["sync_epoch_id"],
            }

        def lifecycle(project_id, **kwargs):
            assert kwargs["sync_epoch_id"] == observed["epoch"]
            assert kwargs["present_resources"][0]["remote_resource_id"].endswith(
                connector
            )
            return {
                "status": "COMPLETE",
                "cursor_checkpoint_committed": True,
            }

        with _scope(
            tmp_path,
            connector,
            snapshot_delegate=snapshot,
            lifecycle_delegate=lifecycle,
        ):
            descriptors = discover_feishu_resources_with_recovery_intent(
                "token",
                "scope",
            )
            run = sync_feishu_snapshot_with_recovery_intent(
                PROJECT,
                connector_instance_id=connector,
                items=[],
            )
            reconcile_feishu_lifecycle_with_recovery_intent(
                PROJECT,
                connector_instance_id=connector,
                present_resources=[_resource(descriptors[0], object())],
                sync_epoch_id=run["sync_epoch_id"],
            )
        return connector, observed["epoch"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(
            executor.map(execute, ["feishu-a", "feishu-b"])
        )

    assert first[0] != second[0]
    assert first[1] != second[1]
    assert load_connector_lifecycle_recovery_intent(
        PROJECT,
        "feishu-a",
        root=tmp_path,
    ) == {}
    assert load_connector_lifecycle_recovery_intent(
        PROJECT,
        "feishu-b",
        root=tmp_path,
    ) == {}
