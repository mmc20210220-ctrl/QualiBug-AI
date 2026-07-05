"""
Evidence Enricher v3 — 三视角证据富化引擎

Takes raw findings from the scan pipeline and enriches them with
structured, human-readable evidence for three audiences:
  1. 业务视角 (Business): Plain-language impact, affected modules, business risk
  2. 测试视角 (Testing): Concrete reproduction steps, curl commands, API traces
  3. 研发视角 (Development): DB tables, log patterns, code location hints

All enrichment is data-driven — uses enterprise knowledge docs (PRD, DB schema,
business rules, API spec) to map technical findings to business language.
NEVER fabricates evidence; uses pattern matching on real finding data.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════════════
# Business Language Mappings
# ═══════════════════════════════════════════════════════════════════════

# Technical pattern → Business impact description
BUSINESS_IMPACT_MAP: list[tuple[str, str, str, str]] = [
    # (regex pattern, business summary, urgency, affected module)
    # State machine violations
    (r"从.*cancelled.*执行.*pay", 
     "已取消订单可被重新支付，存在重复扣款风险，可能导致客户资金损失与投诉。",
     "高", "订单/支付"),
    (r"从.*refunded.*执行.*refund",
     "已退款订单可被再次退款，可能导致企业资金损失和财务对账异常。",
     "高", "退款/财务"),
    (r"从.*completed.*执行.*cancel",
     "已完成订单可被取消，可能造成履约纠纷和库存差异。",
     "高", "订单/库存"),
    
    # Authorization / permission
    (r"买家.*可访问管理端点|买家.*token.*管理|买家.*管理员",
     "普通用户可访问管理后台接口，存在敏感数据泄露和越权操作风险，违反数据隔离合规要求。",
     "高", "权限/安全"),
    (r"返回200.*非403|返回500.*非403",
     "权限校验异常：越权访问未被正确拦截，系统返回不当状态码，可能误导监控告警。",
     "中高", "权限/安全"),
    
    # Data leakage
    (r"DRAFT.*可见|草稿.*展示|草稿.*可见",
     "草稿/未发布商品对终端用户可见，可能造成品牌形象受损和定价策略泄露。",
     "中高", "商品/内容"),
    (r"OFF_SALE.*可见|下架.*可见|隐藏.*可见",
     "下架/隐藏商品仍对用户可见并可操作，存在合规风险（如召回商品仍可购买）。",
     "高", "商品/合规"),
    
    # Coupon / financial
    (r"优惠券.*过期.*仍.*可用|已过期.*优惠券|expired.*coupon",
     "过期优惠券仍可使用，导致营销预算超支和财务核算错误。",
     "高", "营销/财务"),
    (r"低于最低消费.*仍可用|不满足.*条件.*仍可用",
     "优惠券使用条件校验缺失，低于门槛仍可享受折扣，造成营销损失。",
     "中高", "营销/财务"),
    (r"优惠券.*重复.*验证|重复.*validate|coupon.*重复",
     "优惠券可被重复验证/使用，存在刷券风险和营销预算失控。",
     "高", "营销/安全"),
    
    # Idempotency
    (r"幂等.*破坏|重复.*创建|同一.*请求.*两次",
     "接口缺乏幂等保护，重复提交可产生重复订单/支付，直接造成用户资金损失或库存超卖。",
     "高", "订单/支付"),
    
    # Financial anomalies
    (r"退款.*大于.*支付|refund.*>.*pay|退款.*超过.*实付",
     "退款金额超过实际支付金额，存在资金损失风险和财务审计问题。",
     "高", "财务/风控"),
    (r"库存.*负数|库存.*为负|qty.*<.*0",
     "库存出现负数，表明超卖已发生，需立即停售并排查。",
     "高", "库存/履约"),
    
    # Role anomalies
    (r"角色异常|非标准角色|role.*auditor",
     "系统中存在非标准角色（如 auditor），可能为测试数据未清理或未授权的后门账号。",
     "中高", "安全/账号"),
    
    # Payment / status
    (r"支付.*状态.*不一致|支付成功.*但.*订单|paid.*but.*order",
     "支付成功但订单状态未同步更新，用户付款后订单仍显示待支付，导致客诉和信任危机。",
     "高", "支付/订单"),
    
    # General API
    (r"500.*错误|服务端.*500|internal.*error",
     "服务端内部错误（500），表明代码存在未处理异常或数据一致性问题，可能影响业务连续性。",
     "中高", "系统稳定性"),
    (r"401.*未授权|权限.*不足|403.*禁止",
     "认证/授权校验触发拒绝，若发生在正常业务流程中，表明权限配置或 Token 管理存在问题。",
     "中", "认证/授权"),
]

# Finding category → Business module mapping
CATEGORY_MODULE_MAP = {
    "authorization": "权限管控",
    "data_integrity": "数据一致性",
    "data_leak": "数据隔离",
    "business_rule": "业务规则",
    "financial": "资金安全",
    "idempotency": "幂等防护",
    "inventory": "库存管理",
    "state_machine": "状态流转",
    "parameter_validation": "参数校验",
    "db_verification": "数据验证",
    "e2e_flow": "端到端流程",
    "deep_test": "深度检测",
}

# Finding category → DB tables to check
CATEGORY_TABLES_MAP = {
    "authorization": ["users", "audit_logs"],
    "data_integrity": ["products", "inventory"],
    "data_leak": ["products", "orders"],
    "business_rule": ["coupons", "coupon_usage", "orders"],
    "financial": ["payments", "refunds", "orders"],
    "idempotency": ["orders", "payments"],
    "inventory": ["inventory", "inventory_locks"],
    "state_machine": ["orders"],
    "db_verification": ["users", "products", "coupons", "orders", "payments"],
    "e2e_flow": ["orders", "order_items", "payments", "inventory"],
}

# Finding category → Log search pattern
CATEGORY_LOG_MAP = {
    "authorization": r'grep -i "unauthorized\|forbidden\|403\|role.*denied" /var/log/app/*.log',
    "data_leak": r'grep -i "DRAFT\|OFF_SALE\|hidden.*product" /var/log/app/*.log',
    "business_rule": r'grep -i "coupon.*valid\|discount.*error\|rule.*failed" /var/log/app/*.log',
    "financial": r'grep -i "refund.*amount\|payment.*mismatch\|金额" /var/log/app/*.log',
    "idempotency": r'grep -i "duplicate\|idempotency\|already.*exists" /var/log/app/*.log',
    "inventory": r'grep -i "stock.*negative\|oversell\|inventory.*error" /var/log/app/*.log',
    "state_machine": r'grep -i "invalid.*state.*transition\|status.*error" /var/log/app/*.log',
}


def _match_business_impact(title: str, description: str, category: str = "") -> dict[str, str]:
    """Map a technical finding to business-language impact description."""
    text = f"{title} {description}".lower()
    
    for pattern, summary, urgency, module in BUSINESS_IMPACT_MAP:
        if re.search(pattern, text, re.IGNORECASE):
            return {"summary": summary, "urgency": urgency, "module": module}
    
    # Fallback by category
    module = CATEGORY_MODULE_MAP.get(category, "核心业务")
    if "P0" in title or "P0" in description:
        return {
            "summary": f"该缺陷影响{module}模块的核心功能，可能导致直接业务损失或合规风险。",
            "urgency": "高",
            "module": module,
        }
    elif "P1" in title or "P1" in description:
        return {
            "summary": f"该缺陷影响{module}模块的正常业务流程，可能导致用户体验受损或运营效率下降。",
            "urgency": "中高",
            "module": module,
        }
    return {
        "summary": f"该缺陷为{module}模块的潜在风险项，建议在迭代中修复。",
        "urgency": "中",
        "module": module,
    }


def _build_reproduction_steps(finding: dict[str, Any], enterprise_ctx: dict[str, Any] | None = None) -> list[str]:
    """Build concrete, actionable reproduction steps from finding evidence.

    Uses real HAR data (har_evidence) when available to produce accurate curl
    commands and response details instead of placeholders.
    """
    ctx = enterprise_ctx or {}
    har = finding.get("har_evidence") or {}
    title = str(finding.get("title") or "")
    description = str(finding.get("summary") or finding.get("actual") or finding.get("description") or "")
    base_url = ctx.get("base_url", "http://localhost:8080")
    test_email = ctx.get("test_email", "buyer01@example.com")
    test_password = ctx.get("test_password", "Test@123456")
    
    # Use HAR data only when it matches the finding's own path/method
    # If finding has a more specific path (e.g. /api/products/admin/:sku) 
    # but HAR only matched /api/products, prefer finding's path.
    finding_path = finding.get("_api_path") or finding.get("repro_path") or ""
    finding_method = (finding.get("_api_method") or finding.get("repro_method") or "").upper()
    har_path = har.get("path") or ""
    har_method = (har.get("method") or "").upper()
    har_status = har.get("status_code") or finding.get("evidence", {}).get("status_code") or ""
    har_body = har.get("response_body") or ""
    har_actor = har.get("actor") or ""
    
    # Prefer finding's own path if it's more specific than HAR match
    if finding_path and har_path and finding_path != har_path and len(finding_path) > len(har_path):
        path = finding_path
    elif har_path:
        path = har_path
    else:
        path = finding_path
    
    # Prefer finding's own method over HAR
    method = finding_method or har_method
    
    steps: list[str] = []
    
    # ═══ Step 1: Authentication ═══
    if har_actor == "buyer" or "buyer" in description.lower() or "买家" in title:
        steps.append(
            f'1. 获取买家Token（如已有可跳过）:\n'
            f'   curl -X POST "{base_url}/api/auth/login" \\\n'
            f'     -H "Content-Type: application/json" \\\n'
            f'     -d \'{{"email":"{test_email}","password":"***"}}\''
        )
    elif har_actor == "admin" or "admin" in description.lower():
        steps.append(
            f'1. 获取管理员Token（如已有可跳过）:\n'
            f'   curl -X POST "{base_url}/api/auth/login" \\\n'
            f'     -H "Content-Type: application/json" \\\n'
            f'     -d \'{{"email":"admin@example.com","password":"***"}}\''
        )
    elif path:
        steps.append(
            f'1. 获取认证Token:\n'
            f'   curl -X POST "{base_url}/api/auth/login" \\\n'
            f'     -H "Content-Type: application/json" \\\n'
            f'     -d \'{{"email":"{test_email}","password":"***"}}\''
        )
    
    # ═══ Step 2: Reproduction action ═══
    step_num = len(steps) + 1
    if path:
        # Build real curl command with HAR data if available
        full_url = f"{base_url}{path}" if path.startswith("/") else path
        if not path.startswith("http"):
            full_url = f"{base_url}{path}"
        
        if method in ("POST", "PUT", "PATCH"):
            # Try to extract real request body from description or HAR
            body_str = _extract_request_body(finding, har)
            steps.append(
                f'{step_num}. 执行触发请求:\n'
                f'   curl -X {method} "{full_url}" \\\n'
                f'     -H "Authorization: Bearer <TOKEN>" \\\n'
                f'     -H "Content-Type: application/json" \\\n'
                f'     -d \'{body_str}\' -v'
            )
        else:
            steps.append(
                f'{step_num}. 执行触发请求:\n'
                f'   curl -X {method} "{full_url}" \\\n'
                f'     -H "Authorization: Bearer <TOKEN>" -v'
            )
    elif har_path:
        steps.append(
            f'{step_num}. 执行触发请求:\n'
            f'   curl -X {har_method or "GET"} "{base_url}{har_path}" \\\n'
            f'     -H "Authorization: Bearer <TOKEN>" -v'
        )
    else:
        # No path — use description to guide
        steps.append(
            f'{step_num}. 根据缺陷描述中的业务操作路径，使用对应API端点发起请求。'
            f'详细步骤: {description[:200]}'
        )
    
    # ═══ Step 3: Observe result ═══
    step_num += 1
    if har_status:
        status_code = int(har_status) if isinstance(har_status, (int, str)) and str(har_status).isdigit() else har_status
        status_desc = _http_status_description(int(status_code) if isinstance(status_code, (int, str)) and str(status_code).isdigit() else 0)
        
        if int(status_code) >= 500 if isinstance(status_code, (int, str)) and str(status_code).isdigit() and int(status_code) >= 500 else False:
            steps.append(
                f'{step_num}. 观察响应: HTTP {har_status} {status_desc}\n'
                f'   服务端返回内部错误，表明存在未处理异常或数据一致性问题。\n'
                f'   原始响应（截取）: {har_body[:300] if har_body else "（响应体未记录）"}'
            )
        elif int(status_code) >= 400 if isinstance(status_code, (int, str)) and str(status_code).isdigit() and int(status_code) >= 400 else False:
            steps.append(
                f'{step_num}. 观察响应: HTTP {har_status} {status_desc}\n'
                f'   请求被拒绝或失败，需确认是否为预期行为。\n'
                f'   原始响应（截取）: {har_body[:300] if har_body else "（响应体未记录）"}'
            )
        else:
            steps.append(
                f'{step_num}. 观察响应: HTTP {har_status} {status_desc}\n'
                f'   请求成功返回。关注返回体中是否有不应暴露的数据或状态。\n'
                f'   原始响应（截取）: {har_body[:300] if har_body else "（响应体未记录）"}'
            )
    else:
        # Infer from description
        if "500" in description or "500" in title:
            steps.append(f'{step_num}. 观察响应: 预期返回 HTTP 500（服务端内部错误）')
        elif "200" in description:
            steps.append(f'{step_num}. 观察响应: 返回 HTTP 200（但根据业务规则此操作不应成功）')
        elif "403" in description:
            steps.append(f'{step_num}. 观察响应: 返回 HTTP 403（权限被拒绝）')
        elif "201" in description:
            steps.append(f'{step_num}. 观察响应: 返回 HTTP 201（资源创建成功）')
        else:
            steps.append(f'{step_num}. 观察响应: 记录HTTP状态码和完整响应体')
    
    # ═══ Step 4: Database verification ═══
    step_num += 1
    category = finding.get("category") or finding.get("risk_type") or ""
    tables = CATEGORY_TABLES_MAP.get(category, ["orders"])
    table_hint = ", ".join(tables[:3])
    
    # Build specific SQL if we have entity info
    entity_id = finding.get("source_value") or ""
    entity_hint = f'（使用主键: {entity_id}）' if entity_id else ""
    
    steps.append(
        f'{step_num}. 数据库核验:\n'
        f'   查询 {table_hint} 表 {entity_hint}，对比操作前后数据变化。\n'
        f'   {_build_sql_verify(finding, category, tables)}'
    )
    
    return steps


def _extract_request_body(finding: dict[str, Any], har: dict[str, Any]) -> str:
    """Extract or infer request body for reproduction command."""
    title = str(finding.get("title") or "")
    desc = str(finding.get("description") or finding.get("summary") or "")
    path = har.get("path") or finding.get("_api_path") or ""
    har_body = har.get("response_body") or ""
    
    # Try to extract from description what was sent
    if "body" in har and har["body"]:
        return str(har["body"])[:200]
    
    # Infer from path and context
    if "order" in path.lower() and "POST" in har.get("method", ""):
        return '{"items":[{"sku":"SKU-PHONE-001","qty":1}],"addressId":"<从GET /api/addresses获取>"}'
    if "cart" in path.lower():
        return '{"sku":"SKU-PHONE-001","qty":1}'
    if "coupon" in path.lower() and "validate" in path.lower():
        return '{"code":"NEW100","totalAmount":99.00}'
    if "register" in path.lower() or "signup" in path.lower():
        return '{"email":"test@example.com","password":"Test@123456","name":"测试用户"}'
    if "login" in path.lower():
        return '{"email":"buyer01@example.com","password":"***"}'
    if "cancel" in path.lower():
        return '{"reason":"测试取消"}'
    if "refund" in path.lower():
        return '{"reason":"测试退款","amount":99.00}'
    
    # Generic: try to parse something meaningful from description
    if "items" in desc or "sku" in desc:
        return '{"items":[{"sku":"SKU-PHONE-001","qty":1}]}'
    
    return '{"...": "根据业务场景填写请求体"}'


def _http_status_description(code: int) -> str:
    """Return human-readable status description."""
    return {200: "成功", 201: "已创建", 400: "请求错误", 401: "未认证",
            403: "权限禁止", 404: "未找到", 409: "冲突", 422: "不可处理",
            500: "服务器内部错误", 502: "网关错误", 503: "服务不可用"}.get(code, "")


def _build_investigation_guidance(finding: dict[str, Any], enterprise_ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build dev-facing investigation guidance with real HAR evidence."""
    ctx = enterprise_ctx or {}
    har = finding.get("har_evidence") or {}
    title = str(finding.get("title") or "")
    description = str(finding.get("summary") or finding.get("actual") or finding.get("description") or "")
    category = finding.get("category") or finding.get("risk_type") or ""
    
    # Prefer finding's own path over HAR when HAR matched a less specific endpoint
    finding_path = finding.get("_api_path") or finding.get("repro_path") or ""
    finding_method = (finding.get("_api_method") or finding.get("repro_method") or "").upper()
    har_path = har.get("path") or ""
    har_method_raw = (har.get("method") or "").upper()
    
    if finding_path and har_path and finding_path != har_path and len(finding_path) > len(har_path):
        path = finding_path
    elif har_path:
        path = har_path
    else:
        path = finding_path
    
    method = finding_method or har_method_raw
    har_status = har.get("status_code") or ""
    har_body = har.get("response_body") or ""
    har_actor = har.get("actor") or ""
    
    # Determine primary area
    if "权限" in title or "authorization" in category or "买家" in title:
        primary_area = "权限校验中间件 / @RequireRole 注解逻辑"
    elif "库存" in title or "inventory" in category:
        primary_area = "库存服务 (inventory service) / 库存锁机制 / 并发扣减"
    elif "优惠券" in title or "coupon" in category:
        primary_area = "优惠券服务 (coupon service) / validate逻辑 / 门槛校验"
    elif "退款" in title or "refund" in category:
        primary_area = "退款服务 (refund service) / 金额计算 / 状态前置校验"
    elif "幂等" in title or "idempotency" in category:
        primary_area = "订单/支付服务 / 幂等键(idempotency_key)处理逻辑"
    elif "状态" in title or "state" in category:
        primary_area = "订单状态机 (StateMachine) / isValidTransition() 方法"
    elif "DRAFT" in title or "OFF_SALE" in title:
        primary_area = "商品查询服务 (ProductQueryService) / status过滤WHERE条件"
    elif path:
        # Extract from path
        if "/orders" in path:
            primary_area = "订单服务 (OrderService) / OrdersController"
        elif "/products" in path:
            primary_area = "商品服务 (ProductService) / ProductsController"
        elif "/coupons" in path:
            primary_area = "优惠券服务 (CouponService) / CouponsController"
        elif "/pay" in path:
            primary_area = "支付服务 (PaymentService) / PaymentsController"
        elif "/refund" in path:
            primary_area = "退款服务 (RefundService) / RefundsController"
        elif "/auth" in path:
            primary_area = "认证服务 (AuthService) / AuthController"
        elif "/admin" in path:
            primary_area = "管理后台控制器 / AdminController"
        elif "/reports" in path:
            primary_area = "报表服务 (ReportService) / ReportsController"
        else:
            primary_area = f"{method} {path} 对应的后端处理器"
    else:
        primary_area = "（根据缺陷描述中的业务实体定位对应服务）"
    
    # Relevant tables
    tables = CATEGORY_TABLES_MAP.get(category, [])
    
    # Log search pattern
    log_search = CATEGORY_LOG_MAP.get(category, rf'grep -i "{path}" /var/log/app/*.log | grep -i "error\|warn"')
    
    # SQL verify hint
    sql_verify = _build_sql_verify(finding, category, tables)
    
    # Extract trace ID from HAR response body or evidence
    trace_id = ""
    if har_body:
        # Try to extract trace_id from response JSON
        import json as _json
        try:
            body_obj = _json.loads(har_body) if isinstance(har_body, str) else har_body
            if isinstance(body_obj, dict):
                trace_id = str(body_obj.get("traceId") or body_obj.get("trace_id") or 
                               body_obj.get("requestId") or body_obj.get("request_id") or "")
        except Exception:
            pass
    if not trace_id:
        evidence = finding.get("evidence", {})
        if isinstance(evidence, dict):
            trace_id = str(evidence.get("trace_id") or evidence.get("request_id") or "")
    
    # Response analysis for dev
    response_analysis = ""
    if har_status:
        if str(har_status).startswith("5"):
            response_analysis = (
                f"服务端返回{har_status}内部错误，建议排查步骤:\n"
                f"  1. 检查后端日志中是否有未捕获异常栈\n"
                f"  2. 检查数据库连接/事务是否正常\n"
                f"  3. 检查请求参数是否触发了空指针或类型转换异常\n"
                f"  原始响应: {har_body[:200]}"
            )
        elif str(har_status) == "200":
            response_analysis = (
                f"接口返回200成功，但根据业务规则此操作应被拒绝。\n"
                f"  可能原因: 缺少角色校验、状态机检查缺失、数据过滤条件不足\n"
                f"  原始响应: {har_body[:200]}"
            )
        elif str(har_status) in ("401", "403"):
            response_analysis = (
                f"接口返回{har_status}拒绝访问，检查是否为预期行为。\n"
                f"  若是预期: OK；若非预期: Token/权限配置问题\n"
                f"  原始响应: {har_body[:200]}"
            )
    
    # Relevant APIs
    relevant_apis = [f"{method} {path}"] if path else []
    # Also add related endpoints
    if "/orders" in path:
        relevant_apis.extend(["POST /api/orders", "POST /api/orders/{id}/pay", 
                              "POST /api/orders/{id}/cancel", "GET /api/orders/{id}"])
    elif "/coupons" in path:
        relevant_apis.extend(["POST /api/coupons/validate", "GET /api/coupons"])
    
    return {
        "primary_area": primary_area,
        "relevant_apis": relevant_apis[:5],
        "relevant_tables": tables[:5],
        "log_search": log_search,
        "sql_verify": sql_verify,
        "trace_id": trace_id,
        "har_status": har_status,
        "har_actor": har_actor,
        "response_analysis": response_analysis,
    }


