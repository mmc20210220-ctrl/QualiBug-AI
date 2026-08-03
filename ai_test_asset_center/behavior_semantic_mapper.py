"""
Behavior Semantic Mapper — enriches raw engine findings with business-facing
metadata that the frontend consumes: business impact, investigation guidance,
log/trace hints, and business-language reproduction steps.

Integrated into the discovery pipeline after finding generation.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .scan_post_hooks import register_scan_post_hook

HOOK_NAME = "behavior_semantic_mapper"

FINDING_CARRIER_KEYS = (
    "real_findings",
    "bug_scores",
    "db_findings",
    "e2e_findings",
    "deep_findings",
    "ui_findings",
)

# ── Trace ID detection ─────────────────────────────────────
# Trace header name varies by company. Scan known patterns,
# configurable per project via QUALIBUG_TRACE_HEADERS env var.

TRACE_HEADER_PATTERNS = [
    r'(?i)^x-trace-id$', r'(?i)^x-request-id$', r'(?i)^x-b3-traceid$',
    r'(?i)^x-amzn-trace-id$', r'(?i)^x-correlation-id$',
    r'(?i)^x-otel-trace-id$', r'(?i)^x-datadog-trace-id$',
    r'(?i)^traceparent$', r'(?i)^tracestate$',
    r'(?i)^request-id$', r'(?i)^correlation-id$',
    r'(?i)^x-edge-request-id$', r'(?i)^x-request-id$',
]

TRACE_BODY_PATTERNS = [
    r'trace[_\-]?id', r'request[_\-]?id', r'traceId', r'requestId',
    r'correlation[_\-]?id', r'req[_\-]?id',
]


def extract_trace_id(headers: dict[str, str] | None, body: dict[str, Any] | None = None) -> str:
    """Extract trace/request identifier from API response headers or body.
    Handles X-Trace-Id, X-Request-Id, X-B3-TraceId, traceparent, etc.
    Also searches response body recursively for trace-like fields."""
    if headers:
        for pattern in TRACE_HEADER_PATTERNS:
            for key, value in headers.items():
                if re.match(pattern, key) and value:
                    return str(value)

    def _search_body(obj: Any, depth: int = 0) -> str:
        if depth > 5 or obj is None:
            return ""
        if isinstance(obj, dict):
            for key, value in obj.items():
                for pattern in TRACE_BODY_PATTERNS:
                    if re.search(pattern, key, re.IGNORECASE) and isinstance(value, str) and value:
                        return value
                if isinstance(value, (dict, list)):
                    result = _search_body(value, depth + 1)
                    if result:
                        return result
        elif isinstance(obj, list):
            for item in obj:
                result = _search_body(item, depth + 1)
                if result:
                    return result
        return ""

    return _search_body(body)


# ── Knowledge bases ────────────────────────────────────────

# API path → frontend page mapping
PAGE_MAP: dict[str, str] = {
    "/api/orders": "订单管理",
    "/api/orders/{id}/detail": "订单详情",
    "/api/orders/{id}/pay": "订单支付",
    "/api/orders/{id}/refund": "订单退款",
    "/api/orders/{id}/approve": "订单审批",
    "/api/orders/{id}/close": "订单关闭",
    "/api/orders/{id}/dispatch": "订单发货",
    "/api/orders/{id}/assign": "订单分配",
    "/api/orders/{id}/archive": "订单归档",
    "/api/knowledge/ingest": "企业资料上传",
    "/api/knowledge/delete": "企业资料管理",
    "/api/knowledge/reanalyze": "知识库重建",
    "/api/scan/run": "扫描引擎",
    "/api/settings/save": "系统设置",
    "/api/connectors/register": "集成连接器",
    "/api/pilot/tasks": "任务管理",
    "/api/pilot/tasks/approve": "任务审批",
    "/api/pilot/tasks/run-next": "任务执行",
    "/api/environment/config": "环境配置",
    "/api/run-scenario": "测试场景",
    "/api/health": "系统健康",
}

# Role mapping
ROLE_ACTIONS: dict[str, str] = {
    "游客": "以游客身份访问系统",
    "买家": "以买家身份登录",
    "商家运营": "以商家运营身份登录",
    "客服": "以客服身份登录",
    "财务": "以财务身份登录",
    "平台管理员": "以平台管理员身份登录",
    "operator": "以操作员身份登录",
    "admin": "以管理员身份登录",
}

# Risk → business impact language
RISK_IMPACT: dict[str, str] = {
    "permission_boundary": "未授权用户可访问或操作敏感功能，存在越权风险",
    "error_contract": "接口错误响应不完整，调用方无法正确处理异常情况",
    "idempotency_gap": "重复提交可导致数据重复或业务副作用，影响数据准确性",
    "spec_structure": "API 规范定义不完整，影响接口文档可读性和自动化工具集成",
    "missing_openapi": "缺少 API 规范文档，无法进行接口级自动验证",
    "missing_prd": "缺少需求文档，无法验证接口行为是否符合业务预期",
    "llm_status": "LLM 不可用，语义级缺陷发现能力受限",
    "async_observability_gap": "异步操作缺少进度反馈，用户无法获知操作状态",
    "unreachable": "服务端点不可达，影响系统可用性",
    "db_verified": "数据库数据存在不一致，可能导致业务判断错误",
    "conservation": "业务守恒规则被违反，可能导致库存/资金异常",
    "causality_coverage": "因果约束缺失，状态变更可能产生未预期的副作用",
    "idempotent_side_effect": "幂等性保障缺失，重复操作可产生业务副作用",
}

# Category → SQL template for investigation
SQL_HINTS: dict[str, str] = {
    "库存": "SELECT * FROM inventory WHERE quantity < 0",
    "bom": "SELECT b.*, m.status FROM bom_line b LEFT JOIN material m ON b.material_code = m.code WHERE m.code IS NULL",
    "订单": "SELECT * FROM orders WHERE status IN ('approved_after_closed', 'paid', 'refunding') AND created_at > datetime('now', '-7 days')",
    "幂等": "SELECT business_no, COUNT(*) cnt FROM orders GROUP BY business_no HAVING cnt > 1",
    "流水": "SELECT txn_type, ref_no, COUNT(*) cnt FROM inventory_ledger GROUP BY txn_type, ref_no HAVING cnt > 1",
}


def enrich_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Add business-facing metadata to a raw finding dict. Mutates and returns."""
    title = str(finding.get("title", ""))
    method = str(finding.get("method", ""))
    path = str(finding.get("path", ""))
    category = str(finding.get("category", ""))
    risk_type = str(finding.get("risk_type", ""))

    # Extract method/path from title if missing
    if not path and not method:
        import re
        m = re.match(r'^(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)', title, re.IGNORECASE)
        if m:
            if not method: method = m.group(1).upper()
            if not path: path = m.group(2)
            finding["method"] = method
            finding["path"] = path

    source_entity = str(finding.get("source_entity", ""))
    target_entity = str(finding.get("target_entity", ""))

    # 1. Evidence hint
    if "evidence_hint" not in finding or not finding["evidence_hint"]:
        finding["evidence_hint"] = _build_evidence_hint(finding, title, method, path, category, source_entity)

    # 2. Business impact
    if "business_impact" not in finding or not isinstance(finding.get("business_impact"), dict):
        finding["business_impact"] = _build_business_impact(finding, title, risk_type, category)

    # 3. Investigation guidance
    if "investigation_guidance" not in finding or not isinstance(finding.get("investigation_guidance"), dict):
        finding["investigation_guidance"] = _build_investigation_guidance(finding, title, method, path, category, source_entity, target_entity)

    # 4. Business reproduction steps
    if "reproduce_steps_business" not in finding or not finding.get("reproduce_steps_business"):
        finding["reproduce_steps_business"] = _build_business_steps(finding, title, method, path)

    return finding


