from __future__ import annotations

import hashlib
import json

import pytest

from ai_test_asset_center.connector_sync_authority import (
    ConnectorSyncError,
    _instance_by_id,
    _load_connector_registry,
    _save_connector_registry,
    _write_run_receipt,
    abort_connector_sync_run,
    list_connector_instances,
    load_connector_sync_run,
    register_connector_instance,
    sync_connector_snapshot_batch,
)
from ai_test_asset_center.enterprise_knowledge_center import (
    list_enterprise_knowledge_sources,
)


ACTOR = {"name": "qa-owner", "role": "qa_lead"}


def _register(tmp_path, connector: str = "feishu-prod"):
    return register_connector_instance(
        "enterprise-project",
        root=tmp_path,
        connector_instance_id=connector,
        connector_type="feishu",
        display_name="飞书正式资料库",
        resource_scope="wiki-space:quality",
        connection_profile_ref="vault-ref://connectors/feishu-prod",
        metadata={"tenant": "enterprise-a"},
        actor=ACTOR,
    )


def _item(remote_id: str, text: str, revision: str = "1"):
    return {
        "remote_resource_id": remote_id,
        "resource_kind": "document",
        "source_type": "prd",
        "content": text,
        "filename": f"{remote_id}.md",
        "remote_revision": revision,
        "canonical_url": f"https://docs.example.com/{remote_id}?ticket=temporary",
    }


def _active_refs(tmp_path):
    inventory = list_enterprise_knowledge_sources(
        "enterprise-project",
        root=tmp_path,
    )
    return {
        row["source_ref"]
        for row in inventory["sources"]
        if row.get("source_ref", "").startswith("connector://feishu-prod/")
    }