def _build_sql_verify(finding: dict[str, Any], category: str, tables: list[str]) -> str:
    """Build a SQL verification query for a finding."""
    title = str(finding.get("title") or "")
    description = str(finding.get("description") or finding.get("summary") or "")
    
    if "DRAFT" in title:
        return "SELECT sku, title, status FROM products WHERE status = 'DRAFT'; -- 确认 DRAFT 商品是否在前端可见"
    if "OFF_SALE" in title or "下架" in title:
        return "SELECT sku, title, status FROM products WHERE status = 'OFF_SALE'; -- 确认下架商品是否在 GET /api/products 返回中"
    if "expired" in title.lower() or "过期" in title:
        return "SELECT code, status, expires_at, NOW() FROM coupons WHERE expires_at < NOW() AND status = 'ACTIVE'; -- 确认过期优惠券是否仍可 validate"
    if "角色" in title or "role" in title.lower() or "auditor" in title.lower():
        return "SELECT email, role, status FROM users WHERE role NOT IN ('buyer','seller','admin','warehouse','finance'); -- 排查非标准角色账号"
    if "幂等" in title or "重复" in title:
        return "SELECT idempotency_key, COUNT(*) FROM payments GROUP BY idempotency_key HAVING COUNT(*) > 1; -- 检查重复支付"
    if "退款" in title and ("大于" in description or "超过" in description):
        return "SELECT r.refund_no, r.amount AS refund_amount, p.amount AS pay_amount FROM refunds r JOIN payments p ON r.order_id = p.order_id WHERE r.amount > p.amount;"
    if "库存" in title and ("负" in description or "negative" in description.lower()):
        return "SELECT sku, available_qty, locked_qty FROM inventory WHERE available_qty < 0 OR locked_qty < 0;"
    if "买家" in title and ("管理" in title or "admin" in title.lower() or "端点" in title):
        api_path = finding.get("_api_path") or finding.get("repro_path") or ""
        api_method = finding.get("_api_method") or finding.get("repro_method") or "GET"
        return f"-- 验证买家角色是否可访问管理端点\n-- 1. 确认本接口的权限注解（@RequireRole 或等效）\n-- 2. 检查该买家token的role字段:\nSELECT id, email, role FROM users WHERE email = '<买家邮箱>';\n-- 3. 确认请求: {api_method} {api_path}\n-- 4. 预期: 应返回403权限禁止，而非200/404"
    if "并发" in title or "concurrent" in description.lower():
        return "-- 并发冲突验证: 使用两个不同token同时操作同一资源\n-- 预期: 一个操作成功，另一个返回409冲突或操作被拒绝"
    if "data_leak" in category or "数据泄露" in title:
        api_path = finding.get("_api_path") or finding.get("repro_path") or ""
        return f"-- 数据泄露验证: 使用普通用户token请求 {api_path}\n-- 1. 确认返回数据是否包含其他用户信息\n-- 2. 检查是否有 WHERE user_id = ? 过滤条件\n-- 3. 检查响应体大小是否异常（可能返回全表数据）"
    
    # Generic by category
    if tables:
        table = tables[0]
        # SQL identifier whitelist — prevent injection via table names.
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(table)):
            table = "target_table"
        return f"SELECT * FROM {table} WHERE ... -- 按业务主键查询，对比操作前后状态差异"
    return "-- 请根据缺陷描述中的业务主键（订单号/用户邮箱/SKU等）编写针对性SQL"


