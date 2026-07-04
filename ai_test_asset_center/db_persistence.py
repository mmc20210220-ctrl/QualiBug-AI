"""
QualiBug persistence layer — SQLite-based multi-tenant storage.
Replaces file-based JSON with database, while keeping backward compatibility.
"""
from __future__ import annotations
import json, os, sqlite3, time, hashlib, secrets
from pathlib import Path
from typing import Any
from contextlib import contextmanager

SCHEMA_VERSION = 1
DB_FILENAME = "qualibug.db"

def _db_path(root: Path) -> Path:
    return root / DB_FILENAME

@contextmanager
def _conn(root: Path):
    db_path = _db_path(root)
    db = sqlite3.connect(str(db_path))
    # Restrict file permissions: owner read/write only (0o600)
    if db_path.exists():
        try:
            os.chmod(str(db_path), 0o600)
        except Exception:
            pass
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init_db(root: Path) -> None:
    """Create tables if they don't exist."""
    with _conn(root) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                api_key_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                quota_scans INTEGER DEFAULT 100
            )
        """)
        tenant_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(tenants)").fetchall()
        }
        if "username" not in tenant_columns:
            db.execute("ALTER TABLE tenants ADD COLUMN username TEXT DEFAULT ''")
        if "password_hash" not in tenant_columns:
            db.execute("ALTER TABLE tenants ADD COLUMN password_hash TEXT DEFAULT ''")
        if "role" not in tenant_columns:
            db.execute("ALTER TABLE tenants ADD COLUMN role TEXT DEFAULT 'admin'")
        db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                name TEXT NOT NULL,
                base_url TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (id, tenant_id),
                FOREIGN KEY (tenant_id) REFERENCES tenants(id)
            )
        """)
        db.execute("""
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
        """)
        db.execute("""
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
        """)
        db.execute("""
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
        """)
        # Set schema version
        db.execute("CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT OR REPLACE INTO _meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))


# ── Tenant management ──

def _password_hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def create_tenant(
    root: Path,
    tenant_id: str,
    name: str,
    api_key: str | None = None,
    *,
    username: str = "",
    password: str = "",
    role: str = "admin",
) -> dict:
    """Create a new tenant with an API key."""
    key = api_key or f"qb_{secrets.token_hex(24)}"
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    password_hash = _password_hash(password) if password else ""
    init_db(root)
    with _conn(root) as db:
        try:
            db.execute(
                "INSERT INTO tenants (id, name, api_key_hash, username, password_hash, role) VALUES (?, ?, ?, ?, ?, ?)",
                (tenant_id, name, key_hash, username, password_hash, role or "admin"),
            )
            return {
                "ok": True,
                "tenant_id": tenant_id,
                "api_key": key,
                "name": name,
                "username": username,
                "role": role or "admin",
            }
        except sqlite3.IntegrityError:
            return {"ok": False, "error": "TENANT_EXISTS"}

def authenticate_tenant(root: Path, username_or_api_key: str, password: str = "") -> dict | None:
    """Authenticate a tenant by username/password or API key and return tenant metadata."""
    init_db(root)
    with _conn(root) as db:
        row = None
        if password:
            row = db.execute(
                "SELECT id, username, role FROM tenants WHERE username = ? AND password_hash = ?",
                (username_or_api_key, _password_hash(password)),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT id, username, role FROM tenants WHERE api_key_hash = ?",
                (hashlib.sha256(username_or_api_key.encode()).hexdigest(),),
            ).fetchone()
        if not row:
            return None
        return {
            "tenant_id": row["id"],
            "username": row["username"] or row["id"],
            "role": row["role"] or "admin",
        }

def verify_api_key(root: Path, api_key: str) -> str | None:
    """Verify API key, return tenant_id if valid, None otherwise."""
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    init_db(root)
    with _conn(root) as db:
        row = db.execute("SELECT id FROM tenants WHERE api_key_hash = ?", (key_hash,)).fetchone()
        return row["id"] if row else None

def list_tenants(root: Path) -> list[dict]:
    init_db(root)
    with _conn(root) as db:
        rows = db.execute(
            "SELECT id, name, username, role, created_at, quota_scans FROM tenants ORDER BY created_at"
        ).fetchall()
        return [
            {
                "tenant_id": r["id"],
                "name": r["name"],
                "username": r["username"],
                "role": r["role"] or "admin",
                "created_at": r["created_at"],
                "quota_scans": r["quota_scans"],
            }
            for r in rows
        ]


# ── Project management ──

def create_project(root: Path, tenant_id: str, project_id: str, name: str = "", base_url: str = "") -> dict:
    init_db(root)
    with _conn(root) as db:
        try:
            db.execute("INSERT INTO projects (id, tenant_id, name, base_url) VALUES (?, ?, ?, ?)",
                       (project_id, tenant_id, name or project_id, base_url))
            # Also ensure file directory for backward compat
            out_dir = root / "platform_outputs" / project_id
            out_dir.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "project_id": project_id, "tenant_id": tenant_id}
        except sqlite3.IntegrityError:
            return {"ok": False, "error": "PROJECT_EXISTS"}

