#!/usr/bin/env python3
"""PRD-to-Probe Adapter. Bridges business_rules.py -> executable HTTP probes.

Takes PRD text + API routes, produces executable probes.
Fully generic: every rule is tested against every route, response classifier
determines if the probe hit a real business rule violation.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExecutableProbe:
    rule_id: str
    rule_description: str
    rule_type: str
    method: str
    path: str
    expected: str
    actual_trigger: str
    payload: dict = field(default_factory=dict)
    severity: str = "P0"
    repro_steps: list[str] = field(default_factory=list)
    needs_db_evidence: bool = False


def _extract_validated_property(text: str) -> str:
    if any(w in text for w in ["qty", "quantity", "负数", "不能为负"]):
        return "quantity"
    if any(w in text for w in ["amount", "price", "金额", "价格"]):
        return "amount"
    if any(w in text for w in ["password", "密码", "弱密码"]):
        return "password"
    if any(w in text for w in ["email", "邮箱"]):
        return "email"
    return "generic"


def _build_payload(path: str, overrides: dict) -> dict:
    p = {}
    if "login" in path or "register" in path:
        p = {"email": "test@e.com", "password": "Test@123456"}
    elif "order" in path:
        p = {"items": [{"sku": "SKU-PHONE-001", "qty": 1}]}
    elif "cart" in path:
        p = {"sku": "SKU-PHONE-001", "qty": 1}
    elif "pay" in path:
        p = {"orderId": "dummy", "amount": 0}
    elif "refund" in path:
        p = {"orderId": "dummy", "amount": 0, "reason": "test"}
    elif "product" in path:
        p = {"title": "test", "price": 0, "category": "test"}
    elif "coupon" in path:
        p = {"code": "TEST", "items": [], "totalAmount": 0}
    p.update(overrides)
    return p


def generate_probes_from_prd(prd_text: str, api_routes: list[dict]) -> list[ExecutableProbe]:
    """Generate executable probes from PRD rules + all API routes.

    No entity matching, no mapping tables. Every rule generates probes
    against every POST/PATCH route. The response classifier (caller side)
    determines if the probe actually hit a valid endpoint.
    """
    from ai_test_asset_center.analyzers.business_rules import (
        BusinessRulesAnalyzer, RuleType)

    analyzer = BusinessRulesAnalyzer()
    rules = analyzer.extract_rules_from_prd(prd_text)

    # Only use mutation routes (POST/PATCH/PUT/DELETE) for rule testing
    mutation_routes = [r for r in api_routes
                       if r.get("method", "GET").upper() in ("POST", "PATCH", "PUT", "DELETE")]

    probes: list[ExecutableProbe] = []

    for rule in rules:
        rt = rule.rule_type

        for route in mutation_routes:
            m = route.get("method", "POST").upper()
            pth = route.get("path", "/")

            if rt == RuleType.VALIDATION:
                prop = _extract_validated_property(rule.description)
                if prop == "quantity":
                    for v, lbl in [(-1, "qty=-1"), (0, "qty=0")]:
                        probes.append(ExecutableProbe(
                            rule_id=rule.id, rule_description=rule.description,
                            rule_type="VALIDATION", method=m, path=pth,
                            expected="应拒绝", actual_trigger=f"发送 {lbl}",
                            payload=_build_payload(pth, {"qty": v}),
                            severity=rule.priority.value))
                elif prop == "amount":
                    probes.append(ExecutableProbe(
                        rule_id=rule.id, rule_description=rule.description,
                        rule_type="VALIDATION", method=m, path=pth,
                        expected="应拒绝", actual_trigger="发送负数金额",
                        payload=_build_payload(pth, {"amount": -1, "price": -1}),
                        severity=rule.priority.value))
                elif prop == "password":
                    probes.append(ExecutableProbe(
                        rule_id=rule.id, rule_description=rule.description,
                        rule_type="VALIDATION", method=m, path=pth,
                        expected="应拒绝弱密码", actual_trigger="发送 password='1'",
                        payload=_build_payload(pth, {"password": "1"}),
                        severity=rule.priority.value))
                else:
                    probes.append(ExecutableProbe(
                        rule_id=rule.id, rule_description=rule.description,
                        rule_type="VALIDATION", method=m, path=pth,
                        expected="应拒绝", actual_trigger="发送边界值",
                        payload=_build_payload(pth, {}),
                        severity=rule.priority.value))

            elif rt == RuleType.STATE_MACHINE:
                probes.append(ExecutableProbe(
                    rule_id=rule.id, rule_description=rule.description,
                    rule_type="STATE_MACHINE", method=m, path=pth,
                    expected="应拒绝", actual_trigger="状态违规操作",
                    payload={}, severity=rule.priority.value,
                    needs_db_evidence=True))

            elif rt == RuleType.CONSERVATION:
                probes.append(ExecutableProbe(
                    rule_id=rule.id, rule_description=rule.description,
                    rule_type="CONSERVATION", method=m, path=pth,
                    expected="数据应守恒", actual_trigger="双重操作",
                    payload={}, severity=rule.priority.value,
                    needs_db_evidence=True))

    return probes


def bridge_prd_to_pipeline(prd_path: str, api_routes: list[dict]) -> list[ExecutableProbe]:
    with open(prd_path, encoding='utf-8') as f:
        return generate_probes_from_prd(f.read(), api_routes)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from ai_test_asset_center.multi_service_discovery import extract_routes

    prd = str(Path(__file__).parent.parent.parent / "benchmark_mall" / "docs" / "PRD.md")
    routes = extract_routes(str(Path(__file__).parent.parent.parent / "benchmark_mall" / "docs"))
    probes = bridge_prd_to_pipeline(prd, routes)
    print(f"PRD -> {len(probes)} probes (all routes, no matching)")
    for p in probes[:8]:
        print(f"  [{p.rule_type:15s}] {p.method} {p.path:30s} | {p.actual_trigger[:40]}")
