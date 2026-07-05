"""
V12 Oracle Engine — 插件化Oracle Framework，首期内置6层26类Oracle，可按行业扩展。

L1 基础协议Oracle:    HTTP状态/Schema校验/字段类型/必填字段/错误码规范
L2 数据一致性Oracle:  DataIntegrity/Consistency/Transaction/CacheConsistency
L3 业务规则Oracle:    Money/Inventory/State/Workflow/Quota/Temporal
L4 安全权限Oracle:    Permission/Privacy/TenantIsolation/AuthSession
L5 运行时行为Oracle:  Idempotency/Concurrency/Performance/Recovery
L6 商业证据Oracle:    Audit/Notification/Evidence

Architecture: OracleRegistry (extensible plugin) → auto-detect from PRD keywords
→ evaluate each scenario against ALL relevant oracles across ALL layers.
"""

from __future__ import annotations

import json, re, time, uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OracleResult:
    passed: bool
    oracle_name: str = ""
    layer: str = ""
    violated_rule: str = ""
    expected: str = ""
    actual: str = ""
    severity: str = "P1"
    confidence: float = 0.5
    explanation: str = ""

    def to_dict(self) -> dict:
        return {
            "passed": self.passed, "layer": self.layer,
            "oracle_name": self.oracle_name, "oracle": self.oracle_name,
            "violated_rule": self.violated_rule, "expected": self.expected,
            "actual": self.actual, "severity": self.severity,
            "confidence": self.confidence, "explanation": self.explanation,
        }


class BaseOracle(ABC):
    name: str = ""
    layer: str = ""
    trigger_keywords: list[str] = []
    priority: int = 50

    @abstractmethod
    def evaluate(self, scenario: dict, trace: dict, snapshots: Any = None) -> OracleResult: ...


# ═══════════════════════════════════════════════════
# L1 — 基础协议Oracle (5 types)
# ═══════════════════════════════════════════════════

class HttpStatusOracle(BaseOracle):
    name = "HttpStatusOracle"; layer = "L1"
    trigger_keywords = []; priority = 10  # Always run

    def evaluate(self, scenario, trace, snapshots=None):
        for s in trace.get("steps", []):
            status = s.get("response", {}).get("status_code", 0) if isinstance(s.get("response"), dict) else s.get("status", 0)
            if status >= 500:
                return OracleResult(False, "HttpStatusOracle", "L1", "server_5xx",
                    "服务应正常响应", f"HTTP {status}", "P0", 0.95)
            if status == 200:
                body = s.get("response", {}).get("body", {}) if isinstance(s.get("response"), dict) else {}
                if isinstance(body, dict) and body.get("ok") is False:
                    return OracleResult(False, "HttpStatusOracle", "L1", "200_with_error",
                        "200不应携带业务错误", str(body)[:100], "P1", 0.85)
            if status == 204 and s.get("method") in ("POST", "PUT") and s.get("expected_status") == 201:
                return OracleResult(False, "HttpStatusOracle", "L1", "wrong_create_status",
                    "创建成功应返回201", "HTTP 204", "P1", 0.80)
        return OracleResult(True, "HttpStatusOracle", "L1")


class SchemaOracle(BaseOracle):
    name = "SchemaOracle"; layer = "L1"
    trigger_keywords = []; priority = 10

    def evaluate(self, scenario, trace, snapshots=None):
        for s in trace.get("steps", []):
            body = s.get("response", {}).get("body", {}) if isinstance(s.get("response"), dict) else {}
            if isinstance(body, dict):
                schema_hints = scenario.get("response_schema", scenario.get("expected_fields", []))
                if schema_hints:
                    missing = [f for f in schema_hints if f not in body]
                    if missing:
                        return OracleResult(False, "SchemaOracle", "L1", "schema_mismatch",
                            f"响应应包含字段{missing}", f"缺失{len(missing)}个字段", "P1", 0.82)
        return OracleResult(True, "SchemaOracle", "L1")


class FieldTypeOracle(BaseOracle):
    name = "FieldTypeOracle"; layer = "L1"
    trigger_keywords = []; priority = 10

    def evaluate(self, scenario, trace, snapshots=None):
        for s in trace.get("steps", []):
            body = s.get("response", {}).get("body", {}) if isinstance(s.get("response"), dict) else {}
            if isinstance(body, dict):
                for k, v in body.items():
                    if k.endswith("_at") or k.endswith("Date") or "timestamp" in k.lower():
                        if isinstance(v, (int, float)):
                            return OracleResult(False, "FieldTypeOracle", "L1", "timestamp_as_number",
                                f"{k}应为ISO时间字符串", f"{k}={v}", "P2", 0.70)
                    if k.startswith("is_") or k.startswith("has_"):
                        if not isinstance(v, bool):
                            return OracleResult(False, "FieldTypeOracle", "L1", "bool_as_non_bool",
                                f"{k}应为布尔值", f"{k}={type(v).__name__}", "P2", 0.70)
        return OracleResult(True, "FieldTypeOracle", "L1")


class RequiredFieldOracle(BaseOracle):
    name = "RequiredFieldOracle"; layer = "L1"
    trigger_keywords = []; priority = 10

    def evaluate(self, scenario, trace, snapshots=None):
        for s in trace.get("steps", []):
            body = s.get("response", {}).get("body", {}) if isinstance(s.get("response"), dict) else {}
            if isinstance(body, dict) and body:
                for field in ("id", "status", "created_at"):
                    if body.get(field) is None and s.get("expected_status") == 200:
                        return OracleResult(False, "RequiredFieldOracle", "L1", "null_required",
                            f"必填字段{field}不应为null", f"{field}=null", "P1", 0.80)
        return OracleResult(True, "RequiredFieldOracle", "L1")


