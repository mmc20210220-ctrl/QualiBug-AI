from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from ai_test_asset_center import feishu_connector_adapter as adapter


PROJECT = "online-materialization-project"
CONNECTOR = "feishu-prod"
TOKEN = "tenant-token-123456"


def _descriptor(index: int) -> dict:
    return {
        "space_id": "space1",
        "node_token": f"node{index}",
        "obj_token": f"docx{index}",
        "obj_type": "docx",
        "title": f"Document {index}",
        "parent_node_token": "",
        "has_child": False,
        "remote_revision": "1",
        "remote_updated_at": "1",
        "remote_resource_id": f"wiki:space1:node{index}",
        "resource_kind": "feishu-wiki-docx",
    }


def _materialized(descriptor: dict) -> dict:
    token = descriptor["node_token"]
    return {
        "remote_resource_id": descriptor["remote_resource_id"],
        "resource_kind": descriptor["resource_kind"],
        "source_type": "feishu_document",
        "content": f"# {token}\ncontent for {token}",
        "filename": f"{token}.txt",
        "remote_revision": descriptor["remote_revision"],
        "remote_updated_at": descriptor["remote_updated_at"],
        "parent_remote_id": descriptor["parent_node_token"],
        "export_format": "txt",
        "declared_mime": "text/plain",
        "adapter_degraded": False,
        "degradation_reason": "",
    }


def _wire_sync_boundary(
    monkeypatch: pytest.MonkeyPatch,
    descriptors: list[dict],
    captured: dict,
) -> None:
    monkeypatch.setattr(
        adapter,
        "_connector_instance",
        lambda *args, **kwargs: {
            "connector_instance_id": CONNECTOR,
            "connector_type": "feishu",
            "status": "ACTIVE",
            "resource_scope": "wiki-space:space1",
            "connection_profile_ref": "connection-profile://feishu-prod",
            "last_committed_cursor_fingerprint": "",
        },
    )
    monkeypatch.setattr(
        adapter,
        "_resolve_access_token",
        lambda *args, **kwargs: (TOKEN, "tenant_access_token"),
    )
    monkeypatch.setattr(
        adapter,
        "discover_feishu_wiki_resources",
        lambda *args, **kwargs: [dict(row) for row in descriptors],
    )
    monkeypatch.setattr(
        adapter,
        "connector_snapshot_observation_index",
        lambda *args, **kwargs: {},
    )

    def sync_batch(*args, **kwargs):
        captured["batch_called"] = True
        captured["items"] = list(kwargs["items"])
        captured["unchanged_observations"] = list(
            kwargs["unchanged_observations"]
        )
        return {
            "status": "COMPLETE",
            "success_count": len(kwargs["items"]),
            "failure_count": 0,
        }

    monkeypatch.setattr(adapter, "sync_connector_snapshot_batch", sync_batch)


def test_changed_materialization_is_bounded_parallel_and_ordered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptors = [_descriptor(index) for index in range(6)]
    captured: dict = {}
    _wire_sync_boundary(monkeypatch, descriptors, captured)
    monkeypatch.setenv("QUALIBUG_FEISHU_MATERIALIZATION_WORKERS", "4")

    lock = threading.Lock()
    release = threading.Event()
    active = 0
    max_active = 0

    def materialize(descriptor, *args, **kwargs):
        nonlocal active, max_active
        index = int(descriptor["node_token"].removeprefix("node"))
        with lock:
            active += 1
            max_active = max(max_active, active)
            if max_active >= 2:
                release.set()
        assert release.wait(timeout=1.0), "materialization remained serial"
        time.sleep((len(descriptors) - index) * 0.002)
        try:
            return _materialized(descriptor)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(adapter, "materialize_feishu_resource", materialize)

    receipt = adapter.sync_feishu_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        resolve_connection_profile=lambda _: {},
        root=tmp_path,
        actor={"name": "connector", "role": "connector_service"},
        sleeper=lambda _: None,
    )

    assert max_active == 4
    assert receipt["status"] == "COMPLETE"
    assert receipt["materialized_resource_count"] == 6
    assert receipt["materialization_worker_count"] == 4
    assert receipt["parallel_materialization_used"] is True
    assert captured["batch_called"] is True
    assert captured["unchanged_observations"] == []
    assert [row["remote_resource_id"] for row in captured["items"]] == [
        row["remote_resource_id"] for row in descriptors
    ]
    assert all(
        row["remote_materialization_fingerprint"]
        for row in captured["items"]
    )


def test_materialization_failure_never_reaches_atomic_ingestion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptors = [_descriptor(index) for index in range(4)]
    captured: dict = {}
    _wire_sync_boundary(monkeypatch, descriptors, captured)
    monkeypatch.setenv("QUALIBUG_FEISHU_MATERIALIZATION_WORKERS", "4")

    started = threading.Barrier(4)

    def materialize(descriptor, *args, **kwargs):
        started.wait(timeout=1.0)
        if descriptor["node_token"] == "node1":
            raise adapter.FeishuConnectorError("synthetic_export_failure")
        return _materialized(descriptor)

    monkeypatch.setattr(adapter, "materialize_feishu_resource", materialize)

    with pytest.raises(
        adapter.FeishuConnectorError,
        match="synthetic_export_failure",
    ):
        adapter.sync_feishu_connector(
            PROJECT,
            connector_instance_id=CONNECTOR,
            resolve_connection_profile=lambda _: {},
            root=tmp_path,
            actor={"name": "connector", "role": "connector_service"},
            sleeper=lambda _: None,
        )

    assert captured.get("batch_called") is not True


def test_materialization_worker_policy_fails_fast_on_invalid_operator_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUALIBUG_FEISHU_MATERIALIZATION_WORKERS", "9")
    with pytest.raises(
        adapter.FeishuConnectorError,
        match="workers_out_of_range",
    ):
        adapter._materialization_worker_count(2)