def test_connector_instance_registry_stores_references_not_credentials(tmp_path):
    receipt = _register(tmp_path)
    instance = receipt["connector_instance"]

    assert receipt["created"] is True
    assert instance["connector_instance_id"] == "feishu-prod"
    assert instance["connection_profile_ref"].startswith("vault-ref://")
    assert instance["credentials_persisted"] is False

    payload = json.dumps(
        _load_connector_registry("enterprise-project", root=tmp_path),
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "access_token" not in payload
    assert "password" not in payload

    with pytest.raises(
        ConnectorSyncError,
        match="metadata_secret_key_rejected",
    ):
        register_connector_instance(
            "enterprise-project",
            root=tmp_path,
            connector_instance_id="unsafe",
            connector_type="feishu",
            metadata={"access_token": "must-not-persist"},
            actor=ACTOR,
        )

    with pytest.raises(
        ConnectorSyncError,
        match="metadata_secret_value_rejected",
    ):
        register_connector_instance(
            "enterprise-project",
            root=tmp_path,
            connector_instance_id="unsafe-value",
            connector_type="feishu",
            metadata={"note": "Authorization: Bearer supersecretvalue12345"},
            actor=ACTOR,
        )

    with pytest.raises(
        ConnectorSyncError,
        match="connection_profile_ref_invalid",
    ):
        register_connector_instance(
            "enterprise-project",
            root=tmp_path,
            connector_instance_id="unsafe-ref",
            connector_type="feishu",
            connection_profile_ref="plain-secret-value",
            actor=ACTOR,
        )


def test_incremental_sync_commits_only_cursor_fingerprint(tmp_path):
    _register(tmp_path)
    run = sync_connector_snapshot_batch(
        "enterprise-project",
        root=tmp_path,
        connector_instance_id="feishu-prod",
        sync_mode="INCREMENTAL",
        items=[_item("doc-order", "# 订单规则\n订单创建后为待支付。")],
        next_cursor="cursor-1",
        actor=ACTOR,
    )

    fingerprint = hashlib.sha256(b"cursor-1").hexdigest()
    assert run["status"] == "COMPLETE"
    assert run["success_count"] == 1
    assert run["cursor_checkpoint_committed"] is True
    assert run["committed_cursor_fingerprint"] == fingerprint

    persisted = load_connector_sync_run(
        "enterprise-project",
        connector_instance_id="feishu-prod",
        sync_epoch_id=run["sync_epoch_id"],
        root=tmp_path,
    )
    serialized = json.dumps(persisted, ensure_ascii=False, sort_keys=True)
    assert "cursor-1" not in serialized
    assert "订单创建后为待支付" not in serialized
    assert persisted["raw_cursor_values_persisted"] is False
    assert persisted["source_content_persisted_in_run_receipt"] is False

    instance = list_connector_instances(
        "enterprise-project",
        root=tmp_path,
    )["connector_instances"][0]
    assert instance["last_committed_cursor_fingerprint"] == fingerprint


def test_cursor_mismatch_blocks_before_material_mutation(tmp_path):
    _register(tmp_path)
    first = sync_connector_snapshot_batch(
        "enterprise-project",
        root=tmp_path,
        connector_instance_id="feishu-prod",
        items=[_item("doc-order", "# 订单规则\n订单不得跨租户查看。")],
        next_cursor="cursor-1",
        actor=ACTOR,
    )
    before = set(_active_refs(tmp_path))

    with pytest.raises(ConnectorSyncError, match="cursor_mismatch"):
        sync_connector_snapshot_batch(
            "enterprise-project",
            root=tmp_path,
            connector_instance_id="feishu-prod",
            items=[_item("doc-order", "# 订单规则\n内容变化。", "2")],
            previous_cursor="wrong-cursor",
            next_cursor="cursor-2",
            actor=ACTOR,
        )

    assert _active_refs(tmp_path) == before
    registry = _load_connector_registry("enterprise-project", root=tmp_path)
    instance = _instance_by_id(registry, "feishu-prod")
    assert instance["last_successful_sync_epoch_id"] == first["sync_epoch_id"]
    assert instance["active_sync_epoch_id"] == ""


def test_failed_item_retains_previous_snapshot_and_cursor(tmp_path):
    _register(tmp_path)
    first = sync_connector_snapshot_batch(
        "enterprise-project",
        root=tmp_path,
        connector_instance_id="feishu-prod",
        items=[_item("doc-order", "# 订单规则\n订单只能由所属租户查看。")],
        next_cursor="cursor-1",
        actor=ACTOR,
    )
    before_refs = _active_refs(tmp_path)
    before_fingerprint = hashlib.sha256(b"cursor-1").hexdigest()

    failed = sync_connector_snapshot_batch(
        "enterprise-project",
        root=tmp_path,
        connector_instance_id="feishu-prod",
        items=[
            {
                **_item("doc-order", "Authorization: Bearer supersecretvalue12345", "2"),
            }
        ],
        previous_cursor="cursor-1",
        next_cursor="cursor-2",
        actor=ACTOR,
    )

    assert failed["status"] == "FAILED"
    assert failed["cursor_checkpoint_committed"] is False
    assert failed["errors"][0]["previous_snapshot_retained"] is True
    assert _active_refs(tmp_path) == before_refs

    registry = _load_connector_registry("enterprise-project", root=tmp_path)
    instance = _instance_by_id(registry, "feishu-prod")
    assert instance["last_committed_cursor_fingerprint"] == before_fingerprint
    assert instance["last_successful_sync_epoch_id"] == first["sync_epoch_id"]


def test_complete_full_sync_can_retire_missing_occurrences(tmp_path):
    _register(tmp_path)
    sync_connector_snapshot_batch(
        "enterprise-project",
        root=tmp_path,
        connector_instance_id="feishu-prod",
        sync_mode="FULL",
        snapshot_complete=True,
        items=[
            _item("doc-a", "# A\n规则 A。"),
            _item("doc-b", "# B\n规则 B。"),
        ],
        next_cursor="full-1",
        actor=ACTOR,
    )

    run = sync_connector_snapshot_batch(
        "enterprise-project",
        root=tmp_path,
        connector_instance_id="feishu-prod",
        sync_mode="FULL",
        snapshot_complete=True,
        deletion_policy="RETIRE_MISSING",
        max_retire_count=10,
        max_retire_ratio=1.0,
        previous_cursor="full-1",
        next_cursor="full-2",
        items=[_item("doc-a", "# A\n规则 A。", "2")],
        actor=ACTOR,
    )

    assert run["status"] == "COMPLETE"
    assert run["deletion_reconciliation"]["status"] == "COMPLETE"
    assert run["retired_count"] == 1
    assert _active_refs(tmp_path) == {
        "connector://feishu-prod/document/doc-a"
    }


def test_deletion_guard_blocks_catastrophic_empty_snapshot(tmp_path):
    _register(tmp_path)
    sync_connector_snapshot_batch(
        "enterprise-project",
        root=tmp_path,
        connector_instance_id="feishu-prod",
        sync_mode="FULL",
        snapshot_complete=True,
        items=[
            _item("doc-a", "# A\n规则 A。"),
            _item("doc-b", "# B\n规则 B。"),
            _item("doc-c", "# C\n规则 C。"),
            _item("doc-d", "# D\n规则 D。"),
        ],
        next_cursor="full-1",
        actor=ACTOR,
    )
    before = _active_refs(tmp_path)

    blocked = sync_connector_snapshot_batch(
        "enterprise-project",
        root=tmp_path,
        connector_instance_id="feishu-prod",
        sync_mode="FULL",
        snapshot_complete=True,
        deletion_policy="RETIRE_MISSING",
        max_retire_count=10,
        max_retire_ratio=0.25,
        previous_cursor="full-1",
        next_cursor="full-2",
        items=[],
        actor=ACTOR,
    )

    assert blocked["status"] == "BLOCKED"
    assert blocked["cursor_checkpoint_committed"] is False
    assert blocked["deletion_reconciliation"]["status"] == "BLOCKED"
    assert _active_refs(tmp_path) == before


def test_missing_retirement_requires_explicit_complete_full_snapshot(tmp_path):
    _register(tmp_path)
    with pytest.raises(
        ConnectorSyncError,
        match="requires_complete_full_snapshot",
    ):
        sync_connector_snapshot_batch(
            "enterprise-project",
            root=tmp_path,
            connector_instance_id="feishu-prod",
            sync_mode="INCREMENTAL",
            deletion_policy="RETIRE_MISSING",
            snapshot_complete=True,
            items=[],
            actor=ACTOR,
        )


def test_duplicate_remote_identity_is_rejected_before_run(tmp_path):
    _register(tmp_path)
    duplicate = _item("doc-a", "# A\n规则。")
    with pytest.raises(
        ConnectorSyncError,
        match="duplicate_remote_identity",
    ):
        sync_connector_snapshot_batch(
            "enterprise-project",
            root=tmp_path,
            connector_instance_id="feishu-prod",
            items=[duplicate, dict(duplicate)],
            actor=ACTOR,
        )

    registry = _load_connector_registry("enterprise-project", root=tmp_path)
    assert registry["sync_runs"] == []
    assert _instance_by_id(
        registry, "feishu-prod"
    )["active_sync_epoch_id"] == ""


def test_operator_can_abort_stranded_run_without_advancing_cursor(tmp_path):
    _register(tmp_path)
    registry = _load_connector_registry("enterprise-project", root=tmp_path)
    instance = _instance_by_id(registry, "feishu-prod")
    instance["active_sync_epoch_id"] = "sync_stranded"
    instance["last_committed_cursor_fingerprint"] = hashlib.sha256(
        b"cursor-1"
    ).hexdigest()
    registry["sync_runs"].append(
        {
            "sync_epoch_id": "sync_stranded",
            "connector_instance_id": "feishu-prod",
            "status": "RUNNING",
        }
    )
    _save_connector_registry(
        "enterprise-project", tmp_path, registry
    )
    _write_run_receipt(
        "enterprise-project",
        "feishu-prod",
        "sync_stranded",
        tmp_path,
        {
            "sync_epoch_id": "sync_stranded",
            "connector_instance_id": "feishu-prod",
            "status": "RUNNING",
        },
    )

    aborted = abort_connector_sync_run(
        "enterprise-project",
        connector_instance_id="feishu-prod",
        reason="worker terminated before completion",
        root=tmp_path,
        actor=ACTOR,
    )

    assert aborted["status"] == "ABORTED"
    assert aborted["cursor_checkpoint_committed"] is False
    registry = _load_connector_registry("enterprise-project", root=tmp_path)
    instance = _instance_by_id(registry, "feishu-prod")
    assert instance["active_sync_epoch_id"] == ""
    assert instance["last_committed_cursor_fingerprint"] == hashlib.sha256(
        b"cursor-1"
    ).hexdigest()
