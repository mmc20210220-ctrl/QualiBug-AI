from __future__ import annotations

"""
Multi-Service Enterprise Project Configuration.

Real enterprises don't have one monolith. They have order-service, payment-service,
inventory-service, logistics-service, notification-service... each with its own
base URL, OpenAPI spec, and document set.

This module replaces the single-service assumption in real_project_onboarding

1. Per-service configuration (base_url, openapi_spec, prd docs)
2. Cross-service relationship modeling (order→payment, payment→refund, etc.)
3. Service-boundary bug detection (bugs that only appear when services interact)
4. Incremental analysis (only re-analyze changed services)
"""

import json
import os
from pathlib import Path
from typing import Any

from .real_project_onboarding import ROOT, _safe_project_id, _write_json, _load_json

# ---------------------------------------------------------------------------
# Service configuration
# ---------------------------------------------------------------------------

# Customer-maintained project-level metadata required by the main chain
# (主链 1：客户维护被测系统信息). These are NOT auto-inferred — the customer
# owns them, and they must drive downstream execution, not just be stored.
PROJECT_INDUSTRY_KEY = "industry"
PROJECT_MODULE_SCOPE_KEY = "module_scope"
# List of path fragments / regex patterns describing data or endpoints the
# system is forbidden to touch (e.g. production PII, finance settlement tables).
# The executor consults this as a hard safety boundary — see
# match_production_data_exclusion().
PROJECT_PRODUCTION_DATA_EXCLUSION_KEY = "production_data_exclusion"

