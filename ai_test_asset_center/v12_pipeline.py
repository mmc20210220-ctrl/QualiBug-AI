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
from .real_id_resolver import infer_path_params, normalize_path_placeholders, path_has_placeholders
from .enterprise_project_config import (
    match_production_data_exclusion,
    _load_execution_safety_boundary,
)

_v12_har_entries: list[dict[str, Any]] = []
_SENSITIVE = {"authorization", "token", "password", "secret", "cookie", "api_key", "apikey"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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
        "approved_sandbox_write",
        "runtime_approved",
    }


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
    return {
        "slice_budget": _as_int(os.environ.get("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", budget), 15, 1, 15),
        "round_number": _as_int(os.environ.get("QUALIBUG_DISCOVERY_ROUND", round_number), 1, 1, 12),
        "round_limit": _as_int(os.environ.get("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", round_limit), 8, 1, 12),
    }


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
    profile = _test_profile(project, root)
    login_route = _login_route(catalog)
    login_path = str(login_route.get("path") or "")
    if not login_path:
        return False
    try:
        from .enterprise_pilot_runtime import ordered_test_credentials
        candidates = ordered_test_credentials(profile)
    except Exception:
        credentials = profile.get("test_credentials") if isinstance(profile, dict) else {}
        candidates = [value for value in credentials.values() if isinstance(credentials, dict) and isinstance(value, dict)]
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

def _enrich_coupon_validation_scenarios(scenarios: list[Any], dsn: str) -> None:
    samples = _coupon_validation_samples(dsn)
    for scenario in scenarios:
        if str(getattr(scenario, "entity", "") or "").strip().lower() != "coupon":
            continue
        rule = _coupon_rule_from_scenario(scenario)
        if not rule:
            continue
        sample = dict(samples.get(rule) or {})
        if not sample:
            gaps = [str(item) for item in (getattr(scenario, "evidence_gaps", []) or []) if str(item).strip()]
            if "DB_SAMPLE_DISCOVERY_MISSING" not in gaps:
                gaps.append("DB_SAMPLE_DISCOVERY_MISSING")
            scenario.evidence_gaps = gaps
            scenario.execution_policy = "plan_only_requires_fixture"
            continue
        runtime_hints = dict(getattr(scenario, "runtime_hints", {}) or {})
        runtime_hints["coupon_validation_rule"] = rule
        runtime_hints["coupon_validation_sample"] = sample
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
    manifest, source_issues = _source_manifest_details(context, source_text)
    if not base_url:
        return {
            "status": "plan_only",
            "reason": "runtime_target_missing",
            "approved_base_url": "",
            "source_manifest": manifest,
            "source_issues": source_issues,
        }
    missing = list(source_issues)
    if not str(context.get("scope_id") or "").strip():
        missing.append("CAMPAIGN_SCOPE_MISSING")
    if not str(context.get("environment_ref") or context.get("target_environment") or "").strip():
        missing.append("ENVIRONMENT_REFERENCE_MISSING")
    if missing:
        return {
            "status": "blocked",
            "reason": "runtime_contract_missing",
            "missing_requirements": sorted(set(missing)),
            "approved_base_url": "",
            "source_manifest": manifest,
        }
    return {
        "status": "approved",
        "reason": "",
        "missing_requirements": [],
        "approved_base_url": str(base_url).rstrip("/"),
        "source_manifest": manifest,
    }


