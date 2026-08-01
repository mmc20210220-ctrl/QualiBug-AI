from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from ai_test_asset_center import connector_workspace_maintenance as maintenance


PROJECT = "enterprise-project"
CONNECTOR = "feishu-main"
ACTOR = {"name": "auto", "role": "knowledge_admin"}


def _old(path: Path) -> None:
    timestamp = time.time() - 2 * 60 * 60
    os.utime(path, (timestamp, timestamp))


def _missing_owner(*args, **kwargs):
    return {
        "state": "MISSING",
        "owner_alive": None,
        "owner_dead": False,
    }


def _make_directory_link(link: Path, target: Path) -> bool:
    """Create a directory link (POSIX symlink / Windows junction when possible)."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except OSError:
        try:
            os.symlink(target, link, target_is_directory=True)
            return True
        except OSError:
            return False


def test_maintenance_does_not_follow_directory_symlink_escape(
    monkeypatch,
    tmp_path: Path,
):
    registry = {
        "project_id": PROJECT,
        "connector_instances": [
            {
                "connector_instance_id": CONNECTOR,
                "active_sync_epoch_id": "",
            }
        ],
        "audit_events": [],
        "governance": {},
    }
    monkeypatch.setattr(maintenance, "_retention_seconds", lambda: 60)
    monkeypatch.setattr(maintenance, "_scan_limit", lambda: 1000)
    monkeypatch.setattr(
        maintenance,
        "_load_connector_registry",
        lambda *a, **k: registry,
    )
    monkeypatch.setattr(
        maintenance,
        "_save_connector_registry",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        maintenance,
        "inspect_connector_sync_ownership",
        _missing_owner,
    )
    monkeypatch.setattr(
        maintenance,
        "_load_registry",
        lambda *a, **k: {"sources": []},
    )

    source_dir = maintenance._paths(PROJECT, tmp_path)["source_dir"]
    source_dir.mkdir(parents=True, exist_ok=True)
    workspace = source_dir.parent
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_temp = outside / ".must-stay.tmp"
    outside_temp.write_text("outside", encoding="utf-8")
    _old(outside_temp)

    escape = workspace / "escape"
    try:
        escape.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    result = maintenance.maintain_connector_workspace(
        PROJECT,
        root=tmp_path,
        actor=ACTOR,
    )

    assert result["path_boundary_enforced"] is True
    assert outside_temp.exists()
    assert outside_temp.read_text(encoding="utf-8") == "outside"


def test_project_workspace_symlink_outside_root_blocks_maintenance(
    tmp_path: Path,
):
    workspace_root = tmp_path / "platform_workspace"
    workspace_root.mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside-project"
    outside.mkdir(exist_ok=True)
    project_link = workspace_root / PROJECT
    try:
        project_link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    result = maintenance.maintain_connector_workspace(
        PROJECT,
        root=tmp_path,
        actor=ACTOR,
    )

    assert result["status"] == "BLOCKED_PATH_BOUNDARY"
    assert result["temporary_files_removed"] == 0
    assert result["path_boundary_enforced"] is True


def test_regular_file_walker_never_descends_into_directory_links(
    tmp_path: Path,
):
    real = tmp_path / "real"
    real.mkdir()
    (real / "secret.tmp").write_text("secret", encoding="utf-8")
    link = tmp_path / "escape"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    yielded = list(maintenance._iter_regular_files(tmp_path))
    names = {path.name for path in yielded}
    assert "secret.tmp" not in names
    assert "escape" not in names


def test_path_boundary_check_precedes_unlink():
    source = Path(maintenance.__file__).read_text(encoding="utf-8")
    scan = source[
        source.index("for path in _iter_regular_files(resolved_maintenance_root)")
        :
    ]
    assert "not _is_within(resolved_maintenance_root, path)" in scan
    assert scan.index("not _is_within(resolved_maintenance_root, path)") < scan.index(
        "path.unlink()"
    )


def test_internal_directory_link_never_deletes_customer_tmp_source(
    monkeypatch,
    tmp_path: Path,
):
    """Regression: a directory link inside the maintenance root used to break the
    resolved-vs-raw path basis, so a customer upload named ``*.json.tmp`` inside
    ``sources/`` was misclassified as atomic-write residue and deleted.
    """
    registry = {
        "project_id": PROJECT,
        "connector_instances": [
            {
                "connector_instance_id": CONNECTOR,
                "active_sync_epoch_id": "",
            }
        ],
        "audit_events": [],
        "governance": {},
    }
    monkeypatch.setattr(maintenance, "_retention_seconds", lambda: 60)
    monkeypatch.setattr(maintenance, "_scan_limit", lambda: 1000)
    monkeypatch.setattr(
        maintenance,
        "_load_connector_registry",
        lambda *a, **k: registry,
    )
    monkeypatch.setattr(
        maintenance,
        "_save_connector_registry",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        maintenance,
        "inspect_connector_sync_ownership",
        _missing_owner,
    )
    monkeypatch.setattr(
        maintenance,
        "_load_registry",
        lambda *a, **k: {"sources": []},
    )

    storage = tmp_path / "storage"
    source_dir = (
        storage
        / "pw_target"
        / PROJECT
        / "enterprise_knowledge_center"
        / "sources"
    )
    source_dir.mkdir(parents=True, exist_ok=True)
    customer = source_dir / "customer_report.json.tmp"
    customer.write_text("customer material", encoding="utf-8")
    _old(customer)

    link = tmp_path / "platform_workspace"
    if not _make_directory_link(link, storage / "pw_target"):
        pytest.skip("directory link unavailable")

    result = maintenance.maintain_connector_workspace(
        PROJECT,
        root=tmp_path,
        actor=ACTOR,
    )

    assert result["status"] == "COMPLETE"
    assert result["temporary_files_removed"] == 0
    assert customer.exists()
    assert customer.read_text(encoding="utf-8") == "customer material"


def test_ownership_dir_scan_blocks_when_registry_has_no_instance(
    monkeypatch,
    tmp_path: Path,
):
    """Regression: ownership inspection was driven by registry enumeration, so a
    live owner was missed when the registry held no connector instance (damaged,
    rolled back, or externally cleared registry) and cleanup ran anyway.
    """
    registry = {
        "project_id": PROJECT,
        "connector_instances": [],
        "audit_events": [],
        "governance": {},
    }
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
    ownership_dir = workspace / "connector_sync_ownership"
    ownership_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    (ownership_dir / f"{CONNECTOR}.json").write_text(
        json.dumps(
            {
                "state": "ACTIVE",
                "pid": os.getpid(),
                "owner_thread_id": threading.current_thread().ident,
                "last_heartbeat_unix": now,
                "last_progress_unix": now,
                "started_unix": now,
            }
        ),
        encoding="utf-8",
    )

    result = maintenance.maintain_connector_workspace(
        PROJECT,
        root=tmp_path,
        actor=ACTOR,
    )

    assert result["status"] == "SKIPPED_ACTIVE_MUTATION"
    assert result["reason"] == "SYNC_OWNER_ALIVE"


def test_invalid_retention_env_fails_fast(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(
        "QUALIBUG_CONNECTOR_TEMP_RETENTION_SECONDS",
        "not-a-number",
    )
    result = maintenance.maintain_connector_workspace(
        PROJECT,
        root=tmp_path,
        actor=ACTOR,
    )
    assert result["status"] == "FAILED_MAINTENANCE"
    assert "QUALIBUG_CONNECTOR_TEMP_RETENTION_SECONDS" in result["error_detail"]
    assert result["temporary_files_removed"] == 0


def test_out_of_range_retention_env_fails_fast(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("QUALIBUG_CONNECTOR_TEMP_RETENTION_SECONDS", "5")
    result = maintenance.maintain_connector_workspace(
        PROJECT,
        root=tmp_path,
        actor=ACTOR,
    )
    assert result["status"] == "FAILED_MAINTENANCE"
    assert "out of range" in result["error_detail"]