def _build_evidence_hint(finding: dict, title: str, method: str, path: str, category: str, source_entity: str) -> str:
    """Build log/Trace ID hints for developer investigation."""
    parts = []

    # Priority 1: Real trace ID captured from API response
    validation_evidence = finding.get("validation_evidence", {})
    if isinstance(validation_evidence, dict):
        resp_headers = validation_evidence.get("response_headers", {})
        resp_body = validation_evidence.get("response_body", {})
        trace_id = extract_trace_id(resp_headers, resp_body)
    else:
        trace_id = ""

    # Priority 2: Fall back to validation_task_id or bug_id
    if not trace_id:
        trace_id = finding.get("validation_task_id") or finding.get("bug_id") or ""

    if method and path:
        if trace_id:
            parts.append(f"Trace ID: {trace_id}")
        parts.append(f"请求路径: {method} {path}")
        header_hint = _guess_trace_header_name(validation_evidence.get("response_headers", {}) if isinstance(validation_evidence, dict) else {})
        parts.append(f"日志搜索: grep '{trace_id or method} {path}' access.log" + (f" | grep '{header_hint}'" if header_hint else ""))

    elif trace_id:
        parts.append(f"Trace ID: {trace_id}")

    # Database hint
    if source_entity:
        parts.append(f"数据表: {source_entity}")

    # SQL hint
    for kw, sql in SQL_HINTS.items():
        if kw in title:
            parts.append(f"SQL: {sql}")
            break

    if not parts and trace_id:
        parts.append(f"日志关键词: {trace_id}")

    return "；".join(parts) if parts else ""


