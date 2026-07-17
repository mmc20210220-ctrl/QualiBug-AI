"""Cross-dialect database discovery helpers.

SSOT for DSN/profile/relation introspection. Industry-specific coupon sampling
was removed from the product path (violates industry-neutral contract).
"""
from __future__ import annotations

import re
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
    if is_sqlite:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        rows = cur.fetchall() or []
        return [str(row[0] if not isinstance(row, dict) else row.get("name") or "") for row in rows if row]
    if dialect == "postgresql":
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
        )
    elif dialect in {"mysql", "mariadb"}:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=DATABASE() AND table_type='BASE TABLE'"
        )
    else:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_type='BASE TABLE'"
        )
    rows = cur.fetchall() or []
    names: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            names.append(str(row.get("table_name") or row.get("TABLE_NAME") or ""))
        else:
            names.append(str(row[0] if row else ""))
    return [name for name in names if name]


def _list_relation_columns(cur: Any, table: str, *, dialect: str, is_sqlite: bool) -> list[str]:
    if is_sqlite:
        cur.execute(f"PRAGMA table_info({table})")
        rows = cur.fetchall() or []
        cols: list[str] = []
        for row in rows:
            if isinstance(row, dict):
                cols.append(str(row.get("name") or ""))
            else:
                # PRAGMA table_info: cid, name, type, notnull, dflt_value, pk
                cols.append(str(row[1] if len(row) > 1 else ""))
        return [col for col in cols if col]
    if dialect == "postgresql":
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
            (table,),
        )
    elif dialect in {"mysql", "mariadb"}:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=DATABASE() AND table_name=? ORDER BY ordinal_position",
            (table,),
        )
    else:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=? ORDER BY ordinal_position",
            (table,),
        )
    rows = cur.fetchall() or []
    cols = []
    for row in rows:
        if isinstance(row, dict):
            cols.append(str(row.get("column_name") or row.get("COLUMN_NAME") or ""))
        else:
            cols.append(str(row[0] if row else ""))
    return [col for col in cols if col]


def _map_schema_columns(columns: list[str], alias_groups: dict[str, tuple[str, ...]]) -> dict[str, str]:
    normalized = {_normalize_schema_token(col): col for col in columns if str(col or "").strip()}
    mapped: dict[str, str] = {}
    for logical, aliases in alias_groups.items():
        for alias in aliases:
            hit = normalized.get(_normalize_schema_token(alias))
            if hit:
                mapped[logical] = hit
                break
        if logical in mapped:
            continue
        for alias in aliases:
            alias_norm = _normalize_schema_token(alias)
            for norm, original in normalized.items():
                if alias_norm and alias_norm in norm:
                    mapped[logical] = original
                    break
            if logical in mapped:
                break
    return mapped
