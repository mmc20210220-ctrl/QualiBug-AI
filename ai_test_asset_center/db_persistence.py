"""SQLite persistence for the private-pilot multi-tenant runtime.

Tenant and project identity are mandatory dimensions of every customer-data
query. Findings are stored once through the cumulative merge authority; scan
persistence stores the scan envelope only. Account usernames are globally
unique and every JWT/cookie session is bound to a server-side session version.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .project_runtime_primitives import safe_project_id

SCHEMA_VERSION = 3
DB_FILENAME = "qualibug.db"
_PBKDF2_ITERATIONS = 200_000
_ALLOWED_FINDING_STATUSES = frozenset({"open", "resolved", "falsified"})


def _db_path(root: Path) -> Path:
    return Path(root).resolve() / DB_FILENAME


@contextmanager
def _conn(root: Path):
    db_path = _db_path(root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path), timeout=30)
    if db_path.exists():
        try:
            os.chmod(str(db_path), 0o600)
        except OSError:
            pass
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=30000")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _ensure_unique_usernames(db: sqlite3.Connection) -> None:
    duplicates = db.execute(
        "SELECT username, COUNT(*) AS count FROM tenants "
        "WHERE username <> '' GROUP BY username HAVING COUNT(*) > 1"
    ).fetchall()
    if duplicates:
        names = ", ".join(str(row["username"]) for row in duplicates[:10])
        raise RuntimeError(
            "duplicate tenant usernames must be resolved before startup: " + names
        )
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tenants_username "
        "ON tenants(username) WHERE username <> ''"
    )


def init_db(root: Path) -> None:
    with _conn(root) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                api_key_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                quota_scans INTEGER DEFAULT 100
            )
            """
        )
        tenant_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(tenants)").fetchall()
        }
        if "username" not in tenant_columns:
            db.execute("ALTER TABLE tenants ADD COLUMN username TEXT DEFAULT ''")
        if "password_hash" not in tenant_columns:
            db.execute("ALTER TABLE tenants ADD COLUMN password_hash TEXT DEFAULT ''")
        if "role" not in tenant_columns:
            db.execute("ALTER TABLE tenants ADD COLUMN role TEXT DEFAULT 'admin'")
        if "session_version" not in tenant_columns:
            db.execute(
                "ALTER TABLE tenants ADD COLUMN session_version INTEGER NOT NULL DEFAULT 1"
            )
        _ensure_unique_usernames(db)
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                name TEXT NOT NULL,
                base_url TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (id, tenant_id),
                FOREIGN KEY (tenant_id) REFERENCES tenants(id)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS scans (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT (datetime('now')),
                finished_at TEXT,
                grade TEXT DEFAULT '',
                score REAL DEFAULT 0,
                coverage REAL DEFAULT 0,
                total_findings INTEGER DEFAULT 0,
                total_ms INTEGER DEFAULT 0,
                layers_json TEXT DEFAULT '{}',
                FOREIGN KEY (tenant_id) REFERENCES tenants(id)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS findings (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                scan_id TEXT NOT NULL,
                title TEXT NOT NULL,
                severity TEXT DEFAULT 'P1',
                category TEXT DEFAULT '',
                description TEXT DEFAULT '',
                confidence REAL DEFAULT 0,
                status TEXT DEFAULT 'open',
                evidence_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (tenant_id) REFERENCES tenants(id),
                FOREIGN KEY (scan_id) REFERENCES scans(id)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_docs (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                content TEXT DEFAULT '',
                type TEXT DEFAULT 'prd',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (tenant_id) REFERENCES tenants(id)
            )
            """
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_projects_tenant ON projects(tenant_id, id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_scans_tenant_project "
            "ON scans(tenant_id, project_id, finished_at)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_findings_tenant_project "
            "ON findings(tenant_id, project_id, status, created_at)"
        )
        db.execute("CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)")
        db.execute(
            "INSERT OR REPLACE INTO _meta VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )


def _password_hash(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
        dklen=32,
    )
    return (
        f"pbkdf2_sha256${_PBKDF2_ITERATIONS}$"
        f"{base64.b64encode(salt).decode()}$"
        f"{base64.b64encode(derived).decode()}"
    )


def _verify_password(password: str, stored: str) -> bool:
    if not stored or not password:
        return False
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iteration_text, salt_text, hash_text = stored.split("$", 3)
            iterations = int(iteration_text)
            salt = base64.b64decode(salt_text, validate=True)
            expected = base64.b64decode(hash_text, validate=True)
            actual = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt,
                iterations,
                dklen=len(expected),
            )
            return hmac.compare_digest(actual, expected)
        except Exception:
            return False
    return hmac.compare_digest(
        hashlib.sha256(password.encode("utf-8")).hexdigest(),
        stored,
    )


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _tenant_provisioning_allowed(
    db: sqlite3.Connection,
    provisioning_token: str,
) -> tuple[bool, str]:
    count = int(db.execute("SELECT COUNT(*) FROM tenants").fetchone()[0])
    if count > 0 and not _truthy_env("QUALIBUG_ALLOW_TENANT_PROVISIONING"):
        return False, "TENANT_PROVISIONING_DISABLED"
    public_bind = os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") == "1"
    self_registration = _truthy_env("QUALIBUG_ALLOW_PUBLIC_SELF_REGISTRATION")
    if public_bind and not self_registration:
        expected = os.environ.get("QUALIBUG_TENANT_BOOTSTRAP_TOKEN", "").strip()
        supplied = str(provisioning_token or "").strip()
        if not expected or not supplied or not hmac.compare_digest(supplied, expected):
            return False, "TENANT_BOOTSTRAP_TOKEN_REQUIRED"
    return True, "APPROVED"


def create_tenant(
    root: Path,
    tenant_id: str,
    name: str,
    api_key: str | None = None,
    *,
    username: str = "",
    password: str = "",
    role: str = "admin",
    provisioning_token: str = "",
) -> dict[str, Any]:
    """Create one tenant under the deployment provisioning policy."""

    del role
    try:
        tenant = safe_project_id(tenant_id)
    except ValueError:
        return {"ok": False, "error": "TENANT_ID_INVALID"}
    display_name = str(name or "").strip()
    account_name = str(username or "").strip()
    secret = str(password or "")
    if not display_name or not account_name or len(secret) < 8:
        return {"ok": False, "error": "TENANT_FIELDS_INVALID"}
    key = api_key or f"qb_{secrets.token_hex(24)}"
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    init_db(root)
    with _conn(root) as db:
        allowed, reason = _tenant_provisioning_allowed(db, provisioning_token)
        if not allowed:
            return {"ok": False, "error": reason}
        try:
            db.execute(
                "INSERT INTO tenants "
                "(id, name, api_key_hash, username, password_hash, role, session_version) "
                "VALUES (?, ?, ?, ?, ?, 'admin', 1)",
                (
                    tenant,
                    display_name,
                    key_hash,
                    account_name,
                    _password_hash(secret),
                ),
            )
        except sqlite3.IntegrityError as exc:
            message = str(exc).lower()
            return {
                "ok": False,
                "error": "USERNAME_EXISTS" if "username" in message else "TENANT_EXISTS",
            }
    return {
        "ok": True,
        "tenant_id": tenant,
        "api_key": key,
        "name": display_name,
        "username": account_name,
        "role": "admin",
        "session_version": 1,
    }


def authenticate_tenant(
    root: Path,
    username_or_api_key: str,
    password: str = "",
) -> dict[str, Any] | None:
    init_db(root)
    identity = str(username_or_api_key or "")
    with _conn(root) as db:
        row = None
        if password:
            candidate = db.execute(
                "SELECT id, username, role, password_hash, session_version "
                "FROM tenants WHERE username = ?",
                (identity,),
            ).fetchone()
            if candidate and _verify_password(password, candidate["password_hash"] or ""):
                row = candidate
        else:
            row = db.execute(
                "SELECT id, username, role, session_version FROM tenants "
                "WHERE api_key_hash = ?",
                (hashlib.sha256(identity.encode()).hexdigest(),),
            ).fetchone()
        if not row:
            return None
        return {
            "tenant_id": row["id"],
            "username": row["username"] or row["id"],
            "role": row["role"] or "viewer",
            "session_version": int(row["session_version"] or 1),
        }


def get_tenant_auth_state(root: Path, tenant_id: str) -> dict[str, Any] | None:
    init_db(root)
    with _conn(root) as db:
        row = db.execute(
            "SELECT id, username, role, session_version FROM tenants WHERE id = ?",
            (tenant_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "tenant_id": row["id"],
            "username": row["username"] or row["id"],
            "role": row["role"] or "viewer",
            "session_version": int(row["session_version"] or 1),
        }


def revoke_tenant_sessions(root: Path, tenant_id: str) -> int:
    """Invalidate every outstanding JWT/cookie for one tenant."""

    init_db(root)
    with _conn(root) as db:
        cursor = db.execute(
            "UPDATE tenants SET session_version = session_version + 1 WHERE id = ?",
            (tenant_id,),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"tenant not found: {tenant_id}")
        row = db.execute(
            "SELECT session_version FROM tenants WHERE id = ?",
            (tenant_id,),
        ).fetchone()
        return int(row["session_version"])


def reset_tenant_password(
    root: Path,
    *,
    tenant_id: str,
    username: str,
    new_password: str,
    current_password: str = "",
) -> dict[str, Any]:
    """Change a password and revoke JWT, cookie and API-key sessions."""

    tid = str(tenant_id or "").strip()
    user = str(username or "").strip()
    replacement = str(new_password or "")
    current = str(current_password or "")
    if not tid or not user or not replacement:
        return {"ok": False, "error": "MISSING_FIELDS"}
    if len(replacement) < 8:
        return {"ok": False, "error": "PASSWORD_TOO_SHORT"}
    if not current:
        return {"ok": False, "error": "RESET_AUTH_REQUIRED"}
    init_db(root)
    with _conn(root) as db:
        row = db.execute(
            "SELECT id, username, password_hash FROM tenants "
            "WHERE id = ? AND username = ?",
            (tid, user),
        ).fetchone()
        if not row or not _verify_password(current, row["password_hash"] or ""):
            return {"ok": False, "error": "RESET_DENIED"}
        revoked_api_key_hash = hashlib.sha256(
            f"revoked:{tid}:{secrets.token_hex(32)}".encode()
        ).hexdigest()
        db.execute(
            "UPDATE tenants SET password_hash = ?, api_key_hash = ?, "
            "session_version = session_version + 1 "
            "WHERE id = ? AND username = ?",
            (
                _password_hash(replacement),
                revoked_api_key_hash,
                tid,
                user,
            ),
        )
        state = db.execute(
            "SELECT session_version FROM tenants WHERE id = ?",
            (tid,),
        ).fetchone()
    return {
        "ok": True,
        "tenant_id": tid,
        "username": user,
        "session_version": int(state["session_version"]),
        "api_key_revoked": True,
    }


def verify_api_key(root: Path, api_key: str) -> str | None:
    account = authenticate_tenant(root, api_key, "")
    return str(account.get("tenant_id")) if isinstance(account, dict) else None


def list_tenants(root: Path) -> list[dict[str, Any]]:
    init_db(root)
    with _conn(root) as db:
        rows = db.execute(
            "SELECT id, name, username, role, session_version, created_at, quota_scans "
            "FROM tenants ORDER BY created_at"
        ).fetchall()
        return [
            {
                "tenant_id": row["id"],
                "name": row["name"],
                "username": row["username"],
                "role": row["role"] or "viewer",
                "session_version": int(row["session_version"] or 1),
                "created_at": row["created_at"],
                "quota_scans": row["quota_scans"],
            }
            for row in rows
        ]


def create_project(
    root: Path,
    tenant_id: str,
    project_id: str,
    name: str = "",
    base_url: str = "",
) -> dict[str, Any]:
    try:
        tenant = safe_project_id(tenant_id)
        project = safe_project_id(project_id)
    except ValueError:
        return {"ok": False, "error": "PROJECT_ID_INVALID"}
    init_db(root)
    with _conn(root) as db:
        if not db.execute("SELECT id FROM tenants WHERE id = ?", (tenant,)).fetchone():
            return {"ok": False, "error": "TENANT_NOT_FOUND"}
        try:
            db.execute(
                "INSERT INTO projects (id, tenant_id, name, base_url) VALUES (?, ?, ?, ?)",
                (project, tenant, name or project, base_url),
            )
        except sqlite3.IntegrityError:
            return {"ok": False, "error": "PROJECT_EXISTS"}
    output_root = (Path(root).resolve() / "platform_outputs").resolve()
    output_dir = (output_root / project).resolve()
    if output_root != output_dir and output_root not in output_dir.parents:
        raise ValueError("project output path escaped platform_outputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "project_id": project, "tenant_id": tenant}


def ensure_workspace_owned_project(
    root: Path,
    tenant_id: str,
    project_id: str,
) -> dict[str, bool]:
    """Idempotently register a workspace-provisioned project under its tenant.

    Workspace-provisioned projects (benchmark/local targets) are created by
    directory provisioning and never touch the account registry, so the
    tenant-scoped persistence layer would otherwise reject their scan
    envelopes. The governed scan path calls this ONLY for
    loopback-local-development principals (see
    private_pilot_scan_handlers._handle_v12_scan); credential-authenticated
    principals must register through the account API and are never
    auto-provisioned here. Credentials on a created tenant row are random and
    their plaintext is discarded immediately — the row exists only to satisfy
    identity and ownership invariants.
    """
    tenant = safe_project_id(tenant_id)
    project = safe_project_id(project_id)
    init_db(root)
    with _conn(root) as db:
        tenant_exists = (
            db.execute("SELECT 1 FROM tenants WHERE id = ?", (tenant,)).fetchone()
            is not None
        )
        if not tenant_exists:
            secret = secrets.token_hex(24)
            key = secrets.token_hex(24)
            db.execute(
                "INSERT INTO tenants "
                "(id, name, api_key_hash, username, password_hash, role, session_version) "
                "VALUES (?, ?, ?, ?, ?, 'admin', 1)",
                (
                    tenant,
                    f"{tenant} workspace (local development)",
                    hashlib.sha256(key.encode()).hexdigest(),
                    f"{tenant}_dev",
                    hashlib.sha256(secret.encode()).hexdigest(),
                ),
            )
        project_exists = (
            db.execute(
                "SELECT 1 FROM projects WHERE tenant_id = ? AND id = ?",
                (tenant, project),
            ).fetchone()
            is not None
        )
        if not project_exists:
            db.execute(
                "INSERT INTO projects (id, tenant_id, name, base_url) "
                "VALUES (?, ?, ?, ?)",
                (project, tenant, project, ""),
            )
    return {
        "tenant_created": not tenant_exists,
        "project_created": not project_exists,
    }


def list_projects(root: Path, tenant_id: str) -> list[dict[str, Any]]:
    init_db(root)
    with _conn(root) as db:
        rows = db.execute(
            "SELECT id, name, base_url, created_at FROM projects "
            "WHERE tenant_id = ? ORDER BY created_at",
            (tenant_id,),
        ).fetchall()
        return [
            {
                "project_id": row["id"],
                "customer_name": row["name"],
                "project_name": row["name"],
                "base_url": row["base_url"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]


def _require_owned_project(
    db: sqlite3.Connection,
    tenant_id: str,
    project_id: str,
) -> None:
    if not db.execute(
        "SELECT 1 FROM projects WHERE tenant_id = ? AND id = ?",
        (tenant_id, project_id),
    ).fetchone():
        raise PermissionError("tenant does not own project")


def save_scan(
    root: Path,
    tenant_id: str,
    project_id: str,
    scan_result: dict[str, Any],
) -> str:
    """Persist the scan envelope; findings are merged exactly once separately."""

    tenant = safe_project_id(tenant_id)
    project = safe_project_id(project_id)
    scan_id = f"scan_{secrets.token_hex(16)}"
    layers = scan_result.get("layers") if isinstance(scan_result.get("layers"), dict) else {}
    init_db(root)
    with _conn(root) as db:
        _require_owned_project(db, tenant, project)
        db.execute(
            """
            INSERT INTO scans (
                id, tenant_id, project_id, grade, score, coverage,
                total_findings, total_ms, layers_json, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                scan_id,
                tenant,
                project,
                scan_result.get("grade", ""),
                scan_result.get("score", 0),
                scan_result.get("coverage", 0),
                scan_result.get("total_findings", 0),
                scan_result.get("total_ms", 0),
                json.dumps(layers, ensure_ascii=False),
            ),
        )
    return scan_id


def _finding_dedupe_key(finding: dict[str, Any]) -> str:
    import re

    title = str(
        finding.get("title") or finding.get("description") or ""
    )[:200].strip().lower()
    title = re.sub(r"^(\[[^\]]*\]\s*)+", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    method = str(
        finding.get("_api_method") or finding.get("method") or evidence.get("method") or ""
    ).upper()
    path = str(
        finding.get("_api_path") or finding.get("path") or evidence.get("path") or ""
    ).strip()
    return f"{title}|{method}|{path}"


def merge_findings_cumulative(
    root: Path,
    tenant_id: str,
    project_id: str,
    scan_id: str,
    new_findings: list[dict[str, Any]],
) -> dict[str, int]:
    tenant = safe_project_id(tenant_id)
    project = safe_project_id(project_id)
    init_db(root)
    with _conn(root) as db:
        _require_owned_project(db, tenant, project)
        scan = db.execute(
            "SELECT id FROM scans WHERE id = ? AND tenant_id = ? AND project_id = ?",
            (scan_id, tenant, project),
        ).fetchone()
        if not scan:
            raise PermissionError("scan is outside tenant project scope")
        existing_rows = db.execute(
            "SELECT id, title, severity, category, description, confidence, status, "
            "evidence_json, scan_id FROM findings "
            "WHERE tenant_id = ? AND project_id = ?",
            (tenant, project),
        ).fetchall()
        existing_map: dict[str, dict[str, Any]] = {}
        for row in existing_rows:
            projected: dict[str, Any] = {
                "title": row["title"],
                "description": row["description"],
                "evidence": {},
            }
            try:
                evidence = json.loads(row["evidence_json"] or "{}")
            except Exception:
                evidence = {}
            if isinstance(evidence, dict):
                projected["evidence"] = evidence
            existing_map[_finding_dedupe_key(projected)] = dict(row)

        new_count = 0
        existing_count = 0
        incoming_seen: set[str] = set()
        for index, finding in enumerate(new_findings):
            if not isinstance(finding, dict):
                continue
            key = _finding_dedupe_key(finding)
            if not key.strip("|") or key in incoming_seen:
                continue
            incoming_seen.add(key)
            if key in existing_map:
                existing_count += 1
                continue
            evidence = (
                dict(finding.get("evidence"))
                if isinstance(finding.get("evidence"), dict)
                else {}
            )
            if finding.get("_api_path"):
                evidence.setdefault("path", finding["_api_path"])
            if finding.get("_api_method"):
                evidence.setdefault("method", finding["_api_method"])
            finding_id = f"{scan_id}_m{index}_{hashlib.sha256(key.encode()).hexdigest()[:12]}"
            db.execute(
                """
                INSERT INTO findings (
                    id, tenant_id, project_id, scan_id, title, severity,
                    category, description, confidence, status, evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
                """,
                (
                    finding_id,
                    tenant,
                    project,
                    scan_id,
                    str(finding.get("title") or finding.get("description") or "")[:500],
                    str(finding.get("severity") or "P1"),
                    str(finding.get("category") or finding.get("risk_type") or ""),
                    str(finding.get("description") or "")[:500],
                    float(finding.get("confidence_score") or finding.get("score") or 0),
                    json.dumps(evidence, ensure_ascii=False),
                ),
            )
            new_count += 1
    return {
        "new": new_count,
        "existing": existing_count,
        "total_in_scan": len(new_findings),
    }


def get_cumulative_findings(
    root: Path,
    tenant_id: str,
    project_id: str,
    *,
    include_resolved: bool = False,
) -> list[dict[str, Any]]:
    tenant = safe_project_id(tenant_id)
    project = safe_project_id(project_id)
    init_db(root)
    with _conn(root) as db:
        _require_owned_project(db, tenant, project)
        sql = (
            "SELECT id, title, severity, category, description, confidence, status, "
            "evidence_json, scan_id, created_at FROM findings "
            "WHERE tenant_id = ? AND project_id = ?"
        )
        params: list[Any] = [tenant, project]
        if not include_resolved:
            sql += " AND status = 'open'"
        sql += " ORDER BY created_at"
        rows = db.execute(sql, tuple(params)).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            finding: dict[str, Any] = {
                "risk_id": row["id"],
                "title": row["title"],
                "severity": row["severity"],
                "category": row["category"],
                "description": row["description"],
                "confidence_score": row["confidence"],
                "status": row["status"],
                "scan_id": row["scan_id"],
                "first_seen_at": row["created_at"],
            }
            try:
                evidence = json.loads(row["evidence_json"] or "{}")
            except Exception:
                evidence = {}
            if isinstance(evidence, dict):
                finding["evidence"] = evidence
                if evidence.get("path"):
                    finding["_api_path"] = evidence["path"]
                if evidence.get("method"):
                    finding["_api_method"] = evidence["method"]
            results.append(finding)
        return results


def update_finding_status(
    root: Path,
    finding_id: str,
    status: str,
    *,
    tenant_id: str = "",
    project_id: str = "",
) -> bool:
    """Update one finding only inside an explicit tenant/project scope."""

    if status not in _ALLOWED_FINDING_STATUSES or not tenant_id or not project_id:
        return False
    tenant = safe_project_id(tenant_id)
    project = safe_project_id(project_id)
    init_db(root)
    with _conn(root) as db:
        _require_owned_project(db, tenant, project)
        cursor = db.execute(
            "UPDATE findings SET status = ? "
            "WHERE id = ? AND tenant_id = ? AND project_id = ?",
            (status, finding_id, tenant, project),
        )
        return cursor.rowcount == 1


def get_finding_stats(root: Path, tenant_id: str, project_id: str) -> dict[str, int]:
    tenant = safe_project_id(tenant_id)
    project = safe_project_id(project_id)
    init_db(root)
    with _conn(root) as db:
        _require_owned_project(db, tenant, project)
        rows = db.execute(
            "SELECT status, COUNT(*) AS cnt FROM findings "
            "WHERE tenant_id = ? AND project_id = ? GROUP BY status",
            (tenant, project),
        ).fetchall()
        stats = {"open": 0, "resolved": 0, "falsified": 0, "total": 0}
        for row in rows:
            value = row["status"] or "open"
            stats[value] = stats.get(value, 0) + int(row["cnt"])
            stats["total"] += int(row["cnt"])
        return stats


def get_scan_history(
    root: Path,
    tenant_id: str,
    project_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    tenant = safe_project_id(tenant_id)
    project = safe_project_id(project_id)
    bounded_limit = max(1, min(int(limit or 20), 200))
    init_db(root)
    with _conn(root) as db:
        _require_owned_project(db, tenant, project)
        rows = db.execute(
            """
            SELECT id, grade, score, coverage, total_findings, total_ms, finished_at
            FROM scans
            WHERE tenant_id = ? AND project_id = ?
            ORDER BY finished_at DESC
            LIMIT ?
            """,
            (tenant, project, bounded_limit),
        ).fetchall()
        return [
            {
                "scan_id": row["id"],
                "grade": row["grade"],
                "score": row["score"],
                "coverage": row["coverage"],
                "total_findings": row["total_findings"],
                "total_ms": row["total_ms"],
                "finished_at": row["finished_at"],
            }
            for row in rows
        ]


def save_knowledge_doc(
    root: Path,
    tenant_id: str,
    project_id: str,
    filename: str,
    content: str,
    doc_type: str = "prd",
) -> str:
    tenant = safe_project_id(tenant_id)
    project = safe_project_id(project_id)
    init_db(root)
    document_id = hashlib.sha256(
        f"{tenant}\0{project}\0{filename}".encode()
    ).hexdigest()[:24]
    with _conn(root) as db:
        _require_owned_project(db, tenant, project)
        db.execute(
            """
            INSERT OR REPLACE INTO knowledge_docs
            (id, tenant_id, project_id, filename, content, type)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (document_id, tenant, project, filename, content, doc_type),
        )
    return document_id


def get_knowledge_docs(
    root: Path,
    tenant_id: str,
    project_id: str,
) -> list[dict[str, Any]]:
    tenant = safe_project_id(tenant_id)
    project = safe_project_id(project_id)
    init_db(root)
    with _conn(root) as db:
        _require_owned_project(db, tenant, project)
        rows = db.execute(
            "SELECT id, filename, type, created_at FROM knowledge_docs "
            "WHERE tenant_id = ? AND project_id = ?",
            (tenant, project),
        ).fetchall()
        return [
            {
                "source_id": row["id"],
                "display_name": row["filename"],
                "type": row["type"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]


def get_knowledge_doc_content(
    root: Path,
    doc_id: str,
    *,
    tenant_id: str = "",
    project_id: str = "",
) -> str:
    if not tenant_id or not project_id:
        return ""
    tenant = safe_project_id(tenant_id)
    project = safe_project_id(project_id)
    init_db(root)
    with _conn(root) as db:
        _require_owned_project(db, tenant, project)
        row = db.execute(
            "SELECT content FROM knowledge_docs "
            "WHERE id = ? AND tenant_id = ? AND project_id = ?",
            (doc_id, tenant, project),
        ).fetchone()
        return row["content"] if row else ""


__all__ = [
    "authenticate_tenant",
    "create_project",
    "create_tenant",
    "get_cumulative_findings",
    "get_finding_stats",
    "get_knowledge_doc_content",
    "get_knowledge_docs",
    "get_scan_history",
    "get_tenant_auth_state",
    "init_db",
    "list_projects",
    "list_tenants",
    "merge_findings_cumulative",
    "reset_tenant_password",
    "revoke_tenant_sessions",
    "save_knowledge_doc",
    "save_scan",
    "update_finding_status",
    "verify_api_key",
]