def _build_business_impact(finding: dict, title: str, risk_type: str, category: str) -> dict[str, str]:
    """Build business-readable impact summary."""
    key = risk_type or category
    impact_text = RISK_IMPACT.get(key, "")

    # Derive urgency from severity
    sev = str(finding.get("severity", "P2"))
    urgency_map = {"P0": "立即修复，阻塞上线", "P1": "本迭代修复，高风险", "P2": "建议修复，中等风险", "P3": "低优先级，技术债"}
    urgency = urgency_map.get(sev, "待评估")

    return {
        "summary": impact_text or f"系统行为与预期存在偏差，可能影响{_guess_module(title)}的正常运行",
        "urgency": urgency,
        "module": _guess_module(title),
    }


def _build_investigation_guidance(finding: dict, title: str, method: str, path: str, category: str, source_entity: str, target_entity: str) -> dict[str, Any]:
    """Build developer investigation guidance."""
    task_id = finding.get("validation_task_id", "")
    log_search = ""
    if task_id:
        log_search = f"搜索 Trace ID: {task_id}"
    elif method and path:
        log_search = f"grep '{method} {path}' access.log"
    return {
        "primary_area": _guess_module(title),
        "relevant_apis": [f"{method} {path}"] if path else [],
        "relevant_tables": [source_entity] if source_entity else [],
        "log_search": log_search,
        "sql_verify": _find_sql_hint(title),
        "trace_id": task_id,
    }


def _build_business_steps(finding: dict, title: str, method: str, path: str) -> list[str]:
    """Build business-language reproduction steps matching customer frontend operations."""
    t = title.lower()

    # Auth / permission
    if "401" in t or "403" in t or "lacks" in t or "auth" in t:
        if "ingest" in t or "upload" in t or "上传" in t:
            return ["进入企业资料页面", "在不登录或使用低权限账号的情况下点击上传按钮", "预期应弹出登录提示或无权限提示"]
        if "delete" in t or "删除" in t:
            return ["在未登录状态下直接访问资源删除页面", "观察是否返回权限不足提示", "若删除成功则存在越权风险"]
        if "config" in t or "settings" in t or "配置" in t:
            return ["以普通用户身份登录系统", "进入系统设置页面", "预期应提示权限不足，若可修改配置则确认缺陷"]
        if "scan" in t or "run" in t or "扫描" in t:
            return ["以非管理员身份登录系统", "尝试触发扫描或执行运维操作", "预期被拒绝"]
        return ["退出登录以未认证状态访问系统", "尝试操作需要权限的功能页面", "成功执行则确认权限校验缺失"]

    # Idempotency
    if "idempotenc" in t or "幂等" in t or "replay" in t:
        if "ingest" in t or "upload" in t:
            return ["进入企业资料页面选择一个文件上传", "上传成功后立即再次点击上传同一文件", "预期提示文件已存在"]
        if "approve" in t or "审批" in t:
            return ["打开一条待审批记录点击审批通过", "快速再次点击审批按钮（模拟网络重试）", "预期提示已审批，不应重复生成审批记录"]
        return ["在对应功能页面执行一次写操作", "快速重复提交（双击按钮或网络重试）", "预期只产生一条业务记录"]

    # DB verified
    if "db verified" in t or "库存" in t or "负" in t:
        return ["进入库存管理页面查看物料库存列表", "筛选可用量为负数的物料记录", "记录异常物料编码，登录数据库进一步验证"]
    if "bom" in t:
        return ["打开物料清单(BOM)管理页面", "选择一个产品查看其组成物料", "检查物料用量、精度与 PRD 计算规则是否一致"]

    # Payment / refund
    if "pay" in t or "支付" in t or "refund" in t or "退款" in t:
        return ["以买家身份登录进入下单页面", "选择商品填写地址选择支付方式后提交订单", "在订单详情页检查支付状态和金额是否正确"]

    # Order
    if "订单" in t or "order" in t:
        return ["以对应角色登录进入订单管理页面", "选择一条订单记录进行查看或操作", "观察订单状态变更是否和 PRD 描述一致"]

    # Generic by page
    page = _path_to_page(path)
    return [f"进入{page}", "按照正常业务流程执行操作", "观察系统实际行为是否和 PRD 预期一致"]