class ErrorCodeOracle(BaseOracle):
    name = "ErrorCodeOracle"; layer = "L1"
    trigger_keywords = []; priority = 10

    def evaluate(self, scenario, trace, snapshots=None):
        for s in trace.get("steps", []):
            body = s.get("response", {}).get("body", {}) if isinstance(s.get("response"), dict) else {}
            status = s.get("response", {}).get("status_code", 0) if isinstance(s.get("response"), dict) else s.get("status", 0)
            if status >= 400 and isinstance(body, dict):
                if not body.get("error") and not body.get("code") and not body.get("message"):
                    return OracleResult(False, "ErrorCodeOracle", "L1", "no_error_code",
                        "错误响应应包含错误码/信息", f"HTTP {status} 无错误字段", "P2", 0.75)
                if body.get("error") == "Internal Server Error" or "traceback" in str(body).lower():
                    return OracleResult(False, "ErrorCodeOracle", "L1", "raw_error_leak",
                        "不应泄露原始异常", str(body)[:100], "P0", 0.90)
        return OracleResult(True, "ErrorCodeOracle", "L1")


# ═══════════════════════════════════════════════════
# L2 — 数据一致性Oracle (4 types)
# ═══════════════════════════════════════════════════

class DataIntegrityOracle(BaseOracle):
    name = "DataIntegrityOracle"; layer = "L2"
    trigger_keywords = ["字段约束", "关联关系", "脏数据", "integrity", "constraint", "foreign key"]

    def evaluate(self, scenario, trace, snapshots=None):
        for s in trace.get("steps", []):
            body = s.get("response", {}).get("body", {}) if isinstance(s.get("response"), dict) else {}
            if isinstance(body, dict):
                for k, v in body.items():
                    if isinstance(v, str) and len(v) > 100000:
                        return OracleResult(False, "DataIntegrityOracle", "L2", "field_too_long",
                            f"{k}不应超长", f"len({k})={len(v)}", "P1", 0.75)
                    if isinstance(v, str) and v.lower() == "null":
                        return OracleResult(False, "DataIntegrityOracle", "L2", "string_null",
                            f"{k}不应为字符串'null'", f"{k}='null'", "P2", 0.70)
        return OracleResult(True, "DataIntegrityOracle", "L2")


class ConsistencyOracle(BaseOracle):
    name = "ConsistencyOracle"; layer = "L2"
    trigger_keywords = ["一致性", "对账", "consistency", "reconciliation"]

    def evaluate(self, scenario, trace, snapshots=None):
        if snapshots and hasattr(snapshots, "diff"):
            diff = getattr(snapshots, "diff", {})
            if len(diff) > 5:
                return OracleResult(False, "ConsistencyOracle", "L2", "excessive_changes",
                    "单次操作不应引起大量变化", f"{len(diff)}个字段", "P1", 0.70)
        steps = trace.get("steps", [])
        gets = [s for s in steps if s.get("method") == "GET"]
        for i in range(len(gets)-1):
            b1 = gets[i].get("response", {}).get("body", {}) if isinstance(gets[i].get("response"), dict) else {}
            b2 = gets[i+1].get("response", {}).get("body", {}) if isinstance(gets[i+1].get("response"), dict) else {}
            if isinstance(b1, dict) and isinstance(b2, dict):
                r1 = b1.get("records", b1.get("data", []))
                r2 = b2.get("records", b2.get("data", []))
                if isinstance(r1, list) and isinstance(r2, list):
                    common = set(str(x.get("id","")) for x in r1 if isinstance(x, dict) and x.get("id") is not None) & set(str(x.get("id","")) for x in r2 if isinstance(x, dict) and x.get("id") is not None)
                    if common:
                        return OracleResult(False, "ConsistencyOracle", "L2", "pagination_overlap",
                            f"分页数据重复: {common}", "", "P1", 0.65)
        return OracleResult(True, "ConsistencyOracle", "L2")


class TransactionOracle(BaseOracle):
    name = "TransactionOracle"; layer = "L2"
    trigger_keywords = ["事务", "回滚", "部分成功", "transaction", "rollback", "atomic"]

    def evaluate(self, scenario, trace, snapshots=None):
        errors = trace.get("errors", [])
        steps = trace.get("steps", [])
        if errors and any(s.get("response", {}).get("status_code", 0) == 200 for s in steps if isinstance(s.get("response"), dict)):
            return OracleResult(False, "TransactionOracle", "L2", "partial_success",
                "事务应全成功或全回滚", f"{len(errors)}错误但部分成功", "P0", 0.90)
        if snapshots and hasattr(snapshots, "diff") and errors:
            return OracleResult(False, "TransactionOracle", "L2", "dirty_data",
                "失败应回滚数据", str(getattr(snapshots, "diff", {}))[:100], "P0", 0.88)
        return OracleResult(True, "TransactionOracle", "L2")


class CacheConsistencyOracle(BaseOracle):
    name = "CacheConsistencyOracle"; layer = "L2"
    trigger_keywords = ["缓存", "cache", "redis", "memcache"]

    def evaluate(self, scenario, trace, snapshots=None):
        steps = trace.get("steps", [])
        writes = [s for s in steps if s.get("method") in ("POST", "PUT", "DELETE", "PATCH")]
        if not writes:
            return OracleResult(True, "CacheConsistencyOracle", "L2")
        # Find reads after the last write to the same entity
        write_paths = {w.get("path", "") for w in writes}
        reads_after = [s for s in steps if s.get("method") == "GET"]
        if not reads_after:
            return OracleResult(True, "CacheConsistencyOracle", "L2")
        # Check if any read after a write returns stale data (not reflecting the write)
        for w in writes:
            w_entity = w.get("path", "").split("/")[2] if len(w.get("path", "").split("/")) > 2 else ""
            w_body = w.get("response", {}).get("body", {}) if isinstance(w.get("response"), dict) else {}
            w_data = w_body.get("data", w_body)
            for r in reads_after:
                if w_entity and w_entity not in r.get("path", ""):
                    continue
                r_body = r.get("response", {}).get("body", {}) if isinstance(r.get("response"), dict) else {}
                r_data = r_body.get("data", r_body)
                # Compare: if write changed something but read doesn't reflect it → stale cache
                if isinstance(w_data, dict) and isinstance(r_data, dict) and w_data != r_data:
                    # Check if the write contained new data not in read
                    for k in w_data:
                        if k in r_data and w_data[k] != r_data[k]:
                            return OracleResult(False, "CacheConsistencyOracle", "L2",
                                "stale_cache",
                                "写入后读取应返回最新数据",
                                f"字段{k}: 写入={w_data[k]}, 读取={r_data.get(k)}",
                                "P1", 0.70)
        return OracleResult(True, "CacheConsistencyOracle", "L2")