EXAMPLE_MULTI_SERVICE_CONFIG = {
    "project_name": "acme_ecommerce",
    "services": [
        {
            "name": "order-service",
            "base_url": "http://order-service.internal:8080",
            "openapi_spec": "docs/openapi/order-service.yaml",
            "prd": "docs/prd/order-service.md",
            "description": "订单管理：创建、查询、状态流转、取消",
            "depends_on": [],
            "exposes_to": ["payment-service", "logistics-service"],
            "auth": {
                "type": "password_login",
                "login_api": "/auth/login",
                "admin": {"username": "${QUALIBUG_SVC_ORDER_SERVICE_ADMIN_USER}", "password": "${QUALIBUG_SVC_ORDER_SERVICE_ADMIN_PASS}"},
                "viewer": {"username": "${QUALIBUG_SVC_ORDER_SERVICE_VIEWER_USER}", "password": "${QUALIBUG_SVC_ORDER_SERVICE_VIEWER_PASS}"},
            },
            "db": {"host": "order-db.internal", "port": 3306,
                   "name": "order_db", "user": "${QUALIBUG_SVC_ORDER_SERVICE_DB_USER}", "password": "${QUALIBUG_SVC_ORDER_SERVICE_DB_PASS}"},
        },
        {
            "name": "payment-service",
            "base_url": "http://payment-service.internal:8081",
            "openapi_spec": "docs/openapi/payment-service.yaml",
            "prd": "docs/prd/payment-service.md",
            "description": "支付处理：收款、退款、对账",
            "depends_on": ["order-service"],
            "exposes_to": ["order-service"],
            "auth": {
                "type": "password_login",
                "login_api": "/auth/login",
                "admin": {"username": "${QUALIBUG_SVC_PAYMENT_SERVICE_ADMIN_USER}", "password": "${QUALIBUG_SVC_PAYMENT_SERVICE_ADMIN_PASS}"},
                "viewer": {"username": "${QUALIBUG_SVC_PAYMENT_SERVICE_VIEWER_USER}", "password": "${QUALIBUG_SVC_PAYMENT_SERVICE_VIEWER_PASS}"},
            },
            "db": {"host": "payment-db.internal", "port": 5432,
                   "name": "payment_db", "user": "${QUALIBUG_SVC_PAYMENT_SERVICE_DB_USER}", "password": "${QUALIBUG_SVC_PAYMENT_SERVICE_DB_PASS}"},
        },
        {
            "name": "inventory-service",
            "base_url": "http://inventory-service.internal:8082",
            "openapi_spec": "docs/openapi/inventory-service.yaml",
            "prd": "docs/prd/inventory-service.md",
            "description": "库存管理：扣减、回补、盘点",
            "depends_on": ["order-service"],
            "exposes_to": ["order-service", "logistics-service"],
            "auth": {
                "type": "password_login",
                "login_api": "/auth/login",
                "admin": {"username": "${QUALIBUG_SVC_INVENTORY_SERVICE_ADMIN_USER}", "password": "${QUALIBUG_SVC_INVENTORY_SERVICE_ADMIN_PASS}"},
                "viewer": {"username": "${QUALIBUG_SVC_INVENTORY_SERVICE_VIEWER_USER}", "password": "${QUALIBUG_SVC_INVENTORY_SERVICE_VIEWER_PASS}"},
            },
            "db": {"host": "inventory-db.internal", "port": 3306,
                   "name": "inventory_db", "user": "${QUALIBUG_SVC_INVENTORY_SERVICE_DB_USER}", "password": "${QUALIBUG_SVC_INVENTORY_SERVICE_DB_PASS}"},
        },
        {
            "name": "logistics-service",
            "base_url": "http://logistics-service.internal:8083",
            "openapi_spec": "docs/openapi/logistics-service.yaml",
            "prd": "docs/prd/logistics-service.md",
            "description": "物流管理：发货、追踪、签收",
            "depends_on": ["order-service", "inventory-service"],
            "exposes_to": ["order-service"],
            "external_integrations": ["jt-express", "pinduoduo"],
            "auth": {
                "type": "bearer_token",
                "login_api": "/auth/token",
                "admin": {"username": "${QUALIBUG_SVC_LOGISTICS_SERVICE_ADMIN_USER}", "password": "${QUALIBUG_SVC_LOGISTICS_SERVICE_ADMIN_PASS}"},
                "viewer": {"username": "${QUALIBUG_SVC_LOGISTICS_SERVICE_VIEWER_USER}", "password": "${QUALIBUG_SVC_LOGISTICS_SERVICE_VIEWER_PASS}"},
            },
            "db": {"host": "logistics-db.internal", "port": 3306,
                   "name": "logistics_db", "user": "${QUALIBUG_SVC_LOGISTICS_SERVICE_DB_USER}", "password": "${QUALIBUG_SVC_LOGISTICS_SERVICE_DB_PASS}"},
        },
    ],
    "cross_service_contracts": [
        {
            "contract_id": "order_to_payment",
            "from_service": "order-service",
            "to_service": "payment-service",
            "relationship": "order.payment_id → payment.id",
            "invariant": "订单paid状态 → 必须存在对应payment记录，且金额相等",
            "trigger": "order.status == 'paid'",
            "verification": "GET /orders?status=paid → for each: GET /payments?order_id={id} → assert payment.amount == order.total_amount",
        },
        {
            "contract_id": "payment_to_refund",
            "from_service": "payment-service",
            "to_service": "payment-service",
            "relationship": "同一服务内：refund.payment_id → payment.id",
            "invariant": "退款金额 ≤ 原支付金额，且退款必须原路返回",
        },
        {
            "contract_id": "order_to_shipment",
            "from_service": "order-service",
            "to_service": "logistics-service",
            "relationship": "order.id → shipment.order_id",
            "invariant": "订单shipped状态 → 必须存在对应shipment记录，且tracking_number不为空",
        },
        {
            "contract_id": "inventory_to_order",
            "from_service": "inventory-service",
            "to_service": "order-service",
            "relationship": "inventory.sku → order.line_items.sku",
            "invariant": "订单创建时库存必须充足；订单取消时库存必须回补",
            "trigger": "order.status == 'confirmed'",
            "verification": "下单前GET /inventory/{sku}记录库存 → 下单后再次GET → 库存减少量 == 订单数量",
        },
    ],
    "external_integrations": [
        {
            "integration_id": "jt_express_order_flow",
            "name": "极兔工单对接",
            "our_service": "logistics-service",
            "external_system": "jt-express",
            "external_system_type": "third_party_logistics",
            "data_flow": "outbound",
            "description": "我方创建物流单 → 推送极兔 → 极兔返回运单号 → 我方记录 → 极兔回传状态更新",
            "contract": {
                "step_1": "POST /api/external/jt/orders → 极兔创建工单",
                "step_2": "极兔回调 POST /api/callback/jt/status → 我方更新物流状态",
                "step_3": "我方 GET /api/external/jt/orders/{id}/tracking → 查询最新轨迹",
            },
            "invariants": [
                "推送成功后 30 秒内必须收到极兔返回的运单号",
                "极兔回传的订单状态变更不允许跳步（shipped→delivered，不能直接从pending→delivered）",
                "我方物流单号与极兔运单号必须一对一绑定",
            ],
        },
        {
            "integration_id": "pinduoduo_order_sync",
            "name": "拼多多订单同步",
            "our_service": "order-service",
            "external_system": "pinduoduo",
            "external_system_type": "ecommerce_platform",
            "data_flow": "inbound",
            "description": "拼多多推送订单 → 我方接收 → 我方处理 → 回传状态到拼多多",
            "contract": {
                "step_1": "拼多多推送 POST /api/external/pdd/orders → 我方接收",
                "step_2": "我方回传 PUT /api/external/pdd/orders/{id}/status → 拼多多",
            },
            "invariants": [
                "拼多多推送的订单必须在我方系统创建成功，且关键字段（金额、商品、收货地址）完全一致",
                "我方回传的状态变更必须在拼多多规定的时效内完成",
            ],
        },
    ],
}