def _guess_module(title: str) -> str:
    """Guess which business module the finding belongs to."""
    mapping = [
        ("订单|order", "订单管理"), ("支付|payment|pay", "支付模块"),
        ("库存|inventory|stock", "库存管理"), ("bom|物料|material", "物料管理"),
        ("用户|user|auth|登录|认证|授权|permission", "用户与权限"),
        ("审批|approve|approval", "审批流程"), ("通知|notif", "通知服务"),
        ("知识|knowledge|ingest|文档|upload", "企业资料"), ("配置|config|settings|环境", "系统配置"),
        ("扫描|scan|pilot|任务|task", "测试引擎"), ("连接器|connector|集成|integrat", "集成对接"),
        ("健康|health", "系统监控"), ("退款|refund", "退款管理"),
    ]
    for pattern, module in mapping:
        if any(word in title.lower() for word in pattern.split("|")):
            return module
    return "系统被测模块"


def _path_to_page(path: str) -> str:
    """Map API path to frontend page name."""
    for api_path, page in PAGE_MAP.items():
        if api_path in path:
            return page
    parts = path.strip("/").split("/")
    if "approvals" in parts:
        return "审批流程"
    if "orders" in parts:
        return "订单管理"
    if "data" in parts:
        return "数据管理"
    if "knowledge" in parts:
        return "企业资料"
    if "pilot" in parts or "tasks" in parts:
        return "任务管理"
    if "scan" in parts or "run" in parts:
        return "扫描引擎"
    if "settings" in parts or "config" in parts:
        return "系统设置"
    if "connectors" in parts:
        return "集成连接器"
    if "actions" in parts:
        return "操作面板"
    if "sync" in parts:
        return "数据同步"
    if "cache" in parts:
        return "缓存管理"
    if "contracts" in parts:
        return "合同管理"
    if "concurrent" in parts or "import" in parts:
        return "系统管理"
    return "系统功能"


def _find_sql_hint(title: str) -> str:
    """Try to generate a relevant SQL verification query."""
    for kw, sql in SQL_HINTS.items():
        if kw in title:
            return sql
    return ""


def _guess_trace_header_name(headers: dict[str, str]) -> str:
    """Detect which trace header the target system uses."""
    if not headers:
        return ""
    for pattern in TRACE_HEADER_PATTERNS:
        for key in headers:
            if re.match(pattern, key):
                return key
    return ""


def attach_behavior_semantics(
    scan_result: dict[str, Any],
    *,
    project: str,
    root: Path,
) -> dict[str, Any]:
    """Enrich every finding carrier with business-facing metadata.

    Enrichment mutates finding dicts in place and stays additive: no finding is
    dropped, reclassified, or promoted by this projection.
    """
    if not isinstance(scan_result, dict):
        return scan_result
    for key in FINDING_CARRIER_KEYS:
        items = scan_result.get(key)
        if not isinstance(items, list):
            continue
        enriched: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                enriched.append(item)
                continue
            try:
                enriched.append(enrich_finding(item))
            except Exception:
                enriched.append(item)
        scan_result[key] = enriched
    return scan_result


def install_behavior_semantics() -> None:
    register_scan_post_hook(HOOK_NAME, attach_behavior_semantics)
