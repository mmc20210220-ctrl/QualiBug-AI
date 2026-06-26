from __future__ import annotations

from typing import Any, Dict, List


class LocalAIEngine:
    """Deterministic local engine for offline enterprise demos.

    The platform treats these outputs exactly like LLM outputs: structured
    assets first, schema validation next, then template-based code generation.
    """

    def analyze_requirement(self, requirement_text: str) -> Dict[str, Any]:
        text = requirement_text.lower()
        risks: List[Dict[str, Any]] = []

        def add(risk_id: str, risk: str, priority: str, reason: str, test_types: list[str]) -> None:
            if not any(item["risk_id"] == risk_id for item in risks):
                risks.append(
                    {
                        "risk_id": risk_id,
                        "risk": risk,
                        "priority": priority,
                        "reason": reason,
                        "recommended_test_type": test_types,
                    }
                )

        if any(k in requirement_text for k in ["登录", "密码", "账号"]) or "login" in text or "password" in text:
            add(
                "RISK_LOGIN_BRUTE_FORCE",
                "登录暴力破解与账号锁定风险",
                "P0",
                "登录失败次数、账号锁定和解锁策略会直接影响账户安全。",
                ["api", "security", "regression"],
            )
        if any(k in requirement_text for k in ["权限", "管理员", "越权"]) or "admin" in text:
            add(
                "RISK_PRIVILEGE_ESCALATION",
                "普通用户越权访问管理员资源",
                "P0",
                "权限边界错误可能导致敏感订单、用户或配置数据泄露。",
                ["api", "security", "permission"],
            )
        if any(k in requirement_text for k in ["优惠券", "折扣", "支付", "金额", "订单"]) or any(k in text for k in ["coupon", "payment", "order"]):
            add(
                "RISK_ORDER_AMOUNT_INCONSISTENCY",
                "订单金额、优惠券和支付链路一致性风险",
                "P0",
                "优惠叠加、重复提交或支付金额不一致会造成真实资损。",
                ["api", "data", "regression"],
            )
        if any(k in requirement_text for k in ["库存", "商品", "购物车"]) or any(k in text for k in ["inventory", "product", "cart"]):
            add(
                "RISK_INVENTORY_OVERSOLD",
                "库存扣减与购物车并发一致性风险",
                "P1",
                "库存不足、重复扣减或超卖会影响交易履约。",
                ["api", "data", "regression"],
            )

        if not risks:
            add(
                "RISK_GENERIC_FUNCTIONAL",
                "核心业务流程回归风险",
                "P1",
                "需求变更后核心流程可能产生回归缺陷。",
                ["api", "ui", "regression"],
            )

        return {"business_rules": self._extract_business_rules(requirement_text), "risks": risks}

    def generate_test_cases(self, analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {
                "case_id": "LOGIN_001",
                "title": "正常用户使用正确密码登录成功",
                "priority": "P0",
                "type": "api",
                "risk_refs": ["RISK_LOGIN_BRUTE_FORCE"],
                "data_profile": "active_normal_user",
                "steps": ["创建 active 普通用户", "使用正确账号密码调用登录接口"],
                "expected": ["登录结果 success 为 true", "登录成功后跳转到 /home"],
                "automation_candidate": True,
            },
            {
                "case_id": "LOGIN_002",
                "title": "密码连续错误 5 次后账号被锁定",
                "priority": "P0",
                "type": "api",
                "risk_refs": ["RISK_LOGIN_BRUTE_FORCE"],
                "data_profile": "active_normal_user",
                "steps": ["创建 active 普通用户", "连续 5 次使用错误密码登录", "再次使用正确密码登录"],
                "expected": ["第 5 次错误登录后返回 ACCOUNT_LOCKED", "账号锁定后正确密码也不能登录"],
                "automation_candidate": True,
            },
            {
                "case_id": "PERMISSION_001",
                "title": "普通用户不能访问管理员页面",
                "priority": "P0",
                "type": "api",
                "risk_refs": ["RISK_PRIVILEGE_ESCALATION"],
                "data_profile": "active_normal_user",
                "steps": ["创建 active 普通用户", "检查该用户是否可以访问 admin_page"],
                "expected": ["访问结果为 false"],
                "automation_candidate": True,
            },
        ]

    def generate_data_profiles(self, test_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "active_normal_user": {
                "entity": "user",
                "role": "user",
                "status": "active",
                "password_state": "valid",
                "create_strategy": "factory",
                "cleanup": "auto",
                "privacy_level": "synthetic_only",
            },
            "locked_normal_user": {
                "entity": "user",
                "role": "user",
                "status": "locked",
                "password_state": "valid",
                "create_strategy": "factory",
                "cleanup": "auto",
                "privacy_level": "synthetic_only",
            },
            "active_admin_user": {
                "entity": "user",
                "role": "admin",
                "status": "active",
                "password_state": "valid",
                "create_strategy": "factory",
                "cleanup": "auto",
                "privacy_level": "synthetic_only",
            },
        }

    def generate_dsl(self, test_cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        dsl: List[Dict[str, Any]] = []
        for case in test_cases:
            if case["case_id"] == "LOGIN_001":
                dsl.append(
                    {
                        "case_id": case["case_id"],
                        "title": case["title"],
                        "type": "api",
                        "priority": case["priority"],
                        "data_profile": "active_normal_user",
                        "actions": [
                            {"action": "create_user", "as": "user", "role": "user", "status": "active"},
                            {"action": "login", "username": "{{user.username}}", "password": "{{user.password}}", "as": "login_result"},
                        ],
                        "assertions": [
                            {"target": "login_result.success", "operator": "equals", "value": True},
                            {"target": "login_result.redirect_url", "operator": "equals", "value": "/home"},
                        ],
                    }
                )
            elif case["case_id"] == "LOGIN_002":
                dsl.append(
                    {
                        "case_id": case["case_id"],
                        "title": case["title"],
                        "type": "api",
                        "priority": case["priority"],
                        "data_profile": "active_normal_user",
                        "actions": [
                            {"action": "create_user", "as": "user", "role": "user", "status": "active"},
                            {"action": "login_wrong_password", "times": 5, "as": "last_wrong_result"},
                            {"action": "login", "username": "{{user.username}}", "password": "{{user.password}}", "as": "login_after_locked"},
                        ],
                        "assertions": [
                            {"target": "last_wrong_result.error_code", "operator": "equals", "value": "ACCOUNT_LOCKED"},
                            {"target": "login_after_locked.success", "operator": "equals", "value": False},
                            {"target": "login_after_locked.error_code", "operator": "equals", "value": "ACCOUNT_LOCKED"},
                        ],
                    }
                )
            elif case["case_id"] == "PERMISSION_001":
                dsl.append(
                    {
                        "case_id": case["case_id"],
                        "title": case["title"],
                        "type": "api",
                        "priority": case["priority"],
                        "data_profile": "active_normal_user",
                        "actions": [
                            {"action": "create_user", "as": "user", "role": "user", "status": "active"},
                            {"action": "check_access", "username": "{{user.username}}", "resource": "admin_page", "as": "access_result"},
                        ],
                        "assertions": [{"target": "access_result", "operator": "equals", "value": False}],
                    }
                )
        return dsl

    @staticmethod
    def _extract_business_rules(requirement_text: str) -> List[str]:
        rules = []
        for line in requirement_text.splitlines():
            line = line.strip()
            if line.startswith(("1.", "2.", "3.", "4.", "5.", "6.", "-", "*")):
                rules.append(line)
        return rules[:20]