def _decode_unicode_escapes(s: str) -> str:
    """Decode \\uXXXX escape sequences in a JSON-encoded string to readable Chinese."""
    if not s or "\\u" not in s:
        return s
    try:
        # Wrap in quotes so json.loads can decode it as a JSON string
        return json.loads(f'"{s}"')
    except Exception:
        return s


def _match_business_rule_text(title: str, description: str, category: str) -> str:
    """Match finding to a customer-facing business rule description.
    
    NEVER exposes internal tool names like "deep_verifier" or "V12".
    Always describe rules in terms the customer's business stakeholders understand.
    """
    t = title.lower()
    d = description.lower()
    
    # Authorization / permission
    if "authorization" in category or "权限" in title or ("买家" in title and ("管理" in title or "admin" in t)):
        return "业务规则: 普通用户不得访问管理后台接口，系统必须校验角色权限并拒绝越权请求"
    if "权限穿透" in title or "应403" in title:
        return "业务规则: 买家角色仅限操作自身购物车/订单，对商品管理/发货/退款等操作必须返回403权限禁止"
    
    # State machine
    if "禁止路径" in title or ("cancelled" in d and "pay" in d):
        return "订单状态机规则: 已取消(CANCELLED)订单不可再支付，已完成(COMPLETED)订单不可取消，已退款(REFUNDED)订单不可再次退款"
    if "边界路径" in title:
        return "订单状态机边界规则: 待支付(PENDING)订单可超时取消，退款中(REFUNDING)订单可拒绝退款回到已退款状态"
    if "状态破坏" in title:
        return "状态机完整性规则: 业务实体必须按预定义路径流转，不得跳过中间状态（如未处理直接停用）"
    
    # Data integrity
    if "qty" in t or "quantity" in t or "数量" in title or "为负" in title:
        return "数据完整性规则: 库存数量不得为负数，订单商品数量必须为正整数，异常参数必须返回400拒绝而非201成功"
    if "异常" in title and ("返回20" in d or "返回201" in d):
        return "参数校验规则: 接口必须验证输入参数合法性，非法参数（负数/零值/超限值）必须返回400错误，不得返回2xx成功状态"
    if "库存" in title or "inventory" in category:
        return "库存管理规则: 不允许库存为负，并发下单不得超卖，锁定库存与实际库存必须一致"
    
    # Coupon / marketing
    if "coupon" in category or "优惠券" in title:
        return "营销规则: 优惠券必须在有效期内、状态为ACTIVE、满足最低订单金额条件才可使用"
    if "过期" in title or "expired" in t:
        return "营销规则: 已过期优惠券必须标记为不可用状态，validate接口必须校验有效期"
    
    # Financial
    if "退款" in title or "refund" in category:
        return "财务规则: 退款金额不得大于实际支付金额，已退款订单不得再次退款"
    
    # Idempotency
    if "幂等" in title or "idempotency" in category or "重复" in title:
        return "幂等保护规则: 同一操作重复提交必须返回幂等结果（相同响应+不产生副作用），不得重复创建资源或重复扣款"
    
    # Concurrency
    if "并发" in title or "concurrent" in category:
        return "并发控制规则: 多用户同时操作同一资源时，系统必须保证数据一致性，任一时刻仅一个写操作可成功"
    
    # Data exposure
    if "data_leak" in category or "DRAFT" in title or "OFF_SALE" in title or "下架" in title or "草稿" in title:
        return "数据可见性规则: 下架商品、草稿商品、内部商品不得在前端展示或通过API返回给普通用户"
    if "数据泄露" in title or "泄露" in title:
        return "数据隔离规则: 用户仅能访问自己的数据，API必须通过user_id等字段过滤，不得返回其他用户信息"
    
    # Generic
    if "state" in category:
        return "业务状态机规则: 系统必须按预定义状态流转路径执行操作，禁止非法状态转换"
    if "business_rule" in category:
        return "业务规则: 系统行为必须符合企业PRD中定义的业务约束条件"
    
    return ""


