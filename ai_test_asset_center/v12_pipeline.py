"""V12 source-grounded behavior pipeline with enterprise Campaign governance.

A Campaign is an auditable project scope, environment reference and source
snapshot. Planning can proceed without a target; any runtime traffic additionally
requires a valid source contract and a time-bounded execution approval issued for
the resolved Campaign.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from .enterprise_campaign import (
    EnterpriseCampaign,
    EnterpriseCampaignStore,
    has_real_confirmation_receipt,
    source_snapshot_hash,
)
from .real_id_resolver import (
    alternate_collection_paths,
    bind_entity_fields,
    bind_path_params_from_documented_body,
    collection_path,
    infer_path_params,
    normalize_path_placeholders,
    param_field_candidates,
    path_has_placeholders,
)
from .enterprise_project_config import (
    match_production_data_exclusion,
    _load_execution_safety_boundary,
)
from .target_policy import build_target_policy_decision

_v12_har_entries: list[dict[str, Any]] = []
# NOTE: _v12_har_entries is a module-level global. It is reset at the start of each
# pipeline run (line ~1414: `global _v12_har_entries; _v12_har_entries = []`).
# CONCURRENCY WARNING: If two scans run concurrently in the same process (e.g.,
# multithreaded server), HAR entries from one scan will contaminate the other.
# This is safe for the current single-scan-per-process deployment model.
# If multi-scan concurrency is ever enabled, replace this with threading.local().
_SENSITIVE = {"authorization", "token", "password", "secret", "cookie", "api_key", "apikey"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_AUTH_ACCEPTANCE_KEY_TOKENS = (
    "access_token",
    "refresh_token",
    "id_token",
    "auth_token",
    "jwt",
    "token",
    "session",
    "session_id",
    "sessionid",
    "bearer",
)
_AUTH_SUCCESS_BOOL_KEYS = {
    "authenticated",
    "authorized",
    "logged_in",
    "login_success",
    "success",
    "ok",
}
_AUTH_PRINCIPAL_KEYS = {"user", "account", "principal", "profile", "identity"}
_AUTH_ACCEPTANCE_HEADER_TOKENS = {"authorization", "set-cookie", "x-auth-token", "x-session-id"}
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")


def _auth_value_present(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return value not in (None, "", [], {})


def _auth_acceptance_observed(body: Any, headers: dict[str, Any] | None = None, *, _depth: int = 0) -> bool:
    """True when an auth response actually accepts the login, not just HTTP 200."""

    if _depth == 0:
        for key, value in (headers or {}).items():
            key_l = str(key or "").strip().lower()
            if key_l in _AUTH_ACCEPTANCE_HEADER_TOKENS and str(value or "").strip():
                return True
    if _depth > 8:
        return False
    if isinstance(body, dict):
        for key, value in body.items():
            key_l = str(key or "").strip().lower().replace("-", "_")
            if any(token in key_l for token in _AUTH_ACCEPTANCE_KEY_TOKENS) and _auth_value_present(value):
                return True
            if key_l in _AUTH_SUCCESS_BOOL_KEYS and value is True:
                return True
            if key_l in _AUTH_PRINCIPAL_KEYS and isinstance(value, dict) and bool(value):
                return True
            if _auth_acceptance_observed(value, None, _depth=_depth + 1):
                return True
        return False
    if isinstance(body, list):
        return any(_auth_acceptance_observed(item, None, _depth=_depth + 1) for item in body[:20])
    if isinstance(body, str):
        return bool(_JWT_RE.search(body))
    return False


def _record_v12_har(method: str, url: str, status: int, body: Any, actor: str = "", elapsed_ms: float = 0.0) -> None:
    _v12_har_entries.append({
        "startedDateTime": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "time": elapsed_ms,
        "request": {"method": method, "url": url},
        "response": {"status": status, "content": {"mimeType": "application/json", "text": str(body)[:5000]}},
        "_actor": actor,
    })


def _v12_har_report() -> dict[str, Any]:
    if not _v12_har_entries:
        return {"status": "no_traffic"}
    counts: dict[int, int] = {}
    entries: list[dict[str, Any]] = []
    for item in _v12_har_entries:
        status = int(item.get("response", {}).get("status") or 0)
        counts[status] = counts.get(status, 0) + 1
        content = item.get("response", {}).get("content", {})
        entries.append({
            "startedDateTime": item.get("startedDateTime"),
            "time": item.get("time"),
            "request": item.get("request"),
            "response": {"status": status, "body": str(content.get("text") if isinstance(content, dict) else content)[:2000]},
            "_actor": item.get("_actor", ""),
        })
    return {
        "status": "captured",
        "total_calls": len(entries),
        "error_responses": sum(count for status, count in counts.items() if status >= 400),
        "status_distribution": counts,
        "entries": entries,
    }


def is_v12_enabled() -> bool:
    return os.environ.get("ENABLE_V12_STATE_GRAPH_ENGINE", "false").lower() in {"1", "true", "yes", "on"}


def _scenario_executable(scenario: Any) -> bool:
    return bool(getattr(scenario, "steps", []) or []) and str(getattr(scenario, "execution_policy", "") or "") in {
        "safe_read_only",
        "approved_test_write",
        "approved_sandbox_write",
        "runtime_approved",
    }


def _is_test_write_allowed() -> bool:
    """Check if test-environment write operations are approved.

    ``QUALIBUG_ALLOW_TEST_WRITE=1`` means the target is a customer test
    environment where write operations are safe.  The system will still
    enforce DELETE safety guards and fixture data isolation.
    """
    return os.environ.get("QUALIBUG_ALLOW_TEST_WRITE", "").strip().lower() in ("1", "true", "yes")


def _test_write_fixture_prefix() -> str:
    return os.environ.get("QUALIBUG_TEST_FIXTURE_PREFIX", "qualibug_test_").strip() or "qualibug_test_"


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _behavior_slice_settings() -> dict[str, int]:
    try:
        from .policy_wiring import get_policy_value
        budget = get_policy_value("execution", "max_behavior_slices_per_round", 15)
        round_number = get_policy_value("execution", "incremental_discovery_round", 1)
        round_limit = get_policy_value("execution", "incremental_discovery_round_limit", 8)
    except Exception:
        budget, round_number, round_limit = 15, 1, 8
    # These are the PRE-POOL starting values. The real per-round budget and round
    # limit are auto-scaled to the discovered candidate-pool size just before
    # scheduling (see _auto_scale_slice_budget / _auto_scale_round_limit), so an
    # operator never has to hand-tune env vars for a large enterprise system.
    # An explicit env value still wins (power-user override).
    return {
        "slice_budget": _as_int(os.environ.get("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", budget), 15, 1, _ABS_MAX_SLICE_BUDGET),
        "round_number": _as_int(os.environ.get("QUALIBUG_DISCOVERY_ROUND", round_number), 1, 1, 24),
        "round_limit": _as_int(os.environ.get("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", round_limit), 8, 1, _ABS_MAX_ROUND_LIMIT),
    }


# Absolute safety clamps — the auto-scaler and any env override are bounded by
# these so a pathological pool size can never explode API cost without bound.
_ABS_MAX_SLICE_BUDGET = 200
_ABS_MAX_ROUND_LIMIT = 24

# Lower rank = higher scheduling priority (account_status / money before transition).
_SELECTION_KIND_RANK: dict[str, int] = {
    "account_status": 0,
    "money": 1,
    "inventory": 2,
    "concurrency": 3,
    "permission": 4,
    "isolation": 5,
    "transition": 6,
    "invariant": 7,
    "dependency": 8,
    "source_observation": 9,
}

_POOL_COLLAPSE_KINDS = frozenset({"permission", "isolation"})
_POOL_PROTECTED_KINDS = frozenset({"account_status", "money", "concurrency", "inventory"})
_POOL_ORIGIN_KEEP_RANK: dict[str, int] = {
    "historical_bug": 0,
    "supplementary": 1,
    "state_graph": 2,
    "analyzer": 3,
    "llm_reasoner": 4,
}


def _auto_scale_slice_budget(pool_size: int) -> int:
    """Per-round slice budget that follows the business system's scale.

    Small systems (few source-bound slices) keep the lean historical floor of 15.
    Large enterprises (hundreds of slices from state graph + analyzers + LLM
    reasoner) automatically get a proportionally larger budget so the candidate
    pool is actually consumed instead of starving at 15/round — no env tuning.
    Target: drain the pool in ~2 rounds, bounded by _ABS_MAX_SLICE_BUDGET.
    """
    import math

    if pool_size <= 0:
        return 15
    return max(15, min(_ABS_MAX_SLICE_BUDGET, math.ceil(pool_size / 2)))


def _auto_scale_round_limit(pool_size: int, budget: int) -> int:
    """Automatic round count sized to actually drain ``pool_size`` at ``budget``/round."""
    import math

    if pool_size <= 0 or budget <= 0:
        return 8
    needed = math.ceil(pool_size / budget) + 1
    return max(8, min(_ABS_MAX_ROUND_LIMIT, needed))


def _test_profile(project: str, root: Path) -> dict[str, Any]:
    try:
        from .enterprise_pilot_runtime import load_connector_registry

        registry = load_connector_registry(project, root)
    except Exception:
        return {}
    profile = registry.get("test_profile") if isinstance(registry, dict) else {}
    return dict(profile) if isinstance(profile, dict) else {}


def _login_route(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    for route in catalog or []:
        if not isinstance(route, dict):
            continue
        method = str(route.get("method") or "").upper()
        path = str(route.get("path") or "")
        summary = str(route.get("summary") or "")
        operation_id = str(route.get("operation_id") or route.get("operationId") or "")
        fingerprint = f"{path} {summary} {operation_id}".lower()
        if method == "POST" and "login" in fingerprint and path.startswith("/"):
            return route
    return {}


def _login_example_credentials(api_doc: str, login_path: str) -> list[dict[str, str]]:
    if not str(api_doc or "").strip() or not str(login_path or "").strip():
        return []
    try:
        from .auto_test_data_factory import _markdown_request_example
        example = _markdown_request_example(api_doc, "POST", login_path)
    except Exception:
        return []
    if not isinstance(example, dict):
        return []
    email = str(example.get("email") or example.get("username") or "").strip()
    password = str(example.get("password") or example.get("pass") or "").strip()
    if not email or not password:
        return []
    return [{"email": email, "password": password}]


def _login_parameter_fuzzer(fuzzer: Any, catalog: list[dict[str, Any]], project: str, root: Path, api_doc: str = "") -> bool:
    login_route = _login_route(catalog)
    login_path = str(login_route.get("path") or "")
    if not login_path:
        return False
    from .enterprise_pilot_runtime import load_project_test_credentials

    candidates = load_project_test_credentials(project, root)
    candidates.extend(_login_example_credentials(api_doc, login_path))
    for item in candidates:
        email = str(item.get("email") or "").strip()
        password = str(item.get("password") or "").strip()
        if email and password and fuzzer.login(email=email, password=password, login_path=login_path):
            return True
    return False


def _read_only_runtime_token(base_url: str, catalog: list[dict[str, Any]], project: str, root: Path, api_doc: str = "") -> str:
    if not str(base_url or "").strip():
        return ""
    try:
        from .parameter_fuzzer import ParameterFuzzer
    except Exception:
        return ""
    fuzzer = ParameterFuzzer(base_url, allow_write=False)
    if _login_parameter_fuzzer(fuzzer, catalog, project, root, api_doc=api_doc):
        return str(getattr(fuzzer, "_token", "") or "")
    return ""


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


def _runtime_db_dsn(project: str, root: Path, db_schema_text: str = "") -> str:
    env_dsn = str(os.environ.get("QUALIBUG_DB_DSN") or "").strip()
    if env_dsn:
        return env_dsn
    profile_dsn = _profile_database_dsn(_test_profile(project, root))
    if profile_dsn:
        return profile_dsn
    return _dsn_from_text(db_schema_text)


def _coupon_rule_from_scenario(scenario: Any) -> str:
    runtime_hints = getattr(scenario, "runtime_hints", {}) or {}
    if isinstance(runtime_hints, dict):
        rule = str(runtime_hints.get("coupon_validation_rule") or "").strip()
        if rule:
            return rule
    for item in getattr(scenario, "oracle_rules", []) or []:
        text = str(item or "").strip()
        if text.startswith("CouponOracle."):
            return text.split(".", 1)[1].strip()
    return ""


_PROMO_ENTITY_ALIASES = {
    "coupon", "coupons", "promotion", "promotions", "promo", "promo_code", "promo_codes",
    "voucher", "vouchers", "discount", "discounts", "subsidy", "subsidies",
    "fee_waiver", "fee_waivers", "rebate", "rebates", "优惠券", "促销", "代金券", "补贴",
}
_PROMO_TABLE_ALIASES = (
    "coupons", "coupon", "promotions", "promotion", "promo_codes", "promo_code",
    "vouchers", "voucher", "discounts", "discount", "subsidies", "subsidy",
    "fee_waivers", "fee_waiver", "rebates", "rebate",
)
_CATALOG_TABLE_ALIASES = (
    "products", "product", "items", "item", "goods", "skus", "sku",
    "materials", "material", "catalog_items", "catalog_item", "offerings", "offering",
)
_PROMO_COLUMN_ALIASES = {
    "code": ("code", "coupon_code", "promo_code", "voucher_code", "discount_code", "subsidy_code"),
    "status": ("status", "state", "enabled", "active"),
    "expires_at": ("expires_at", "expire_at", "expired_at", "valid_until", "end_time", "expiry_date", "end_at"),
    "min_order_amount": ("min_order_amount", "min_amount", "threshold", "minimum_amount", "min_order", "order_threshold"),
    "category_scope": ("category_scope", "category", "scope", "applicable_category", "category_id"),
}
_CATALOG_COLUMN_ALIASES = {
    "sku": ("sku", "product_sku", "item_code", "material_code", "product_code", "code", "product_id", "item_id"),
    "price": ("price", "unit_price", "amount", "sale_price", "list_price"),
    "category": ("category", "category_name", "category_id", "type", "class"),
    "status": ("status", "state", "sale_status"),
}


def _normalize_schema_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _is_promo_validation_entity(entity: str) -> bool:
    token = _normalize_schema_token(entity)
    if not token:
        return False
    aliases = {_normalize_schema_token(item) for item in _PROMO_ENTITY_ALIASES}
    return token in aliases or any(alias in token or token in alias for alias in aliases if alias)


def _pick_schema_name(names: list[str], aliases: tuple[str, ...]) -> str:
    normalized = {_normalize_schema_token(name): name for name in names if str(name or "").strip()}
    for alias in aliases:
        hit = normalized.get(_normalize_schema_token(alias))
        if hit:
            return hit
    # Fuzzy contains match for industry-specific prefixes/suffixes (e.g. t_promo_codes).
    for alias in aliases:
        alias_norm = _normalize_schema_token(alias)
        for norm, original in normalized.items():
            if alias_norm and (alias_norm in norm or norm in alias_norm):
                return original
    return ""


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


def _coupon_validation_request(code: str, *, sku: str, price: float, qty: int) -> dict[str, Any]:
    normalized_qty = max(1, int(qty or 1))
    normalized_price = round(float(price or 0.0), 2)
    total_amount = round(normalized_price * normalized_qty, 2)
    return {
        "code": str(code or "").strip(),
        "items": [{"sku": str(sku or "").strip(), "qty": normalized_qty, "price": normalized_price}],
        "totalAmount": total_amount,
    }


def _db_dialect_from_dsn(dsn: str) -> str:
    """Infer the SQL dialect from a DSN prefix.  Returns "" for NoSQL schemes."""
    _dsn = str(dsn or "").strip().lower()
    if not _dsn:
        return ""
    if _dsn.startswith(("postgresql://", "postgres://")):
        return "postgresql"
    if _dsn.startswith(("mysql://", "mariadb://")):
        return "mysql"
    if _dsn.startswith(("sqlite:///", "sqlite:")):
        return "sqlite"
    if _dsn.startswith(("mssql://", "sqlserver://")):
        return "mssql"
    if _dsn.startswith("oracle://"):
        return "oracle"
    if "://" in _dsn:
        return ""
    return "other"


def _discover_coupon_validation_samples(dsn: str) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Schema-driven promo/coupon sample discovery for validation scenarios.

    Discovers promo-like and catalog-like tables/columns from the live DB
    instead of hardcoding ecommerce ``products`` / ``coupons`` names.
    """
    meta: dict[str, Any] = {"status": "empty", "gap_code": "DB_SAMPLE_DISCOVERY_MISSING"}
    if not str(dsn or "").strip():
        meta.update({"status": "skipped", "gap_code": "DB_DSN_MISSING", "reason": "dsn_empty"})
        return {}, meta

    _dsn = str(dsn).strip()
    _dialect = _db_dialect_from_dsn(_dsn)
    if not _dialect:
        meta.update({"status": "skipped", "gap_code": "DB_DIALECT_UNSUPPORTED", "reason": "nosql_or_unrecognized"})
        return {}, meta

    conn = None
    _placeholder = "%s"
    _is_sqlite = _dialect == "sqlite"

    try:
        if _dialect == "sqlite":
            import sqlite3
            _db_path = _dsn
            for _pfx in ("sqlite:///", "sqlite:"):
                if _db_path.lower().startswith(_pfx):
                    _db_path = _db_path[len(_pfx):]
                    break
            if not Path(_db_path).exists():
                meta.update({"status": "failed", "gap_code": "DB_FILE_MISSING", "reason": _db_path})
                return {}, meta
            conn = sqlite3.connect(_db_path)
            conn.row_factory = sqlite3.Row
            _placeholder = "?"
        elif _dialect == "postgresql":
            import psycopg2
            conn = psycopg2.connect(_dsn)
        else:
            import pyodbc
            conn = pyodbc.connect(_dsn)
            _placeholder = "?"
    except Exception as exc:
        meta.update({"status": "failed", "gap_code": "DB_CONNECT_FAILED", "reason": str(exc)[:300]})
        return {}, meta

    if conn is None:
        meta.update({"status": "failed", "gap_code": "DB_CONNECT_FAILED", "reason": "conn_none"})
        return {}, meta

    def _now() -> str:
        return "datetime('now')" if _is_sqlite else "NOW()"

    def _nulls_last(order_col: str) -> str:
        if _is_sqlite:
            return f"CASE WHEN {order_col} IS NULL THEN 1 ELSE 0 END, {order_col}"
        return f"{order_col} NULLS LAST"

    try:
        cur = conn.cursor()
        tables = _list_relation_names(cur, dialect=_dialect, is_sqlite=_is_sqlite)
        promo_table = _pick_schema_name(tables, _PROMO_TABLE_ALIASES)
        catalog_table = _pick_schema_name(tables, _CATALOG_TABLE_ALIASES)
        meta["tables_seen"] = tables[:40]
        meta["promo_table"] = promo_table
        meta["catalog_table"] = catalog_table
        if not promo_table or not catalog_table:
            meta.update({
                "status": "failed",
                "gap_code": "DB_SCHEMA_TABLE_NOT_FOUND",
                "reason": f"promo={promo_table or '-'} catalog={catalog_table or '-'}",
            })
            return {}, meta

        promo_cols = _map_schema_columns(
            _list_relation_columns(cur, promo_table, dialect=_dialect, is_sqlite=_is_sqlite),
            _PROMO_COLUMN_ALIASES,
        )
        catalog_cols = _map_schema_columns(
            _list_relation_columns(cur, catalog_table, dialect=_dialect, is_sqlite=_is_sqlite),
            _CATALOG_COLUMN_ALIASES,
        )
        meta["promo_columns"] = promo_cols
        meta["catalog_columns"] = catalog_cols
        required_promo = {"code"}
        required_catalog = {"sku", "price"}
        if not required_promo.issubset(promo_cols) or not required_catalog.issubset(catalog_cols):
            meta.update({
                "status": "failed",
                "gap_code": "DB_SCHEMA_COLUMN_NOT_FOUND",
                "reason": f"promo_cols={promo_cols} catalog_cols={catalog_cols}",
            })
            return {}, meta

        def one(sql: str, params: tuple = ()) -> dict:
            if _placeholder == "?":
                sql = sql.replace("%s", "?")
            cur.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                return {}
            if _is_sqlite:
                return dict(row)
            cols = [str(item[0]) for item in cur.description]
            return dict(zip(cols, row))

        def project_promo(row: dict) -> dict:
            return {
                "code": row.get(promo_cols["code"]),
                "min_order_amount": row.get(promo_cols["min_order_amount"]) if "min_order_amount" in promo_cols else None,
                "category_scope": row.get(promo_cols["category_scope"]) if "category_scope" in promo_cols else None,
                "status": row.get(promo_cols["status"]) if "status" in promo_cols else None,
                "expires_at": row.get(promo_cols["expires_at"]) if "expires_at" in promo_cols else None,
            }

        def project_catalog(row: dict) -> dict:
            return {
                "sku": row.get(catalog_cols["sku"]),
                "category": row.get(catalog_cols["category"]) if "category" in catalog_cols else None,
                "price": row.get(catalog_cols["price"]),
                "status": row.get(catalog_cols["status"]) if "status" in catalog_cols else None,
            }

        status_col = catalog_cols.get("status")
        price_col = catalog_cols["price"]
        sku_col = catalog_cols["sku"]
        category_col = catalog_cols.get("category")

        def saleable_product(*, excluded_category: str = "") -> dict:
            where = [f"COALESCE({price_col}, 0) > 0"]
            params: list[Any] = []
            if status_col:
                where.append(
                    f"UPPER(COALESCE(CAST({status_col} AS TEXT), '')) IN "
                    f"('ON_SALE', 'ACTIVE', 'ENABLED', 'AVAILABLE', 'PUBLISHED')"
                )
            if excluded_category and category_col:
                where.append(f"COALESCE(CAST({category_col} AS TEXT), '') <> {_placeholder}")
                params.append(excluded_category)
            select_cols = [sku_col, price_col]
            if category_col:
                select_cols.append(category_col)
            if status_col:
                select_cols.append(status_col)
            sql = (
                f"SELECT {', '.join(select_cols)} FROM {catalog_table} "
                f"WHERE {' AND '.join(where)} ORDER BY {price_col} DESC, {sku_col} ASC LIMIT 1"
            )
            row = one(sql, tuple(params))
            return project_catalog(row) if row else {}

        def quantity_for(min_order_amount, price) -> int:
            import math as _math
            price_value = max(float(price or 0.0), 0.01)
            minimum = max(float(min_order_amount or 0.0), 0.0)
            return max(1, int(_math.ceil(max(minimum, price_value) / price_value)))

        code_col = promo_cols["code"]
        promo_status_col = promo_cols.get("status")
        expires_col = promo_cols.get("expires_at")
        min_col = promo_cols.get("min_order_amount")
        scope_col = promo_cols.get("category_scope")
        promo_select = [code_col]
        for optional in (min_col, scope_col, promo_status_col, expires_col):
            if optional and optional not in promo_select:
                promo_select.append(optional)

        samples: dict[str, dict[str, Any]] = {}
        if expires_col:
            expired = one(
                f"""
                SELECT {', '.join(promo_select)}
                FROM {promo_table}
                WHERE {expires_col} IS NOT NULL AND {expires_col} < {_now()}
                ORDER BY {expires_col} ASC, {code_col} ASC
                LIMIT 1
                """
            )
            if expired:
                product = saleable_product()
                if product:
                    projected = project_promo(expired)
                    qty = quantity_for(projected.get("min_order_amount"), product.get("price"))
                    samples["expired_coupon_must_be_invalid"] = {
                        "body": _coupon_validation_request(
                            str(projected.get("code") or ""),
                            sku=str(product.get("sku") or ""),
                            price=float(product.get("price") or 0.0),
                            qty=qty,
                        ),
                        "coupon_code": str(projected.get("code") or ""),
                        "coupon_status": str(projected.get("status") or ""),
                        "coupon_expires_at": str(projected.get("expires_at") or ""),
                        "item_sku": str(product.get("sku") or ""),
                        "item_category": str(product.get("category") or ""),
                        "source_tables": {"promo": promo_table, "catalog": catalog_table},
                    }

        if promo_status_col:
            inactive = one(
                f"""
                SELECT {', '.join(promo_select)}
                FROM {promo_table}
                WHERE UPPER(COALESCE(CAST({promo_status_col} AS TEXT), '')) NOT IN ('ACTIVE', 'ENABLED', 'ON')
                ORDER BY {_nulls_last(expires_col or code_col)} ASC, {code_col} ASC
                LIMIT 1
                """
            )
            if inactive:
                product = saleable_product()
                if product:
                    projected = project_promo(inactive)
                    qty = quantity_for(projected.get("min_order_amount"), product.get("price"))
                    samples["inactive_coupon_must_be_invalid"] = {
                        "body": _coupon_validation_request(
                            str(projected.get("code") or ""),
                            sku=str(product.get("sku") or ""),
                            price=float(product.get("price") or 0.0),
                            qty=qty,
                        ),
                        "coupon_code": str(projected.get("code") or ""),
                        "coupon_status": str(projected.get("status") or ""),
                        "coupon_expires_at": str(projected.get("expires_at") or ""),
                        "item_sku": str(product.get("sku") or ""),
                        "item_category": str(product.get("category") or ""),
                        "source_tables": {"promo": promo_table, "catalog": catalog_table},
                    }

        if scope_col and promo_status_col:
            where_active = (
                f"UPPER(COALESCE(CAST({promo_status_col} AS TEXT), '')) IN ('ACTIVE', 'ENABLED', 'ON') "
                f"AND {scope_col} IS NOT NULL"
            )
            if expires_col:
                where_active += f" AND ({expires_col} IS NULL OR {expires_col} >= {_now()})"
            order_col = min_col or code_col
            mismatched_category = one(
                f"""
                SELECT {', '.join(promo_select)}
                FROM {promo_table}
                WHERE {where_active}
                ORDER BY {_nulls_last(order_col)} DESC, {code_col} ASC
                LIMIT 1
                """
            )
            if mismatched_category:
                projected = project_promo(mismatched_category)
                product = saleable_product(excluded_category=str(projected.get("category_scope") or ""))
                if product:
                    qty = quantity_for(projected.get("min_order_amount"), product.get("price"))
                    samples["coupon_category_scope_must_match"] = {
                        "body": _coupon_validation_request(
                            str(projected.get("code") or ""),
                            sku=str(product.get("sku") or ""),
                            price=float(product.get("price") or 0.0),
                            qty=qty,
                        ),
                        "coupon_code": str(projected.get("code") or ""),
                        "coupon_status": str(projected.get("status") or ""),
                        "coupon_expires_at": str(projected.get("expires_at") or ""),
                        "coupon_category_scope": str(projected.get("category_scope") or ""),
                        "item_sku": str(product.get("sku") or ""),
                        "item_category": str(product.get("category") or ""),
                        "source_tables": {"promo": promo_table, "catalog": catalog_table},
                    }

        if samples:
            meta.update({"status": "ok", "gap_code": "", "sample_rules": sorted(samples.keys())})
        else:
            meta.update({"status": "empty", "gap_code": "DB_SAMPLE_DISCOVERY_MISSING", "reason": "no_matching_rows"})
        return samples, meta
    except Exception as exc:
        meta.update({"status": "failed", "gap_code": "DB_SAMPLE_DISCOVERY_ERROR", "reason": str(exc)[:300]})
        return {}, meta
    finally:
        try:
            conn.close()
        except Exception as exc:
            result["phases"]["api_spec_normalization"] = {
                "status": "degraded",
                "input_count": 1,
                "output_count": 0,
                "lost_count": 1,
                "duration_ms": 0,
                "errors": [{
                    "stage": "api_spec_normalization",
                    "code": "API_SPEC_NORMALIZATION_FAILED",
                    "identity": "submitted_api_spec_text",
                    "retryability": "after_source_fix",
                    "operator_action": "validate the submitted API specification format",
                    "detail": f"{type(exc).__name__}: {exc}"[:500],
                }],
            }


def _coupon_validation_samples(dsn: str) -> dict[str, dict[str, Any]]:
    """Return DB-discovered promo/coupon samples for validation scenarios."""
    samples, meta = _discover_coupon_validation_samples(dsn)
    _coupon_validation_samples.last_meta = meta  # type: ignore[attr-defined]
    return samples


