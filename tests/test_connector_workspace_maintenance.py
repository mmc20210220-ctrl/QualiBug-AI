from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from ai_test_asset_center import connector_sync_fencing as fencing
from ai_test_asset_center import connector_workspace_maintenance as maintenance


PROJECT = "enterprise-project"
CONNECTOR = "feishu-main"
ACTOR = {"name": "auto", "role": "knowledge_admin"}


def _old(path: Path) -> None:
    timestamp = time.time() - 2 * 60 * 60
    os.utime(path, (timestamp, timestamp))


def _registry(active_epoch: str = "") -> dict:
    return {
        "project_id": PROJECT,
        "connector_instances": [
            {
                "connector_instance_id": CONNECTOR,
                "active_sync_epoch_id": active_epoch,
            }
        ],
        "audit_events": [],
        "governance": {},
    }


def test_maintenance_removes_only_old_atomic_temporary_files(
    monkeypatch,
    tmp_path: Path,
):
    registry = _registry()
    saved: list[dict] = []
    monkeypatch.setattr(maintenance, "_retention_seconds", lambda: 60 * 60)
    monkeypatch.setattr(maintenance, "_scan_limit", lambda: 1000)
    monkeypatch.setattr(
        maintenance,
        "_load_connector_registry",
        lambda *a, **k: registry,
    )
    monkeypatch.setattr(
        maintenance,
        "_save_connector_registry",
        lambda *a, **k: saved.append(dict(registry)),
    )
    monkeypatch.setattr(
        maintenance,
        "inspect_connector_sync_ownership",
        lambda *a, **k: {
            "state": "MISSING",
            "owner_alive": None,
            "owner_dead": False,
        },
    )

    paths = maintenance._paths(PROJECT, tmp_path)
    source_dir = paths["source_dir"]
    source_dir.mkdir(parents=True, exist_ok=True)
    referenced = source_dir / "src_ref_v1_prd.md"
    referenced.write_text("referenced", encoding="utf-8")
    referenced_relative = referenced.relative_to(tmp_path).as_posix()
    monkeypatch.setattr(
        maintenance,
        "_load_registry",
        lambda *a, **k: {
            "sources": [{"stored_path": referenced_relative}],
        },
    )

    knowledge_workspace = (
        tmp_path
        / "platform_workspace"
        / PROJECT
        / "enterprise_knowledge_center"
    )
    runtime_registry = (
        tmp_path
        / "platform_workspace"
        / PROJECT
        / "source_registry"
    )
    runtime_registry.mkdir(parents=True, exist_ok=True)

    knowledge_temp = knowledge_workspace / ".connector_sync_registry.dead.tmp"
    knowledge_temp.parent.mkdir(parents=True, exist_ok=True)
    knowledge_temp.write_text("temporary", encoding="utf-8")
    _old(knowledge_temp)

    runtime_temp = runtime_registry / "registry.json.tmp"
    runtime_temp.write_text("temporary", encoding="utf-8")
    _old(runtime_temp)

    source_temp = source_dir / ".src_v1_dead.tmp"
    source_temp.write_text("temporary", encoding="utf-8")
    _old(source_temp)

    # A customer source can legitimately end in .tmp. It must not be inferred to be residue.
    customer_tmp_source = source_dir / "src_x_v1_customer.json.tmp"
    customer_tmp_source.write_text("customer material", encoding="utf-8")
    _old(customer_tmp_source)

    detached = source_dir / "detached_immutable.bin"
    detached.write_bytes(b"retained")
    _old(detached)

    fresh_temp = knowledge_workspace / ".fresh.tmp"
    fresh_temp.write_text("fresh", encoding="utf-8")

    result = maintenance.maintain_connector_workspace(
        PROJECT,
        root=tmp_path,
        actor=ACTOR,
        trigger_connector_instance_id=CONNECTOR,
    )

    assert result["status"] == "COMPLETE"
    assert result["temporary_files_removed"] == 3
    assert not knowledge_temp.exists()
    assert not runtime_temp.exists()
    assert not source_temp.exists()
    assert customer_tmp_source.exists()
    assert detached.exists()
    assert fresh_temp.exists()
    assert referenced.exists()
    assert result["detached_immutable_source_count"] == 2
    assert result["detached_immutable_sources_deleted"] is False
    assert result["checkpoint_artifacts_deleted"] is False
    assert result["run_receipts_deleted"] is False
    assert result["historical_source_bytes_retained"] is True
    assert result["raw_source_names_returned"] is False
    assert saved
    event = registry["audit_events"][-1]
    assert event["event"] == "maintain_connector_workspace"
    assert event["raw_source_names_persisted"] is False
    assert "detached_immutable.bin" not in repr(event)


