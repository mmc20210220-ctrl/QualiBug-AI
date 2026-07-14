"""Database discovery and coupon validation utilities.
Extracted from v12_pipeline.py.
NOTE: Some coupon-specific functions should be generalized for cross-industry use.
"""
from __future__ import annotations

import json, os, re
from pathlib import Path
from typing import Any


def _profile_database_dsn(profile: dict[str, Any]) -> str:
    database = profile.get("database") if isinstance(profile, dict) else {}
    if not isinstance(database, dict):
        return ""
    for key in ("dsn", "url", "connection_string"):
        value = str(database.get(key) or "").strip()
        if value:
            return value
    host = str(database.get("host") or "").strip()
    name = str(database.get("database") or database.get("name") or "").strip()
    user = str(database.get("user") or "").strip()
    password = str(database.get("password") or "").strip()
    if not (host and name and user):
        return ""
    port = int(database.get("port") or 5432)
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def _dsn_from_text(text: str) -> str:
    match = re.search(r"(postgres(?:ql)?://[^\s`\"']+)", str(text or ""), re.I)
    return str(match.group(1) or "").strip() if match else ""


def _db_dialect_from_dsn(dsn: str) -> str:
    if not dsn:
        return ""
    dsn_lower = dsn.lower().strip()
    if dsn_lower.startswith("sqlite"):
        return "sqlite"
    if any(dsn_lower.startswith(p) for p in ("postgresql://", "postgres://")):
        return "postgresql"
    if dsn_lower.startswith("mysql"):
        return "mysql"
    if dsn_lower.startswith("mssql") or "sql server" in dsn_lower:
        return "mssql"
    return "unknown"


def _list_relation_names(cur: Any, *, dialect: str, is_sqlite: bool) -> list[str]:
    """Cross-dialect table/relation listing."""
    names: list[str] = []
    try:
        if is_sqlite:
            rows = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        elif dialect in ("postgresql", "mysql"):
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema NOT IN ('information_schema','pg_catalog')")
            rows = cur.fetchall()
        else:
            rows = cur.tables()
        for row in rows:
            name = str(row[0] if isinstance(row, (tuple, list)) else getattr(row, 'table_name', str(row))).strip()
            if name:
                names.append(name)
    except Exception:
        pass
    return sorted(set(names))


def _list_relation_columns(cur: Any, table: str, *, dialect: str, is_sqlite: bool) -> list[str]:
    """Cross-dialect column listing."""
    cols: list[str] = []
    try:
        if is_sqlite:
            rows = cur.execute(f"PRAGMA table_info('{table}')").fetchall()
            cols = [str(r[1]).strip() for r in rows if r[1]]
        elif dialect in ("postgresql", "mysql"):
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", (table,))
            cols = [str(r[0]).strip() for r in cur.fetchall()]
        else:
            for desc in cur.columns(table):
                cols.append(str(desc.get('column_name', desc.get('name', ''))).strip())
    except Exception:
        pass
    return sorted(set(c for c in cols if c))


def _map_schema_columns(columns: list[str], alias_groups: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """Map actual column names to canonical names using alias groups."""
    result: dict[str, str] = {}
    lower_cols = {c.lower(): c for c in columns}
    for canonical, aliases in alias_groups.items():
        for alias in aliases:
            if alias.lower() in lower_cols:
                result[canonical] = lower_cols[alias.lower()]
                break
    return result