def _enrich_coupon_validation_scenarios(scenarios: list[Any], dsn: str) -> None:
    samples = _coupon_validation_samples(dsn)
    meta = dict(getattr(_coupon_validation_samples, "last_meta", {}) or {})
    gap_code = str(meta.get("gap_code") or "DB_SAMPLE_DISCOVERY_MISSING").strip() or "DB_SAMPLE_DISCOVERY_MISSING"
    for scenario in scenarios:
        if not _is_promo_validation_entity(str(getattr(scenario, "entity", "") or "")):
            continue
        rule = _coupon_rule_from_scenario(scenario)
        if not rule:
            continue
        sample = dict(samples.get(rule) or {})
        if not sample:
            gaps = [str(item) for item in (getattr(scenario, "evidence_gaps", []) or []) if str(item).strip()]
            if gap_code not in gaps:
                gaps.append(gap_code)
            if "DB_SAMPLE_DISCOVERY_MISSING" not in gaps and gap_code != "DB_SAMPLE_DISCOVERY_MISSING":
                gaps.append("DB_SAMPLE_DISCOVERY_MISSING")
            scenario.evidence_gaps = gaps
            scenario.execution_policy = "plan_only_requires_fixture"
            runtime_hints = dict(getattr(scenario, "runtime_hints", {}) or {})
            runtime_hints["coupon_sample_discovery"] = meta
            scenario.runtime_hints = runtime_hints
            continue
        runtime_hints = dict(getattr(scenario, "runtime_hints", {}) or {})
        runtime_hints["coupon_validation_rule"] = rule
        runtime_hints["coupon_validation_sample"] = sample
        runtime_hints["coupon_sample_discovery"] = meta
        scenario.runtime_hints = runtime_hints
        steps = list(getattr(scenario, "steps", []) or [])
        if steps:
            steps[0].body_template = dict(sample.get("body") or {})


def _source_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _source_manifest_details(context: dict[str, Any], source_text: Any) -> tuple[dict[str, str], list[str]]:
    manifest = _dict(context.get("source_manifest"))
    source_id = str(manifest.get("source_id") or "").strip()
    source_hash = str(manifest.get("source_hash") or "").strip().lower().removeprefix("sha256:").strip()
    source_origin = str(manifest.get("source_origin") or "declared_manifest").strip()
    actual_hash = hashlib.sha256(_source_text(source_text).encode("utf-8")).hexdigest()
    issues: list[str] = []
    if not source_id or not source_hash:
        issues.append("SOURCE_PROVENANCE_MISSING")
    elif not _SHA256_RE.fullmatch(source_hash):
        issues.append("SOURCE_HASH_INVALID")
    elif source_hash != actual_hash:
        issues.append("SOURCE_HASH_MISMATCH")
    return {
        "source_id": source_id[:160],
        "source_hash": source_hash[:128],
        "source_origin": source_origin[:80],
        "source_version_id": str(manifest.get("source_version_id") or "")[:80],
    }, issues


def _runtime_contract(context: dict[str, Any], base_url: str, source_text: Any) -> dict[str, Any]:
    """Direct callers cannot bypass source, scope, environment, or hash approval."""
    verification_text = context.get("_source_verification_text", source_text)
    manifest, source_issues = _source_manifest_details(context, verification_text)
    environment_ref = str(context.get("environment_ref") or context.get("target_environment") or "").strip()
    environment_kind = str(
        context.get("environment_kind")
        or context.get("environment_type")
        or context.get("environment_class")
        or ""
    ).strip().lower()
    execution_mode = str(context.get("execution_mode") or "safe_read_only").strip() or "safe_read_only"
    if not base_url:
        return {
            "status": "plan_only",
            "reason": "runtime_target_missing",
            "approved_base_url": "",
            "environment_ref": environment_ref,
            "environment_kind": environment_kind,
            "execution_mode": execution_mode,
            "source_manifest": manifest,
            "source_issues": source_issues,
        }
    missing = list(source_issues)
    if not str(context.get("scope_id") or "").strip():
        missing.append("CAMPAIGN_SCOPE_MISSING")
    if not environment_ref:
        missing.append("ENVIRONMENT_REFERENCE_MISSING")
    if execution_mode == "approved_sandbox_write" and not environment_kind:
        missing.append("UNKNOWN_ENVIRONMENT")
    explicitly_approved_url = str(
        context.get("approved_base_url")
        or _dict(context.get("target_policy")).get("approved_base_url")
        or (base_url if not missing else "")
    ).strip()
    decision = build_target_policy_decision(
        requested_base_url=base_url,
        approved_base_url=explicitly_approved_url,
        environment_type=environment_kind,
        environment_ref=environment_ref,
        execution_mode=execution_mode,
        runtime_status="approved" if not missing else "blocked",
    )
    if execution_mode == "approved_sandbox_write" and not decision.get("write_allowed"):
        missing.extend(str(code) for code in decision.get("blocking_codes") or [])
    elif not decision.get("read_allowed"):
        missing.extend(str(code) for code in decision.get("blocking_codes") or [])
    if missing:
        return {
            "status": "blocked",
            "reason": "runtime_contract_missing",
            "missing_requirements": sorted(set(missing)),
            "approved_base_url": "",
            "environment_ref": environment_ref,
            "environment_kind": environment_kind,
            "execution_mode": execution_mode,
            "source_manifest": manifest,
            "target_policy_decision": decision,
        }
    return {
        "status": "approved",
        "reason": "",
        "missing_requirements": [],
        "requested_base_url": str(base_url).rstrip("/"),
        "approved_base_url": explicitly_approved_url.rstrip("/"),
        "environment_ref": environment_ref,
        "environment_kind": environment_kind,
        "execution_mode": execution_mode,
        "source_manifest": manifest,
        "target_policy_decision": decision,
    }


def _execution_approval_contract(context: dict[str, Any], campaign: EnterpriseCampaign, base_url: str, root: Path) -> dict[str, Any]:
    """Enforce the environment boundary after Campaign identity is known.

    A source-bound non-production campaign is authorized for automatic reads
    and writes without per-probe approval. Production and unknown environments
    are fail-closed for every write mode. ``execution_approval_id`` remains
    accepted as audit metadata, but is not a runtime prerequisite.
    """
    if not base_url:
        return {"status": "not_required", "reason": "runtime_target_missing"}
    execution_mode = str(context.get("execution_mode") or "safe_read_only").strip()
    if execution_mode == "safe_read_only":
        return {"status": "approved", "execution_mode": execution_mode, "write_allowed": False}
    from .sandbox_write_executor import (
        resolve_environment_kind,
    )

    environment_ref = str(campaign.environment_ref or context.get("environment_ref") or "").strip()
    environment_kind = str(
        context.get("environment_kind")
        or context.get("environment_type")
        or context.get("environment_class")
        or ""
    ).strip()
    if not environment_kind:
        # Project configuration is an explicit declaration. environment_ref is
        # never interpreted as an environment class.
        environment_kind = resolve_environment_kind(
            root,
            str(getattr(campaign, "project_id", "") or context.get("project") or ""),
            dict(context or {}),
        )
    decision = build_target_policy_decision(
        requested_base_url=base_url,
        approved_base_url=str(context.get("approved_base_url") or base_url),
        environment_type=environment_kind,
        environment_ref=environment_ref,
        execution_mode=execution_mode,
        runtime_status="approved",
    )
    if not decision.get("write_allowed"):
        codes = list(decision.get("blocking_codes") or [])
        return {
            "status": "blocked",
            "code": str(codes[0] if codes else "TARGET_POLICY_BLOCKED"),
            "blocking_codes": codes,
            "execution_mode": execution_mode,
            "target_policy_decision": decision,
        }
    approval_id = str(context.get("execution_approval_id") or "").strip()
    return {
        "status": "approved",
        "approval_id": approval_id,
        "execution_mode": execution_mode,
        "environment_ref": environment_ref,
        "environment_kind": environment_kind,
        "authorization_basis": "source_bound_nonproduction_campaign",
        "write_allowed": True,
        "target_policy_decision": decision,
    }


def _slice_ledger_path(root: Path, project: str) -> Path:
    return root / "platform_workspace" / str(project) / "defect_discovery" / "v12_behavior_slice_ledger.json"


def _evidence_chain_path(root: Path, project: str, evidence_id: str) -> Path:
    return root / "platform_workspace" / str(project) / "defect_discovery" / "evidence_chains" / f"{evidence_id}.json"


def _persist_evidence_chain(root: Path, project: str, evidence: dict[str, Any]) -> str:
    """主链 7: land a collected evidence chain on disk keyed by its (stable)
    evidence_id so it can be retrieved for regression (主链 9) and delivery
    (主链 8). Returns the written path, or '' when the evidence has no id."""
    evidence_id = str(evidence.get("evidence_id") or "").strip()
    if not evidence_id:
        raise ValueError("EVIDENCE_ID_MISSING")
    path = _evidence_chain_path(root, project, evidence_id)
    try:
        from .artifact_redactor import write_json_redacted

        write_json_redacted(path, evidence)
    except Exception as exc:
        raise RuntimeError(f"EVIDENCE_CHAIN_PERSIST_FAILED:{evidence_id}:{type(exc).__name__}") from exc
    return str(path)


def _confirmed_findings_path(root: Path, project: str) -> Path:
    """主链 9 Gap B1: location of the persistable confirmed-defect ledger. The
    regression runner reads this file to re-verify already-confirmed defects
    after a fix — closing the loop between 主链 6/7 and 主链 9.

    Uses the same ``platform_workspace/<project>/defect_discovery`` base as the
    evidence chains (主链 7) so 主链 9 can read both products from one place.
    """
    return root / "platform_workspace" / str(project) / "defect_discovery" / "confirmed_findings.json"


def _persist_confirmed_findings(root: Path, project: str, findings: list[dict[str, Any]]) -> int:
    """主链 9 Gap B1: persist every *deliverable* confirmed defect
    (``customer_delivery_status == "defect"``, must carry a stable ``evidence_id``)
    into ``defect_discovery/confirmed_findings.json`` keyed by evidence_id, so the
    regression runner can replay its reproduction reliably and tell resolved from
    regression.

    Only real defects are persisted — findings blocked by the 主链 1 production-data
    safety boundary (``blocked_safety_boundary``) are deliberately excluded, exactly
    as they are excluded from the customer delivery gate. Returns the number saved.
    """
    if not isinstance(findings, list):
        return 0
    ledger: dict[str, dict[str, Any]] = {}
    path = _confirmed_findings_path(root, project)
    if path.exists():
        try:
            _loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
            if isinstance(_loaded, dict):
                ledger = _loaded
        except Exception:
            ledger = {}
    saved = 0
    for f in findings:
        if not isinstance(f, dict):
            continue
        if str(f.get("customer_delivery_status") or "") != "defect":
            continue
        evidence_id = str(f.get("evidence_id") or "").strip()
        if not evidence_id:
            continue
        ev = f.get("evidence") if isinstance(f.get("evidence"), dict) else {}
        raw_evidence = f.get("raw_evidence") if isinstance(f.get("raw_evidence"), dict) else {}
        response_raw = raw_evidence.get("response_raw") if isinstance(raw_evidence.get("response_raw"), dict) else {}
        try:
            buggy_status_code = int(response_raw.get("status_code") or 0)
        except (TypeError, ValueError):
            buggy_status_code = 0
        # ── System Behavior Space contract forwarding ──
        # Preserve system promise metadata through the confirmed-findings ledger
        # so regression suite builder, regression runner, and risk clue pool all
        # inherit the contract without re-reading fragile two-step patches.
        _system_promise_id = str(f.get("system_promise_id") or "").strip()
        _regression_contract = f.get("regression_contract") if isinstance(f.get("regression_contract"), dict) else {}
        _sb_evidence = f.get("system_behavior_space_evidence") if isinstance(f.get("system_behavior_space_evidence"), dict) else {}
        _sb_dimensions = f.get("system_behavior_dimensions") if isinstance(f.get("system_behavior_dimensions"), list) else []
        _sb_surface_plan = f.get("system_behavior_surface_plan") if isinstance(f.get("system_behavior_surface_plan"), list) else []
        _sb_required_assets = f.get("system_behavior_required_assets") if isinstance(f.get("system_behavior_required_assets"), list) else []
        _sb_source_family = str(f.get("system_behavior_source_family") or "").strip()
        _learning_signal = f.get("learning_signal") if isinstance(f.get("learning_signal"), dict) else {}

        entry: dict[str, Any] = {
            "evidence_id": evidence_id,
            "title": str(f.get("title") or ""),
            "severity": str(f.get("severity") or "P2"),
            "confirmation_status": str(f.get("confirmation_status") or ""),
            "bug_status": str(f.get("bug_status") or ""),
            "customer_delivery_status": "defect",
            "expected": str(f.get("expected") or ""),
            "actual": str(f.get("actual") or ""),
            "buggy_status_code": buggy_status_code,
            "behavior_slice_id": str(f.get("behavior_slice_id") or ""),
            "discovery_round": f.get("discovery_round"),
            "campaign_id": str(f.get("campaign_id") or ""),
            "timestamp": str(f.get("timestamp") or ""),
            "reproduction": {
                "request": str(ev.get("request") or ""),
                "target": str(ev.get("target") or ""),
                "method": str((ev.get("request") or "").split(" ", 1)[0].strip()),
                "path": "/" + str((ev.get("request") or "").split(" ", 1)[-1].lstrip("/")) if " " in str(ev.get("request") or "") else "",
                "actor": str(ev.get("actor") or ""),
                "reproduction_steps": list(ev.get("reproduction_steps") or []),
            },
        }
        if _system_promise_id:
            entry["system_promise_id"] = _system_promise_id
        if _regression_contract:
            entry["regression_contract"] = _regression_contract
        if _sb_evidence:
            entry["system_behavior_space_evidence"] = _sb_evidence
        if _sb_dimensions:
            entry["system_behavior_dimensions"] = [str(item) for item in _sb_dimensions if str(item)]
        if _sb_surface_plan:
            entry["system_behavior_surface_plan"] = [str(item) for item in _sb_surface_plan if str(item)]
        if _sb_required_assets:
            entry["system_behavior_required_assets"] = [str(item) for item in _sb_required_assets if str(item)]
        if _sb_source_family:
            entry["system_behavior_source_family"] = _sb_source_family
        if _learning_signal:
            entry["learning_signal"] = _learning_signal
        ledger[evidence_id] = entry
        saved += 1
    if saved:
        try:
            from .artifact_redactor import write_json_redacted

            write_json_redacted(path, ledger)
        except Exception as exc:
            raise RuntimeError(f"CONFIRMED_FINDING_PERSIST_FAILED:{type(exc).__name__}") from exc
    return saved


def _load_persisted_slice_history(
    root: Path,
    project: str,
    source_snapshot_hash: str = "",
    source_hash: str = "",
) -> list[dict[str, Any]]:
    path = _slice_ledger_path(root, project)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    expected_snapshot = str(source_snapshot_hash or "").strip()
    expected_source_hash = str(source_hash or "").strip()
    persisted_snapshot = str(payload.get("source_snapshot_hash") or "").strip()
    persisted_source_hash = str(payload.get("source_hash") or "").strip()
    snapshot_matches = bool(expected_snapshot) and persisted_snapshot == expected_snapshot
    source_hash_matches = bool(expected_source_hash) and persisted_source_hash == expected_source_hash
    if expected_snapshot or expected_source_hash:
        if not snapshot_matches and not source_hash_matches:
            return []
    return [{"behavior_slice_ledger": payload}]


def _derive_slice_status(
    attempted_ids: list[str] | set[str] | tuple[str, ...],
    confirmed_ids: list[str] | set[str] | tuple[str, ...],
    campaign_status: str,
) -> dict[str, str]:
    """主链 4: turn raw campaign progress into an explicit per-task status map so
    the task list surfaced to the API/frontend carries pending/running/passed/blocked
    instead of only opaque id sets.

    - attempted & confirmed        -> passed
    - attempted & not confirmed     -> running (or blocked when the campaign is blocked)
    - not attempted (planned)       -> omitted from the map, implicitly "pending"
    """
    confirmed_set = set()
    for value in confirmed_ids:
        if value is None:
            continue
        s = str(value).strip()
        if s:
            confirmed_set.add(s)
    status: dict[str, str] = {}
    blocked = str(campaign_status or "").strip() == "blocked"
    for value in attempted_ids:
        if value is None:
            continue
        sid = str(value).strip()
        if not sid:
            continue
        if sid in confirmed_set:
            status[sid] = "passed"
        else:
            status[sid] = "blocked" if blocked else "running"
    return status