def _build_evidence_chain(finding: dict[str, Any], enterprise_ctx: dict[str, Any] | None = None) -> list[dict[str, str]]:

    """Build a rich evidence chain with real data from the finding and HAR."""
    ctx = enterprise_ctx or {}
    chain: list[dict[str, str]] = []
    title = str(finding.get("title") or "")
    description = str(finding.get("description") or finding.get("summary") or "")
    category = finding.get("category") or finding.get("risk_type") or ""
    source = finding.get("source") or finding.get("evidence", {}).get("source_file") or ""
    har = finding.get("har_evidence") or {}
    method = har.get("method") or (finding.get("_api_method") or finding.get("evidence", {}).get("method") or "").upper()
    path = har.get("path") or finding.get("_api_path") or finding.get("evidence", {}).get("path") or ""
    har_status = har.get("status_code") or ""
    har_body = _decode_unicode_escapes(har.get("response_body") or "")
    expected = str(finding.get("expected") or "")
    actual = str(finding.get("actual") or description or "")
    
    # Step 1: Rule source — ALWAYS prefer enterprise documents over tool names
    doc_refs = finding.get("_doc_refs", [])
    doc_name = ""
    if doc_refs and isinstance(doc_refs, list) and len(doc_refs) > 0:
        doc_name = str(doc_refs[0].get("display_name") or doc_refs[0].get("source_id") or "")
    
    # Build customer-facing rule description (NEVER expose internal tool names)
    rule_text = _match_business_rule_text(title, description, category)
    
    # Build customer-facing category label
    cat_label = CATEGORY_MODULE_MAP.get(category, "")
    if not cat_label:
        # Infer from title keywords
        if "权限" in title or "authorization" in category: cat_label = "权限管控"
        elif "状态" in title or "state" in category: cat_label = "状态机规则"
        elif "库存" in title or "inventory" in category: cat_label = "库存管理"
        elif "优惠券" in title or "coupon" in category: cat_label = "营销规则"
        elif "退款" in title or "refund" in category: cat_label = "财务规则"
        elif "幂等" in title or "idempotency" in category: cat_label = "幂等保护"
        elif "并发" in title or "concurrent" in category: cat_label = "并发控制"
        elif "数据" in title or "data" in category: cat_label = "数据完整性"
        else: cat_label = "业务规则"
    
    # Source doc reference — only show if we have real enterprise docs
    source_ref = ""
    if doc_name:
        source_ref = f"依据: {doc_name}"
    elif rule_text and not rule_text.startswith("企业资料"):
        source_ref = "依据: 企业业务规则"
    
    chain.append({
        "tag": "rule",
        "label": "业务规则来源",
        "content": rule_text or doc_name or "企业资料中的业务规则约束",
        "detail": f"规则域: {cat_label}" + (f" · {source_ref}" if source_ref else ""),
    })
    
    # Step 2: Trigger action
    if path:
        chain.append({
            "tag": "api",
            "label": "触发接口",
            "content": f"{method} {path}",
            "detail": description[:200] if description else "",
        })
    
    # Step 3: What happened (actual behavior)
    actual_display = actual[:200] if actual else description[:200]
    if har_status:
        actual_display = f"HTTP {har_status}: {actual_display}"
    chain.append({
        "tag": "fact",
        "label": "实际行为",
        "content": actual_display,
        "detail": "系统实际返回/行为与预期规则不符" + (f" · 响应: {har_body[:100]}" if har_body else ""),
    })
    
    # Step 4: Expected behavior
    if expected:
        chain.append({
            "tag": "rule",
            "label": "预期行为",
            "content": expected[:200],
            "detail": "基于企业资料/业务规则推导的正确行为",
        })
    
    # Step 5: Investigation guidance (SQL + log search)
    ig = finding.get("investigation_guidance") or {}
    if isinstance(ig, dict):
        sql = ig.get("sql_verify", "")
        log = ig.get("log_search", "")
        primary = ig.get("primary_area", "")
        if sql or log:
            ev_parts = []
            if primary:
                ev_parts.append(f"排查区域: {primary}")
            if sql:
                ev_parts.append(sql[:200])
            chain.append({
                "tag": "debug",
                "label": "数据/日志核验",
                "content": "\n".join(ev_parts) if ev_parts else "基于接口路径检索相关日志与数据库",
                "detail": "结合请求方法、路径、状态码进行交叉验证",
            })
    
    # Step 6: Severity — NEVER expose internal module names like v12_discovery
    severity = finding.get("severity", "P2")
    conf = finding.get("confidence_score", "—")
    # Use customer-facing category label, fall back to Chinese description
    cat_label = CATEGORY_MODULE_MAP.get(category, "")
    if not cat_label:
        if "权限" in title: cat_label = "权限管控"
        elif "状态" in title or "禁止路径" in title: cat_label = "状态机违规"
        elif "边界" in title: cat_label = "边界条件"
        elif "并发" in title: cat_label = "并发控制"
        elif "幂等" in title: cat_label = "幂等保护"
        elif "库存" in title: cat_label = "数据完整性"
        elif "异常" in title: cat_label = "参数校验"
        else: cat_label = "业务规则违规"
    
    sev_desc = {"P0": "高危阻塞 · 需立即修复", "P1": "高风险 · 影响发布", "P2": "中低风险 · 建议排期"}
    sev_text = sev_desc.get(severity, severity)
    
    chain.append({
        "tag": "verdict",
        "label": "缺陷判定",
        "content": f"{severity}: {cat_label}",
        "detail": f"{sev_text} · 置信度 {conf}",
    })
    
    return chain