# ═══════════════════════════════════════════════════
# L3 — 业务规则Oracle (6 types)
# ═══════════════════════════════════════════════════

class MoneyOracle(BaseOracle):
    name = "MoneyOracle"; layer = "L3"
    trigger_keywords = ["支付", "退款", "金额", "价格", "费用", "余额", "结算", "payment", "refund", "amount"]

    def evaluate(self, scenario, trace, snapshots=None):
        for s in trace.get("steps", []):
            body = s.get("response", {}).get("body", {}) if isinstance(s.get("response"), dict) else {}
            if isinstance(body, dict):
                for field in ("amount", "total_price", "total", "price", "balance", "fee"):
                    val = body.get(field)
                    if val is not None:
                        try:
                            if float(val) < 0:
                                return OracleResult(False, "MoneyOracle", "L3", "negative_amount",
                                    f"{field} >= 0", f"{field} = {val}", "P0", 0.95, f"负金额: {field}={val}")
                        except (ValueError, TypeError, AttributeError):
                            pass  # non-numeric value — can't compare, skip
        refunds = [s for s in trace.get("steps", []) if "refund" in str(s.get("action","")).lower()]
        if len(refunds) >= 2 and refunds[-1].get("response", {}).get("status_code", 0) == 200:
            return OracleResult(False, "MoneyOracle", "L3", "double_refund",
                "重复退款应被拒绝", f"第{len(refunds)}次退款成功", "P0", 0.92)
        return OracleResult(True, "MoneyOracle", "L3")


class InventoryOracle(BaseOracle):
    name = "InventoryOracle"; layer = "L3"
    trigger_keywords = ["库存", "超卖", "扣减", "物料", "数量", "stock", "inventory"]

    def evaluate(self, scenario, trace, snapshots=None):
        for s in trace.get("steps", []):
            body = s.get("response", {}).get("body", {}) if isinstance(s.get("response"), dict) else {}
            if isinstance(body, dict):
                for field in ("stock", "quantity", "inventory", "remaining"):
                    val = body.get(field)
                    if val is not None:
                        try:
                            if float(val) < 0:
                                return OracleResult(False, "InventoryOracle", "L3", "negative_stock",
                                    f"{field} >= 0", f"{field} = {val}", "P0", 0.95, f"负库存: {field}={val}")
                        except (ValueError, TypeError, AttributeError):
                            pass  # non-numeric value — skip
        if snapshots and hasattr(snapshots, "diff"):
            for field, change in getattr(snapshots, "diff", {}).items():
                if "stock" in field.lower():
                    if isinstance(change.get("to", 0), (int, float)) and float(change["to"]) < 0:
                        return OracleResult(False, "InventoryOracle", "L3", "stock_went_negative",
                            "库存不应为负", f"{field}: {change['from']}→{change['to']}", "P0", 0.93)
        return OracleResult(True, "InventoryOracle", "L3")


class StateOracle(BaseOracle):
    name = "StateOracle"; layer = "L3"
    trigger_keywords = ["状态", "流转", "status", "state", "transition"]

    def evaluate(self, scenario, trace, snapshots=None):
        is_forbidden = scenario.get("flags", {}).get("forbidden", False)
        steps = trace.get("steps", [])
        if not steps: return OracleResult(True, "StateOracle", "L3")

        # Extract status fields from step responses to check transitions
        observed_statuses: list[str] = []
        for s in steps:
            body = s.get("response", {}).get("body", {}) if isinstance(s.get("response"), dict) else {}
            if isinstance(body, dict):
                for f in ("status", "state", "order_status", "task_status"):
                    val = body.get(f)
                    if val and isinstance(val, str):
                        observed_statuses.append(val)
                        break

        # Check forbidden transitions
        if is_forbidden:
            last_resp = steps[-1].get("response", {}) if isinstance(steps[-1], dict) else {}
            status = last_resp.get("status_code", 0) if isinstance(last_resp, dict) else 0
            if status == 200:
                return OracleResult(False, "StateOracle", "L3", "forbidden_transition",
                    "禁止的状态转换应被阻止", "HTTP 200", "P0", 0.93,
                    f"禁止路径: {scenario.get('title','')}")

        # Check: if there are multiple statuses, did any go backwards?
        # Simple heuristic: "completed"/"cancelled" → "pending" = regression
        terminal_states = {"completed", "cancelled", "closed", "archived", "deleted", "refunded"}
        for i in range(len(observed_statuses) - 1):
            if observed_statuses[i].lower() in terminal_states:
                return OracleResult(False, "StateOracle", "L3", "terminal_to_nonterminal",
                    f"终态 {observed_statuses[i]} 不应转换为 {observed_statuses[i+1]}",
                    f"{observed_statuses[i]} → {observed_statuses[i+1]}", "P0", 0.88)

        return OracleResult(True, "StateOracle", "L3")


class WorkflowOracle(BaseOracle):
    name = "WorkflowOracle"; layer = "L3"
    trigger_keywords = ["审批", "工单", "流程", "workflow", "approval", "ticket"]

    def evaluate(self, scenario, trace, snapshots=None):
        for s in trace.get("steps", []):
            action = str(s.get("action", ""))
            status = s.get("response", {}).get("status_code", 0) if isinstance(s.get("response"), dict) else s.get("status", 0)
            if any(kw in action.lower() for kw in ("approve", "审批", "通过")) and status == 200:
                if s.get("expected_status") in (401, 403):
                    return OracleResult(False, "WorkflowOracle", "L3", "approval_bypass",
                        "审批不应被绕过", f"HTTP {status}", "P0", 0.90)
        return OracleResult(True, "WorkflowOracle", "L3")


class QuotaOracle(BaseOracle):
    name = "QuotaOracle"; layer = "L3"
    trigger_keywords = ["限额", "次数", "频率", "配额", "上限", "quota", "limit", "capacity"]

    def evaluate(self, scenario, trace, snapshots=None):
        for s in trace.get("steps", []):
            body = s.get("response", {}).get("body", {}) if isinstance(s.get("response"), dict) else {}
            if isinstance(body, dict):
                for field in ("remaining", "available", "used", "quota", "limit"):
                    val = body.get(field)
                    if val is not None:
                        try:
                            if float(val) < 0:
                                return OracleResult(False, "QuotaOracle", "L3", "quota_negative",
                                    f"{field} >= 0", f"{field} = {val}", "P0", 0.90, f"配额异常: {field}={val}")
                        except (ValueError, TypeError, AttributeError):
                            pass  # non-numeric value — skip
        return OracleResult(True, "QuotaOracle", "L3")