def _persist_slice_ledger(root: Path, project: str, ledger: dict[str, Any]) -> None:
    path = _slice_ledger_path(root, project)
    attempted = [str(value) for value in ledger.get("attempted_slice_ids", []) if str(value)]
    confirmed = [str(value) for value in ledger.get("confirmed_slice_ids", []) if str(value)]
    # 主链 4: derive an explicit per-task status map from the campaign progress
    # so the task list surfaced to the API/frontend carries pending/running/
    # passed/blocked instead of only opaque id sets.
    slice_status = _derive_slice_status(attempted, confirmed, ledger.get("campaign_status") or "")
    safe = {
        "campaign_id": str(ledger.get("campaign_id") or ""),
        "campaign_status": str(ledger.get("campaign_status") or ""),
        "scope_id": str(ledger.get("scope_id") or ""),
        "source_snapshot_hash": str(ledger.get("source_snapshot_hash") or ""),
        "source_id": str(ledger.get("source_id") or ""),
        "source_hash": str(ledger.get("source_hash") or ""),
        "project": str(project),
        "round": int(ledger.get("round") or 0),
        "round_limit": int(ledger.get("round_limit") or 0),
        "slice_budget": int(ledger.get("slice_budget") or 0),
        "selection_mode": str(ledger.get("selection_mode") or ""),
        "selected_slice_ids": [str(value) for value in ledger.get("selected_slice_ids", []) if str(value)],
        "attempted_slice_ids": attempted,
        "confirmed_slice_ids": confirmed,
        "slice_status": slice_status,
        "next_round": ledger.get("next_round"),
        "stop_reason": str(ledger.get("stop_reason") or ""),
        "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    from .artifact_redactor import write_json_redacted

    write_json_redacted(path, safe)


def _history_item_counts_as_attempted(item: dict[str, Any]) -> bool:
    if has_real_confirmation_receipt(item):
        return True
    return str(item.get("execution_status") or "").strip().lower() == "executed"


def _slice_history(history: list[dict[str, Any]] | None) -> tuple[set[str], set[str]]:
    attempted: set[str] = set()
    confirmed: set[str] = set()
    for item in history or []:
        if not isinstance(item, dict):
            continue
        ledger = item.get("behavior_slice_ledger")
        if isinstance(ledger, dict):
            attempted.update(str(value) for value in ledger.get("attempted_slice_ids", []) if str(value))
            confirmed.update(str(value) for value in ledger.get("confirmed_slice_ids", []) if str(value))
        slice_id = str(item.get("behavior_slice_id") or item.get("source_slice_id") or item.get("slice_id") or "").strip()
        if slice_id and _history_item_counts_as_attempted(item):
            attempted.add(slice_id)
        if slice_id and has_real_confirmation_receipt(item):
            confirmed.add(slice_id)
    return attempted, confirmed


def _selection_result(*, status: str, stop_reason: str, selected: list[dict[str, Any]], pending: list[dict[str, Any]], attempted: set[str], confirmed: set[str], next_round: int | None, selection_mode: str) -> dict[str, Any]:
    return {
        "status": status,
        "stop_reason": stop_reason,
        "selected": selected,
        "selected_slice_ids": [str(item.get("slice_id") or "") for item in selected],
        "next_round": next_round,
        "remaining_slice_count": max(0, len(pending) - len(selected)),
        "attempted_slice_ids": sorted(attempted),
        "confirmed_slice_ids": sorted(confirmed),
        "selection_mode": selection_mode,
    }


def _slice_has_source_executable_route(item: dict[str, Any]) -> bool:
    endpoints = item.get("endpoints") if isinstance(item, dict) else []
    if not isinstance(endpoints, list):
        return False
    return any(str(path or "").strip().startswith("/") for path in endpoints)


def _scenario_selection_score(scenario: Any) -> float:
    score = 0.0
    execution_policy = str(getattr(scenario, "execution_policy", "") or "")
    category = str(getattr(scenario, "category", "") or "")
    severity = str(getattr(scenario, "severity", "") or "")
    evidence_gaps = list(getattr(scenario, "evidence_gaps", []) or [])
    steps = list(getattr(scenario, "steps", []) or [])
    confidence = float(getattr(scenario, "confidence", 0.0) or 0.0)

    if execution_policy == "approved_sandbox_write":
        score += 6.0
    elif execution_policy == "approved_test_write":
        score += 5.0
    elif execution_policy in {"runtime_approved", "safe_read_only"}:
        score += 3.0

    if bool(getattr(scenario, "is_forbidden_path", False)):
        score += 3.0
    if bool(getattr(scenario, "is_boundary_path", False)):
        score += 1.0
    if bool(getattr(scenario, "is_concurrent", False)) or category == "concurrency":
        score += 2.0
    elif category == "state_machine":
        score += 1.5
    elif category == "dependency":
        score += 1.0
    elif category == "source_observation":
        score -= 1.0

    severity_boost = {"P0": 3.0, "P1": 2.0, "P2": 1.0}.get(severity, 0.0)
    score += severity_boost
    score += min(len(steps), 6) * 0.15
    score += min(max(confidence, 0.0), 1.0)
    score -= min(len(evidence_gaps), 4) * 0.5
    return score


def _normalize_selection_family(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"^(\[[^\]]*\]\s*)+", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"'[^']+'", "'<id>'", text)
    text = re.sub(r'"[^"]+"', '"<id>"', text)
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<id>", text, flags=re.I)
    text = re.sub(r"\b\d{6,}\b", "<id>", text)
    return text.strip()


def _slice_selection_family(item: dict[str, Any]) -> str:
    family = _normalize_selection_family(item.get("_selection_family"))
    if family:
        return family
    entity = str(item.get("entity") or "").strip().lower()
    kind = str(item.get("kind") or "").strip().lower()
    states = ",".join(str(value).strip().lower() for value in (item.get("states") or []) if str(value).strip())
    endpoints = ",".join(str(value).strip().lower() for value in (item.get("endpoints") or []) if str(value).strip())
    return "|".join(part for part in (entity, kind, states, endpoints) if part) or str(item.get("slice_id") or "")


def _slice_selection_entity(item: dict[str, Any]) -> str:
    return str(item.get("entity") or "").strip().lower()


def _prioritize_confirmed_state_variants(
    items: list[dict[str, Any]],
    *,
    confirmed_slice_ids: set[str],
    all_slices: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not items or not confirmed_slice_ids or not all_slices:
        return items
    slice_index = {
        str(item.get("slice_id") or ""): item
        for item in all_slices
        if isinstance(item, dict) and str(item.get("slice_id") or "")
    }
    confirmed_families: dict[tuple[str, str], set[str]] = defaultdict(set)
    for slice_id in confirmed_slice_ids:
        confirmed_item = slice_index.get(str(slice_id))
        if not confirmed_item:
            continue
        entity = _slice_selection_entity(confirmed_item)
        kind = str(confirmed_item.get("kind") or "").strip().lower()
        family = _slice_selection_family(confirmed_item)
        if entity and kind and family:
            confirmed_families[(entity, kind)].add(family)
    if not confirmed_families:
        return items
    prioritized: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for item in items:
        entity = _slice_selection_entity(item)
        kind = str(item.get("kind") or "").strip().lower()
        family = _slice_selection_family(item)
        states = [str(value).strip().upper() for value in (item.get("states") or []) if str(value).strip()]
        if states and family and family not in confirmed_families.get((entity, kind), set()):
            prioritized.append(item)
        else:
            deferred.append(item)
    return prioritized + deferred if prioritized else items


def _slice_hypothesis_origin(item: dict[str, Any]) -> str:
    origin = str(item.get("_hypothesis_origin") or "").strip().lower()
    if origin:
        return origin
    if item.get("_historical_bug_id"):
        return "historical_bug"
    for ref in item.get("source_refs") or []:
        if isinstance(ref, dict) and str(ref.get("kind") or "").strip().lower() == "historical_bug":
            return "historical_bug"
    return "state_graph"


def _slice_is_pool_protected(item: dict[str, Any]) -> bool:
    kind = str(item.get("kind") or "").strip().lower()
    if kind in _POOL_PROTECTED_KINDS:
        return True
    if item.get("_historical_bug_id"):
        return True
    if _slice_hypothesis_origin(item) == "historical_bug":
        return True
    for ref in item.get("source_refs") or []:
        if isinstance(ref, dict) and str(ref.get("kind") or "").strip().lower() == "historical_bug":
            return True
    return False


def _slice_route_collapse_key(item: dict[str, Any]) -> tuple[str, str, str] | None:
    kind = str(item.get("kind") or "").strip().lower()
    if kind not in _POOL_COLLAPSE_KINDS:
        return None
    method = str(
        item.get("_permission_method")
        or item.get("_bound_method")
        or item.get("method")
        or "GET"
    ).upper()
    path = str(item.get("_permission_path") or item.get("_bound_path") or "").strip().lower()
    if not path:
        endpoints = item.get("endpoints") if isinstance(item.get("endpoints"), list) else []
        for endpoint in endpoints:
            text = str(endpoint or "").strip().lower()
            if text.startswith("/"):
                path = text.split("?", 1)[0]
                break
    if not path:
        return None
    return (kind, method, path)


def _slice_llm_invariant_collapse_key(item: dict[str, Any]) -> tuple[str, str, tuple[str, ...], str] | None:
    kind = str(item.get("kind") or "").strip().lower()
    if kind != "invariant" or _slice_hypothesis_origin(item) != "llm_reasoner":
        return None
    entity = _slice_selection_entity(item) or "resource"
    endpoints = tuple(
        sorted(
            str(value or "").strip().lower().split("?", 1)[0]
            for value in (item.get("endpoints") or [])
            if str(value or "").strip().startswith("/")
        )
    )
    if not endpoints:
        return None
    # Do not collapse every invariant that happens to touch the same route.
    # Payment, lifecycle, audit, and conservation hypotheses commonly share one
    # endpoint but represent different executable assertions.  Collapse only
    # semantically identical text, while retaining the old endpoint-level
    # fallback for legacy slices with no semantic text at all.  Keep numeric
    # thresholds and amounts intact: normalizing them would erase distinct
    # business assertions such as "<= 100" versus "<= 200".
    semantic = str(
        item.get("_invariant_text")
        or item.get("_selection_family")
        or item.get("_hypothesis_family")
        or ""
    ).strip().lower()
    semantic = re.sub(r"\s+", " ", semantic)
    return (kind, entity, endpoints, semantic)


def _slice_has_actor_credentials(item: dict[str, Any]) -> bool:
    for key in (
        "_permission_email",
        "_default_email",
        "_isolation_viewer_email",
        "_account_status_email",
    ):
        if str(item.get(key) or "").strip():
            return True
    return False


def _slice_pool_keep_score(item: dict[str, Any]) -> tuple[int, int, int, float, str]:
    origin_rank = _POOL_ORIGIN_KEEP_RANK.get(_slice_hypothesis_origin(item), 9)
    has_route = 1 if _slice_has_source_executable_route(item) else 0
    has_creds = 1 if _slice_has_actor_credentials(item) else 0
    priority = float(item.get("priority") or 0.0)
    slice_id = str(item.get("slice_id") or "")
    return (has_route, has_creds, -origin_rank, priority, slice_id)


def _optimize_behavior_slice_pool(slices: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collapse redundant LLM permission/isolation and invariant duplicates.

    Multi-role execution already exercises each route from every actor; keeping
    hundreds of near-identical LLM permission slices starves money, concurrency,
    and historical-bug coverage within the auto-scaled round budget.
    """
    protected: list[dict[str, Any]] = []
    route_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    invariant_groups: dict[tuple[str, str, tuple[str, ...]], list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    stats = {
        "input": len(slices),
        "protected": 0,
        "collapsed_permission_isolation": 0,
        "collapsed_llm_invariant": 0,
        "output": 0,
    }

    for item in slices:
        if not isinstance(item, dict):
            continue
        if _slice_is_pool_protected(item):
            protected.append(item)
            stats["protected"] += 1
            continue
        route_key = _slice_route_collapse_key(item)
        if route_key is not None:
            route_groups.setdefault(route_key, []).append(item)
            continue
        invariant_key = _slice_llm_invariant_collapse_key(item)
        if invariant_key is not None:
            invariant_groups.setdefault(invariant_key, []).append(item)
            continue
        passthrough.append(item)

    kept: list[dict[str, Any]] = list(protected)
    for group in route_groups.values():
        winner = max(group, key=_slice_pool_keep_score)
        kept.append(winner)
        if len(group) > 1:
            stats["collapsed_permission_isolation"] += len(group) - 1
    for group in invariant_groups.values():
        winner = max(group, key=_slice_pool_keep_score)
        kept.append(winner)
        if len(group) > 1:
            stats["collapsed_llm_invariant"] += len(group) - 1
    kept.extend(passthrough)
    stats["output"] = len(kept)
    return kept, stats


def _selection_kind_rank(item: dict[str, Any]) -> int:
    kind = str(item.get("kind") or "").strip().lower()
    return _SELECTION_KIND_RANK.get(kind, 9)


def _entity_primary_slice_rank(item: dict[str, Any], index: int) -> tuple[int, int]:
    return (_selection_kind_rank(item), index)


def _take_diverse_slice_batch(items: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    if budget <= 0 or not items:
        return []
    selected: list[dict[str, Any]] = []
    entity_deferred: list[dict[str, Any]] = []
    family_deferred: list[dict[str, Any]] = []
    entity_primary_ids: set[str] = set()
    seen_entities: set[str] = set()
    seen_families: set[str] = set()
    best_entity_items: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}

    for index, item in enumerate(items):
        entity = _slice_selection_entity(item)
        if not entity:
            continue
        candidate = (_entity_primary_slice_rank(item, index), item)
        current = best_entity_items.get(entity)
        if current is None or candidate[0] < current[0]:
            best_entity_items[entity] = candidate

    for item in items:
        entity = _slice_selection_entity(item)
        family = _slice_selection_family(item)
        primary = best_entity_items.get(entity)
        primary_item = primary[1] if primary else None
        primary_id = str(primary_item.get("slice_id") or "") if isinstance(primary_item, dict) else ""
        current_id = str(item.get("slice_id") or "")
        if entity and entity not in seen_entities and primary_id and current_id == primary_id:
            seen_entities.add(entity)
            if primary_id:
                entity_primary_ids.add(primary_id)
            if family:
                seen_families.add(family)
            selected.append(item)
        else:
            entity_deferred.append(item)
        if len(selected) >= budget:
            return selected

    for item in entity_deferred:
        if str(item.get("slice_id") or "") in entity_primary_ids:
            continue
        family = _slice_selection_family(item)
        if family and family not in seen_families:
            seen_families.add(family)
            selected.append(item)
        else:
            family_deferred.append(item)
        if len(selected) >= budget:
            return selected

    for item in family_deferred:
        selected.append(item)
        if len(selected) >= budget:
            break
    return selected


def _rank_behavior_slices_for_selection(slices: list[dict[str, Any]], scenarios: list[Any] | None = None) -> list[dict[str, Any]]:
    from .policy_wiring import get_policy_value

    configured_signals = get_policy_value(
        "discovery",
        "candidate_ranking_signals",
        ["source_strength", "endpoint_executability", "evidence_gap", "historical_yield"],
    )
    ranking_signals = [
        str(item).strip()
        for item in (configured_signals if isinstance(configured_signals, list) else [])
        if str(item).strip()
    ]

    def numeric(value: Any) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.0
        return parsed if math.isfinite(parsed) else 0.0

    def policy_signal(item: dict[str, Any], signal: str, dynamic: float) -> float:
        if signal == "source_strength":
            return float(len(item.get("source_refs") or []))
        if signal == "endpoint_executability":
            return 1.0 if item.get("endpoints") else 0.0
        if signal == "evidence_gap":
            return float(len(item.get("evidence_gaps") or []))
        if signal == "historical_yield":
            return numeric(item.get("historical_yield"))
        if signal == "weakness_recurrence":
            return numeric(item.get("weakness_recurrence"))
        if signal == "cross_industry_recurrence":
            return numeric(item.get("cross_industry_recurrence"))
        if signal == "runtime_executability":
            explicit = item.get("runtime_executability")
            return numeric(explicit) if explicit is not None else (1.0 if math.isfinite(dynamic) else 0.0)
        if signal == "cleanup_risk":
            return -numeric(item.get("cleanup_risk"))
        if signal == "evidence_completion_probability":
            return numeric(item.get("evidence_completion_probability"))
        return 0.0

    scenario_scores: dict[str, float] = {}
    scenario_families: dict[str, str] = {}
    scenario_selection_origins: dict[str, str] = {}
    for scenario in scenarios or []:
        slice_id = str(getattr(scenario, "behavior_slice_id", "") or "").strip()
        if not slice_id:
            continue
        scenario_scores[slice_id] = max(scenario_scores.get(slice_id, float("-inf")), _scenario_selection_score(scenario))
        title_family = _normalize_selection_family(getattr(scenario, "title", "") or getattr(scenario, "description", ""))
        if title_family and slice_id not in scenario_families:
            scenario_families[slice_id] = title_family
        selection_origin = str(getattr(scenario, "selection_origin", "") or "").strip().lower()
        if selection_origin:
            scenario_selection_origins[slice_id] = selection_origin

    def sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        slice_id = str(item.get("slice_id") or "")
        selection_origin = str(item.get("_selection_origin") or "").strip().lower()
        materialized_boost = 1 if selection_origin == "active_slice_fallback_materialized" else 0
        dynamic = scenario_scores.get(slice_id, float("-inf"))
        base = float(item.get("priority") or 0.0)
        kind_rank = _selection_kind_rank(item)
        policy_scores = tuple(policy_signal(item, signal, dynamic) for signal in ranking_signals)
        return (
            materialized_boost,
            dynamic,
            *policy_scores,
            base,
            kind_rank,
            -len(item.get("source_refs") or []),
        )

    ranked = []
    for item in slices:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        slice_id = str(normalized.get("slice_id") or "")
        if slice_id and slice_id in scenario_families:
            normalized["_selection_family"] = scenario_families[slice_id]
        if slice_id and slice_id in scenario_selection_origins:
            normalized["_selection_origin"] = scenario_selection_origins[slice_id]
        ranked.append(normalized)
    def descending_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        values = sort_key(item)
        numeric_prefix = values[: 2 + len(ranking_signals) + 1]
        kind_rank = values[-2]
        source_ref_rank = values[-1]
        return (
            *(-numeric(value) for value in numeric_prefix),
            kind_rank,
            source_ref_rank,
            str(item.get("entity") or ""),
            str(item.get("slice_id") or ""),
        )

    ranked.sort(key=descending_sort_key)
    return ranked


def _schedule_behavior_slices(slices: list[dict[str, Any]], settings: dict[str, int], history: list[dict[str, Any]] | None) -> dict[str, Any]:
    attempted, confirmed = _slice_history(history)
    all_slices = [item for item in slices if isinstance(item, dict) and str(item.get("slice_id") or "")]
    pending = [item for item in all_slices if str(item["slice_id"]) not in confirmed]
    round_number, round_limit, budget = int(settings["round_number"]), int(settings["round_limit"]), int(settings["slice_budget"])
    if not all_slices:
        return _selection_result(status="stopped", stop_reason="no_source_bound_behavior_slices", selected=[], pending=[], attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="none")
    if not pending:
        return _selection_result(status="stopped", stop_reason="all_source_bound_slices_confirmed", selected=[], pending=[], attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="none")
    if round_number > round_limit:
        return _selection_result(status="stopped", stop_reason="configured_round_limit_reached", selected=[], pending=pending, attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="round_limit")
    unattempted = [item for item in pending if str(item["slice_id"]) not in attempted]
    if attempted:
        executable_pending = [item for item in pending if _slice_has_source_executable_route(item)]
        executable_unattempted = [item for item in unattempted if _slice_has_source_executable_route(item)]
        executable_pending = _prioritize_confirmed_state_variants(executable_pending, confirmed_slice_ids=confirmed, all_slices=all_slices)
        executable_unattempted = _prioritize_confirmed_state_variants(executable_unattempted, confirmed_slice_ids=confirmed, all_slices=all_slices)
        if executable_unattempted:
            selected = _take_diverse_slice_batch(executable_unattempted, budget)
            remaining = len(executable_unattempted) - len(selected)
            return _selection_result(status="planned", stop_reason="slice_budget_reached" if remaining else "selected_final_unattempted_slice_batch", selected=selected, pending=executable_unattempted, attempted=attempted, confirmed=confirmed, next_round=round_number + 1 if remaining and round_number < round_limit else None, selection_mode="next_unattempted_executable_after_history")
        if unattempted:
            return _selection_result(status="stopped", stop_reason="remaining_unattempted_slices_not_source_executable", selected=[], pending=unattempted, attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="history_exhausted")
        if not unattempted:
            if executable_pending:
                selected = _take_diverse_slice_batch(executable_pending, budget)
                remaining = len(executable_pending) - len(selected)
                return _selection_result(status="planned", stop_reason="slice_budget_reached" if remaining else "selected_retryable_executable_slice_batch", selected=selected, pending=executable_pending, attempted=attempted, confirmed=confirmed, next_round=round_number + 1 if remaining and round_number < round_limit else None, selection_mode="retry_executable_after_history")
            return _selection_result(status="stopped", stop_reason="all_pending_slices_attempted_needs_new_evidence_or_policy", selected=[], pending=pending, attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="history_exhausted")
        return _selection_result(status="stopped", stop_reason="all_pending_slices_attempted_needs_new_evidence_or_policy", selected=[], pending=pending, attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="history_exhausted")
    offset = (round_number - 1) * budget
    # Prioritize slices with source-bound executable routes on first round too.
    # Without this filter, route-less slices (DB-only entities) generate
    # scenarios with empty steps → 404 false positives.
    _candidates = [item for item in pending if _slice_has_source_executable_route(item)] or pending
    selected = _take_diverse_slice_batch(_candidates[offset:], budget)
    if not selected:
        return _selection_result(status="stopped", stop_reason="no_remaining_slice_in_configured_round", selected=[], pending=pending, attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="round_paging")
    remaining = len(pending) - offset - len(selected)
    return _selection_result(status="planned", stop_reason="slice_budget_reached" if remaining else "selected_final_available_slice_batch", selected=selected, pending=pending[offset:], attempted=attempted, confirmed=confirmed, next_round=round_number + 1 if remaining and round_number < round_limit else None, selection_mode="round_paging")


def _active_policy_version() -> str:
    try:
        from .policy_registry import get_policy_registry
        return str(getattr(get_policy_registry().get_active(), "policy_version", "") or "")
    except Exception:
        return ""


def _campaign_identity_defaults(project: str, root: Path) -> dict[str, str]:
    try:
        from .enterprise_pilot_runtime import load_connector_registry

        registry = load_connector_registry(project, root)
    except Exception:
        return {}
    profile = registry.get("test_profile") if isinstance(registry, dict) else {}
    if not isinstance(profile, dict):
        return {}
    defaults: dict[str, str] = {}
    scope_id = str(
        profile.get("scope_id")
        or profile.get("deployment_scope_id")
        or profile.get("project_scope_id")
        or ""
    ).strip()
    environment_ref = str(
        profile.get("environment_ref")
        or profile.get("target_environment")
        or profile.get("environment")
        or ""
    ).strip()
    if scope_id:
        defaults["scope_id"] = scope_id[:160]
    if environment_ref:
        defaults["environment_ref"] = environment_ref[:160]
    return defaults


def _campaign_context(project: str, prd_text: str, api_spec_text: str, db_schema_text: str, base_url: str, settings: dict[str, int], context: dict[str, Any], root: Path, submitted_api_spec_text: Any) -> tuple[EnterpriseCampaign, EnterpriseCampaignStore, str]:
    policy_version = str(context.get("policy_version") or _active_policy_version())[:120]
    defaults = _campaign_identity_defaults(project, root)
    scope_id = str(
        context.get("scope_id")
        or defaults.get("scope_id")
        or f"project_scope_{hashlib.sha256(project.encode()).hexdigest()[:12]}"
    )[:160]
    environment_ref = str(
        context.get("environment_ref")
        or context.get("target_environment")
        or defaults.get("environment_ref")
        or (f"target_{hashlib.sha256(base_url.encode()).hexdigest()[:16]}" if base_url else "unbound_environment")
    )[:160]
    rerun_key = str(context.get("campaign_rerun_key") or context.get("campaign_restart_key") or "")[:120]
    rerun_reason = str(context.get("campaign_rerun_reason") or context.get("campaign_restart_reason") or "")[:240]
    snapshot = source_snapshot_hash(prd_text, api_spec_text, db_schema_text, scope_id, environment_ref)
    verification_text = context.get("_source_verification_text", submitted_api_spec_text)
    source_manifest, source_issues = _source_manifest_details(context, verification_text)
    candidate = EnterpriseCampaign.create(
        project,
        scope_id,
        environment_ref,
        snapshot,
        source_id=source_manifest["source_id"] if not source_issues else "",
        source_hash=source_manifest["source_hash"] if not source_issues else "",
        policy_version=policy_version,
        rerun_key=rerun_key,
        rerun_reason=rerun_reason,
        slice_budget=settings["slice_budget"],
        automatic_round_limit=settings["round_limit"],
    )
    store = EnterpriseCampaignStore(root, project)
    campaign, mode = store.open_or_create(candidate)
    # NOTE: the effective per-round budget / round limit are auto-scaled to the
    # discovered candidate-pool size just before scheduling. Here we only align the
    # persisted campaign ceilings with the (possibly env-overridden) starting
    # settings; the auto-scaler may raise them further at scheduling time.
    campaign.slice_budget = min(campaign.slice_budget, settings["slice_budget"])
    campaign.automatic_round_limit = min(campaign.automatic_round_limit, settings["round_limit"])
    return campaign, store, mode


def _behavior_contract_rerun_key(behavior_contract: dict[str, Any]) -> str:
    slices_payload: list[dict[str, Any]] = []
    for item in behavior_contract.get("slices", []) if isinstance(behavior_contract, dict) else []:
        row = _dict(item)
        slice_id = str(row.get("slice_id") or "").strip()
        if not slice_id:
            continue
        slices_payload.append({
            "slice_id": slice_id,
            "entity": str(row.get("entity") or "").strip(),
            "kind": str(row.get("kind") or "").strip(),
            "states": sorted(str(state or "").strip() for state in row.get("states", []) if str(state or "").strip()),
            "endpoints": sorted(str(path or "").strip() for path in row.get("endpoints", []) if str(path or "").strip()),
            "evidence_gaps": sorted(str(gap or "").strip() for gap in row.get("evidence_gaps", []) if str(gap or "").strip()),
        })
    gap_payload: list[dict[str, str]] = []
    for item in behavior_contract.get("coverage_gaps", []) if isinstance(behavior_contract, dict) else []:
        row = _dict(item)
        gap_payload.append({
            "kind": str(row.get("kind") or "").strip(),
            "title": str(row.get("title") or "").strip(),
            "entity": str(row.get("entity") or "").strip(),
            "reason": str(row.get("reason") or "").strip(),
        })
    payload = {
        "schema": "behavior_contract_rerun_v1",
        "slices": sorted(slices_payload, key=lambda item: item["slice_id"]),
        "coverage_gaps": sorted(gap_payload, key=lambda item: (item["kind"], item["entity"], item["title"], item["reason"])),
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]
    return f"behavior_contract:{digest}"


def _maybe_start_behavior_contract_rerun(
    project: str,
    prd_text: str,
    api_spec_text: str,
    db_schema_text: str,
    base_url: str,
    settings: dict[str, int],
    context: dict[str, Any],
    root: Path,
    submitted_api_spec_text: Any,
    behavior_contract: dict[str, Any],
    campaign: EnterpriseCampaign,
    campaign_store: EnterpriseCampaignStore,
    campaign_mode: str,
) -> tuple[EnterpriseCampaign, EnterpriseCampaignStore, str]:
    explicit_rerun_key = str(context.get("campaign_rerun_key") or context.get("campaign_restart_key") or "").strip()
    if explicit_rerun_key or campaign.status != "completed":
        return campaign, campaign_store, campaign_mode
    derived_rerun_key = _behavior_contract_rerun_key(behavior_contract)
    if not derived_rerun_key or campaign.rerun_key == derived_rerun_key:
        return campaign, campaign_store, campaign_mode
    rerun_context = dict(context)
    rerun_context["campaign_rerun_key"] = derived_rerun_key
    rerun_context.setdefault("campaign_rerun_reason", "re-evaluate current behavior contract")
    rerun_campaign, rerun_store, rerun_mode = _campaign_context(
        project,
        prd_text,
        api_spec_text,
        db_schema_text,
        base_url,
        settings,
        rerun_context,
        root,
        submitted_api_spec_text,
    )
    return rerun_campaign, rerun_store, rerun_mode


def _recover_stale_campaign_state(campaign: EnterpriseCampaign, slices: list[dict[str, Any]] | None = None) -> bool:
    if campaign.status != "coverage_deferred":
        return False
    deferred_reason = str(campaign.coverage_deferred_reason or "").strip()
    recoverable_reasons = {
        "all_pending_slices_attempted_needs_new_evidence_or_policy",
        "configured_round_limit_reached",
        "slice_budget_reached",
        "automatic_campaign_budget_exhausted",
        "remaining_unattempted_slices_not_source_executable",
    }
    if deferred_reason and deferred_reason not in recoverable_reasons:
        return False
    current_slice_ids = {
        str(item.get("slice_id") or "")
        for item in (slices or [])
        if isinstance(item, dict) and str(item.get("slice_id") or "")
    }
    confirmed = set(campaign.confirmation_receipts)
    unattempted_current = current_slice_ids.difference(campaign.attempted_slice_ids).difference(confirmed)
    if not current_slice_ids or not unattempted_current:
        return False
    campaign.status = "active"
    campaign.round_count = 0
    campaign.coverage_deferred_reason = ""
    campaign.next_campaign_reason = ""
    return True


def _knowledge_asset_planning_text(asset: dict[str, Any]) -> str:
    """Flatten the structured enterprise knowledge asset into a planning text
    block the behavior-graph builder can parse, so uploaded docs (business
    rules, permission matrix, historical-bug patterns) drive the test plan —
    主链 2: parsed knowledge must feed test planning, not just sit in a report.

    Fully generic: no industry/endpoint/field hardcoding — only the customer's
    own parsed statements are surfaced.
    """
    parts: list[str] = []

    rules = asset.get("rule_library") or []
    if isinstance(rules, list):
        lines = []
        for r in rules[:300]:
            if not isinstance(r, dict):
                continue
            stmt = str(r.get("statement") or r.get("expression") or "").strip()
            if stmt:
                lines.append(f"- {stmt}")
        if lines:
            parts.append("## 企业资料解析出的业务规则（驱动数据一致性/金额/库存/状态类测试）\n" + "\n".join(lines))

    perms = asset.get("permission_matrix") or []
    if isinstance(perms, list):
        lines = []
        for p in perms[:300]:
            if not isinstance(p, dict):
                continue
            # Permission entries use role/resource/actions/scope, not statement.
            role = str(p.get("role") or "").strip()
            resource = str(p.get("resource") or "").strip()
            actions = p.get("actions") or []
            scope = str(p.get("scope") or "").strip()
            if role and resource:
                action_text = ", ".join(str(a) for a in actions) if isinstance(actions, list) else str(actions)
                line = f"- {role} 对 {resource} 具有 {action_text or 'access'} 权限"
                if scope and scope not in ("unspecified", ""):
                    line += f" (scope: {scope})"
                lines.append(line)
        if lines:
            parts.append("## 企业资料解析出的权限矩阵（驱动越权/未授权访问测试）\n" + "\n".join(lines))

    risks = asset.get("risk_domains") or []
    if isinstance(risks, list):
        lines = []
        for r in risks[:300]:
            if not isinstance(r, dict):
                continue
            # Risk domain entries use title/expected/risk_type, not statement.
            title = str(r.get("title") or "").strip()
            expected = str(r.get("expected") or "").strip()
            risk_type = str(r.get("risk_type") or "").strip()
            stmt = title or expected or risk_type
            if stmt:
                lines.append(f"- {stmt}")
        if lines:
            parts.append("## 企业资料解析出的历史风险/历史Bug模式（驱动回归测试）\n" + "\n".join(lines))

    return "\n\n".join(parts)


def _normalize_executable_api_document(api_document: Any) -> tuple[str, dict[str, Any]]:
    """Compile customer API material into the one executable OpenAPI view.

    The immutable submitted document remains available separately for source
    identity and reasoning. Parser-bound runtime consumers must receive this
    normalized JSON view so a Markdown document is not accidentally parsed as
    YAML again later in the pipeline.
    """

    raw = (
        json.dumps(api_document, ensure_ascii=False, default=str)
        if isinstance(api_document, dict)
        else str(api_document or "")
    )
    if not raw.strip():
        return "", {
            "status": "missing",
            "input_format": "unknown",
            "normalized_path_count": 0,
            "normalized_operation_count": 0,
            "reason": "api_document_missing",
        }

    try:
        from .universal_api_parser import detect_format, parse_to_openapi

        input_format = detect_format(raw)
        normalized = parse_to_openapi(raw)
        if not isinstance(normalized, dict):
            normalized = {}
        paths = normalized.get("paths") if isinstance(normalized.get("paths"), dict) else {}
        normalized = {
            **normalized,
            "openapi": str(normalized.get("openapi") or "3.0.0"),
            "info": (
                normalized.get("info")
                if isinstance(normalized.get("info"), dict)
                else {"title": "normalized customer API"}
            ),
            "paths": paths,
            "components": (
                normalized.get("components")
                if isinstance(normalized.get("components"), dict)
                else {"schemas": {}}
            ),
        }
        normalized.setdefault("components", {}).setdefault("schemas", {})
        operation_count = sum(
            1
            for operations in paths.values()
            if isinstance(operations, dict)
            for method in operations
            if str(method).lower()
            in {"get", "post", "put", "patch", "delete", "head", "options"}
        )
        return json.dumps(normalized, ensure_ascii=False, default=str), {
            "status": "normalized" if paths else "degraded",
            "input_format": input_format,
            "normalized_path_count": len(paths),
            "normalized_operation_count": operation_count,
            "reason": "" if paths else "api_document_has_no_executable_paths",
        }
    except Exception as exc:
        # A malformed source must become an observable, safe empty executable
        # catalog. Keeping the original text here would merely move the same
        # parser exception into preview/scenario generation and erase execution.
        safe_empty = {
            "openapi": "3.0.0",
            "info": {"title": "unparseable customer API"},
            "paths": {},
            "components": {"schemas": {}},
        }
        return json.dumps(safe_empty, ensure_ascii=False), {
            "status": "FAILED_SAFE",
            "input_format": "unknown",
            "normalized_path_count": 0,
            "normalized_operation_count": 0,
            "reason": "api_document_parse_failed",
            "error_type": type(exc).__name__,
            "detail": str(exc)[:300],
        }


def _redacted_trace_path(value: Any) -> str:
    path = str(value or "").strip()
    if not path:
        return ""
    path = re.sub(r"^https?://[^/]+", "", path, flags=re.IGNORECASE)
    path = path.split("?", 1)[0]
    path = re.sub(r"/[0-9]+(?=/|$)", "/{id}", path)
    path = re.sub(
        r"/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}(?=/|$)",
        "/{id}",
        path,
        flags=re.IGNORECASE,
    )
    return path


def _redacted_execution_error(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "missing_runtime_path_binding" in text:
        return "missing_runtime_path_binding"
    if "precondition" in text:
        return "precondition_not_met"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "connection" in text or "unreachable" in text:
        return "connection_failure"
    if "auth" in text or "token" in text or "credential" in text:
        return "authentication_failure"
    if "validation" in text or "invalid input" in text:
        return "invalid_test_input"
    return "execution_error"


def _redacted_trace_status(step: dict[str, Any]) -> int:
    response = _dict(step.get("response"))
    try:
        return max(0, int(step.get("status") or response.get("status_code") or 0))
    except (TypeError, ValueError):
        return 0


def _execution_trace_identity(scenario: Any, trace: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(getattr(scenario, "behavior_slice_id", "") or "").strip(),
        str(trace.get("scenario_id") or getattr(scenario, "id", "") or "").strip(),
        str(trace.get("actor_role") or "").strip(),
    )


def _redacted_execution_trace_graph(
    scenario: Any,
    trace: dict[str, Any],
    *,
    discovery_round: int,
) -> dict[str, Any]:
    """Keep stage evidence for every attempt without persisting payloads."""

    steps: list[dict[str, Any]] = []
    for raw_step in trace.get("steps") or []:
        if not isinstance(raw_step, dict):
            continue
        steps.append(
            {
                "method": str(raw_step.get("method") or "").strip().upper(),
                "path": _redacted_trace_path(raw_step.get("path")),
                "action": str(raw_step.get("action") or "")[:80],
                "status": _redacted_trace_status(raw_step),
                "skipped_reason": _redacted_execution_error(raw_step.get("skipped_reason"))
                if str(raw_step.get("skipped_reason") or "").strip()
                else "",
            }
        )

    sandbox_write = _dict(trace.get("sandbox_write"))
    cleanup = _dict(sandbox_write.get("cleanup"))
    audit_records = [
        item for item in sandbox_write.get("audit_records") or []
        if isinstance(item, dict)
    ]
    execution_trace = {
        "scenario_id": str(trace.get("scenario_id") or getattr(scenario, "id", "") or ""),
        "actor_role": str(trace.get("actor_role") or "")[:80],
        "steps": steps,
        "errors": [
            _redacted_execution_error(item)
            for item in trace.get("errors") or []
            if str(item or "").strip()
        ],
        "precondition_not_met": [
            {}
            for item in trace.get("precondition_not_met") or []
            if item is not None
        ],
        "sandbox_write": {
            "status": str(sandbox_write.get("status") or ""),
            "cleanup": {
                "status": str(cleanup.get("status") or ""),
                "receipt_ref": "present" if cleanup.get("receipt_ref") else "",
            },
            "audit_path": "present" if sandbox_write.get("audit_path") else "",
            "audit_record_count": len(audit_records),
        },
    }
    return {
        "scenario": {
            "id": str(trace.get("scenario_id") or getattr(scenario, "id", "") or ""),
            "behavior_slice_id": str(
                getattr(scenario, "behavior_slice_id", "") or ""
            ),
            "discovery_round": int(discovery_round or 0),
        },
        "execution_trace": execution_trace,
        "oracle_results": [],
        "layers_triggered": [],
        "redaction_contract": {
            "request_body_persisted": False,
            "response_body_persisted": False,
            "query_string_persisted": False,
            "credentials_persisted": False,
        },
    }


def _publish_behavior_contract_snapshot(
    result: dict[str, Any],
    behavior_contract: dict[str, Any],
    slices: list[dict[str, Any]],
) -> int:
    """Expose grounded candidates before preview, scheduling, or execution.

    Later stages may fail on one customer document, fixture, or provider.  The
    already-built candidate pool must remain observable so a failed run reports
    where conversion stopped instead of collapsing the entire funnel to zero.
    """

    preserved = [dict(item) for item in slices if isinstance(item, dict)]
    summary = _dict(behavior_contract.get("summary"))
    coverage_gaps = [
        dict(item)
        for item in behavior_contract.get("coverage_gaps", [])
        if isinstance(item, dict)
    ]
    result["behavior_slices"] = preserved
    result["behavior_contract"] = {
        "summary": {**summary, "total_slices": len(preserved)},
        "coverage_gaps": coverage_gaps,
    }
    return len(preserved)


def _record_pipeline_failure(result: dict[str, Any], exc: Exception) -> None:
    """Fail safe while preserving every candidate produced before the error."""

    detail = str(exc)[:500]
    slices = [
        item for item in result.get("behavior_slices", [])
        if isinstance(item, dict)
    ]
    result["error"] = detail
    phases = result.setdefault("phases", {})
    phases["pipeline"] = {
        "status": "FAILED_SAFE",
        "reason": "pipeline_exception",
        "error_type": type(exc).__name__,
        "detail": detail,
        "preserved_slice_count": len(slices),
    }
    stage_status = result.setdefault("stage_status", {})
    if isinstance(stage_status, dict):
        stage_status["pipeline"] = "FAILED_SAFE"
    stage_failures = result.setdefault("stage_failures", [])
    failure_marker = f"pipeline:{type(exc).__name__}:{detail}"
    if isinstance(stage_failures, list) and failure_marker not in stage_failures:
        stage_failures.append(failure_marker)
    ledger = _dict(result.get("behavior_slice_ledger"))
    if slices and not ledger:
        pending_ids = [
            str(item.get("slice_id") or "")
            for item in slices
            if str(item.get("slice_id") or "").strip()
        ]
        result["behavior_slice_ledger"] = {
            "total_slices": len(slices),
            "selected_slice_ids": [],
            "attempted_slice_ids": [],
            "confirmed_slice_ids": [],
            "pending_slice_ids": pending_ids,
            "stop_reason": "pipeline_failed_before_selection",
        }
def _extract_api_operations_for_ir(api_spec_text: str) -> list[dict[str, Any]]:
    """Derive generic operation facts from API docs for Behavior IR.

    Uses existing parsers only; never hardcodes industry paths.
    """
    text = str(api_spec_text or "")
    if not text.strip():
        return []
    operations: list[dict[str, Any]] = []
    try:
        from .universal_api_parser import parse_api_document

        parsed = parse_api_document(text)
        if isinstance(parsed, dict):
            for item in parsed.get("operations") or parsed.get("endpoints") or []:
                if isinstance(item, dict):
                    operations.append(item)
    except Exception:
        operations = []
    if operations:
        return operations[:500]
    import re as _re

    cleaned: list[dict[str, Any]] = []
    for match in _re.finditer(
        r"(?im)^(?:\s*#{1,6}\s*)?(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)",
        text,
    ):
        method = match.group(1).upper()
        path = match.group(2).strip().rstrip("`").rstrip(",").rstrip(")")
        cleaned.append({
            "method": method,
            "path": path,
            "operation_id": f"{method.lower()}:{path}",
            "source_id": "api_spec_text",
            "side_effect_class": "write" if method in {"POST", "PUT", "PATCH", "DELETE"} else "read",
        })
    if not cleaned:
        # Also accept OpenAPI paths blocks via parse_to_openapi when available.
        try:
            from .universal_api_parser import parse_to_openapi

            spec = parse_to_openapi(text)
            paths = spec.get("paths") if isinstance(spec, dict) else {}
            if isinstance(paths, dict):
                for path, methods in paths.items():
                    if not isinstance(methods, dict):
                        continue
                    for method, op in methods.items():
                        m = str(method or "").upper()
                        if m not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                            continue
                        op_dict = op if isinstance(op, dict) else {}
                        cleaned.append({
                            "method": m,
                            "path": str(path),
                            "operation_id": str(op_dict.get("operationId") or f"{m.lower()}:{path}"),
                            "source_id": "api_spec_openapi",
                            "summary": str(op_dict.get("summary") or ""),
                            "description": str(op_dict.get("description") or ""),
                            "tags": list(op_dict.get("tags") or []),
                            "side_effect_class": "write" if m in {"POST", "PUT", "PATCH", "DELETE"} else "read",
                            "parameters": list(op_dict.get("parameters") or []),
                            "request_schema": op_dict.get("requestBody"),
                            "response_schema": op_dict.get("responses"),
                        })
        except Exception:
            pass
    return cleaned[:500]


def _extract_runtime_actors_for_ir(root: Path, project: str, context: dict[str, Any]) -> list[dict[str, Any]]:
    """Load declared test actors as secret_ref-only IR actors."""
    actors: list[dict[str, Any]] = []
    accounts_path = Path(root) / "platform_inputs" / str(project) / "test_accounts.json"
    payload: Any = {}
    if accounts_path.exists():
        try:
            payload = json.loads(accounts_path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError):
            payload = {}
    rows = []
    if isinstance(payload, dict):
        rows = payload.get("accounts") or payload.get("actors") or payload.get("users") or []
        if not rows and payload:
            # mapping of role -> account object
            rows = [
                {**(value if isinstance(value, dict) else {"name": key}), "account_ref": key}
                for key, value in payload.items()
                if isinstance(value, dict) and key not in {"schema", "schema_version", "meta"}
            ]
    elif isinstance(payload, list):
        rows = payload
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or row.get("name") or row.get("id") or "").strip()
        if not role:
            continue
        account_ref = str(row.get("account_ref") or row.get("email") or row.get("username") or row.get("id") or role).strip()
        actors.append({
            "role": role,
            "account_ref": account_ref,
            "tenant": row.get("tenant") or row.get("scope"),
            "secret_ref": f"secret_ref:test_accounts:{account_ref}",
            "status": str(row.get("status") or "active"),
        })
    # Context-declared actor
    scenario = _dict(context.get("runtime_scenario_contract"))
    declared = _dict(scenario.get("actor"))
    role = str(declared.get("role") or declared.get("name") or declared.get("id") or "").strip()
    if role and not any(a.get("role") == role for a in actors):
        actors.append({
            "role": role,
            "secret_ref": f"secret_ref:context:{role}",
            "status": "active",
        })
    return actors


def run_v12_pipeline(project: str, root: Path, prd_text: str = "", api_spec_text: str = "", db_schema_text: str = "", base_url: str = "", existing_findings: list[dict] | None = None, campaign_context: dict[str, Any] | None = None) -> dict[str, Any]:
    global _v12_har_entries
    _v12_har_entries = []
    started = time.time()
    context = _dict(campaign_context)
    submitted_api_spec_text = api_spec_text
    # Campaign identity fingerprints immutable source inputs, not the derived
    # knowledge-planning text appended later in this function.
    campaign_prd_text = prd_text
    runtime_contract = _runtime_contract(context, base_url, submitted_api_spec_text)
    approved_base_url = str(runtime_contract.get("approved_base_url") or "")
    result: dict[str, Any] = {
        "v12_version": "2.5",
        "enabled": True,
        "phases": {},
        "findings": [],
        "external_findings": [],
        "external_signal_execution": {},
        "ui_findings": [],
        "ui_execution": {},
        "evidence_graphs": [],
        "execution_trace_summaries": [],
        "risk_clues_saved": 0,
        "behavior_slice_ledger": {},
        "campaign": {},
        "runtime_contract": runtime_contract,
    }
    ledger_for_persistence: dict[str, Any] | None = None
    api_spec_text, api_document_normalization = _normalize_executable_api_document(api_spec_text)
    result["api_document_normalization"] = api_document_normalization
    if api_document_normalization.get("status") == "FAILED_SAFE":
        result["stage_status"] = {"api_document": "FAILED_SAFE"}
        result["stage_failures"] = [
            "api_document:"
            f"{api_document_normalization.get('error_type') or 'ParseError'}:"
            f"{api_document_normalization.get('detail') or api_document_normalization.get('reason')}"
        ]
    try:
        graph_started = time.time()
        from .business_state_graph import BusinessStateGraphBuilder
        # 主链 2: fold the structured enterprise knowledge asset (parsed from the
        # customer's uploaded docs) into the planning text so business rules,
        # permission boundaries and historical-bug patterns drive the live test
        # plan. Additive only — when no asset exists the pipeline is unchanged.
        _permission_matrix: list[dict[str, Any]] = []
        _behavior_ir: dict[str, Any] = {}
        _obligation_compile: dict[str, Any] = {}
        _experiment_compile: dict[str, Any] = {}
        _obligation_plan: dict[str, Any] = {}
        _adapter_matrix: dict[str, Any] = {}
        try:
            from .enterprise_knowledge_center import (
                build_enterprise_business_knowledge_asset,
            )
            # Rebuild from the current declared project inputs so a stale or
            # reset registry cannot silently disconnect enterprise materials
            # from discovery planning.
            _asset = build_enterprise_business_knowledge_asset(project, root)
            if _asset:
                _parser_receipts = [
                    dict(item) for item in (_asset.get("parser_receipts") or [])
                    if isinstance(item, dict)
                ]
                _parse_gaps = [
                    dict(item) for item in (_asset.get("coverage_gaps") or [])
                    if isinstance(item, dict) and str(item.get("kind") or "").startswith("SOURCE_PARSE_")
                ]
                result["phases"]["knowledge_asset"] = {
                    "status": "degraded" if _parse_gaps else "completed",
                    "input_count": len(_parser_receipts),
                    "output_count": sum(1 for item in _parser_receipts if str(item.get("parser_status") or "") == "parsed"),
                    "lost_count": len(_parse_gaps),
                    "duration_ms": int((time.time() - graph_started) * 1000),
                    "error_codes": sorted({
                        str(error.get("code") or "SOURCE_PARSE_FAILED")
                        for item in _parser_receipts
                        for error in (item.get("errors") or [])
                        if isinstance(error, dict)
                    }),
                    "identity_samples": [str(item.get("receipt_id") or "") for item in _parser_receipts[:5]],
                    "operator_action": "inspect parser receipts and replace only damaged source versions" if _parse_gaps else "none",
                    "parser_receipts": _parser_receipts,
                    "coverage_gaps": _parse_gaps,
                }
                if _parse_gaps:
                    result.setdefault("coverage_gaps", []).extend(_parse_gaps)
                _permission_matrix = [
                    dict(item)
                    for item in (_asset.get("permission_matrix") or [])
                    if isinstance(item, dict)
                ]
                _enrich = _knowledge_asset_planning_text(_asset)
                if _enrich:
                    prd_text = (prd_text or "") + "\n\n" + _enrich
                # Phase 1–4 vertical slice: structured Behavior IR → obligations →
                # experiments → adaptive plan on the real V12 main chain.
                try:
                    from .behavior_ir import behavior_ir_summary, build_behavior_ir_from_knowledge_asset
                    from .obligation_compiler import compile_obligations_from_behavior_ir
                    from .experiment_compiler import compile_experiments
                    from .adaptive_discovery_planner import plan_obligation_round
                    from .execution_adapter import build_adapter_capability_matrix
                    from .policy_registry import get_policy_registry

                    _env_type = str(
                        _dict(context.get("runtime") or {}).get("environment_type")
                        or context.get("environment_kind")
                        or context.get("environment_type")
                        or ""
                    ).strip().lower()
                    # Missing environment must fail closed — never default to "test".
                    _behavior_ir = build_behavior_ir_from_knowledge_asset(
                        _asset,
                        project_id=project,
                        source_snapshot_hash=str(
                            _dict(context.get("source_manifest")).get("source_hash") or ""
                        ),
                        api_operations=_extract_api_operations_for_ir(
                            submitted_api_spec_text or api_spec_text
                        ),
                        runtime_actors=_extract_runtime_actors_for_ir(root, project, context),
                    )
                    _obligation_compile = compile_obligations_from_behavior_ir(_behavior_ir)
                    _experiment_compile = compile_experiments(
                        list(_obligation_compile.get("obligations") or []),
                        behavior_ir=_behavior_ir,
                        environment_type=_env_type,
                        policy_version=str(getattr(get_policy_registry().get_active(), "policy_version", "") or ""),
                    )
                    from .fixture_dag import attach_fixture_dag_to_experiments

                    _experiment_compile = attach_fixture_dag_to_experiments(
                        _experiment_compile,
                        behavior_ir=_behavior_ir,
                    )
                    _exp_by_obl = {
                        str(item.get("obligation_id") or ""): item
                        for item in list(_experiment_compile.get("experiments") or [])
                        if isinstance(item, dict)
                    }
                    _obligation_plan = plan_obligation_round(
                        list(_obligation_compile.get("obligations") or []),
                        experiments_by_obligation=_exp_by_obl,
                        budget=int(_behavior_slice_settings().get("slice_budget") or 20),
                    )
                    _adapter_matrix = build_adapter_capability_matrix(
                        ui_available=False,
                        db_available=bool(str((__import__("os").environ.get("QUALIBUG_DB_DSN") or "")).strip()),
                        log_available=False,
                    )
                    # Selected experiment → fixture → governed HTTP → observers →
                    # typed assertions → contract oracle (every item gets a receipt).
                    from .experiment_executor import execute_selected_experiments

                    _experiment_execution = execute_selected_experiments(
                        list(_obligation_plan.get("selected") or []),
                        experiments_by_obligation=_exp_by_obl,
                        behavior_ir=_behavior_ir,
                        root=root,
                        project=project,
                        base_url=approved_base_url,
                        runtime_contract=runtime_contract,
                        campaign_id=str(_dict(context).get("campaign_id") or ""),
                    )
                    for _finding in list(_experiment_execution.get("findings") or []):
                        if isinstance(_finding, dict):
                            result["findings"].append(_finding)
                    result["behavior_ir"] = {
                        "summary": behavior_ir_summary(_behavior_ir),
                        "model_id": _behavior_ir.get("model_id"),
                        "schema_version": _behavior_ir.get("schema_version"),
                        "coverage_gaps": list(_behavior_ir.get("coverage_gaps") or [])[:50],
                    }
                    result["test_obligations"] = {
                        "count": int(_obligation_compile.get("obligation_count") or 0),
                        "by_family": dict(_obligation_compile.get("by_family") or {}),
                        "obligations": list(_obligation_compile.get("obligations") or [])[:200],
                    }
                    result["experiment_compile"] = {
                        "compiled_count": int(_experiment_compile.get("compiled_count") or 0),
                        "blocked_count": int(_experiment_compile.get("blocked_count") or 0),
                        "block_reason_counts": dict(_experiment_compile.get("block_reason_counts") or {}),
                        "experiments": list(_experiment_compile.get("experiments") or [])[:100],
                        "blocked_experiments": list(_experiment_compile.get("blocked_experiments") or [])[:100],
                    }
                    result["obligation_plan"] = _obligation_plan
                    result["experiment_execution"] = {
                        "selected_count": int(_experiment_execution.get("selected_count") or 0),
                        "executed_count": int(_experiment_execution.get("executed_count") or 0),
                        "blocked_count": int(_experiment_execution.get("blocked_count") or 0),
                        "harness_failure_count": int(_experiment_execution.get("harness_failure_count") or 0),
                        "cleanup_failures": int(_experiment_execution.get("cleanup_failures") or 0),
                        "every_experiment_has_receipt": bool(
                            _experiment_execution.get("every_experiment_has_receipt")
                        ),
                        "results": list(_experiment_execution.get("results") or [])[:100],
                    }
                    result["execution_adapters"] = _adapter_matrix
                    result["phases"]["behavior_ir"] = {
                        "status": "completed",
                        "model_id": _behavior_ir.get("model_id"),
                        "obligation_count": int(_obligation_compile.get("obligation_count") or 0),
                        "compiled_experiments": int(_experiment_compile.get("compiled_count") or 0),
                        "blocked_experiments": int(_experiment_compile.get("blocked_count") or 0),
                        "experiment_executed": int(_experiment_execution.get("executed_count") or 0),
                        "experiment_blocked": int(_experiment_execution.get("blocked_count") or 0),
                        "environment_type": _env_type or "MISSING",
                    }
                except Exception as _ir_exc:
                    import sys as _sys
                    _sys.stderr.write(f"[v12_behavior_ir] project={project} compile failed: {_ir_exc}\n")
                    _sys.stderr.flush()
                    result["phases"]["behavior_ir"] = {
                        "status": "FAILED_SAFE",
                        "error": f"{type(_ir_exc).__name__}: {str(_ir_exc)[:200]}",
                    }
        except Exception as _ka_exc:
            import sys as _sys
            _sys.stderr.write(f"[v12_knowledge_asset] project={project} enrichment failed: {_ka_exc}\n")
            _sys.stderr.flush()
            result["phases"]["knowledge_asset"] = {
                "status": "FAILED",
                "error": f"{type(_ka_exc).__name__}: {str(_ka_exc)[:500]}",
            }
            raise RuntimeError(f"enterprise knowledge asset unavailable for project {project}") from _ka_exc
        builder = BusinessStateGraphBuilder()
        graph_api_doc = submitted_api_spec_text if str(submitted_api_spec_text or "").strip() else api_spec_text
        graphs = builder.build(prd_text, graph_api_doc, db_schema_text)
        behavior_contract = builder.behavior_contract()
        settings = _behavior_slice_settings()
        campaign, campaign_store, campaign_mode = _campaign_context(project, campaign_prd_text, api_spec_text, db_schema_text, approved_base_url, settings, context, root, submitted_api_spec_text)
        campaign, campaign_store, campaign_mode = _maybe_start_behavior_contract_rerun(
            project,
            campaign_prd_text,
            api_spec_text,
            db_schema_text,
            approved_base_url,
            settings,
            context,
            root,
            submitted_api_spec_text,
            behavior_contract,
            campaign,
            campaign_store,
            campaign_mode,
        )
        recovered_stale_campaign = _recover_stale_campaign_state(campaign, behavior_contract["slices"])
        approval = _execution_approval_contract(context, campaign, approved_base_url, root)
        if approved_base_url and approval.get("status") != "approved":
            runtime_contract = {
                **runtime_contract,
                "status": "blocked",
                "reason": "execution_approval_required",
                "missing_requirements": [str(approval.get("code") or "EXECUTION_APPROVAL_MISSING")],
                "approved_base_url": "",
                "execution_approval": approval,
            }
            approved_base_url = ""
        else:
            scenario_contract = _dict(context.get("runtime_scenario_contract"))
            declared_actor = _dict(scenario_contract.get("actor"))
            runtime_contract = {
                **runtime_contract,
                "execution_approval": approval,
                "execution_mode": str(approval.get("execution_mode") or context.get("execution_mode") or "safe_read_only"),
                "actor_identity": str(
                    declared_actor.get("id")
                    or declared_actor.get("name")
                    or scenario_contract.get("actor_id")
                    or ""
                ).strip(),
            }
        result["runtime_contract"] = runtime_contract
        ranked_behavior_slices = list(behavior_contract["slices"])
        # ── Supplementary coverage: inject actor-aware / data-isolation /
        # concurrency / financial-integrity slices that the state-machine builder
        # cannot express, so oracles beyond idempotency + state + invariant get
        # runtime evidence against the live target.  Config-driven, no per-project
        # hardcoding — the endpoint catalog comes from _api_facts, actors from
        # test_accounts.json (or test_accounts.md fallback).
        try:
            from .supplementary_behavior_slices import generate_supplementary_slices

            graph_api_doc = submitted_api_spec_text if str(submitted_api_spec_text or "").strip() else api_spec_text
            try:
                from .api_doc_assets import enrich_api_spec_text

                _enriched_doc = enrich_api_spec_text(root, project, graph_api_doc)
                if str(_enriched_doc or "").strip():
                    graph_api_doc = _enriched_doc
            except Exception:
                pass
            supp = generate_supplementary_slices(
                root,
                project,
                graph_api_doc,
                permission_matrix=_permission_matrix,
            )
            if supp:
                ranked_behavior_slices = list(ranked_behavior_slices) + supp
                behavior_contract["slices"] = ranked_behavior_slices
                behavior_contract["summary"]["total_slices"] = len(ranked_behavior_slices)
                behavior_contract["summary"]["supplementary_slices"] = len(supp)
        except Exception as _supp_exc:
            result["phases"]["supplementary_behavior_slices"] = {
                "status": "FAILED",
                "error": f"{type(_supp_exc).__name__}: {str(_supp_exc)[:500]}",
            }
            raise RuntimeError("source-grounded supplementary behavior generation failed") from _supp_exc
        # ── 主链统一: 分析器 + LLM Reasoner 候选并入同一执行队列 ──
        # 加法、可开关、源绑定；关闭时行为与现状一致。
        try:
            import re as _re_unify

            from .business_state_graph import _api_facts
            from .hypothesis_slice_bridge import hypotheses_to_slices

            graph_api_doc = submitted_api_spec_text if str(submitted_api_spec_text or "").strip() else api_spec_text
            try:
                from .api_doc_assets import enrich_api_spec_text

                _enriched_doc = enrich_api_spec_text(root, project, graph_api_doc)
                if str(_enriched_doc or "").strip():
                    graph_api_doc = _enriched_doc
            except Exception:
                pass
            _state_re = _re_unify.compile(
                r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])",
                _re_unify.I,
            )
            _entities, _states, _endpoints = _api_facts(graph_api_doc, _state_re)
            try:
                from .system_behavior_space import _merge_api_endpoints, _openapi_route_facts

                _openapi_paths = [
                    root / "platform_inputs" / project / "openapi.json",
                    root / "platform_inputs" / project / "swagger.json",
                    root / "platform_workspace" / project / "input" / "openapi.json",
                ]
                for _openapi_file in _openapi_paths:
                    if _openapi_file.is_file():
                        _extra = _openapi_route_facts(_openapi_file.read_text(encoding="utf-8", errors="replace"))
                        if _extra:
                            _endpoints = _merge_api_endpoints(_endpoints, _extra)
                        break
            except Exception:
                pass
            _unify_stats: dict[str, Any] = {}

            if os.environ.get("QUALIBUG_UNIFY_ANALYZERS", "1") == "1":
                from .analyzers_adapter import build_analyzer_hypotheses

                _ana = build_analyzer_hypotheses(prd_text, graph_api_doc)
                _ana_flat = [h for hs in (_ana or {}).values() for h in hs if isinstance(h, dict)]
                _ana_slices, _ana_funnel = hypotheses_to_slices(
                    _ana_flat, api_endpoints=_endpoints, origin="analyzer",
                )
                ranked_behavior_slices = list(ranked_behavior_slices) + _ana_slices
                _unify_stats["analyzer"] = _ana_funnel

            if os.environ.get("QUALIBUG_UNIFY_LLM_REASONER", "0") == "1":
                from .stage_reason_all_v2 import collect_reasoner_hypotheses

                _llm_hyps, _llm_meta = collect_reasoner_hypotheses(prd_text, graph_api_doc)
                if str(_llm_meta.get("status") or "") == "provider_unavailable":
                    _unify_stats["llm_reasoner"] = {
                        "status": "provider_unavailable",
                        "input": 0,
                        "bound": 0,
                        "dropped_no_endpoint": 0,
                        "reason": str(_llm_meta.get("reason") or "llm_not_configured"),
                    }
                else:
                    _llm_slices, _llm_funnel = hypotheses_to_slices(
                        _llm_hyps, api_endpoints=_endpoints, origin="llm_reasoner",
                    )
                    ranked_behavior_slices = list(ranked_behavior_slices) + _llm_slices
                    _unify_stats["llm_reasoner"] = {**_llm_funnel, **{k: v for k, v in _llm_meta.items() if k not in _llm_funnel}}

            if _unify_stats:
                behavior_contract["slices"] = ranked_behavior_slices
                behavior_contract["summary"]["total_slices"] = len(ranked_behavior_slices)
                behavior_contract["summary"]["unified_slices"] = sum(
                    int(f.get("bound") or 0) for f in _unify_stats.values() if isinstance(f, dict)
                )
                result["mainline_unification"] = _unify_stats
        except Exception as exc:
            result.setdefault("mainline_unification", {})["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        _optimized_slices, _pool_opt = _optimize_behavior_slice_pool(
            [item for item in ranked_behavior_slices if isinstance(item, dict)]
        )
        ranked_behavior_slices = _optimized_slices
        behavior_contract["slices"] = _optimized_slices
        behavior_contract["summary"]["total_slices"] = len(_optimized_slices)
        if _pool_opt.get("collapsed_permission_isolation") or _pool_opt.get("collapsed_llm_invariant"):
            behavior_contract["summary"]["pool_optimization"] = _pool_opt
            result["behavior_slice_pool_optimization"] = _pool_opt
        # Persist the grounded pool before any preview/fixture/execution work.
        # This is the diagnostic hand-off point from reasoning to execution.
        _publish_behavior_contract_snapshot(
            result,
            behavior_contract,
            ranked_behavior_slices,
        )
        if runtime_contract.get("status") == "approved":
            from .semantic_scenario_generator import SemanticScenarioGenerator

            # Scenario generation is parser-bound. Always use the normalized
            # executable view; the raw submitted document is retained only for
            # source identity and semantic reasoning.
            preview_api_doc = api_spec_text
            preview_scenarios = SemanticScenarioGenerator().generate(
                graphs,
                preview_api_doc,
                active_slice_ids=None,
                active_slices=behavior_contract["slices"],
                discovery_round=settings["round_number"],
                allow_source_runtime=True,
                root=root,
                project=project,
            )
            ranked_behavior_slices = _rank_behavior_slices_for_selection(behavior_contract["slices"], preview_scenarios)
            behavior_contract["slices"] = ranked_behavior_slices
            _publish_behavior_contract_snapshot(
                result,
                behavior_contract,
                ranked_behavior_slices,
            )
        if campaign.status in {"coverage_deferred", "completed", "blocked"}:
            selection = _selection_result(status="stopped", stop_reason=f"campaign_{campaign.status}", selected=[], pending=ranked_behavior_slices, attempted=set(campaign.attempted_slice_ids), confirmed=set(campaign.confirmation_receipts), next_round=None, selection_mode="campaign_terminal")
        else:
            history: list[dict[str, Any]] = [campaign.history_item()]
            if existing_findings is not None:
                history.extend(item for item in existing_findings if isinstance(item, dict))
            elif not campaign.attempted_slice_ids and not recovered_stale_campaign:
                history.extend(_load_persisted_slice_history(root, project, campaign.source_snapshot_hash, campaign.source_hash))
            if "QUALIBUG_DISCOVERY_ROUND" not in os.environ and campaign.round_count:
                settings["round_number"] = min(campaign.round_count + 1, campaign.automatic_round_limit + 1)
            # ── Auto-scale per-round budget / round limit to the real system scale ──
            # The candidate pool (state graph + supplementary + analyzer + LLM
            # reasoner slices) directly reflects how big the customer's system is.
            # Size the per-round batch to drain it in a few rounds so large
            # enterprises work out of the box — no env tuning. An explicit env
            # value still wins for power users.
            _executable_pool = [
                s for s in ranked_behavior_slices
                if isinstance(s, dict) and str(s.get("slice_id") or "") and _slice_has_source_executable_route(s)
            ]
            _pool_size = len(_executable_pool) or len([
                s for s in ranked_behavior_slices if isinstance(s, dict) and str(s.get("slice_id") or "")
            ])
            if "QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND" not in os.environ:
                _auto_budget = _auto_scale_slice_budget(_pool_size)
                settings["slice_budget"] = _auto_budget
                campaign.slice_budget = _auto_budget
            if "QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT" not in os.environ:
                _auto_rounds = _auto_scale_round_limit(_pool_size, settings["slice_budget"])
                settings["round_limit"] = _auto_rounds
                campaign.automatic_round_limit = _auto_rounds
            settings["slice_budget"] = min(settings["slice_budget"], campaign.slice_budget)
            settings["round_limit"] = min(settings["round_limit"], campaign.automatic_round_limit)
            result["auto_scale"] = {
                "executable_pool_size": _pool_size,
                "slice_budget": settings["slice_budget"],
                "round_limit": settings["round_limit"],
                "source": "explicit_env" if "QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND" in os.environ else "auto_scaled_to_system_size",
            }
            selection = _schedule_behavior_slices(ranked_behavior_slices, settings, history)
        selected_ids = set(selection["selected_slice_ids"])
        result["campaign"] = {**campaign.public_contract(), "campaign_mode": campaign_mode}
        # Behavior-IR experiments are compiled before campaign scheduling, so
        # bind the final campaign identity onto every execution/evidence/finding
        # receipt once the authoritative campaign exists. No selected item may
        # disappear from the continuity ledger with an empty campaign_id.
        _experiment_results = _dict(result.get("experiment_execution")).get("results")
        if isinstance(_experiment_results, list):
            for _execution_row in _experiment_results:
                if not isinstance(_execution_row, dict):
                    continue
                _execution_row["campaign_id"] = campaign.campaign_id
                _execution_receipt = _execution_row.get("execution_receipt")
                if isinstance(_execution_receipt, dict):
                    _execution_receipt["campaign_id"] = campaign.campaign_id
                _execution_finding = _execution_row.get("finding")
                if isinstance(_execution_finding, dict):
                    _execution_finding["campaign_id"] = campaign.campaign_id
        for _result_finding in result.get("findings") or []:
            if isinstance(_result_finding, dict) and _result_finding.get("execution_id"):
                _result_finding["campaign_id"] = campaign.campaign_id
        result["behavior_slice_ledger"] = {
            "campaign_id": campaign.campaign_id,
            "campaign_status": campaign.status,
            "scope_id": campaign.scope_id,
            "source_snapshot_hash": campaign.source_snapshot_hash,
            "source_id": campaign.source_id,
            "source_hash": campaign.source_hash,
            "project": project,
            "round": settings["round_number"],
            "round_limit": settings["round_limit"],
            "slice_budget": settings["slice_budget"],
            "selection_mode": selection["selection_mode"],
            "selected_slice_ids": selection["selected_slice_ids"],
            "attempted_slice_ids": selection["attempted_slice_ids"],
            "confirmed_slice_ids": selection["confirmed_slice_ids"],
            # 主链 4: surface the per-task status map in the scan result so the
            # API/frontend can render real task states, not just id sets.
            "slice_status": _derive_slice_status(
                selection["attempted_slice_ids"],
                selection["confirmed_slice_ids"],
                campaign.status,
            ),
            "next_round": selection["next_round"],
            "stop_reason": selection["stop_reason"],
        }
        skip_history_persistence = (
            str(runtime_contract.get("status") or "") == "blocked"
            and str(runtime_contract.get("reason") or "") == "execution_approval_required"
        )
        ledger_for_persistence = None if skip_history_persistence else dict(result["behavior_slice_ledger"])
        # 主链 8: surface the planned test-task slices (each carrying its lifecycle
        # `status` from 主链 4) so the frontend "测试任务看板" can render the full
        # task board, not just the id-only ledger. Backend is the single source of
        # truth; the frontend renders this verbatim (zero transform).
        result["behavior_slices"] = list(behavior_contract.get("slices", []) or [])
        result["phases"]["state_graph"] = {
            "status": "completed",
            "entities": sorted(graphs),
            "summary": {name: graph.to_dict()["stats"] for name, graph in graphs.items()},
            "behavior_slices": behavior_contract["summary"],
            "coverage_gaps": behavior_contract["coverage_gaps"],
            "duration_ms": int((time.time() - graph_started) * 1000),
        }
        result["phases"]["incremental_discovery"] = {
            "status": selection["status"],
            "round": settings["round_number"],
            "round_limit": settings["round_limit"],
            "slice_budget": settings["slice_budget"],
            "selection_mode": selection["selection_mode"],
            "selected_slices": selection["selected"],
            "remaining_slice_count": selection["remaining_slice_count"],
            "next_round": selection["next_round"],
            "stop_reason": selection["stop_reason"],
            "campaign_id": campaign.campaign_id,
            "campaign_status": campaign.status,
        }
        selected_paths = {str(path) for item in selection["selected"] for path in item.get("endpoints", []) if str(path)}
        attempted_slice_ids: set[str] = set()
        catalog: list[dict[str, Any]] = []
        if approved_base_url and (api_spec_text or submitted_api_spec_text):
            try:
                from .api_doc_assets import enrich_api_spec_text
                from .route_catalog_builder import RouteCatalogBuilder

                # Merge OpenAPI + Markdown API materials so sandbox cleanup can
                # see compensating routes (e.g. POST …/{id}/cancel) that a
                # partial openapi.json alone may omit.
                catalog_source = str(submitted_api_spec_text or api_spec_text or "")
                try:
                    catalog_source = enrich_api_spec_text(root, project, catalog_source) or catalog_source
                except Exception:
                    pass
                catalog = [
                    entry.to_dict()
                    for entry in RouteCatalogBuilder().build(
                        catalog_source,
                        str(api_spec_text or ""),
                        str(submitted_api_spec_text or ""),
                    )
                ]
            except Exception:
                catalog = []
        fuzz_started = time.time()
        fuzzer_findings: list[dict[str, Any]] = []
        fuzzer_execution_receipts: list[dict[str, Any]] = []
        fuzzer_error = ""
        if approved_base_url and selected_paths and selection["status"] == "planned":
            try:
                from .parameter_fuzzer import ParameterFuzzer
                scoped_catalog = [entry for entry in catalog if str(entry.get("path") or "") in selected_paths]
                fuzzer = ParameterFuzzer(approved_base_url, allow_write=False)
                _login_parameter_fuzzer(fuzzer, catalog, project, root, api_doc=api_spec_text)
                fuzzer_findings = fuzzer.fuzz_all(scoped_catalog, max_variants=6)
                fuzzer_execution_receipts = list(fuzzer.execution_receipts)
                fuzzer_receipt_paths = {
                    str(receipt.get("path") or "")
                    for receipt in fuzzer_execution_receipts
                    if isinstance(receipt, dict) and int(receipt.get("status") or 0) > 0
                }
                attempted_slice_ids.update(
                    str(item.get("slice_id") or "")
                    for item in selection["selected"]
                    if any(str(path) in fuzzer_receipt_paths for path in item.get("endpoints", []))
                )
                for finding in fuzzer_findings:
                    if isinstance(finding, dict):
                        matching = next((item for item in selection["selected"] if str(finding.get("path") or "") in item.get("endpoints", [])), None)
                        if matching:
                            finding.update({"behavior_slice_id": matching["slice_id"], "discovery_round": settings["round_number"], "campaign_id": campaign.campaign_id, "execution_status": "executed", "confirmation_status": "candidate"})
            except Exception as exc:
                fuzzer_error = f"parameter_fuzzer_failed:{type(exc).__name__}:{str(exc)[:300]}"
                logger.exception(
                    "parameter fuzzer failed campaign=%s selected_path_count=%s",
                    campaign.campaign_id,
                    len(selected_paths),
                )
        result["findings"].extend(fuzzer_findings)
        fuzzer_reason = fuzzer_error or (
            "selected_source_bound_read_routes_only"
            if selected_paths and approved_base_url
            else (runtime_contract.get("reason") or "no_selected_source_bound_read_routes")
        )
        result["phases"]["parameter_fuzzer"] = {
            "status": (
                "failed"
                if fuzzer_error
                else ("completed" if approved_base_url and selected_paths and selection["status"] == "planned" else "skipped")
            ),
            "reason": fuzzer_reason,
            "findings": len(fuzzer_findings),
            "execution_receipts": len([
                receipt
                for receipt in fuzzer_execution_receipts
                if isinstance(receipt, dict) and int(receipt.get("status") or 0) > 0
            ]),
            "execution_policy": "documented_read_only_only",
            "slice_scoped": True,
            "duration_ms": int((time.time() - fuzz_started) * 1000),
        }
        scenario_started = time.time()
        scenario_api_doc = api_spec_text
        from .semantic_scenario_generator import SemanticScenarioGenerator
        scenarios = SemanticScenarioGenerator().generate(
            graphs,
            scenario_api_doc,
            active_slice_ids=selected_ids,
            active_slices=selection["selected"],
            discovery_round=settings["round_number"],
            allow_source_runtime=runtime_contract.get("status") == "approved",
            root=root,
            project=project,
        )
        _enrich_coupon_validation_scenarios(scenarios, _runtime_db_dsn(project, root, db_schema_text))
        executable = [scenario for scenario in scenarios if _scenario_executable(scenario)]
        runtime_token = _read_only_runtime_token(approved_base_url, catalog, project, root, api_doc=scenario_api_doc) if executable else ""
        if runtime_token:
            for scenario in executable:
                if str(getattr(scenario, "execution_policy", "") or "") in {"safe_read_only", "approved_test_write", "approved_sandbox_write"} and not str(getattr(scenario, "actor_token", "") or ""):
                    scenario.actor_token = runtime_token
        plan_only = [scenario for scenario in scenarios if scenario not in executable]
        result["phases"]["scenario_generation"] = {
            "status": "completed" if selection["status"] == "planned" else "stopped",
            "total_scenarios": len(scenarios),
            "executable_scenarios": len(executable),
            "plan_only_scenarios": len(plan_only),
            "by_category": _count_by(scenarios, "category"),
            "by_severity": _count_by(scenarios, "severity"),
            "forbidden_paths": sum(1 for scenario in scenarios if getattr(scenario, "is_forbidden_path", False)),
            "selected_slice_ids": selection["selected_slice_ids"],
            "duration_ms": int((time.time() - scenario_started) * 1000),
        }
        result["plan_only_scenarios"] = [scenario.to_dict() for scenario in plan_only]
        # ``traces`` feeds the oracle and therefore contains only real HTTP
        # receipts. ``attempted_traces`` is the complete execution ledger,
        # including governed blocks and precondition failures.  Keeping the two
        # separate prevents an unexecuted scenario from becoming a fake Bug,
        # while still making every lost execution observable.
        traces: list[tuple[Any, dict[str, Any]]] = []
        attempted_traces: list[tuple[Any, dict[str, Any]]] = []
        if base_url and runtime_contract.get("status") == "blocked" and "SOURCE_PROVENANCE_MISSING" not in set(runtime_contract.get("missing_requirements") or []):
            result["phases"]["execution"] = {"status": "blocked", "reason": str(runtime_contract.get("reason") or "runtime_contract_missing"), "missing_requirements": runtime_contract.get("missing_requirements", []), "planned_only": len(plan_only), "executed": 0}
        elif selection["status"] != "planned":
            result["phases"]["execution"] = {"status": "stopped", "reason": selection["stop_reason"], "planned_only": 0, "executed": 0}
        elif (
            base_url
            and not approved_base_url
            and "SOURCE_PROVENANCE_MISSING" in set(runtime_contract.get("missing_requirements") or [])
            and str(base_url).startswith(("http://127.0.0.1", "http://localhost"))
        ):
            result["phases"]["execution"] = {"status": "plan_only", "reason": "source_manifest_required_before_runtime", "missing_requirements": runtime_contract.get("missing_requirements", []), "planned_only": len(plan_only), "executed": 0}
        elif base_url and not approved_base_url:
            result["phases"]["execution"] = {"status": "blocked", "reason": str(runtime_contract.get("reason") or "runtime_contract_missing"), "missing_requirements": runtime_contract.get("missing_requirements", []), "planned_only": len(plan_only), "executed": 0}
        elif not approved_base_url:
            result["phases"]["execution"] = {"status": "skipped", "reason": "no_base_url", "planned_only": len(plan_only), "executed": 0}
        elif not executable:
            result["phases"]["execution"] = {"status": "plan_only", "reason": "fixture_actor_cleanup_contract_required", "planned_only": len(plan_only), "executed": 0}
        else:
            # 主链 5 × 主链 1: load the production-data safety boundary once and
            # enforce it on every real request the executor fires. Single source
            # of truth with grounded_probe_executor (match_production_data_exclusion).
            _safety_boundary = _load_execution_safety_boundary(project, root)
            execution_started, failed = time.time(), 0
            # ── Multi-role token collection ──
            # Collect all available role tokens so each scenario can be executed
            # from multiple actor perspectives, catching cross-role auth bugs.
            _role_tokens: dict[str, str] = {}
            _account_statuses: dict[str, str] = {}
            _execution_observability: list[dict[str, Any]] = []
            _accounts_path = root / "platform_inputs" / project / "test_accounts.json"
            try:
                if not _accounts_path.exists():
                    _execution_observability.append({
                        "kind": "multi_role_accounts",
                        "status": "missing",
                        "reason": "test_accounts_json_missing",
                        "path": str(_accounts_path),
                    })
                else:
                    _accounts = json.loads(_accounts_path.read_text(encoding="utf-8"))
                    if isinstance(_accounts, dict):
                        for _name, _acc in _accounts.items():
                            if isinstance(_acc, dict):
                                _tok = str(_acc.get("token") or "").strip()
                                _role = str(_acc.get("role") or _name).strip()
                                _status = str(_acc.get("status") or "").strip().upper()
                                if _tok and not _tok.startswith("ERROR") and _role:
                                    _role_key = f"{_role}:{_name}"
                                    _role_tokens[_role_key] = _tok
                                    if _status:
                                        _account_statuses[_role_key] = _status
                    if not _role_tokens:
                        _execution_observability.append({
                            "kind": "multi_role_accounts",
                            "status": "empty",
                            "reason": "test_accounts_json_has_no_usable_tokens",
                            "path": str(_accounts_path),
                        })
                    else:
                        _execution_observability.append({
                            "kind": "multi_role_accounts",
                            "status": "ok",
                            "roles": sorted(_role_tokens.keys()),
                            "path": str(_accounts_path),
                        })
            except Exception as exc:
                _execution_observability.append({
                    "kind": "multi_role_accounts",
                    "status": "failed",
                    "reason": str(exc)[:300],
                    "path": str(_accounts_path),
                })
                logger.warning("multi-role account load failed for %s: %s", project, exc)
            if not _role_tokens and runtime_token:
                _role_tokens["default"] = runtime_token
                _execution_observability.append({
                    "kind": "multi_role_accounts",
                    "status": "degraded_default_token",
                    "reason": "fallback_to_single_runtime_token",
                })
            try:
                from .supplementary_behavior_slices import probe_disabled_account_logins

                _probe_api_doc = api_spec_text
                for _login_finding in probe_disabled_account_logins(
                    root,
                    project,
                    _probe_api_doc,
                    approved_base_url,
                    campaign_id=str(campaign.campaign_id or ""),
                    discovery_round=int(settings["round_number"]),
                ):
                    result["findings"].append(_login_finding)
                    _probe_sid = str(_login_finding.get("behavior_slice_id") or "").strip()
                    if _probe_sid:
                        attempted_slice_ids.add(_probe_sid)
                _execution_observability.append({
                    "kind": "disabled_account_login_probe",
                    "status": "ok",
                })
            except Exception as exc:
                _execution_observability.append({
                    "kind": "disabled_account_login_probe",
                    "status": "failed",
                    "reason": str(exc)[:300],
                })
                logger.warning("disabled-account login probe failed for %s: %s", project, exc)
            _disabled_finding_keys: set[tuple[str, str, str]] = set()
            for scenario in executable:
                _orig_token = getattr(scenario, "actor_token", "") or ""
                _slice_kind = str(getattr(scenario, "behavior_slice_kind", "") or "").strip().lower()
                _scenario_category = str(getattr(scenario, "category", "") or "").strip().lower()
                _is_account_status = _slice_kind == "account_status"
                _scenario_has_login = any(
                    str(getattr(step, "action", "") or "").strip().lower() == "login"
                    for step in (getattr(scenario, "steps", []) or [])
                )
                _multi_role_kinds = {"permission", "isolation"}
                _role_iter: list[tuple[str, str]]
                if _is_account_status:
                    _role_iter = [("account_status_probe", "")]
                elif _scenario_has_login:
                    _role_iter = [("scenario_native", "")]
                elif _slice_kind in _multi_role_kinds or _scenario_category in _multi_role_kinds:
                    _role_iter = list(_role_tokens.items()) or [("default", "")]
                elif _role_tokens:
                    _default_role = next(
                        (
                            item for item in _role_tokens.items()
                            if _account_statuses.get(item[0], "") not in ("DISABLED", "LOCKED")
                        ),
                        next(iter(_role_tokens.items())),
                    )
                    _role_iter = [_default_role]
                else:
                    _role_iter = [("default", "")]
                for _role, _token in _role_iter:
                    execution_mode = str(runtime_contract.get("execution_mode") or "safe_read_only").strip().lower()
                    scenario_actor_identity = str(
                        getattr(scenario, "actor_role", "")
                        or getattr(scenario, "actor", "")
                        or getattr(scenario, "actor_id", "")
                        or runtime_contract.get("actor_identity")
                        or ""
                    ).strip()
                    if _scenario_has_login and not scenario_actor_identity:
                        _declared_actors = list(getattr(scenario, "actors", []) or [])
                        if _declared_actors:
                            scenario_actor_identity = str(_declared_actors[0]).strip()
                    if not _token and execution_mode != "safe_read_only" and not scenario_actor_identity:
                        attempted_traces.append((scenario, {
                            "scenario_id": getattr(scenario, "id", "?"),
                            "steps": [],
                            "errors": ["test_actor_identity_missing"],
                            "execution_blocked": True,
                            "execution_block_reason": "test_actor_identity_missing",
                            "actor_role": _role,
                        }))
                        continue
                    # ── Role-aware expected status ──
                    # If the actor is disabled/locked, all endpoints should reject.
                    # This catches AUTH-001 (disabled user login) automatically.
                    _account_status = _account_statuses.get(_role, "")
                    _role_expected_override = None
                    if _account_status in ("DISABLED", "LOCKED"):
                        _role_expected_override = 403
                    try:
                        scenario.actor_token = "" if (_is_account_status or _scenario_has_login) else _token
                        from .sandbox_write_executor import execute_with_sandbox_write

                        trace = execute_with_sandbox_write(
                            scenario,
                            approved_base_url,
                            root=root,
                            project=project,
                            runtime_contract=runtime_contract,
                            campaign_id=str(campaign.campaign_id or ""),
                            safety_boundary=_safety_boundary,
                            observer_token=str(_orig_token or _token or ""),
                            documented_routes=catalog,
                            execute_fn=lambda sc, bu, safety_boundary=None, write_observer=None: _execute_scenario(
                                sc,
                                bu,
                                max_retries=2,
                                safety_boundary=safety_boundary,
                                write_observer=write_observer,
                            ),
                        )
                        # Tag trace with role info for oracle
                        trace["actor_role"] = _role
                        attempted_traces.append((scenario, trace))
                        # ── DISABLED/LOCKED account check ──
                        # Catch AUTH-001 automatically: any 2xx response from a
                        # disabled/locked account is a confirmed violation.
                        if _account_status in ("DISABLED", "LOCKED"):
                            for step in trace.get("steps", []):
                                if not isinstance(step, dict):
                                    continue
                                _step_response = step.get("response") if isinstance(step.get("response"), dict) else {}
                                st = int(_step_response.get("status_code") or step.get("status") or 0)
                                if 200 <= st < 300:
                                    _step_method = str(step.get("method") or "").upper()
                                    _step_path = str(step.get("path") or "")
                                    _resp_body = _step_response.get("body", {})
                                    _resp_headers = _step_response.get("headers", {})
                                    _action_name = str(step.get("action") or "").strip().lower()
                                    _is_login_step = (
                                        _action_name == "login"
                                        or _action_name.startswith("login_")
                                        or _step_path.rstrip("/").lower().endswith("/login")
                                    )
                                    if _is_login_step and not _auth_acceptance_observed(_resp_body, _resp_headers):
                                        continue
                                    _disabled_key = (_role, _step_method, _step_path)
                                    if _disabled_key in _disabled_finding_keys:
                                        continue
                                    _disabled_finding_keys.add(_disabled_key)
                                    _ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                                    _evidence_id = f"EVID_DISABLED_{_role}_{int(time.time())}"
                                    _expected_text = f"{_account_status} account must be rejected before business access or credentials/session are granted (HTTP 401/403)."
                                    _actual_text = (
                                        f"HTTP {st} issued a login acceptance signal."
                                        if _is_login_step
                                        else f"HTTP {st} allowed a DISABLED/LOCKED account request."
                                    )
                                    _failed_assertion = f"Expected {_expected_text} Actual {_actual_text}"
                                    _step_request = step.get("request") if isinstance(step.get("request"), dict) else {}
                                    _raw_request = {"method": _step_method, "path": _step_path, "actor": _role}
                                    if "body" in _step_request:
                                        _raw_request["body"] = _redact(_step_request.get("body"))
                                    _reproduction_steps = [f"{_step_method} {_step_path}"]
                                    result["findings"].append({
                                        "severity": "P0",
                                        "title": f"[账号状态绕过] DISABLED/LOCKED账号 {_role} 访问成功 HTTP {st}",
                                        "category": "authorization_access_control",
                                        "source": "v12_role_aware_executor",
                                        "description": f"账号状态={_account_status}但端点返回 {st}，应返回401/403",
                                        "confidence_score": 0.95,
                                        "behavior_slice_id": getattr(scenario, "behavior_slice_id", ""),
                                        "discovery_round": settings["round_number"],
                                        "campaign_id": campaign.campaign_id,
                                        "execution_status": "executed",
                                        "confirmation_status": "confirmed",
                                        "gate_passed": True,
                                        "customer_delivery_status": "defect",
                                        "bug_status": "reproduced",
                                        "expected": _expected_text,
                                        "actual": _actual_text,
                                        "timestamp": _ts,
                                        "failed_assertions": [_failed_assertion],
                                        "expected_actual_comparison": {
                                            "expected": _expected_text,
                                            "actual": _actual_text,
                                            "difference": "Disabled or locked account was accepted by the target.",
                                        },
                                        "method": _step_method,
                                        "path": _step_path,
                                        "evidence_id": _evidence_id,
                                        "evidence": {
                                            "request": f"{_step_method} {_step_path}",
                                            "response": {"status_code": st, "headers": _resp_headers, "body": _resp_body},
                                            "assertion": "DISABLED/LOCKED account must be rejected (401/403)",
                                            "timestamp": _ts,
                                            "target": _step_path,
                                            "actor": _role,
                                            "reproduction_steps": _reproduction_steps,
                                        },
                                        "raw_evidence": {
                                            "has_real_evidence": True,
                                            "actor_role": _role,
                                            "account_status": _account_status,
                                            "request_raw": _raw_request,
                                            "response_raw": {"status_code": st, "headers": _resp_headers, "body": _resp_body},
                                            "execution_trace": {"evidence_id": _evidence_id, "layers": ["runtime_http_authz_status"]},
                                            "timestamp": _ts,
                                        },
                                        "reproduction": {
                                            "method": _step_method,
                                            "path": _step_path,
                                            "actor": _role,
                                            "is_synthetic": False,
                                            "reproduction_steps": _reproduction_steps,
                                            "har_evidence": {"status_code": st, "response_headers": _resp_headers, "response_body": _resp_body},
                                        },
                                        "evidence_quality": {
                                            "level": "validated",
                                            "score": 95,
                                            "can_reproduce": True,
                                            "evidence_strength": "runtime_http_authz_status",
                                        },
                                        "evidence_status": {
                                            "semantic_verdict": "SEMANTIC_CONFIRMED",
                                            "business_evidence_status": "VALIDATED",
                                            "final_review_status": "VALIDATED_CANDIDATE",
                                            "missing_requirements": [],
                                        },
                                        "final_review_status": "VALIDATED_CANDIDATE",
                                        "business_evidence_status": "VALIDATED",
                                        "evidence_strength": "runtime_http_authz_status",
                                        "reproduction_steps": _reproduction_steps,
                                        "system_promise_id": f"AUTO-DISABLED-{_role}",
                                        "regression_contract": {"contract_type": "system_behavior_promise_regression", "promise_id": f"AUTO-DISABLED-{_role}", "dimensions": ["authorization_access_control"], "surface_plan": ["api", "auth"], "source_family": "authorization_access_control"},
                                        "system_behavior_dimensions": ["authorization_access_control"],
                                        "system_behavior_surface_plan": ["api", "auth"],
                                        "learning_signal": {"source": "role_aware_executor", "dimensions": ["authorization_access_control"], "surfaces": ["api", "auth"]},
                                    })
                        has_runtime_receipt = any(
                            int(step.get("status") or _dict(step.get("response")).get("status_code") or 0) > 0
                            for step in (trace.get("steps") or [])
                            if isinstance(step, dict)
                        )
                        if has_runtime_receipt:
                            traces.append((scenario, trace))
                        slice_id = str(getattr(scenario, "behavior_slice_id", "") or "").strip()
                        if slice_id and has_runtime_receipt:
                            attempted_slice_ids.add(slice_id)
                        for step in trace.get("steps", []):
                            if int(step.get("status") or 0) >= 500:
                                result["findings"].append({
                                    "severity": "P0",
                                    "title": f"[场景执行错误] {str(getattr(scenario, 'title', 'scenario'))[:80]}",
                                    "category": getattr(scenario, "category", "scenario_flow"),
                                    "source": "v12_scenario_executor",
                                    "description": f"服务端错误 HTTP{step.get('status')}: {step.get('path', '')}",
                                    "confidence_score": 0.80,
                                    "behavior_slice_id": getattr(scenario, "behavior_slice_id", ""),
                                    "discovery_round": settings["round_number"],
                                    "campaign_id": campaign.campaign_id,
                                    "execution_status": "executed",
                                    "confirmation_status": "candidate",
                                    "evidence": {"calls": [{"call": f"{step.get('method', '')} {step.get('path', '')}", "results": {"execution": {"status": step.get("status"), "body": step.get("response", {}).get("body", {})}}}]},
                                })
                    except Exception as _exec_err:
                        logger.error(
                            "scenario execution failed scenario=%s role=%s type=%s err=%s",
                            getattr(scenario, "behavior_slice_id", "") or getattr(scenario, "id", "?"),
                            _role,
                            type(_exec_err).__name__,
                            str(_exec_err)[:300],
                        )
                        failed += 1
                        attempted_traces.append((scenario, {
                            "scenario_id": getattr(scenario, "id", "?"),
                            "steps": [],
                            "errors": [f"scenario_execution_failed:{type(_exec_err).__name__}:{_exec_err}"],
                            "execution_blocked": True,
                            "execution_block_reason": "scenario_execution_failed",
                            "actor_role": _role,
                        }))
                # Restore original token after role loop
                scenario.actor_token = _orig_token
            real_trace_count = sum(
                1
                for _, trace in traces
                if any(
                    int(step.get("status") or _dict(step.get("response")).get("status_code") or 0) > 0
                    for step in (trace.get("steps") or [])
                    if isinstance(step, dict)
                )
            )
            execution_telemetry = _summarize_execution_skip_telemetry(attempted_traces)
            observed_http_request_count = sum(
                1
                for _, trace in attempted_traces
                for step in (trace.get("steps") or [])
                if isinstance(step, dict)
                and int(step.get("status") or _dict(step.get("response")).get("status_code") or 0) > 0
            )
            execution_phase_status = "completed" if real_trace_count > 0 else "blocked"
            execution_reason = "" if real_trace_count > 0 else (
                "test_actor_identity_missing"
                if not _role_tokens and not any(
                    str(
                        getattr(scenario, "actor_role", "")
                        or getattr(scenario, "actor", "")
                        or getattr(scenario, "actor_id", "")
                        or runtime_contract.get("actor_identity")
                        or ""
                    ).strip()
                    for scenario in executable
                )
                else "no_runtime_execution_receipts"
            )
            result["phases"]["execution"] = {
                "status": execution_phase_status,
                "reason": execution_reason,
                "executed": real_trace_count,
                "failed": failed,
                "selected_slices": len(selection["selected_slice_ids"]),
                "generated_scenarios": len(scenarios),
                "executable_scenarios": len(executable),
                "scenario_attempts": len(attempted_traces),
                "observed_http_request_count": observed_http_request_count,
                "production_http_requests": 0,
                "attempts_without_http": int(execution_telemetry.get("scenarios_blocked") or 0),
                "planned_only": len(plan_only),
                # 主链 5 × 主链 1: observability — how many scenarios were blocked
                # by the customer's production-data safety boundary (never touched).
                "production_data_blocked": sum(1 for _, t in attempted_traces if t.get("production_data_blocked")),
                "skip_telemetry": execution_telemetry,
                "observability": _execution_observability,
                "duration_ms": int((time.time() - execution_started) * 1000),
            }
            if any(
                str(item.get("status") or "") in {"failed", "missing"}
                for item in _execution_observability
                if isinstance(item, dict)
            ):
                # Keep execution receipts, but surface account/probe failures so
                # operators never confuse "no bugs" with "multi-role path died".
                result["phases"]["execution"]["observability_status"] = "FAILED_SAFE"
                if not result["phases"]["execution"].get("reason"):
                    result["phases"]["execution"]["reason"] = "execution_observability_gap"
        result["execution_trace_summaries"] = [
            _redacted_execution_trace_graph(
                scenario,
                trace,
                discovery_round=settings["round_number"],
            )
            for scenario, trace in attempted_traces
            if isinstance(trace, dict)
        ]
        try:
            from .ui_execution_adapter import execute_ui_execution_requests

            ui_execution = execute_ui_execution_requests(
                project,
                context.get("ui_execution_requests"),
                runtime_contract,
                root=root,
                run_id=f"{campaign.campaign_id or project}_round_{settings['round_number']}",
                execution_context=context,
            )
        except Exception as exc:
            ui_execution = {
                "status": "failed",
                "requested": 0,
                "executed": 0,
                "failed": 1,
                "blocked": 0,
                "provider_distribution": {},
                "results": [],
                "findings": [],
                "artifacts": [],
                "duration_ms": 0,
                "reason": f"ui_execution_adapter_error:{type(exc).__name__}",
            }
        result["ui_execution"] = ui_execution
        normalized_ui_findings, ui_evidence_graphs = _normalize_ui_execution_findings(
            ui_execution,
            campaign_id=campaign.campaign_id,
            discovery_round=settings["round_number"],
        )
        result["ui_findings"] = normalized_ui_findings
        result["evidence_graphs"].extend(ui_evidence_graphs)
        result["phases"]["ui_execution"] = {
            "status": str(ui_execution.get("status") or "not_requested"),
            "requested": int(ui_execution.get("requested") or 0),
            "executed": int(ui_execution.get("executed") or 0),
            "failed": int(ui_execution.get("failed") or 0),
            "blocked": int(ui_execution.get("blocked") or 0),
            "provider_distribution": dict(ui_execution.get("provider_distribution") or {}),
            "findings": len(normalized_ui_findings),
            "duration_ms": int(ui_execution.get("duration_ms") or 0),
        }
        try:
            from .external_signal_adapter import execute_external_signal_requests

            external_signal_execution = execute_external_signal_requests(
                project,
                context.get("external_signal_requests"),
                runtime_contract,
                root=root,
                run_id=f"{campaign.campaign_id or project}_round_{settings['round_number']}",
                execution_context=context,
            )
        except Exception as exc:
            external_signal_execution = {
                "status": "failed",
                "requested": 0,
                "imported": 0,
                "failed": 1,
                "blocked": 0,
                "provider_distribution": {},
                "results": [],
                "findings": [],
                "artifacts": [],
                "duration_ms": 0,
                "reason": f"external_signal_adapter_error:{type(exc).__name__}",
            }
        result["external_signal_execution"] = external_signal_execution
        result["external_findings"] = list(external_signal_execution.get("findings") or [])
        result["phases"]["external_signals"] = {
            "status": str(external_signal_execution.get("status") or "not_requested"),
            "requested": int(external_signal_execution.get("requested") or 0),
            "imported": int(external_signal_execution.get("imported") or 0),
            "failed": int(external_signal_execution.get("failed") or 0),
            "blocked": int(external_signal_execution.get("blocked") or 0),
            "provider_distribution": dict(external_signal_execution.get("provider_distribution") or {}),
            "duration_ms": int(external_signal_execution.get("duration_ms") or 0),
        }
        oracle_started = time.time()
        from .oracle_engine import EvidenceGraphBuilder, OracleEngine
        oracle, evidence_builder = OracleEngine(), EvidenceGraphBuilder()
        _pre_oracle_findings = len(result["findings"])
        _oracle_evaluated = 0
        _oracle_violations = 0
        _execution_trace_summary_index = {
            _execution_trace_identity(scenario, trace): summary
            for (scenario, trace), summary in zip(
                attempted_traces,
                result.get("execution_trace_summaries") or [],
            )
            if isinstance(trace, dict) and isinstance(summary, dict)
        }
        for scenario, trace in traces:
            # Skip traces that never made a real HTTP request (all steps errored/skipped).
            # These produce false-positive 404/400 findings from unexecutable scenarios.
            _has_real_request = any(
                int(s.get("status") or s.get("response", {}).get("status_code") or 0) > 0
                for s in (trace.get("steps") if isinstance(trace, dict) and isinstance(trace.get("steps"), list) else [])
                if isinstance(s, dict)
            )
            if not _has_real_request:
                continue
            _oracle_evaluated += 1
            _summary_graph = _execution_trace_summary_index.get(
                _execution_trace_identity(scenario, trace)
            )
            for oracle_result in oracle.evaluate(scenario.to_dict(), trace, None):
                if isinstance(_summary_graph, dict):
                    _summary_graph.setdefault("oracle_results", []).append(
                        {
                            "oracle_name": str(
                                getattr(oracle_result, "oracle_name", "") or ""
                            ),
                            "passed": bool(oracle_result.passed),
                        }
                    )
                if oracle_result.passed:
                    continue
                _oracle_violations += 1
                evidence = evidence_builder.build(scenario.to_dict(), trace, None, [oracle_result])
                result["evidence_graphs"].append(evidence.to_dict())
                result["findings"].append(_confirmed_oracle_finding(
                    scenario,
                    trace,
                    oracle_result,
                    evidence,
                    campaign_id=campaign.campaign_id,
                    discovery_round=settings["round_number"],
                    base_url=approved_base_url,
                ))
        result["phases"]["oracle"] = {
            "status": "completed",
            "traces_total": len(traces),
            "traces_with_http": _oracle_evaluated,
            "total_evaluated": _oracle_evaluated,
            "violations_found": _oracle_violations,
            "findings_total_after_oracle": len(result["findings"]),
            "pre_oracle_findings": _pre_oracle_findings,
            "duration_ms": int((time.time() - oracle_started) * 1000),
        }
        # Phase 3: demote heuristic business-oracle hits lacking contract evidence
        # to internal clues (never silently keep them as customer defects).
        try:
            from .contract_oracles import demote_heuristic_business_oracle_finding

            result["findings"] = [
                demote_heuristic_business_oracle_finding(item)
                if isinstance(item, dict) else item
                for item in (result.get("findings") or [])
            ]
        except Exception as _demote_exc:
            result["phases"]["oracle"]["contract_oracle_demotion_error"] = (
                f"{type(_demote_exc).__name__}: {str(_demote_exc)[:160]}"
            )
        # 主链 7: persist every collected evidence chain keyed by its stable
        # evidence_id so it is retrievable for regression (主链 9) & delivery.
        _evidence_chains_saved = 0
        _evidence_persistence_errors: list[dict[str, Any]] = []
        for _eg in result["evidence_graphs"]:
            if not isinstance(_eg, dict):
                continue
            try:
                if _persist_evidence_chain(root, project, _eg):
                    _evidence_chains_saved += 1
            except Exception as exc:
                _evidence_persistence_errors.append({
                    "stage": "evidence_persistence",
                    "code": str(exc).split(":", 1)[0],
                    "identity": str(_eg.get("evidence_id") or _eg.get("scenario_id") or "unknown"),
                    "retryability": "after_storage_or_identity_fix",
                    "operator_action": "inspect evidence identity and artifact storage",
                    "detail": f"{type(exc).__name__}: {exc}"[:500],
                })
        result["phases"]["oracle"]["evidence_chains_saved"] = _evidence_chains_saved
        if _evidence_persistence_errors:
            result["phases"]["oracle"]["status"] = "degraded"
            result["phases"]["oracle"]["evidence_persistence_errors"] = _evidence_persistence_errors
            result.setdefault("coverage_gaps", []).extend(
                {"kind": "EVIDENCE_PERSISTENCE_FAILED", **item}
                for item in _evidence_persistence_errors
            )
        # 主链 9 Gap B1: persist deliverable confirmed defects keyed by their
        # stable evidence_id so the regression runner can re-verify them.
        result["phases"]["oracle"]["confirmed_findings_saved"] = _persist_confirmed_findings(
            root, project, result["findings"]
        )
        try:
            from .risk_clue_pool import save_risk_clues
            result["risk_clues_saved"] = save_risk_clues(project, root, result["findings"]).get("new_this_scan", 0)
        except Exception as exc:
            result.setdefault("coverage_gaps", []).append({
                "kind": "RISK_CLUE_PERSISTENCE_FAILED",
                "stage": "evidence_persistence",
                "code": "RISK_CLUE_PERSISTENCE_FAILED",
                "identity": str(campaign.campaign_id),
                "retryability": "after_storage_fix",
                "operator_action": "inspect risk clue storage",
                "detail": f"{type(exc).__name__}: {exc}"[:500],
            })
        execution_status = str(result["phases"]["execution"].get("status") or "")
        if not skip_history_persistence:
            campaign.record_cycle(
                round_number=settings["round_number"],
                selection=selection,
                findings=result["findings"],
                coverage_gap_count=len(behavior_contract["coverage_gaps"]),
                execution_status=execution_status,
                attempted_slice_ids=sorted(attempted_slice_ids),
            )
            campaign_store.save(campaign)
            result["campaign"] = {**campaign.public_contract(), "campaign_mode": campaign_mode}
            result["behavior_slice_ledger"]["campaign_status"] = campaign.status
            result["behavior_slice_ledger"]["attempted_slice_ids"] = list(campaign.attempted_slice_ids)
            # confirmed_slice_ids = newly confirmed this cycle (campaign receipts)
            # UNION carried-forward confirmations for the same source (selection
            # history, keyed on source_hash). Dropping the carried-forward set here
            # made same-source reruns lose previously confirmed slices.
            result["behavior_slice_ledger"]["confirmed_slice_ids"] = sorted(
                set(campaign.confirmation_receipts)
                | {str(v) for v in (selection.get("confirmed_slice_ids") or []) if str(v)}
            )
            # 主链 4: keep the per-task status map consistent with the final
            # attempted/confirmed ids after the campaign cycle is recorded.
            result["behavior_slice_ledger"]["slice_status"] = _derive_slice_status(
                result["behavior_slice_ledger"]["attempted_slice_ids"],
                result["behavior_slice_ledger"]["confirmed_slice_ids"],
                campaign.status,
            )
            result["phases"]["incremental_discovery"]["campaign_status"] = campaign.status
            ledger_for_persistence = dict(result["behavior_slice_ledger"])
    except Exception as exc:
        logger.exception("run_v12_pipeline unexpected error")
        _record_pipeline_failure(result, exc)
    if ledger_for_persistence is not None and not result.get("error"):
        try:
            _persist_slice_ledger(root, project, ledger_for_persistence)
        except Exception as _ledger_exc:
            logger.exception("persist slice ledger failed")
            result["slice_ledger_persist_error"] = (
                f"{type(_ledger_exc).__name__}: {str(_ledger_exc)[:200]}"
            )
            # Critical path failure must degrade health — never silent pass.
            if not result.get("error"):
                result["discovery_funnel_error"] = (
                    result.get("discovery_funnel_error")
                    or f"slice_ledger_persist_failed:{type(_ledger_exc).__name__}"
                )
    result["total_duration_ms"] = int((time.time() - started) * 1000)
    result["auto_har"] = _v12_har_report()
    try:
        from .discovery_funnel import build_funnel

        gate_results = []
        for finding in result.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            missing = finding.get("business_gate_missing")
            if missing:
                gate_results.append({"business_gate_missing": missing})
            status = str(finding.get("final_review_status") or finding.get("business_evidence_status") or "")
            if status and ("NEEDS_MORE_EVIDENCE" in status.upper() or status.upper().startswith("PENDING")):
                gate_results.append({
                    "business_gate_missing": list(
                        (finding.get("evidence_status") or {}).get("missing_requirements") or []
                    ) or ([status] if status else []),
                })
        result["discovery_funnel"] = build_funnel(result, gate_results)
    except Exception as exc:
        result["discovery_funnel"] = {
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            "stages": [],
            "top_blocking_reasons": [],
            "validated_bug_count": 0,
            "pending_finding_count": 0,
            "candidate_count": 0,
            "explanation": f"漏斗聚合失败：{type(exc).__name__}",
        }
    return result


# NOTE: _load_execution_safety_boundary now lives in enterprise_project_config.py
# (single source of truth shared with regression_runner) and is imported above.


def _execute_scenario(
    scenario: Any,
    base_url: str,
    max_retries: int = 2,
    safety_boundary: dict[str, Any] | None = None,
    write_observer: Any = None,
) -> dict[str, Any]:
    # Retrying an entire write scenario can duplicate already-accepted writes.
    # Read-only scenarios retain bounded retry behavior; write scenarios execute
    # once and surface the original failure.
    has_write = any(
        str(getattr(step, "api_method", "") or "").upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and str(getattr(step, "action", "") or "").strip().lower() != "login"
        for step in (getattr(scenario, "steps", []) or [])
    )
    attempt_limit = 0 if has_write else max(0, int(max_retries or 0))
    for attempt in range(attempt_limit + 1):
        try:
            return __execute_scenario_once(
                scenario,
                base_url,
                safety_boundary=safety_boundary,
                write_observer=write_observer,
            )
        except Exception as exc:
            if attempt < attempt_limit:
                time.sleep(0.5 * (attempt + 1))
                continue
            return {"scenario_id": getattr(scenario, "id", "?"), "steps": [], "errors": [f"failed_after_retries:{exc}"], "duration_ms": 0}
    return {"scenario_id": "?", "steps": [], "errors": ["unreachable"], "duration_ms": 0}


def _resolve_get_candidates(path: str) -> list[str]:
    """Resolve-step GET URLs: bare collection first, then paginated variants."""
    base = str(path or "").split("?", 1)[0]
    if not base:
        return []
    from .real_id_resolver import _PAGINATION_SUFFIXES

    candidates = [base] if "?" not in str(path or "") else [str(path)]
    if "?" not in base:
        candidates.extend(base + suffix for suffix in _PAGINATION_SUFFIXES)
    return list(dict.fromkeys(item for item in candidates if item.startswith("/")))


def _read_observer_action(action_name: str) -> bool:
    action = str(action_name or "").strip().lower()
    return (
        action.startswith("observe_")
        or action.startswith("verify_")
        or "observer" in action
        or "observation" in action
    )


def _entity_candidates_from_response(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [dict(item) for item in body if isinstance(item, dict)]
    if not isinstance(body, dict):
        return []
    for field in ("records", "data", "items", "results", "list", "rows"):
        value = body.get(field)
        if isinstance(value, list):
            rows = [dict(item) for item in value if isinstance(item, dict)]
            if rows:
                return rows
        if isinstance(value, dict):
            nested = _entity_candidates_from_response(value)
            if nested:
                return nested
    for value in body.values():
        nested = _entity_candidates_from_response(value)
        if nested:
            return nested
    return []


def _identity_binding_keys(path_template: str, bindings: dict[str, Any]) -> list[str]:
    params = infer_path_params(path_template)
    if params:
        return params
    priority = ["id", "uuid", "code", "sku", "orderId", "order_id"]
    discovered = [
        str(key)
        for key, value in bindings.items()
        if value not in (None, "", [], {})
        and (
            str(key).lower() in {"id", "uuid", "code", "sku"}
            or str(key).lower().endswith("id")
            or str(key).lower().endswith("_id")
        )
    ]
    return list(dict.fromkeys([*priority, *discovered]))


def _binding_value_for_key(key: str, bindings: dict[str, Any]) -> Any:
    for alias in param_field_candidates(key):
        value = bindings.get(alias)
        if value not in (None, "", [], {}):
            return value
    value = bindings.get(key)
    if value not in (None, "", [], {}):
        return value
    return None


def _entity_matches_runtime_binding(entity: dict[str, Any], key: str, expected: Any) -> bool:
    if expected in (None, "", [], {}):
        return False
    expected_text = str(expected)
    for field in param_field_candidates(key):
        value = entity.get(field)
        if value not in (None, "", [], {}) and str(value) == expected_text:
            return True
    return False


def _project_bound_observer_entity(
    body: Any,
    *,
    path_template: str,
    bindings: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return the bound entity from a collection response, preserving trace metadata."""

    candidates = _entity_candidates_from_response(body)
    if not candidates:
        return None, {}
    keys = _identity_binding_keys(path_template, bindings)
    matched_keys: list[str] = []
    for key in keys:
        expected = _binding_value_for_key(key, bindings)
        if expected in (None, "", [], {}):
            continue
        for index, entity in enumerate(candidates):
            if _entity_matches_runtime_binding(entity, key, expected):
                matched_keys.append(key)
                return dict(entity), {
                    "projection": "bound_entity_from_collection",
                    "matched_key": key,
                    "matched_value": str(expected),
                    "candidate_index": index,
                    "candidate_count": len(candidates),
                }
    return None, {
        "projection": "bound_entity_from_collection_not_found",
        "candidate_count": len(candidates),
        "binding_keys": keys,
    }


def _declared_get_hints(scenario: Any) -> set[str]:
    hints = getattr(scenario, "runtime_hints", {}) or {}
    if not isinstance(hints, dict):
        return set()
    values = hints.get("declared_get_paths") or hints.get("declared_read_paths") or []
    if not isinstance(values, list):
        return set()
    return {
        normalize_path_placeholders(str(item or "")).split("?", 1)[0]
        for item in values
        if str(item or "").startswith("/")
    }


def _observer_collection_fallback_paths(path_template: str, concrete_path: str, scenario: Any) -> list[str]:
    normalized_template = normalize_path_placeholders(path_template).split("?", 1)[0]
    if not path_has_placeholders(normalized_template):
        return []
    primary = collection_path(normalized_template)
    candidates = [primary] if primary else []
    candidates.extend(alternate_collection_paths(normalized_template))
    declared = _declared_get_hints(scenario)
    concrete = str(concrete_path or "").split("?", 1)[0].rstrip("/")
    filtered: list[str] = []
    for candidate in candidates:
        path = normalize_path_placeholders(str(candidate or "")).split("?", 1)[0]
        if not path.startswith("/") or path_has_placeholders(path):
            continue
        if path.rstrip("/") == concrete:
            continue
        if declared and path not in declared:
            continue
        filtered.append(path)
    return list(dict.fromkeys(filtered))


def _encoded_request_url(base_url: str, path: str) -> str:
    """Percent-encode non-ASCII route/query text without changing separators."""

    raw_path, separator, raw_query = str(path or "").partition("?")
    encoded_path = urllib.parse.quote(raw_path, safe="/%:@-._~!$&'()*+,;=")
    encoded_query = urllib.parse.quote(raw_query, safe="=&;%:@/?-._~!$'()*+,") if separator else ""
    suffix = f"?{encoded_query}" if separator else ""
    return base_url.rstrip("/") + encoded_path + suffix


def _body_has_unbound_placeholders(body: Any) -> list[str]:
    text = json.dumps(body, ensure_ascii=False) if body else ""
    return list(dict.fromkeys(re.findall(r"\{([A-Za-z_]\w*)\}", text)))


def _coerce_runtime_amount(value: Any, original: Any) -> Any:
    text = str(value).strip()
    if not text:
        return original
    try:
        number = float(text)
    except (TypeError, ValueError):
        return value
    if isinstance(original, int) and not isinstance(original, bool) and number.is_integer():
        return int(number)
    if isinstance(original, float):
        return float(number)
    if number.is_integer():
        return int(number)
    return number


def _runtime_amount_binding(bindings: dict[str, Any]) -> Any:
    for key in (
        "payable_amount",
        "payableAmount",
        "amount_due",
        "amountDue",
        "due_amount",
        "dueAmount",
        "total_amount",
        "totalAmount",
        "amount",
    ):
        value = bindings.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _is_money_control_step(scenario: Any, action_name: str) -> bool:
    category = str(getattr(scenario, "category", "") or "").strip().lower()
    if "money" in category or "financial" in category or "conservation" in category:
        return True
    action = str(action_name or "").lower()
    return any(
        token in action
        for token in (
            "money",
            "payment",
            "refund",
            "pay",
            "charge",
            "capture",
            "settle",
            "checkout",
        )
    )


def _bind_runtime_amount_controls(body: Any, bindings: dict[str, Any], scenario: Any, action_name: str) -> Any:
    if not isinstance(body, dict) or not _is_money_control_step(scenario, action_name):
        return body
    amount = _runtime_amount_binding(bindings)
    if amount in (None, "", [], {}):
        return body
    out: dict[str, Any] = {}
    for key, value in body.items():
        key_l = str(key).strip().lower()
        if isinstance(value, dict):
            out[key] = _bind_runtime_amount_controls(value, bindings, scenario, action_name)
        elif isinstance(value, list):
            out[key] = [
                _bind_runtime_amount_controls(item, bindings, scenario, action_name)
                if isinstance(item, dict) else item
                for item in value
            ]
        elif key_l == "amount" or key_l.endswith("amount") or key_l.endswith("_amount"):
            out[key] = _coerce_runtime_amount(amount, value)
        else:
            out[key] = value
    return out


def __execute_scenario_once(
    scenario: Any,
    base_url: str,
    safety_boundary: dict[str, Any] | None = None,
    write_observer: Any = None,
) -> dict[str, Any]:
    trace: dict[str, Any] = {"scenario_id": getattr(scenario, "id", "?"), "steps": [], "errors": []}
    # Runtime bindings are produced only by explicit, source-derived scenario
    # steps (login/resolver/create). Never perform hidden seed reads or writes
    # before authentication: those bypass the scenario evidence chain and make
    # the executor industry-specific.
    bindings: dict[str, Any] = {}
    actor_tokens: dict[str, str] = {}

    # ── DB evidence: snapshot the data layer before/after write scenarios ──
    # Config-driven (QUALIBUG_DB_DSN); no per-project table hardcoding. The diff
    # itself reveals which table changed, giving idempotency/consistency/state
    # oracles a real data-layer proof instead of an HTTP-status-only inference.
    _db_verifier = None
    _db_tables: list[str] = []
    _scenario_has_write = any(
        str(getattr(s, "api_method", "") or "").upper() in {"POST", "PUT", "PATCH", "DELETE"}
        for s in (getattr(scenario, "steps", []) or [])
    )
    if _scenario_has_write and os.environ.get("QUALIBUG_DB_DSN"):
        try:
            from .db_snapshot_verifier import DBSnapshotVerifier

            _db_verifier = DBSnapshotVerifier()
            _db_tables = _db_verifier.list_tables()
            if _db_tables:
                _db_verifier.snapshot_before(_db_tables)
            else:
                _db_verifier = None
        except Exception as _db_exc:
            trace["db_evidence"] = {"status": "unavailable", "reason": f"db_snapshot_setup_failed:{type(_db_exc).__name__}"}
            _db_verifier = None
    for step in getattr(scenario, "steps", []) or []:
        method = str(getattr(step, "api_method", "") or "").upper()
        raw_path = str(getattr(step, "api_path", "") or "")
        path_template = normalize_path_placeholders(raw_path)
        path, body = path_template, getattr(step, "body_template", {}) or {}
        if not method or not path.startswith("/"):
            trace["errors"].append("invalid_source_bound_step")
            continue
        # Normalize placeholders FIRST (:id → {id}) so _replace can match them
        path, body = _replace(path, bindings), _replace(body, bindings)
        if path_has_placeholders(path) and body and method not in {"GET", "HEAD"}:
            from .policy_wiring import get_policy_value

            allowed_binding_sources = {
                str(item).strip()
                for item in (
                    get_policy_value("execution", "runtime_binding_sources", []) or []
                )
                if str(item).strip()
            }
            body_provenance = str(getattr(step, "body_provenance", "") or "").strip()
            required_source = {
                "documented_example": "documented_example",
                "documented_schema_generated": "documented_schema_generated_value",
            }.get(body_provenance, "")
            if required_source and required_source in allowed_binding_sources:
                candidate_bindings, binding_evidence = bind_path_params_from_documented_body(
                    path,
                    body,
                )
                for key, value in candidate_bindings.items():
                    bindings.setdefault(key, value)
                if binding_evidence:
                    trace.setdefault("runtime_binding_events", []).extend([
                        {
                            **item,
                            "source": required_source,
                            "body_provenance": body_provenance,
                        }
                        for item in binding_evidence
                    ])
                    path, body = _replace(path, bindings), _replace(body, bindings)
        if path_has_placeholders(path):
            # Alias fill: sku/orderId/id often share the same bound value under
            # different names depending on which list endpoint returned them.
            path = _fill_path_aliases(path, bindings)
            body = _replace(body, bindings)
        if path_has_placeholders(path):
            missing_bindings = infer_path_params(path)
            reason = f"missing_runtime_path_binding:{','.join(missing_bindings)}" if missing_bindings else "missing_runtime_path_binding"
            trace["errors"].append(reason)
            trace.setdefault("precondition_not_met", list(trace.get("precondition_not_met", [])))
            trace["precondition_not_met"].append({
                "step": getattr(step, "action", ""),
                "path": path,
                "missing_path_params": missing_bindings,
            })
            trace["steps"].append({
                "action": getattr(step, "action", ""),
                "method": method,
                "path": path,
                "status": 0,
                "request": {"body": _redact(body)} if body else {},
                "response": {"status_code": 0, "headers": {}, "body": {"error": reason}},
                "expected_status": getattr(step, "expected_status", 0),
                "skipped_reason": reason,
            })
            break
        # 主链 5 × 主链 1: honor the customer-defined production-data safety
        # boundary during REAL execution. Reuses match_production_data_exclusion
        # — the single source of truth shared with grounded_probe_executor
        # (主链 1). If a step would touch excluded production data we MUST NOT
        # fire the request: block only, never enable. The step is recorded as
        # blocked so the oracle knows the evidence is intentionally absent (not
        # a fabricated pass).
        if safety_boundary:
            _excl = match_production_data_exclusion(
                safety_boundary, path, str(getattr(step, "risk_type", "") or "")
            )
            if _excl:
                trace.setdefault("production_data_blocked", True)
                trace["production_data_block_reason"] = _excl
                trace["errors"].append(_excl)
                trace["steps"].append({
                    "action": getattr(step, "action", ""),
                    "method": method,
                    "path": path,
                    "status": 0,
                    "request": {"body": _redact(body)} if body else {},
                    "response": {"status_code": 0, "headers": {}, "body": {"error": _excl}},
                    "expected_status": getattr(step, "expected_status", 0),
                    "skipped_reason": _excl,
                    "execution_blocked": True,
                })
                continue
        # ── DELETE safety guard: never allow blanket DELETE ──
        # Every DELETE must target a specific resource (path param like /:id)
        # or carry a query/filter condition.  Table-wide deletes are never
        # allowed — even in test environments.
        if method == "DELETE":
            _has_path_param = bool(re.search(r"/:[A-Za-z_]|/\{[A-Za-z_]", path))
            _has_query_filter = bool(re.search(r"[?&](id|uuid|sku|code|name|limit)=", str(getattr(step, "api_path", "") or "")))
            _has_body_filter = isinstance(body, dict) and any(
                k for k in body if k.lower() in ("id", "ids", "uuid", "sku", "email", "code", "filter", "where")
            )
            if not (_has_path_param or _has_query_filter or _has_body_filter):
                _delete_block_reason = "DELETE_SAFETY_GUARD: 禁止无条件的全表删除操作。DELETE 必须携带路径参数 (/:id) 或查询条件 (?id=) 或请求体过滤。"
                trace["errors"].append(_delete_block_reason)
                trace["steps"].append({
                    "action": getattr(step, "action", ""),
                    "method": method,
                    "path": path,
                    "status": 0,
                    "request": {"body": _redact(body)} if body else {},
                    "response": {"status_code": 0, "headers": {}, "body": {"error": _delete_block_reason}},
                    "expected_status": getattr(step, "expected_status", 0),
                    "skipped_reason": _delete_block_reason,
                    "execution_blocked": True,
                })
                continue
            # Inject safety LIMIT if not present
            if "limit" not in str(getattr(step, "api_path", "") or "").lower() and not _has_path_param:
                # Append ?_limit=1 or &limit=1 as safety net
                sep = "&" if "?" in path else "?"
                path = f"{path}{sep}_qualibug_safe_limit=1"
        unbound_body_fields = _body_has_unbound_placeholders(body) if body and method not in {"GET", "HEAD"} else []
        if unbound_body_fields:
            reason = f"missing_runtime_body_binding:{','.join(unbound_body_fields)}"
            trace["errors"].append(reason)
            trace.setdefault("precondition_not_met", list(trace.get("precondition_not_met", [])))
            trace["precondition_not_met"].append({
                "step": getattr(step, "action", ""),
                "path": path,
                "missing_body_fields": unbound_body_fields,
            })
            trace["steps"].append({
                "action": getattr(step, "action", ""),
                "method": method,
                "path": path,
                "status": 0,
                "request": {"body": _redact(body)} if body else {},
                "response": {"status_code": 0, "headers": {}, "body": {"error": reason}},
                "expected_status": getattr(step, "expected_status", 0),
                "skipped_reason": reason,
            })
            break
        action_name = str(getattr(step, "action", "") or "").strip().lower()
        if method in {"POST", "PUT", "PATCH", "DELETE"} and body:
            body = _bind_runtime_amount_controls(body, bindings, scenario, action_name)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body and method not in {"GET", "HEAD"} else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        token, actor = str(getattr(scenario, "actor_token", "") or ""), str(getattr(step, "actor", "") or "")
        if actor and actor in actor_tokens:
            token = actor_tokens[actor]
        # If the scenario didn't carry a pre-issued actor token, fall back to a
        # token captured earlier in this scenario (e.g. from a login step whose
        # extract_from_response=["token"]).  This lets multi-actor permission /
        # isolation probes authenticate as the role they logged in as.
        if not token and isinstance(bindings.get("token"), str) and bindings.get("token"):
            token = str(bindings["token"])
        if token:
            headers["Authorization"] = f"Bearer {token}"
        write_event_id: Any = None
        if method in {"POST", "PUT", "PATCH", "DELETE"} and action_name != "login" and write_observer is not None:
            write_event_id = write_observer(
                "before",
                {
                    "action": action_name,
                    "method": method,
                    "path": path,
                    "body": body,
                    "token": token,
                },
            )
        started = time.time()
        resolve_attempt_paths = (
            _resolve_get_candidates(path)
            if method == "GET" and action_name.startswith("resolve_")
            else [path]
        )
        status, response_body, response_headers = 0, {}, {}
        url = _encoded_request_url(base_url, path)
        har_recorded = False
        observer_projection: dict[str, Any] = {}
        for attempt_path in resolve_attempt_paths:
            url = _encoded_request_url(base_url, attempt_path)
            try:
                request = urllib.request.Request(url, method=method, data=data, headers=headers)
                with urllib.request.urlopen(request, timeout=10) as response:
                    raw = response.read(300_000).decode("utf-8", errors="replace")
                    status, response_body, response_headers = int(response.status), _json_or_text(raw), dict(response.headers.items())
            except urllib.error.HTTPError as exc:
                raw = exc.read(300_000).decode("utf-8", errors="replace") if exc.fp else ""
                status, response_body, response_headers = int(exc.code), _json_or_text(raw), dict(exc.headers.items()) if exc.headers else {}
            except Exception as exc:
                status, response_body, response_headers = 0, {"error": str(exc)}, {}
                trace["errors"].append(str(exc))
            # A resolver fallback is useful for an explicit HTTP 4xx/5xx
            # response, but a transport/runtime failure is not evidence that a
            # different pagination shape will work. Stop immediately so one
            # unavailable endpoint does not fan out into several opaque calls.
            if method == "GET" and action_name.startswith("resolve_") and status == 0:
                break
            if method != "GET" or not action_name.startswith("resolve_"):
                break
            if 200 <= status < 300:
                # A successful empty collection is an authoritative negative
                # result for this resolver.  Trying every pagination dialect
                # after a valid [] response only creates opaque traffic (and
                # can turn a deterministic missing-binding trace into a
                # transport-error cascade).  Pagination fallbacks remain
                # available for explicit HTTP errors and non-empty envelopes
                # whose requested identity field is absent.
                if isinstance(response_body, list) and not response_body:
                    path = attempt_path
                    break
                trial_bindings = dict(bindings)
                for field in getattr(step, "extract_from_response", []) or []:
                    value = _extract(response_body, str(field))
                    if value not in (None, "", [], {}):
                        trial_bindings[str(field)] = value
                for key, value in bind_entity_fields(response_body, attempt_path).items():
                    trial_bindings.setdefault(key, value)
                expected_fields = [str(f) for f in (getattr(step, "extract_from_response", []) or []) if str(f)]
                if not expected_fields or any(trial_bindings.get(f) not in (None, "", [], {}) for f in expected_fields):
                    path = attempt_path
                    break
            if attempt_path == resolve_attempt_paths[-1]:
                path = attempt_path
                break
        if method == "GET" and _read_observer_action(action_name):
            if status in {404, 405}:
                fallback_paths = _observer_collection_fallback_paths(path_template, path, scenario)
                if fallback_paths:
                    original_event = {
                        "source": "read_observer_collection_fallback",
                        "reason": f"observer_detail_http_{status}",
                        "original_path": path,
                        "original_status": status,
                        "candidate_paths": list(fallback_paths),
                    }
                    _record_v12_har(method, url, status, _redact(response_body), actor, (time.time() - started) * 1000)
                    har_recorded = True
                    trace.setdefault("observer_fallback_events", []).append(dict(original_event))
                    for fallback_path in fallback_paths:
                        fallback_started = time.time()
                        fallback_url = _encoded_request_url(base_url, fallback_path)
                        fallback_status, fallback_body, fallback_headers = 0, {}, {}
                        fallback_error = ""
                        try:
                            request = urllib.request.Request(fallback_url, method=method, headers=headers)
                            with urllib.request.urlopen(request, timeout=10) as response:
                                raw = response.read(300_000).decode("utf-8", errors="replace")
                                fallback_status = int(response.status)
                                fallback_body = _json_or_text(raw)
                                fallback_headers = dict(response.headers.items())
                        except urllib.error.HTTPError as exc:
                            raw = exc.read(300_000).decode("utf-8", errors="replace") if exc.fp else ""
                            fallback_status = int(exc.code)
                            fallback_body = _json_or_text(raw)
                            fallback_headers = dict(exc.headers.items()) if exc.headers else {}
                        except Exception as exc:
                            fallback_status = 0
                            fallback_body = {"error": str(exc)}
                            fallback_headers = {}
                            fallback_error = str(exc)
                        _record_v12_har(
                            method,
                            fallback_url,
                            fallback_status,
                            _redact(fallback_body),
                            actor,
                            (time.time() - fallback_started) * 1000,
                        )
                        event = {
                            "source": "read_observer_collection_fallback",
                            "original_path": path,
                            "original_status": status,
                            "fallback_path": fallback_path,
                            "fallback_status": fallback_status,
                        }
                        if fallback_error:
                            event["fallback_error"] = fallback_error
                        selected, projection = _project_bound_observer_entity(
                            fallback_body,
                            path_template=path_template,
                            bindings=bindings,
                        )
                        if projection:
                            event.update(projection)
                        trace.setdefault("observer_fallback_events", []).append(event)
                        if 200 <= fallback_status < 300 and selected is not None:
                            observer_projection = {
                                **event,
                                "original_response": {
                                    "status_code": status,
                                    "body": _redact(response_body),
                                },
                            }
                            path = fallback_path
                            url = fallback_url
                            status = fallback_status
                            response_body = selected
                            response_headers = fallback_headers
                            break
            if 200 <= status < 300 and not observer_projection:
                selected, projection = _project_bound_observer_entity(
                    response_body,
                    path_template=path_template,
                    bindings=bindings,
                )
                if selected is not None:
                    observer_projection = {
                        **projection,
                        "source": "read_observer_collection_projection",
                        "observer_path": path,
                    }
                    trace.setdefault("observer_projection_events", []).append(dict(observer_projection))
                    response_body = selected
        if not har_recorded:
            _record_v12_har(method, url, status, _redact(response_body), actor, (time.time() - started) * 1000)
        if write_event_id is not None and write_observer is not None:
            write_observer(
                "after",
                {
                    "event_id": write_event_id,
                    "action": action_name,
                    "method": method,
                    "path": path,
                    "status": status,
                    "response_body": response_body,
                    "token": token,
                },
            )
        for field in getattr(step, "extract_from_response", []) or []:
            value = _extract(response_body, str(field))
            if value not in (None, "", [], {}):
                bindings[str(field)] = value
        # When extract asked for orderId/sku/... but the body only exposed id,
        # mirror the primary identity onto those alias fields so later body
        # placeholders like {orderId} bind without a second round-trip.
        if method == "GET" and 200 <= status < 300:
            primary = bindings.get("id")
            if primary not in (None, ""):
                from .real_id_resolver import param_field_candidates

                for field in getattr(step, "extract_from_response", []) or []:
                    field_name = str(field or "").strip()
                    if not field_name or bindings.get(field_name) not in (None, ""):
                        continue
                    aliases = {c.lower() for c in param_field_candidates(field_name)}
                    if "id" in aliases or field_name.lower() in aliases:
                        bindings[field_name] = str(primary)
        # Resolve steps / list GETs: bind all identity fields needed by later
        # path placeholders (sku, orderId, id, ...), not just the first "id".
        if method == "GET" and 200 <= status < 300 and (
            action_name.startswith("resolve_") or "resolve_entity" in action_name or action_name.startswith("observe_")
        ):
            for key, value in bind_entity_fields(response_body, path).items():
                bindings.setdefault(key, value)
        if action_name == "login" and actor and bindings.get("token"):
            actor_tokens[actor] = str(bindings["token"])
        # ── Auto-extract: POST responses that create resources ──
        # If a POST/PUT step succeeded (2xx) and the response contains an "id"
        # field, bind it for subsequent steps.  This enables multi-step flows
        # like: create a source-derived entity → bind its ID → invoke a related operation.
        if method in ("POST", "PUT") and 200 <= status < 300:
            for key, value in bind_entity_fields(response_body, path).items():
                bindings[key] = value
            if isinstance(response_body, dict):
                for auto_field in ("id", "sku", "order_id", "orderId", "order_no", "code"):
                    auto_val = response_body.get(auto_field)
                    if auto_val not in (None, "", [], {}):
                        bindings[str(auto_field)] = str(auto_val)
                        bindings.setdefault("id", str(auto_val))
            # Bootstrap creates name the target path param in the action
            # (bootstrap_create_orderId). Mirror the created id onto that param
            # and its REST aliases so later steps can bind {orderId}/{id}.
            if action_name.startswith("bootstrap_create_"):
                created_param = action_name[len("bootstrap_create_"):].strip()
                created_value = (
                    _extract(response_body, created_param)
                    or _extract(response_body, "id")
                    or _extract(response_body, "uuid")
                    or bindings.get("id")
                )
                if created_param and created_value not in (None, ""):
                    bindings[created_param] = str(created_value)
                    bindings["id"] = str(created_value)
                    from .real_id_resolver import param_field_candidates

                    for alias in param_field_candidates(created_param):
                        bindings[alias] = str(created_value)
            if isinstance(response_body, dict):
                for amount_field in (
                    "payable_amount",
                    "payableAmount",
                    "amount_due",
                    "amountDue",
                    "due_amount",
                    "dueAmount",
                    "total_amount",
                    "totalAmount",
                    "amount",
                ):
                    amount_val = response_body.get(amount_field)
                    if amount_val not in (None, "", [], {}):
                        bindings[amount_field] = amount_val
            # Also honor extract_from_response aliases when the body only has id.
            for field in getattr(step, "extract_from_response", []) or []:
                field_name = str(field)
                if bindings.get(field_name) not in (None, ""):
                    continue
                if bindings.get("id") not in (None, ""):
                    from .real_id_resolver import param_field_candidates

                    if "id" in {c.lower() for c in param_field_candidates(field_name)} or field_name.lower() in {"id", "uuid"}:
                        bindings[field_name] = str(bindings["id"])
        # Filtered extraction: pick items in a GET list whose attribute
        # matches a where= clause, then extract their ids.  This lets a
        # precondition resolver bind a concrete entity id when the state
        # precondition (e.g. status=PAID) is real at runtime.
        step_where = dict(getattr(step, "extract_where", None) or {})
        if step_where and isinstance(response_body, (list, dict)):
            candidates = response_body if isinstance(response_body, list) else [response_body]
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                if not all(str(candidate.get(k)) == str(v) for k, v in step_where.items()):
                    continue
                for field in (getattr(step, "extract_from_response", []) or []):
                    value = _extract(candidate, str(field))
                    if value not in (None, "", [], {}):
                        bindings[str(field)] = value
                        break  # first matching entity
                break  # only need one matching entity
            if not bindings.get("id"):
                trace.setdefault("precondition_not_met", list(trace.get("precondition_not_met", [])))
                trace["precondition_not_met"].append(step_where)
        step_record = {
            "action": getattr(step, "action", ""),
            "method": method,
            "path": path,
            "status": status,
            "request": {"body": _redact(body)} if body else {},
            "response": {"status_code": status, "headers": _redact(response_headers), "body": _redact(response_body)},
            "expected_status": getattr(step, "expected_status", 0),
        }
        if observer_projection:
            step_record["observer_projection"] = observer_projection
        trace["steps"].append(step_record)
    # ── DB evidence: snapshot after all steps and diff against the before state ──
    if _db_verifier is not None:
        try:
            _db_verifier.snapshot_after(_db_tables)
            _db_result = _db_verifier.verify()
            _changed = [
                {
                    "table": d.get("table"),
                    "before_count": d.get("before_count"),
                    "after_count": d.get("after_count"),
                    "added": d.get("added_rows"),
                    "removed": d.get("removed_rows"),
                    "modified": d.get("modified_rows"),
                }
                for d in (_db_result.diffs or [])
                if d.get("checksum_changed") or d.get("added_rows") or d.get("removed_rows") or d.get("modified_rows")
            ]
            trace["db_evidence"] = {
                "status": "captured",
                "db_type": _db_result.db_type,
                "tables_checked": _db_result.tables_checked,
                "any_change": bool(_changed),
                "changed_tables": _changed,
                "duration_ms": _db_result.duration_ms,
            }
        except Exception as _db_exc:
            trace["db_evidence"] = {"status": "unavailable", "reason": f"db_snapshot_verify_failed:{type(_db_exc).__name__}"}

    trace["runtime_binding_summary"] = {
        "event_count": len(trace.get("runtime_binding_events") or []),
        "sources": sorted({
            str(item.get("source") or "")
            for item in (trace.get("runtime_binding_events") or [])
            if isinstance(item, dict) and str(item.get("source") or "")
        }),
        "bound_path_params": sorted({
            str(item.get("path_param") or "")
            for item in (trace.get("runtime_binding_events") or [])
            if isinstance(item, dict) and str(item.get("path_param") or "")
        }),
    }
    return trace


def _replace(value: Any, bindings: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _replace(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, bindings) for item in value]
    if not isinstance(value, str):
        return value
    for key, item in bindings.items():
        value = value.replace("{" + key + "}", str(item))
    if "{" in value and "}" in value:
        from .real_id_resolver import param_field_candidates

        for param in infer_path_params(value):
            token = "{" + param + "}"
            if token not in value:
                continue
            for alias in param_field_candidates(param):
                if alias in bindings and bindings[alias] not in (None, ""):
                    value = value.replace(token, str(bindings[alias]))
                    break
    return value


def _summarize_execution_skip_telemetry(traces: list[tuple[Any, dict[str, Any]]]) -> dict[str, Any]:
    """Aggregate skip/precondition reasons across scenario execution traces."""
    reason_counts: dict[str, int] = {}
    path_binding_misses: dict[str, int] = {}
    empty_write_bodies: dict[str, int] = {}
    blocked_samples: list[dict[str, str]] = []
    sandbox_write_status_counts: dict[str, int] = {}
    cleanup_status_counts: dict[str, int] = {}
    cleanup_failure_reasons: dict[str, int] = {}
    observer_status_counts: dict[str, int] = {}
    scenarios_with_http = 0
    scenarios_blocked = 0
    for _, trace in traces:
        if not isinstance(trace, dict):
            continue
        steps = trace.get("steps") or []
        has_http = any(
            int(step.get("status") or _dict(step.get("response")).get("status_code") or 0) > 0
            for step in steps
            if isinstance(step, dict)
        )
        if has_http:
            scenarios_with_http += 1
        else:
            scenarios_blocked += 1
            errors = [str(item) for item in (trace.get("errors") or []) if str(item).strip()]
            skipped_reasons = [
                str(step.get("skipped_reason") or "").strip()
                for step in steps
                if isinstance(step, dict) and str(step.get("skipped_reason") or "").strip()
            ]
            primary_reason = str(
                trace.get("execution_block_reason")
                or (errors[0] if errors else "")
                or (skipped_reasons[0] if skipped_reasons else "")
                or "no_runtime_receipt"
            )
            if len(blocked_samples) < 20:
                blocked_samples.append({
                    "scenario_id": str(
                        getattr(_, "id", "")
                        or getattr(_, "behavior_slice_id", "")
                        or trace.get("scenario_id")
                        or "?"
                    )[:160],
                    "behavior_slice_id": str(getattr(_, "behavior_slice_id", "") or "")[:160],
                    "reason": primary_reason[:240],
                })
        for err in trace.get("errors") or []:
            key = str(err).split(":", 1)[0]
            reason_counts[key] = reason_counts.get(key, 0) + 1
            if key == "missing_runtime_path_binding":
                detail = str(err).split(":", 1)[1] if ":" in str(err) else "unknown"
                path_binding_misses[detail] = path_binding_misses.get(detail, 0) + 1
        for step in steps:
            if not isinstance(step, dict):
                continue
            skipped = str(step.get("skipped_reason") or "").strip()
            if skipped:
                key = skipped.split(":", 1)[0]
                reason_counts[key] = reason_counts.get(key, 0) + 1
            method = str(step.get("method") or "").upper()
            if method in {"POST", "PUT", "PATCH"} and not skipped:
                req_body = _dict(step.get("request")).get("body")
                if not req_body:
                    target = f"{method} {step.get('path') or '?'}"
                    empty_write_bodies[target] = empty_write_bodies.get(target, 0) + 1
        sandbox = trace.get("sandbox_write") if isinstance(trace.get("sandbox_write"), dict) else {}
        if sandbox:
            sandbox_status = str(sandbox.get("status") or "unknown")
            sandbox_write_status_counts[sandbox_status] = sandbox_write_status_counts.get(sandbox_status, 0) + 1
            cleanup = sandbox.get("cleanup") if isinstance(sandbox.get("cleanup"), dict) else {}
            cleanup_status = str(cleanup.get("status") or "unknown")
            cleanup_status_counts[cleanup_status] = cleanup_status_counts.get(cleanup_status, 0) + 1
            cleanup_error = str(cleanup.get("error") or "").strip()
            if cleanup_error:
                cleanup_failure_reasons[cleanup_error] = cleanup_failure_reasons.get(cleanup_error, 0) + 1
            cleanup_warning = str(cleanup.get("warning") or "").strip()
            if cleanup_warning and cleanup_status in {"failed", "not_reversible", "cleanup_incomplete"}:
                cleanup_failure_reasons[cleanup_warning] = cleanup_failure_reasons.get(cleanup_warning, 0) + 1
            for phase in ("before", "after"):
                observation = sandbox.get(phase) if isinstance(sandbox.get(phase), dict) else {}
                ref = str(observation.get("ref") or "")
                status_key = str(observation.get("status") or 0)
                if "documented_observer_missing" in ref:
                    status_key = "documented_observer_missing"
                key = f"{phase}:{status_key}"
                observer_status_counts[key] = observer_status_counts.get(key, 0) + 1
    return {
        "scenarios_with_http": scenarios_with_http,
        "scenarios_blocked": scenarios_blocked,
        "reason_counts": dict(sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))),
        "path_binding_misses": dict(sorted(path_binding_misses.items(), key=lambda item: (-item[1], item[0]))[:20]),
        "empty_write_bodies": dict(sorted(empty_write_bodies.items(), key=lambda item: (-item[1], item[0]))[:20]),
        "blocked_samples": blocked_samples,
        "sandbox_write_status_counts": dict(sorted(sandbox_write_status_counts.items())),
        "cleanup_status_counts": dict(sorted(cleanup_status_counts.items())),
        "cleanup_failure_reasons": dict(sorted(cleanup_failure_reasons.items(), key=lambda item: (-item[1], item[0]))[:20]),
        "observer_status_counts": dict(sorted(observer_status_counts.items())),
    }


def _fill_path_aliases(path: str, bindings: dict[str, Any]) -> str:
    """Fill remaining path placeholders using alias-compatible bindings.

    Example: path needs ``{sku}`` but only ``id`` was bound from a product list
    that used ``sku`` as the primary key — map via shared identity values.
    """
    if not path_has_placeholders(path) or not bindings:
        return path
    from .real_id_resolver import param_field_candidates

    filled = path
    for param in infer_path_params(path):
        token = "{" + param + "}"
        if token not in filled:
            continue
        if param in bindings and bindings[param] not in (None, ""):
            filled = filled.replace(token, str(bindings[param]))
            continue
        for alias in param_field_candidates(param):
            if alias in bindings and bindings[alias] not in (None, ""):
                filled = filled.replace(token, str(bindings[alias]))
                bindings.setdefault(param, str(bindings[alias]))
                break
    return filled


def _extract(value: Any, field: str) -> Any:
    if not field:
        return None
    current = value
    if "." in field:
        for part in field.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if current is not None:
            return current
    if isinstance(value, dict):
        if field in value:
            return value[field]
        for item in value.values():
            found = _extract(item, field)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _extract(item, field)
            if found is not None:
                return found
    return None


def _json_or_text(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return raw[:5000]


def _count_by(items: list[Any], attr: str) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for item in items:
        result[str(getattr(item, attr, "unknown"))] += 1
    return dict(result)


def _evidence_quality_score(gate_passed: bool, evidence_strength: str, *, full_runtime_receipt: bool = False) -> int:
    """Grade confidence by the strongest evidence layer actually captured.

    Avoids a flat 95 for every finding: HTTP-status-only inferences score
    lower than data-layer-confirmed ones so reviewers can triage honestly.
    Confirmed runtime receipts with reproduction steps pass the customer gate.
    """
    if not gate_passed:
        return 55
    return {
        "runtime_and_db": 95,
        "runtime_before_after": 92,
        "db": 90,
        "runtime": 92 if full_runtime_receipt else 65,
    }.get(evidence_strength, 92 if full_runtime_receipt else 65)


def _is_harness_support_step(step: dict[str, Any]) -> bool:
    """True for fixture/auth/resolver calls that are not the tested action."""
    action = str(step.get("action") or "").strip().lower()
    path = str(step.get("path") or "").split("?", 1)[0].rstrip("/").lower()
    return (
        action == "login"
        or action.startswith("login_")
        or action.startswith("resolve_")
        or action.startswith("bootstrap_create_")
        or path.endswith("/login")
    )


def _trace_primary_step(trace: dict[str, Any], oracle_result: Any) -> dict[str, Any]:
    steps = trace.get("steps") if isinstance(trace.get("steps"), list) else []
    if not steps:
        return {}
    rule = str(getattr(oracle_result, "violated_rule", "") or "").strip().lower()
    _writes = {"POST", "PUT", "PATCH", "DELETE"}
    # For idempotency/replay violations the primary evidence must be the
    # repeated *write* call (the duplicate that should have been rejected),
    # never a trailing read-only observation appended for state capture.
    if rule in {"non_idempotent", "replay", "idempotency"}:
        write_steps = [
            s
            for s in steps
            if isinstance(s, dict)
            and not _is_harness_support_step(s)
            and str(s.get("method") or "").upper() in _writes
        ]
        if len(write_steps) >= 2:
            return write_steps[-1]
        if write_steps:
            return write_steps[-1]
    # Otherwise prefer the last step whose observed status contradicts the
    # expected status (the actual assertion failure).
    for step in reversed(steps):
        if not isinstance(step, dict) or _is_harness_support_step(step):
            continue
        status = int(step.get("status") or 0)
        expected = int(step.get("expected_status") or 0)
        if expected and status != expected:
            return step
    # Fall back to the last *write* step before any trailing observe read.
    for step in reversed(steps):
        if (
            isinstance(step, dict)
            and not _is_harness_support_step(step)
            and str(step.get("method") or "").upper() in _writes
        ):
            return step
    for step in reversed(steps):
        if isinstance(step, dict) and not _is_harness_support_step(step):
            return step
    return {}


def _oracle_primary_step_gap(step: dict[str, Any], oracle_result: Any) -> str:
    """Ensure an HTTP oracle verdict describes the selected target step.

    This is deliberately a delivery gate in addition to the oracle-level
    support-step filter.  It protects persisted/third-party oracle results and
    future oracle implementations from attaching a bootstrap failure to a
    successful target mutation.
    """
    oracle_name = str(getattr(oracle_result, "oracle_name", "") or "").strip()
    if oracle_name != "HttpStatusOracle":
        return ""
    if not step:
        return "ORACLE_PRIMARY_STEP_MISSING"
    method = str(step.get("method") or "").upper()
    path = str(step.get("path") or "")
    response = step.get("response") if isinstance(step.get("response"), dict) else {}
    status = int(response.get("status_code") or step.get("status") or 0)
    if not method or not path or not status:
        return "ORACLE_PRIMARY_STEP_MISSING"

    rule = str(getattr(oracle_result, "violated_rule", "") or "").strip().lower()
    expected = int(step.get("expected_status") or 0)
    body = response.get("body")
    if rule == "server_5xx" and status < 500:
        return "ORACLE_PRIMARY_STEP_MISMATCH"
    if rule == "expected_status_mismatch" and (not expected or status == expected):
        return "ORACLE_PRIMARY_STEP_MISMATCH"
    if rule == "wrong_create_status" and not (
        method in {"POST", "PUT"} and expected == 201 and status == 204
    ):
        return "ORACLE_PRIMARY_STEP_MISMATCH"
    if rule == "200_with_error" and not (
        status == 200 and isinstance(body, dict) and body.get("ok") is False
    ):
        return "ORACLE_PRIMARY_STEP_MISMATCH"

    actual = str(getattr(oracle_result, "actual", "") or "")
    actual_status_match = re.search(r"\bHTTP\s+(\d{3})\b", actual, flags=re.IGNORECASE)
    if actual_status_match and int(actual_status_match.group(1)) != status:
        return "ORACLE_PRIMARY_STEP_MISMATCH"
    return ""


def _status_confirmation_gap(
    step: dict[str, Any],
    trace: dict[str, Any],
    oracle_result: Any,
) -> str:
    """Require a proven valid control before calling an expected-2xx 4xx a Bug."""
    oracle_name = str(getattr(oracle_result, "oracle_name", "") or "").strip()
    rule = str(getattr(oracle_result, "violated_rule", "") or "").strip().lower()
    if oracle_name != "HttpStatusOracle" or rule != "expected_status_mismatch":
        return ""
    expected = int(step.get("expected_status") or 0)
    response = step.get("response") if isinstance(step.get("response"), dict) else {}
    actual = int(response.get("status_code") or step.get("status") or 0)
    if not (200 <= expected < 300 and 400 <= actual < 500):
        return ""
    validation = trace.get("request_contract_validation")
    if isinstance(validation, dict) and validation.get("valid_success_control") is True:
        return ""
    if _trace_has_valid_success_control(trace, step):
        return ""
    # A 4xx can be caused by missing fixtures, stale identities, incomplete
    # payloads or credentials. It remains a real observation, but without a
    # successful control proving the request contract it is not a customer
    # deliverable defect.
    return "VALID_SUCCESS_CONTROL_REQUIRED"


def _trace_has_valid_success_control(trace: dict[str, Any], failing_step: dict[str, Any]) -> bool:
    """True only when the same endpoint contract already succeeded in-trace."""
    steps = trace.get("steps") if isinstance(trace.get("steps"), list) else []
    failing_id = id(failing_step)
    failing_method = str(failing_step.get("method") or "").upper()
    failing_path = str(failing_step.get("path") or "").split("?", 1)[0]
    if not failing_method or not failing_path:
        return False

    def _control_path_shape(path: str) -> str:
        # Compare contracts by shape so a prior success on the same route with a
        # different concrete id still counts as a valid control (UUID / long int).
        text = str(path or "").split("?", 1)[0]
        text = re.sub(
            r"/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
            "/{id}",
            text,
        )
        text = re.sub(r"/\d{6,}", "/{id}", text)
        return text

    failing_shape = _control_path_shape(failing_path)
    for item in steps:
        if not isinstance(item, dict) or id(item) == failing_id:
            continue
        status = int(
            (item.get("response") or {}).get("status_code")
            if isinstance(item.get("response"), dict)
            else item.get("status")
            or 0
        )
        if not (200 <= status < 300):
            continue
        method = str(item.get("method") or "").upper()
        path = str(item.get("path") or "").split("?", 1)[0]
        # A successful bootstrap on another endpoint proves only that some
        # authentication and fixture operation worked. It does not prove the
        # failing endpoint's payload, permissions or state preconditions.
        action = str(item.get("action") or "").strip().lower()
        if action.startswith("bootstrap_create_"):
            continue
        if method == failing_method and _control_path_shape(path) == failing_shape:
            return True
    return False


def _trace_before_after_snapshot(trace: dict[str, Any], primary_step: dict[str, Any] | None = None) -> dict[str, Any]:
    steps = trace.get("steps") if isinstance(trace.get("steps"), list) else []
    runtime_steps = [step for step in steps if isinstance(step, dict) and isinstance(step.get("response"), dict)]
    if not runtime_steps:
        return {}

    def _snapshot(step: dict[str, Any]) -> dict[str, Any]:
        response = step.get("response") if isinstance(step.get("response"), dict) else {}
        return {
            "action": str(step.get("action") or ""),
            "method": str(step.get("method") or "").upper(),
            "path": str(step.get("path") or ""),
            "status_code": int(response.get("status_code") or step.get("status") or 0),
            "body": response.get("body"),
            "expected_status": int(step.get("expected_status") or 0),
        }

    def _successful_observer_read(step: dict[str, Any]) -> bool:
        response = step.get("response") if isinstance(step.get("response"), dict) else {}
        status_code = int(response.get("status_code") or step.get("status") or 0)
        return (
            str(step.get("method") or "").upper() in {"GET", "HEAD"}
            and not _is_harness_support_step(step)
            and 200 <= status_code < 300
        )

    before_step = runtime_steps[0]
    after_step = runtime_steps[-1]
    if primary_step:
        primary_index = next(
            (index for index, item in enumerate(steps) if isinstance(item, dict) and item is primary_step),
            -1,
        )
        if primary_index >= 0:
            before_candidates = [
                item
                for item in steps[:primary_index]
                if isinstance(item, dict) and isinstance(item.get("response"), dict) and _successful_observer_read(item)
            ]
            after_candidates = [
                item
                for item in steps[primary_index + 1 :]
                if isinstance(item, dict) and isinstance(item.get("response"), dict) and _successful_observer_read(item)
            ]
            before_step = before_candidates[-1] if before_candidates else before_step
            after_step = after_candidates[-1] if after_candidates else primary_step
    return {
        "before": _snapshot(before_step),
        "after": _snapshot(after_step),
    }


def _runtime_contract_evidence_from_snapshot(
    before_after_snapshot: dict[str, Any],
    primary_step: dict[str, Any],
) -> dict[str, Any]:
    """Expose source-bound before/after observations to the contract gate."""

    before = before_after_snapshot.get("before") if isinstance(before_after_snapshot.get("before"), dict) else {}
    after = before_after_snapshot.get("after") if isinstance(before_after_snapshot.get("after"), dict) else {}
    if not after:
        return {}
    after_body = after.get("body")
    after_status = int(after.get("status_code") or 0)
    if not (200 <= after_status < 300) or after_body in (None, {}, []):
        return {}
    evidence: dict[str, Any] = {
        "final_state_observation": after_body,
        "treatment_observation": after,
        "business_effect_observed": True,
    }
    before_body = before.get("body")
    before_status = int(before.get("status_code") or 0)
    if 200 <= before_status < 300 and before_body not in (None, {}, []):
        evidence["control_observation"] = before
    response = primary_step.get("response") if isinstance(primary_step.get("response"), dict) else {}
    primary_status = int(response.get("status_code") or primary_step.get("status") or 0)
    if primary_status:
        evidence["treatment_result"] = {
            "method": str(primary_step.get("method") or "").upper(),
            "path": str(primary_step.get("path") or ""),
            "status_code": primary_status,
            "body": response.get("body"),
        }
    return evidence


def _compact_semantic_text(value: Any, *, max_len: int = 240) -> str:
    """Return a compact, redaction-aware string for customer/evaluator semantics."""

    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(
        r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+",
        r"\1<REDACTED>",
        text,
    )
    text = re.sub(
        r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key)\s*[:=]\s*[^,\s;}\]]+",
        r"\1=<REDACTED>",
        text,
    )
    if max_len > 0 and len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def _semantic_signature_terms(*values: Any, limit: int = 32) -> list[str]:
    """Derive generic defect signature tokens from runtime semantics, not GT."""

    stop_words = {
        "api",
        "http",
        "https",
        "post",
        "get",
        "put",
        "patch",
        "delete",
        "expected",
        "actual",
        "oracle",
        "status",
        "response",
        "request",
        "should",
        "must",
        "with",
        "when",
        "from",
        "that",
        "this",
        "true",
        "false",
    }
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _compact_semantic_text(value, max_len=500).lower()
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text):
            normalized = token.strip("_-")
            if not normalized or normalized in stop_words or normalized in seen:
                continue
            seen.add(normalized)
            terms.append(normalized)
            if len(terms) >= limit:
                return terms
    return terms