# ---------------------------------------------------------------------------
# Multi-service project management
# ---------------------------------------------------------------------------

class MultiServiceProject:
    """Manages a multi-service enterprise project with credential routing.

    Integration with EnterpriseCredentialManager:
        project = MultiServiceProject("acme_ecommerce")
        mgr = project.get_credential_manager()
        mgr.login_all_services()
        header = mgr.get_auth_header("order-service", "admin")
    """

    def __init__(self, project_id: str, root: Path | None = None):
        self.project_id = _safe_project_id(project_id)
        self.root = root or ROOT
        self._config: dict[str, Any] | None = None
        self._credential_manager: Any = None  # Lazy-loaded EnterpriseCredentialManager

    def get_credential_manager(self):
        """Get or create the EnterpriseCredentialManager for this project."""
        if self._credential_manager is None:
            from .enterprise_credential_manager import EnterpriseCredentialManager
            self._credential_manager = EnterpriseCredentialManager(
                self.project_id, self.root
            )
            # Load from multi_service_config.json if it exists
            config_path = self._config_path()
            if config_path.exists():
                self._credential_manager.load_from_file(config_path)
            # Load from environment variables
            self._credential_manager.load_from_env(self.service_names())
            # Fallback: legacy single-service
            self._credential_manager.load_legacy_fallback()
        return self._credential_manager

    def save_credentials(self, service: str, role: str,
                         username: str, password: str, login_api: str = "/auth/login") -> None:
        """Save credentials for a specific service×role back to config."""
        config = self.config
        for svc in config.get("services", []):
            if svc.get("name") == service:
                svc.setdefault("auth", {}).setdefault(role, {})
                svc["auth"][role]["username"] = username
                svc["auth"][role]["password"] = password
                if login_api:
                    svc["auth"]["login_api"] = login_api
                break
        self._config = config
        self._write_config()
        # Reload credentials
        if self._credential_manager:
            self._credential_manager.load_from_dict(config)

    def save_db_config(self, service: str, host: str, port: int,
                       db_name: str, user: str, password: str) -> None:
        """Save DB connection for a service."""
        config = self.config
        for svc in config.get("services", []):
            if svc.get("name") == service:
                svc["db"] = {
                    "host": host, "port": port,
                    "name": db_name, "user": user, "password": password,
                }
                break
        self._config = config
        self._write_config()

    @property
    def config(self) -> dict[str, Any]:
        if self._config is None:
            self._config = self._load_config()
        return self._config

    def _config_path(self) -> Path:
        pdir = self.root / "platform_workspace" / self.project_id
        return pdir / "multi_service_config.json"

    def _load_config(self) -> dict[str, Any]:
        path = self._config_path()
        if path.exists():
            return _load_json(path, {})
        # Fall back to single-service legacy config
        from .real_project_onboarding import load_real_project_config
        legacy = load_real_project_config(self.project_id, self.root)
        if legacy.get("base_url"):
            # Auto-migrate: wrap single-service config into multi-service format
            return {
                "project_name": legacy.get("project_name", self.project_id),
                "services": [{
                    "name": "default",
                    "base_url": legacy.get("base_url", ""),
                    "openapi_spec": str(legacy.get("openapi_path", "")),
                    "prd": str(legacy.get("prd_path", "")),
                    "description": "Auto-migrated from legacy single-service config",
                    "depends_on": [],
                    "exposes_to": [],
                    "auth": {
                        "type": legacy.get("auth_type", "password_login"),
                        "login_api": legacy.get("login_api", "/auth/login"),
                    },
                    "db": legacy.get("db", {}),
                }],
                "cross_service_contracts": [],
                "external_integrations": [],
            }
        return {}

    def init_from_example(self) -> dict[str, Any]:
        """Initialize a multi-service project with the example template."""
        self._config = dict(EXAMPLE_MULTI_SERVICE_CONFIG)
        self._config["project_name"] = self.project_id
        self._config_path().parent.mkdir(parents=True, exist_ok=True)
        _write_json(self._config_path(), self._config)
        return self._config

    def _write_config(self) -> None:
        """Persist current config back to disk."""
        if self._config:
            self._config_path().parent.mkdir(parents=True, exist_ok=True)
            _write_json(self._config_path(), self._config)

    def services(self) -> list[dict[str, Any]]:
        return self.config.get("services", [])

    def service_names(self) -> list[str]:
        return [s["name"] for s in self.services()]

    def get_service(self, name: str) -> dict[str, Any] | None:
        for s in self.services():
            if s["name"] == name:
                return s
        return None

    def cross_service_contracts(self) -> list[dict[str, Any]]:
        return self.config.get("cross_service_contracts", [])

    def external_integrations(self) -> list[dict[str, Any]]:
        return self.config.get("external_integrations", [])

    def service_dependencies(self) -> dict[str, list[str]]:
        """Build dependency graph: service_name → [dependencies]."""
        deps: dict[str, list[str]] = {}
        for s in self.services():
            deps[s["name"]] = s.get("depends_on", [])
        return deps

    def service_dependents(self) -> dict[str, list[str]]:
        """Build reverse dependency graph: service_name → [who depends on me]."""
        deps: dict[str, list[str]] = {}
        for s in self.services():
            for dep in s.get("depends_on", []):
                deps.setdefault(dep, []).append(s["name"])
        return deps

    def affected_services(self, changed_service: str) -> list[str]:
        """When a service changes, which other services might be affected?
        Returns the full transitive closure of dependents."""
        dependents = self.service_dependents()
        visited: set[str] = set()
        queue = [changed_service]
        while queue:
            svc = queue.pop(0)
            if svc in visited:
                continue
            visited.add(svc)
            for dep in dependents.get(svc, []):
                if dep not in visited:
                    queue.append(dep)
        return sorted(visited)

    def validate(self) -> dict[str, Any]:
        """Validate the multi-service configuration."""
        issues: list[str] = []
        names = set()

        for svc in self.services():
            name = svc.get("name", "")
            if not name:
                issues.append("Service missing 'name' field")
            elif name in names:
                issues.append(f"Duplicate service name: {name}")
            names.add(name)

            base_url = svc.get("base_url", "")
            if not base_url:
                issues.append(f"Service '{name}' missing base_url")

        for contract in self.cross_service_contracts():
            from_svc = contract.get("from_service", "")
            to_svc = contract.get("to_service", "")
            if from_svc not in names:
                issues.append(f"Cross-service contract references unknown service: {from_svc}")
            if to_svc not in names:
                issues.append(f"Cross-service contract references unknown service: {to_svc}")

        return {
            "valid": len(issues) == 0,
            "service_count": len(names),
            "cross_service_contract_count": len(self.cross_service_contracts()),
            "external_integration_count": len(self.external_integrations()),
            "issues": issues,
        }

    # ------------------------------------------------------------------
    # Customer-maintained project metadata (主链 1)
    # ------------------------------------------------------------------
    def set_project_metadata(self, *, industry: str | None = None,
                             module_scope: list[str] | None = None,
                             production_data_exclusion: list[str] | None = None) -> dict[str, Any]:
        """Persist customer-maintained project-level metadata.

        These fields are owned by the customer (not auto-inferred) and must
        drive downstream execution. ``production_data_exclusion`` is consulted
        by the probe executor as a hard safety boundary.
        """
        config = self.config
        if industry is not None:
            config[PROJECT_INDUSTRY_KEY] = str(industry).strip()
        if module_scope is not None:
            if not isinstance(module_scope, list):
                raise ValueError("module_scope must be a list[str]")
            config[PROJECT_MODULE_SCOPE_KEY] = [str(m).strip() for m in module_scope]
        if production_data_exclusion is not None:
            if not isinstance(production_data_exclusion, list):
                raise ValueError("production_data_exclusion must be a list[str]")
            config[PROJECT_PRODUCTION_DATA_EXCLUSION_KEY] = [
                str(p).strip() for p in production_data_exclusion if str(p).strip()
            ]
        self._config = config
        self._write_config()
        return config

    def get_execution_safety_boundary(self) -> list[str]:
        """Return the normalized list of forbidden data/endpoint patterns.

        Used by the probe executor to guarantee the system never touches
        production data the customer marked off-limits.
        """
        raw = self.config.get(PROJECT_PRODUCTION_DATA_EXCLUSION_KEY) or []
        return [str(p).strip() for p in raw if str(p).strip()]

    def project_metadata(self) -> dict[str, Any]:
        """Return the customer-maintained metadata block for API responses."""
        return {
            "industry": self.config.get(PROJECT_INDUSTRY_KEY, ""),
            "module_scope": self.config.get(PROJECT_MODULE_SCOPE_KEY, []),
            "production_data_exclusion": self.get_execution_safety_boundary(),
        }