def list_projects(root: Path, tenant_id: str) -> list[dict]:
    init_db(root)
    with _conn(root) as db:
        rows = db.execute(
            "SELECT id, name, base_url, created_at FROM projects WHERE tenant_id = ? ORDER BY created_at",
            (tenant_id,)
        ).fetchall()
        return [{"project_id": r["id"], "customer_name": r["name"], "project_name": r["name"],
                 "base_url": r["base_url"], "created_at": r["created_at"]} for r in rows]


# ── Scan persistence ──

def save_scan(root: Path, tenant_id: str, project_id: str, scan_result: dict) -> str:
    """Persist a scan and its findings."""
    init_db(root)
    scan_id = f"{tenant_id}_{project_id}_{int(time.time() * 1000)}"
    findings = scan_result.get("findings", [])
    layers = scan_result.get("layers", {})
    with _conn(root) as db:
        db.execute("""
            INSERT INTO scans (id, tenant_id, project_id, grade, score, coverage, total_findings, total_ms, layers_json, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            scan_id, tenant_id, project_id,
            scan_result.get("grade", ""), scan_result.get("score", 0),
            scan_result.get("coverage", 0), scan_result.get("total_findings", 0),
            scan_result.get("total_ms", 0), json.dumps(layers, ensure_ascii=False)
        ))
        for i, f in enumerate(findings):
            fid = f"{scan_id}_{i}"
            db.execute("""
                INSERT INTO findings (id, tenant_id, project_id, scan_id, title, severity, category, description, confidence, evidence_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                fid, tenant_id, project_id, scan_id,
                f.get("title", ""), f.get("severity", "P1"), f.get("category", ""),
                f.get("description", "")[:500], f.get("confidence_score", 0),
                json.dumps(f.get("evidence", {}), ensure_ascii=False)
            ))
    return scan_id

def get_scan_history(root: Path, tenant_id: str, project_id: str, limit: int = 20) -> list[dict]:
    init_db(root)
    with _conn(root) as db:
        rows = db.execute("""
            SELECT id, grade, score, coverage, total_findings, total_ms, finished_at
            FROM scans
            WHERE tenant_id = ? AND project_id = ?
            ORDER BY finished_at DESC
            LIMIT ?
        """, (tenant_id, project_id, limit)).fetchall()
        return [{"scan_id": r["id"], "grade": r["grade"], "score": r["score"],
                 "coverage": r["coverage"], "total_findings": r["total_findings"],
                 "total_ms": r["total_ms"], "finished_at": r["finished_at"]} for r in rows]


# ── Knowledge docs ──

def save_knowledge_doc(root: Path, tenant_id: str, project_id: str, filename: str, content: str, doc_type: str = "prd") -> str:
    init_db(root)
    doc_id = hashlib.md5(f"{tenant_id}_{project_id}_{filename}".encode()).hexdigest()[:16]
    with _conn(root) as db:
        db.execute("""
            INSERT OR REPLACE INTO knowledge_docs (id, tenant_id, project_id, filename, content, type)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (doc_id, tenant_id, project_id, filename, content, doc_type))
    return doc_id

def get_knowledge_docs(root: Path, tenant_id: str, project_id: str) -> list[dict]:
    init_db(root)
    with _conn(root) as db:
        rows = db.execute(
            "SELECT id, filename, type, created_at FROM knowledge_docs WHERE tenant_id = ? AND project_id = ?",
            (tenant_id, project_id)
        ).fetchall()
        return [{"source_id": r["id"], "display_name": r["filename"], "type": r["type"],
                 "created_at": r["created_at"]} for r in rows]

def get_knowledge_doc_content(root: Path, doc_id: str) -> str:
    init_db(root)
    with _conn(root) as db:
        row = db.execute("SELECT content FROM knowledge_docs WHERE id = ?", (doc_id,)).fetchone()
        return row["content"] if row else ""