def _oracle_semantic_signature(
    scenario: Any,
    oracle_result: Any,
    *,
    method: str,
    path: str,
    actor_label: str,
    status: int,
    assertion: str,
    actual: str,
) -> dict[str, Any]:
    """Preserve the concrete defect meaning carried by a confirmed runtime oracle."""

    oracle_name = _compact_semantic_text(getattr(oracle_result, "oracle_name", "Oracle"), max_len=80)
    scenario_title = _compact_semantic_text(getattr(scenario, "title", ""), max_len=160)
    violated_rule = _compact_semantic_text(getattr(oracle_result, "violated_rule", ""), max_len=160)
    explanation = _compact_semantic_text(getattr(oracle_result, "explanation", ""), max_len=260)
    expected_behavior = _compact_semantic_text(assertion or violated_rule, max_len=220)
    actual_behavior = _compact_semantic_text(actual or (f"HTTP {status}" if status else ""), max_len=220)
    request = f"{method} {path}".strip()
    signature = {
        "oracle_name": oracle_name,
        "scenario_title": scenario_title,
        "request": request,
        "method": method,
        "path": path,
        "actor": _compact_semantic_text(actor_label, max_len=80),
        "response_status": status,
        "expected_behavior": expected_behavior,
        "actual_behavior": actual_behavior,
        "violated_rule": violated_rule,
        "explanation": explanation,
        "defect_signature_terms": _semantic_signature_terms(
            oracle_name,
            scenario_title,
            method,
            path,
            actor_label,
            status,
            expected_behavior,
            actual_behavior,
            violated_rule,
            explanation,
        ),
    }
    return {key: value for key, value in signature.items() if value not in ("", [], 0)}


