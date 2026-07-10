from __future__ import annotations

"""Phase103: Enterprise Quality Command Center foundation.

This module turns QualiBug runtime/onboarding/test results into the business
objects that the V1 UI/UX needs: industry templates, business-flow models,
environment readiness, executable test plans, risk cards, evidence bundles,
live-map snapshots, launch decisions, value metrics and executive reports.

The implementation is intentionally pure-Python and framework-free so it can be
used by the CLI, future HTTP API handlers, local demo data, and unit tests.
All payloads returned by this module are customer-safe by default: credentials
and sensitive business identifiers are summarized or redacted before they reach
UI/reporting layers.
"""

import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

PHASE103_VERSION = "phase103-enterprise-command-center-v1"
MANUAL_MINUTES_PER_TEST_POINT = 12
SAFE_EXECUTION_MODES = {"read_only", "write_restricted", "sandbox_write"}
WRITE_TEST_MODES = {"write", "write_restricted", "write_after_cleanup"}
LAUNCH_DECISIONS = {"GO", "CONDITIONAL_GO", "HOLD", "NO_GO", "UNKNOWN"}
SEVERITY_WEIGHTS = {"critical": 100, "high": 60, "medium": 30, "low": 10, "info": 2}

# ── 主链 4: probe-type gating is data-driven, not hardcoded industry vocab ──
# Which flows earn a state-consistency / report-accuracy probe is decided by the
# replaceable `probe_gating_keywords` overlay in policies/semantic_lexicon.json,
# merged (union) over this built-in fallback. Deployments extend per-industry
# without touching plan code.
_SEMANTIC_LEXICON_PATH = Path(__file__).resolve().parent / "policies" / "semantic_lexicon.json"
_PROBE_GATING_DEFAULT: dict[str, list[str]] = {
    "state_consistency": ["支付", "交易", "库存", "工单", "结算", "计费", "订单"],
    "report_accuracy": ["报表", "财务", "对账", "审计"],
}
_PROBE_GATING_CACHE: dict[str, list[str]] | None = None