def test_maintenance_skips_when_a_sync_epoch_is_active(
    monkeypatch,
    tmp_path: Path,
):
    registry = _registry("sync_live")
    monkeypatch.setattr(maintenance, "_retention_seconds", lambda: 60)
    monkeypatch.setattr(
        maintenance,
        "_load_connector_registry",
        lambda *a, **k: registry,
    )
    monkeypatch.setattr(
        maintenance,
        "_save_connector_registry",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("active maintenance must not persist")
        ),
    )

    workspace = (
        tmp_path
        / "platform_workspace"
        / PROJECT
        / "enterprise_knowledge_center"
    )
    target = workspace / ".old.tmp"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old", encoding="utf-8")
    _old(target)

    result = maintenance.maintain_connector_workspace(
        PROJECT,
        root=tmp_path,
        actor=ACTOR,
    )
    assert result["status"] == "SKIPPED_ACTIVE_MUTATION"
    assert result["reason"] == "ACTIVE_SYNC_EPOCH"
    assert target.exists()


def test_maintenance_never_deletes_recovery_artifacts(
    monkeypatch,
    tmp_path: Path,
):
    registry = _registry()
    monkeypatch.setattr(maintenance, "_retention_seconds", lambda: 60)
    monkeypatch.setattr(
        maintenance,
        "_load_connector_registry",
        lambda *a, **k: registry,
    )

    workspace = (
        tmp_path
        / "platform_workspace"
        / PROJECT
        / "enterprise_knowledge_center"
    )
    journal = workspace / "connector_checkpoint_journal" / f"{CONNECTOR}.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("{}", encoding="utf-8")
    target = workspace / ".old.tmp"
    target.write_text("old", encoding="utf-8")
    _old(journal)
    _old(target)

    result = maintenance.maintain_connector_workspace(
        PROJECT,
        root=tmp_path,
        actor=ACTOR,
    )
    assert result["status"] == "SKIPPED_ACTIVE_MUTATION"
    assert result["reason"] == "CHECKPOINT_JOURNAL_PRESENT"
    assert journal.exists()
    assert target.exists()
    assert result["checkpoint_artifacts_deleted"] is False


def test_fence_finalizer_maintenance_failure_never_masks_business_result(
    monkeypatch,
    tmp_path: Path,
):
    completed: list[int] = []
    monkeypatch.setattr(
        fencing,
        "acquire_connector_sync_fence",
        lambda *a, **k: {
            "project_id": PROJECT,
            "connector_instance_id": CONNECTOR,
            "fencing_token": 7,
        },
    )

    @contextmanager
    def fake_write_fence(*args, **kwargs):
        yield {}

    monkeypatch.setattr(fencing, "connector_write_fence", fake_write_fence)
    monkeypatch.setattr(
        fencing,
        "maintain_connector_workspace",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    monkeypatch.setattr(
        fencing,
        "_complete_connector_sync_fence",
        lambda *a, **k: completed.append(int(a[2])) or {"completed": True},
    )

    with fencing.managed_connector_sync_fence(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
    ):
        business_result = "COMPLETE"

    assert business_result == "COMPLETE"
    assert completed == [7]


def test_fence_runs_maintenance_before_lease_completion():
    source = Path(fencing.__file__).read_text(encoding="utf-8")
    function = source[source.index("def managed_connector_sync_fence"):]
    assert "maintain_connector_workspace(" in function
    assert function.index("maintain_connector_workspace(") < function.index(
        "_complete_connector_sync_fence("
    )
    assert "must never mask the business operation" in function


def test_maintenance_authority_is_registered_as_core():
    architecture_path = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center"
        / "architecture_roots.json"
    )
    architecture = json.loads(architecture_path.read_text(encoding="utf-8"))
    assert architecture["module_class_overrides"][
        "ai_test_asset_center.connector_workspace_maintenance"
    ] == "core"


def test_maintenance_has_no_parallel_registry_or_lifecycle_deletion_path():
    source = Path(maintenance.__file__).read_text(encoding="utf-8")
    assert "maintenance_registry" not in source
    assert "_remove_sync_lock" not in source
    assert "abort_connector_sync_run" not in source
    assert "stop_connector_sync_ownership" not in source
    assert "delete_enterprise_knowledge_source" not in source
    assert "shutil.rmtree" not in source
    assert '"second_maintenance_registry_created": False' in source
    assert source.index("if not _is_atomic_temporary_file(") < source.index(
        "path.unlink()"
    )