def _semantic_v12_title(
    scenario: Any,
    oracle_result: Any,
    *,
    method: str,
    path: str,
    assertion: str,
    actual: str,
    status: int,
) -> str:
    oracle_name = _compact_semantic_text(getattr(oracle_result, "oracle_name", "Oracle"), max_len=80) or "Oracle"
    scenario_title = _compact_semantic_text(getattr(scenario, "title", ""), max_len=120)
    request = f"{method} {path}".strip()
    expected_behavior = _compact_semantic_text(
        assertion or getattr(oracle_result, "violated_rule", ""),
        max_len=100,
    )
    actual_behavior = _compact_semantic_text(actual or (f"HTTP {status}" if status else ""), max_len=100)
    parts = [f"[V12 {oracle_name}]"]
    if scenario_title:
        parts.append(scenario_title)
    if request:
        parts.append(request)
    if expected_behavior:
        parts.append(f"expected {expected_behavior}")
    if actual_behavior:
        parts.append(f"actual {actual_behavior}")
    return _compact_semantic_text(" | ".join(parts), max_len=360)


def _semantic_v12_description(
    oracle_result: Any,
    *,
    method: str,
    path: str,
    actor_label: str,
    status: int,
    assertion: str,
    actual: str,
) -> str:
    lines: list[str] = []
    expected_behavior = _compact_semantic_text(assertion, max_len=320)
    actual_behavior = _compact_semantic_text(actual, max_len=320)
    explanation = _compact_semantic_text(getattr(oracle_result, "explanation", ""), max_len=420)
    violated_rule = _compact_semantic_text(getattr(oracle_result, "violated_rule", ""), max_len=180)
    request = f"{method} {path}".strip()
    if explanation:
        lines.append(explanation)
    if expected_behavior:
        lines.append(f"Expected: {expected_behavior}")
    if actual_behavior:
        lines.append(f"Actual: {actual_behavior}")
    if request:
        observed = f"Observed request: {request}"
        if actor_label:
            observed += f" as {actor_label}"
        if status:
            observed += f" -> HTTP {status}"
        lines.append(observed)
    if violated_rule:
        lines.append(f"Violated rule: {violated_rule}")
    return "\n".join(lines)