class TemporalOracle(BaseOracle):
    name = "TemporalOracle"; layer = "L3"
    trigger_keywords = ["过期", "生效", "截止", "有效期", "expire", "deadline", "effective"]

    def evaluate(self, scenario, trace, snapshots=None):
        import datetime
        for s in trace.get("steps", []):
            body = s.get("response", {}).get("body", {}) if isinstance(s.get("response"), dict) else {}
            if isinstance(body, dict):
                for field in ("expired_at", "valid_until", "deadline", "effective_from"):
                    val = body.get(field)
                    if val and str(val):
                        try:
                            dt = datetime.datetime.fromisoformat(str(val).replace("Z",""))
                            if dt < datetime.datetime.now():
                                return OracleResult(False, "TemporalOracle", "L3", "expired_entity",
                                    "过期实体不应返回", f"{field}={val}", "P1", 0.85)
                        except (ValueError, TypeError, AttributeError):
                            pass  # non-numeric value — skip
        return OracleResult(True, "TemporalOracle", "L3")


# ═══════════════════════════════════════════════════
# L4 — 安全权限Oracle (4 types)
# ═══════════════════════════════════════════════════

class PermissionOracle(BaseOracle):
    name = "PermissionOracle"; layer = "L4"
    trigger_keywords = ["权限", "越权", "角色", "permission", "role", "auth", "ACL"]

    def evaluate(self, scenario, trace, snapshots=None):
        for s in trace.get("steps", []):
            resp = s.get("response", {}) if isinstance(s, dict) else {}
            status = resp.get("status_code", 0) if isinstance(resp, dict) else s.get("status", 0)
            expected = s.get("expected_status", 200)
            if expected in (401, 403) and status == 200:
                return OracleResult(False, "PermissionOracle", "L4", "unauthorized_access",
                    f"应返回{expected}", f"HTTP {status}", "P0", 0.95, f"权限绕过: {s.get('action','?')}")
        return OracleResult(True, "PermissionOracle", "L4")


class PrivacyOracle(BaseOracle):
    name = "PrivacyOracle"; layer = "L4"
    trigger_keywords = ["隐私", "敏感", "泄露", "GDPR", "PII", "脱敏", "privacy", "身份证"]

    def evaluate(self, scenario, trace, snapshots=None):
        # Sensitive field KEY names that should never appear in API responses
        sensitive_keys = {"password", "secret", "token", "api_key", "private_key", "access_key"}
        # Sensitive patterns to detect in field VALUES (PII leaks)
        pii_patterns = [
            (r'1[3-9]\d{9}', "手机号"),
            (r'\d{6}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]', "身份证号"),
            (r'\d{16,19}', "银行卡号"),
        ]
        import re as _re
        for s in trace.get("steps", []):
            body = s.get("response", {}).get("body", {}) if isinstance(s.get("response"), dict) else {}
            if not isinstance(body, dict):
                continue
            # Check: does the response contain sensitive FIELD KEYS (not hash variants)?
            for k in body:
                lower_k = k.lower()
                for sk in sensitive_keys:
                    if sk == lower_k and "hash" not in lower_k and "hashed" not in lower_k:
                        return OracleResult(False, "PrivacyOracle", "L4", "sensitive_key_leak",
                            f"响应包含敏感字段'{k}'", "检测到敏感字段", "P0", 0.92)

            # Check: do string VALUES in the response body look like PII?
            body_str = json.dumps(body, ensure_ascii=False)
            for pattern, label in pii_patterns:
                if _re.search(pattern, body_str):
                    return OracleResult(False, "PrivacyOracle", "L4", "pii_leak",
                        f"响应疑似包含{label}", "检测到疑似个人隐私数据", "P0", 0.88)

        return OracleResult(True, "PrivacyOracle", "L4")


class TenantIsolationOracle(BaseOracle):
    name = "TenantIsolationOracle"; layer = "L4"
    trigger_keywords = ["多租户", "租户", "隔离", "tenant", "isolation", "SaaS"]

    def evaluate(self, scenario, trace, snapshots=None):
        steps = trace.get("steps", [])
        for s in steps:
            body = s.get("response", {}).get("body", {}) if isinstance(s.get("response"), dict) else {}
            if isinstance(body, dict):
                if body.get("tenant_id") and scenario.get("actor_tenant") and body["tenant_id"] != scenario["actor_tenant"]:
                    return OracleResult(False, "TenantIsolationOracle", "L4", "cross_tenant_access",
                        "不应访问其他租户数据", f"tenant={body['tenant_id']}", "P0", 0.95)
        return OracleResult(True, "TenantIsolationOracle", "L4")


class AuthSessionOracle(BaseOracle):
    name = "AuthSessionOracle"; layer = "L4"
    trigger_keywords = ["登录", "会话", "token", "session", "JWT", "过期", "refresh"]

    def evaluate(self, scenario, trace, snapshots=None):
        steps = trace.get("steps", [])
        for s in steps:
            resp = s.get("response", {}) if isinstance(s, dict) else {}
            body = resp.get("body", {}) if isinstance(resp, dict) else {}
            status = resp.get("status_code", 0) if isinstance(resp, dict) else s.get("status", 0)
            if isinstance(body, dict) and status == 200:
                # Token should not be in response body (except login)
                if body.get("token") and s.get("action") not in ("login",):
                    return OracleResult(False, "AuthSessionOracle", "L4", "token_leak",
                        "Token不应在非登录响应中", "", "P0", 0.90)
                # Session should have expiry
                if body.get("token") and not body.get("expires_in") and not body.get("expires_at"):
                    return OracleResult(False, "AuthSessionOracle", "L4", "no_session_expiry",
                        "会话应有过期时间", "", "P1", 0.80)
        return OracleResult(True, "AuthSessionOracle", "L4")


