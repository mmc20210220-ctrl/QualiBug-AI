"""
QualiBug persistence layer — SQLite-based multi-tenant storage.
Replaces file-based JSON with database, while keeping backward compatibility.
"""
from __future__ import annotations
import json, os, sqlite3, time, hashlib, hmac, base64, secrets
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

_PBKDF2_ITERATIONS = 200_000

def _password_hash(password: str) -> str:
    """Hash password with PBKDF2-HMAC-SHA256 + random salt.

    Format: ``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>``.
    Replaces the legacy bare SHA-256 which was vulnerable to rainbow tables
    and lacked per-user salts.
    """
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=32)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"

def _verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored hash.

    Supports the new ``pbkdf2_sha256$...`` format and the legacy bare
    SHA-256 hexdigest for backward compatibility with existing tenants.
    Legacy hashes should be migrated on next password change.
    """
    if not stored or not password:
        return False
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iter_str, salt_b64, hash_b64 = stored.split("$", 3)
            iterations = int(iter_str)
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(hash_b64)
            dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=len(expected))
            return hmac.compare_digest(dk, expected)
        except Exception:
            return False
    # Legacy bare sha256 hexdigest — backward compat for pre-existing tenants.
    return hmac.compare_digest(hashlib.sha256(password.encode("utf-8")).hexdigest(), stored)

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
            # Salted hashes cannot be compared in SQL — fetch the stored hash and
            # verify in Python so per-tenant salts are honoured.
            candidate = db.execute(
                "SELECT id, username, role, password_hash FROM tenants WHERE username = ?",
                (username_or_api_key,),
            ).fetchone()
            if candidate and _verify_password(password, candidate["password_hash"] or ""):
                row = candidate
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


# ── Cumulative findings (cross-scan bug accumulation) ──

def _finding_dedupe_key(f: dict) -> str:
    """Generic dedupe key: normalized title + method + path.

    Normalization removes engine/Oracle prefixes like [场景执行], [V12 HttpStatusOracle]
    so that the same finding reported by different engines is treated as one bug.

    Two findings with the same core title targeting the same API endpoint are the
    same bug, regardless of which scan round or engine discovered them.
    """
    import re as _re
    title = str(f.get("title") or f.get("description") or "")[:200].strip().lower()
    # Strip engine/oracle prefixes: [xxx] prefix patterns
    # Examples: [场景执行], [V12 HttpStatusOracle], [INPUT], [权限], [资金], etc.
    title = _re.sub(r'^\[[^\]]*\]\s*', '', title)
    # Also strip multiple leading [xxx] prefixes
    title = _re.sub(r'^(\[[^\]]*\]\s*)+', '', title)
    # Normalize whitespace
    title = _re.sub(r'\s+', ' ', title).strip()

    method = str(f.get("_api_method") or f.get("method") or (f.get("evidence") or {}).get("method") or "").upper()
    path = str(f.get("_api_path") or f.get("path") or (f.get("evidence") or {}).get("path") or "").strip()
    return f"{title}|{method}|{path}"


def merge_findings_cumulative(
    root: Path,
    tenant_id: str,
    project_id: str,
    scan_id: str,
    new_findings: list[dict],
) -> dict:
    """Merge new scan findings into the cumulative findings store.

    Semantics (the "bug shelf" model):
    - Bugs are never silently dropped when a new scan runs.
    - A finding that already exists (same dedupe key) keeps its existing
      ``status`` (open/resolved/falsified) — a new scan does not auto-close it.
    - A finding that is brand-new gets inserted with status='open'.
    - Findings from the previous scan that are NOT in the new scan are NOT
      auto-closed either: they remain on the shelf because the bug may simply
      not have been triggered this round (non-deterministic) or the new scan
      had less coverage.  Only an explicit replay that returns success=False
      (bug no longer reproduces) should flip status to 'resolved'.
    - Returns a summary dict with counts: new, existing, total_open.

    This is generic — no hardcoded business concepts, works for any industry.
    """
    init_db(root)
    import json as _json
    with _conn(root) as db:
        # Load existing open findings for this project
        # Load existing findings for this project across ALL tenants
        # (same project may have been scanned under different tenant_id due to
        #  login variations — bugs are per-project, not per-tenant)
        existing_rows = db.execute(
            "SELECT id, tenant_id, title, severity, category, description, confidence, status, evidence_json, scan_id "
            "FROM findings WHERE project_id = ?",
            (project_id,),
        ).fetchall()

        # Build existing-key → row map
        existing_map: dict[str, dict] = {}
        for r in existing_rows:
            f = {
                "title": r["title"],
                "severity": r["severity"],
                "category": r["category"],
                "description": r["description"],
                "confidence": r["confidence"],
                "_api_method": "",
                "_api_path": "",
            }
            try:
                ev = _json.loads(r["evidence_json"] or "{}")
                if isinstance(ev, dict):
                    f["_api_method"] = ev.get("method", "")
                    f["_api_path"] = ev.get("path", "")
            except Exception:
                pass
            key = _finding_dedupe_key(f)
            existing_map[key] = dict(r)

        new_count = 0
        existing_count = 0
        seen_keys: set[str] = set()

        for i, f in enumerate(new_findings):
            if not isinstance(f, dict):
                continue
            key = _finding_dedupe_key(f)
            seen_keys.add(key)

            if key in existing_map:
                # Already on the shelf — keep its status, do not overwrite
                existing_count += 1
                continue

            # Brand-new finding — insert as open
            fid = f"{scan_id}_m{i}"
            evidence = f.get("evidence") if isinstance(f.get("evidence"), dict) else {}
            # Enrich evidence with API path/method for future dedupe
            if f.get("_api_path") or f.get("_api_method"):
                evidence = {**evidence}
                if f.get("_api_path"):
                    evidence.setdefault("path", f["_api_path"])
                if f.get("_api_method"):
                    evidence.setdefault("method", f["_api_method"])
            db.execute("""
                INSERT INTO findings (id, tenant_id, project_id, scan_id, title, severity, category, description, confidence, status, evidence_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """, (
                fid, tenant_id, project_id, scan_id,
                str(f.get("title") or f.get("description", ""))[:500],
                str(f.get("severity", "P1")),
                str(f.get("category") or f.get("risk_type", "")),
                str(f.get("description", ""))[:500],
                float(f.get("confidence_score") or f.get("score") or 0),
                _json.dumps(evidence, ensure_ascii=False),
            ))
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
) -> list[dict]:
    """Load all cumulative findings for a project from the DB.

    By default returns only open findings (bugs not yet fixed).
    Set include_resolved=True to also return resolved/falsified findings.
    """
    init_db(root)
    import json as _json
    with _conn(root) as db:
        # Query by project_id across ALL tenants (bugs are per-project)
        if include_resolved:
            rows = db.execute(
                "SELECT id, title, severity, category, description, confidence, status, evidence_json, scan_id, created_at "
                "FROM findings WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, title, severity, category, description, confidence, status, evidence_json, scan_id, created_at "
                "FROM findings WHERE project_id = ? AND status = 'open' ORDER BY created_at",
                (project_id,),
            ).fetchall()
        results: list[dict] = []
        for r in rows:
            f = {
                "risk_id": r["id"],
                "title": r["title"],
                "severity": r["severity"],
                "category": r["category"],
                "description": r["description"],
                "confidence_score": r["confidence"],
                "status": r["status"],
                "scan_id": r["scan_id"],
                "first_seen_at": r["created_at"],
            }
            try:
                ev = _json.loads(r["evidence_json"] or "{}")
                if isinstance(ev, dict):
                    f["evidence"] = ev
                    if ev.get("path"):
                        f["_api_path"] = ev["path"]
                    if ev.get("method"):
                        f["_api_method"] = ev["method"]
            except Exception:
                pass
            results.append(f)
        return results


def update_finding_status(root: Path, finding_id: str, status: str) -> bool:
    """Update a finding's status (open/resolved/falsified).

    Used by the replay engine to mark a bug as 'resolved' when replay
    shows it no longer reproduces.
    """
    init_db(root)
    with _conn(root) as db:
        cur = db.execute(
            "UPDATE findings SET status = ? WHERE id = ?",
            (status, finding_id),
        )
        return cur.rowcount > 0


def get_finding_stats(root: Path, tenant_id: str, project_id: str) -> dict:
    """Get cumulative finding statistics for a project (across all tenants)."""
    init_db(root)
    with _conn(root) as db:
        rows = db.execute(
            "SELECT status, COUNT(*) as cnt FROM findings "
            "WHERE project_id = ? GROUP BY status",
            (project_id,),
        ).fetchall()
        stats = {"open": 0, "resolved": 0, "falsified": 0, "total": 0}
        for r in rows:
            s = r["status"] or "open"
            stats[s] = stats.get(s, 0) + r["cnt"]
            stats["total"] += r["cnt"]
        return stats


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
