"""Revocable, expiring, read-only evidence shares.

Share tokens are opaque 256-bit random capabilities. Plaintext tokens are
returned once to the authenticated creator and never persisted; SQLite stores
only SHA-256 token hashes. Public resolution returns a frozen, deliberately
curated and redacted snapshot rather than granting access to project data.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any

from . import db_persistence as db_persist
from .project_runtime_primitives import safe_project_id

_MIN_TTL_SECONDS = 5 * 60
_MAX_TTL_SECONDS = 7 * 24 * 60 * 60
_DEFAULT_TTL_SECONDS = 24 * 60 * 60

_SENSITIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+", re.I), r"\1[REDACTED]"),
    (re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", re.I), "Bearer [REDACTED]"),
    (re.compile(r"\b(cookie\s*[:=]\s*)[^\n]+", re.I), r"\1[REDACTED]"),
    (re.compile(r"\b(set-cookie\s*[:=]\s*)[^\n]+", re.I), r"\1[REDACTED]"),
    (
        re.compile(
            r"([\"']?(?:access_token|refresh_token|id_token|token|api[_-]?key|apikey|password)[\"']?\s*[:=]\s*)"
            r"([\"'])(.*?)\2",
            re.I,
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"([\"']?(?:access_token|refresh_token|id_token|token|api[_-]?key|apikey|password)[\"']?\s*[:=]\s*)"
            r"[^\s,;}&]+",
            re.I,
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"([?&](?:token|access_token|refresh_token|api_key|apikey|password)=)[^&#\s]+", re.I), r"\1[REDACTED]"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        "[REDACTED_JWT]",
    ),
)


def _text(value: Any, limit: int = 0) -> str:
    text = str(value or "").strip()
    return text[:limit] if limit > 0 else text


def _number(value: Any) -> int:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0
    if not (number == number and abs(number) != float("inf")):
        return 0
    return max(0, min(100, int(round(number))))


def redact_external_text(value: Any, *, limit: int = 4000) -> str:
    text = _text(value, limit)
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _ensure_table(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS finding_evidence_shares (
            share_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            finding_id TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            snapshot_json TEXT NOT NULL,
            created_by TEXT DEFAULT '',
            created_unix INTEGER NOT NULL,
            expires_unix INTEGER NOT NULL,
            revoked_unix INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (finding_id) REFERENCES findings(id)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_finding_evidence_shares_scope "
        "ON finding_evidence_shares(tenant_id, project_id, finding_id, expires_unix)"
    )


def _share_table_exists(db: Any) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'finding_evidence_shares'"
    ).fetchone()
    return row is not None


def _module_name(finding: dict[str, Any]) -> str:
    impact = finding.get("business_impact") if isinstance(finding.get("business_impact"), dict) else {}
    return redact_external_text(
        impact.get("module")
        or finding.get("source_entity")
        or finding.get("defect_family_label")
        or "未归类",
        limit=300,
    )


def build_external_finding_snapshot(
    finding: dict[str, Any],
    *,
    project_name: str = "",
) -> dict[str, Any]:
    """Build a conservative external snapshot; raw credentials/bodies/curl are omitted."""

    impact = finding.get("business_impact") if isinstance(finding.get("business_impact"), dict) else {}
    comparison = finding.get("expected_actual_comparison") if isinstance(finding.get("expected_actual_comparison"), dict) else {}
    quality = finding.get("evidence_quality") if isinstance(finding.get("evidence_quality"), dict) else {}
    proof = finding.get("proof") if isinstance(finding.get("proof"), dict) else {}
    reproduction = finding.get("reproduction") if isinstance(finding.get("reproduction"), dict) else {}
    guidance = finding.get("investigation_guidance") if isinstance(finding.get("investigation_guidance"), dict) else {}
    collaboration = finding.get("collaboration") if isinstance(finding.get("collaboration"), dict) else {}

    steps = reproduction.get("steps") if isinstance(reproduction.get("steps"), list) else []
    evidence_chain = finding.get("evidence_chain") if isinstance(finding.get("evidence_chain"), list) else []
    safe_chain: list[dict[str, str]] = []
    for raw in evidence_chain[:30]:
        if not isinstance(raw, dict):
            continue
        safe_chain.append(
            {
                "label": redact_external_text(raw.get("label") or raw.get("tag"), limit=200),
                "content": redact_external_text(raw.get("content"), limit=2000),
                "detail": redact_external_text(raw.get("detail"), limit=2000),
            }
        )

    obligations = finding.get("regression_verification_obligations")
    if not isinstance(obligations, list):
        obligations = []
    relevant_apis = guidance.get("relevant_apis") if isinstance(guidance.get("relevant_apis"), list) else []
    relevant_tables = guidance.get("relevant_tables") if isinstance(guidance.get("relevant_tables"), list) else []

    return {
        "schema": "qualibug.external-finding-evidence.v1",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project_name": redact_external_text(project_name, limit=300),
        "severity": redact_external_text(finding.get("severity"), limit=20),
        "title": redact_external_text(finding.get("title"), limit=500),
        "module": _module_name(finding),
        "business_impact": redact_external_text(
            impact.get("summary") or finding.get("business_summary"),
            limit=3000,
        ),
        "expected": redact_external_text(
            finding.get("expected") or comparison.get("expected") or "未指定",
            limit=3000,
        ),
        "actual": redact_external_text(
            finding.get("actual") or comparison.get("actual") or "未捕获",
            limit=3000,
        ),
        "evidence_quality": {
            "label": redact_external_text(quality.get("label"), limit=200),
            "score": _number(quality.get("score")),
        },
        "repro_rate": _number(proof.get("repro_rate")),
        "reproduction_steps": [redact_external_text(item, limit=1200) for item in steps[:20]],
        "evidence_chain": safe_chain,
        "relevant_apis": [redact_external_text(item, limit=600) for item in relevant_apis[:50]],
        "relevant_tables": [redact_external_text(item, limit=300) for item in relevant_tables[:50]],
        "trace_id": redact_external_text(guidance.get("trace_id"), limit=500),
        "regression_obligations": [redact_external_text(item, limit=1200) for item in obligations[:30]],
        "verification_status": redact_external_text(finding.get("verification_status"), limit=80),
        "handling_status": redact_external_text(collaboration.get("handling_status"), limit=80),
        "fix_version": redact_external_text(collaboration.get("fix_version"), limit=300),
        "notice": "只读脱敏快照；原始证据、认证材料、Cookie、Token、请求体和原始 curl 不包含在此分享中。",
    }


def _token_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _iso(unix_seconds: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(unix_seconds))


def create_finding_evidence_share(
    root: Path,
    tenant_id: str,
    project_id: str,
    finding_id: str,
    snapshot: dict[str, Any],
    *,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    actor_name: str = "",
) -> dict[str, Any]:
    tenant = safe_project_id(tenant_id)
    project = safe_project_id(project_id)
    persisted_id = _text(finding_id, 160)
    if not persisted_id:
        raise ValueError("finding_persistence_id is required")
    if not isinstance(snapshot, dict) or snapshot.get("schema") != "qualibug.external-finding-evidence.v1":
        raise ValueError("external evidence snapshot is invalid")
    ttl = int(ttl_seconds or _DEFAULT_TTL_SECONDS)
    if ttl < _MIN_TTL_SECONDS or ttl > _MAX_TTL_SECONDS:
        raise ValueError("share ttl must be between 300 and 604800 seconds")

    now = int(time.time())
    expires = now + ttl
    token = secrets.token_urlsafe(32)
    token_hash = _token_hash(token)
    share_id = f"qbes_{secrets.token_hex(12)}"

    db_persist.init_db(root)
    with db_persist._conn(root) as db:  # package-internal persistence composition
        db_persist._require_owned_project(db, tenant, project)
        _ensure_table(db)
        owned = db.execute(
            "SELECT 1 FROM findings WHERE id = ? AND tenant_id = ? AND project_id = ?",
            (persisted_id, tenant, project),
        ).fetchone()
        if owned is None:
            raise KeyError("finding_persistence_id is outside the current tenant/project")
        db.execute(
            """
            INSERT INTO finding_evidence_shares (
                share_id, tenant_id, project_id, finding_id, token_hash,
                snapshot_json, created_by, created_unix, expires_unix, revoked_unix
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                share_id,
                tenant,
                project,
                persisted_id,
                token_hash,
                json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                _text(actor_name, 200),
                now,
                expires,
            ),
        )
    return {
        "share_id": share_id,
        "token": token,
        "created_at": _iso(now),
        "expires_at": _iso(expires),
        "expires_unix": expires,
    }


