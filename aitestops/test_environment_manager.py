from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


DEFAULT_ACCOUNT_POOLS = [
    {
        "pool": "normal_users",
        "role": "normal_user",
        "tenant": "demo_tenant",
        "profiles": [
            {"alias": "buyer_a", "username": "alice", "credential_ref": "env:AITESTOPS_BUYER_A_PASSWORD", "data_scope": "own_orders"},
            {"alias": "buyer_b", "username": "bob", "credential_ref": "env:AITESTOPS_BUYER_B_PASSWORD", "data_scope": "own_orders"},
        ],
    },
    {
        "pool": "admin_users",
        "role": "admin",
        "tenant": "demo_tenant",
        "profiles": [
            {"alias": "shop_admin", "username": "admin", "credential_ref": "env:AITESTOPS_ADMIN_PASSWORD", "data_scope": "tenant_all"},
        ],
    },
    {
        "pool": "negative_users",
        "role": "negative",
        "tenant": "demo_tenant",
        "profiles": [
            {"alias": "locked_user", "username": "locked_user", "credential_ref": "vault:locked_user_password", "data_scope": "none"},
            {"alias": "no_permission_user", "username": "guest", "credential_ref": "vault:guest_password", "data_scope": "read_only"},
        ],
    },
]


DEFAULT_DATABASE_ENVIRONMENTS = [
    {
        "name": "test_db",
        "kind": "sqlite_or_test_db",
        "connection_ref": "env:AITESTOPS_TEST_DB_URL",
        "mode": "metadata_only",
        "allowed_operations": ["seed", "assert", "cleanup"],
        "blocked_operations": ["drop", "truncate", "delete_without_where", "update_without_where", "production_write"],
    }
]


DEFAULT_ENVIRONMENTS = [
    {
        "name": "dev",
        "type": "development",
        "base_url_ref": "env:AITESTOPS_DEV_BASE_URL",
        "openapi_url_ref": "env:AITESTOPS_DEV_OPENAPI_URL",
        "account_pool_refs": ["normal_users", "admin_users", "negative_users"],
        "database_refs": ["local_demo", "test_db"],
        "data_policy_overrides": {"cleanup_strategy": "auto_cleanup_by_run_id"},
        "quality_gate": {"allow_destructive_seed": True, "requires_masked_data": False},
    },
    {
        "name": "test",
        "type": "system_test",
        "base_url_ref": "env:AITESTOPS_TEST_BASE_URL",
        "openapi_url_ref": "env:AITESTOPS_TEST_OPENAPI_URL",
        "account_pool_refs": ["normal_users", "admin_users", "negative_users"],
        "database_refs": ["local_demo", "test_db"],
        "data_policy_overrides": {"cleanup_strategy": "auto_cleanup_by_run_id"},
        "quality_gate": {"allow_destructive_seed": False, "requires_masked_data": True},
    },
    {
        "name": "uat",
        "type": "user_acceptance",
        "base_url_ref": "env:AITESTOPS_UAT_BASE_URL",
        "openapi_url_ref": "env:AITESTOPS_UAT_OPENAPI_URL",
        "account_pool_refs": ["normal_users", "admin_users"],
        "database_refs": ["local_demo", "test_db"],
        "data_policy_overrides": {"cleanup_strategy": "test_tenant_isolation"},
        "quality_gate": {"allow_destructive_seed": False, "requires_masked_data": True, "requires_approval": True},
    },
]


DEFAULT_DATA_POLICIES = {
    "isolation_strategy": "test_tenant_or_transaction_snapshot",
    "prefer_setup_order": ["business_api", "seed_api", "database_seed"],
    "cleanup_strategy": "auto_cleanup_by_run_id",
    "masking_required": True,
    "audit_required": True,
    "forbid_production_write": True,
}