# ═══════════════════════════════════════════════════
# L5 — 运行时行为Oracle (4 types)
# ═══════════════════════════════════════════════════

class IdempotencyOracle(BaseOracle):
    name = "IdempotencyOracle"; layer = "L5"
    trigger_keywords = ["幂等", "重复提交", "idempotent", "duplicate"]

    def evaluate(self, scenario, trace, snapshots=None):
        steps = trace.get("steps", [])
        # Group by (method, path) to detect actual same-endpoint duplicates
        step_groups: dict[tuple[str, str], list[dict]] = {}
        for s in steps:
            method = str(s.get("method") or s.get("action", "GET")).upper()
            path = str(s.get("path", "")).rstrip("/")
            step_groups.setdefault((method, path), []).append(s)
        for (method, path), group in step_groups.items():
            if method not in ("POST", "PUT", "PATCH") or len(group) < 2:
                continue
            statuses = set()
            for s in group:
                resp = s.get("response", {}) if isinstance(s.get("response"), dict) else {}
                statuses.add(resp.get("status_code", s.get("status", 0)))
            if statuses <= {200, 201}:  # all succeeded — non-idempotent
                return OracleResult(False, "IdempotencyOracle", "L5", "non_idempotent",
                    f"重复{method} {path} 应返回幂等响应(409/相同结果)",
                    f"{len(group)}次请求均返回成功", "P0", 0.80)
        return OracleResult(True, "IdempotencyOracle", "L5")


class ConcurrencyOracle(BaseOracle):
    name = "ConcurrencyOracle"; layer = "L5"
    trigger_keywords = ["并发", "竞争", "race", "concurrency", "lock"]

    def evaluate(self, scenario, trace, snapshots=None):
        if scenario.get("flags", {}).get("concurrent"):
            successes = [s for s in trace.get("steps", []) if s.get("response", {}).get("status_code", 0) == 200]
            if len(successes) >= 2:
                return OracleResult(False, "ConcurrencyOracle", "L5", "race_condition",
                    "并发操作应互斥", f"{len(successes)}次成功", "P0", 0.85)
        return OracleResult(True, "ConcurrencyOracle", "L5")


class PerformanceOracle(BaseOracle):
    name = "PerformanceOracle"; layer = "L5"
    trigger_keywords = ["响应时间", "慢接口", "N+1", "性能", "performance", "timeout"]

    def evaluate(self, scenario, trace, snapshots=None):
        for s in trace.get("steps", []):
            elapsed = s.get("elapsed_ms", 0)
            if isinstance(elapsed, (int, float)) and elapsed > 5000:
                return OracleResult(False, "PerformanceOracle", "L5", "slow_response",
                    "响应应<5s", f"{elapsed:.0f}ms", "P2", 0.70)
        duration = trace.get("duration_ms", 0)
        if duration > 30000:
            return OracleResult(False, "PerformanceOracle", "L5", "scenario_timeout",
                "场景应<30s", f"{duration}ms", "P2", 0.65)
        gets = [s.get("path","") for s in trace.get("steps", []) if s.get("method") == "GET"]
        for path in set(gets):
            if gets.count(path) > 10:
                return OracleResult(False, "PerformanceOracle", "L5", "n_plus_one",
                    f"疑似N+1: {path} ×{gets.count(path)}", "", "P1", 0.78)
        return OracleResult(True, "PerformanceOracle", "L5")


class RecoveryOracle(BaseOracle):
    name = "RecoveryOracle"; layer = "L5"
    trigger_keywords = ["重试", "恢复", "补偿", "recovery", "retry", "fallback"]

    def evaluate(self, scenario, trace, snapshots=None):
        errors = trace.get("errors", [])
        if errors:
            steps = trace.get("steps", [])
            # Check for actual retry behavior: same path/method called multiple times after error
            error_indices = [i for i, s in enumerate(steps) if any(
                str(e) in str(s.get("response", {}).get("body", "")) for e in errors)]
            has_retry = False
            for ei in error_indices:
                after = steps[ei+1:] if ei + 1 < len(steps) else []
                for a in after:
                    if a.get("method") == steps[ei].get("method") and a.get("path") == steps[ei].get("path"):
                        has_retry = True
                        break
            if not has_retry:
                # Also check if scenario explicitly declares recovery steps
                recovery_steps = [s for s in steps if any(
                    kw in str(s.get("action", "")).lower()
                    for kw in ("recover", "fallback", "compensate"))]
                if not recovery_steps:
                    return OracleResult(False, "RecoveryOracle", "L5", "no_recovery",
                        "异常后应尝试恢复或降级", str(errors[:1])[:80], "P1", 0.72)
        return OracleResult(True, "RecoveryOracle", "L5")


# ═══════════════════════════════════════════════════
# L6 — 商业证据Oracle (3 types)
# ═══════════════════════════════════════════════════