def list_finding_evidence_shares(
    root: Path,
    tenant_id: str,
    project_id: str,
    finding_id: str,
) -> list[dict[str, Any]]:
    tenant = safe_project_id(tenant_id)
    project = safe_project_id(project_id)
    persisted_id = _text(finding_id, 160)
    db_persist.init_db(root)
    now = int(time.time())
    with db_persist._conn(root) as db:
        db_persist._require_owned_project(db, tenant, project)
        _ensure_table(db)
        rows = db.execute(
            """
            SELECT share_id, created_by, created_unix, expires_unix, revoked_unix
            FROM finding_evidence_shares
            WHERE tenant_id = ? AND project_id = ? AND finding_id = ?
            ORDER BY created_unix DESC
            """,
            (tenant, project, persisted_id),
        ).fetchall()
    return [
        {
            "share_id": row["share_id"],
            "created_by": row["created_by"] or "",
            "created_at": _iso(int(row["created_unix"])),
            "expires_at": _iso(int(row["expires_unix"])),
            "revoked": int(row["revoked_unix"] or 0) > 0,
            "active": int(row["revoked_unix"] or 0) == 0 and int(row["expires_unix"]) > now,
        }
        for row in rows
    ]


def revoke_finding_evidence_share(
    root: Path,
    tenant_id: str,
    project_id: str,
    share_id: str,
) -> bool:
    tenant = safe_project_id(tenant_id)
    project = safe_project_id(project_id)
    share = _text(share_id, 160)
    if not share:
        return False
    db_persist.init_db(root)
    with db_persist._conn(root) as db:
        db_persist._require_owned_project(db, tenant, project)
        _ensure_table(db)
        cursor = db.execute(
            """
            UPDATE finding_evidence_shares
            SET revoked_unix = ?
            WHERE share_id = ? AND tenant_id = ? AND project_id = ? AND revoked_unix = 0
            """,
            (int(time.time()), share, tenant, project),
        )
        return cursor.rowcount == 1