DANGEROUS_SQL_PATTERNS = [
    (re.compile(r"\bdrop\s+(table|database|schema)\b", re.I), "drop statement is not allowed"),
    (re.compile(r"\btruncate\s+table\b", re.I), "truncate statement is not allowed"),
    (re.compile(r"\bdelete\s+from\b(?![\s\S]*\bwhere\b)", re.I), "delete without where is not allowed"),
    (re.compile(r"\bupdate\s+\w+\s+set\b(?![\s\S]*\bwhere\b)", re.I), "update without where is not allowed"),
]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def validate_sql_template(sql: str) -> List[str]:
    warnings: List[str] = []
    for pattern, message in DANGEROUS_SQL_PATTERNS:
        if pattern.search(sql or ""):
            warnings.append(message)
    return warnings


def infer_account_requirements(business_model: Dict[str, Any]) -> List[Dict[str, Any]]:
    scenarios = business_model.get("business_scenarios") or []
    risks = business_model.get("risk_matrix") or []
    required = {
        "normal_user": {"reason": "positive business journey execution", "min_accounts": 2},
        "admin": {"reason": "admin and management workflow validation", "min_accounts": 1},
    }
    if any("permission" in json.dumps(item, ensure_ascii=False).lower() for item in [*scenarios, *risks]):
        required["no_permission_user"] = {"reason": "permission boundary and cross-user ownership tests", "min_accounts": 1}
    if any("authentication" in json.dumps(item, ensure_ascii=False).lower() or "account" in json.dumps(item, ensure_ascii=False).lower() for item in scenarios):
        required["locked_user"] = {"reason": "account lockout and abnormal login tests", "min_accounts": 1}
    return [{"role": role, **meta} for role, meta in required.items()]


def infer_seed_templates(business_model: Dict[str, Any]) -> List[Dict[str, Any]]:
    dependencies = business_model.get("data_dependency_model") or []
    templates: List[Dict[str, Any]] = []
    for item in dependencies:
        entity = item.get("entity")
        if not entity:
            continue
        templates.append(
            {
                "entity": entity,
                "preferred_setup": item.get("setup_strategy") or "business_api_or_seed_api",
                "db_seed_template": f"-- metadata only: seed {entity} for run_id={{run_id}} in test tenant\n-- use business API first; DB seed only in isolated test environment",
                "cleanup_template": f"-- cleanup {entity} rows by run_id={{run_id}} or test_tenant={{tenant}}",
                "sql_warnings": [],
            }
        )
    return templates


def select_environment(cfg: Dict[str, Any], target_environment: str | None) -> Dict[str, Any]:
    environments = cfg.get("environments") or DEFAULT_ENVIRONMENTS
    target = target_environment or cfg.get("target_environment") or "test"
    selected = next((item for item in environments if item.get("name") == target), None)
    if selected:
        return selected
    return environments[0] if environments else {"name": target, "type": "unknown", "account_pool_refs": [], "database_refs": []}


def filter_by_refs(items: List[Dict[str, Any]], refs: List[str], key: str) -> List[Dict[str, Any]]:
    if not refs:
        return items
    ref_set = set(refs)
    return [item for item in items if item.get(key) in ref_set or item.get("name") in ref_set]


def evaluate_environment_readiness(account_requirements: List[Dict[str, Any]], account_pools: List[Dict[str, Any]], database_environments: List[Dict[str, Any]]) -> Dict[str, Any]:
    available = set()
    for pool in account_pools:
        if pool.get("role"):
            available.add(pool["role"])
        for profile in pool.get("profiles") or []:
            if profile.get("alias"):
                available.add(profile["alias"])
    missing_accounts = [item["role"] for item in account_requirements if item.get("role") not in available]
    missing_databases = not bool(database_environments)
    return {
        "status": "blocked" if missing_accounts or missing_databases else "ready",
        "missing_accounts": missing_accounts,
        "missing_databases": missing_databases,
    }


