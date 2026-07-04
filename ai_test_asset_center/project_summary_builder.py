"""
ProjectSummaryBuilder — Build structured project summaries for Reasoner engines.

Extracts from PRD/OpenAPI:
- Business rules summary
- State machine summary
- Permission matrix summary
- Money/conservation rules
- Inventory rules
- Risk area summary
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def build_project_summary(
    prd_text: str = "",
    api_spec_text: str = "",
    db_schema_text: str = "",
    bug_history_text: str = "",
    *,
    max_summary_chars: int = 12000,
) -> dict[str, Any]:
    """Build a structured project summary for consumption by Reasoner engines."""

    prd = prd_text or ""
    api = api_spec_text or ""
    db = db_schema_text or ""
    bugs = bug_history_text or ""

    summary = {
        "business_rules": _extract_business_rules(prd),
        "state_machine": _extract_state_machine(prd, api),
        "permission_matrix": _extract_permission_matrix(prd, api),
        "money_rules": _extract_money_rules(prd),
        "inventory_rules": _extract_inventory_rules(prd),
        "risk_areas": _extract_risk_areas(prd, bugs),
        "api_summary": _extract_api_summary(api),
        "db_summary": _extract_db_summary(db),
    }

    # Truncate to max chars
    summary_text = json.dumps(summary, ensure_ascii=False)
    if len(summary_text) > max_summary_chars:
        for key in summary:
            if isinstance(summary[key], str) and len(summary[key]) > 1000:
                summary[key] = summary[key][:1000] + "...[truncated]"

    return summary


def _extract_business_rules(prd: str) -> str:
    """Extract business rules from PRD text."""
    rules = []
    lines = prd.split("\n")

    # Pattern: numbered rules, "必须/不得/禁止/应当/需要" patterns
    rule_patterns = [
        r'^\d+[\.\)、]\s*(.+)',  # Numbered items
        r'(必须|不得|禁止|应当|需要|must|shall|required|禁止).+',
        r'规则\s*[：:]\s*(.+)',
        r'(校验|验证|检查|validate|check|verify).+',
    ]

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for pattern in rule_patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                rule = match.group(1) if match.lastindex else match.group(0)
                if len(rule) > 10:
                    rules.append(rule[:200])
                break

    return "\n".join(rules[:50]) if rules else "(未从PRD中提取到显式业务规则)"


def _extract_state_machine(prd: str, api: str) -> str:
    """Extract state machine information."""
    states = []
    transitions = []

    # Extract status/state values from PRD
    status_patterns = [
        r'(?:状态|status|阶段|phase)[：:\s]*([\w\s,、，/]+)',
        r'(待\w+|已\w+|审核中|审批中|处理中|进行中|已完成|已取消|已关闭|已拒绝)',
    ]
    for pattern in status_patterns:
        for match in re.finditer(pattern, prd, re.IGNORECASE):
            states.append(match.group(1).strip()[:100])

    # Extract transitions from PRD
    trans_pattern = r'(\w+)\s*(?:→|->|→|变成|转为|变更为|流转到)\s*(\w+)'
    for match in re.finditer(trans_pattern, prd):
        transitions.append(f"{match.group(1)}→{match.group(2)}")

    # Extract from OpenAPI enum values
    if api:
        enum_matches = re.findall(r'"enum"\s*:\s*\[([^\]]+)\]', api)
        for enum_str in enum_matches:
            values = re.findall(r'"([^"]+)"', enum_str)
            if values:
                states.extend(values[:10])

    result = []
    if states:
        result.append(f"状态: {', '.join(set(states[:20]))}")
    if transitions:
        result.append(f"流转: {', '.join(set(transitions[:20]))}")

    return "; ".join(result) if result else "(未提取到状态机信息)"


def _extract_permission_matrix(prd: str, api: str) -> str:
    """Extract permission/role information."""
    roles = set()
    permissions = []

    # Extract roles from PRD
    role_patterns = [
        r'(?:角色|role|用户|user|权限|permission)[：:\s]*([\w\s,、，/]+)',
        r'(管理员|普通用户|游客|审核员|运营|财务|客服|买家|卖家|商家)',
        r'(admin|user|viewer|operator|auditor|manager)',
    ]
    for pattern in role_patterns:
        for match in re.finditer(pattern, prd, re.IGNORECASE):
            roles.add(match.group(1).strip()[:50])

    # Extract auth from OpenAPI security schemes
    if api:
        if '"securitySchemes"' in api or '"security"' in api:
            permissions.append("API定义了认证方案")
        if '"bearer"' in api.lower() or '"jwt"' in api.lower():
            permissions.append("JWT/Bearer认证")
        if '"oauth2"' in api.lower():
            permissions.append("OAuth2认证")

    result = []
    if roles:
        result.append(f"角色: {', '.join(list(roles)[:10])}")
    if permissions:
        result.append(f"认证: {', '.join(permissions)}")

    return "; ".join(result) if result else "(未提取到显式权限定义)"


def _extract_money_rules(prd: str) -> str:
    """Extract money/conservation rules."""
    keywords = ["金额", "价格", "费用", "余额", "支付", "退款", "扣款",
                "amount", "price", "fee", "balance", "payment", "refund"]
    return _extract_context_lines(prd, keywords, "资金")


def _extract_inventory_rules(prd: str) -> str:
    """Extract inventory rules."""
    keywords = ["库存", "物料", "数量", "号源", "余量", "配额", "额度",
                "inventory", "stock", "quantity", "capacity", "quota"]
    return _extract_context_lines(prd, keywords, "库存")


def _extract_risk_areas(prd: str, bugs: str) -> str:
    """Extract high-risk areas from PRD and bug history."""
    risk_keywords = [
        "风险", "risk", "安全", "security", "合规", "compliance",
        "资损", "数据泄露", "越权", "并发", "死锁", "超卖",
    ]
    prd_risks = _extract_context_lines(prd, risk_keywords, "风险")

    # Extract bug patterns from history
    bug_patterns = []
    if bugs:
        for line in bugs.split("\n")[:50]:
            if any(kw in line.lower() for kw in ["bug", "defect", "缺陷", "漏洞"]):
                bug_patterns.append(line.strip()[:200])

    result = prd_risks
    if bug_patterns:
        result += f"\n历史Bug: {len(bug_patterns)}条相关记录"

    return result if result.strip() else "(未提取到显式风险区域)"


def _extract_api_summary(api: str) -> str:
    """Extract API summary."""
    if not api:
        return "(无API文档)"
    try:
        spec = json.loads(api)
        paths = spec.get("paths", {})
        endpoints = []
        for path, methods in paths.items():
            for method in methods:
                if method.upper() in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                    endpoints.append(f"{method.upper()} {path}")
        return f"{len(endpoints)}个端点: {', '.join(endpoints[:30])}"
    except Exception:
        # Try markdown extraction
        count = len(re.findall(r'\|\s*(GET|POST|PUT|DELETE)\s*\|', api, re.IGNORECASE))
        return f"约{count}个API端点(Markdown格式)"


def _extract_db_summary(db: str) -> str:
    """Extract database summary."""
    if not db:
        return "(无数据库Schema文档)"

    tables = re.findall(r'(?:CREATE\s+TABLE|表名|table)\s+[`"\']?(\w+)[`"\']?', db, re.IGNORECASE)
    if tables:
        return f"表: {', '.join(tables[:20])}"

    return f"数据库文档 ({len(db)}字符)"


def _extract_context_lines(text: str, keywords: list[str], domain: str) -> str:
    """Extract lines containing domain keywords."""
    lines = []
    for line in text.split("\n"):
        line_lower = line.lower().strip()
        if any(kw.lower() in line_lower for kw in keywords):
            if len(line) > 10:
                lines.append(line.strip()[:200])
    if lines:
        return f"{domain}相关({len(lines)}处): " + "; ".join(lines[:10])
    return f"(未提取到{domain}相关规则)"
