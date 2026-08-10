from __future__ import annotations

from ai_test_asset_center.private_pilot_continuous import (
    _get_continuous_state,
    _public_scan_owner,
)
from ai_test_asset_center.private_pilot_scan_coordinator import project_scan_lease


def test_public_scan_owner_strips_internal_coordination_fields() -> None:
    public = _public_scan_owner(
        {
            "schema": "qualibug.project-scan-lease.v1",
            "token": "secret-lease-token",
            "pid": 123,
            "thread_id": 456,
            "project_id": "project_live",
            "tenant_id": "tenant_private",
            "mode": "manual_scan",
            "actor": {"name": "operator", "role": "admin"},
            "started_at_utc": "2026-08-10T02:00:00Z",
            "started_unix": 1.0,
        }
    )

    assert public == {
        "schema": "qualibug.project-scan-live-status.v1",
        "project_id": "project_live",
        "mode": "manual_scan",
        "started_at_utc": "2026-08-10T02:00:00Z",
    }
    for forbidden in ("token", "pid", "thread_id", "tenant_id", "actor"):
        assert forbidden not in public


def test_continuous_status_projects_real_project_scan_lease(tmp_path) -> None:
    project = "project_live"

    idle = _get_continuous_state(tmp_path, project)
    assert idle["active_scan_live"] is False
    assert idle["active_scan"] == {}

    with project_scan_lease(
        tmp_path,
        project,
        mode="manual_scan",
        tenant_id="tenant_private",
        actor={"name": "operator", "role": "admin"},
    ):
        running = _get_continuous_state(tmp_path, project)
        assert running["active_scan_live"] is True
        assert running["active_scan"]["schema"] == "qualibug.project-scan-live-status.v1"
        assert running["active_scan"]["project_id"] == project
        assert running["active_scan"]["mode"] == "manual_scan"
        assert running["active_scan"]["started_at_utc"]
        assert running["active_scan_elapsed_seconds"] >= 0
        for forbidden in ("token", "pid", "thread_id", "tenant_id", "actor"):
            assert forbidden not in running["active_scan"]

    finished = _get_continuous_state(tmp_path, project)
    assert finished["active_scan_live"] is False
    assert finished["active_scan"] == {}