def resolve_finding_evidence_share(root: Path, token: str) -> dict[str, Any] | None:
    supplied = _text(token, 300)
    if len(supplied) < 32:
        return None
    digest = _token_hash(supplied)
    db_persist.init_db(root)
    now = int(time.time())
    with db_persist._conn(root) as db:
        # Public resolution is strictly read-only. Table creation/migration is
        # performed only by authenticated create/list/revoke paths.
        if not _share_table_exists(db):
            return None
        row = db.execute(
            """
            SELECT share_id, snapshot_json, expires_unix
            FROM finding_evidence_shares
            WHERE token_hash = ? AND revoked_unix = 0 AND expires_unix > ?
            """,
            (digest, now),
        ).fetchone()
    if row is None:
        return None
    try:
        snapshot = json.loads(row["snapshot_json"] or "{}")
    except Exception:
        return None
    if not isinstance(snapshot, dict) or snapshot.get("schema") != "qualibug.external-finding-evidence.v1":
        return None
    return {
        "share_id": row["share_id"],
        "expires_at": _iso(int(row["expires_unix"])),
        "snapshot": snapshot,
    }


__all__ = [
    "build_external_finding_snapshot",
    "create_finding_evidence_share",
    "list_finding_evidence_shares",
    "redact_external_text",
    "resolve_finding_evidence_share",
    "revoke_finding_evidence_share",
]