@dataclass
class EnterpriseTestEnvironmentManager:
    project: str
    input_dir: Path
    output_dir: Path

    def config_path(self) -> Path:
        return self.input_dir / "test_environment.json"

    def default_config(self) -> Dict[str, Any]:
        return {
            "project": self.project,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "target_environment": "test",
            "environments": DEFAULT_ENVIRONMENTS,
            "account_pools": DEFAULT_ACCOUNT_POOLS,
            "database_environments": DEFAULT_DATABASE_ENVIRONMENTS,
            "data_policies": DEFAULT_DATA_POLICIES,
            "custom_seed_templates": [],
        }

    def load_config(self) -> Dict[str, Any]:
        cfg = self.default_config()
        stored = read_json(self.config_path(), {})
        if isinstance(stored, dict):
            cfg.update({k: v for k, v in stored.items() if k in {"project", "target_environment", "environments", "account_pools", "database_environments", "data_policies", "custom_seed_templates"}})
        return cfg

    def save_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        cfg = self.load_config()
        for key in ["target_environment", "environments", "account_pools", "database_environments", "data_policies", "custom_seed_templates"]:
            if key in payload:
                cfg[key] = payload[key]
        cfg["project"] = self.project
        cfg["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        write_json(self.config_path(), cfg)
        return cfg

    def build_plan(self, business_model: Dict[str, Any] | None = None, target_environment: str | None = None) -> Dict[str, Any]:
        cfg = self.load_config()
        business_model = business_model or {}
        environment = select_environment(cfg, target_environment)
        data_policies = dict(cfg.get("data_policies") or {})
        data_policies.update(environment.get("data_policy_overrides") or {})
        account_pools = filter_by_refs(cfg.get("account_pools") or [], environment.get("account_pool_refs") or [], "pool")
        database_environments = filter_by_refs(cfg.get("database_environments") or [], environment.get("database_refs") or [], "name")
        custom_templates = cfg.get("custom_seed_templates") or []
        sql_warnings = []
        for template in custom_templates:
            warnings = validate_sql_template(template.get("sql") or "")
            if warnings:
                sql_warnings.append({"name": template.get("name") or "unnamed_template", "warnings": warnings})

        account_requirements = infer_account_requirements(business_model)
        readiness = evaluate_environment_readiness(account_requirements, account_pools, database_environments)
        plan = {
            "project": self.project,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "target_environment": environment.get("name"),
            "environment": environment,
            "environment_matrix": cfg.get("environments") or [],
            "environment_readiness": readiness,
            "account_requirements": account_requirements,
            "account_pools": account_pools,
            "database_environments": database_environments,
            "data_policies": data_policies,
            "seed_templates": infer_seed_templates(business_model),
            "custom_seed_template_warnings": sql_warnings,
            "execution_contract": {
                "before_case": ["select_role_account", "prepare_business_dependencies", "record_data_lineage"],
                "after_case": ["assert_data_consistency", "cleanup_by_run_id", "write_data_evidence"],
                "blocked_when": ["missing_required_account", "unsafe_sql_template", "production_write_requested"],
            },
            "lineage_fields": ["run_id", "case_id", "account_alias", "tenant", "entity", "record_id", "setup_strategy", "cleanup_status"],
        }
        self.output_dir.mkdir(parents=True, exist_ok=True)
        write_json(self.output_dir / "test_environment_plan.json", plan)
        (self.output_dir / "test_environment_report.html").write_text(render_environment_report(plan), encoding="utf-8")
        return plan


def esc(value: Any) -> str:
    return str(value if value is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_environment_report(plan: Dict[str, Any]) -> str:
    environment = plan.get("environment") or {}
    readiness = plan.get("environment_readiness") or {}
    env_rows = "".join(
        f"<tr><td>{esc(item.get('name'))}</td><td>{esc(item.get('type'))}</td><td>{esc(item.get('base_url_ref'))}</td><td>{esc(item.get('openapi_url_ref'))}</td><td>{esc(', '.join(item.get('database_refs') or []))}</td></tr>"
        for item in plan.get("environment_matrix", [])
    )
    account_rows = "".join(
        f"<tr><td>{esc(item.get('pool'))}</td><td>{esc(item.get('role'))}</td><td>{esc(item.get('tenant'))}</td><td>{esc(len(item.get('profiles') or []))}</td></tr>"
        for item in plan.get("account_pools", [])
    )
    db_rows = "".join(
        f"<tr><td>{esc(item.get('name'))}</td><td>{esc(item.get('kind'))}</td><td>{esc(item.get('connection_ref'))}</td><td>{esc(', '.join(item.get('allowed_operations') or []))}</td></tr>"
        for item in plan.get("database_environments", [])
    )
    req_rows = "".join(
        f"<tr><td>{esc(item.get('role'))}</td><td>{esc(item.get('min_accounts'))}</td><td>{esc(item.get('reason'))}</td></tr>"
        for item in plan.get("account_requirements", [])
    )
    seed_rows = "".join(
        f"<tr><td>{esc(item.get('entity'))}</td><td>{esc(item.get('preferred_setup'))}</td><td>{esc(item.get('cleanup_template'))}</td></tr>"
        for item in plan.get("seed_templates", [])
    )
    warning_rows = "".join(
        f"<li>{esc(item.get('name'))}: {esc('; '.join(item.get('warnings') or []))}</li>"
        for item in plan.get("custom_seed_template_warnings", [])
    ) or "<li>未检测到不安全 SQL 模板。</li>"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>企业测试环境计划</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,Arial,sans-serif;margin:28px;color:#132033}}table{{border-collapse:collapse;width:100%;margin:12px 0 24px}}td,th{{border:1px solid #d8e1ee;padding:8px;text-align:left}}th{{background:#f6f8fb}}section{{margin-bottom:22px}}code{{background:#f6f8fb;padding:2px 5px;border-radius:4px}}</style></head>
<body>
<h1>企业测试环境计划</h1>
<p>项目：<b>{esc(plan.get('project'))}</b> · 生成时间：{esc(plan.get('generated_at'))}</p>
<section><h2>环境就绪度</h2><p>状态：<b>{esc(readiness.get('status'))}</b> · 缺失账号：{esc(', '.join(readiness.get('missing_accounts') or []))} · 缺失数据库：{esc(readiness.get('missing_databases'))}</p></section>
<section><h2>当前环境</h2><table><thead><tr><th>名称</th><th>类型</th><th>被测系统地址引用</th><th>OpenAPI 引用</th><th>质量门禁</th></tr></thead><tbody><tr><td>{esc(environment.get('name'))}</td><td>{esc(environment.get('type'))}</td><td>{esc(environment.get('base_url_ref'))}</td><td>{esc(environment.get('openapi_url_ref'))}</td><td>{esc(environment.get('quality_gate'))}</td></tr></tbody></table></section>
<section><h2>环境矩阵</h2><table><thead><tr><th>名称</th><th>类型</th><th>被测系统地址引用</th><th>OpenAPI 引用</th><th>数据库引用</th></tr></thead><tbody>{env_rows}</tbody></table></section>
<section><h2>所需账号</h2><table><thead><tr><th>角色</th><th>最少账号数</th><th>原因</th></tr></thead><tbody>{req_rows}</tbody></table></section>
<section><h2>账号池</h2><table><thead><tr><th>账号池</th><th>角色</th><th>租户</th><th>画像</th></tr></thead><tbody>{account_rows}</tbody></table></section>
<section><h2>数据库环境</h2><table><thead><tr><th>名称</th><th>类型</th><th>连接引用</th><th>允许操作</th></tr></thead><tbody>{db_rows}</tbody></table></section>
<section><h2>数据准备与清理模板</h2><table><thead><tr><th>实体</th><th>推荐准备方式</th><th>清理策略</th></tr></thead><tbody>{seed_rows}</tbody></table></section>
<section><h2>安全告警</h2><ul>{warning_rows}</ul></section>
<section><h2>执行契约</h2><p>用例前置：<code>{esc(', '.join(plan.get('execution_contract', {}).get('before_case') or []))}</code></p><p>用例后置：<code>{esc(', '.join(plan.get('execution_contract', {}).get('after_case') or []))}</code></p></section>
</body></html>"""
