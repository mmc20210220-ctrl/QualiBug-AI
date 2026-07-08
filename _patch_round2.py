"""Apply Round 2 patches: multi-DB driver + P1 preflight + source routing."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ── 1. v12_pipeline.py: replace _coupon_validation_samples with multi-driver version ──

v12_path = ROOT / "ai_test_asset_center" / "v12_pipeline.py"
v12_text = v12_path.read_text(encoding="utf-8")

old_marker = "def _coupon_validation_samples(dsn: str) -> dict[str, dict[str, Any]]:"
end_marker = "\ndef _enrich_coupon_validation_scenarios"

old_start = v12_text.find(old_marker)
old_end = v12_text.find(end_marker, old_start)
if old_end == -1:
    old_end = v12_text.find("\n\ndef _source_text", old_start)  # fallback

if old_start == -1 or old_end == -1:
    print(f"ERROR: could not find v12 function boundaries: start={old_start}, end={old_end}")
else:
    new_func = '''def _db_dialect_from_dsn(dsn: str) -> str:
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


def _coupon_validation_samples(dsn: str) -> dict[str, dict[str, Any]]:
    """Return DB-discovered coupon samples for validation scenarios.

    Supports PostgreSQL (psycopg2), SQLite (stdlib sqlite3), and MySQL /
    MariaDB / SQL Server / Oracle via pyodbc.  NoSQL databases and
    unsupported schemes return an empty dict gracefully so the scan
    continues with a DB_SAMPLE_DISCOVERY_MISSING gap instead of crashing.
    """
    if not str(dsn or "").strip():
        return {}

    _dsn = str(dsn).strip()
    _dialect = _db_dialect_from_dsn(_dsn)
    if not _dialect:
        return {}  # NoSQL or unrecognized

    conn = None
    _placeholder = "%s"
    _is_sqlite = _dialect == "sqlite"

    if _dialect == "sqlite":
        import sqlite3
        _db_path = _dsn
        for _pfx in ("sqlite:///", "sqlite:"):
            if _db_path.lower().startswith(_pfx):
                _db_path = _db_path[len(_pfx):]
                break
        if not Path(_db_path).exists():
            return {}
        conn = sqlite3.connect(_db_path)
        conn.row_factory = sqlite3.Row
        _placeholder = "?"
    elif _dialect == "postgresql":
        try:
            import psycopg2
            conn = psycopg2.connect(_dsn)
        except Exception:
            return {}
    else:
        try:
            import pyodbc
            conn = pyodbc.connect(_dsn)
            _placeholder = "?"
        except Exception:
            return {}

    if conn is None:
        return {}

    def _now() -> str:
        return "datetime('now')" if _is_sqlite else "NOW()"

    def _nulls_last(order_col: str) -> str:
        if _is_sqlite:
            return f"CASE WHEN {order_col} IS NULL THEN 1 ELSE 0 END, {order_col}"
        return f"{order_col} NULLS LAST"

    try:
        cur = conn.cursor()

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

        def saleable_product(*, excluded_category: str = "") -> dict:
            if excluded_category:
                return one(
                    f"""
                    SELECT sku, category, price, status
                    FROM products
                    WHERE COALESCE(status, '') IN ('ON_SALE', 'ACTIVE')
                      AND COALESCE(price, 0) > 0
                      AND COALESCE(category, '') <> {_placeholder}
                    ORDER BY price DESC, sku ASC
                    LIMIT 1
                    """,
                    (excluded_category,),
                )
            return one(
                f"""
                SELECT sku, category, price, status
                FROM products
                WHERE COALESCE(status, '') IN ('ON_SALE', 'ACTIVE')
                  AND COALESCE(price, 0) > 0
                ORDER BY price DESC, sku ASC
                LIMIT 1
                """
            )

        def quantity_for(min_order_amount, price) -> int:
            import math as _math
            price_value = max(float(price or 0.0), 0.01)
            minimum = max(float(min_order_amount or 0.0), 0.0)
            return max(1, int(_math.ceil(max(minimum, price_value) / price_value)))

        samples: dict = {}
        expired = one(
            f"""
            SELECT code, min_order_amount, category_scope, status, expires_at
            FROM coupons
            WHERE expires_at IS NOT NULL AND expires_at < {_now()}
            ORDER BY expires_at ASC, code ASC
            LIMIT 1
            """
        )
        if expired:
            product = saleable_product()
            if product:
                qty = quantity_for(expired.get("min_order_amount"), product.get("price"))
                samples["expired_coupon_must_be_invalid"] = {
                    "body": _coupon_validation_request(
                        str(expired.get("code") or ""),
                        sku=str(product.get("sku") or ""),
                        price=float(product.get("price") or 0.0),
                        qty=qty,
                    ),
                    "coupon_code": str(expired.get("code") or ""),
                    "coupon_status": str(expired.get("status") or ""),
                    "coupon_expires_at": str(expired.get("expires_at") or ""),
                    "item_sku": str(product.get("sku") or ""),
                    "item_category": str(product.get("category") or ""),
                }

        inactive = one(
            f"""
            SELECT code, min_order_amount, category_scope, status, expires_at
            FROM coupons
            WHERE COALESCE(status, '') <> 'ACTIVE'
            ORDER BY {_nulls_last("expires_at")} ASC, code ASC
            LIMIT 1
            """
        )
        if inactive:
            product = saleable_product()
            if product:
                qty = quantity_for(inactive.get("min_order_amount"), product.get("price"))
                samples["inactive_coupon_must_be_invalid"] = {
                    "body": _coupon_validation_request(
                        str(inactive.get("code") or ""),
                        sku=str(product.get("sku") or ""),
                        price=float(product.get("price") or 0.0),
                        qty=qty,
                    ),
                    "coupon_code": str(inactive.get("code") or ""),
                    "coupon_status": str(inactive.get("status") or ""),
                    "coupon_expires_at": str(inactive.get("expires_at") or ""),
                    "item_sku": str(product.get("sku") or ""),
                    "item_category": str(product.get("category") or ""),
                }

        mismatched_category = one(
            f"""
            SELECT code, min_order_amount, category_scope, status, expires_at
            FROM coupons
            WHERE COALESCE(status, '') = 'ACTIVE'
              AND category_scope IS NOT NULL
              AND (expires_at IS NULL OR expires_at >= {_now()})
            ORDER BY {_nulls_last("min_order_amount")} DESC, code ASC
            LIMIT 10
            """
        )
        if mismatched_category:
            product = saleable_product(excluded_category=str(mismatched_category.get("category_scope") or ""))
            if product:
                qty = quantity_for(mismatched_category.get("min_order_amount"), product.get("price"))
                samples["coupon_category_scope_must_match"] = {
                    "body": _coupon_validation_request(
                        str(mismatched_category.get("code") or ""),
                        sku=str(product.get("sku") or ""),
                        price=float(product.get("price") or 0.0),
                        qty=qty,
                    ),
                    "coupon_code": str(mismatched_category.get("code") or ""),
                    "coupon_status": str(mismatched_category.get("status") or ""),
                    "coupon_expires_at": str(mismatched_category.get("expires_at") or ""),
                    "coupon_category_scope": str(mismatched_category.get("category_scope") or ""),
                    "item_sku": str(product.get("sku") or ""),
                    "item_category": str(product.get("category") or ""),
                }
        return samples
    except Exception:
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass
'''
    v12_new = v12_text[:old_start] + new_func + v12_text[old_end:]
    v12_path.write_text(v12_new, encoding="utf-8")
    print("v12_pipeline.py: updated")

# ── 2. __main__.py: _load_registered_source prefers OpenAPI sources ──

main_path = ROOT / "ai_test_asset_center" / "__main__.py"
main_text = main_path.read_text(encoding="utf-8")

old_func = '''def _load_registered_source(project: str, root: Path, context: dict[str, Any]) -> str:
    manifest = _as_dict(context.get("source_manifest"))
    source_hash = str(manifest.get("source_hash") or "").strip().lower().removeprefix("sha256:")
    try:
        from .enterprise_source_registry import SourceRegistryError, list_source_assets, load_source_content
        if not _SHA256_RE.fullmatch(source_hash):
            assets = list_source_assets(project, root=root)
            latest = max(
                (
                    item
                    for item in assets
                    if isinstance(item, dict) and _SHA256_RE.fullmatch(str(item.get("latest_source_hash") or "").strip().lower())
                ),
                key=lambda item: (str(item.get("updated_at_utc") or ""), str(item.get("source_id") or "")),
                default={},
            )'''

new_func = '''def _load_registered_source(project: str, root: Path, context: dict[str, Any]) -> str:
    """Load the best available registered source as API doc text.

    Prefers OpenAPI / Swagger / Postman type sources.  Falls back to any
    registered source when no API spec is available.
    """
    manifest = _as_dict(context.get("source_manifest"))
    source_hash = str(manifest.get("source_hash") or "").strip().lower().removeprefix("sha256:")
    try:
        from .enterprise_source_registry import SourceRegistryError, list_source_assets, load_source_content
        if not _SHA256_RE.fullmatch(source_hash):
            assets = list_source_assets(project, root=root)
            _api_spec_types = {"openapi", "openapi3", "swagger", "postman", "api_spec"}

            def _sort_key(item):
                _type = str(item.get("source_type") or "").strip().lower()
                _is_api = 0 if _type in _api_spec_types else 1
                return (_is_api, str(item.get("updated_at_utc") or ""), str(item.get("source_id") or ""))

            latest = min(
                (
                    item
                    for item in assets
                    if isinstance(item, dict) and _SHA256_RE.fullmatch(str(item.get("latest_source_hash") or "").strip().lower())
                ),
                key=_sort_key,
                default={},
            )'''

if old_func in main_text:
    main_text = main_text.replace(old_func, new_func)
    main_path.write_text(main_text, encoding="utf-8")
    print("__main__.py: _load_registered_source updated")
else:
    print("WARNING: could not find _load_registered_source in __main__.py")

# ── 3. private_pilot_service.py: enhance preflight with source-type awareness ──

svc_path = ROOT / "ai_test_asset_center" / "private_pilot_service.py"
svc_text = svc_path.read_text(encoding="utf-8")

old_preflight = '''        # 2) source ingested?
        try:
            from .enterprise_source_registry import list_source_assets
            _assets = list_source_assets(project, root=root)
        except Exception:
            _assets = []
        if not _assets:
            reasons.append({"code": "NO_SOURCE", "message": "尚未入库任何资料（PRD / OpenAPI 等），请先上传。"})'''

new_preflight = '''        # 2) source ingested -- with type-awareness so the UI can tell the
        #    customer WHY the scan might still fail even with sources present.
        _assets: list = []
        try:
            from .enterprise_source_registry import list_source_assets
            _assets = list(list_source_assets(project, root=root))
        except Exception:
            _assets = []
        if not _assets:
            reasons.append({"code": "NO_SOURCE", "message": "尚未入库任何资料（PRD / OpenAPI 等），请先上传。"})
        else:
            _source_types = {str(a.get("source_type") or "").strip().lower() for a in _assets}
            _has_openapi = bool(_source_types & {"openapi", "openapi3", "swagger", "postman", "api_spec"})
            if not _has_openapi:
                reasons.append({
                    "code": "NO_API_SPEC",
                    "message": (
                        "已入库 {} 份资料，但缺少 API 接口规范（OpenAPI / Swagger / Postman）。"
                        "扫描将无法生成可执行的 API 探针，只能产出基于 PRD 的候选线索。"
                        "请上传被测系统的接口文档后再运行。"
                    ).format(len(_assets)),
                })'''

if old_preflight in svc_text:
    svc_text = svc_text.replace(old_preflight, new_preflight)
    svc_path.write_text(svc_text, encoding="utf-8")
    print("private_pilot_service.py: preflight enhanced")
else:
    print("WARNING: could not find preflight source check in private_pilot_service.py")
    # Try to find it
    idx = svc_text.find("# 2) source ingested?")
    print(f"  Found '# 2) source ingested?' at position {idx}")

print("\nDone. Run tests to verify.")
