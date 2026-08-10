from __future__ import annotations

import hashlib
import time

import pytest

from ai_test_asset_center import db_persistence as db
from ai_test_asset_center.finding_evidence_shares import (
    build_external_finding_snapshot,
    create_finding_evidence_share,
    list_finding_evidence_shares,
    redact_external_text,
    resolve_finding_evidence_share,
    revoke_finding_evidence_share,
)


def _seed_finding(tmp_path) -> tuple[str, str, str]:
    tenant = "tenant_share"
    project = "project_share"
    created = db.create_tenant(
        tmp_path,
        tenant,
        "Share Tenant",
        username="share_admin",
        password="StrongPassword-123",
    )
    assert created["ok"] is True
    assert db.create_project(tmp_path, tenant, project, "Share Project")["ok"] is True
    scan_id = db.save_scan(
        tmp_path,
        tenant,
        project,
        {"grade": "B", "coverage": 0.75, "total_findings": 1},
    )
    merged = db.merge_findings_cumulative(
        tmp_path,
        tenant,
        project,
        scan_id,
        [
            {
                "title": "越权读取订单",
                "severity": "P0",
                "confidence_score": 0.99,
                "_api_method": "GET",
                "_api_path": "/api/orders/1",
                "evidence": {"method": "GET", "path": "/api/orders/1"},
            }
        ],
    )
    assert merged["new"] == 1
    rows = db.get_cumulative_findings(
        tmp_path,
        tenant,
        project,
        include_resolved=True,
    )
    return tenant, project, rows[0]["risk_id"]


def _snapshot() -> dict:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.abcdefghi0123456789"
    finding = {
        "severity": "P0",
        "title": "越权读取订单",
        "business_impact": {
            "module": "订单",
            "summary": "Authorization: Bearer super-secret-token 不应出现在外发证据",
        },
        "expected": "返回 403",
        "actual": '{"password":"secret-pass","token":"secret-token"}',
        "evidence_quality": {"label": "已验证", "score": 96.4},
        "proof": {"repro_rate": 100},
        "reproduction": {
            "steps": [
                "GET /api/orders/1?access_token=query-secret",
                f"响应日志包含 {jwt}",
            ],
            "curl_command": "curl -H 'Authorization: Bearer raw-curl-secret' ...",
        },
        "evidence_chain": [
            {
                "label": "真实响应",
                "content": "api_key=secret-api-key",
                "detail": 'Cookie: sid=secret-cookie',
            }
        ],
        "investigation_guidance": {
            "relevant_apis": ["GET /api/orders/1"],
            "relevant_tables": ["orders"],
            "trace_id": "trace-001",
        },
        "regression_verification_obligations": ["修复后再次验证跨账号读取被拒绝"],
        "verification_status": "open",
        "collaboration": {"handling_status": "in_progress", "fix_version": "v1.2.3"},
    }
    return build_external_finding_snapshot(finding, project_name="Acme")


def test_external_snapshot_redacts_secrets_and_omits_raw_curl() -> None:
    snapshot = _snapshot()
    encoded = str(snapshot)
    for secret in (
        "super-secret-token",
        "secret-pass",
        "secret-token",
        "query-secret",
        "secret-api-key",
        "secret-cookie",
        "raw-curl-secret",
        "eyJhbGciOiJIUzI1NiJ9",
    ):
        assert secret not in encoded
    assert "[REDACTED]" in encoded or "[REDACTED_JWT]" in encoded
    assert "curl_command" not in snapshot
    assert snapshot["evidence_quality"]["score"] == 96
    assert snapshot["repro_rate"] == 100


def test_plaintext_share_token_is_never_persisted_and_revoke_is_immediate(tmp_path) -> None:
    tenant, project, finding_id = _seed_finding(tmp_path)
    created = create_finding_evidence_share(
        tmp_path,
        tenant,
        project,
        finding_id,
        _snapshot(),
        ttl_seconds=3600,
        actor_name="qa_lead",
    )
    token = created["token"]
    assert len(token) >= 32

    with db._conn(tmp_path) as conn:
        row = conn.execute(
            "SELECT token_hash, snapshot_json FROM finding_evidence_shares WHERE share_id = ?",
            (created["share_id"],),
        ).fetchone()
        assert row is not None
        assert row["token_hash"] == hashlib.sha256(token.encode("utf-8")).hexdigest()
        assert token not in row["token_hash"]
        assert token not in row["snapshot_json"]

    listed = list_finding_evidence_shares(tmp_path, tenant, project, finding_id)
    assert listed[0]["share_id"] == created["share_id"]
    assert listed[0]["active"] is True
    assert "token" not in listed[0]

    resolved = resolve_finding_evidence_share(tmp_path, token)
    assert resolved is not None
    assert resolved["snapshot"]["title"] == "越权读取订单"

    assert revoke_finding_evidence_share(
        tmp_path,
        tenant,
        project,
        created["share_id"],
    ) is True
    assert resolve_finding_evidence_share(tmp_path, token) is None


def test_expired_share_and_invalid_ttl_fail_closed(tmp_path) -> None:
    tenant, project, finding_id = _seed_finding(tmp_path)
    with pytest.raises(ValueError, match="between 300 and 604800"):
        create_finding_evidence_share(
            tmp_path,
            tenant,
            project,
            finding_id,
            _snapshot(),
            ttl_seconds=60,
        )

    created = create_finding_evidence_share(
        tmp_path,
        tenant,
        project,
        finding_id,
        _snapshot(),
        ttl_seconds=300,
    )
    with db._conn(tmp_path) as conn:
        conn.execute(
            "UPDATE finding_evidence_shares SET expires_unix = ? WHERE share_id = ?",
            (int(time.time()) - 1, created["share_id"]),
        )
    assert resolve_finding_evidence_share(tmp_path, created["token"]) is None


def test_public_resolution_does_not_create_share_table(tmp_path) -> None:
    db.init_db(tmp_path)
    assert resolve_finding_evidence_share(tmp_path, "x" * 48) is None
    with db._conn(tmp_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='finding_evidence_shares'"
        ).fetchone()
    assert row is None


def test_json_and_bearer_redaction_variants() -> None:
    text = 'Bearer abcdefghijklmnop {"password":"secret value","api_key":"abc123456"}'
    redacted = redact_external_text(text)
    assert "abcdefghijklmnop" not in redacted
    assert "secret value" not in redacted
    assert "abc123456" not in redacted
