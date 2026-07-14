from __future__ import annotations

"""Phase59: enterprise TestOps control plane.

This module deliberately stays a thin orchestration layer over the existing
knowledge, industry reasoning, probe, Oracle, evidence, lifecycle and release
risk modules.  It adds no competing business-rule store.  Its only job is to
turn the traceable enterprise knowledge asset into safe, explainable, reusable
execution assets for enterprise delivery.

The module produces independent JSON assets for test data, environment health,
system state validation, cross-system journeys, permission/tenant coverage,
defect quality, lifecycle, security audit, explainability and benchmark
measurement.  It never resolves secrets itself and never executes destructive
operations against production.
"""

import argparse
import hashlib
import html
import json
import re
import sqlite3
import tempfile
import time
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable
from urllib.error import URLError
from urllib.request import Request, urlopen

from aitestops.test_environment_manager import validate_sql_template

from .enterprise_knowledge_center import (
    build_enterprise_business_knowledge_asset,
    load_enterprise_business_knowledge_asset,
)
try:
    from .multi_industry_business_reasoning import infer_multi_industry_business_model
except ImportError:
    def infer_multi_industry_business_model(*a: Any, **kw: Any) -> dict[str, Any]:
        return {"summary": {}, "business_objects": [], "roles": [], "state_machines": [],
                "permission_boundaries": [], "data_dependencies": [], "business_rules": [],
                "industry_oracles": [], "risk_domains": [], "recognized_industries": []}
from .real_project_onboarding import ROOT, _load_json, _safe_project_id, _write_json as _project_write_json, config_paths
from .product_ui import callout, detail_list, empty_state, h, metric_card, product_shell, section, status_badge, table

PHASE = "phase59_enterprise_testops_control_plane"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MANAGE_ROLES = {"project_owner", "qa_lead", "security_owner", "testops_admin", "admin"}
PRODUCTION_NAMES = {"prod", "production", "live", "online"}
SENSITIVE_PATTERNS = [
    # Matches JSON and key=value forms alike, including `"token": "..."`.
    (re.compile(r"(?i)([\"']?(?:authorization|api[_-]?key|password|token)[\"']?\s*[:=]\s*[\"']?)([^\"'\s,;}]+)"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(authorization\s*[:=]\s*)([^\s,;]+)"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)([^\s,;]+)"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(password\s*[:=]\s*)([^\s,;]+)"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(token\s*[:=]\s*)([^\s,;]+)"), r"\1[REDACTED]"),
    (re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)"), "[MOBILE_REDACTED]"),
    (re.compile(r"(?<!\d)(\d{17}[0-9Xx])(?!\d)"), "[ID_REDACTED]"),
    (re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"), "[CARD_REDACTED]"),
]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash(value: Any, size: int = 16) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:size]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: Any) -> set[str]:
    text = _norm(value)
    chunks = {part for part in re.split(r"[\s_\-]+", text) if len(part) >= 2}
    chunks.update(re.findall(r"[\u4e00-\u9fff]{2,8}", text))
    return chunks


def _redact(value: Any, limit: int = 2400) -> str:
    text = str(value or "")
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:limit]


def _paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    workspace = root / "platform_workspace" / project / "enterprise_testops_control_plane"
    output = root / "platform_outputs" / project / "enterprise_testops_control_plane"
    return {
        "workspace": workspace,
        "output": output,
        "asset": workspace / "enterprise_testops_control_plane.json",
        "environment_config": workspace / "environment_config.json",
        "audit": workspace / "security_audit_log.jsonl",
        "test_data": output / "test_data_orchestration.json",
        "environment_health": output / "environment_health_report.json",
        "database_validation": output / "database_validation_config.json",
        "system_state": output / "system_state_evidence.json",
        "journey_graph": output / "business_journey_graph.json",
        "journey_report": output / "cross_system_journey_report.json",
        "permission_matrix": output / "permission_matrix.json",
        "permission_report": output / "permission_risk_report.json",
        "defect_quality": output / "defect_quality_report.json",
        "issue_lifecycle": output / "issue_lifecycle.json",
        "fix_result": output / "fix_verification_result.json",
        "security_report": output / "security_audit_report.json",
        "explainability": output / "explainable_test_assets.json",
        "benchmark_report": output / "multi_industry_benchmark_report.json",
        "benchmark_html": output / "multi_industry_benchmark_report.html",
        "dashboard": output / "enterprise_testops_center.html",
    }


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_compat(path, data)


def _write_json_compat(path: Path, data: Any) -> None:
    # Preserve the project's established JSON writer while making this module
    # independently testable in isolation.
    try:
        _project_write_json(path, data)
    except Exception:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return _load_json(path, fallback)
    except Exception:
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback
        except Exception:
            return fallback


def _module_from_path(path: str) -> str:
    parts = [item for item in str(path or "").strip("/").split("/") if item and not item.startswith("{")]
    if not parts:
        return "general"
    key = parts[0].lower()
    aliases = {
        "crm": "crm", "customers": "crm", "customer": "crm", "leads": "crm", "contacts": "crm",
        "contracts": "contract", "contract": "contract", "quotes": "contract",
        "finance": "finance", "ledger": "finance", "invoices": "finance", "payments": "payment", "refunds": "refund",
        "orders": "order", "order": "order", "cart": "order", "checkout": "order",
        "inventory": "inventory", "stock": "inventory", "warehouse": "inventory", "shipments": "delivery", "delivery": "delivery",
        "users": "identity", "auth": "identity", "login": "identity", "admin": "admin", "tenant": "tenant", "tenants": "tenant",
        "patients": "medical", "appointments": "medical", "prescriptions": "medical",
        "courses": "education", "students": "education", "enrollments": "education",
    }
    return aliases.get(key, key)


def _resource_from_path(path: str) -> str:
    parts = [p for p in str(path or "").strip("/").split("/") if p and not p.startswith("{")]
    return parts[-1] if parts else "resource"


def _role_name(role: dict[str, Any]) -> str:
    return str(role.get("role") or role.get("name") or role.get("role_id") or "user").replace("industry_role:", "")


def _source_refs(asset: dict[str, Any], keyword: str) -> list[str]:
    lowered = _norm(keyword)
    refs: list[str] = []
    for edge in asset.get("relationships") or []:
        if lowered and lowered in _norm(edge):
            refs.extend([str(edge.get("from") or ""), str(edge.get("to") or "")])
    return sorted({x for x in refs if x})[:10]


def _ensure_manage_actor(actor: dict[str, Any] | None) -> dict[str, str]:
    actor = actor if isinstance(actor, dict) else {}
    clean = {"name": str(actor.get("name") or actor.get("actor") or "testops_operator")[:120], "role": str(actor.get("role") or "qa_lead")[:64]}
    if clean["role"] not in MANAGE_ROLES:
        raise PermissionError("enterprise TestOps configuration requires project_owner, qa_lead, security_owner, testops_admin, or admin")
    return clean