# ---------------------------------------------------------------------------
# Production-data safety boundary
# ---------------------------------------------------------------------------

def match_production_data_exclusion(config: dict[str, Any], path: str,
                                    risk_type: str = "") -> str | None:
    """If ``path``/``risk_type`` hits a customer-defined forbidden-data pattern,
    return a stable reason string; otherwise ``None``.

    Pure, side-effect-free matcher — safe to call from the executor's decision
    path. Matching is case-insensitive substring against the request path plus
    an optional exact risk-type match. Patterns may be plain path fragments
    (e.g. ``/api/admin/users``) or ``re:``-prefixed regular expressions.
    """
    if not isinstance(config, dict):
        return None
    exclusions = config.get(PROJECT_PRODUCTION_DATA_EXCLUSION_KEY) or []
    if not exclusions:
        return None
    haystack = (path or "").lower()
    risk = (risk_type or "").lower()
    for raw in exclusions:
        pattern = str(raw).strip()
        if not pattern:
            continue
        if pattern.startswith("re:"):
            import re
            try:
                if re.search(pattern[3:], haystack, re.IGNORECASE):
                    return f"production_data_exclusion_matched:{pattern}"
            except re.error:
                # Treat an invalid regex as a literal fragment so it still guards.
                if pattern[3:].lower() in haystack:
                    return f"production_data_exclusion_matched:{pattern}"
            continue
        frag = pattern.lower()
        if frag in haystack or (risk and frag in risk):
            return f"production_data_exclusion_matched:{pattern}"
    return None