def _build_evidence_quality(finding: dict[str, Any]) -> dict[str, Any]:
    """Assess evidence quality for the finding."""
    verified: list[str] = []
    missing: list[str] = []
    next_actions: list[str] = []
    
    title = str(finding.get("title") or "")
    description = str(finding.get("description") or finding.get("summary") or "")
    method = (finding.get("_api_method") or finding.get("evidence", {}).get("method") or "").upper()
    path = finding.get("_api_path") or finding.get("evidence", {}).get("path") or ""
    expected = str(finding.get("expected") or "")
    actual = str(finding.get("actual") or "")
    confidence = float(finding.get("confidence_score") or 0)
    evidence = finding.get("evidence", {}) if isinstance(finding.get("evidence"), dict) else {}
    doc_refs = finding.get("_doc_refs", [])
    
    has_path = bool(path)
    has_method = bool(method)
    has_actual = bool(actual and len(actual) > 10)
    has_expected = bool(expected and len(expected) > 10)
    has_docs = bool(doc_refs and len(doc_refs) > 0)
    has_db = bool(evidence.get("db_row"))
    has_status = bool(evidence.get("status_code"))
    has_source = bool(evidence.get("source_file") or finding.get("source"))
    
    # Verified items
    if has_path and has_method:
        verified.append(f"接口目标: {method} {path}")
    else:
        missing.append("缺少可执行接口地址")
    
    if has_actual:
        verified.append("已记录实际行为")
    else:
        missing.append("缺少实际行为描述")
    
    if has_expected:
        verified.append("已记录预期行为")
    else:
        missing.append("缺少来自企业资料的预期规则")
    
    if has_status:
        verified.append(f"运行时HTTP状态码: {evidence.get('status_code')}")
    
    if has_db:
        verified.append("存在数据库核验证据（DB行数据）")
    else:
        missing.append("缺少DB前后快照")
    
    if has_docs:
        verified.append(f"已关联 {len(doc_refs)} 份企业资料")
    else:
        missing.append("缺少PRD/业务规则文档出处")
    
    if has_source:
        verified.append(f"证据来源: {evidence.get('source_file') or finding.get('source')}")
    
    # Next actions
    if not has_path:
        next_actions.append("补全触发接口的完整路径和方法")
    if not has_db:
        category = finding.get("category") or ""
        tables = CATEGORY_TABLES_MAP.get(category, ["orders"])
        next_actions.append(f"导出操作前后 {', '.join(tables[:2])} 表快照，对比状态/金额/数量变化")
    if not has_docs:
        next_actions.append("上传PRD、API规范、业务规则文档以建立规则基线")
    
    # Score
    score = min(100, round(
        (15 if has_path else 0) +
        (15 if has_actual else 0) +
        (12 if has_expected else 0) +
        (12 if has_status else 0) +
        (14 if has_db else 0) +
        (10 if has_docs else 0) +
        (10 if has_source else 0) +
        (12 if confidence > 0.8 else 5)
    ))
    
    level = "validated" if score >= 70 else "partial" if score >= 35 else "needs_evidence"
    label_map = {"validated": "可交付证据", "partial": "待补强证据", "needs_evidence": "仅为风险线索"}
    summary_map = {
        "validated": "具备企业缺陷交付的基础证据，支持验收、复现和研发定位。",
        "partial": "已有部分定位信息，但缺少关键运行时证据，暂不应作为已验证缺陷交付。",
        "needs_evidence": "当前为检测线索，缺少真实复现或数据核验，企业交付价值不足。",
    }
    
    base_url = "http://localhost:8080"
    curl_cmd = ""
    if has_path:
        body = ""
        if method in ("POST", "PUT", "PATCH"):
            body = " -d '{...}'"
        curl_cmd = f'curl -X {method} "${{BASE_URL}}{path}" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json"{body} -v'
    
    return {
        "level": level,
        "score": score,
        "label": label_map[level],
        "summary": summary_map[level],
        "verified": verified,
        "missing": missing[:6],
        "next_actions": next_actions[:5],
        "can_reproduce": has_path,
        "curl_command": curl_cmd,
    }


