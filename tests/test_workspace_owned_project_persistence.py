"""Workspace-provisioned projects persist scan envelopes under local dev.

Workspace-provisioned projects (benchmark/local targets) are created by
directory provisioning and never touch the account registry, so the
tenant-scoped persistence layer used to reject their scan envelopes at the
end of a full scan run (``SCAN_PERSISTENCE_FAILED``): first on the
duplicate-username integrity guard, then on ``tenant does not own project``.
``ensure_workspace_owned_project`` idempotently registers the tenant and
project rows so ``save_scan`` / ``merge_findings_cumulative`` can own the
envelope; it is invoked by the governed scan path only for
loopback-local-development principals.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ai_test_asset_center import db_persistence as dbp


def _db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(root / "qualibug.db"))
    conn.row_factory = sqlite3.Row
    return conn


def test_ensure_creates_tenant_and_project_rows(tmp_path: Path) -> None:
    created = dbp.ensure_workspace_owned_project(
        tmp_path, "default", "benchmark_mall_131"
    )
    assert created == {"tenant_created": True, "project_created": True}
    with _db(tmp_path) as conn:
        tenant = conn.execute(
            "SELECT id, username, role FROM tenants WHERE id = ?", ("default",)
        ).fetchone()
        assert tenant is not None
        assert tenant["username"] == "default_dev"
        assert tenant["role"] == "admin"
        project = conn.execute(
            "SELECT id, tenant_id FROM projects WHERE tenant_id = ? AND id = ?",
            ("default", "benchmark_mall_131"),
        ).fetchone()
        assert project is not None
        assert project["tenant_id"] == "default"


def test_ensure_is_idempotent(tmp_path: Path) -> None:
    first = dbp.ensure_workspace_owned_project(tmp_path, "default", "benchmark_mall_131")
    second = dbp.ensure_workspace_owned_project(
        tmp_path, "default", "benchmark_mall_131"
    )
    assert first == {"tenant_created": True, "project_created": True}
    assert second == {"tenant_created": False, "project_created": False}
    with _db(tmp_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tenants").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1


def test_ensure_does_not_expose_created_credentials(tmp_path: Path) -> None:
    result = dbp.ensure_workspace_owned_project(tmp_path, "default", "benchmark_mall_131")
    flattened = " ".join(str(v) for v in result.values())
    assert "token_hex" not in flattened
    with _db(tmp_path) as conn:
        tenant = conn.execute(
            "SELECT api_key_hash, password_hash FROM tenants WHERE id = ?", ("default",)
        ).fetchone()
        # hashes are hex digests, never the raw secrets
        assert len(tenant["api_key_hash"]) == 64
        assert len(tenant["password_hash"]) == 64


def test_scan_envelope_persists_after_ensure(tmp_path: Path) -> None:
    """The exact failure mode: save_scan used to raise PermissionError."""
    dbp.ensure_workspace_owned_project(tmp_path, "default", "benchmark_mall_131")
    fake = {
        "grade": "B",
        "score": 0.5,
        "coverage": 0.3,
        "total_findings": 1,
        "total_ms": 1000,
        "layers": {"discovery": {"x": 1}},
        "findings": [
            {
                "title": "persisted finding",
                "severity": "P2",
                "category": "api",
                "description": "persisted",
                "confidence": 0.6,
                "status": "open",
                "evidence": {"method": "GET", "path": "/persisted"},
            }
        ],
    }
    scan_id = dbp.save_scan(tmp_path, "default", "benchmark_mall_131", fake)
    merged = dbp.merge_findings_cumulative(
        tmp_path, "default", "benchmark_mall_131", scan_id, fake["findings"]
    )
    assert merged["new"] == 1
    with _db(tmp_path) as conn:
        scan = conn.execute(
            "SELECT id, tenant_id, project_id FROM scans WHERE id = ?", (scan_id,)
        ).fetchone()
        assert scan is not None
        assert scan["tenant_id"] == "default"
        assert scan["project_id"] == "benchmark_mall_131"


def test_username_conflict_fails_closed(tmp_path: Path) -> None:
    """A foreign tenant squatting on the dev username must fail, not silently
    bind the workspace to the wrong account."""
    dbp.create_tenant(
        tmp_path,
        "other_tenant",
        "Other Tenant",
        username="default_dev",
        password="somepass1234",
    )
    with pytest.raises(sqlite3.IntegrityError):
        dbp.ensure_workspace_owned_project(tmp_path, "default", "benchmark_mall_131")