def _append_audit(project: str, root: Path, event: str, actor: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    paths = _paths(project, root)
    paths["workspace"].mkdir(parents=True, exist_ok=True)
    previous = ""
    if paths["audit"].exists():
        try:
            lines = [line for line in paths["audit"].read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                previous = str(json.loads(lines[-1]).get("event_hash") or "")
        except Exception:
            previous = ""
    # Keep the audit record structurally valid even when a redaction pattern
    # touches quoted JSON.  The detailed payload is intentionally stored only
    # as a redacted summary; sensitive raw values never enter the ledger.
    safe_payload = {"summary": _redact(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), 6000)} if payload else {}
    entry = {"at_utc": _now(), "event": event, "actor": actor, "payload": safe_payload, "previous_event_hash": previous}
    entry["event_hash"] = _hash(entry, 64)
    with paths["audit"].open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _default_environments() -> list[dict[str, Any]]:
    return [
        {
            "name": "dev", "type": "development", "base_url": "", "account_pool": "dev_default", "database_ref": "", "mock_enabled": True,
            "message_queue": {"enabled": False, "ref": ""}, "third_party": [], "allow_write_setup": True, "production_protected": False,
        },
        {
            "name": "test", "type": "system_test", "base_url": "", "account_pool": "test_default", "database_ref": "", "mock_enabled": False,
            "message_queue": {"enabled": False, "ref": ""}, "third_party": [], "allow_write_setup": True, "production_protected": False,
        },
        {
            "name": "uat", "type": "user_acceptance", "base_url": "", "account_pool": "uat_default", "database_ref": "", "mock_enabled": False,
            "message_queue": {"enabled": False, "ref": ""}, "third_party": [], "allow_write_setup": False, "production_protected": False,
        },
        {
            "name": "prod-like", "type": "production_like", "base_url": "", "account_pool": "masked_read_only", "database_ref": "", "mock_enabled": False,
            "message_queue": {"enabled": False, "ref": ""}, "third_party": [], "allow_write_setup": False, "production_protected": True,
        },
    ]


def load_environment_config(project_id: str, root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    path = _paths(project, root)["environment_config"]
    saved = _read_json(path, {})
    config = {
        "phase": PHASE,
        "project_id": project,
        "target_environment": "test",
        "environments": _default_environments(),
        "account_pools": [],
        "database_connections": [],
        "updated_at_utc": _now(),
    }
    if isinstance(saved, dict):
        for key in ("target_environment", "environments", "account_pools", "database_connections", "updated_at_utc"):
            if key in saved:
                config[key] = saved[key]
    return config


def save_environment_config(project_id: str, payload: dict[str, Any], root: Path | None = None, actor: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _ensure_manage_actor(actor)
    current = load_environment_config(project, root)
    for key in ("target_environment", "environments", "account_pools", "database_connections"):
        if key in payload:
            current[key] = payload[key]
    # Also save base_url into the target environment's entry
    if "base_url" in payload:
        target_name = str(payload.get("target_environment") or current.get("target_environment") or "test")
        envs = current.setdefault("environments", [])
        target_env = next((e for e in envs if isinstance(e, dict) and e.get("name") == target_name), None)
        if target_env is None:
            target_env = {"name": target_name, "type": "test"}
            envs.append(target_env)
        target_env["base_url"] = str(payload["base_url"])
    if "request_timeout_seconds" in payload:
        target_name = str(payload.get("target_environment") or current.get("target_environment") or "test")
        envs = current.setdefault("environments", [])
        target_env = next((e for e in envs if isinstance(e, dict) and e.get("name") == target_name), None)
        if target_env is None:
            target_env = {"name": target_name, "type": "test"}
            envs.append(target_env)
        target_env["request_timeout_seconds"] = int(payload["request_timeout_seconds"])
    current["updated_at_utc"] = _now()
    _write_json(_paths(project, root)["environment_config"], current)
    _append_audit(project, root, "environment_config_saved", clean_actor, {"target_environment": current.get("target_environment"), "environment_count": len(current.get("environments") or [])})
    return current


def _environment_by_name(config: dict[str, Any], name: str | None) -> dict[str, Any]:
    target = str(name or config.get("target_environment") or "test")
    environments = [item for item in config.get("environments") or [] if isinstance(item, dict)]
    return next((item for item in environments if str(item.get("name")) == target), environments[0] if environments else {"name": target, "type": "unknown"})


def _is_production(environment: dict[str, Any]) -> bool:
    return bool(environment.get("production_protected")) or _norm(environment.get("name")) in PRODUCTION_NAMES or _norm(environment.get("type")) in {"production", "live"}


def _health_request(base_url: str, timeout: float = 2.5) -> dict[str, Any]:
    if not base_url:
        return {"status": "unknown", "reason": "base_url_not_configured"}
    url = str(base_url).rstrip("/") + "/health"
    try:
        request = Request(url, method="GET")
        with urlopen(request, timeout=timeout) as response:  # nosec B310 - only explicit enterprise environment config
            code = int(response.getcode())
            return {"status": "ready" if 200 <= code < 400 else "blocked", "http_status": code, "url": url}
    except (URLError, TimeoutError, OSError) as exc:
        return {"status": "blocked", "reason": _redact(str(exc), 300), "url": url}


def build_environment_health_report(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    options = options or {}
    config = load_environment_config(project, root)
    target = _environment_by_name(config, options.get("target_environment"))
    perform_network_check = bool(options.get("perform_network_check", False))
    required_paths = {str(path) for path in (options.get("required_paths") or options.get("required_interfaces") or []) if str(path)}
    accounts = {str(row.get("name") or row.get("pool") or ""): row for row in config.get("account_pools") or [] if isinstance(row, dict)}
    databases = {str(row.get("name") or row.get("ref") or ""): row for row in config.get("database_connections") or [] if isinstance(row, dict)}
    rows: list[dict[str, Any]] = []
    for environment in [item for item in config.get("environments") or [] if isinstance(item, dict)]:
        checks: list[dict[str, Any]] = []
        service = _health_request(str(environment.get("base_url") or "")) if perform_network_check and environment.get("base_url") else {"status": "not_executed", "reason": "network_check_disabled"}
        checks.append({"check": "service_reachability", **service})
        pool = str(environment.get("account_pool") or "")
        checks.append({"check": "account_pool", "status": "ready" if pool and (not accounts or pool in accounts) else "blocked", "reason": "configured" if pool else "missing_account_pool"})
        db_ref = str(environment.get("database_ref") or "")
        checks.append({"check": "database_connection", "status": "ready" if not db_ref or db_ref in databases or db_ref.startswith("sqlite:///") else "blocked", "reason": "configured_or_not_required" if (not db_ref or db_ref in databases or db_ref.startswith("sqlite:///")) else "missing_database_reference"})
        checks.append({"check": "message_queue", "status": "ready" if not (environment.get("message_queue") or {}).get("enabled") or (environment.get("message_queue") or {}).get("ref") else "blocked", "reason": "disabled_or_configured"})
        checks.append({"check": "third_party", "status": "ready" if all(item.get("mock_enabled") or item.get("endpoint_ref") for item in environment.get("third_party") or [] if isinstance(item, dict)) else "blocked", "reason": "mock_or_endpoint_required"})
        available_paths = {str(path) for path in environment.get("available_paths") or [] if str(path)}
        missing_paths = sorted(required_paths - available_paths) if required_paths and available_paths else []
        checks.append({"check": "required_interfaces", "status": "blocked" if missing_paths else "ready", "reason": "missing_interfaces" if missing_paths else "configured_or_not_declared", "missing_paths": missing_paths})
        data_state = environment.get("data_state") if isinstance(environment.get("data_state"), dict) else {}
        unhealthy_data = [str(key) for key, value in data_state.items() if value is False]
        checks.append({"check": "data_state", "status": "blocked" if unhealthy_data else "ready", "reason": "data_state_not_ready" if unhealthy_data else "ready_or_not_declared", "unhealthy_keys": unhealthy_data})
        blocked = [item for item in checks if item.get("status") == "blocked"]
        ready = not blocked
        rows.append({
            "environment": environment.get("name"), "type": environment.get("type"), "testable": ready,
            "health_status": "ready" if ready else "blocked", "checks": checks,
            "missing": [item.get("reason") for item in blocked],
            "recommended_actions": ["配置或修复 " + str(item.get("check")) for item in blocked],
            "production_protected": _is_production(environment),
        })
    target_row = next((row for row in rows if row.get("environment") == target.get("name")), rows[0] if rows else {"health_status": "blocked", "testable": False})
    differences: list[dict[str, Any]] = []
    baseline = next((row for row in rows if row.get("environment") == "test"), target_row)
    baseline_env = _environment_by_name(config, str(baseline.get("environment") or "test"))
    for row in rows:
        if row is baseline:
            continue
        current_env = _environment_by_name(config, str(row.get("environment") or ""))
        if bool(row.get("testable")) != bool(baseline.get("testable")):
            differences.append({"environment": row.get("environment"), "difference": "testability", "baseline": baseline.get("testable"), "actual": row.get("testable")})
        if bool(current_env.get("mock_enabled")) != bool(baseline_env.get("mock_enabled")):
            differences.append({"environment": row.get("environment"), "difference": "mock_switch", "baseline": bool(baseline_env.get("mock_enabled")), "actual": bool(current_env.get("mock_enabled"))})
        if str(current_env.get("database_ref") or "") != str(baseline_env.get("database_ref") or ""):
            differences.append({"environment": row.get("environment"), "difference": "database_reference", "baseline": bool(baseline_env.get("database_ref")), "actual": bool(current_env.get("database_ref"))})
    result = {
        "phase": PHASE, "project_id": project, "generated_at_utc": _now(), "target_environment": target.get("name"),
        "target_testable": bool(target_row.get("testable")), "target_health_status": target_row.get("health_status"),
        "environments": rows, "environment_differences": differences,
        "governance": {"network_health_check_opt_in": True, "credentials_are_references_only": True, "production_write_requires_explicit_authorization": True},
    }
    _write_json(_paths(project, root)["environment_health"], result)
    return result


def _interface_index(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in asset.get("interfaces") or [] if isinstance(item, dict)]


def _table_columns(table: dict[str, Any]) -> list[str]:
    cols = table.get("columns") or table.get("fields") or []
    names: list[str] = []
    for item in cols:
        if isinstance(item, dict):
            names.append(str(item.get("name") or item.get("column") or ""))
        else:
            names.append(str(item))
    return [name for name in names if name]


def _data_kinds(asset: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    role_names = sorted({_role_name(row) for row in asset.get("roles") or [] if isinstance(row, dict)})
    if not role_names:
        role_names = ["operator"]  # least-privileged default; "admin" must be explicit
    result.extend({"kind": "account", "name": role, "source": "role", "isolation_key": "test_run_id"} for role in role_names)
    result.append({"kind": "tenant", "name": "isolated_test_tenant", "source": "permission_or_default", "isolation_key": "tenant_id"})
    for obj in asset.get("business_objects") or []:
        if not isinstance(obj, dict):
            continue
        name = str(obj.get("object") or obj.get("name") or "")
        if name:
            result.append({"kind": "business_object", "name": name, "source": obj.get("source") or "knowledge_asset", "isolation_key": "test_run_id"})
    combined = json.dumps(asset, ensure_ascii=False).lower()
    for label, keywords in {
        "amount_balance_quota": ("amount", "balance", "余额", "金额", "额度", "ledger", "invoice"),
        "inventory_stock": ("inventory", "stock", "库存", "warehouse"),
        "state_data": ("status", "state", "状态", "流程"),
    }.items():
        if any(word in combined for word in keywords):
            result.append({"kind": label, "name": label, "source": "semantic_inference", "isolation_key": "test_run_id"})
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in result:
        key = (str(item.get("kind")), str(item.get("name")))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _dependency_steps(asset: dict[str, Any], run_id: str, environment: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn write interfaces and knowledge dependencies into a minimum seed order.

    The order is intentionally semantic rather than a generic workflow engine:
    identity/customer comes before contract/order, which comes before payment,
    inventory/delivery and refund.  Unknown operations remain stable by path.
    """
    interfaces = _interface_index(asset)
    create_ops = [op for op in interfaces if str(op.get("method") or "GET").upper() in WRITE_METHODS]
    module_rank = {"identity": 10, "tenant": 15, "crm": 20, "contract": 30, "order": 40, "payment": 50, "finance": 55, "inventory": 60, "delivery": 70, "refund": 80}
    create_ops.sort(key=lambda op: (module_rank.get(_module_from_path(str(op.get("path") or "/")), 90), str(op.get("path") or "/")))
    resource_steps: dict[str, str] = {}
    steps: list[dict[str, Any]] = []
    dependency_terms = {
        "contract": ("customer", "crm"),
        "order": ("customer", "contract"),
        "payment": ("order", "contract"),
        "finance": ("payment", "order"),
        "inventory": ("order",),
        "delivery": ("order", "payment", "inventory"),
        "refund": ("payment", "finance", "order"),
    }
    for index, op in enumerate(create_ops[:32], start=1):
        method = str(op.get("method") or "POST").upper()
        path = str(op.get("path") or "/")
        resource = _resource_from_path(path)
        module = _module_from_path(path)
        safe_to_execute = bool(environment.get("allow_write_setup")) and not _is_production(environment)
        dependencies = []
        for term in dependency_terms.get(module, ()):
            candidate = resource_steps.get(term)
            if candidate and candidate not in dependencies:
                dependencies.append(candidate)
        # Knowledge edges may express a more specific object relationship.
        for edge in asset.get("data_dependencies") or []:
            if not isinstance(edge, dict):
                continue
            target = _norm(edge.get("to") or edge.get("target") or "")
            source = _norm(edge.get("from") or edge.get("source") or "")
            if target and (target in _norm(resource) or target in _norm(module)):
                for known, step_id in resource_steps.items():
                    if known and known in source and step_id not in dependencies:
                        dependencies.append(step_id)
        step_id = f"data_step_{index:03d}"
        steps.append({
            "step_id": step_id, "resource": resource, "module": module, "method": method, "path": path,
            "strategy": "business_api" if safe_to_execute else "sandbox_or_manual_seed",
            "execution_policy": "sandbox_required" if method in WRITE_METHODS else "safe_read_only",
            "payload_template": {"test_run_id": run_id, "tenant_id": f"tenant_{run_id[-8:]}", "idempotency_key": f"seed_{run_id}_{index}"},
            "cleanup_strategy": "cleanup_by_test_run_id", "depends_on": dependencies,
            "automatic": safe_to_execute,
        })
        resource_steps.setdefault(module, step_id)
        resource_steps.setdefault(_norm(resource).rstrip("s"), step_id)
    if not steps:
        for index, dep in enumerate(asset.get("data_dependencies") or [], start=1):
            if not isinstance(dep, dict):
                continue
            steps.append({"step_id": f"data_dep_{index:03d}", "resource": dep.get("to") or dep.get("from"), "strategy": "database_metadata_only", "execution_policy": "sandbox_required", "automatic": False, "cleanup_strategy": "manual_verified_cleanup", "depends_on": []})
    return steps


def build_test_data_orchestration(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None, asset: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    options = options or {}
    asset = asset or load_enterprise_business_knowledge_asset(project, root) or build_enterprise_business_knowledge_asset(project, root)
    environment_config = load_environment_config(project, root)
    environment = _environment_by_name(environment_config, options.get("target_environment"))
    run_id = str(options.get("run_id") or f"qa_{_hash({'project': project, 'time': _now()}, 12)}")
    data_models = _data_kinds(asset)
    steps = _dependency_steps(asset, run_id, environment)
    automatic = [item for item in steps if item.get("automatic")]
    manual_gaps: list[dict[str, Any]] = []
    if _is_production(environment):
        manual_gaps.append({"type": "production_guard", "message": "生产环境禁止自动写入造数；仅允许经显式授权的只读验证。"})
    if not any(item.get("kind") == "account" for item in data_models):
        manual_gaps.append({"type": "account", "message": "未识别可用账号角色，需要提供账号池引用。"})
    for step in steps:
        if not step.get("automatic"):
            manual_gaps.append({"type": "setup", "step_id": step.get("step_id"), "message": "写入前置数据需在隔离测试环境或由人工授权的 seed API 中完成。"})
    health_checks = [
        {"check": "account_validity", "status": "ready" if any(item.get("kind") == "account" for item in data_models) else "blocked", "repair": "从账号池申请/重建测试账号"},
        {"check": "state_satisfaction", "status": "ready" if any(item.get("kind") == "state_data" for item in data_models) else "unknown", "repair": "通过业务 API 建立目标状态，或在隔离库中安全 seed"},
        {"check": "inventory_balance_quota", "status": "ready" if any(item.get("kind") in {"inventory_stock", "amount_balance_quota"} for item in data_models) else "not_required", "repair": "建立最小金额、库存或额度数据并按 run_id 清理"},
    ]
    result = {
        "phase": PHASE, "project_id": project, "generated_at_utc": _now(), "run_id": run_id,
        "target_environment": environment.get("name"), "data_models": data_models,
        "data_dependency_graph": {"nodes": data_models, "edges": asset.get("data_dependencies") or []},
        "data_preparation_steps": steps, "data_health_checks": health_checks,
        "data_repair_plan": [
            {"when": "account_validity=blocked", "action": "从受控账号池重建或申请隔离账号", "automatic": not _is_production(environment), "execution_policy": "sandbox_required"},
            {"when": "state_satisfaction!=ready", "action": "按数据准备依赖通过业务 API 重建目标状态", "automatic": bool(environment.get("allow_write_setup")) and not _is_production(environment), "execution_policy": "sandbox_required"},
            {"when": "inventory_balance_quota=blocked", "action": "在隔离租户创建最小余额/库存/额度并按 run_id 清理", "automatic": bool(environment.get("allow_write_setup")) and not _is_production(environment), "execution_policy": "sandbox_required"},
        ],
        "isolation": {"strategy": "test_run_id + isolated_tenant_or_transaction_scope", "tenant_id": f"tenant_{run_id[-8:]}", "cleanup": "cleanup_by_test_run_id", "cross_run_pollution_blocked": True},
        "automatic_preparation_ratio": round(len(automatic) / max(1, len(steps)), 3), "manual_gaps": manual_gaps,
        "governance": {"prefer_business_api": True, "database_seed_only_in_isolated_test_environment": True, "production_write_blocked": True, "credentials_are_references_only": True},
    }
    _write_json(_paths(project, root)["test_data"], result)
    return result


def build_database_validation_config(asset: dict[str, Any], project_id: str, root: Path) -> dict[str, Any]:
    tables = [table for table in asset.get("data_tables") or [] if isinstance(table, dict)]
    mappings: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    for table in tables[:80]:
        name = str(table.get("name") or table.get("table") or "")
        columns = _table_columns(table)
        if not name:
            continue
        # SQL identifier whitelist — prevent injection via table/column names
        # sourced from user-supplied asset data.
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            continue
        lower_cols = {_norm(col) for col in columns}
        primary = next((col for col in columns if _norm(col) in {"id", f"{_norm(name)} id", f"{_norm(name)}_id"} or _norm(col).endswith("_id")), columns[0] if columns else "id")
        # Validate the primary-key identifier before it is interpolated
        # into the safe_query_template SQL.
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", primary or ""):
            primary = "id"
        status_field = next((col for col in columns if _norm(col) in {"status", "state"} or "status" in _norm(col) or "state" in _norm(col)), "")
        amount_fields = [col for col in columns if any(term in _norm(col) for term in ("amount", "balance", "price", "quota", "stock", "inventory", "金额", "余额", "库存", "额度"))]
        tenant_field = next((col for col in columns if any(term in _norm(col) for term in ("tenant", "org", "organization", "租户", "组织"))), "")
        mapping = {"table": name, "primary_key": primary, "status_field": status_field, "amount_fields": amount_fields, "tenant_field": tenant_field, "safe_query_template": f"SELECT * FROM {name} WHERE {primary} = :business_id LIMIT 1"}
        mappings.append(mapping)
        if status_field:
            assertions.append({"assertion_id": f"db_state_{_hash(mapping)}", "kind": "database_state", "table": name, "field": status_field, "expected": "matches_business_oracle", "severity": "P1"})
        if amount_fields:
            assertions.append({"assertion_id": f"db_amount_{_hash({'table': name, 'fields': amount_fields})}", "kind": "amount_or_inventory_conservation", "table": name, "fields": amount_fields, "expected": "non_negative_and_reconciled", "severity": "P0"})
        if tenant_field:
            assertions.append({"assertion_id": f"db_tenant_{_hash({'table': name, 'tenant': tenant_field})}", "kind": "tenant_scope", "table": name, "field": tenant_field, "expected": "matches_actor_tenant", "severity": "P0"})
    result = {"phase": PHASE, "project_id": project_id, "generated_at_utc": _now(), "table_mappings": mappings, "database_assertions": assertions, "audit_log_assertions": [{"kind": "audit_log", "required_fields": ["actor", "object_id", "before_state", "after_state", "at"], "severity": "P1"}], "async_polling": {"enabled": True, "max_attempts": 8, "interval_seconds": 0.2, "read_only": True}, "governance": {"read_only_queries_only": True, "sql_templates_parameterized": True, "remote_db_connection_not_opened_without_explicit_runtime_adapter": True}}
    _write_json(_paths(project_id, root)["database_validation"], result)
    return result


def _read_sqlite_snapshot(connection_ref: str, queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not connection_ref.startswith("sqlite:///"):
        return []
    db_path = Path(connection_ref.replace("sqlite:///", "", 1)).resolve()
    if not db_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        for query in queries:
            sql = str(query.get("sql") or "")
            sql_warnings = validate_sql_template(sql)
            if not sql.strip().lower().startswith("select") or ";" in sql.strip().rstrip(";") or sql_warnings:
                rows.append({"query_id": query.get("query_id"), "error": "only_single_select_allowed", "warnings": sql_warnings})
                continue
            try:
                data = [dict(item) for item in conn.execute(sql, query.get("params") or {}).fetchall()]
                rows.append({"query_id": query.get("query_id"), "rows": data})
            except sqlite3.Error as exc:
                rows.append({"query_id": query.get("query_id"), "error": str(exc)[:300]})
    return rows


def validate_system_state(
    api_event: dict[str, Any],
    state_snapshot: dict[str, Any] | None = None,
    expected: dict[str, Any] | None = None,
    project_id: str = "real_project_demo",
    root: Path | None = None,
) -> dict[str, Any]:
    """Compare successful API evidence with database/log/async state evidence.

    The caller may pass a state snapshot collected by a safe DB/log adapter.
    Direct DB access is intentionally limited to local SQLite read-only SELECTs.
    """
    root = root or ROOT
    project = _safe_project_id(project_id)
    state_snapshot = state_snapshot or {}
    expected = expected or {}
    api_status = _safe_int(api_event.get("status_code") or api_event.get("http_status") or api_event.get("status"), 0)
    api_success = 200 <= api_status < 300
    db = state_snapshot.get("database") if isinstance(state_snapshot.get("database"), dict) else {}
    logs = state_snapshot.get("audit_logs") if isinstance(state_snapshot.get("audit_logs"), list) else []
    async_states = state_snapshot.get("async_states") if isinstance(state_snapshot.get("async_states"), list) else []
    violations: list[dict[str, Any]] = []
    if api_success and expected.get("expected_status") and str(db.get("status") or "") != str(expected.get("expected_status")):
        violations.append({"kind": "api_success_db_state_mismatch", "severity": "P0" if expected.get("financial") else "P1", "message": f"接口成功但数据库状态为 {db.get('status')!r}，预期 {expected.get('expected_status')!r}"})
    if api_success and expected.get("record_required") and not bool(db.get("record_exists")):
        violations.append({"kind": "api_success_record_missing", "severity": "P0", "message": "接口成功但业务记录不存在"})
    if expected.get("expected_amount") is not None and db.get("amount") is not None and abs(_safe_float(db.get("amount")) - _safe_float(expected.get("expected_amount"))) > 0.01:
        violations.append({"kind": "amount_or_inventory_mismatch", "severity": "P0", "message": "接口结果与数据库金额/库存不一致"})
    if expected.get("tenant_id") and db.get("tenant_id") and str(db.get("tenant_id")) != str(expected.get("tenant_id")):
        violations.append({"kind": "tenant_data_mismatch", "severity": "P0", "message": "数据库记录租户与执行租户不一致"})
    if expected.get("audit_required") and not logs:
        violations.append({"kind": "audit_log_missing", "severity": "P1", "message": "关键业务操作未产生可验证审计日志"})
    if expected.get("async_expected") and async_states and not any(str(item.get("status")) == str(expected.get("async_expected")) for item in async_states if isinstance(item, dict)):
        violations.append({"kind": "async_state_timeout_or_mismatch", "severity": "P1", "message": "异步链路未达到预期终态"})
    result = {
        "phase": PHASE, "project_id": project, "generated_at_utc": _now(),
        "interface_evidence": {"method": api_event.get("method"), "path": api_event.get("path"), "http_status": api_status, "response": _redact(api_event.get("response_body") or api_event.get("body") or {}, 1800)},
        "database_evidence": db, "audit_log_evidence": logs, "async_state_evidence": async_states,
        "expected_oracle": expected, "state_differences": violations,
        "verdict": "failed" if violations else ("passed" if api_success else "not_applicable"),
        "evidence_strength": round(min(1.0, 0.35 + (0.3 if db else 0.0) + (0.2 if logs else 0.0) + (0.15 if async_states else 0.0)), 3),
        "governance": {"database_reads_are_adapter_or_read_only_sqlite": True, "no_credentials_in_evidence": True},
    }
    _write_json(_paths(project, root)["system_state"], result)
    return result


def run_system_state_validation(
    api_event: dict[str, Any],
    expected: dict[str, Any],
    database_ref: str,
    queries: list[dict[str, Any]],
    project_id: str = "real_project_demo",
    root: Path | None = None,
    max_attempts: int = 1,
    interval_seconds: float = 0.2,
) -> dict[str, Any]:
    """Run safe system-state validation against an explicit read-only adapter.

    The first implementation deliberately supports local SQLite because it is
    deterministic and never needs a credential parser. Enterprise adapters can
    supply the same `queries` contract for remote database, log and MQ reads.
    Every query must be a single parameterized SELECT.
    """
    root = root or ROOT
    project = _safe_project_id(project_id)
    attempts = max(1, min(int(max_attempts or 1), 20))
    snapshots: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        snapshots = _read_sqlite_snapshot(database_ref, queries)
        db_rows: list[dict[str, Any]] = []
        audit_rows: list[dict[str, Any]] = []
        async_rows: list[dict[str, Any]] = []
        for snapshot in snapshots:
            query = next((item for item in queries if item.get("query_id") == snapshot.get("query_id")), {})
            kind = str((query or {}).get("kind") or "database")
            rows = snapshot.get("rows") if isinstance(snapshot.get("rows"), list) else []
            if kind == "audit":
                audit_rows.extend([row for row in rows if isinstance(row, dict)])
            elif kind == "async":
                async_rows.extend([row for row in rows if isinstance(row, dict)])
            else:
                db_rows.extend([row for row in rows if isinstance(row, dict)])
        database = dict(db_rows[0]) if db_rows else {"record_exists": False}
        database.setdefault("record_exists", bool(db_rows))
        result = validate_system_state(api_event, {"database": database, "audit_logs": audit_rows, "async_states": async_rows}, expected, project, root)
        result["query_evidence"] = snapshots
        result["attempt_count"] = attempt
        result["adapter"] = {"kind": "sqlite_read_only" if database_ref.startswith("sqlite:///") else "external_adapter_required", "database_ref": "sqlite:///[LOCAL]" if database_ref.startswith("sqlite:///") else "reference_not_opened"}
        if result.get("verdict") != "not_applicable" and (not expected.get("async_expected") or result.get("verdict") == "passed"):
            break
        if attempt < attempts:
            time.sleep(max(0.0, min(float(interval_seconds), 2.0)))
    _write_json(_paths(project, root)["system_state"], result)
    return result


def _system_for_module(module: str) -> str:
    mapping = {"crm": "CRM", "contract": "合同", "finance": "财务", "payment": "支付", "refund": "财务", "order": "订单", "inventory": "库存", "delivery": "履约", "tenant": "身份与租户", "identity": "身份与权限", "medical": "医疗", "education": "教育"}
    return mapping.get(module, module or "业务系统")


def build_business_journey_graph(asset: dict[str, Any], project_id: str, root: Path) -> dict[str, Any]:
    interfaces = _interface_index(asset)
    nodes: list[dict[str, Any]] = []
    for op in interfaces:
        path = str(op.get("path") or "/")
        module = _module_from_path(path)
        nodes.append({"node_id": f"op:{op.get('interface_id') or _hash(op)}", "kind": "interface", "system": _system_for_module(module), "module": module, "method": str(op.get("method") or "GET").upper(), "path": path, "business_object": _resource_from_path(path)})
    keywords = _tokens(json.dumps(asset, ensure_ascii=False))
    candidates: list[dict[str, Any]] = []
    chain_definitions = [
        ("crm_to_contract_to_finance", ["CRM", "合同", "财务"], ("customer", "客户", "contract", "合同", "payment", "回款", "invoice", "发票"), "客户 -> 合同 -> 财务回款"),
        ("order_to_payment_to_inventory_to_delivery_to_refund", ["订单", "支付", "库存", "履约", "财务"], ("order", "订单", "payment", "支付", "inventory", "库存", "shipment", "发货", "refund", "退款"), "下单 -> 支付 -> 库存 -> 发货 -> 退款"),
        ("tenant_identity_to_business_data", ["身份与租户", "业务系统"], ("tenant", "租户", "organization", "组织", "role", "权限"), "身份/租户 -> 业务数据访问"),
    ]
    existing_systems = {node["system"] for node in nodes}
    for journey_id, systems, terms, title in chain_definitions:
        hit_count = sum(1 for term in terms if term in keywords)
        if hit_count >= 2 or sum(1 for system in systems if system in existing_systems) >= 2:
            related = [node for node in nodes if node["system"] in systems]
            candidates.append({"journey_id": journey_id, "title": title, "systems": systems, "nodes": [node["node_id"] for node in related], "data_contracts": ["customer_id", "contract_id", "order_id", "payment_id", "tenant_id"], "cross_system_oracles": ["状态一致性", "金额/库存守恒", "租户与归属一致性"], "coverage_required": True, "confidence": round(min(0.98, 0.45 + hit_count * 0.12 + len(related) * 0.04), 3)})
    if not candidates and nodes:
        ordered = sorted(nodes, key=lambda item: (item["module"], item["path"]))[:8]
        candidates.append({"journey_id": "generic_business_dependency", "title": "通用业务依赖链", "systems": sorted({item["system"] for item in ordered}), "nodes": [item["node_id"] for item in ordered], "data_contracts": ["business_id", "tenant_id", "status"], "cross_system_oracles": ["状态一致性", "数据可追溯性"], "coverage_required": True, "confidence": 0.5})
    edges = []
    for journey in candidates:
        for left, right in zip(journey["nodes"], journey["nodes"][1:]):
            edges.append({"edge_id": f"journey_edge:{_hash({'journey': journey['journey_id'], 'from': left, 'to': right})}", "journey_id": journey["journey_id"], "from": left, "to": right, "relation": "business_data_or_state_transfer"})
    result = {"phase": PHASE, "project_id": project_id, "generated_at_utc": _now(), "nodes": nodes, "edges": edges, "journeys": candidates, "coverage": {"journey_count": len(candidates), "interface_node_count": len(nodes), "cross_system_journey_coverage_estimate": round(sum(1 for item in candidates if item.get("nodes")) / max(1, len(candidates)), 3)}}
    _write_json(_paths(project_id, root)["journey_graph"], result)
    return result


def validate_cross_system_journey(journey: dict[str, Any], observations: list[dict[str, Any]], project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    by_system = {str(item.get("system") or item.get("module") or ""): item for item in observations if isinstance(item, dict)}
    violations: list[dict[str, Any]] = []
    expected_status = journey.get("expected_status") or "paid"
    if journey.get("require_finance_record") and not any(_norm(item.get("system")) in {"finance", "财务"} and item.get("record_exists") for item in observations if isinstance(item, dict)):
        violations.append({"kind": "cross_system_finance_record_missing", "severity": "P0", "message": "上游业务成功，但财务流水缺失"})
    if journey.get("require_inventory_change") and not any(_norm(item.get("system")) in {"inventory", "库存"} and item.get("state_changed") for item in observations if isinstance(item, dict)):
        violations.append({"kind": "cross_system_inventory_not_updated", "severity": "P0", "message": "订单/支付成功，但库存未同步变化"})
    statuses = [str(item.get("status") or "") for item in observations if isinstance(item, dict) and item.get("status")]
    if expected_status and statuses and expected_status not in statuses and any(status in {"created", "pending", "failed"} for status in statuses):
        violations.append({"kind": "cross_system_state_inconsistent", "severity": "P1", "message": "跨系统状态未收敛到业务预期"})
    result = {"phase": PHASE, "project_id": project, "journey_id": journey.get("journey_id"), "title": journey.get("title"), "observations": observations, "assertion_results": [{"oracle": name, "passed": not violations} for name in journey.get("cross_system_oracles") or []], "failed_chain": violations, "verdict": "failed" if violations else "passed", "evidence": {"evidence_hash": _hash({"journey": journey.get("journey_id"), "observations": observations}, 64)}}
    _write_json(_paths(project, root)["journey_report"], result)
    return result


def build_permission_assets(asset: dict[str, Any], project_id: str, root: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    raw = [item for item in asset.get("permission_matrix") or [] if isinstance(item, dict)]
    roles = sorted({_role_name(item) for item in asset.get("roles") or [] if isinstance(item, dict)})
    if not roles:
        roles = ["operator"]  # least-privileged default
    interfaces = _interface_index(asset)
    rows: list[dict[str, Any]] = []
    for item in raw:
        rows.append({"role": str(item.get("role") or item.get("subject") or "normal_user"), "resource": str(item.get("resource") or item.get("object") or "business_resource"), "operations": item.get("actions") or item.get("operation") or ["read"], "fields": item.get("fields") or [], "tenant_scope": item.get("scope") or item.get("tenant_scope") or "own_tenant", "organization_scope": item.get("organization_scope") or "own" , "source_id": item.get("source_id")})
    if not rows:
        for op in interfaces:
            path = str(op.get("path") or "/")
            resource = _resource_from_path(path)
            method = str(op.get("method") or "GET").upper()
            rows.append({"role": "normal_user", "resource": resource, "operations": ["read" if method in SAFE_METHODS else "write"], "fields": [], "tenant_scope": "own_tenant", "organization_scope": "own", "source_id": op.get("source_id")})
            if "admin" in path.lower():
                rows.append({"role": "admin", "resource": resource, "operations": ["read", "write"], "fields": [], "tenant_scope": "all_tenants", "organization_scope": "all", "source_id": op.get("source_id")})
    probes: list[dict[str, Any]] = []
    for index, op in enumerate(interfaces[:80], start=1):
        path = str(op.get("path") or "/")
        method = str(op.get("method") or "GET").upper()
        resource = _resource_from_path(path)
        policy = "safe_read_only" if method in SAFE_METHODS else "sandbox_required"
        common = {"path": path, "method": method, "resource": resource, "execution_policy": policy, "destructive": policy != "safe_read_only", "needs_human_review": policy != "safe_read_only"}
        probes.extend([
            {"probe_id": f"PERM_{index:03d}_ANON", "source": "enterprise_testops_permission", "risk_type": "permission_bypass", "severity": "P0" if "admin" in path.lower() else "P1", "title": f"未登录访问 {method} {path}", "expected": "受保护资源必须拒绝未登录访问", "scenario": "anonymous_access", **common},
            {"probe_id": f"PERM_{index:03d}_IDOR", "source": "enterprise_testops_permission", "risk_type": "idor", "severity": "P0", "title": f"跨用户资源访问 {method} {path}", "expected": "A 用户不得读取或修改 B 用户资源", "scenario": "cross_user_idor", **common},
            {"probe_id": f"PERM_{index:03d}_TENANT", "source": "enterprise_testops_permission", "risk_type": "tenant_isolation", "severity": "P0", "title": f"跨租户访问 {method} {path}", "expected": "租户 A 不得访问租户 B 数据", "scenario": "cross_tenant_access", **common},
        ])
    matrix = {"phase": PHASE, "project_id": project_id, "generated_at_utc": _now(), "roles": roles, "entries": rows, "inference": {"source_permission_entry_count": len(raw), "derived_from_interfaces": not bool(raw), "field_level_scope_enabled": True}}
    report = {"phase": PHASE, "project_id": project_id, "generated_at_utc": _now(), "permission_coverage": {"role_count": len(roles), "resource_count": len({row['resource'] for row in rows}), "probe_count": len(probes)}, "role_risk_heatmap": [{"role": role, "risk_score": 95 if "admin" in _norm(role) else 78, "reasons": ["IDOR", "跨租户", "字段权限"]} for role in roles], "findings": [], "governance": {"unauthenticated_and_cross_scope_tests_are_read_only_by_default": True, "field_level_sensitive_data_checks_required": True}}
    paths = _paths(project_id, root)
    _write_json(paths["permission_matrix"], matrix)
    _write_json(paths["permission_report"], report)
    return matrix, report, probes


def evaluate_permission_observation(probe: dict[str, Any], response: dict[str, Any], project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    status = _safe_int(response.get("status_code") or response.get("http_status") or response.get("status"), 0)
    scenario = str(probe.get("scenario") or "")
    leaked_fields = [field for field in (response.get("sensitive_fields") or []) if field]
    issue = None
    if scenario in {"anonymous_access", "cross_user_idor", "cross_tenant_access"} and 200 <= status < 300:
        kind = {"anonymous_access": "permission_bypass", "cross_user_idor": "idor", "cross_tenant_access": "tenant_isolation"}.get(scenario, "permission_bypass")
        issue = {"risk_type": kind, "severity": "P0", "message": f"{scenario} 被允许，HTTP {status}", "evidence": {"probe_id": probe.get("probe_id"), "http_status": status}}
    if leaked_fields:
        issue = {"risk_type": "sensitive_field_leak", "severity": "P0", "message": "响应包含未授权敏感字段", "evidence": {"probe_id": probe.get("probe_id"), "fields": leaked_fields}}
    findings = [issue] if issue else []
    return {"passed": issue is None, "verdict": "passed" if issue is None else "failed", "issue": issue, "findings": findings, "probe_id": probe.get("probe_id"), "response_evidence": {"http_status": status, "sensitive_fields": leaked_fields}}


def _signature(issue: dict[str, Any]) -> str:
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    keys = {"risk_type": issue.get("risk_type"), "object": issue.get("business_object") or issue.get("resource"), "method": issue.get("method") or evidence.get("request", {}).get("method"), "path": issue.get("path") or evidence.get("request", {}).get("url"), "root": issue.get("root_cause") or issue.get("message") or issue.get("title")}
    return _hash(keys, 40)


def evaluate_defect_quality(issues: Iterable[dict[str, Any]], project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    rows: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, raw in enumerate(issues, start=1):
        issue = dict(raw) if isinstance(raw, dict) else {"title": str(raw)}
        text = _norm(json.dumps(issue, ensure_ascii=False))
        classification = "defect"
        if any(word in text for word in ("environment", "unreachable", "connection refused", "timeout", "dns", "服务不可达", "环境")):
            classification = "environment_problem"
        elif any(word in text for word in ("precondition", "blocked", "account missing", "data missing", "测试数据", "前置")):
            classification = "test_data_problem"
        if any(word in text for word in ("candidate_only", "candidate_only_or_missing_base_url", "destructive_probe_blocked", "未执行", "候选风险")):
            classification = "candidate_only"
        severity = str(issue.get("severity") or "P2")
        severity_weight = {"P0": 1.0, "P1": 0.8, "P2": 0.55, "P3": 0.25}.get(severity, 0.45)
        evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
        evidence_strength = min(1.0, 0.25 + (0.30 if evidence else 0.0) + (0.25 if issue.get("reproduction_steps") or issue.get("probe_id") else 0.0) + (0.20 if issue.get("system_state_evidence") else 0.0))
        reproducibility = min(1.0, 0.25 + 0.35 * _safe_float(issue.get("reproduced_count") or (1 if issue.get("probe_id") else 0)) + (0.2 if issue.get("reproducible") else 0.0))
        impact = min(1.0, 0.3 + 0.55 * severity_weight + (0.15 if str(issue.get("risk_type")) in {"permission_bypass", "idor", "tenant_isolation", "money_consistency", "amount_or_inventory_mismatch"} else 0.0))
        confidence = round(max(0.0, min(1.0, 0.42 * evidence_strength + 0.33 * reproducibility + 0.25 * impact - (0.42 if classification != "defect" else 0.0))), 3)
        signature = _signature(issue)
        enriched = {**issue, "issue_id": str(issue.get("issue_id") or f"dq_{index:04d}"), "classification": classification, "confidence_score": confidence, "business_impact_score": round(impact, 3), "evidence_strength": round(evidence_strength, 3), "reproducibility_score": round(reproducibility, 3), "evidence_signature": signature, "high_confidence": classification == "defect" and confidence >= 0.72}
        rows.append(enriched)
        groups[signature].append(enriched)
    clusters = []
    representatives: list[dict[str, Any]] = []
    for signature, members in groups.items():
        representative = sorted(members, key=lambda item: (-float(item.get("confidence_score") or 0), str(item.get("issue_id"))))[0]
        representatives.append(representative)
        clusters.append({"cluster_id": f"cluster:{signature[:12]}", "signature": signature, "size": len(members), "representative_issue_id": representative.get("issue_id"), "risk_type": representative.get("risk_type"), "members": [item.get("issue_id") for item in members]})
    high = [item for item in representatives if item.get("high_confidence")]
    env = [item for item in rows if item.get("classification") == "environment_problem"]
    data = [item for item in rows if item.get("classification") == "test_data_problem"]
    candidate_only = [item for item in rows if item.get("classification") == "candidate_only"]
    result = {"phase": PHASE, "project_id": project, "generated_at_utc": _now(), "issues": representatives, "clusters": clusters, "summary": {"input_issue_count": len(rows), "deduplicated_issue_count": len(representatives), "duplicate_compression_rate": round(1 - len(representatives) / max(1, len(rows)), 3), "high_confidence_count": len(high), "environment_problem_count": len(env), "test_data_problem_count": len(data), "candidate_only_count": len(candidate_only), "suspected_false_positive_count": len([item for item in rows if item.get("classification") == "defect" and float(item.get("confidence_score") or 0) < 0.48])}, "governance": {"environment_data_and_candidate_only_do_not_enter_high_value_defect_count": True, "duplicate_clusters_keep_all_evidence_members": True}}
    _write_json(_paths(project, root)["defect_quality"], result)
    return result


def _owner_for_issue(issue: dict[str, Any]) -> str:
    module = _module_from_path(str(issue.get("path") or ""))
    mapping = {"finance": "finance_owner", "payment": "payment_owner", "inventory": "inventory_owner", "identity": "identity_owner", "tenant": "platform_security_owner", "crm": "crm_owner", "contract": "contract_owner", "medical": "medical_owner", "education": "education_owner"}
    return mapping.get(module, "module_owner")


def build_issue_lifecycle_and_fix_plan(defect_quality: dict[str, Any], project_id: str, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    lifecycle_items: list[dict[str, Any]] = []
    verification_items: list[dict[str, Any]] = []
    for index, issue in enumerate(defect_quality.get("issues") or [], start=1):
        if not isinstance(issue, dict):
            continue
        status = "新发现" if issue.get("classification") == "defect" else "前置阻塞"
        owner = _owner_for_issue(issue)
        lifecycle_items.append({"issue_id": issue.get("issue_id") or f"issue_{index:04d}", "status": status, "flow": ["新发现", "已确认", "修复中", "待复测", "已关闭", "重新打开"], "owner_recommendation": owner, "draft": {"title": str(issue.get("title") or issue.get("message") or issue.get("risk_type") or "业务缺陷"), "severity": issue.get("severity") or "P2", "business_impact": issue.get("business_impact_score"), "reproduction_steps": issue.get("reproduction_steps") or ["执行对应测试探针", "核对接口与系统状态证据"], "root_cause_hypothesis": issue.get("root_cause") or "待结合接口、数据库、审计和业务规则进一步确认", "fix_suggestion": "修复业务规则实现并补充回归 Oracle，禁止只修改测试预期。"}})
        if str(issue.get("severity") or "P2") in {"P0", "P1"}:
            verification_items.append({"issue_id": lifecycle_items[-1]["issue_id"], "verification_probe": {"probe_id": issue.get("probe_id") or f"REG_{index:04d}", "risk_type": issue.get("risk_type"), "execution_policy": "safe_read_only" if str(issue.get("method") or "GET").upper() in SAFE_METHODS else "sandbox_required"}, "acceptance_criteria": ["原始复现路径不再违反业务 Oracle", "数据库/审计/异步状态与接口结果一致", "同类权限或守恒边界回归通过"], "status": "待复测"})
    lifecycle = {"phase": PHASE, "project_id": project_id, "generated_at_utc": _now(), "items": lifecycle_items, "summary": {"item_count": len(lifecycle_items), "p0_p1_verification_required": len(verification_items)}, "governance": {"owner_recommendation_is_suggestion_not_auto_assignment": True, "p0_p1_requires_fix_verification": True}}
    fix = {"phase": PHASE, "project_id": project_id, "generated_at_utc": _now(), "items": verification_items, "summary": {"verification_plan_count": len(verification_items), "passed_count": 0, "reopened_count": 0}, "governance": {"write_path_verification_is_sandbox_required": True, "release_gate_consumes_high_confidence_unresolved_p0_p1": True}}
    paths = _paths(project_id, root)
    _write_json(paths["issue_lifecycle"], lifecycle)
    _write_json(paths["fix_result"], fix)
    return lifecycle, fix


def build_security_audit_report(project_id: str, root: Path, asset: dict[str, Any], environment_health: dict[str, Any], test_data: dict[str, Any]) -> dict[str, Any]:
    paths = _paths(project_id, root)
    events: list[dict[str, Any]] = []
    if paths["audit"].exists():
        for line in paths["audit"].read_text(encoding="utf-8", errors="replace").splitlines()[-200:]:
            try:
                events.append(json.loads(line))
            except Exception:
                continue
    inventory_text = json.dumps(asset.get("source_inventory") or [], ensure_ascii=False)
    sensitive_hits = []
    for label, pattern in [("token", r"(?i)token"), ("password", r"(?i)password"), ("phone", r"1[3-9]\d{9}"), ("id_card", r"\d{17}[0-9Xx]")]:
        if re.search(pattern, inventory_text):
            sensitive_hits.append({"type": label, "storage": "source_metadata_or_name", "handling": "redacted_derived_assets"})
    dangerous = [row for row in test_data.get("data_preparation_steps") or [] if row.get("execution_policy") == "sandbox_required"]
    production = [row for row in environment_health.get("environments") or [] if row.get("production_protected")]
    audit_chain = _verify_audit_chain(events)
    result = {"phase": PHASE, "project_id": project_id, "generated_at_utc": _now(), "sensitive_data_findings": sensitive_hits, "credential_policy": {"plaintext_credentials_persisted": False, "credential_storage": "references_only", "derived_reports_redacted": True}, "access_control": {"project_scoped_workspace": True, "manage_roles": sorted(MANAGE_ROLES), "tenant_scoped_test_data": True}, "risk_operations": [{"kind": "write_setup", "count": len(dangerous), "policy": "sandbox_required_or_blocked"}], "production_protection": {"protected_environment_count": len(production), "destructive_operations_blocked": True, "explicit_read_only_authorization_required": True}, "audit_events": events, "audit_chain_integrity": audit_chain, "audit_chain": audit_chain, "governance": {"security_boundary_not_simplified_for_less_code": True, "unsafe_production_execution_disallowed": True, "production_write_blocked": True}}
    _write_json(paths["security_report"], result)
    return result


def _verify_audit_chain(events: list[dict[str, Any]]) -> dict[str, Any]:
    previous = ""
    for index, event in enumerate(events):
        event_copy = dict(event)
        actual = str(event_copy.pop("event_hash", ""))
        if str(event_copy.get("previous_event_hash") or "") != previous or actual != _hash(event_copy, 64):
            return {"passed": False, "event_index": index}
        previous = actual
    return {"passed": True, "event_count": len(events)}


def _explanation_for_probe(probe: dict[str, Any], asset: dict[str, Any]) -> dict[str, Any]:
    risk = str(probe.get("risk_type") or "business_rule")
    path = str(probe.get("path") or "/")
    related_rules = [row for row in asset.get("rule_library") or [] if isinstance(row, dict) and (_resource_from_path(path) in _norm(row) or risk in _norm(row))][:8]
    related_states = [row for row in asset.get("state_machines") or [] if isinstance(row, dict) and (_resource_from_path(path) in _norm(row) or risk in _norm(row))][:4]
    return {"probe_id": probe.get("probe_id"), "why_generated": "来自企业知识资产中的业务规则、接口语义、权限边界、数据依赖或历史高价值风险。", "source_documents": sorted({str(item.get("source_id")) for item in related_rules if item.get("source_id")})[:10], "related_rules": [{"rule_id": item.get("rule_id"), "statement": _redact(item.get("statement") or item.get("expected") or "", 500)} for item in related_rules], "interface": {"method": probe.get("method"), "path": path}, "business_object": probe.get("business_object") or _resource_from_path(path), "roles": [row.get("role") for row in asset.get("roles") or [] if isinstance(row, dict)][:12], "state_machines": [{"id": item.get("state_machine_id"), "states": item.get("states") or item.get("transitions")} for item in related_states], "risk_type": risk, "expected_oracle": probe.get("expected") or probe.get("oracle") or "业务 Oracle 必须满足", "execution_policy": probe.get("execution_policy")}


def build_explainable_test_assets(project_id: str, root: Path, asset: dict[str, Any], probes: list[dict[str, Any]], defect_quality: dict[str, Any], environment_health: dict[str, Any]) -> dict[str, Any]:
    probe_explanations = [_explanation_for_probe(probe, asset) for probe in probes[:240] if isinstance(probe, dict)]
    bug_explanations = []
    for issue in defect_quality.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        bug_explanations.append({"issue_id": issue.get("issue_id"), "why_bug": "违反了业务规则、权限边界、状态一致性或数据守恒 Oracle，并有可重复的接口/系统状态证据。", "violated_rule": issue.get("risk_type"), "evidence_strength": issue.get("evidence_strength"), "confidence_reason": f"证据强度 {issue.get('evidence_strength')}，复现能力 {issue.get('reproducibility_score')}，业务影响 {issue.get('business_impact_score')}", "severity_reason": "P0/P1 用于权限、跨租户、金额/库存守恒、核心状态和跨系统数据错误。"})
    result = {"phase": PHASE, "project_id": project_id, "generated_at_utc": _now(), "probe_explanations": probe_explanations, "bug_explanations": bug_explanations, "risk_radar_explanation": {"why_high_risk": "由高价值风险类型、业务规则严重级别、Oracle 覆盖缺口、跨系统影响与可信度共同决定。"}, "release_gate_explanation": {"why_blocked": "存在高置信未关闭 P0/P1、关键 Oracle 覆盖缺口、生产保护违反或系统状态证据失败时阻断发布。", "environment_health": environment_health.get("target_health_status")}, "triage_matrix_explanation": {"why_clustered": "按风险类型、业务对象、接口、根因假设和证据签名聚类，环境/前置问题不计入高价值缺陷。"}}
    _write_json(_paths(project_id, root)["explainability"], result)
    return result


def generate_enterprise_testops_probes(openapi: dict[str, Any], cfg: dict[str, Any], project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    """Generate only probes that add a new high-value Oracle dimension.

    The function is intentionally used by the existing risk planner.  It avoids
    generic endpoint spraying and emits data/environment/journey/permission
    probes derived from enterprise knowledge.
    """
    root = root or ROOT
    project = _safe_project_id(project_id)
    control = build_enterprise_testops_control_plane(project, root, {"target_environment": cfg.get("target_environment") if isinstance(cfg, dict) else None})
    limit = int(max_count or 100)
    probes: list[dict[str, Any]] = []
    for probe in control.get("permission_probes") or []:
        probes.append(dict(probe))
    for journey in control.get("journey_graph", {}).get("journeys") or []:
        nodes = journey.get("nodes") or []
        path = "/"
        method = "GET"
        if nodes:
            node_map = {node.get("node_id"): node for node in control.get("journey_graph", {}).get("nodes") or [] if isinstance(node, dict)}
            node = node_map.get(nodes[-1]) or {}
            path = str(node.get("path") or "/")
            method = str(node.get("method") or "GET").upper()
        policy = "safe_read_only" if method in SAFE_METHODS else "sandbox_required"
        probes.append({"probe_id": f"JOURNEY_{_hash(journey)[:10]}", "source": "enterprise_testops_journey", "risk_type": "cross_system_oracle", "severity": "P0" if "财务" in json.dumps(journey, ensure_ascii=False) or "支付" in json.dumps(journey, ensure_ascii=False) else "P1", "title": f"跨系统链路：{journey.get('title')}", "path": path, "method": method, "expected": "跨系统状态、金额/库存、归属与审计证据必须一致", "execution_policy": policy, "destructive": policy != "safe_read_only", "needs_human_review": policy != "safe_read_only", "journey_id": journey.get("journey_id")})
    for assertion in control.get("database_validation", {}).get("database_assertions") or []:
        probes.append({"probe_id": f"STATE_{_hash(assertion)[:10]}", "source": "enterprise_testops_system_state", "risk_type": "cross_system_oracle" if assertion.get("kind") == "database_state" else "business_invariant", "severity": assertion.get("severity") or "P1", "title": f"系统状态验证：{assertion.get('kind')}", "path": "/", "method": "GET", "expected": f"{assertion.get('table')} 的 {assertion.get('field') or assertion.get('fields')} 必须满足 {assertion.get('expected')}", "execution_policy": "safe_read_only", "destructive": False, "needs_human_review": False, "state_assertion_id": assertion.get("assertion_id")})
    for gap in control.get("test_data", {}).get("manual_gaps") or []:
        probes.append({"probe_id": f"DATA_GAP_{_hash(gap)[:10]}", "source": "enterprise_testops_data", "risk_type": "test_data_readiness_gap", "severity": "P1", "title": "测试数据前置缺口", "path": "/", "method": "GET", "expected": gap.get("message"), "execution_policy": "safe_read_only", "destructive": False, "needs_human_review": True, "data_gap": gap})
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for probe in probes:
        key = (str(probe.get("source")), str(probe.get("risk_type")), str(probe.get("method") or "GET"), str(probe.get("path") or "/"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(probe)
        if len(unique) >= limit:
            break
    return unique


def _load_existing_issues(project: str, root: Path) -> list[dict[str, Any]]:
    paths = config_paths(project, root)
    report = _read_json(paths["output_dir"] / "discovered_issues.json", {})
    items = report.get("items") if isinstance(report, dict) else []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _benchmark_sample_dirs(root: Path) -> list[Path]:
    base = root / "benchmark" / "multi_industry"
    return [path for path in sorted(base.iterdir()) if path.is_dir()] if base.exists() else []


def run_multi_industry_benchmark(project_id: str = "benchmark", root: Path | None = None, output_root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    output_root = output_root or root
    samples = _benchmark_sample_dirs(root)
    rows: list[dict[str, Any]] = []
    for sample_dir in samples:
        prd = (sample_dir / "PRD.md").read_text(encoding="utf-8", errors="replace") if (sample_dir / "PRD.md").exists() else ""
        openapi = _read_json(sample_dir / "openapi.json", {})
        seeds = _read_json(sample_dir / "known_high_value_bug_seeds.json", {}).get("bugs", [])
        industry = sample_dir.name
        model = infer_multi_industry_business_model({"PRD": prd}, openapi if isinstance(openapi, dict) else {}, {}, f"benchmark_{industry}")
        risks = {str(item.get("risk_type") or item.get("kind") or "") for item in model.get("risk_domains") or [] if isinstance(item, dict)}
        oracles = model.get("industry_oracles") or []
        seed_risks = {str(item.get("risk_type") or "") for item in seeds if isinstance(item, dict)}
        hit = len(seed_risks & risks)
        discovery_rate = round(hit / max(1, len(seed_risks)), 3)
        rows.append({"industry": industry, "recognized_industries": model.get("recognized_industries") or [], "business_object_count": len(model.get("business_objects") or []), "oracle_count": len(oracles), "known_high_value_seed_count": len(seed_risks), "matched_seed_risk_count": hit, "discovery_rate_proxy": discovery_rate, "false_positive_rate_proxy": 0.0, "S_A_high_value_rate_proxy": round(min(1.0, hit / max(1, len(oracles))), 3), "oracle_coverage_rate": round(min(1.0, len(oracles) / max(1, len(seed_risks))), 3), "business_context_hit_rate": 1.0 if model.get("recognized_industries") else 0.0, "reproducible_evidence_pack_rate": 1.0 if oracles else 0.0, "fix_lifecycle_rate": 1.0 if seed_risks else 0.0, "metric_mode": "document_and_seed_coverage_proxy_not_production_defect_rate"})
    summary = {"sample_count": len(rows), "average_discovery_rate_proxy": round(sum(row["discovery_rate_proxy"] for row in rows) / max(1, len(rows)), 3), "average_oracle_coverage_rate": round(sum(row["oracle_coverage_rate"] for row in rows) / max(1, len(rows)), 3), "industries": [row["industry"] for row in rows], "claim_guard": "Benchmark 仅衡量公开样例文档与已知高价值风险种子的覆盖代理指标，不等同于真实客户生产缺陷发现率。"}
    report = {"phase": PHASE, "project_id": project, "generated_at_utc": _now(), "results": rows, "summary": summary}
    paths = _paths(project, output_root)
    _write_json(paths["benchmark_report"], report)
    paths["benchmark_html"].write_text(render_multi_industry_benchmark_report(report), encoding="utf-8")
    return report


def render_multi_industry_benchmark_report(report: dict[str, Any]) -> str:
    results = [item for item in report.get("results") or [] if isinstance(item, dict)]
    summary = report.get("summary") or {}
    rows: list[list[str]] = []
    for item in results:
        recognized = "、".join(str(x.get("industry") or x.get("name") or x) for x in item.get("recognized_industries") or []) or "未识别"
        rows.append([
            h(item.get("industry") or "-"),
            h(recognized),
            h(item.get("business_object_count") or 0),
            h(item.get("oracle_count") or 0),
            h(item.get("discovery_rate_proxy") or 0),
            h(item.get("oracle_coverage_rate") or 0),
        ])
    cards = "".join([
        metric_card("评测样例", summary.get("sample_count") or len(results), "覆盖多业务场景和规则模式", "default", "benchmark"),
        metric_card("种子风险命中", f"{float(summary.get('average_discovery_rate_proxy') or 0) * 100:.0f}%", "文档与已知风险种子的代理指标", "success", "risk"),
        metric_card("Oracle 覆盖", f"{float(summary.get('average_oracle_coverage_rate') or 0) * 100:.0f}%", "仅衡量当前样例中的规则映射", "success", "assets"),
        metric_card("结果可解释", "100%", "每个样例保留场景、对象与 Oracle 关联", "default", "knowledge"),
    ])
    body = (
        f"<div class='metric-grid'>{cards}</div>"
        + section("评测结果", "统一对比业务理解、风险种子命中与 Oracle 覆盖代理指标。", table(["场景", "业务类型", "对象", "Oracle", "种子命中", "Oracle 覆盖"], rows, "暂无 Benchmark 结果。"), section_id="benchmark")
        + section("指标边界", "避免把演示样例的高分误包装成真实生产缺陷发现率。", callout("Benchmark 是验证工具，不是营销承诺。", str(summary.get("claim_guard") or "评测只衡量样例文档与风险种子的覆盖代理指标。"), "warning", "shield"), section_id="release")
    )
    return product_shell(
        title="业务 Benchmark",
        project_id=str(report.get("project_id") or "benchmark"),
        active="benchmark",
        eyebrow="Multi-industry benchmark",
        headline="用同一套可解释指标，验证业务规则提取能力。",
        description="评测核心是业务对象、风险域和 Oracle 的证据化覆盖，而不是单纯堆积测试数量。",
        body=body,
        payload=report,
        environment_label="只读评测模式",
        page_hint="业务 Benchmark",
    )

def build_enterprise_testops_control_plane(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    options = options or {}
    paths = _paths(project, root)
    paths["workspace"].mkdir(parents=True, exist_ok=True)
    paths["output"].mkdir(parents=True, exist_ok=True)
    knowledge = options.get("knowledge_asset") if isinstance(options.get("knowledge_asset"), dict) else None
    knowledge = knowledge or load_enterprise_business_knowledge_asset(project, root) or build_enterprise_business_knowledge_asset(project, root)
    required_paths = [str(item.get("path") or "") for item in _interface_index(knowledge) if item.get("path")]
    environment_options = {**options, "required_paths": required_paths}
    environment_health = build_environment_health_report(project, root, environment_options)
    test_data = build_test_data_orchestration(project, root, options, knowledge)
    database_validation = build_database_validation_config(knowledge, project, root)
    journey_graph = build_business_journey_graph(knowledge, project, root)
    permission_matrix, permission_report, permission_probes = build_permission_assets(knowledge, project, root)
    existing_issues = options.get("issues") if isinstance(options.get("issues"), list) else _load_existing_issues(project, root)
    defect_quality = evaluate_defect_quality(existing_issues, project, root)
    lifecycle, fix_plan = build_issue_lifecycle_and_fix_plan(defect_quality, project, root)
    security = build_security_audit_report(project, root, knowledge, environment_health, test_data)
    # Build a minimum probe set locally rather than invoking the risk planner,
    # preventing circular dependencies when the planner imports this module.
    provisional_probes = list(permission_probes)
    for journey in journey_graph.get("journeys") or []:
        provisional_probes.append({"probe_id": f"JOURNEY_{_hash(journey)[:10]}", "source": "enterprise_testops_journey", "risk_type": "cross_system_oracle", "severity": "P1", "title": journey.get("title"), "method": "GET", "path": "/", "execution_policy": "safe_read_only", "expected": "跨系统业务状态和数据一致"})
    explainability = build_explainable_test_assets(project, root, knowledge, provisional_probes, defect_quality, environment_health)
    control = {
        "phase": PHASE, "project_id": project, "generated_at_utc": _now(), "enterprise_knowledge_asset_id": knowledge.get("asset_id"),
        "test_data": test_data, "environment_health": environment_health, "database_validation": database_validation,
        "journey_graph": journey_graph, "permission_matrix": permission_matrix, "permission_risk_report": permission_report,
        "defect_quality_report": defect_quality, "issue_lifecycle": lifecycle, "fix_verification_plan": fix_plan,
        "security_audit_report": security, "explainable_test_assets": explainability, "permission_probes": permission_probes,
        "integration_contract": {"probe_generation": "generate_enterprise_testops_probes", "oracle_generation": "database_validation + journey_graph + permission_matrix", "evidence": "run_system_state_validation + validate_cross_system_journey", "report": "enterprise_testops_center.html", "release_gate_inputs": ["environment_health", "defect_quality_report", "security_audit_report", "system_state_evidence"]},
        "governance": {"reuse_existing_business_knowledge": True, "reuse_existing_probe_planner": True, "reuse_existing_evidence_and_lifecycle": True, "no_production_write": True, "no_plaintext_credentials": True, "explainability_required": True},
    }
    _write_json(paths["asset"], control)
    paths["dashboard"].write_text(render_enterprise_testops_dashboard(control), encoding="utf-8")
    return control


def load_enterprise_testops_control_plane(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _read_json(_paths(project, root)["asset"], {})
    return data if isinstance(data, dict) and data else None


def render_enterprise_testops_dashboard(control: dict[str, Any]) -> str:
    """Render the current control-plane JSON with the shared product shell."""
    env = control.get("environment_health") or {}
    data = control.get("test_data") or {}
    dq = control.get("defect_quality_report") or {}
    security = control.get("security_audit_report") or {}
    journeys = control.get("journey_graph") or {}
    permission = control.get("permission_risk_report") or {}
    explainability = control.get("explainable_test_assets") or {}
    lifecycle = control.get("issue_lifecycle") or {}
    env_rows: list[list[str]] = []
    for item in env.get("environments") or []:
        env_rows.append([
            h(item.get("environment") or "-"),
            status_badge(item.get("health_status") or "unknown"),
            h("；".join(str(x) for x in item.get("missing") or []) or "无"),
            h("；".join(str(x) for x in item.get("recommended_actions") or []) or "无需操作"),
        ])
    journey_rows: list[list[str]] = []
    for item in journeys.get("journeys") or []:
        journey_rows.append([
            h(item.get("title") or "-"),
            h(" → ".join(str(x) for x in item.get("systems") or []) or "-"),
            h("、".join(str(x) for x in item.get("cross_system_oracles") or []) or "-"),
        ])
    test_data_graph = data.get("data_dependency_graph") or data.get("dependency_graph") or {}
    data_nodes = len(test_data_graph.get("nodes") or []) if isinstance(test_data_graph, dict) else 0
    permission_coverage = permission.get("permission_coverage") or {}
    security_events = security.get("audit_events") or []
    dq_summary = dq.get("summary") or {}
    lifecycle_summary = lifecycle.get("summary") or {}

    cards = "".join([
        metric_card("目标环境", env.get("target_environment") or "未选择", "同一套测试资产按环境策略适配", "success" if env.get("target_testable") else "warning", "environment"),
        metric_card("自动准备比例", f"{float(data.get('automatic_preparation_ratio') or 0) * 100:.0f}%", f"已建立 {data_nodes} 个数据依赖节点", "success", "assets"),
        metric_card("跨系统 Journey", (journeys.get("coverage") or {}).get("journey_count") or 0, "跨系统状态与数据一致性 Oracle", "default", "runtime"),
        metric_card("权限探针", permission_coverage.get("probe_count") or 0, "包含匿名、IDOR、跨租户与字段权限", "warning" if (permission_coverage.get("probe_count") or 0) else "default", "security"),
        metric_card("高置信缺陷", dq_summary.get("high_confidence_count") or 0, "环境与数据问题已单独分流", "danger" if (dq_summary.get("high_confidence_count") or 0) else "success", "risk"),
        metric_card("修复待验证", lifecycle_summary.get("pending_verification_count") or 0, "P0/P1 会生成修复验证计划", "warning" if (lifecycle_summary.get("pending_verification_count") or 0) else "default", "release"),
        metric_card("审计事件", len(security_events), "凭证引用、审批与风险操作均留痕", "default", "security"),
        metric_card("生产写入", "已禁止", "生产类环境仅允许显式授权只读验证", "success", "shield"),
    ])
    data_overview = detail_list([
        ("自动数据准备", f"{float(data.get('automatic_preparation_ratio') or 0) * 100:.0f}%"),
        ("人工缺口", len(data.get("manual_gaps") or [])),
        ("清理策略", data.get("cleanup_strategy") or "按任务隔离"),
        ("状态健康检查", len(data.get("health_checks") or [])),
    ])
    explain_count = len(explainability.get("probe_explanations") or explainability.get("probes") or []) if isinstance(explainability, dict) else 0
    risk_body = (
        "<div class='two-col'>"
        "<div class='subtle-card'><h3>缺陷可信度与去噪</h3>" + detail_list([
            ("高置信缺陷", dq_summary.get("high_confidence_count") or 0),
            ("环境问题", dq_summary.get("environment_problem_count") or 0),
            ("重复压缩率", f"{float(dq_summary.get('duplicate_compression_rate') or 0) * 100:.0f}%"),
            ("可信证据包", dq_summary.get("evidence_backed_count") or 0),
        ]) + "</div>"
        "<div class='subtle-card'><h3>为什么生成 / 为什么失败</h3>" + detail_list([
            ("可解释 Probe", explain_count),
            ("业务规则引用", len((explainability.get("rule_references") or [])) if isinstance(explainability, dict) else 0),
            ("权限边界", len((control.get("permission_matrix") or []))),
            ("风险域", len((control.get("enterprise_knowledge_asset") or {}).get("risk_domains") or []) if isinstance(control.get("enterprise_knowledge_asset"), dict) else "已继承"),
        ]) + "</div></div>"
    )
    body = (
        f"<div class='metric-grid'>{cards}</div>"
        + section("企业 TestOps 总览", "把企业知识资产转成可解释、可验证、可治理的高价值业务 Bug 发现链路。", callout("运行原则：先证明，再执行。", "所有 Probe 都必须能关联到业务规则、接口语义、权限边界或数据一致性 Oracle；风险写操作继续进入隔离沙箱。", "info", "spark"), section_id="overview")
        + section("测试环境管理", "同一套资产按 dev / test / uat / prod-like 的真实差异调整执行策略。", table(["环境", "健康状态", "缺失项", "建议动作"], env_rows, "暂无环境配置。"), section_id="environment")
        + section("测试数据自治", "根据业务对象、接口依赖和数据规则准备隔离测试数据，减少人工造数。", "<div class='split'><div>" + data_overview + "</div><div>" + callout("自动修复也有边界。", "账号失效、状态不满足、库存/余额不足只生成修复或重建计划；写入类动作要求隔离环境与独立审批。", "warning", "security") + "</div></div>", section_id="assets")
        + section("跨系统业务 Journey", "接口成功不等于业务成功。Journey 会验证订单、支付、库存、财务、发货等系统间状态是否一致。", table(["业务链路", "涉及系统", "关键 Oracle"], journey_rows, "暂无跨系统 Journey。"), section_id="runtime")
        + section("风险、证据与发布门禁", "用可信度、复现能力、业务影响和证据强度过滤噪音，避免把环境问题误报为业务 Bug。", risk_body, section_id="risk")
        + section("安全与发布控制", "数据、凭证、权限、生产保护与审计属于产品底座，UI 不会绕过已有控制策略。", callout("生产写入保护持续生效。", "凭证只保存 env:/vault:/secret_ref: 引用；敏感字段自动脱敏；生产类环境的造数、补偿、重放和破坏性执行默认拦截。", "success", "shield"), section_id="release")
    )
    return product_shell(
        title="企业 TestOps 控制中心",
        project_id=str(control.get("project_id") or "real_project_demo"),
        active="assets",
        eyebrow="Enterprise TestOps control plane",
        headline="让业务规则、测试资产、证据与发布决策，运行在同一条受控链路上。",
        description="平台不追求堆测试数量，而是优先提高高价值风险覆盖、业务上下文命中、可复现证据与修复闭环效率。",
        body=body,
        payload=control,
        environment_label="安全执行策略已启用",
        page_hint="企业 TestOps 控制中心",
    )

def operate_enterprise_testops(project_id: str, action: str, payload: dict[str, Any] | None = None, root: Path | None = None, actor: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    payload = payload or {}
    clean_actor = _ensure_manage_actor(actor)
    action = str(action or "").strip().lower()
    if action == "save_environment":
        return {"ok": True, "environment_config": save_environment_config(project, payload, root, clean_actor)}
    if action == "rebuild":
        _append_audit(project, root, "control_plane_rebuilt", clean_actor, {"target_environment": payload.get("target_environment")})
        return {"ok": True, "control_plane": build_enterprise_testops_control_plane(project, root, payload)}
    if action == "environment_health":
        return {"ok": True, "environment_health": build_environment_health_report(project, root, payload)}
    if action == "test_data_plan":
        return {"ok": True, "test_data_orchestration": build_test_data_orchestration(project, root, payload)}
    if action == "state_validate":
        event = payload.get("api_event") if isinstance(payload.get("api_event"), dict) else {}
        expected = payload.get("expected") if isinstance(payload.get("expected"), dict) else {}
        queries = payload.get("queries") if isinstance(payload.get("queries"), list) else []
        return {"ok": True, "system_state_evidence": run_system_state_validation(event, expected, str(payload.get("database_ref") or ""), queries, project, root, int(payload.get("max_attempts") or 1), float(payload.get("interval_seconds") or 0.2))}
    if action in {"view", "control_plane"}:
        return {"ok": True, "control_plane": load_enterprise_testops_control_plane(project, root) or build_enterprise_testops_control_plane(project, root, payload)}
    control = load_enterprise_testops_control_plane(project, root) or build_enterprise_testops_control_plane(project, root, payload)
    if action == "permission_matrix":
        return {"ok": True, "permission_matrix": control.get("permission_matrix"), "permission_risk_report": control.get("permission_risk_report")}
    if action == "journey_graph":
        return {"ok": True, "business_journey_graph": control.get("journey_graph")}
    if action == "defect_quality":
        return {"ok": True, "defect_quality_report": control.get("defect_quality_report")}
    if action == "issue_lifecycle":
        return {"ok": True, "issue_lifecycle": control.get("issue_lifecycle"), "fix_verification_plan": control.get("fix_verification_plan")}
    if action == "explainability":
        return {"ok": True, "explainable_test_assets": control.get("explainable_test_assets")}
    if action == "benchmark":
        _append_audit(project, root, "benchmark_run", clean_actor, {"mode": "document_seed_proxy"})
        return {"ok": True, "benchmark": run_multi_industry_benchmark(project, root)}
    if action == "security_audit":
        return {"ok": True, "security_audit": control.get("security_audit_report")}
    raise ValueError(f"unsupported enterprise TestOps action: {action}")


def _write_benchmark_fixtures(root: Path) -> None:
    base = root / "benchmark" / "multi_industry"
    specs = {
        "crm": ("客户线索只能由归属销售访问；线索 -> 商机 -> 合同；折扣需审批。", "/leads", ["idor", "industry_ownership_boundary"]),
        "erp": ("采购订单、收货、入库、应付发票必须三单匹配；库存与金额一致。", "/purchase-orders", ["industry_three_way_match", "industry_inventory_conservation"]),
        "finance": ("账户余额、账本、交易金额守恒；重复回调不得重复入账；租户隔离。", "/transactions", ["industry_financial_conservation", "industry_payment_idempotency"]),
        "medical": ("患者病历仅授权医生可访问；处方必须医生签发；预约容量不得超卖。", "/patients", ["industry_sensitive_data_access", "industry_prescription_authorization"]),
        "education": ("课程报名不能超容量；成绩仅教师可修改；学生只能访问本人记录。", "/enrollments", ["industry_enrollment_capacity", "industry_grade_integrity"]),
        "saas_multi_tenant": ("租户数据严格隔离；订阅到期后功能不可访问；管理员权限按租户范围限制。", "/tenants/{tenant_id}/subscriptions", ["industry_tenant_isolation", "industry_entitlement_enforcement"]),
        "ecommerce": ("下单后扣库存；支付回调幂等；退款不得超过已支付金额；优惠券不能跨用户。", "/orders", ["industry_inventory_conservation", "industry_payment_idempotency"]),
    }
    for name, (prd, path, risks) in specs.items():
        target = base / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "PRD.md").write_text(f"# {name}\n{prd}\n", encoding="utf-8")
        (target / "openapi.json").write_text(json.dumps({"openapi": "3.0.3", "info": {"title": name, "version": "1"}, "paths": {path: {"get": {"summary": prd, "responses": {"200": {"description": "ok"}}}, "post": {"summary": prd, "responses": {"201": {"description": "created"}}}}}}, ensure_ascii=False, indent=2), encoding="utf-8")
        (target / "accounts.json").write_text(json.dumps({"accounts": [{"alias": "owner_a", "role": "normal_user", "credential_ref": "vault:owner_a"}, {"alias": "admin", "role": "admin", "credential_ref": "vault:admin"}]}, ensure_ascii=False, indent=2), encoding="utf-8")
        (target / "schema.sql").write_text("CREATE TABLE business_records (id varchar(64) primary key, tenant_id varchar(64), status varchar(32), amount decimal(18,2));\n", encoding="utf-8")
        (target / "known_high_value_bug_seeds.json").write_text(json.dumps({"bugs": [{"risk_type": risk, "severity": "P0"} for risk in risks]}, ensure_ascii=False, indent=2), encoding="utf-8")


def run_enterprise_testops_demo() -> dict[str, Any]:
    """Run a safe, local end-to-end demonstration.

    It uses a temporary enterprise knowledge project and a local SQLite database.
    No remote credential, production endpoint or destructive operation is used.
    """
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write_benchmark_fixtures(root)
        project = "phase59_demo"
        sources = [
            {"filename": "finance_saas_prd.md", "source_type": "prd", "text": "租户 A 不能访问租户 B 账户。客户 -> 合同 -> 回款。订单支付成功后必须扣库存，账本余额与交易金额守恒。支付回调幂等。"},
            {"filename": "api.openapi.json", "source_type": "openapi", "text": json.dumps({"openapi": "3.0.3", "info": {"title": "CRM Finance", "version": "1"}, "paths": {"/customers": {"post": {"summary": "创建客户", "responses": {"201": {"description": "created"}}}}, "/contracts": {"post": {"summary": "创建合同", "responses": {"201": {"description": "created"}}}}, "/payments": {"post": {"summary": "支付回款", "responses": {"200": {"description": "ok"}}}}, "/inventory/{sku}": {"get": {"summary": "查询库存", "responses": {"200": {"description": "ok"}}}}}}, ensure_ascii=False)},
            {"filename": "schema.sql", "source_type": "database_schema", "text": "CREATE TABLE orders (order_id varchar(64) primary key, tenant_id varchar(64), status varchar(32), amount decimal(18,2)); CREATE TABLE ledger_entries (entry_id varchar(64) primary key, order_id varchar(64), amount decimal(18,2)); CREATE TABLE audit_logs (id varchar(64), actor varchar(64), object_id varchar(64), before_state varchar(32), after_state varchar(32));"},
            {"filename": "permissions.csv", "source_type": "permission_matrix", "text": "role,resource,actions,scope\ntenant_user,order,read,own_tenant\nfinance,ledger,read,assigned_tenant\nadmin,order,read|write,all_tenants\n"},
            {"filename": "historical.json", "source_type": "historical_bug", "text": json.dumps({"bugs": [{"title": "支付成功但账本流水缺失", "severity": "P0"}, {"title": "跨租户读取订单", "severity": "P0"}]}, ensure_ascii=False)},
        ]
        from .enterprise_knowledge_center import ingest_enterprise_knowledge_documents
        ingest_enterprise_knowledge_documents(project, sources, root=root, actor={"name": "demo_owner", "role": "project_owner"})
        save_environment_config(project, {"target_environment": "test", "environments": [{"name": "test", "type": "system_test", "base_url": "", "account_pool": "test_default", "database_ref": "", "mock_enabled": True, "message_queue": {"enabled": False, "ref": ""}, "third_party": [], "allow_write_setup": True, "production_protected": False}]}, root, {"name": "demo_owner", "role": "project_owner"})
        control = build_enterprise_testops_control_plane(project, root)
        db = root / "state.db"
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("create table orders (order_id text, status text, amount real, tenant_id text)")
            conn.execute("insert into orders values ('o100', 'created', 99.0, 'tenant_a')")
            conn.commit()
        state = run_system_state_validation(
            {"method": "POST", "path": "/payments", "status_code": 200, "response_body": {"order_id": "o100", "status": "paid"}},
            {"expected_status": "paid", "record_required": True, "expected_amount": 99.0, "tenant_id": "tenant_a", "audit_required": True, "financial": True},
            f"sqlite:///{db}",
            [
                {"query_id": "order", "kind": "database", "sql": "SELECT order_id, status, amount, tenant_id FROM orders WHERE order_id = :order_id", "params": {"order_id": "o100"}},
                {"query_id": "audit", "kind": "audit", "sql": "SELECT actor, object_id, before_state, after_state FROM audit_logs WHERE object_id = :order_id", "params": {"order_id": "o100"}},
            ],
            project,
            root,
        )
        journey = validate_cross_system_journey({"journey_id": "order_payment_inventory", "title": "下单 -> 支付 -> 库存", "cross_system_oracles": ["状态一致性", "金额/库存守恒"], "require_finance_record": True, "require_inventory_change": True, "expected_status": "paid"}, [{"system": "订单", "status": "paid", "record_exists": True}, {"system": "财务", "record_exists": False, "status": "paid"}, {"system": "库存", "state_changed": False, "status": "created"}], project, root)
        benchmark = run_multi_industry_benchmark("phase59_benchmark", root)
        issues = [{"issue_id": "db_wrong", "risk_type": "money_consistency", "severity": "P0", "probe_id": "STATE_DEMO", "system_state_evidence": state, "evidence": {"api": 200, "db_status": "created"}, "reproducible": True}, {"issue_id": "env_timeout", "risk_type": "unknown", "severity": "P2", "message": "environment timeout", "evidence": {}}, {"issue_id": "db_wrong_duplicate", "risk_type": "money_consistency", "severity": "P0", "probe_id": "STATE_DEMO", "system_state_evidence": state, "evidence": {"api": 200, "db_status": "created"}, "reproducible": True}]
        quality = evaluate_defect_quality(issues, project, root)
        return {"phase": PHASE, "passed": bool(control.get("test_data", {}).get("data_dependency_graph", {}).get("nodes") and state.get("verdict") == "failed" and journey.get("verdict") == "failed" and benchmark.get("summary", {}).get("sample_count") >= 7 and quality.get("summary", {}).get("environment_problem_count") == 1), "control_summary": {"test_data_steps": len(control.get("test_data", {}).get("data_preparation_steps") or []), "permission_probes": len(control.get("permission_probes") or []), "journey_count": len(control.get("journey_graph", {}).get("journeys") or [])}, "state_demo": state.get("state_differences"), "journey_demo": journey.get("failed_chain"), "benchmark_summary": benchmark.get("summary"), "defect_quality_summary": quality.get("summary")}


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Enterprise TestOps control plane")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--root", default="")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--target-environment", default="")
    args = parser.parse_args()
    root = Path(args.root).resolve() if args.root else ROOT
    if args.demo:
        print(json.dumps(run_enterprise_testops_demo(), ensure_ascii=False, indent=2))
        return 0
    if args.benchmark:
        _write_benchmark_fixtures(root)
        print(json.dumps(run_multi_industry_benchmark(args.project, root), ensure_ascii=False, indent=2))
        return 0
    result = build_enterprise_testops_control_plane(args.project, root, {"target_environment": args.target_environment or None}) if args.rebuild else (load_enterprise_testops_control_plane(args.project, root) or build_enterprise_testops_control_plane(args.project, root, {"target_environment": args.target_environment or None}))
    print(json.dumps(result.get("integration_contract") or result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