class AuditOracle(BaseOracle):
    name = "AuditOracle"; layer = "L6"
    trigger_keywords = ["审计", "日志", "留痕", "audit", "log", "trace"]

    def evaluate(self, scenario, trace, snapshots=None):
        steps = trace.get("steps", [])
        mutations = [s for s in steps
                     if s.get("action") in ("create", "update", "delete", "cancel", "refund")
                     and s.get("response", {}).get("status_code", 0) == 200]

        if not mutations:
            return OracleResult(True, "AuditOracle", "L6",
                explanation="无变更操作，无需审计检查")

        # Check 1: Do mutation responses carry trace/correlation IDs?
        missing_trace = []
        for m in mutations:
            resp = m.get("response", {}) if isinstance(m.get("response"), dict) else {}
            body = resp.get("body", {}) if isinstance(resp.get("body"), dict) else {}
            has_trace = body.get("trace_id") or body.get("request_id") or body.get("correlation_id") or body.get("log_id")
            if not has_trace:
                missing_trace.append(m.get("path", "?"))

        # Check 2: Were there audit/query side-effect calls in the trace?
        audit_steps = [s for s in steps
                       if any(kw in str(s.get("path", "")).lower()
                              for kw in ("audit", "log", "history", "trace", "journal", "record"))]

        # Check 3: Cross-check snapshots for audit table growth
        audit_growth_detected = False
        if snapshots:
            if isinstance(snapshots, dict):
                before = snapshots.get("before", {})
                after = snapshots.get("after", {})
            elif isinstance(snapshots, (list, tuple)) and len(snapshots) >= 2:
                before, after = snapshots[0], snapshots[1]
            else:
                before, after = {}, {}
            if isinstance(before, dict) and isinstance(after, dict):
                audit_before = before.get("audit_logs") or before.get("logs") or 0
                audit_after = after.get("audit_logs") or after.get("logs") or 0
                try:
                    if int(audit_after) > int(audit_before):
                        audit_growth_detected = True
                except (ValueError, TypeError):
                    pass

        # Verdict logic (audit endpoint presence takes priority over trace_id)
        if audit_steps or audit_growth_detected:
            return OracleResult(True, "AuditOracle", "L6",
                explanation=f"审计记录已确认：{len(audit_steps)}个审计端点调用"
                + (f"，审计表增长={audit_growth_detected}" if audit_growth_detected else ""))

        if missing_trace:
            return OracleResult(False, "AuditOracle", "L6",
                violated_rule="missing_audit_trail",
                expected=f"{len(mutations)}个变更操作应产生审计记录",
                actual=f"缺少trace_id: {', '.join(missing_trace[:3])}，且未检测到审计端点的调用",
                severity="P1", confidence=0.75,
                explanation=f"变更操作完成后未发现审计追踪（trace_id缺失，无audit端点调用）")

        return OracleResult(True, "AuditOracle", "L6",
            explanation=f"所有{len(mutations)}条变更均有trace_id")


class NotificationOracle(BaseOracle):
    name = "NotificationOracle"; layer = "L6"
    trigger_keywords = ["通知", "消息", "提醒", "notification", "message", "alert"]

    def evaluate(self, scenario, trace, snapshots=None):
        steps = trace.get("steps", [])
        mutations = [s for s in steps
                     if s.get("action") in ("create", "update", "cancel", "refund", "submit", "approve")
                     and s.get("response", {}).get("status_code", 0) == 200]

        if len(mutations) < 2:
            return OracleResult(True, "NotificationOracle", "L6",
                explanation="变更操作不足2个，跳过通知检查")

        # Check 1: Were notification endpoints called in the trace?
        notify_steps = [s for s in steps
                        if any(kw in str(s.get("path", "")).lower()
                               for kw in ("notif", "message", "alert", "push", "webhook", "email", "sms", "callback"))]
        has_notify = len(notify_steps) > 0

        # Check 2: Do mutation responses reference notification side-effects?
        notify_in_response = False
        for m in mutations:
            resp = m.get("response", {}) if isinstance(m.get("response"), dict) else {}
            body = resp.get("body", {}) if isinstance(resp.get("body"), dict) else {}
            # Check for notification-related fields in response body
            for nf in ("notification_id", "message_sent", "alert_id", "notified", "push_result"):
                if body.get(nf) is not None:
                    notify_in_response = True
                    break
            if notify_in_response:
                break

        # Check 3: Cross-check snapshots for notification table growth
        notify_growth = False
        if snapshots:
            if isinstance(snapshots, dict):
                before = snapshots.get("before", {})
                after = snapshots.get("after", {})
            elif isinstance(snapshots, (list, tuple)) and len(snapshots) >= 2:
                before, after = snapshots[0], snapshots[1]
            else:
                before, after = {}, {}
            if isinstance(before, dict) and isinstance(after, dict):
                notif_before = before.get("notifications") or before.get("messages") or 0
                notif_after = after.get("notifications") or after.get("messages") or 0
                try:
                    if int(notif_after) > int(notif_before):
                        notify_growth = True
                except (ValueError, TypeError):
                    pass

        # Verdict
        if has_notify or notify_in_response or notify_growth:
            return OracleResult(True, "NotificationOracle", "L6",
                explanation=f"通知机制已确认：{len(notify_steps)}个端点调用，响应内通知={notify_in_response}")

        # No notification mechanism detected at all after multiple mutations
        return OracleResult(False, "NotificationOracle", "L6",
            violated_rule="no_notification_for_mutations",
            expected=f"{len(mutations)}个变更操作应触发通知机制",
            actual="未检测到通知端点调用、响应内通知字段或通知数据增长",
            severity="P1", confidence=0.72,
            explanation="多次变更操作后缺少消息通知/异步事件证据")


class EvidenceOracle(BaseOracle):
    name = "EvidenceOracle"; layer = "L6"
    trigger_keywords = ["证据", "复现", "evidence", "reproduce", "trace_id"]

    def evaluate(self, scenario, trace, snapshots=None):
        steps = trace.get("steps", [])
        has_trace_id = False
        for s in steps:
            body = s.get("response", {}).get("body", {}) if isinstance(s.get("response"), dict) else {}
            if isinstance(body, dict) and (body.get("trace_id") or body.get("request_id") or body.get("correlation_id")):
                has_trace_id = True; break
        errors = trace.get("errors", [])
        if errors and not has_trace_id:
            return OracleResult(False, "EvidenceOracle", "L6", "no_trace_id",
                "错误响应应包含trace_id便于排查", str(errors[:1])[:80], "P2", 0.65)
        return OracleResult(True, "EvidenceOracle", "L6")


# ═══════════════════════════════════════════════════
# Oracle Registry
# ═══════════════════════════════════════════════════