def _load_execution_safety_boundary(project: str, root: Path) -> dict[str, Any]:
    """主链 5/9 × 主链 1: load the customer-defined production-data exclusion
    list into the shape ``match_production_data_exclusion`` expects, so BOTH the
    real execution path (v12 pipeline) and the regression-runner HTTP probes
    honor the "生产数据禁触" hard requirement from a single source of truth.

    Returns {} when no boundary is configured (the matcher is then a no-op).
    The boundary is the single source of truth shared with
    grounded_probe_executor and regression_runner.
    """
    try:
        exclusions = MultiServiceProject(project, root).get_execution_safety_boundary()
    except Exception:
        exclusions = []
    if not exclusions:
        return {}
    return {"production_data_exclusion": exclusions}


# ---------------------------------------------------------------------------
# Cross-service bug detection hints
# ---------------------------------------------------------------------------

CROSS_SERVICE_BUG_PATTERNS = [
    {
        "pattern": "service_a_success_service_b_failure",
        "description": "服务A操作成功但服务B操作失败，导致分布式事务不一致",
        "example": "订单服务创建订单成功，但库存服务扣减失败 → 订单存在但库存未扣",
        "detection": "检查 order.status=confirmed 时，inventory 的预留量是否等于订单量",
    },
    {
        "pattern": "cross_service_data_drift",
        "description": "同一业务实体在不同服务中存在不一致的副本",
        "example": "订单服务中 order.total=100，支付服务中 payment.amount=99.99",
        "detection": "对账：order.total 与 payment.amount 的跨服务比较",
    },
    {
        "pattern": "service_version_mismatch",
        "description": "服务A升级了API但服务B还在用旧版本，导致字段缺失或类型错误",
        "example": "订单服务新增了 tax_rate 字段但支付服务未更新，导致计税错误",
        "detection": "对比各服务的 OpenAPI spec 变更，标记不兼容的字段变更",
    },
    {
        "pattern": "external_callback_timeout",
        "description": "外部系统回调超时或未到达，导致状态卡在中间态",
        "example": "极兔创建工单成功但 30 秒内未回调运单号",
        "detection": "监控 external_integrations 中定义的回调时效",
    },
    {
        "pattern": "cross_org_data_field_mismatch",
        "description": "跨组织数据流转时，字段映射错误或精度丢失",
        "example": "拼多多推送的订单金额是分（整数），我方系统期望是元（浮点），造成金额×100",
        "detection": "对比外部推送的原始数据与我方接收后的存储数据",
    },
]
