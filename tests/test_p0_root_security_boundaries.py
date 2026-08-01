from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center import db_persistence
from ai_test_asset_center import jwt_auth
from ai_test_asset_center.private_pilot_tenant_auth import (
    TenantAuthenticationError,
    _actor,
    _tenant_from_headers,
)
from ai_test_asset_center.project_runtime_primitives import safe_project_id
from ai_test_asset_center.replay_engine import ReplayEngine


def _tenant(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tenant_id: str,
    username: str,
) -> dict:
    monkeypatch.setenv("QUALIBUG_ALLOW_TENANT_PROVISIONING", "1")
    result = db_persistence.create_tenant(
        root,
        tenant_id,
        tenant_id,
        username=username,
        password="strong-password",
        role="platform_admin",
    )
    assert result["ok"] is True
    assert result["role"] == "admin"
    return result


def test_project_identity_rejects_path_segments() -> None:
    for value in (".", "..", "../other", "a/b", "a\\b", "/absolute"):
        with pytest.raises(ValueError):
            safe_project_id(value)
    assert safe_project_id("tenant.project-1") == "tenant.project-1"


def test_authenticated_role_comes_from_signed_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = _tenant(tmp_path, monkeypatch, "tenant-a", "tenant-a-user")
    monkeypatch.setenv("QUALIBUG_JWT_SECRET", "test-secret-with-enough-entropy")
    jwt_auth._cached_secret = None
    token = jwt_auth.create_token(
        account["tenant_id"],
        role=account["role"],
        username=account["username"],
        session_version=account["session_version"],
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-QualiBug-Actor": "attacker",
        "X-QualiBug-Role": "viewer",
        "X-QualiBug-Project-Scopes": "*",
    }
    assert _tenant_from_headers(headers, root=tmp_path) == "tenant-a"
    assert _actor(headers, root=tmp_path) == {
        "name": "tenant-a-user",
        "role": "admin",
    }


def test_missing_credentials_do_not_fall_back_to_default_tenant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("QUALIBUG_LOCAL_DEV_ACTOR", raising=False)
    monkeypatch.delenv("QUALIBUG_AUTH_BYPASS", raising=False)
    with pytest.raises(TenantAuthenticationError):
        _tenant_from_headers({}, root=tmp_path)


def test_finding_queries_and_status_are_tenant_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _tenant(tmp_path, monkeypatch, "tenant-a", "user-a")
    _tenant(tmp_path, monkeypatch, "tenant-b", "user-b")
    assert db_persistence.create_project(tmp_path, "tenant-a", "shared-project")["ok"]
    assert db_persistence.create_project(tmp_path, "tenant-b", "shared-project")["ok"]

    scan_a = db_persistence.save_scan(
        tmp_path,
        "tenant-a",
        "shared-project",
        {"total_findings": 1},
    )
    scan_b = db_persistence.save_scan(
        tmp_path,
        "tenant-b",
        "shared-project",
        {"total_findings": 1},
    )
    db_persistence.merge_findings_cumulative(
        tmp_path,
        "tenant-a",
        "shared-project",
        scan_a,
        [{"title": "tenant-a-only", "evidence": {"path": "/a"}}],
    )
    db_persistence.merge_findings_cumulative(
        tmp_path,
        "tenant-b",
        "shared-project",
        scan_b,
        [{"title": "tenant-b-only", "evidence": {"path": "/b"}}],
    )

    findings_a = db_persistence.get_cumulative_findings(
        tmp_path,
        "tenant-a",
        "shared-project",
    )
    findings_b = db_persistence.get_cumulative_findings(
        tmp_path,
        "tenant-b",
        "shared-project",
    )
    assert [row["title"] for row in findings_a] == ["tenant-a-only"]
    assert [row["title"] for row in findings_b] == ["tenant-b-only"]

    finding_a = findings_a[0]["risk_id"]
    assert db_persistence.update_finding_status(
        tmp_path,
        finding_a,
        "resolved",
        tenant_id="tenant-b",
        project_id="shared-project",
    ) is False
    assert db_persistence.update_finding_status(
        tmp_path,
        finding_a,
        "resolved",
        tenant_id="tenant-a",
        project_id="shared-project",
    ) is True


def test_scan_persistence_does_not_double_insert_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _tenant(tmp_path, monkeypatch, "tenant-a", "user-a")
    assert db_persistence.create_project(tmp_path, "tenant-a", "project-a")["ok"]
    scan_id = db_persistence.save_scan(
        tmp_path,
        "tenant-a",
        "project-a",
        {"total_findings": 1, "findings": [{"title": "one"}]},
    )
    merge = db_persistence.merge_findings_cumulative(
        tmp_path,
        "tenant-a",
        "project-a",
        scan_id,
        [{"title": "one"}],
    )
    assert merge["new"] == 1
    assert len(
        db_persistence.get_cumulative_findings(
            tmp_path,
            "tenant-a",
            "project-a",
        )
    ) == 1


def test_replay_requires_explicit_oracle_to_close() -> None:
    engine = ReplayEngine(Path("."), "project-a")
    finding = {"evidence": {"status_code": 500}}
    original = {"status_code": 500, "response_body_excerpt": ""}
    response = {"status_code": 200, "body": "ok"}
    verdict, detail = engine._evaluate_replay(finding, original, response)
    assert verdict == "inconclusive"
    assert detail["basis"] == "insufficient_replay_oracle"

    explicit = {
        "replay_oracle": {
            "expected_status": 500,
            "expected_body_contains": "known failure",
        }
    }
    verdict, _ = engine._evaluate_replay(
        explicit,
        original,
        {"status_code": 200, "body": "ok"},
    )
    assert verdict == "not_reproduced"


def test_replay_rejects_absolute_url_outside_approved_target(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "platform_inputs" / "project-a"
    config_dir.mkdir(parents=True)
    (config_dir / "real_project_config.json").write_text(
        json.dumps(
            {
                "base_url": "http://127.0.0.1:9000",
                "approved_base_url": "http://127.0.0.1:9000",
                "environment_type": "test",
                "environment_ref": "test-a",
                "execution_mode": "safe_read_only",
                "runtime_status": "approved",
            }
        ),
        encoding="utf-8",
    )
    engine = ReplayEngine(tmp_path, "project-a")
    with pytest.raises(ValueError, match="REPLAY_URL_OUTSIDE_APPROVED_TARGET"):
        engine._resolve_replay_url("http://169.254.169.254/latest/meta-data")