class OracleRegistry:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._oracles: dict[str, BaseOracle] = {}
            cls._instance._register_builtins()
        return cls._instance

    def _register_builtins(self):
        builtins = [
            # L1
            HttpStatusOracle(), SchemaOracle(), FieldTypeOracle(), RequiredFieldOracle(), ErrorCodeOracle(),
            # L2
            DataIntegrityOracle(), ConsistencyOracle(), TransactionOracle(), CacheConsistencyOracle(),
            # L3
            MoneyOracle(), InventoryOracle(), StateOracle(), WorkflowOracle(), QuotaOracle(), TemporalOracle(),
            # L4
            PermissionOracle(), PrivacyOracle(), TenantIsolationOracle(), AuthSessionOracle(),
            # L5
            IdempotencyOracle(), ConcurrencyOracle(), PerformanceOracle(), RecoveryOracle(),
            # L6
            AuditOracle(), NotificationOracle(), EvidenceOracle(),
        ]
        for o in builtins:
            self._oracles[o.name] = o

    def register_custom(self, oracle: BaseOracle):
        self._oracles[oracle.name] = oracle

    def get_by_layer(self, layer: str) -> list[BaseOracle]:
        return [o for o in self._oracles.values() if o.layer == layer]

    def get_all_layers(self) -> dict[str, list[str]]:
        layers = {}
        for o in self._oracles.values():
            layers.setdefault(o.layer, []).append(o.name)
        return layers

    def get_for_category(self, category: str) -> list[BaseOracle]:
        mapping = {
            "state_machine": ["StateOracle", "WorkflowOracle"],
            "permission": ["PermissionOracle", "TenantIsolationOracle"],
            "money": ["MoneyOracle", "TransactionOracle"],
            "inventory": ["InventoryOracle", "TransactionOracle"],
            "concurrency": ["ConcurrencyOracle", "IdempotencyOracle"],
            "invariant": ["ConsistencyOracle", "DataIntegrityOracle"],
            "privacy": ["PrivacyOracle", "AuditOracle"],
            "performance": ["PerformanceOracle"],
            "notification": ["NotificationOracle", "RecoveryOracle"],
        }
        matched = []
        for name in mapping.get(category, []):
            o = self._oracles.get(name)
            if o: matched.append(o)
        # Always include L1 protocol oracles
        for o in self.get_by_layer("L1"):
            if o not in matched: matched.append(o)
        # Always include universal L2+L5 oracles
        for name in ("StateOracle", "ConsistencyOracle", "DataIntegrityOracle", "ErrorCodeOracle"):
            o = self._oracles.get(name)
            if o and o not in matched: matched.append(o)
        return matched

    def auto_detect(self, prd_text: str, limit: int = 15) -> list[BaseOracle]:
        scored = []
        for o in self._oracles.values():
            if o.layer == "L1": continue  # L1 always included
            score = sum(1 for kw in o.trigger_keywords if kw.lower() in prd_text.lower())
            if score > 0: scored.append((score, o))
        scored.sort(key=lambda x: -x[0])
        result = [o for _, o in scored[:limit]]
        # Always prepend L1 oracles
        return self.get_by_layer("L1") + result

    def get_all_names(self) -> list[str]:
        return list(self._oracles.keys())


# ═══════════════════════════════════════════════════
# Oracle Engine
# ═══════════════════════════════════════════════════

class OracleEngine:
    def __init__(self):
        self.registry = OracleRegistry()

    def evaluate(self, scenario: dict, trace: dict, snapshots: Any = None) -> list[OracleResult]:
        import logging
        _log = logging.getLogger("OracleEngine")
        results = []
        oracles = self.registry.get_for_category(scenario.get("category", ""))
        for oracle in oracles:
            try:
                results.append(oracle.evaluate(scenario, trace, snapshots))
            except Exception as e:
                _log.warning("Oracle %s crashed on scenario %s: %s",
                    oracle.name, scenario.get("title", "?")[:80], e)
                # Generate a failed result so the crash is visible in the output
                results.append(OracleResult(
                    passed=False, oracle_name=oracle.name, layer=oracle.layer,
                    violated_rule="oracle_crash",
                    expected="Oracle正常执行",
                    actual=f"Oracle崩溃: {type(e).__name__}: {str(e)[:200]}",
                    severity="P2", confidence=0.30,
                    explanation=f"Oracle内部异常，判定降级为P2低置信度"))
        return results

    def evaluate_all(self, scenarios: list, traces: list[dict]) -> list[OracleResult]:
        results = []
        for sc, tr in zip(scenarios, traces):
            d = sc.to_dict() if hasattr(sc, "to_dict") else sc
            results.extend(self.evaluate(d, tr))
        return results

    def evaluate_chain(self, scenario: dict, trace: dict, snapshots: Any = None) -> dict:
        """V12.2: Chained oracle evaluation — L1→L2→...→L6, each layer can feed next.
        
        Returns standardized output: {oracle_name, result, confidence, severity, evidence}.
        """
        results = []
        for layer in ("L1", "L2", "L3", "L4", "L5", "L6"):
            oracles = self.registry.get_by_layer(layer)
            for oracle in oracles:
                if oracle.name in ("HttpStatusOracle", "SchemaOracle", "ErrorCodeOracle"):
                    # L1 always runs
                    pass
                elif oracle.name in ("StateOracle", "ConsistencyOracle", "DataIntegrityOracle"):
                    # Universal oracles
                    pass
                elif not any(kw in str(scenario.get("oracle_rules", [])).lower() for kw in oracle.trigger_keywords if kw):
                    # Skip if no trigger keyword match
                    continue
                try:
                    r = oracle.evaluate(scenario, trace, snapshots)
                    results.append(r)
                except Exception as e:
                    _log.warning("Oracle %s crashed in evaluate_chain: %s",
                        oracle.name, e)
                    results.append(OracleResult(
                        passed=False, oracle_name=oracle.name, layer=oracle.layer,
                        violated_rule="oracle_crash",
                        expected="Oracle正常执行",
                        actual=f"Oracle崩溃: {type(e).__name__}: {str(e)[:200]}",
                        severity="P2", confidence=0.30,
                        explanation=f"Oracle内部异常，判定降级为P2低置信度"))
        return self._standard_output(results)

    def _standard_output(self, results: list[OracleResult]) -> dict:
        """V12.2: Standardized output schema for all oracle evaluations."""
        violations = [r for r in results if not r.passed]
        by_layer = {}
        for r in violations:
            by_layer.setdefault(r.layer, []).append({
                "oracle": r.oracle_name,
                "result": "violation",
                "confidence": r.confidence,
                "severity": r.severity,
                "evidence": r.explanation[:200],
            })
        return {
            "total_evaluated": len(results),
            "violations_found": len(violations),
            "by_layer": by_layer,
            "details": [r.to_dict() for r in violations],
        }

    def get_violations(self, results: list[OracleResult]) -> list[OracleResult]:
        return [r for r in results if not r.passed]

    def summary(self, results: list[OracleResult]) -> dict:
        violations = self.get_violations(results)
        by_layer = {}
        by_oracle = {}
        for r in violations:
            by_layer[r.layer] = by_layer.get(r.layer, 0) + 1
            by_oracle[r.oracle_name] = by_oracle.get(r.oracle_name, 0) + 1
        return {
            "total_checks": len(results), "violations": len(violations),
            "pass_rate": round(1 - len(violations) / max(len(results), 1), 2),
            "by_layer": by_layer, "by_oracle": by_oracle,
        }