def _confirmed_oracle_finding(
    scenario: Any,
    trace: dict[str, Any],
    oracle_result: Any,
    evidence: Any,
    *,
    campaign_id: str,
    discovery_round: int,
    base_url: str,
) -> dict[str, Any]:
    step = _trace_primary_step(trace, oracle_result)
    # 主链 6 × 主链 1/5: a finding whose primary evidence step was deliberately
    # blocked by the customer's production-data safety boundary carries NO real
    # evidence (the request was never sent). It must never be claimed as a
    # reproduced/confirmed defect — only as an auditable, blocked candidate.
    _step_blocked = bool(step.get("execution_blocked"))
    _trace_blocked = bool(trace.get("production_data_blocked"))
    safety_boundary_blocked = _step_blocked or _trace_blocked
    safety_boundary_reason = (
        str(step.get("skipped_reason") or "") if _step_blocked
        else str(trace.get("production_data_block_reason") or "")
    )
    path = str(step.get("path") or "")
    method = str(step.get("method") or "").upper()
    response = step.get("response") if isinstance(step.get("response"), dict) else {}
    status = int(response.get("status_code") or step.get("status") or 0)
    actor = str(getattr(scenario, "actor_token", "") or "")
    actor_label = str((getattr(scenario, "actors", []) or ["readonly"])[0] or "readonly")
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    reproduction_steps = [line for line in str(getattr(evidence, "reproduction_steps", "") or "").splitlines() if str(line).strip()]
    if not reproduction_steps and method and path:
        reproduction_steps = [f"{method} {path}"]
    assertion = str(getattr(oracle_result, "expected", "") or "").strip()
    actual = str(getattr(oracle_result, "actual", "") or "").strip()
    target = (base_url.rstrip("/") + path) if base_url and path.startswith("/") else (base_url or path)
    before_after_snapshot = _trace_before_after_snapshot(trace, primary_step=step)
    if not before_after_snapshot and isinstance(trace.get("before_after_snapshot"), dict):
        before_after_snapshot = dict(trace.get("before_after_snapshot") or {})
    runtime_contract_evidence = _runtime_contract_evidence_from_snapshot(before_after_snapshot, step)
    business_invariant_evaluation = (
        dict(trace.get("business_invariant_evaluation") or {})
        if isinstance(trace.get("business_invariant_evaluation"), dict)
        else {}
    )
    db_evidence = dict(trace.get("db_evidence") or {}) if isinstance(trace.get("db_evidence"), dict) else {}
    # Only real DB evidence counts; an "unavailable" marker must not inflate the
    # evidence strength. Accept both canonical shapes: the runtime verifier shape
    # (status == "captured") and an explicit before/after snapshot pair.
    db_status = str(db_evidence.get("status") or "").strip().lower()
    _has_before_after_db = isinstance(db_evidence.get("before_db_snapshot"), dict) and isinstance(
        db_evidence.get("after_db_snapshot"), dict
    )
    db_captured = db_status == "captured" or (_has_before_after_db and db_status != "unavailable")
    status_confirmation_gap = _status_confirmation_gap(step, trace, oracle_result)
    oracle_primary_step_gap = _oracle_primary_step_gap(step, oracle_result)
    evidence_strength = "runtime"
    if before_after_snapshot and db_captured:
        evidence_strength = "runtime_and_db"
    elif before_after_snapshot:
        evidence_strength = "runtime_before_after"
    elif db_captured:
        evidence_strength = "db"
    runtime_confirmable = (
        _scenario_executable(scenario)
        and bool(method and path and status)
        and bool(reproduction_steps)
        and not (trace.get("errors") if isinstance(trace, dict) else [])
        and bool(getattr(evidence, "vote_summary", {}).get("confirmation_threshold_met"))
        # A path that still carries an unresolved {param}/:param placeholder means
        # the probe never bound a real entity id — the request was malformed, so a
        # resulting 4xx/5xx is a probe artifact, not a confirmed target defect.
        and "{" not in path and not re.search(r"/:[A-Za-z_]", path)
        # A declared state precondition (e.g. status=PAID) that could not be
        # satisfied at runtime means the tested transition was never actually
        # exercised from the claimed state — do not confirm on fabricated state.
        and not (trace.get("precondition_not_met") if isinstance(trace, dict) else None)
        # Expected-success 4xx mismatches need a known-valid control. Otherwise
        # they are usually probe/test-data artifacts and must stay candidates.
        and not status_confirmation_gap
        # The oracle violation must be evidenced by the selected target step,
        # never by a failed fixture/bootstrap request elsewhere in the trace.
        and not oracle_primary_step_gap
        # 主链 6 × 主链 1/5: a step blocked by the production-data safety
        # boundary was never executed, so any "violation" derived from its
        # absent response is not a confirmed target defect. Force candidate.
        and not safety_boundary_blocked
    )
    confirmation_status = "confirmed" if runtime_confirmable else "candidate"
    gate_passed = bool(runtime_confirmable)
    if safety_boundary_blocked:
        # Defense in depth: even if a future change drops the implicit errors
        # marker, a blocked step can never become a confirmed defect.
        confirmation_status = "candidate"
        gate_passed = False
    bug_status = "reproduced" if gate_passed else "suspected"
    oracle_tier = str(getattr(oracle_result, "oracle_tier", "") or "").strip()
    oracle_customer_deliverable = getattr(oracle_result, "customer_deliverable", None)
    if oracle_customer_deliverable is False or oracle_tier == "internal_clue":
        # Contract-gated heuristic business oracles must not enter customer delivery.
        confirmation_status = "candidate"
        gate_passed = False
        bug_status = "suspected"
    raw_request = {"method": method, "path": path}
    if actor_label:
        raw_request["actor"] = actor_label
    step_request = step.get("request") if isinstance(step.get("request"), dict) else {}
    if "body" in step_request:
        # The executor already redacts request bodies before placing them on the
        # trace. Preserve that source-bound payload in the evidence receipt so
        # protocol failures from materially different mutations are not merged.
        raw_request["body"] = step_request.get("body")
    raw_response = {"status_code": status, "body": response.get("body")}
    delivery_status = (
        "blocked_safety_boundary"
        if safety_boundary_blocked
        else (
            "clue"
            if oracle_customer_deliverable is False or oracle_tier == "internal_clue"
            else ("defect" if gate_passed else "candidate")
        )
    )
    trace_evidence = trace.get("evidence") if isinstance(trace.get("evidence"), dict) else {}
    contract_observation_keys = (
        "control_succeeded",
        "authorized_control",
        "effect_count",
        "invariant_held",
        "control_observation",
        "treatment_observation",
        "treatment_result",
        "observer_ids",
    )
    semantic_signature = _oracle_semantic_signature(
        scenario,
        oracle_result,
        method=method,
        path=path,
        actor_label=actor_label,
        status=status,
        assertion=assertion,
        actual=actual,
    )
    _contract_keys_present = [key for key in contract_observation_keys if key in trace_evidence]
    if _contract_keys_present:
        semantic_signature["contract_observation_keys"] = _contract_keys_present
    finding = {
        "severity": getattr(oracle_result, "severity", "P1"),
        "title": _semantic_v12_title(
            scenario,
            oracle_result,
            method=method,
            path=path,
            assertion=assertion,
            actual=actual,
            status=status,
        ),
        "category": getattr(scenario, "category", "scenario_flow"),
        "source": "v12_state_graph",
        "description": _semantic_v12_description(
            oracle_result,
            method=method,
            path=path,
            actor_label=actor_label,
            status=status,
            assertion=assertion,
            actual=actual,
        ),
        "confidence_score": float(getattr(oracle_result, "confidence", 0.0) or 0.0),
        "evidence_id": str(getattr(evidence, "evidence_id", "") or ""),
        "oracle": oracle_result.to_dict() if hasattr(oracle_result, "to_dict") else {},
        "behavior_slice_id": getattr(scenario, "behavior_slice_id", ""),
        "discovery_round": discovery_round,
        "campaign_id": campaign_id,
        "source_refs": [
            dict(item)
            for item in (getattr(scenario, "source_refs", []) or [])
            if isinstance(item, dict)
        ],
        "execution_status": "executed",
        "confirmation_status": confirmation_status,
        "gate_passed": gate_passed,
        "bug_status": bug_status,
        "customer_delivery_status": delivery_status,
        "oracle_tier": oracle_tier or ("internal_clue" if delivery_status == "clue" else ""),
        "blocked_by_safety_boundary": safety_boundary_blocked,
        "blocked_reason": safety_boundary_reason if safety_boundary_blocked else "",
        "expected": assertion,
        "actual": actual,
        "semantic_signature": semantic_signature,
        "timestamp": timestamp,
        "failed_assertions": [actual] if actual else [],
        "evidence": {
            "request": f"{method} {path}",
            "response": f"HTTP {status}",
            "assertion": assertion or actual or str(getattr(oracle_result, "violated_rule", "") or "oracle_violation"),
            "semantic_signature": semantic_signature,
            "expected_behavior": semantic_signature.get("expected_behavior", ""),
            "actual_behavior": semantic_signature.get("actual_behavior", ""),
            "defect_signature_terms": semantic_signature.get("defect_signature_terms", []),
            "timestamp": timestamp,
            "target": target,
            "actor": actor_label,
            "reproduction_steps": reproduction_steps,
            "dual_2xx": bool(
                oracle_tier == "internal_clue"
                and (
                    "idempot" in str(getattr(oracle_result, "oracle_name", "") or "").lower()
                    or "concurr" in str(getattr(oracle_result, "oracle_name", "") or "").lower()
                )
            ),
        },
        "raw_evidence": {
            "has_real_evidence": bool(method and path and status),
            "timestamp": timestamp,
            "request_raw": raw_request,
            "response_raw": raw_response,
            "execution_trace": {"evidence_id": str(getattr(evidence, "evidence_id", "") or ""), "layers": list(getattr(evidence, "layers_triggered", []) or [])},
            "db_snapshot": db_evidence if db_evidence else {},
        },
        "reproduction": {
            "method": method,
            "path": path,
            "is_synthetic": False,
            "har_evidence": {"status_code": status, "response_body": response.get("body")},
        },
        "evidence_quality": {
            "level": "validated" if gate_passed else "needs_evidence",
            "score": _evidence_quality_score(
                gate_passed,
                evidence_strength,
                full_runtime_receipt=bool(gate_passed and method and path and status and reproduction_steps),
            ),
            "can_reproduce": bool(gate_passed),
            "evidence_strength": evidence_strength,
        },
        "evidence_status": {
            "semantic_verdict": "SEMANTIC_CONFIRMED" if gate_passed else "SEMANTIC_CANDIDATE",
            "business_evidence_status": "VALIDATED" if gate_passed else "PENDING_EVIDENCE",
            "final_review_status": "VALIDATED_CANDIDATE" if gate_passed else "NEEDS_MORE_EVIDENCE",
            "missing_requirements": [
                gap for gap in (status_confirmation_gap, oracle_primary_step_gap) if gap
            ],
        },
        "final_review_status": "VALIDATED_CANDIDATE" if gate_passed else "NEEDS_MORE_EVIDENCE",
        "business_evidence_status": "VALIDATED" if gate_passed else "PENDING_EVIDENCE",
        "reproduction_steps": reproduction_steps,
        "before_after_snapshot": before_after_snapshot,
        "business_invariant_evaluation": business_invariant_evaluation,
        "db_evidence": db_evidence,
        "evidence_strength": evidence_strength,
    }
    if runtime_contract_evidence:
        finding["evidence"].update(runtime_contract_evidence)
    # Attach sandbox write evidence (before/after/cleanup) when present on the trace.
    sandbox = trace.get("sandbox_write") if isinstance(trace.get("sandbox_write"), dict) else {}
    sandbox_evidence = sandbox.get("evidence") if isinstance(sandbox.get("evidence"), dict) else {}
    # Preserve typed contract observations for the downstream contract-oracle
    # gate.  A runtime State/Permission/Concurrency result is not customer
    # deliverable merely because an HTTP response looked wrong; when the
    # governed observer explicitly records a control or invariant result, keep
    # that fact attached to the finding instead of dropping it at normalization.
    for contract_key in contract_observation_keys:
        if contract_key in trace_evidence:
            finding["evidence"][contract_key] = trace_evidence[contract_key]
    before_ref = str(
        sandbox_evidence.get("before_snapshot_ref")
        or trace_evidence.get("before_snapshot_ref")
        or ""
    )
    after_ref = str(
        sandbox_evidence.get("after_snapshot_ref")
        or trace_evidence.get("after_snapshot_ref")
        or ""
    )
    cleanup = (
        sandbox_evidence.get("cleanup")
        or trace_evidence.get("cleanup")
        or sandbox.get("cleanup")
        or {}
    )
    if isinstance(cleanup, dict) and (before_ref or after_ref or cleanup):
        finding["evidence"]["before_snapshot_ref"] = before_ref
        finding["evidence"]["after_snapshot_ref"] = after_ref
        finding["evidence"]["cleanup"] = {
            "status": str(cleanup.get("status") or ""),
            "receipt_ref": str(cleanup.get("receipt_ref") or ""),
        }
    if actor:
        finding["evidence"]["actor_token_present"] = True
    return finding