def _execution_approval_contract(context: dict[str, Any], campaign: EnterpriseCampaign, base_url: str, root: Path) -> dict[str, Any]:
    """Verify an approval only after the canonical Campaign identity is known."""
    if not base_url:
        return {"status": "not_required", "reason": "runtime_target_missing"}
    approval_id = str(context.get("execution_approval_id") or "").strip()
    execution_mode = str(context.get("execution_mode") or "safe_read_only").strip()
    if not approval_id:
        return {"status": "blocked", "code": "EXECUTION_APPROVAL_MISSING", "execution_mode": execution_mode}
    try:
        from .execution_approvals import verify_execution_approval
        verdict = verify_execution_approval(
            campaign.project_id,
            approval_id,
            root=root,
            campaign_id=campaign.campaign_id,
            scope_id=campaign.scope_id,
            environment_ref=campaign.environment_ref,
            source_hash=campaign.source_hash,
            target_base_url=base_url,
            execution_mode=execution_mode,
        )
    except Exception as exc:
        return {"status": "blocked", "code": f"EXECUTION_APPROVAL_VERIFICATION_ERROR:{type(exc).__name__}", "execution_mode": execution_mode}
    if verdict.get("valid") is not True:
        return {"status": "blocked", "code": str(verdict.get("code") or "EXECUTION_APPROVAL_INVALID"), "execution_mode": execution_mode}
    approval = _dict(verdict.get("approval"))
    return {
        "status": "approved",
        "approval_id": approval_id,
        "approval_hash": str(approval.get("approval_hash") or ""),
        "execution_mode": execution_mode,
        "expires_at_utc": str(approval.get("expires_at_utc") or ""),
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
        return ""
    path = _evidence_chain_path(root, project, evidence_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return ""
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
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            return 0
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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


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


def _entity_primary_slice_rank(item: dict[str, Any], index: int) -> tuple[int, int]:
    kind = str(item.get("kind") or "").strip().lower()
    kind_rank = {
        "transition": 0,
        "invariant": 1,
        "dependency": 2,
        "source_observation": 3,
    }.get(kind, 9)
    return (kind_rank, index)


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

    def sort_key(item: dict[str, Any]) -> tuple[int, float, float, int, str, str]:
        slice_id = str(item.get("slice_id") or "")
        selection_origin = str(item.get("_selection_origin") or "").strip().lower()
        materialized_boost = 1 if selection_origin == "active_slice_fallback_materialized" else 0
        dynamic = scenario_scores.get(slice_id, float("-inf"))
        base = float(item.get("priority") or 0.0)
        kind = str(item.get("kind") or "")
        kind_rank = {"transition": 0, "invariant": 1, "dependency": 2, "source_observation": 3}.get(kind, 9)
        return (
            materialized_boost,
            dynamic,
            base,
            -kind_rank,
            -len(item.get("source_refs") or []),
            0.0,
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
    ranked.sort(
        key=lambda item: (
            -sort_key(item)[0],
            -sort_key(item)[1],
            -sort_key(item)[2],
            sort_key(item)[3],
            sort_key(item)[4],
            str(item.get("entity") or ""),
            str(item.get("slice_id") or ""),
        )
    )
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
    selected = _take_diverse_slice_batch(pending[offset:], budget)
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
    source_manifest, source_issues = _source_manifest_details(context, submitted_api_spec_text)
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
    campaign.slice_budget = min(campaign.slice_budget, settings["slice_budget"], 15)
    campaign.automatic_round_limit = min(campaign.automatic_round_limit, settings["round_limit"], 12)
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
            stmt = str(p.get("statement") or p.get("expression") or p.get("rule") or "").strip()
            if stmt:
                lines.append(f"- {stmt}")
        if lines:
            parts.append("## 企业资料解析出的权限矩阵（驱动越权/未授权访问测试）\n" + "\n".join(lines))

    risks = asset.get("risk_domains") or []
    if isinstance(risks, list):
        lines = []
        for r in risks[:300]:
            if not isinstance(r, dict):
                continue
            stmt = str(r.get("statement") or r.get("description") or r.get("risk_type") or "").strip()
            if stmt:
                lines.append(f"- {stmt}")
        if lines:
            parts.append("## 企业资料解析出的历史风险/历史Bug模式（驱动回归测试）\n" + "\n".join(lines))

    return "\n\n".join(parts)


def run_v12_pipeline(project: str, root: Path, prd_text: str = "", api_spec_text: str = "", db_schema_text: str = "", base_url: str = "", existing_findings: list[dict] | None = None, campaign_context: dict[str, Any] | None = None) -> dict[str, Any]:
    global _v12_har_entries
    _v12_har_entries = []
    started = time.time()
    context = _dict(campaign_context)
    submitted_api_spec_text = api_spec_text
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
        "risk_clues_saved": 0,
        "behavior_slice_ledger": {},
        "campaign": {},
        "runtime_contract": runtime_contract,
    }
    ledger_for_persistence: dict[str, Any] | None = None
    if api_spec_text and not isinstance(api_spec_text, dict):
        try:
            from .universal_api_parser import detect_format, parse_to_openapi
            if detect_format(api_spec_text) not in {"openapi3", "unknown"}:
                normalized = parse_to_openapi(api_spec_text)
                if normalized.get("paths"):
                    api_spec_text = json.dumps(normalized, ensure_ascii=False, default=str)
        except Exception:
            pass
    try:
        graph_started = time.time()
        from .business_state_graph import BusinessStateGraphBuilder
        # 主链 2: fold the structured enterprise knowledge asset (parsed from the
        # customer's uploaded docs) into the planning text so business rules,
        # permission boundaries and historical-bug patterns drive the live test
        # plan. Additive only — when no asset exists the pipeline is unchanged.
        try:
            from .enterprise_knowledge_center import (
                build_enterprise_business_knowledge_asset,
                load_enterprise_business_knowledge_asset,
            )
            _asset = load_enterprise_business_knowledge_asset(project, root)
            if _asset is None:
                _asset = build_enterprise_business_knowledge_asset(project, root)
            if _asset:
                _enrich = _knowledge_asset_planning_text(_asset)
                if _enrich:
                    prd_text = (prd_text or "") + "\n\n" + _enrich
        except Exception as _ka_exc:
            import sys as _sys
            try:
                _sys.stderr.write(f"[v12_knowledge_asset] project={project} enrichment skipped: {_ka_exc}\n")
                _sys.stderr.flush()
            except Exception:
                pass
        builder = BusinessStateGraphBuilder()
        graph_api_doc = submitted_api_spec_text if str(submitted_api_spec_text or "").strip() else api_spec_text
        graphs = builder.build(prd_text, graph_api_doc, db_schema_text)
        behavior_contract = builder.behavior_contract()
        settings = _behavior_slice_settings()
        campaign, campaign_store, campaign_mode = _campaign_context(project, prd_text, api_spec_text, db_schema_text, approved_base_url, settings, context, root, submitted_api_spec_text)
        campaign, campaign_store, campaign_mode = _maybe_start_behavior_contract_rerun(
            project,
            prd_text,
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
            runtime_contract = {**runtime_contract, "execution_approval": approval}
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
            supp = generate_supplementary_slices(root, project, graph_api_doc)
            if supp:
                ranked_behavior_slices = list(ranked_behavior_slices) + supp
                behavior_contract["slices"] = ranked_behavior_slices
                behavior_contract["summary"]["total_slices"] = len(ranked_behavior_slices)
                behavior_contract["summary"]["supplementary_slices"] = len(supp)
        except Exception:
            pass  # Supplementary coverage best-effort; never blocks the scan
        if runtime_contract.get("status") == "approved":
            from .semantic_scenario_generator import SemanticScenarioGenerator

            preview_api_doc = submitted_api_spec_text if str(submitted_api_spec_text or "").strip() else api_spec_text
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
            settings["slice_budget"] = min(settings["slice_budget"], campaign.slice_budget)
            settings["round_limit"] = min(settings["round_limit"], campaign.automatic_round_limit)
            selection = _schedule_behavior_slices(ranked_behavior_slices, settings, history)
        selected_ids = set(selection["selected_slice_ids"])
        result["campaign"] = {**campaign.public_contract(), "campaign_mode": campaign_mode}
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
        if approved_base_url and api_spec_text:
            try:
                from .route_catalog_builder import RouteCatalogBuilder

                catalog = [entry.to_dict() for entry in RouteCatalogBuilder().build(api_spec_text)]
            except Exception:
                catalog = []
        fuzz_started = time.time()
        fuzzer_findings: list[dict[str, Any]] = []
        if approved_base_url and selected_paths and selection["status"] == "planned":
            try:
                from .parameter_fuzzer import ParameterFuzzer
                scoped_catalog = [entry for entry in catalog if str(entry.get("path") or "") in selected_paths]
                fuzzer = ParameterFuzzer(approved_base_url, allow_write=False)
                _login_parameter_fuzzer(fuzzer, catalog, project, root, api_doc=submitted_api_spec_text if str(submitted_api_spec_text or "").strip() else api_spec_text)
                fuzzer_findings = fuzzer.fuzz_all(scoped_catalog, max_variants=6)
                attempted_slice_ids.update(
                    str(item.get("slice_id") or "")
                    for item in selection["selected"]
                    if any(str(path) in selected_paths for path in item.get("endpoints", []))
                )
                for finding in fuzzer_findings:
                    if isinstance(finding, dict):
                        matching = next((item for item in selection["selected"] if str(finding.get("path") or "") in item.get("endpoints", [])), None)
                        if matching:
                            finding.update({"behavior_slice_id": matching["slice_id"], "discovery_round": settings["round_number"], "campaign_id": campaign.campaign_id, "execution_status": "executed", "confirmation_status": "candidate"})
            except Exception:
                pass
        result["findings"].extend(fuzzer_findings)
        fuzzer_reason = "selected_source_bound_read_routes_only" if selected_paths and approved_base_url else (runtime_contract.get("reason") or "no_selected_source_bound_read_routes")
        result["phases"]["parameter_fuzzer"] = {
            "status": "completed" if approved_base_url and selected_paths and selection["status"] == "planned" else "skipped",
            "reason": fuzzer_reason,
            "findings": len(fuzzer_findings),
            "execution_policy": "documented_read_only_only",
            "slice_scoped": True,
            "duration_ms": int((time.time() - fuzz_started) * 1000),
        }
        scenario_started = time.time()
        scenario_api_doc = submitted_api_spec_text if str(submitted_api_spec_text or "").strip() else api_spec_text
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
                if str(getattr(scenario, "execution_policy", "") or "") in {"safe_read_only", "approved_sandbox_write"} and not str(getattr(scenario, "actor_token", "") or ""):
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
        traces: list[tuple[Any, dict[str, Any]]] = []
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
            for scenario in executable:
                try:
                    trace = _execute_scenario(scenario, approved_base_url, max_retries=2, safety_boundary=_safety_boundary)
                    traces.append((scenario, trace))
                    slice_id = str(getattr(scenario, "behavior_slice_id", "") or "").strip()
                    if slice_id:
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
                    # Fail Fast / observable: never swallow an error silently.
                    # Real execution failures (HTTP/timeout) are expected and
                    # counted as `failed`; but a programming error (e.g. a
                    # signature mismatch) must be visible in the logs so it is
                    # not mistaken for a routine execution failure.
                    logger.error(
                        "scenario execution failed scenario=%s type=%s err=%s",
                        getattr(scenario, "behavior_slice_id", "") or getattr(scenario, "id", "?"),
                        type(_exec_err).__name__,
                        str(_exec_err)[:300],
                    )
                    failed += 1
            result["phases"]["execution"] = {
                "status": "completed",
                "executed": len(traces),
                "failed": failed,
                "planned_only": len(plan_only),
                # 主链 5 × 主链 1: observability — how many scenarios were blocked
                # by the customer's production-data safety boundary (never touched).
                "production_data_blocked": sum(1 for _, t in traces if t.get("production_data_blocked")),
                "duration_ms": int((time.time() - execution_started) * 1000),
            }
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
        for scenario, trace in traces:
            for oracle_result in oracle.evaluate(scenario.to_dict(), trace, None):
                if oracle_result.passed:
                    continue
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
        result["phases"]["oracle"] = {"status": "completed", "total_evaluated": len(traces), "violations_found": len(result["findings"]), "duration_ms": int((time.time() - oracle_started) * 1000)}
        # 主链 7: persist every collected evidence chain keyed by its stable
        # evidence_id so it is retrievable for regression (主链 9) & delivery.
        _evidence_chains_saved = 0
        for _eg in result["evidence_graphs"]:
            if isinstance(_eg, dict) and _persist_evidence_chain(root, project, _eg):
                _evidence_chains_saved += 1
        result["phases"]["oracle"]["evidence_chains_saved"] = _evidence_chains_saved
        # 主链 9 Gap B1: persist deliverable confirmed defects keyed by their
        # stable evidence_id so the regression runner can re-verify them.
        result["phases"]["oracle"]["confirmed_findings_saved"] = _persist_confirmed_findings(
            root, project, result["findings"]
        )
        try:
            from .risk_clue_pool import save_risk_clues
            result["risk_clues_saved"] = save_risk_clues(project, root, result["findings"]).get("new_this_scan", 0)
        except Exception:
            pass
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
        result["error"] = str(exc)[:500]
    if ledger_for_persistence is not None and not result.get("error"):
        try:
            _persist_slice_ledger(root, project, ledger_for_persistence)
        except Exception:
            pass
    result["total_duration_ms"] = int((time.time() - started) * 1000)
    result["auto_har"] = _v12_har_report()
    return result


# NOTE: _load_execution_safety_boundary now lives in enterprise_project_config.py
# (single source of truth shared with regression_runner) and is imported above.


def _execute_scenario(scenario: Any, base_url: str, max_retries: int = 2,
                      safety_boundary: dict[str, Any] | None = None) -> dict[str, Any]:
    for attempt in range(max_retries + 1):
        try:
            return __execute_scenario_once(scenario, base_url, safety_boundary=safety_boundary)
        except Exception as exc:
            if attempt < max_retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            return {"scenario_id": getattr(scenario, "id", "?"), "steps": [], "errors": [f"failed_after_retries:{exc}"], "duration_ms": 0}
    return {"scenario_id": "?", "steps": [], "errors": ["unreachable"], "duration_ms": 0}


def __execute_scenario_once(scenario: Any, base_url: str,
                            safety_boundary: dict[str, Any] | None = None) -> dict[str, Any]:
    trace: dict[str, Any] = {"scenario_id": getattr(scenario, "id", "?"), "steps": [], "errors": []}
    bindings: dict[str, Any] = {}

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
        path = str(getattr(step, "api_path", "") or "")
        if not method or not path.startswith("/"):
            trace["errors"].append("invalid_source_bound_step")
            continue
        path, body = _replace(path, bindings), _replace(getattr(step, "body_template", {}) or {}, bindings)
        normalized_path = normalize_path_placeholders(path)
        if path_has_placeholders(normalized_path):
            missing_bindings = infer_path_params(normalized_path)
            reason = f"missing_runtime_path_binding:{','.join(missing_bindings)}" if missing_bindings else "missing_runtime_path_binding"
            trace["errors"].append(reason)
            trace.setdefault("precondition_not_met", list(trace.get("precondition_not_met", [])))
            trace["precondition_not_met"].append({
                "step": getattr(step, "action", ""),
                "path": normalized_path,
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
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body and method not in {"GET", "HEAD"} else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        token, actor = str(getattr(scenario, "actor_token", "") or ""), str(getattr(step, "actor", "") or "")
        # If the scenario didn't carry a pre-issued actor token, fall back to a
        # token captured earlier in this scenario (e.g. from a login step whose
        # extract_from_response=["token"]).  This lets multi-actor permission /
        # isolation probes authenticate as the role they logged in as.
        if not token and isinstance(bindings.get("token"), str) and bindings.get("token"):
            token = str(bindings["token"])
        if token:
            headers["Authorization"] = f"Bearer {token}"
        started, url = time.time(), base_url.rstrip("/") + path
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
        _record_v12_har(method, url, status, _redact(response_body), actor, (time.time() - started) * 1000)
        for field in getattr(step, "extract_from_response", []) or []:
            value = _extract(response_body, str(field))
            if value not in (None, "", [], {}):
                bindings[str(field)] = value
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
        trace["steps"].append({
            "action": getattr(step, "action", ""),
            "method": method,
            "path": path,
            "status": status,
            "request": {"body": _redact(body)} if body else {},
            "response": {"status_code": status, "headers": _redact(response_headers), "body": _redact(response_body)},
            "expected_status": getattr(step, "expected_status", 0),
        })

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
    return value


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


def _evidence_quality_score(gate_passed: bool, evidence_strength: str) -> int:
    """Grade confidence by the strongest evidence layer actually captured.

    Avoids a flat 95 for every finding: HTTP-status-only inferences score
    lower than data-layer-confirmed ones so reviewers can triage honestly.
    """
    if not gate_passed:
        return 55
    return {
        "runtime_and_db": 95,
        "runtime_before_after": 80,
        "db": 78,
        "runtime": 65,
    }.get(evidence_strength, 65)


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
        write_steps = [s for s in steps if isinstance(s, dict) and str(s.get("method") or "").upper() in _writes]
        if len(write_steps) >= 2:
            return write_steps[-1]
        if write_steps:
            return write_steps[-1]
    # Otherwise prefer the last step whose observed status contradicts the
    # expected status (the actual assertion failure).
    for step in reversed(steps):
        if not isinstance(step, dict):
            continue
        status = int(step.get("status") or 0)
        expected = int(step.get("expected_status") or 0)
        if expected and status != expected:
            return step
    # Fall back to the last *write* step before any trailing observe read.
    for step in reversed(steps):
        if isinstance(step, dict) and str(step.get("method") or "").upper() in _writes:
            return step
    return steps[-1] if isinstance(steps[-1], dict) else {}


def _trace_before_after_snapshot(trace: dict[str, Any]) -> dict[str, Any]:
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

    before_step = runtime_steps[0]
    after_step = runtime_steps[-1]
    return {
        "before": _snapshot(before_step),
        "after": _snapshot(after_step),
    }


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
    before_after_snapshot = _trace_before_after_snapshot(trace)
    if not before_after_snapshot and isinstance(trace.get("before_after_snapshot"), dict):
        before_after_snapshot = dict(trace.get("before_after_snapshot") or {})
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
    raw_request = {"method": method, "path": path}
    if actor_label:
        raw_request["actor"] = actor_label
    raw_response = {"status_code": status, "body": response.get("body")}
    finding = {
        "severity": getattr(oracle_result, "severity", "P1"),
        "title": f"[V12 {getattr(oracle_result, 'oracle_name', 'Oracle')}] {getattr(scenario, 'title', '')}",
        "category": getattr(scenario, "category", "scenario_flow"),
        "source": "v12_state_graph",
        "description": str(getattr(oracle_result, "explanation", "") or ""),
        "confidence_score": float(getattr(oracle_result, "confidence", 0.0) or 0.0),
        "evidence_id": str(getattr(evidence, "evidence_id", "") or ""),
        "oracle": oracle_result.to_dict() if hasattr(oracle_result, "to_dict") else {},
        "behavior_slice_id": getattr(scenario, "behavior_slice_id", ""),
        "discovery_round": discovery_round,
        "campaign_id": campaign_id,
        "execution_status": "executed",
        "confirmation_status": confirmation_status,
        "gate_passed": gate_passed,
        "bug_status": bug_status,
        "customer_delivery_status": "blocked_safety_boundary" if safety_boundary_blocked else "defect",
        "blocked_by_safety_boundary": safety_boundary_blocked,
        "blocked_reason": safety_boundary_reason if safety_boundary_blocked else "",
        "expected": assertion,
        "actual": actual,
        "timestamp": timestamp,
        "failed_assertions": [actual] if actual else [],
        "evidence": {
            "request": f"{method} {path}",
            "response": f"HTTP {status}",
            "assertion": assertion or actual or str(getattr(oracle_result, "violated_rule", "") or "oracle_violation"),
            "timestamp": timestamp,
            "target": target,
            "actor": actor_label,
            "reproduction_steps": reproduction_steps,
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
            "score": _evidence_quality_score(gate_passed, evidence_strength),
            "can_reproduce": bool(gate_passed),
            "evidence_strength": evidence_strength,
        },
        "evidence_status": {
            "semantic_verdict": "SEMANTIC_CONFIRMED" if gate_passed else "SEMANTIC_CANDIDATE",
            "business_evidence_status": "VALIDATED" if gate_passed else "PENDING_EVIDENCE",
            "final_review_status": "VALIDATED_CANDIDATE" if gate_passed else "NEEDS_MORE_EVIDENCE",
            "missing_requirements": [],
        },
        "reproduction_steps": reproduction_steps,
        "before_after_snapshot": before_after_snapshot,
        "business_invariant_evaluation": business_invariant_evaluation,
        "db_evidence": db_evidence,
        "evidence_strength": evidence_strength,
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
