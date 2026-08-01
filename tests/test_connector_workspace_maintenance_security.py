from __future__ import annotations

import os
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


def test_path_boundary_check_precedes_unlink():
    source = Path(maintenance.__file__).read_text(encoding="utf-8")
    scan = source[source.index("for path in maintenance_root.rglob"):]
    assert "not _is_within(maintenance_root, path)" in scan
    assert scan.index("not _is_within(maintenance_root, path)") < scan.index(
        "path.unlink()"
    )