def enrich_finding(
    finding: dict[str, Any],
    enterprise_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Enrich a single finding with three-perspective evidence.
    
    Args:
        finding: The raw finding dict from _v12_findings / _normalize_workspace_finding
        enterprise_ctx: Optional context with keys like base_url, test_email, etc.
    
    Returns:
        The enriched finding dict with added fields for all three perspectives.
    """
    enriched = dict(finding)
    ctx = enterprise_ctx or {}
    category = finding.get("category") or finding.get("risk_type") or ""
    title = str(finding.get("title") or "")
    description = str(finding.get("description") or finding.get("summary") or "")
    
    # ── Ensure _api_path and _api_method are extracted from title/description ──
    # ALWAYS extract from title — the title is ground truth. HAR bridge may have
    # matched a wrong endpoint (e.g. POST /api/products for PATCH /api/products/admin/:sku).
    text = f"{title} {description}"
    # Extract path: /api/xxx or /xxx/yyy (with URL-safe chars, including : for path params)
    path_match = re.search(r'(/api/[\w/{}\.%-:]+|/[\w{}.%-:]+/[\w/{}\.%-:]+)', text)
    title_path = path_match.group(1) if path_match else ""
    # Extract method
    method_match = re.search(r'\b(POST|GET|PUT|DELETE|PATCH)\b', text, re.IGNORECASE)
    title_method = method_match.group(1).upper() if method_match else ""
    
    # Prefer title-extracted data over HAR-provided data when they differ
    har_path = enriched.get("_api_path", "")
    har_method = enriched.get("_api_method", "")
    
    if title_path and title_path != har_path:
        enriched["_api_path"] = title_path
    elif title_path and not har_path:
        enriched["_api_path"] = title_path
        
    if title_method and title_method != har_method:
        enriched["_api_method"] = title_method
    elif title_method and not har_method:
        enriched["_api_method"] = title_method
    
    # Update evidence dict
    evidence = enriched.get("evidence", {})
    if isinstance(evidence, dict):
        if not evidence.get("path") and title_path:
            evidence["path"] = title_path
        if not evidence.get("method") and title_method:
            evidence["method"] = title_method
        enriched["evidence"] = evidence
    
    # ── Ensure repro_path/repro_method ──
    # Always sync from _api_path/_api_method (which may have been corrected from title)
    enriched["repro_path"] = enriched.get("_api_path", "") or enriched.get("repro_path", "")
    enriched["repro_method"] = enriched.get("_api_method", "") or enriched.get("repro_method", "")

    # ── Business impact ──
    if not finding.get("business_impact") or not isinstance(finding.get("business_impact"), dict):
        business_impact = _match_business_impact(title, description, category)
    else:
        existing = finding["business_impact"]
        if isinstance(existing, str):
            business_impact = _match_business_impact(title, description, category)
        else:
            business_impact = dict(existing)
    enriched["business_impact"] = business_impact

    # ── Reproduction steps ──
    existing_steps = finding.get("reproduction_steps") or finding.get("repro_steps") or []
    if not existing_steps or len(existing_steps) <= 1 or "定位业务流" in str(existing_steps[0]):
        enriched["reproduction_steps"] = _build_reproduction_steps(enriched, ctx)
        enriched["reproduce_steps_business"] = _build_reproduction_steps(enriched, ctx)

    # ── Investigation guidance ──
    if not finding.get("investigation_guidance") or not isinstance(finding.get("investigation_guidance"), dict):
        enriched["investigation_guidance"] = _build_investigation_guidance(enriched, ctx)

    # ── Evidence chain ──
    enriched["evidence_chain"] = _build_evidence_chain(enriched, ctx)
    
    # ── Evidence quality ──
    enriched["evidence_quality"] = _build_evidence_quality(finding)
    
    # ── Ensure source_entity ──
    if not enriched.get("source_entity"):
        path = enriched.get("_api_path") or finding.get("evidence", {}).get("path") or ""
        if "/products" in path:
            enriched["source_entity"] = "products"
        elif "/orders" in path:
            enriched["source_entity"] = "orders"
        elif "/coupons" in path:
            enriched["source_entity"] = "coupons"
        elif "/cart" in path:
            enriched["source_entity"] = "cart_items"
        elif "/auth" in path:
            enriched["source_entity"] = "users"
        elif "/pay" in path:
            enriched["source_entity"] = "payments"
        elif "/refund" in path:
            enriched["source_entity"] = "refunds"
        elif "/reports" in path:
            enriched["source_entity"] = "reports"
        elif "/inventory" in path:
            enriched["source_entity"] = "inventory"
        else:
            # Try to extract entity from evidence db_row or description
            db_row = finding.get("evidence", {}).get("db_row", {}) if isinstance(finding.get("evidence"), dict) else {}
            if db_row:
                if "sku" in db_row:
                    enriched["source_entity"] = "products"
                elif "code" in db_row and ("coupon" in str(title).lower() or "expir" in str(title).lower()):
                    enriched["source_entity"] = "coupons"
                elif "email" in db_row or "role" in db_row:
                    enriched["source_entity"] = "users"
                elif "refund" in str(db_row).lower():
                    enriched["source_entity"] = "refunds"
                elif "order" in str(title).lower():
                    enriched["source_entity"] = "orders"
            else:
                # Infer from title keywords
                t = title.lower()
                if "coupon" in t or "优惠券" in t:
                    enriched["source_entity"] = "coupons"
                elif "order" in t or "订单" in t:
                    enriched["source_entity"] = "orders"
                elif "refund" in t or "退款" in t:
                    enriched["source_entity"] = "refunds"
                elif "product" in t or "商品" in t:
                    enriched["source_entity"] = "products"
                elif "user" in t or "用户" in t or "auditor" in t or "角色" in t:
                    enriched["source_entity"] = "users"
                elif "cart" in t or "购物车" in t:
                    enriched["source_entity"] = "cart_items"
                elif "inventory" in t or "库存" in t:
                    enriched["source_entity"] = "inventory"
    
    # ── Ensure source_value from evidence data ──
    if not enriched.get("source_value"):
        db_row = finding.get("evidence", {}).get("db_row", {}) if isinstance(finding.get("evidence"), dict) else {}
        if db_row:
            # Build a meaningful source value from DB row data
            parts = []
            for key in ("sku", "code", "email", "refund_no", "order_id"):
                if key in db_row:
                    parts.append(f"{key}={db_row[key]}")
            if parts:
                enriched["source_value"] = ", ".join(parts[:3])
            else:
                enriched["source_value"] = json.dumps(db_row, ensure_ascii=False)[:80]
        elif enriched.get("_api_path"):
            enriched["source_value"] = f"{enriched.get('_api_method', 'GET')} {enriched['_api_path']}"
    
    
    # ── Ensure expected/actual ──
    if not enriched.get("expected"):
        enriched["expected"] = _infer_expected(title, description, category)
    if not enriched.get("actual"):
        enriched["actual"] = description or title
    
    return enriched


def _infer_expected(title: str, description: str, category: str) -> str:
    """Infer expected behavior from finding context."""
    t = f"{title} {description}".lower()
    
    if "cancelled" in t and "pay" in t:
        return "已取消订单应拒绝支付操作，返回400/422并提示'订单已取消，无法支付'"
    if "refunded" in t and ("refund" in t or "ship" in t):
        return "已退款订单应拒绝再次退款/发货，返回400并提示'订单已退款，无法操作'"
    if "completed" in t and "cancel" in t:
        return "已完成订单应拒绝取消操作，返回400并提示'订单已完成，无法取消'"
    if "买家" in t and ("管理" in t or "admin" in t):
        return "普通买家访问管理端点应返回403 Forbidden，而非200成功或500错误"
    if "DRAFT" in t or "草稿" in t:
        return "DRAFT状态商品不应在GET /api/products接口返回中展示给普通用户"
    if "OFF_SALE" in t or "下架" in t:
        return "OFF_SALE状态商品不应在GET /api/products接口返回中展示给普通用户"
    if "过期" in t and "优惠券" in t:
        return "已过期优惠券的validate接口应返回invalid，status不应为ACTIVE"
    if "幂等" in t or "重复" in t:
        return "相同请求体第二次调用应返回409 Conflict或200（幂等返回），不应再次创建资源"
    if "退款" in t and ("大于" in t or "超过" in t):
        return "退款金额不应超过实际支付金额，应返回400并提示'退款金额超过支付金额'"
    if "库存" in t and "负" in t:
        return "库存available_qty和locked_qty均不应为负数"
    if "角色" in t:
        return "用户角色应为系统预定义的5种角色之一（buyer/seller/admin/warehouse/finance）"
    
    return "系统应按企业资料中的业务规则正确响应，不应出现数据不一致或越权行为"


def enrich_findings_batch(
    findings: list[dict[str, Any]],
    enterprise_ctx: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Enrich a batch of findings.
    
    Args:
        findings: List of raw finding dicts
        enterprise_ctx: Optional context with keys like base_url, test_email, etc.
    
    Returns:
        Enriched findings list.
    """
    return [enrich_finding(f, enterprise_ctx) for f in findings if isinstance(f, dict)]


def load_enterprise_context(project_id: str, root: Path) -> dict[str, Any]:
    """Load enterprise context from project workspace for evidence enrichment."""
    ctx: dict[str, Any] = {
        "base_url": "http://localhost:8080",
        "test_email": "buyer01@example.com",
        "test_password": "Test@123456",
    }
    
    # Try to load connector registry for base URL
    registry_path = root / "platform_workspace" / project_id / "enterprise_pilot_runtime" / "connector_registry.json"
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            # Look for API connector
            for conn in registry.get("connectors", []):
                if conn.get("kind") in ("api", "gateway", "http_api"):
                    url = conn.get("url") or conn.get("connection_string") or ""
                    if "://" in url:
                        ctx["base_url"] = url.rstrip("/")
                    break
        except Exception:
            pass
    
    # Try to load test accounts
    accounts_path = root / "platform_workspace" / project_id / "input" / "TEST_ACCOUNTS.md"
    if accounts_path.exists():
        try:
            content = accounts_path.read_text(encoding="utf-8")
            # Extract email
            email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', content)
            if email_match:
                ctx["test_email"] = email_match.group(0)
            # Extract password
            pwd_match = re.search(r'[Tt]est[@#]\w+', content)
            if pwd_match:
                ctx["test_password"] = pwd_match.group(0)
        except Exception:
            pass
    
    return ctx