def _probe_gating_keywords() -> dict[str, list[str]]:
    global _PROBE_GATING_CACHE
    if _PROBE_GATING_CACHE is not None:
        return _PROBE_GATING_CACHE
    merged: dict[str, list[str]] = {key: list(values) for key, values in _PROBE_GATING_DEFAULT.items()}
    try:
        data = json.loads(_SEMANTIC_LEXICON_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    overlay = data.get("probe_gating_keywords") if isinstance(data, dict) else None
    if isinstance(overlay, dict):
        for key, values in overlay.items():
            if key == "comment" or not isinstance(values, list):
                continue
            extra = [str(item).strip() for item in values if str(item).strip()]
            if not extra:
                continue
            base = merged.setdefault(str(key), [])
            for term in extra:
                if term not in base:
                    base.append(term)
    _PROBE_GATING_CACHE = merged
    return merged
RISK_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

SENSITIVE_KEY_RE = re.compile(
    r"(?i)(password|passwd|pwd|secret|client_secret|access_token|refresh_token|id_token|token|authorization|cookie|session|session_id|api[_-]?key|private[_-]?key)"
)
MOBILE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
ID_CARD_RE = re.compile(r"(?<!\d)(\d{17}[0-9Xx])(?!\d)")
BANK_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
HEADER_SECRET_RE = re.compile(r"(?i)((?:authorization|cookie|x-auth-token|x-access-token|x-session-id|api-key)\s*[:=]\s*)[^\s,;]+")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _slug(value: Any, prefix: str = "id", size: int = 12) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()[:size]}"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple | set):
        return list(value)
    return [value]


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


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def redact_value(value: Any) -> Any:
    """Return a customer-safe value for UI/reporting layers."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            safe[key_str] = "****" if SENSITIVE_KEY_RE.search(key_str) else redact_value(item)
        return safe
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    text = str(value)
    text = BEARER_RE.sub(r"\1****", text)
    text = HEADER_SECRET_RE.sub(r"\1****", text)
    text = MOBILE_RE.sub(lambda m: m.group(1)[:3] + "****" + m.group(1)[-4:], text)
    text = ID_CARD_RE.sub(lambda m: m.group(1)[:6] + "********" + m.group(1)[-4:], text)
    text = BANK_CARD_RE.sub("**** **** **** ****", text)
    return text


def _safe_text(value: Any, max_len: int = 260) -> str:
    text = str(redact_value(value if value is not None else "")).strip()
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _normalise_status(value: Any, passed_values: set[str] | None = None) -> str:
    raw = str(value or "unknown").strip().lower()
    passed_values = passed_values or {"passed", "pass", "ok", "success", "ready", "true", "valid"}
    if raw in passed_values:
        return "passed"
    if raw in {"failed", "fail", "error", "blocked", "false", "invalid"}:
        return "failed"
    if raw in {"partial", "partial_passed", "partial_ready", "warning", "degraded"}:
        return "partial_passed"
    if raw in {"not_configured", "missing", "pending", "unknown", "none", ""}:
        return "not_configured"
    return raw


INDUSTRY_TEMPLATES: dict[str, dict[str, Any]] = {
    "ecommerce": {
        "display_name": "电商 / 零售",
        "description": "适用于电商、零售、会员、订单、支付、库存与售后系统。",
        "default_roles": ["normal_user", "merchant_user", "ops_user", "warehouse_user", "finance_user", "admin_user"],
        "default_risk_focus": ["价格计算错误", "优惠券叠加异常", "重复支付", "库存超卖", "订单状态不一致", "退款金额异常", "越权访问订单", "销售报表金额不准确"],
        "default_business_flows": [
            {"flow_id": "login_auth", "name": "用户登录认证链路", "domain": "认证", "criticality": "critical", "nodes": ["用户入口", "登录认证", "Session 验证"], "roles": ["normal_user"]},
            {"flow_id": "order_payment", "name": "订单支付链路", "domain": "交易", "criticality": "critical", "nodes": ["登录认证", "购物车", "创建订单", "支付处理", "库存扣减", "发票生成"], "roles": ["normal_user", "finance_user"]},
            {"flow_id": "refund_after_sale", "name": "退款售后链路", "domain": "售后", "criticality": "high", "nodes": ["订单查询", "退款申请", "退款审核", "资金退回", "售后报表"], "roles": ["normal_user", "ops_user", "finance_user"]},
            {"flow_id": "admin_permission", "name": "后台权限管理链路", "domain": "权限", "criticality": "critical", "nodes": ["后台登录", "权限校验", "订单管理", "用户管理"], "roles": ["normal_user", "admin_user"]},
        ],
    },
    "finance": {
        "display_name": "金融 / 支付 / 保险",
        "description": "适用于资金交易、账户、支付清算、保险理赔、风控和审计系统。",
        "default_roles": ["normal_user", "operator_user", "risk_user", "finance_user", "auditor_user", "admin_user"],
        "default_risk_focus": ["资金状态不一致", "重复扣款", "越权访问账户", "交易未入账", "账务对账异常", "审批绕过", "审计日志缺失", "敏感数据泄露"],
        "default_business_flows": [
            {"flow_id": "account_auth", "name": "账户认证链路", "domain": "认证", "criticality": "critical", "nodes": ["用户认证", "账户绑定", "权限校验"], "roles": ["normal_user"]},
            {"flow_id": "fund_transaction", "name": "资金交易链路", "domain": "资金", "criticality": "critical", "nodes": ["账户查询", "交易提交", "风控校验", "扣款入账", "账务确认"], "roles": ["normal_user", "risk_user", "finance_user"]},
            {"flow_id": "reconciliation_audit", "name": "对账审计链路", "domain": "审计", "criticality": "critical", "nodes": ["交易流水", "账务对账", "审计报表", "审计留痕"], "roles": ["finance_user", "auditor_user"]},
        ],
    },
    "manufacturing": {
        "display_name": "制造 / ERP / MES",
        "description": "适用于制造企业 ERP、MES、WMS、生产计划、库存、质检和财务系统。",
        "default_roles": ["planner_user", "operator_user", "qc_user", "warehouse_user", "finance_user", "admin_user"],
        "default_risk_focus": ["工单状态流转异常", "库存扣减错误", "物料占用不一致", "质检结果丢失", "ERP/MES 数据不同步", "财务结算金额异常", "权限越权"],
        "default_business_flows": [
            {"flow_id": "production_order", "name": "生产工单流转链路", "domain": "生产", "criticality": "critical", "nodes": ["销售订单", "生产计划", "物料需求", "生产工单", "工序流转", "质检", "成品入库"], "roles": ["planner_user", "operator_user", "qc_user", "warehouse_user"]},
            {"flow_id": "inventory_sync", "name": "库存出入库链路", "domain": "库存", "criticality": "critical", "nodes": ["物料占用", "库存扣减", "成品入库", "ERP 同步", "库存报表"], "roles": ["warehouse_user", "planner_user"]},
            {"flow_id": "finance_settlement", "name": "财务结算链路", "domain": "财务", "criticality": "high", "nodes": ["生产完成", "成本归集", "财务结算", "财务报表"], "roles": ["finance_user", "admin_user"]},
        ],
    },
    "healthcare": {
        "display_name": "医疗 / 医院信息系统",
        "description": "适用于 HIS、EMR、LIS、PACS、医保结算和诊疗流程系统。",
        "default_roles": ["patient_user", "doctor_user", "nurse_user", "pharmacy_user", "billing_user", "auditor_user", "admin_user"],
        "default_risk_focus": ["患者数据错配", "医嘱状态异常", "处方流转中断", "收费金额不一致", "医保结算异常", "越权查看患者信息", "审计日志缺失"],
        "default_business_flows": [
            {"flow_id": "diagnosis_treatment", "name": "诊疗医嘱链路", "domain": "诊疗", "criticality": "critical", "nodes": ["患者建档", "预约挂号", "医生接诊", "医嘱开立", "报告查看"], "roles": ["doctor_user", "patient_user"]},
            {"flow_id": "prescription_billing", "name": "处方收费链路", "domain": "收费", "criticality": "critical", "nodes": ["处方流转", "药房发药", "收费结算", "医保结算"], "roles": ["doctor_user", "pharmacy_user", "billing_user"]},
        ],
    },
    "government": {
        "display_name": "政企 / 审批 / 办公",
        "description": "适用于政企统一登录、组织权限、流程审批、公文、附件和审计系统。",
        "default_roles": ["normal_user", "approver_user", "org_admin", "auditor_user", "admin_user"],
        "default_risk_focus": ["审批流程绕过", "普通用户越权审批", "附件权限泄露", "组织权限错配", "流程状态不一致", "电子签章缺失", "审计记录不完整"],
        "default_business_flows": [
            {"flow_id": "approval_workflow", "name": "审批流转链路", "domain": "流程", "criticality": "critical", "nodes": ["统一登录", "流程发起", "审批流转", "电子签章", "归档审计"], "roles": ["normal_user", "approver_user", "auditor_user"]},
            {"flow_id": "org_permission", "name": "组织权限链路", "domain": "权限", "criticality": "critical", "nodes": ["组织架构", "权限分配", "数据查询", "审计留痕"], "roles": ["normal_user", "org_admin", "auditor_user"]},
        ],
    },
    "education": {
        "display_name": "教育 / 教务 / 在线学习",
        "description": "适用于教务、选课、考试、成绩、缴费和在线学习系统。",
        "default_roles": ["student_user", "teacher_user", "finance_user", "admin_user"],
        "default_risk_focus": ["学生越权查看成绩", "教师权限错配", "选课容量异常", "成绩计算错误", "考试提交失败", "缴费状态不一致", "教务报表错误"],
        "default_business_flows": [
            {"flow_id": "course_exam", "name": "选课考试链路", "domain": "教务", "criticality": "critical", "nodes": ["学生登录", "选课", "考试提交", "成绩录入", "成绩查询"], "roles": ["student_user", "teacher_user"]},
            {"flow_id": "tuition_payment", "name": "缴费结算链路", "domain": "缴费", "criticality": "high", "nodes": ["缴费订单", "支付", "缴费状态", "财务报表"], "roles": ["student_user", "finance_user"]},
        ],
    },
    "logistics": {
        "display_name": "物流 / 供应链",
        "description": "适用于订单接入、仓储、运输调度、轨迹、签收和结算系统。",
        "default_roles": ["customer_user", "warehouse_user", "dispatcher_user", "carrier_user", "finance_user", "admin_user"],
        "default_risk_focus": ["库存锁定失败", "订单状态不同步", "轨迹更新延迟", "重复出库", "签收状态异常", "费用结算错误", "承运商接口异常"],
        "default_business_flows": [
            {"flow_id": "fulfillment_delivery", "name": "订单履约配送链路", "domain": "履约", "criticality": "critical", "nodes": ["订单接入", "仓库分配", "库存锁定", "拣货出库", "运输调度", "轨迹更新", "签收"], "roles": ["customer_user", "warehouse_user", "dispatcher_user", "carrier_user"]},
            {"flow_id": "settlement_reconciliation", "name": "结算对账链路", "domain": "结算", "criticality": "high", "nodes": ["签收确认", "费用计算", "结算对账", "客户通知"], "roles": ["finance_user", "customer_user"]},
        ],
    },
    "saas": {
        "display_name": "SaaS / 多租户企业软件",
        "description": "适用于多租户、组织管理、角色权限、订阅计费、API 与第三方集成系统。",
        "default_roles": ["tenant_admin", "normal_user", "billing_admin", "auditor_user", "cross_tenant_user", "admin_user"],
        "default_risk_focus": ["跨租户数据泄露", "普通用户越权访问", "订阅权限未生效", "套餐限制绕过", "API token 权限过大", "组织数据错配", "审计日志缺失"],
        "default_business_flows": [
            {"flow_id": "tenant_isolation", "name": "多租户隔离链路", "domain": "安全", "criticality": "critical", "nodes": ["租户注册", "用户登录", "组织管理", "数据隔离", "审计日志"], "roles": ["tenant_admin", "normal_user", "cross_tenant_user"]},
            {"flow_id": "subscription_billing", "name": "订阅计费链路", "domain": "计费", "criticality": "high", "nodes": ["套餐配置", "订阅权限", "计费扣费", "报表导出"], "roles": ["billing_admin", "tenant_admin"]},
            {"flow_id": "api_integration", "name": "API 与第三方集成链路", "domain": "集成", "criticality": "high", "nodes": ["API Token", "接口调用", "权限范围", "第三方集成"], "roles": ["tenant_admin", "admin_user"]},
        ],
    },
}


def list_industry_templates() -> list[dict[str, Any]]:
    """Return customer-safe summaries for the built-in industry templates."""
    return [
        {
            "template_id": f"industry_{industry}",
            "industry": industry,
            "display_name": data["display_name"],
            "description": data["description"],
            "business_flow_count": len(data["default_business_flows"]),
            "default_roles": list(data["default_roles"]),
            "default_risk_focus": list(data["default_risk_focus"]),
        }
        for industry, data in sorted(INDUSTRY_TEMPLATES.items())
    ]


def get_industry_template(industry_or_template_id: str) -> dict[str, Any]:
    industry = (industry_or_template_id or "").replace("industry_", "").strip().lower()
    if not industry or industry in {"unknown", "unknown_general_business", "general", "general_business", "auto"}:
        raise ValueError(
            f"industry template requires an explicit evidence-selected industry, got: {industry_or_template_id!r}"
        )
    if industry not in INDUSTRY_TEMPLATES:
        raise ValueError(f"unsupported industry template: {industry_or_template_id}")
    data = deepcopy(INDUSTRY_TEMPLATES[industry])
    data["template_id"] = f"industry_{industry}"
    data["industry"] = industry
    return data


def resolve_industry_template_or_general(industry_or_template_id: str) -> dict[str, Any]:
    """Return an industry template only when explicitly selected; else a general empty pack.

    Never defaults to ecommerce. Callers that need vertical flows must pass a
    recognized industry id from evidence-gated inference or operator selection.
    """
    industry = (industry_or_template_id or "").replace("industry_", "").strip().lower()
    if industry in INDUSTRY_TEMPLATES:
        return get_industry_template(industry)
    return {
        "template_id": "industry_general_business",
        "industry": "general_business",
        "display_name": "通用业务（未识别垂直行业）",
        "description": "行业证据不足时不套用垂直模板；仅保留通用角色与空流程，等待来源证据激活。",
        "default_roles": ["normal_user", "admin_user", "auditor_user"],
        "default_risk_focus": ["越权访问", "租户隔离", "状态机绕过", "幂等缺失", "审计日志缺失"],
        "default_business_flows": [],
        "activation": "suppressed_unknown_general_business",
    }


def build_customer_business_model(
    project_id: str,
    industry: str,
    *,
    enabled_flow_ids: Iterable[str] | None = None,
    critical_flow_ids: Iterable[str] | None = None,
    role_config: Mapping[str, Any] | None = None,
    custom_terms: Mapping[str, str] | None = None,
    approved_by: str | None = None,
) -> dict[str, Any]:
    """Create a V1 customer business model from an industry template."""
    template = get_industry_template(industry)
    enabled_set = set(enabled_flow_ids or [flow["flow_id"] for flow in template["default_business_flows"]])
    critical_set = set(critical_flow_ids or [])
    role_config = role_config or {}
    flows: list[dict[str, Any]] = []
    all_roles: set[str] = set()
    for flow in template["default_business_flows"]:
        flow_id = str(flow["flow_id"])
        roles = list(flow.get("roles", []))
        all_roles.update(roles)
        criticality = "critical" if flow_id in critical_set else flow.get("criticality", "medium")
        test_mode = str(flow.get("test_mode") or "read_only")
        flows.append(
            {
                "business_flow_id": f"flow_{flow_id}",
                "template_flow_id": flow_id,
                "project_id": project_id,
                "name": flow["name"],
                "business_domain": flow.get("domain", "通用"),
                "criticality": criticality,
                "enabled": flow_id in enabled_set,
                "test_mode": test_mode,
                "status": "not_started",
                "nodes": [
                    {
                        "node_id": f"node_{_slug(project_id + flow_id + node, 'n', 8)}",
                        "label": node,
                        "node_type": _infer_node_type(node),
                    }
                    for node in flow.get("nodes", [])
                ],
                "recommended_risks": _recommended_risks_for_flow(template, flow),
                "required_roles": roles,
            }
        )
    roles = []
    for role in sorted(set(template["default_roles"]) | all_roles):
        cfg = role_config.get(role, {}) if isinstance(role_config.get(role, {}), Mapping) else {"configured": bool(role_config.get(role))}
        configured = bool(cfg.get("configured", False))
        auth_status = str(cfg.get("auth_status") or ("not_configured" if not configured else "unknown"))
        roles.append(
            {
                "role_key": role,
                "display_name": _role_display_name(role),
                "required": role in all_roles,
                "configured": configured,
                "auth_status": auth_status,
                "affected_flows": [f["business_flow_id"] for f in flows if role in f["required_roles"]],
            }
        )
    return {
        "project_id": project_id,
        "industry": template["industry"],
        "template_id": template["template_id"],
        "display_name": template["display_name"],
        "confirmed_business_flows": flows,
        "confirmed_roles": roles,
        "confirmed_risk_focus": list(template["default_risk_focus"]),
        "excluded_flows": [f["business_flow_id"] for f in flows if not f["enabled"]],
        "custom_terms": dict(custom_terms or {}),
        "approved_by": approved_by,
        "approved_at": _now() if approved_by else None,
        "version": PHASE103_VERSION,
    }


def _infer_node_type(label: str) -> str:
    text = label.lower()
    if any(word in label for word in ["登录", "认证", "SSO", "Token", "Session"]):
        return "auth"
    if any(word in label for word in ["权限", "角色", "隔离", "审计"]):
        return "security"
    if any(word in label for word in ["报表", "数据库", "流水", "数据"]):
        return "data"
    if any(word in label for word in ["ERP", "MES", "WMS", "第三方", "承运商"]):
        return "external_system"
    return "business_step"


def _recommended_risks_for_flow(template: Mapping[str, Any], flow: Mapping[str, Any]) -> list[str]:
    focus = list(template.get("default_risk_focus", []))
    name = str(flow.get("name", ""))
    domain = str(flow.get("domain", ""))
    selected: list[str] = []
    for risk in focus:
        if any(token and token in risk for token in [domain, name[:2], "权限", "状态", "报表", "同步", "重复", "越权"]):
            selected.append(risk)
    return selected[:5] or focus[:3]


def _role_display_name(role: str) -> str:
    names = {
        "normal_user": "普通用户",
        "admin_user": "管理员",
        "finance_user": "财务人员",
        "auditor_user": "审计人员",
        "tenant_admin": "租户管理员",
        "cross_tenant_user": "跨租户对照账号",
        "planner_user": "计划员",
        "operator_user": "生产操作员",
        "qc_user": "质检员",
        "warehouse_user": "仓库人员",
        "doctor_user": "医生",
        "patient_user": "患者",
        "teacher_user": "教师",
        "student_user": "学生",
    }
    return names.get(role, role.replace("_", " "))


def build_environment_readiness_report(
    project_id: str,
    preflight_result: Mapping[str, Any] | None = None,
    business_model: Mapping[str, Any] | None = None,
    *,
    safe_execution_mode: str | None = None,
) -> dict[str, Any]:
    """Normalize runtime/onboarding preflight output into a UI-ready report."""
    raw = dict(preflight_result or {})
    checks_raw = raw.get("checks") if isinstance(raw.get("checks"), Mapping) else raw
    url = _extract_check(checks_raw, "url", aliases=("base_url", "url_check"))
    dns = _extract_check(checks_raw, "dns", aliases=("host", "host_resolution"))
    http = _extract_check(checks_raw, "http", aliases=("reachability", "http_reachability"))
    auth = _extract_check(checks_raw, "auth", aliases=("authentication", "connectivity_auth", "login"))
    session = _extract_check(checks_raw, "session", aliases=("session_health", "session_health_check"))
    smoke = _extract_check(checks_raw, "api_smoke", aliases=("authenticated_api_smoke", "protected_api_smoke", "runtime_smoke"))
    blockers = _as_list(raw.get("interactive_auth_blockers") or raw.get("auth_blockers") or raw.get("blockers"))
    required_inputs = [_normalise_required_input(item) for item in _as_list(raw.get("required_customer_inputs"))]

    role_inputs = _missing_role_inputs(business_model)
    required_inputs.extend(role_inputs)

    checks = {
        "url": _normalise_url_check(raw, url),
        "dns": _normalise_generic_check("dns", dns),
        "http": _normalise_http_check(http),
        "auth": _normalise_auth_check(auth),
        "session": _normalise_session_check(session),
        "api_smoke": _normalise_api_smoke(smoke),
        "interactive_auth_blockers": [redact_value(b) for b in blockers],
    }
    blockers_text = _environment_blockers(checks, required_inputs)
    score = _environment_score(checks, required_inputs)
    if score >= 90 and not blockers_text:
        status = "ready"
    elif any(_normalise_status(checks[k].get("status")) == "failed" for k in ["url", "dns", "http", "auth"]):
        status = "blocked"
    elif blockers_text:
        status = "needs_customer_input" if required_inputs else "partial_ready"
    else:
        status = "partial_ready"
    mode = safe_execution_mode or raw.get("safe_execution_mode") or "read_only"
    if mode not in SAFE_EXECUTION_MODES:
        mode = "read_only"
    allow_formal_test = status == "ready" or (status == "partial_ready" and checks["auth"].get("status") == "passed")
    return {
        "project_id": project_id,
        "status": status,
        "score": score,
        "allow_formal_test": allow_formal_test,
        "safe_execution_mode": mode,
        "current_blockers": blockers_text,
        "suggested_actions": _suggested_environment_actions(checks, required_inputs),
        "last_checked_at": raw.get("last_checked_at") or raw.get("checked_at") or _now(),
        "checks": checks,
        "required_customer_inputs": required_inputs,
        "redaction_status": "safe",
        "version": PHASE103_VERSION,
    }


def _extract_check(data: Mapping[str, Any], name: str, aliases: Sequence[str] = ()) -> dict[str, Any]:
    for key in (name, *aliases):
        value = data.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _normalise_url_check(raw: Mapping[str, Any], check: Mapping[str, Any]) -> dict[str, Any]:
    base_url = raw.get("base_url") or check.get("base_url") or check.get("url")
    valid = check.get("valid")
    if valid is None:
        valid = bool(base_url) and str(base_url).startswith(("http://", "https://"))
    return {
        "base_url": _safe_text(base_url, 180),
        "valid": bool(valid),
        "status": "passed" if valid else "failed",
        "scheme": check.get("scheme"),
        "host": _safe_text(check.get("host") or _host_from_url(base_url), 120),
        "port": check.get("port"),
        "issue": _safe_text(check.get("issue") or (None if valid else "base_url 缺失或格式无效。")),
    }


def _host_from_url(url: Any) -> str | None:
    text = str(url or "")
    if "://" not in text:
        return None
    return text.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0] or None


def _normalise_generic_check(name: str, check: Mapping[str, Any]) -> dict[str, Any]:
    status = _normalise_status(check.get("status") or check.get("result") or check.get("passed"))
    if not check:
        status = "not_configured"
    return {
        "name": name,
        "status": status,
        "result": status,
        "interpretation": _safe_text(check.get("interpretation") or check.get("message") or "尚未获得该检查结果。"),
        "latency_ms": check.get("latency_ms"),
        "redaction_status": "safe",
    }


def _normalise_http_check(check: Mapping[str, Any]) -> dict[str, Any]:
    status_code = _safe_int(check.get("status_code") or check.get("code"), -1)
    reachable = check.get("reachable")
    if reachable is None:
        reachable = status_code > 0 and status_code not in {0, 599}
    status = "passed" if reachable else "failed"
    interpretation = check.get("interpretation")
    if not interpretation:
        if status_code in {401, 403}:
            interpretation = "服务可达，但需要认证或权限。"
        elif status_code == 404:
            interpretation = "服务可达，但路径可能不正确。"
        elif status_code >= 500:
            interpretation = "客户服务返回 5xx，可能存在服务异常。"
        elif status_code > 0:
            interpretation = "服务入口可访问。"
        else:
            interpretation = "尚未获得 HTTP 可达性结果。"
    return {
        "reachable": bool(reachable),
        "status": status,
        "status_code": status_code if status_code >= 0 else None,
        "content_type": _safe_text(check.get("content_type"), 80),
        "interpretation": _safe_text(interpretation),
        "latency_ms": check.get("latency_ms"),
        "redaction_status": "safe",
    }


def _normalise_auth_check(check: Mapping[str, Any]) -> dict[str, Any]:
    status = _normalise_status(check.get("status") or check.get("result"))
    token = bool(check.get("access_token_acquired") or check.get("token_acquired") or check.get("access_token"))
    refresh = bool(check.get("refresh_token_acquired") or check.get("refresh_token"))
    cookie_count = _safe_int(check.get("cookie_count") or len(_as_list(check.get("cookies"))), 0)
    csrf = bool(check.get("csrf_token_acquired") or check.get("xsrf_token_acquired") or check.get("csrf_token"))
    if status == "not_configured" and (token or cookie_count or csrf):
        status = "passed"
    return {
        "auth_type": _safe_text(check.get("auth_type") or check.get("type") or "unknown", 80),
        "status": status,
        "access_token_acquired": token,
        "refresh_token_acquired": refresh,
        "cookie_count": cookie_count,
        "csrf_token_acquired": csrf,
        "oauth_session_count": _safe_int(check.get("oauth_session_count"), 0),
        "issue": _safe_text(check.get("issue") or check.get("error")),
        "redaction_status": "safe",
    }


def _normalise_session_check(check: Mapping[str, Any]) -> dict[str, Any]:
    status = _normalise_status(check.get("status") or check.get("result"))
    status_code = _safe_int(check.get("status_code"), -1)
    content_type = str(check.get("content_type") or "")
    if status == "not_configured" and status_code in range(200, 300):
        status = "passed" if "html" not in content_type.lower() else "failed"
    interpretation = check.get("interpretation")
    if not interpretation:
        if status == "passed":
            interpretation = "session 有效，认证后接口可访问。"
        elif "html" in content_type.lower():
            interpretation = "认证后接口返回 HTML 登录页，session 可能未生效或被 SSO 重定向。"
        elif status_code in {401, 403}:
            interpretation = "token/cookie 已配置，但认证后接口仍返回 401/403。"
        else:
            interpretation = "尚未完成 session 健康检查。"
    return {
        "path": _safe_text(check.get("path") or check.get("session_health_path"), 180),
        "status": status,
        "status_code": status_code if status_code >= 0 else None,
        "content_type": _safe_text(content_type, 80),
        "interpretation": _safe_text(interpretation),
        "redaction_status": "safe",
    }


def _normalise_api_smoke(check: Mapping[str, Any]) -> dict[str, Any]:
    items = []
    for item in _as_list(check.get("items") or check.get("paths") or check.get("results")):
        if not isinstance(item, Mapping):
            continue
        status_code = _safe_int(item.get("status_code"), -1)
        content_type = str(item.get("content_type") or "")
        result = _normalise_status(item.get("result") or item.get("status"))
        if result == "not_configured" and status_code > 0:
            result = "passed" if 200 <= status_code < 300 and "html" not in content_type.lower() else "failed"
        issue = item.get("issue") or item.get("interpretation")
        if not issue and result == "failed":
            issue = _api_smoke_issue(status_code, content_type)
        items.append(
            {
                "path": _safe_text(item.get("path") or item.get("url"), 180),
                "method": str(item.get("method") or "GET").upper(),
                "status_code": status_code if status_code >= 0 else None,
                "content_type": _safe_text(content_type, 80),
                "result": result,
                "issue": _safe_text(issue),
                "affected_flow": _safe_text(item.get("affected_flow") or item.get("business_flow"), 80),
            }
        )
    total = _safe_int(check.get("total"), len(items))
    passed = _safe_int(check.get("passed"), sum(1 for item in items if item["result"] == "passed"))
    failed = _safe_int(check.get("failed"), sum(1 for item in items if item["result"] == "failed"))
    if not items and not check:
        status = "not_configured"
    elif failed and passed:
        status = "partial_passed"
    elif failed:
        status = "failed"
    else:
        status = "passed"
    return {"status": status, "total": total, "passed": passed, "failed": failed, "items": items, "redaction_status": "safe"}


def _api_smoke_issue(status_code: int, content_type: str) -> str:
    if status_code in {401, 403}:
        return "认证成功，但测试账号缺少该业务 API 权限或认证上下文未正确生效。"
    if status_code == 404:
        return "base_url 或 API path 可能不正确。"
    if status_code in {301, 302, 303, 307, 308}:
        return "认证上下文可能丢失，业务 API 被重定向。"
    if "html" in content_type.lower():
        return "认证后业务 API 返回 HTML 登录页，session 可能未生效或被 SSO 拦截。"
    if status_code >= 500:
        return "客户服务返回 5xx，业务 API 可能异常。"
    return "API smoke 未通过。"


def _normalise_required_input(item: Any) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        item = {"title": str(item)}
    title = _safe_text(item.get("title") or item.get("missing") or item.get("name") or "待客户补充信息")
    return {
        "input_id": item.get("input_id") or _slug(title, "input", 10),
        "type": _safe_text(item.get("type") or "customer_input", 80),
        "title": title,
        "priority": _safe_text(item.get("priority") or "medium", 20),
        "impact": _safe_text(item.get("impact") or item.get("business_impact") or "影响后续业务链路测试。"),
        "why_needed": _safe_text(item.get("why_needed") or item.get("reason") or "用于完成客户环境接入和测试准入。"),
        "suggested_input": _safe_text(item.get("suggested_input") or item.get("suggestion") or "请客户补充对应配置或账号。"),
        "affected_flows": [str(x) for x in _as_list(item.get("affected_flows"))],
        "status": _safe_text(item.get("status") or "pending", 40),
    }


def _missing_role_inputs(business_model: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not business_model:
        return []
    inputs = []
    for role in business_model.get("confirmed_roles", []):
        if isinstance(role, Mapping) and role.get("required") and not role.get("configured"):
            role_key = str(role.get("role_key") or "test_account")
            inputs.append(
                _normalise_required_input(
                    {
                        "input_id": f"input_{role_key}",
                        "type": "test_account",
                        "title": f"缺少 {role_key} 测试账号",
                        "priority": "high",
                        "impact": f"{role.get('display_name') or role_key} 相关业务链路无法完整验证。",
                        "why_needed": "系统需要使用该角色验证业务链路、权限边界和证据复现。",
                        "suggested_input": f"请提供具备对应权限的 {role.get('display_name') or role_key} 测试账号。",
                        "affected_flows": role.get("affected_flows") or [],
                    }
                )
            )
    return inputs


def _environment_blockers(checks: Mapping[str, Any], required_inputs: Sequence[Mapping[str, Any]]) -> list[str]:
    blockers: list[str] = []
    for key, label in [("url", "URL 配置"), ("dns", "DNS / Host 解析"), ("http", "HTTP 可达性"), ("auth", "认证链路")]:
        status = _normalise_status(checks[key].get("status"))
        if status == "failed":
            blockers.append(f"{label} 未通过")
    if checks["api_smoke"].get("failed", 0):
        blockers.append(f"API Smoke 有 {checks['api_smoke'].get('failed')} 项失败")
    blockers.extend(item["title"] for item in required_inputs if item.get("priority") in {"high", "critical"})
    return blockers[:10]


def _environment_score(checks: Mapping[str, Any], required_inputs: Sequence[Mapping[str, Any]]) -> int:
    weights = {"url": 15, "dns": 10, "http": 15, "auth": 25, "session": 15, "api_smoke": 20}
    score = 0.0
    for key, weight in weights.items():
        status = _normalise_status(checks[key].get("status"))
        if status == "passed":
            score += weight
        elif status == "partial_passed":
            score += weight * 0.55
        elif status == "not_configured":
            score += weight * 0.15
    score -= min(20, len(required_inputs) * 4)
    return int(max(0, min(100, round(score))))


def _suggested_environment_actions(checks: Mapping[str, Any], required_inputs: Sequence[Mapping[str, Any]]) -> list[str]:
    actions: list[str] = []
    if _normalise_status(checks["url"].get("status")) == "failed":
        actions.append("请提供完整可访问的 http/https base_url。")
    if _normalise_status(checks["dns"].get("status")) == "failed":
        actions.append("请确认 VPN、内网 DNS 或访问白名单。")
    if _normalise_status(checks["auth"].get("status")) in {"failed", "not_configured"}:
        actions.append("请确认认证方式、测试账号、CSRF/OAuth 配置和 session_health_path。")
    if checks["api_smoke"].get("failed", 0):
        actions.append("请确认认证后业务 API 路径、测试账号权限或租户绑定。")
    actions.extend(item.get("suggested_input") for item in required_inputs if item.get("suggested_input"))
    return [str(redact_value(a)) for a in actions[:10]]


def generate_ai_test_plan(
    project_id: str,
    business_model: Mapping[str, Any],
    environment_report: Mapping[str, Any],
    *,
    plan_name: str | None = None,
) -> dict[str, Any]:
    """Generate a UI-ready AI test plan from business model + env readiness."""
    safe_mode = str(environment_report.get("safe_execution_mode") or "read_only")
    env_blocked = environment_report.get("status") in {"blocked", "unknown"}
    configured_roles = {
        str(role.get("role_key"))
        for role in business_model.get("confirmed_roles", [])
        if isinstance(role, Mapping) and (role.get("configured") or role.get("auth_status") in {"passed", "auth_passed"})
    }
    required_input_titles = {str(item.get("title")) for item in environment_report.get("required_customer_inputs", []) if isinstance(item, Mapping)}
    probe_groups = []
    total_equivalent = 0
    executable_flows = 0
    blocked_flows = 0
    core_executable = 0
    for flow in business_model.get("confirmed_business_flows", []):
        if not isinstance(flow, Mapping) or not flow.get("enabled", True):
            continue
        required_roles = set(map(str, flow.get("required_roles", [])))
        missing_roles = sorted(role for role in required_roles if role not in configured_roles)
        flow_test_mode = str(flow.get("test_mode") or "read_only")
        probes = _default_probes_for_flow(flow, safe_mode)
        for probe in probes:
            blocked_reason = None
            if env_blocked:
                blocked_reason = "客户环境未通过准入，无法执行该探针。"
            elif missing_roles and any(role in probe["required_roles"] for role in missing_roles):
                blocked_reason = f"缺少必要测试账号：{', '.join(missing_roles)}。"
            elif probe["execution_mode"] in WRITE_TEST_MODES and safe_mode == "read_only":
                blocked_reason = "当前安全模式为只读测试，未授权写入型探针。"
            elif flow_test_mode == "暂不测试":
                blocked_reason = "该业务链路已被客户标记为暂不测试。"
            probe["executable"] = blocked_reason is None
            probe["blocked_reason"] = blocked_reason
        probe_total = len(probes)
        probe_executable = sum(1 for probe in probes if probe["executable"])
        probe_blocked = probe_total - probe_executable
        group_status = "executable" if probe_executable == probe_total else "partial_executable" if probe_executable else "blocked"
        if group_status == "blocked":
            blocked_flows += 1
        else:
            executable_flows += 1
            if flow.get("criticality") == "critical":
                core_executable += 1
        group_points = sum(_safe_int(probe.get("estimated_test_points"), 1) for probe in probes if probe["executable"])
        total_equivalent += group_points
        probe_groups.append(
            {
                "group_id": _slug([project_id, flow.get("business_flow_id")], "group", 10),
                "business_flow_id": flow.get("business_flow_id"),
                "business_flow_name": flow.get("name"),
                "status": group_status,
                "probe_total": probe_total,
                "probe_executable": probe_executable,
                "probe_blocked": probe_blocked,
                "blocked_reasons": sorted({p["blocked_reason"] for p in probes if p.get("blocked_reason")}),
                "probes": probes,
            }
        )
    plan_id = _slug([project_id, plan_name, total_equivalent, len(probe_groups)], "plan", 10)
    return {
        "plan_id": plan_id,
        "project_id": project_id,
        "name": plan_name or "V1 AI 质量风险测试计划",
        "status": "generated",
        "generated_at": _now(),
        "coverage_summary": {
            "business_flow_total": len(probe_groups),
            "business_flow_executable": executable_flows,
            "business_flow_blocked": blocked_flows,
            "core_flow_executable": core_executable,
        },
        "estimated_value": {
            "equivalent_test_points": total_equivalent,
            "estimated_hours_saved": round(total_equivalent * MANUAL_MINUTES_PER_TEST_POINT / 60, 2),
            "manual_minutes_per_test_point": MANUAL_MINUTES_PER_TEST_POINT,
        },
        "safe_execution_mode": safe_mode,
        "probe_groups": probe_groups,
        "required_customer_inputs": [item for item in environment_report.get("required_customer_inputs", []) if item.get("title") in required_input_titles],
        "version": PHASE103_VERSION,
    }


def _default_probes_for_flow(flow: Mapping[str, Any], safe_mode: str) -> list[dict[str, Any]]:
    flow_id = str(flow.get("business_flow_id"))
    # Industry-agnostic: use only the roles the flow actually declares. Never
    # fabricate a business role (e.g. "normal_user") — a role-less flow in a
    # non-consumer industry must not be forced into a consumer role.
    roles = [role for role in map(str, flow.get("required_roles", []) or []) if role.strip()]
    probes: list[dict[str, Any]] = [
        {
            "probe_id": _slug([flow_id, "auth"], "probe", 10),
            "name": f"{flow.get('name')}认证与 session 探针",
            "probe_type": "auth_probe",
            "business_flow_id": flow_id,
            "required_roles": roles[:1],
            "execution_mode": "read_only",
            "estimated_test_points": 2,
        },
        {
            "probe_id": _slug([flow_id, "permission"], "probe", 10),
            "name": f"{flow.get('name')}权限边界探针",
            "probe_type": "role_permission_probe",
            "business_flow_id": flow_id,
            "required_roles": roles,
            "execution_mode": "read_only",
            "estimated_test_points": max(2, len(roles)),
        },
        {
            "probe_id": _slug([flow_id, "flow"], "probe", 10),
            "name": f"{flow.get('name')}业务链路只读验证探针",
            "probe_type": "business_flow_probe",
            "business_flow_id": flow_id,
            "required_roles": roles[:1],
            "execution_mode": "read_only",
            "estimated_test_points": max(3, len(flow.get("nodes", []))),
        },
    ]
    if any(keyword in str(flow.get("name")) for keyword in _probe_gating_keywords().get("state_consistency", [])):
        probes.append(
            {
                "probe_id": _slug([flow_id, "state"], "probe", 10),
                "name": f"{flow.get('name')}状态一致性探针",
                "probe_type": "state_consistency_probe",
                "business_flow_id": flow_id,
                "required_roles": roles[:1],
                "execution_mode": "write_restricted",
                "estimated_test_points": 4,
            }
        )
    if any(keyword in str(flow.get("name")) for keyword in _probe_gating_keywords().get("report_accuracy", [])):
        probes.append(
            {
                "probe_id": _slug([flow_id, "report"], "probe", 10),
                "name": f"{flow.get('name')}报表准确性探针",
                "probe_type": "report_accuracy_probe",
                "business_flow_id": flow_id,
                "required_roles": roles,
                "execution_mode": "read_only",
                "estimated_test_points": 3,
            }
        )
    return probes


def translate_risk_finding(
    project_id: str,
    technical_finding: Mapping[str, Any],
    business_model: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Translate a technical finding into an executive-friendly risk card."""
    safe = redact_value(dict(technical_finding))
    raw_text = " ".join(str(safe.get(k, "")) for k in ["title", "technical_title", "message", "path", "error", "observed_issue", "risk_type"]).lower()
    flow = _match_business_flow(technical_finding, business_model)
    risk_type, title, impact, severity, launch_blocking = _risk_translation_rule(raw_text, flow)
    severity = str(technical_finding.get("severity") or severity).lower()
    risk_id = technical_finding.get("risk_id") or _slug([project_id, safe, title], "risk", 12)
    evidence_score = _clamp(_safe_float(technical_finding.get("evidence_score"), 0.75))
    reproducibility = _clamp(_safe_float(technical_finding.get("reproducibility_score"), 0.7))
    confidence = _clamp(_safe_float(technical_finding.get("confidence_score"), (evidence_score + reproducibility) / 2))
    return {
        "risk_id": risk_id,
        "project_id": project_id,
        "title": _safe_text(technical_finding.get("business_title") or title, 120),
        "technical_title": _safe_text(technical_finding.get("technical_title") or technical_finding.get("title") or technical_finding.get("message"), 180),
        "severity": severity if severity in SEVERITY_WEIGHTS else "medium",
        "risk_type": str(technical_finding.get("risk_type") or risk_type),
        "business_impact": _safe_text(technical_finding.get("business_impact") or impact, 320),
        "affected_business_flow": {
            "business_flow_id": flow.get("business_flow_id") if flow else technical_finding.get("business_flow_id"),
            "name": flow.get("name") if flow else technical_finding.get("business_flow_name") or "未归属业务链路",
        },
        "affected_modules": [str(redact_value(x)) for x in _as_list(technical_finding.get("affected_modules") or technical_finding.get("module")) if x],
        "affected_roles": [str(redact_value(x)) for x in _as_list(technical_finding.get("affected_roles") or technical_finding.get("role") or _role_from_text(raw_text)) if x],
        "launch_blocking": bool(technical_finding.get("launch_blocking", launch_blocking)),
        "confidence_score": round(confidence, 2),
        "evidence_score": round(evidence_score, 2),
        "reproducibility_score": round(reproducibility, 2),
        "status": str(technical_finding.get("status") or "confirmed"),
        "suggested_action": _safe_text(technical_finding.get("suggested_action") or _suggested_action_for_risk(risk_type), 260),
        "owner": technical_finding.get("owner"),
        "first_seen_at": technical_finding.get("first_seen_at") or _now(),
        "last_verified_at": technical_finding.get("last_verified_at") or _now(),
        "redaction_status": "safe",
    }