def _normalize_ui_execution_findings(
    ui_execution: dict[str, Any] | None,
    *,
    campaign_id: str,
    discovery_round: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    execution = _dict(ui_execution)
    findings: list[dict[str, Any]] = []
    graphs: list[dict[str, Any]] = []
    for result in execution.get("results") if isinstance(execution.get("results"), list) else []:
        if not isinstance(result, dict):
            continue
        bridge_findings = result.get("findings") if isinstance(result.get("findings"), list) else []
        if bridge_findings:
            for item in bridge_findings:
                if not isinstance(item, dict):
                    continue
                normalized = _ui_bridge_finding(
                    item,
                    request_result=result,
                    campaign_id=campaign_id,
                    discovery_round=discovery_round,
                )
                findings.append(normalized)
                graphs.append(_ui_evidence_graph(normalized, result))
            continue
        status = str(result.get("status") or "")
        if status not in {"failed", "blocked"}:
            continue
        normalized = _ui_execution_status_finding(
            result,
            campaign_id=campaign_id,
            discovery_round=discovery_round,
        )
        findings.append(normalized)
        graphs.append(_ui_evidence_graph(normalized, result))
    return findings, graphs


def _normalize_ui_created_data(request_result: dict[str, Any]) -> dict[str, Any]:
    created_data = _dict(request_result.get("created_data"))
    if not created_data:
        return {}
    object_id = str(
        created_data.get("object_id")
        or created_data.get("entity_id")
        or created_data.get("resource_id")
        or created_data.get("id")
        or ""
    ).strip()
    object_type = str(
        created_data.get("object_type")
        or created_data.get("entity")
        or created_data.get("resource_type")
        or created_data.get("type")
        or ""
    ).strip()
    current_url = str(request_result.get("current_url") or request_result.get("start_url") or "").strip()
    object_url = str(created_data.get("object_url") or created_data.get("url") or current_url or "").strip()
    data_scope_ref = str(
        created_data.get("data_scope_ref")
        or created_data.get("scope_ref")
        or (f"{object_type}:{object_id}" if object_type and object_id else "")
    ).strip()
    normalized = dict(created_data)
    if object_id:
        normalized["object_id"] = object_id
    if object_type:
        normalized["object_type"] = object_type
    if data_scope_ref:
        normalized["data_scope_ref"] = data_scope_ref
    if object_url:
        normalized["object_url"] = object_url
    return normalized


def _ui_execution_evidence_payload(request_result: dict[str, Any]) -> dict[str, Any]:
    artifacts = request_result.get("artifacts") if isinstance(request_result.get("artifacts"), list) else []
    artifact_refs: list[str] = []
    artifact_types: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        ref = str(artifact.get("ref") or "").strip()
        if ref and ref not in artifact_refs:
            artifact_refs.append(ref)
        artifact_type = str(artifact.get("artifact_type") or "").strip()
        if artifact_type and artifact_type not in artifact_types:
            artifact_types.append(artifact_type)
    return {
        "request_id": str(request_result.get("request_id") or ""),
        "provider": str(request_result.get("provider") or ""),
        "bridge_provider": str(request_result.get("bridge_provider") or ""),
        "status": str(request_result.get("status") or ""),
        "reason": str(request_result.get("reason") or ""),
        "current_url": str(request_result.get("current_url") or request_result.get("start_url") or ""),
        "artifact_dir": str(request_result.get("artifact_dir") or ""),
        "artifact_refs": artifact_refs,
        "artifact_types": artifact_types,
        "history_count": len(request_result.get("history") if isinstance(request_result.get("history"), list) else []),
        "console_count": len(request_result.get("console") if isinstance(request_result.get("console"), list) else []),
        "network_count": len(request_result.get("network") if isinstance(request_result.get("network"), list) else []),
        "metadata": dict(request_result.get("metadata") or {}) if isinstance(request_result.get("metadata"), dict) else {},
    }


def _ui_bridge_finding(
    item: dict[str, Any],
    *,
    request_result: dict[str, Any],
    campaign_id: str,
    discovery_round: int,
) -> dict[str, Any]:
    finding = dict(item)
    created_data = _normalize_ui_created_data(request_result)
    ui_execution_result = _ui_execution_evidence_payload(request_result)
    finding.setdefault("severity", "P2")
    finding.setdefault("title", f"[UI] {str(request_result.get('title') or request_result.get('request_id') or 'ui_request')[:120]}")
    finding.setdefault("category", "ui_execution")
    finding.setdefault("source", "ui_execution_bridge")
    finding.setdefault("description", str(request_result.get("reason") or "ui_execution_signal"))
    finding.setdefault("confidence_score", 0.7)
    finding.setdefault("campaign_id", campaign_id)
    finding.setdefault("discovery_round", discovery_round)
    finding.setdefault("execution_status", "executed" if str(request_result.get("status") or "") == "executed" else str(request_result.get("status") or "not_executed"))
    finding.setdefault("confirmation_status", "candidate")
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    evidence.setdefault("request", str(request_result.get("task") or request_result.get("title") or "ui_request"))
    evidence.setdefault("response", str(request_result.get("status") or ""))
    evidence.setdefault("target", str(request_result.get("current_url") or request_result.get("start_url") or ""))
    evidence.setdefault("ui_artifacts", request_result.get("artifacts") if isinstance(request_result.get("artifacts"), list) else [])
    evidence.setdefault("reproduction_steps", [str(request_result.get("task") or request_result.get("title") or "ui_request")])
    finding["evidence"] = evidence
    finding.setdefault(
        "raw_evidence",
        {
            "has_real_evidence": bool(
                ui_execution_result.get("artifact_dir")
                or ui_execution_result.get("current_url")
                or ui_execution_result.get("artifact_refs")
                or created_data
            ),
            "ui_execution_result": ui_execution_result,
            "created_data": created_data,
        },
    )
    return finding


def _ui_execution_status_finding(
    request_result: dict[str, Any],
    *,
    campaign_id: str,
    discovery_round: int,
) -> dict[str, Any]:
    status = str(request_result.get("status") or "blocked")
    request_id = str(request_result.get("request_id") or "ui_request")
    title = str(request_result.get("title") or request_id or "ui_request")
    current_url = str(request_result.get("current_url") or request_result.get("start_url") or "")
    severity = "P1" if status == "failed" else "P2"
    created_data = _normalize_ui_created_data(request_result)
    ui_execution_result = _ui_execution_evidence_payload(request_result)
    return {
        "severity": severity,
        "title": f"[UI Execution {status.upper()}] {title[:120]}",
        "category": "ui_execution",
        "source": "ui_execution_adapter",
        "description": str(request_result.get("reason") or f"ui_execution_{status}"),
        "confidence_score": 0.6 if status == "failed" else 0.45,
        "campaign_id": campaign_id,
        "discovery_round": discovery_round,
        "execution_status": status,
        "confirmation_status": "candidate",
        "evidence": {
            "request": str(request_result.get("task") or title),
            "response": status,
            "target": current_url,
            "ui_artifacts": request_result.get("artifacts") if isinstance(request_result.get("artifacts"), list) else [],
            "reproduction_steps": [str(request_result.get("task") or title)],
        },
        "raw_evidence": {
            "has_real_evidence": bool(current_url or ui_execution_result.get("artifact_dir") or ui_execution_result.get("artifact_refs") or created_data),
            "ui_execution_result": ui_execution_result,
            "created_data": created_data,
        },
    }


def _ui_evidence_graph(finding: dict[str, Any], request_result: dict[str, Any]) -> dict[str, Any]:
    request_id = str(request_result.get("request_id") or "ui_request")
    current_url = str(request_result.get("current_url") or request_result.get("start_url") or "")
    history = request_result.get("history") if isinstance(request_result.get("history"), list) else []
    console = request_result.get("console") if isinstance(request_result.get("console"), list) else []
    network = request_result.get("network") if isinstance(request_result.get("network"), list) else []
    return {
        "bug_id": f"UI_{request_id}",
        "title": str(finding.get("title") or request_id),
        "severity": str(finding.get("severity") or "P2"),
        "confidence": float(finding.get("confidence_score") or 0.0),
        "scenario": {
            "id": request_id,
            "category": "ui_execution",
            "title": str(request_result.get("title") or request_id),
            "provider": str(request_result.get("provider") or ""),
            "task": str(request_result.get("task") or ""),
        },
        "request_chain": [{"url": current_url, "task": str(request_result.get("task") or "")}],
        "response_chain": [{"status": str(request_result.get("status") or ""), "reason": str(request_result.get("reason") or "")}],
        "state_diff": {},
        "execution_trace": {
            "current_url": current_url,
            "artifact_dir": str(request_result.get("artifact_dir") or ""),
            "history": history,
            "console": console,
            "network": network,
        },
        "before_snapshot": {},
        "after_snapshot": {},
        "oracle_results": [],
        "reproduction_steps": "\n".join(finding.get("evidence", {}).get("reproduction_steps", []) if isinstance(finding.get("evidence"), dict) else []),
        "evidence_id": str(finding.get("evidence_id") or f"ui_evidence_{request_id}"),
        "layers_triggered": ["UI"],
        "vote_summary": {
            "total_votes": 1,
            "failed_votes": 1,
            "passed_votes": 0,
            "failure_weight": 1.0,
            "total_weight": 1.0,
            "confirmation_threshold_met": False,
        },
    }


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): ("<REDACTED>" if str(key).lower().replace("-", "_") in _SENSITIVE else _redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value[:25]]
    text = str(value)
    return text[:1000] + "…" if len(text) > 1000 else value