# ═══════════════════════════════════════════════════
# Snapshot Engine
# ═══════════════════════════════════════════════════

@dataclass
class Snapshot:
    entity: str; entity_id: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    captured_at_utc: str = ""

@dataclass
class SnapshotPair:
    scenario_id: str; entity: str
    before: Snapshot; after: Snapshot
    diff: dict[str, dict[str, Any]] = field(default_factory=dict)

class SnapshotEngine:
    def capture(self, entity: str, entity_id: str, state: dict) -> Snapshot:
        return Snapshot(entity=entity, entity_id=entity_id, state=dict(state),
                        captured_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    def diff(self, before: Snapshot, after: Snapshot, scenario_id: str = "") -> SnapshotPair:
        changes = {}
        for key in set(before.state.keys()) | set(after.state.keys()):
            bv = before.state.get(key); av = after.state.get(key)
            if bv != av: changes[key] = {"from": bv, "to": av}
        return SnapshotPair(scenario_id=scenario_id, entity=before.entity,
                           before=before, after=after, diff=changes)


# ═══════════════════════════════════════════════════
# Evidence Graph Builder
# ═══════════════════════════════════════════════════

@dataclass
class BugEvidenceGraph:
    bug_id: str; title: str
    scenario: dict = field(default_factory=dict)
    request_chain: list[dict] = field(default_factory=list)   # V12.2: full request chain
    response_chain: list[dict] = field(default_factory=list)  # V12.2: full response chain
    state_diff: dict = field(default_factory=dict)            # V12.2: before/after diff
    execution_trace: dict = field(default_factory=dict)
    before_snapshot: dict = field(default_factory=dict)
    after_snapshot: dict = field(default_factory=dict)
    oracle_results: list = field(default_factory=list)
    reproduction_steps: str = ""
    severity: str = "P1"; confidence: float = 0.0
    evidence_id: str = ""
    layers_triggered: list[str] = field(default_factory=list)
    vote_summary: dict = field(default_factory=dict)          # voting-based confirmation

    def to_dict(self) -> dict:
        return {
            "bug_id": self.bug_id, "title": self.title, "severity": self.severity,
            "confidence": self.confidence, "scenario": self.scenario,
            "request_chain": self.request_chain, "response_chain": self.response_chain,
            "state_diff": self.state_diff,
            "execution_trace": self.execution_trace,
            "before_snapshot": self.before_snapshot, "after_snapshot": self.after_snapshot,
            "oracle_results": [r.to_dict() for r in self.oracle_results],
            "reproduction_steps": self.reproduction_steps,
            "evidence_id": self.evidence_id, "layers_triggered": self.layers_triggered,
            "vote_summary": self.vote_summary,
        }

class EvidenceGraphBuilder:
    # Layer priority weights — higher layers get more veto power
    LAYER_WEIGHTS = {"L1": 1.0, "L2": 1.0, "L3": 1.5, "L4": 2.0, "L5": 1.5, "L6": 1.0}

    def build(self, scenario: dict, trace: dict, snapshots: Any,
              oracle_results: list[OracleResult]) -> BugEvidenceGraph:
        sid = scenario.get("id", str(uuid.uuid4().hex[:8]))

        # ── Vote-based bug confirmation ──
        # A bug is CONFIRMED only when weighted failures exceed weighted passes,
        # not when just a single oracle says so.
        total_weight = 0.0
        failure_weight = 0.0
        for r in oracle_results:
            w = self.LAYER_WEIGHTS.get(r.layer, 1.0)
            total_weight += w
            if not r.passed:
                failure_weight += w

        bug_id = f"BUG_V12_{sid}"
        if total_weight > 0 and failure_weight / total_weight >= 0.5:
            bug_id = f"BUG_CONFIRMED_{sid}"
        elif failure_weight > 0:
            bug_id = f"BUG_FLAGGED_{sid}"  # flagged but not outright confirmed

        steps = trace.get("steps", [])
        repro = [f"{s.get('method','POST')} {s.get('path','?')} → HTTP {s.get('status','?')}"
                 for s in steps if isinstance(s, dict)]

        # ── Aggregated severity and confidence ──
        worst = oracle_results[0] if oracle_results else OracleResult(True)
        failed_confidences = [r.confidence for r in oracle_results if not r.passed]
        # Take the worst severity, average the confidence of all failed oracles
        for r in oracle_results:
            if not r.passed and r.severity == "P0":
                worst = r
                break
        if not any(not r.passed for r in oracle_results):
            worst = oracle_results[0] if oracle_results else OracleResult(True)
        agg_confidence = (sum(failed_confidences) / len(failed_confidences)) if failed_confidences else worst.confidence

        layers = list(set(r.layer for r in oracle_results if not r.passed))
        return BugEvidenceGraph(
            bug_id=bug_id, title=scenario.get("title", ""), scenario=scenario,
            execution_trace=trace,
            before_snapshot=getattr(snapshots, "before", None).__dict__ if snapshots and hasattr(snapshots, "before") else {},
            after_snapshot=getattr(snapshots, "after", None).__dict__ if snapshots and hasattr(snapshots, "after") else {},
            oracle_results=oracle_results,
            reproduction_steps="\n".join(repro),
            severity=worst.severity, confidence=round(agg_confidence, 2),
            evidence_id=str(uuid.uuid4()), layers_triggered=layers,
            vote_summary={
                "total_votes": len(oracle_results),
                "failed_votes": sum(1 for r in oracle_results if not r.passed),
                "passed_votes": sum(1 for r in oracle_results if r.passed),
                "failure_weight": round(failure_weight, 2),
                "total_weight": round(total_weight, 2),
                "confirmation_threshold_met": failure_weight / total_weight >= 0.5 if total_weight > 0 else False,
            },
        )