def _match_business_flow(finding: Mapping[str, Any], business_model: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not business_model:
        return None
    text = " ".join(str(finding.get(k, "")) for k in ["business_flow_id", "business_flow_name", "path", "title", "message", "module"]).lower()
    flows = business_model.get("confirmed_business_flows", [])
    for flow in flows:
        if not isinstance(flow, Mapping):
            continue
        if str(flow.get("business_flow_id", "")).lower() in text or str(flow.get("template_flow_id", "")).lower() in text:
            return dict(flow)
        if any(str(node.get("label", "")).lower() in text for node in flow.get("nodes", []) if isinstance(node, Mapping)):
            return dict(flow)
        if any(token and token.lower() in text for token in str(flow.get("name", "")).split()):
            return dict(flow)
    keyword_map = {
        "payment": ["支付", "交易", "扣款", "payment", "pay"],
        "report": ["报表", "对账", "report", "finance"],
        "permission": ["权限", "admin", "role", "rbac", "越权"],
        "inventory": ["库存", "warehouse", "inventory"],
        "order": ["订单", "order", "工单"],
    }
    for flow in flows:
        name = str(flow.get("name", "")).lower()
        for tokens in keyword_map.values():
            if any(t.lower() in text for t in tokens) and any(t.lower() in name for t in tokens):
                return dict(flow)
    return dict(flows[0]) if flows else None


def _risk_translation_rule(raw_text: str, flow: Mapping[str, Any] | None) -> tuple[str, str, str, str, bool]:
    flow_name = flow.get("name") if flow else "相关业务链路"
    if any(token in raw_text for token in ["unauthorized", "authorization", "admin", "rbac", "越权", "403 expected", "权限"]):
        return (
            "authorization_bypass",
            "普通用户可访问受限管理资源",
            f"可能导致普通用户越权访问 {flow_name} 中的受限数据或管理能力，存在敏感信息暴露和权限边界失效风险。",
            "critical",
            True,
        )
    if any(token in raw_text for token in ["payment", "duplicate", "idempot", "支付", "重复", "扣款"]):
        return (
            "idempotency_issue",
            "支付重复提交可能导致业务状态不一致",
            f"可能造成 {flow_name} 中订单状态、支付流水或财务对账不一致，影响客户体验和交易准确性。",
            "critical",
            True,
        )
    if any(token in raw_text for token in ["report", "amount", "reconciliation", "报表", "金额", "对账"]):
        return (
            "report_accuracy",
            "报表金额或业务数据可能不准确",
            f"可能影响 {flow_name} 的经营分析、财务核算或管理决策，建议上线前完成修复和复测。",
            "high",
            True,
        )
    if any(token in raw_text for token in ["html login", "redirect", "session", "sso", "cookie", "认证", "登录页"]):
        return (
            "session_invalid",
            "认证后业务接口仍被重定向到登录页",
            f"说明认证上下文未完全打通，{flow_name} 后续业务测试无法完整执行。",
            "medium",
            False,
        )
    if any(token in raw_text for token in ["timeout", "dns", "connection", "环境", "不可达"]):
        return (
            "environment_blocker",
            "客户环境前置条件未满足",
            f"当前环境阻断 {flow_name} 的自动化验证，需要补齐网络、账号、权限或 API 路径后重新预检。",
            "medium",
            False,
        )
    return (
        "business_flow_blocker",
        "核心业务链路存在异常风险",
        f"AI 在 {flow_name} 中发现异常结果，可能影响业务连续性或上线稳定性，建议结合证据链确认并处理。",
        "medium",
        False,
    )


def _role_from_text(raw_text: str) -> list[str]:
    roles = []
    for role in ["normal_user", "admin_user", "finance_user", "tenant_admin", "auditor_user"]:
        if role.lower() in raw_text:
            roles.append(role)
    return roles


def _suggested_action_for_risk(risk_type: str) -> str:
    suggestions = {
        "authorization_bypass": "优先修复 RBAC/角色权限校验，修复后执行越权回归探针。",
        "idempotency_issue": "增加幂等键、状态锁和重复提交保护，修复后执行状态一致性回归探针。",
        "report_accuracy": "核对报表聚合口径与底层业务数据，修复后执行报表准确性回归探针。",
        "session_invalid": "检查 token/cookie/session 作用域、租户绑定和 SSO 网关配置后重新预检。",
        "environment_blocker": "按客户补料清单补齐 URL、账号、权限、API 路径或网络白名单。",
    }
    return suggestions.get(risk_type, "根据证据链定位业务异常，修复后执行关联回归探针。")


def build_evidence_bundle(risk: Mapping[str, Any], raw_evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw = redact_value(dict(raw_evidence or {}))
    risk_id = risk.get("risk_id") or _slug(risk, "risk", 12)
    request = raw.get("request_summary") if isinstance(raw.get("request_summary"), Mapping) else {}
    response = raw.get("response_summary") if isinstance(raw.get("response_summary"), Mapping) else {}
    risk_type = str(risk.get("risk_type") or "business_flow_blocker")
    snapshots = raw.get("snapshots")
    if not isinstance(snapshots, Mapping):
        snapshots = {"before": raw.get("before_snapshot") or {}, "after": raw.get("after_snapshot") or {}}
    db_snapshot = raw.get("db_snapshot") if isinstance(raw.get("db_snapshot"), Mapping) else {}
    if db_snapshot:
        merged_snapshots = dict(snapshots)
        merged_snapshots["database"] = db_snapshot
        snapshots = merged_snapshots
    return {
        "evidence_id": raw.get("evidence_id") or _slug([risk_id, raw], "ev", 12),
        "risk_id": risk_id,
        "summary": _safe_text(raw.get("summary") or f"AI 已为风险“{risk.get('title')}”生成脱敏证据链。", 360),
        "redaction_status": "safe",
        "discovery_path": _normalise_discovery_path(raw.get("discovery_path"), risk),
        "request_summary": {
            "method": str(request.get("method") or raw.get("method") or "GET").upper(),
            "path": _safe_text(request.get("path") or raw.get("path") or raw.get("url"), 180),
            "auth_context": _safe_text(request.get("auth_context") or raw.get("auth_context") or ", ".join(risk.get("affected_roles", [])), 120),
            "headers_redacted": True,
            "body_redacted": True,
        },
        "response_summary": {
            "status_code": response.get("status_code") or raw.get("status_code"),
            "content_type": _safe_text(response.get("content_type") or raw.get("content_type"), 100),
            "body_redacted": True,
            "observed_issue": _safe_text(response.get("observed_issue") or raw.get("observed_issue") or risk.get("business_impact"), 260),
        },
        "snapshots": redact_value(snapshots),
        "reproduction_steps": _reproduction_steps_for_risk(risk, raw),
        "suggested_fix": _suggested_fix_for_risk(risk_type),
        "closure_criteria": _closure_criteria_for_risk(risk_type),
    }


def _normalise_discovery_path(path: Any, risk: Mapping[str, Any]) -> list[dict[str, str]]:
    items = []
    for idx, item in enumerate(_as_list(path), start=1):
        if isinstance(item, Mapping):
            items.append({"step": idx, "name": _safe_text(item.get("name") or item.get("step") or f"步骤 {idx}", 100), "status": _safe_text(item.get("status") or "passed", 30)})
        elif item:
            items.append({"step": idx, "name": _safe_text(item, 100), "status": "passed"})
    if items:
        return items
    flow_name = (risk.get("affected_business_flow") or {}).get("name") if isinstance(risk.get("affected_business_flow"), Mapping) else "业务链路"
    return [
        {"step": 1, "name": "获取测试认证上下文", "status": "passed"},
        {"step": 2, "name": f"执行 {flow_name} 风险探针", "status": "failed"},
        {"step": 3, "name": "生成脱敏证据链", "status": "passed"},
    ]


def _reproduction_steps_for_risk(risk: Mapping[str, Any], raw: Mapping[str, Any]) -> list[str]:
    explicit = [str(redact_value(x)) for x in _as_list(raw.get("reproduction_steps")) if x]
    if explicit:
        return explicit
    role = ", ".join(risk.get("affected_roles") or ["测试账号"])
    title = str(risk.get("title") or "风险")
    return [
        f"使用 {role} 登录客户测试环境。",
        "执行证据链中记录的业务路径或 API 请求。",
        f"观察是否出现“{title}”对应的异常结果。",
        "修复后重新执行关联回归探针，确认风险不再出现。",
    ]


def _suggested_fix_for_risk(risk_type: str) -> list[str]:
    fixes = {
        "authorization_bypass": ["在受限接口增加 RBAC/租户/角色权限校验。", "未授权角色应返回 403，授权角色应保持正常访问。", "修复后执行权限边界回归探针。"],
        "idempotency_issue": ["为写入接口增加幂等键和状态锁。", "重复请求应返回明确业务状态，不应重复创建流水或重复流转。", "修复后执行状态一致性回归探针。"],
        "report_accuracy": ["核对报表字段来源、聚合口径和过滤条件。", "使用底层业务数据与报表输出进行一致性对比。", "修复后执行报表准确性回归探针。"],
        "session_invalid": ["检查 token/cookie/session 的作用域、header 名称、租户绑定和 SSO 网关配置。", "确认 session_health_path 与业务 API 使用同一认证上下文。"],
        "environment_blocker": ["补齐客户环境前置条件，包括账号、权限、API path、VPN、DNS 或白名单。", "重新执行环境适配预检。"],
    }
    return fixes.get(risk_type, ["根据证据链定位业务异常。", "修复后执行关联回归探针。"])


def _closure_criteria_for_risk(risk_type: str) -> list[str]:
    criteria = {
        "authorization_bypass": ["未授权角色访问受限资源返回 403。", "授权角色访问受限资源保持正常。", "权限边界探针连续通过。"],
        "idempotency_issue": ["重复提交后业务状态保持一致。", "不会重复创建流水或重复扣减。", "状态一致性探针连续通过。"],
        "report_accuracy": ["报表字段与底层业务数据一致。", "报表准确性探针通过。", "脱敏证据链生成 verified 记录。"],
        "session_invalid": ["session_health_path 返回 JSON/API 响应。", "业务 API 不再返回登录页 HTML 或无效重定向。"],
        "environment_blocker": ["环境适配状态达到 ready 或 partial_ready。", "相关 API smoke 通过。"],
    }
    return criteria.get(risk_type, ["风险对应探针复测通过。", "证据链生成 verified 记录。"])


def calculate_launch_decision(
    environment_report: Mapping[str, Any],
    risks: Sequence[Mapping[str, Any]],
    business_flow_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    flow_summary = business_flow_summary or {}
    reasons: list[str] = []
    required_actions: list[str] = []
    env_status = environment_report.get("status")
    critical_blockers = [r for r in risks if r.get("severity") == "critical" and r.get("launch_blocking")]
    high_core = [r for r in risks if r.get("severity") == "high" and r.get("launch_blocking")]
    coverage_rate = _safe_float(flow_summary.get("coverage_rate"), 0.0)
    if env_status == "blocked":
        recommendation = "HOLD"
        reasons.append("客户环境适配未通过，核心业务测试尚未准入。")
        required_actions.extend(environment_report.get("suggested_actions") or [])
    elif critical_blockers:
        recommendation = "NO_GO"
        reasons.append(f"存在 {len(critical_blockers)} 个高危上线阻断风险。")
        required_actions.extend(r.get("suggested_action") for r in critical_blockers[:3] if r.get("suggested_action"))
    elif high_core:
        recommendation = "HOLD"
        reasons.append(f"存在 {len(high_core)} 个高风险问题影响核心业务链路。")
        required_actions.extend(r.get("suggested_action") for r in high_core[:3] if r.get("suggested_action"))
    elif coverage_rate and coverage_rate < 0.8:
        recommendation = "HOLD"
        reasons.append("核心业务链路覆盖率不足 80%。")
        required_actions.append("补齐阻断链路的账号、权限或 API smoke 路径后重新测试。")
    elif any(r.get("severity") == "medium" for r in risks):
        recommendation = "CONDITIONAL_GO"
        reasons.append("核心链路未发现高危阻断项，但仍存在中风险问题，建议灰度期间重点监控。")
    else:
        recommendation = "GO"
        reasons.append("核心链路通过且未发现高危上线阻断风险。")
    title_map = {"GO": "建议上线", "CONDITIONAL_GO": "可灰度上线", "HOLD": "暂缓上线", "NO_GO": "不建议上线", "UNKNOWN": "暂无建议"}
    return {
        "decision_id": _slug([recommendation, reasons, required_actions], "launch", 10),
        "recommendation": recommendation,
        "risk_level": _decision_risk_level(recommendation),
        "title": title_map[recommendation],
        "summary": _launch_summary(recommendation, reasons),
        "reasons": [str(redact_value(x)) for x in reasons[:6]],
        "required_actions": [str(redact_value(x)) for x in required_actions[:6]],
        "generated_at": _now(),
    }


def _decision_risk_level(recommendation: str) -> str:
    return {"GO": "low", "CONDITIONAL_GO": "medium", "HOLD": "high", "NO_GO": "critical"}.get(recommendation, "unknown")


def _launch_summary(recommendation: str, reasons: Sequence[str]) -> str:
    first = reasons[0] if reasons else "暂无充足测试结果。"
    if recommendation == "GO":
        return f"当前建议上线。{first}"
    if recommendation == "CONDITIONAL_GO":
        return f"当前可灰度上线。{first}"
    if recommendation == "HOLD":
        return f"当前建议暂缓上线。{first}"
    if recommendation == "NO_GO":
        return f"当前不建议上线。{first}"
    return "暂无上线建议。"


def calculate_value_metrics(
    project_id: str,
    test_plan: Mapping[str, Any] | None = None,
    risks: Sequence[Mapping[str, Any]] | None = None,
    evidence_bundles: Sequence[Mapping[str, Any]] | None = None,
    business_flow_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    plan_value = (test_plan or {}).get("estimated_value", {}) if isinstance((test_plan or {}).get("estimated_value", {}), Mapping) else {}
    test_points = _safe_int(plan_value.get("equivalent_test_points"), 0)
    risks = list(risks or [])
    evidence_bundles = list(evidence_bundles or [])
    launch_blocking = sum(1 for r in risks if r.get("launch_blocking"))
    evidence_scores = [_safe_float(r.get("evidence_score"), 0.0) for r in risks if r.get("evidence_score") is not None]
    bundle_bonus = 0.05 if evidence_bundles else 0.0
    evidence_trust = _clamp((sum(evidence_scores) / len(evidence_scores) if evidence_scores else 0.0) + bundle_bonus)
    risk_score = sum(SEVERITY_WEIGHTS.get(str(r.get("severity", "info")), 2) * _safe_float(r.get("reproducibility_score"), 0.7) for r in risks)
    impact_min = int(round(risk_score * 800))
    impact_max = int(round(max(impact_min * 2.5, impact_min + 50000))) if impact_min else 0
    coverage_rate = _safe_float((business_flow_summary or {}).get("coverage_rate"), _safe_float(plan_value.get("business_flow_coverage_rate"), 0.0))
    return {
        "project_id": project_id,
        "ai_equivalent_test_points": test_points,
        "estimated_hours_saved": round(test_points * MANUAL_MINUTES_PER_TEST_POINT / 60, 2),
        "manual_minutes_per_test_point": MANUAL_MINUTES_PER_TEST_POINT,
        "business_flow_coverage_rate": round(coverage_rate, 2),
        "launch_blocking_risks": launch_blocking,
        "evidence_trust_score": round(evidence_trust, 2),
        "estimated_business_impact_min": impact_min,
        "estimated_business_impact_max": impact_max,
        "currency": "CNY",
        "calculation_notes": [
            "节省工时按单个测试点平均 12 分钟估算。",
            "潜在业务影响为风险暴露估算区间，不代表确定收益。",
        ],
        "redaction_status": "safe",
    }


def build_business_flow_summary(business_model: Mapping[str, Any], risks: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    risks = list(risks or [])
    flows = [f for f in business_model.get("confirmed_business_flows", []) if isinstance(f, Mapping) and f.get("enabled", True)]
    by_flow: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for risk in risks:
        flow_id = ((risk.get("affected_business_flow") or {}).get("business_flow_id") if isinstance(risk.get("affected_business_flow"), Mapping) else risk.get("business_flow_id"))
        if flow_id:
            by_flow[str(flow_id)].append(risk)
    covered = 0
    passed = 0
    with_risk = 0
    blocked = 0
    for flow in flows:
        status = flow.get("status") or "not_started"
        flow_id = str(flow.get("business_flow_id"))
        if by_flow.get(flow_id):
            covered += 1
            with_risk += 1
        elif status in {"covered_passed", "passed"}:
            covered += 1
            passed += 1
        elif status in {"blocked", "partial_covered"}:
            blocked += 1
    total = len(flows)
    return {
        "total": total,
        "covered": covered,
        "covered_passed": passed,
        "covered_with_risk": with_risk,
        "blocked": blocked,
        "not_covered": max(0, total - covered - blocked),
        "coverage_rate": round(covered / total, 2) if total else 0.0,
    }


def build_realtime_map_snapshot(
    project_id: str,
    business_model: Mapping[str, Any],
    risks: Sequence[Mapping[str, Any]] | None = None,
    test_run: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    risks = list(risks or [])
    risks_by_flow: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for risk in risks:
        flow_ref = risk.get("affected_business_flow") or {}
        flow_id = flow_ref.get("business_flow_id") if isinstance(flow_ref, Mapping) else risk.get("business_flow_id")
        if flow_id:
            risks_by_flow[str(flow_id)].append(risk)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    overlays: list[dict[str, Any]] = []
    for flow_index, flow in enumerate(business_model.get("confirmed_business_flows", [])):
        if not isinstance(flow, Mapping) or not flow.get("enabled", True):
            continue
        flow_id = str(flow.get("business_flow_id"))
        flow_risks = risks_by_flow.get(flow_id, [])
        prev_node_id = None
        for idx, node in enumerate(flow.get("nodes", [])):
            if not isinstance(node, Mapping):
                continue
            node_id = str(node.get("node_id") or _slug([flow_id, idx, node.get("label")], "node", 10))
            node_risks = flow_risks if idx == max(0, len(flow.get("nodes", [])) - 2) else []
            status = "risk" if node_risks else "passed" if flow.get("status") in {"covered_passed", "covered_with_risk"} else "idle"
            severity = max((str(r.get("severity", "info")) for r in node_risks), key=lambda x: RISK_ORDER.get(x, 0), default="none")
            nodes.append(
                {
                    "node_id": node_id,
                    "label": node.get("label"),
                    "node_type": node.get("node_type") or _infer_node_type(str(node.get("label"))),
                    "business_flow_ids": [flow_id],
                    "status": status,
                    "risk_level": severity,
                    "metrics": {
                        "probe_total": 0,
                        "probe_passed": 0,
                        "probe_failed": len(node_risks),
                        "risk_count": len(node_risks),
                    },
                    "position": {"x": 160 + idx * 170, "y": 120 + flow_index * 110, "z": 0},
                }
            )
            if prev_node_id:
                edges.append(
                    {
                        "edge_id": _slug([prev_node_id, node_id], "edge", 10),
                        "from_node_id": prev_node_id,
                        "to_node_id": node_id,
                        "label": f"{flow.get('name')}流程依赖",
                        "status": "risk" if node_risks else "passed",
                        "business_flow_id": flow_id,
                    }
                )
            prev_node_id = node_id
            for risk in node_risks:
                overlays.append(
                    {
                        "risk_id": risk.get("risk_id"),
                        "node_id": node_id,
                        "severity": risk.get("severity"),
                        "visual_type": "pulse",
                        "business_impact": risk.get("business_impact"),
                        "launch_blocking": bool(risk.get("launch_blocking")),
                    }
                )
    return {
        "map_id": _slug([project_id, len(nodes), len(risks)], "map", 10),
        "project_id": project_id,
        "run_id": (test_run or {}).get("run_id"),
        "layout_mode": "business_flow",
        "nodes": nodes,
        "edges": edges,
        "risk_overlays": overlays,
        "events": _events_from_risks(risks, test_run),
        "updated_at": _now(),
        "version": PHASE103_VERSION,
    }


def _events_from_risks(risks: Sequence[Mapping[str, Any]], test_run: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    events = []
    if test_run:
        events.append({"event_id": _slug([test_run.get("run_id"), "start"], "evt", 10), "event_type": "test_run_status", "timestamp": test_run.get("started_at") or _now(), "message": f"AI 测试运行状态：{test_run.get('status', 'unknown')}", "severity": "info"})
    for risk in risks[:20]:
        events.append(
            {
                "event_id": _slug([risk.get("risk_id"), "detected"], "evt", 10),
                "event_type": "risk_detected",
                "timestamp": risk.get("first_seen_at") or _now(),
                "risk_id": risk.get("risk_id"),
                "message": f"AI 发现风险：{risk.get('title')}",
                "severity": risk.get("severity"),
            }
        )
    return events


def build_command_center_snapshot(
    project: Mapping[str, Any],
    business_model: Mapping[str, Any],
    environment_report: Mapping[str, Any],
    test_plan: Mapping[str, Any] | None = None,
    risks: Sequence[Mapping[str, Any]] | None = None,
    evidence_bundles: Sequence[Mapping[str, Any]] | None = None,
    test_run: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    risks = list(risks or [])
    flow_summary = build_business_flow_summary(business_model, risks)
    launch_decision = calculate_launch_decision(environment_report, risks, flow_summary)
    value_metrics = calculate_value_metrics(str(project.get("project_id")), test_plan, risks, evidence_bundles, flow_summary)
    risk_summary = dict(Counter(str(r.get("severity", "info")) for r in risks))
    risk_summary["launch_blocking"] = sum(1 for r in risks if r.get("launch_blocking"))
    quality_score = _quality_health_score(environment_report, flow_summary, risks, value_metrics)
    top_risks = sorted(risks, key=_risk_priority_score, reverse=True)[:5]
    live_map = build_realtime_map_snapshot(str(project.get("project_id")), business_model, risks, test_run)
    return {
        "project_id": project.get("project_id"),
        "snapshot_id": _slug([project.get("project_id"), quality_score, risk_summary], "snapshot", 10),
        "quality_health_score": quality_score,
        "launch_decision": launch_decision,
        "environment_readiness": {
            "status": environment_report.get("status"),
            "score": environment_report.get("score"),
            "safe_execution_mode": environment_report.get("safe_execution_mode"),
            "current_blockers": environment_report.get("current_blockers", []),
        },
        "business_flow_summary": flow_summary,
        "risk_summary": risk_summary,
        "value_metrics": value_metrics,
        "top_risks": top_risks,
        "recent_events": live_map["events"][:10],
        "live_map": live_map,
        "executive_summary": _executive_summary(project, flow_summary, risk_summary, launch_decision, value_metrics),
        "updated_at": _now(),
        "version": PHASE103_VERSION,
    }


def _quality_health_score(environment_report: Mapping[str, Any], flow_summary: Mapping[str, Any], risks: Sequence[Mapping[str, Any]], value_metrics: Mapping[str, Any]) -> int:
    env_score = _safe_float(environment_report.get("score"), 0.0)
    coverage = _safe_float(flow_summary.get("coverage_rate"), 0.0) * 100
    evidence = _safe_float(value_metrics.get("evidence_trust_score"), 0.0) * 100
    penalty = sum({"critical": 18, "high": 9, "medium": 4, "low": 1, "info": 0}.get(str(r.get("severity", "info")), 0) for r in risks)
    score = env_score * 0.25 + coverage * 0.35 + evidence * 0.15 + 25 - penalty
    return int(max(0, min(100, round(score))))


def _risk_priority_score(risk: Mapping[str, Any]) -> float:
    explicit = _safe_float(risk.get("priority_score"), -1.0)
    if explicit >= 0:
        return explicit + (15 if risk.get("high_confidence_candidate") else 0) + (10 if str(risk.get("verification_badge") or "") == "ui_verified" else 0)
    return (
        (100 if risk.get("launch_blocking") else 0)
        + SEVERITY_WEIGHTS.get(str(risk.get("severity", "info")), 2)
        + _safe_float(risk.get("reproducibility_score"), 0.5) * 10
        + _safe_float(risk.get("evidence_score"), 0.5) * 10
        + (15 if risk.get("high_confidence_candidate") else 0)
        + (10 if str(risk.get("verification_badge") or "") == "ui_verified" else 0)
    )


def _executive_summary(project: Mapping[str, Any], flow_summary: Mapping[str, Any], risk_summary: Mapping[str, Any], launch_decision: Mapping[str, Any], value_metrics: Mapping[str, Any]) -> str:
    return (
        f"本轮 AI 测试覆盖 {flow_summary.get('covered', 0)} / {flow_summary.get('total', 0)} 条业务链路，"
        f"发现 {risk_summary.get('launch_blocking', 0)} 个上线阻断风险。"
        f"当前上线建议：{launch_decision.get('title')}。"
        f"AI 等价执行 {value_metrics.get('ai_equivalent_test_points', 0)} 个测试点，"
        f"预计节省 {value_metrics.get('estimated_hours_saved', 0)} 小时人工测试。"
    )


def generate_executive_report(
    project: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    risks: Sequence[Mapping[str, Any]] | None = None,
    evidence_bundles: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    risks = sorted(list(risks or snapshot.get("top_risks") or []), key=_risk_priority_score, reverse=True)
    value = snapshot.get("value_metrics", {}) if isinstance(snapshot.get("value_metrics"), Mapping) else {}
    decision = snapshot.get("launch_decision", {}) if isinstance(snapshot.get("launch_decision"), Mapping) else {}
    report_id = _slug([project.get("project_id"), snapshot.get("snapshot_id"), len(risks)], "report", 10)
    top_risks = [
        {
            "risk_id": risk.get("risk_id"),
            "title": risk.get("title"),
            "severity": risk.get("severity"),
            "affected_business_flow": risk.get("affected_business_flow"),
            "business_impact": risk.get("business_impact"),
            "evidence_score": risk.get("evidence_score"),
            "reproducibility_score": risk.get("reproducibility_score"),
            "suggested_action": risk.get("suggested_action"),
            "priority_score": risk.get("priority_score"),
            "priority_label": risk.get("priority_label"),
            "defect_intake_recommended": risk.get("defect_intake_recommended"),
            "defect_intake_priority": risk.get("defect_intake_priority"),
        }
        for risk in risks[:10]
    ]
    next_actions = [
        {
            "priority": "P0" if risk.get("severity") == "critical" else "P1",
            "title": f"修复{risk.get('title')}风险",
            "owner_suggestion": "对应业务系统负责人",
            "verification_probe": f"{risk.get('risk_type')}_regression_probe",
            "reason": "该风险属于上线阻断项。" if risk.get("launch_blocking") else "该风险建议上线前完成确认。",
        }
        for risk in risks[:5]
    ]
    return {
        "report_id": report_id,
        "project_id": project.get("project_id"),
        "title": f"{project.get('system_name') or project.get('project_name') or '企业系统'} 上线质量风险评估报告",
        "generated_at": _now(),
        "quality_health_score": snapshot.get("quality_health_score"),
        "launch_recommendation": decision.get("recommendation", "UNKNOWN"),
        "executive_summary": snapshot.get("executive_summary"),
        "coverage_summary": snapshot.get("business_flow_summary", {}),
        "risk_summary": snapshot.get("risk_summary", {}),
        "top_risks": top_risks,
        "business_impact_summary": _business_impact_summary(top_risks),
        "evidence_trust_summary": {
            "evidence_trust_score": value.get("evidence_trust_score", 0),
            "evidence_bundle_count": len(evidence_bundles or []),
            "redaction_status": "safe",
            "statement": "报告证据默认脱敏，未展示 token、cookie、password、session 原值和客户敏感业务数据。",
        },
        "value_summary": value,
        "next_actions": next_actions,
        "markdown": _report_markdown(project, snapshot, top_risks, next_actions),
        "version": PHASE103_VERSION,
    }


def _business_impact_summary(top_risks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_flow: dict[str, list[str]] = defaultdict(list)
    for risk in top_risks:
        flow = risk.get("affected_business_flow") or {}
        name = flow.get("name") if isinstance(flow, Mapping) else "未归属业务链路"
        by_flow[str(name)].append(str(risk.get("title")))
    return [{"business_flow": flow, "risk_count": len(items), "risks": items[:5]} for flow, items in by_flow.items()]


def _report_markdown(project: Mapping[str, Any], snapshot: Mapping[str, Any], top_risks: Sequence[Mapping[str, Any]], next_actions: Sequence[Mapping[str, Any]]) -> str:
    value = snapshot.get("value_metrics", {}) if isinstance(snapshot.get("value_metrics"), Mapping) else {}
    lines = [
        f"# {project.get('system_name') or project.get('project_name') or '企业系统'} 上线质量风险评估报告",
        "",
        "## 执行摘要",
        str(snapshot.get("executive_summary") or "暂无摘要。"),
        "",
        "## 上线建议",
        str((snapshot.get("launch_decision") or {}).get("summary") or "暂无上线建议。"),
        "",
        "## 高危风险摘要",
    ]
    if top_risks:
        for idx, risk in enumerate(top_risks[:5], start=1):
            lines.append(f"{idx}. **{risk.get('title')}**：{risk.get('business_impact')}")
    else:
        lines.append("当前未发现高危业务风险。")
    lines.extend(
        [
            "",
            "## AI 价值量化",
            f"- AI 等价测试点：{value.get('ai_equivalent_test_points', 0)}",
            f"- 预计节省人工测试：{value.get('estimated_hours_saved', 0)} 小时",
            f"- 潜在业务影响区间：{value.get('estimated_business_impact_min', 0)} - {value.get('estimated_business_impact_max', 0)} {value.get('currency', 'CNY')}",
            "",
            "## 下一步行动建议",
        ]
    )
    if next_actions:
        for idx, action in enumerate(next_actions[:5], start=1):
            lines.append(f"{idx}. {action.get('title')}；建议负责人：{action.get('owner_suggestion')}；验证探针：{action.get('verification_probe')}")
    else:
        lines.append("继续保持核心链路回归验证。")
    lines.extend(["", "> 说明：报告默认脱敏，不展示 token、cookie、password、session 原值和客户敏感业务数据。"])
    return "\n".join(lines)
