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

import hashlib, json, logging, re, time, uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger("OracleEngine")

# First-class System Behavior Space hooks — no method replacement on OracleEngine.
OracleEvaluateHook = Callable[..., list[Any]]
EvidenceScenarioHook = Callable[..., dict[str, Any]]
_ORACLE_EVALUATE_HOOK: OracleEvaluateHook | None = None
_EVIDENCE_SCENARIO_HOOK: EvidenceScenarioHook | None = None


def register_oracle_evaluate_hook(hook: OracleEvaluateHook | None) -> None:
    """Post-evaluate hook: may annotate/add SystemPromiseOracle results."""
    global _ORACLE_EVALUATE_HOOK
    _ORACLE_EVALUATE_HOOK = hook


def register_evidence_scenario_hook(hook: EvidenceScenarioHook | None) -> None:
    """Pre-build hook: may attach system_behavior_space evidence onto scenario."""
    global _EVIDENCE_SCENARIO_HOOK
    _EVIDENCE_SCENARIO_HOOK = hook


def clear_oracle_hooks() -> None:
    register_oracle_evaluate_hook(None)
    register_evidence_scenario_hook(None)

_COLLECTION_RESPONSE_KEYS = {"items", "rows", "records", "results", "list", "series", "buckets"}


def _is_harness_support_step(step: dict[str, Any]) -> bool:
    """Return whether a trace step prepares the probe rather than tests it.

    Resolver, login and bootstrap calls can fail because the harness could not
    construct the target preconditions.  Treating those failures as if they
    came from the behavior slice's intended endpoint creates a real HTTP
    observation with a false defect attribution.
    """
    action = str(step.get("action") or "").strip().lower()
    path = str(step.get("path") or "").split("?", 1)[0].rstrip("/").lower()
    return (
        action == "login"
        or action.startswith("login_")
        or action.startswith("resolve_")
        or action.startswith("bootstrap_create_")
        or path.endswith("/login")
    )


_STATE_FIELD_NAMES = {"status", "state", "order_status", "task_status", "lifecycle_state"}
_IDENTITY_FIELD_RE = re.compile(r"(?:^id$|^uuid$|^sku$|^code$|_id$|id$)", re.I)


def _nested_field_values(value: Any, *, field_kind: str, depth: int = 0) -> set[str]:
    """Collect bounded state or identity values from a runtime response."""
    if depth > 6:
        return set()
    values: set[str] = set()
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key or "").strip()
            matched = (
                key.lower() in _STATE_FIELD_NAMES
                if field_kind == "state"
                else bool(_IDENTITY_FIELD_RE.search(key))
            )
            if matched and isinstance(child, (str, int, float)) and str(child).strip():
                values.add(str(child).strip())
            if isinstance(child, (dict, list)):
                values.update(_nested_field_values(child, field_kind=field_kind, depth=depth + 1))
    elif isinstance(value, list):
        for child in value[:200]:
            values.update(_nested_field_values(child, field_kind=field_kind, depth=depth + 1))
    return values


def _state_identity_observations(value: Any, *, depth: int = 0) -> list[tuple[str, str]]:
    """Return state observations only when the same object carries identity proof."""

    if depth > 6:
        return []
    observations: list[tuple[str, str]] = []
    if isinstance(value, list):
        for child in value[:200]:
            observations.extend(_state_identity_observations(child, depth=depth + 1))
        return observations
    if not isinstance(value, dict):
        return observations

    status = ""
    for key, child in value.items():
        if str(key or "").strip().lower() in _STATE_FIELD_NAMES and isinstance(child, (str, int, float)):
            status = str(child).strip().upper()
            if status:
                break

    identity_candidates: list[tuple[str, str]] = []
    for key, child in value.items():
        if not isinstance(child, (str, int, float)) or not str(child).strip():
            continue
        key_l = str(key or "").strip().lower()
        if _IDENTITY_FIELD_RE.search(key_l):
            identity_candidates.append((key_l, str(child).strip()))
    identity = ""
    for preferred in ("id", "uuid", "sku", "code"):
        match = next((candidate for key, candidate in identity_candidates if key == preferred), "")
        if match:
            identity = match
            break
    if not identity and len(identity_candidates) == 1:
        identity = identity_candidates[0][1]
    if status and identity:
        observations.append((identity, status))

    for child in value.values():
        if isinstance(child, (dict, list)):
            observations.extend(_state_identity_observations(child, depth=depth + 1))
    return observations


def _step_response_body(step: dict[str, Any]) -> Any:
    response = step.get("response") if isinstance(step.get("response"), dict) else {}
    return response.get("body")


def _step_identity_targets(step: dict[str, Any]) -> set[str]:
    request = step.get("request") if isinstance(step.get("request"), dict) else {}
    targets = _nested_field_values(request.get("body"), field_kind="identity")
    targets.update(_nested_field_values(_step_response_body(step), field_kind="identity"))
    for segment in str(step.get("path") or "").split("?", 1)[0].split("/"):
        text = segment.strip()
        if text and "{" not in text and not text.startswith(":"):
            targets.add(text)
    return targets


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
    oracle_tier: str = ""
    customer_deliverable: bool | None = None
    demotion_reason: str = ""
    is_finding: bool = True  # False marks diagnostic-only results (e.g. oracle crash) that must never count as a confirmed finding

    def to_dict(self) -> dict:
        payload = {
            "passed": self.passed, "layer": self.layer,
            "oracle_name": self.oracle_name, "oracle": self.oracle_name,
            "violated_rule": self.violated_rule, "expected": self.expected,
            "actual": self.actual, "severity": self.severity,
            "confidence": self.confidence, "explanation": self.explanation,
            "is_finding": self.is_finding,
        }
        if self.oracle_tier:
            payload["oracle_tier"] = self.oracle_tier
        if self.customer_deliverable is not None:
            payload["customer_deliverable"] = self.customer_deliverable
        if self.demotion_reason:
            payload["demotion_reason"] = self.demotion_reason
        return payload


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
        scenario_signals = " ".join([
            str(scenario.get("category") or ""),
            str(scenario.get("behavior_slice_kind") or ""),
            " ".join(str(item or "") for item in scenario.get("oracle_rules", []) or []),
        ]).lower()
        permission_probe = "permissionoracle" in scenario_signals or "permission" in scenario_signals or "isolation" in scenario_signals
        replay_probe = "concurrencyoracle" in scenario_signals or "idempotencyoracle" in scenario_signals or "concurrency" in scenario_signals or "idempotency" in scenario_signals
        state_probe = "stateoracle" in scenario_signals or "state_machine" in scenario_signals or "transition" in scenario_signals
        for s in trace.get("steps", []):
            if not isinstance(s, dict) or _is_harness_support_step(s):
                continue
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
            expected = s.get("expected_status") or 0
            if expected and int(expected) and status != expected:
                expected_i, status_i = int(expected), int(status)
                # REST success class: 200/201/202/204 are interchangeable for
                # "expected success" unless a create-specific rule already fired.
                if 200 <= expected_i < 300 and 200 <= status_i < 300:
                    continue
                # Permission/isolation scenarios need semantic evidence, not a
                # literal status-code equality. A different 4xx is inconclusive
                # (validation may run before authorization), while a 2xx denial
                # bypass is handled by PermissionOracle below.
                if permission_probe:
                    continue
                # Repeated-write status expectations are not universal REST
                # contracts. Idempotency/Concurrency oracles decide whether an
                # externally observable duplicate side effect actually exists.
                if replay_probe and expected_i == 409:
                    continue
                # A forbidden state transition is a semantic claim. Only the
                # StateOracle can confirm that the same entity was proven in
                # the required source state and reached the forbidden target.
                if state_probe and expected_i >= 400:
                    continue
                return OracleResult(False, "HttpStatusOracle", "L1", "expected_status_mismatch",
                    f"应返回 HTTP {expected}", f"实际返回 HTTP {status}", "P1", 0.90)
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
                if _looks_like_aggregate_or_collection_response(body):
                    continue
                for field in ("id", "status", "created_at"):
                    if field in body and body.get(field) is None and s.get("expected_status") == 200:
                        return OracleResult(False, "RequiredFieldOracle", "L1", "null_required",
                            f"必填字段{field}不应为null", f"{field}=null", "P1", 0.80)
        return OracleResult(True, "RequiredFieldOracle", "L1")


def _looks_like_aggregate_or_collection_response(body: dict[str, Any]) -> bool:
    for key, value in body.items():
        normalized = str(key or "").strip().lower()
        if normalized in _COLLECTION_RESPONSE_KEYS and isinstance(value, list):
            return True
    return False


def _response_records(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if not isinstance(body, dict):
        return []
    for key in ("items", "rows", "records", "results", "list", "data"):
        value = body.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return [body] if body else []


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


class VisibilityOracle(BaseOracle):
    name = "VisibilityOracle"; layer = "L2"
    trigger_keywords = ["展示", "显示", "可见", "隐藏", "visible", "visibility", "hidden", "invisible"]

    def evaluate(self, scenario, trace, snapshots=None):
        signal_text = " ".join([
            str(scenario.get("title") or ""),
            str(scenario.get("description") or ""),
            " ".join(str(item or "") for item in scenario.get("oracle_rules", []) or []),
        ]).lower()
        visibility_contract_phrases = (
            "不展示",
            "不得展示",
            "不应展示",
            "禁止展示",
            "只展示",
            "仅展示",
            "不可见",
            "应隐藏",
            "必须隐藏",
            "隐藏状态",
            "隐藏资源",
            "隐藏实体",
            "must not display",
            "should not display",
            "must not show",
            "should not show",
            "not visible",
            "must be hidden",
            "hidden state",
            "hidden resource",
            "only display",
            "only show",
        )
        if not any(phrase in signal_text for phrase in visibility_contract_phrases):
            return OracleResult(True, "VisibilityOracle", "L2")
        expected_state = str(scenario.get("expected_state") or "").strip().upper()
        forbidden_states = {expected_state} if expected_state else set()
        if not forbidden_states:
            return OracleResult(True, "VisibilityOracle", "L2")
        steps = trace.get("steps", []) if isinstance(trace, dict) else []
        for step in steps:
            response = step.get("response", {}) if isinstance(step, dict) else {}
            body = response.get("body", {}) if isinstance(response, dict) else {}
            records = _response_records(body)
            if not records:
                continue
            leaked = []
            for item in records:
                status = str(
                    item.get("status")
                    or item.get("state")
                    or item.get("product_status")
                    or item.get("visibility_status")
                    or ""
                ).strip().upper()
                if status in forbidden_states:
                    leaked.append({
                        "id": str(item.get("id") or item.get("sku") or item.get("code") or ""),
                        "status": status,
                    })
            if leaked:
                sample = leaked[0]
                return OracleResult(
                    False,
                    "VisibilityOracle",
                    "L2",
                    "hidden_entity_exposed",
                    f"前台不应展示状态为 {', '.join(sorted(forbidden_states))} 的资源",
                    f"{sample.get('id') or 'resource'} status={sample['status']}",
                    "P1",
                    0.96,
                    "来源约束要求资源对前台隐藏，但运行时响应仍返回了隐藏态实体",
                )
        return OracleResult(True, "VisibilityOracle", "L2")


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

    @staticmethod
    def _numeric_field(body: Any, names: tuple[str, ...]) -> float | None:
        if not isinstance(body, dict):
            return None
        for name in names:
            val = body.get(name)
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str):
                try:
                    return float(val.strip())
                except (ValueError, TypeError):
                    continue
        return None

    def evaluate(self, scenario, trace, snapshots=None):
        steps = trace.get("steps", []) if isinstance(trace, dict) else []
        money_fields = (
            "amount", "total_price", "total", "price", "balance", "fee",
            "payableAmount", "payable_amount", "amountPaid", "amount_paid",
            "paidAmount", "refundAmount", "refund_amount", "available_qty", "stock",
        )
        for s in steps:
            if not isinstance(s, dict):
                continue
            body = s.get("response", {}).get("body", {}) if isinstance(s.get("response"), dict) else {}
            if isinstance(body, dict):
                for field in money_fields:
                    val = body.get(field)
                    if val is not None:
                        try:
                            if float(val) < 0:
                                return OracleResult(False, "MoneyOracle", "L3", "negative_amount",
                                    f"{field} >= 0", f"{field} = {val}", "P0", 0.95, f"负金额: {field}={val}")
                        except (ValueError, TypeError, AttributeError):
                            pass  # non-numeric value — can't compare, skip

        # Successful pay/refund whose request amount disagrees with the resource
        # payable/paid field — classic money_quantity_conservation defect.
        paid_amounts: list[float] = []
        for s in steps:
            if not isinstance(s, dict):
                continue
            method = str(s.get("method") or "").upper()
            if method not in {"POST", "PUT", "PATCH"}:
                continue
            resp = s.get("response") if isinstance(s.get("response"), dict) else {}
            status = int(s.get("status") or resp.get("status_code") or 0)
            if not (200 <= status < 300):
                continue
            path_l = str(s.get("path") or "").lower()
            action_l = str(s.get("action") or "").lower()
            req = s.get("request") if isinstance(s.get("request"), dict) else {}
            req_body = req.get("body") if isinstance(req.get("body"), dict) else {}
            resp_body = resp.get("body") if isinstance(resp.get("body"), dict) else {}
            req_amount = self._numeric_field(
                req_body, ("amount", "payAmount", "pay_amount", "refundAmount", "refund_amount"),
            )
            if req_amount is None:
                continue
            is_pay = any(tok in path_l or tok in action_l for tok in ("pay", "payment", "settle", "charge"))
            is_refund = "refund" in path_l or "refund" in action_l
            if is_pay:
                paid_amounts.append(req_amount)
                payable = self._numeric_field(
                    resp_body,
                    (
                        "payableAmount", "payable_amount", "orderAmount", "order_amount",
                        "totalAmount", "total_amount", "amountDue", "amount_due",
                    ),
                )
                if payable is not None and abs(payable - req_amount) > 0.009:
                    return OracleResult(
                        False, "MoneyOracle", "L3", "payment_amount_mismatch",
                        "支付金额必须等于应付金额",
                        f"request.amount={req_amount} payable={payable}",
                        "P0", 0.90,
                        f"支付金额与应付不一致: {req_amount} vs {payable}",
                    )
            if is_refund:
                paid = self._numeric_field(
                    resp_body,
                    ("amountPaid", "amount_paid", "paidAmount", "paid_amount", "payableAmount", "payable_amount"),
                )
                baseline = paid if paid is not None else (max(paid_amounts) if paid_amounts else None)
                if baseline is not None and req_amount > baseline + 0.009:
                    return OracleResult(
                        False, "MoneyOracle", "L3", "refund_exceeds_paid",
                        "退款金额不得超过已付金额",
                        f"refund={req_amount} paid={baseline}",
                        "P0", 0.92,
                        f"超付退款: refund={req_amount} > paid={baseline}",
                    )

        # Before/after observe bookends: money fields must not go negative and
        # must not invent balance from thin air on a no-op observe pair.
        before_steps = [
            s for s in steps
            if isinstance(s, dict) and str(s.get("action") or "").startswith("observe_money")
            and "after" not in str(s.get("action") or "").lower()
        ]
        after_steps = [
            s for s in steps
            if isinstance(s, dict) and "observe_money_after" in str(s.get("action") or "").lower()
        ]
        if before_steps and after_steps:
            before_body = (before_steps[0].get("response") or {}).get("body") if isinstance(before_steps[0].get("response"), dict) else {}
            after_body = (after_steps[-1].get("response") or {}).get("body") if isinstance(after_steps[-1].get("response"), dict) else {}
            for field in ("balance", "available_qty", "stock", "quantity", "amount"):
                b = self._numeric_field(before_body if isinstance(before_body, dict) else {}, (field,))
                a = self._numeric_field(after_body if isinstance(after_body, dict) else {}, (field,))
                if a is not None and a < 0:
                    return OracleResult(
                        False, "MoneyOracle", "L3", "negative_amount_after_write",
                        f"{field} >= 0 after write", f"{field}={a}", "P0", 0.93,
                        f"写后负值: {field}={a}",
                    )
                if b is not None and a is not None and a > b * 10 + 1000 and b >= 0:
                    # Extreme inflation without a matching credit signal — soft
                    # conservation alarm (inventory/balance explosion).
                    return OracleResult(
                        False, "MoneyOracle", "L3", "quantity_explosion",
                        f"{field} 不应无依据暴涨", f"{field}: {b}→{a}", "P1", 0.70,
                        f"数量异常膨胀: {field} {b}→{a}",
                    )

        refunds = [s for s in steps if isinstance(s, dict) and "refund" in str(s.get("action", "")).lower()]
        if len(refunds) >= 2 and isinstance(refunds[-1].get("response"), dict) and refunds[-1].get("response", {}).get("status_code", 0) == 200:
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

        if is_forbidden:
            hints = scenario.get("runtime_hints") if isinstance(scenario.get("runtime_hints"), dict) else {}
            source_state = str(hints.get("source_state") or "").strip().upper()
            target_state = str(hints.get("target_state") or "").strip().upper()
            treatment_index = -1
            treatment: dict[str, Any] = {}
            for index, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                action = str(step.get("action") or "").strip().lower()
                method = str(step.get("method") or "").upper()
                expected = int(step.get("expected_status") or 0)
                if action.startswith("transition_") or (
                    method in {"POST", "PUT", "PATCH", "DELETE"} and expected >= 400
                ):
                    treatment_index = index
                    treatment = step
            if treatment_index < 0 or not treatment:
                trace.setdefault("oracle_evidence_gaps", []).append({
                    "code": "STATE_TRANSITION_TREATMENT_MISSING",
                    "operator_action": "inspect_scenario_transition_step",
                })
                return OracleResult(True, "StateOracle", "L3")

            target_identities = _step_identity_targets(treatment)
            source_proven = False
            if source_state and target_identities:
                for step in steps[:treatment_index]:
                    if not isinstance(step, dict):
                        continue
                    states = {
                        value.upper()
                        for value in _nested_field_values(_step_response_body(step), field_kind="state")
                    }
                    identities = _nested_field_values(_step_response_body(step), field_kind="identity")
                    if source_state in states and identities.intersection(target_identities):
                        source_proven = True
                        break
            if not source_proven:
                trace.setdefault("precondition_not_met", []).append({
                    "required_source_state": source_state or "UNDECLARED",
                    "target_identity_sample": sorted(target_identities)[:3],
                    "reason": "source_state_identity_not_proven",
                })
                return OracleResult(True, "StateOracle", "L3")

            treatment_response = treatment.get("response") if isinstance(treatment.get("response"), dict) else {}
            treatment_status = int(treatment_response.get("status_code") or treatment.get("status") or 0)
            effect_proven = not target_state
            if target_state:
                for step in steps[treatment_index:]:
                    if not isinstance(step, dict):
                        continue
                    states = {
                        value.upper()
                        for value in _nested_field_values(_step_response_body(step), field_kind="state")
                    }
                    identities = _nested_field_values(_step_response_body(step), field_kind="identity")
                    if target_state in states and identities.intersection(target_identities):
                        effect_proven = True
                        break
            if 200 <= treatment_status < 300 and effect_proven:
                return OracleResult(
                    False,
                    "StateOracle",
                    "L3",
                    "forbidden_transition",
                    "Forbidden state transition must be rejected",
                    f"HTTP {treatment_status}",
                    "P0",
                    0.93,
                    f"Forbidden path accepted: {scenario.get('title', '')}",
                )
            if 200 <= treatment_status < 300 and not effect_proven:
                trace.setdefault("oracle_evidence_gaps", []).append({
                    "code": "STATE_TRANSITION_EFFECT_NOT_PROVEN",
                    "required_target_state": target_state or "UNDECLARED",
                    "operator_action": "add_bound_post_transition_observer",
                })
            return OracleResult(True, "StateOracle", "L3")

        # Compare only observations that prove the same entity identity. A list
        # containing one cancelled record followed by another pending record is
        # not a state transition, and an unchanged terminal state is not a
        # regression.
        terminal_states = {"completed", "cancelled", "closed", "archived", "deleted", "refunded"}
        last_state_by_identity: dict[str, str] = {}
        for step in steps:
            if not isinstance(step, dict):
                continue
            for identity, observed_status in _state_identity_observations(_step_response_body(step)):
                previous = last_state_by_identity.get(identity, "")
                if (
                    previous.lower() in terminal_states
                    and observed_status != previous
                    and observed_status.lower() not in terminal_states
                ):
                    return OracleResult(False, "StateOracle", "L3", "terminal_to_nonterminal",
                        f"终态 {previous} 不应转换为 {observed_status}",
                        f"{previous} → {observed_status}", "P0", 0.88)
                last_state_by_identity[identity] = observed_status

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


class CouponOracle(BaseOracle):
    name = "CouponOracle"; layer = "L3"
    trigger_keywords = ["优惠券", "coupon", "有效期", "类目", "折扣", "validate"]

    def evaluate(self, scenario, trace, snapshots=None):
        runtime_hints = scenario.get("runtime_hints", {}) if isinstance(scenario, dict) else {}
        sample = runtime_hints.get("coupon_validation_sample", {}) if isinstance(runtime_hints, dict) else {}
        rule = str(runtime_hints.get("coupon_validation_rule") or "").strip()
        if not rule:
            for item in scenario.get("oracle_rules", []) if isinstance(scenario, dict) else []:
                text = str(item or "").strip()
                if text.startswith("CouponOracle."):
                    rule = text.split(".", 1)[1].strip()
                    break
        if not rule:
            return OracleResult(True, "CouponOracle", "L3")
        steps = trace.get("steps", []) if isinstance(trace, dict) else []
        if not steps:
            return OracleResult(True, "CouponOracle", "L3")
        response = steps[-1].get("response", {}) if isinstance(steps[-1], dict) else {}
        body = response.get("body", {}) if isinstance(response, dict) else {}
        if not isinstance(body, dict) or body.get("valid") is not True:
            return OracleResult(True, "CouponOracle", "L3")
        coupon = body.get("coupon", {}) if isinstance(body.get("coupon"), dict) else {}
        if rule == "expired_coupon_must_be_invalid":
            expires_at = str(sample.get("coupon_expires_at") or coupon.get("expires_at") or "").strip()
            if expires_at:
                return OracleResult(
                    False, "CouponOracle", "L3", "expired_coupon_accepted",
                    "过期优惠券应返回 valid=false", f"valid=true, expires_at={expires_at}", "P0", 0.98,
                    "优惠券已过期但校验接口仍返回 valid=true"
                )
        if rule == "inactive_coupon_must_be_invalid":
            status = str(sample.get("coupon_status") or coupon.get("status") or "").strip().upper()
            if status and status != "ACTIVE":
                return OracleResult(
                    False, "CouponOracle", "L3", "inactive_coupon_accepted",
                    "非 ACTIVE 优惠券应返回 valid=false", f"valid=true, status={status}", "P0", 0.98,
                    "已停用优惠券仍被接口判定为可用"
                )
        if rule == "coupon_category_scope_must_match":
            expected_category = str(sample.get("coupon_category_scope") or coupon.get("category_scope") or "").strip().lower()
            actual_category = str(sample.get("item_category") or "").strip().lower()
            if expected_category and actual_category and expected_category != actual_category:
                return OracleResult(
                    False, "CouponOracle", "L3", "coupon_category_scope_bypassed",
                    f"类目券仅可用于 {expected_category}", f"valid=true, item_category={actual_category}", "P0", 0.96,
                    "类目券与商品类目不匹配，但校验接口仍返回 valid=true"
                )
        if rule == "coupon_min_order_amount_must_match":
            request = steps[-1].get("request", {}) if isinstance(steps[-1], dict) else {}
            request_body = request.get("body", {}) if isinstance(request, dict) else {}
            total_amount = request_body.get("totalAmount") if isinstance(request_body, dict) else None
            minimum = coupon.get("min_order_amount")
            try:
                if minimum is not None and total_amount is not None and float(total_amount) < float(minimum):
                    return OracleResult(
                        False, "CouponOracle", "L3", "coupon_min_order_bypassed",
                        f"订单金额需 >= {minimum}", f"valid=true, totalAmount={total_amount}", "P0", 0.95,
                        "未达到优惠券门槛金额，但校验接口仍返回 valid=true"
                    )
            except (TypeError, ValueError):
                pass
        return OracleResult(True, "CouponOracle", "L3")


# ═══════════════════════════════════════════════════
# L4 — 安全权限Oracle (4 types)
# ═══════════════════════════════════════════════════

class PermissionOracle(BaseOracle):
    name = "PermissionOracle"; layer = "L4"
    trigger_keywords = ["权限", "越权", "角色", "permission", "role", "auth", "ACL"]

    def evaluate(self, scenario, trace, snapshots=None):
        for s in trace.get("steps", []):
            if not isinstance(s, dict) or _is_harness_support_step(s):
                continue
            resp = s.get("response", {}) if isinstance(s, dict) else {}
            status = resp.get("status_code", 0) if isinstance(resp, dict) else s.get("status", 0)
            expected = s.get("expected_status", 200)
            if expected in (401, 403) and 200 <= int(status or 0) < 300:
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
        owner_ids: set[str] = set()

        def collect_ids(value):
            collected: set[str] = set()
            if isinstance(value, dict):
                identity = value.get("id")
                if identity not in (None, ""):
                    collected.add(str(identity))
                for child in value.values():
                    collected.update(collect_ids(child))
            elif isinstance(value, list):
                for child in value:
                    collected.update(collect_ids(child))
            return collected

        for step in steps:
            if not isinstance(step, dict):
                continue
            if str(step.get("action") or "").startswith("resolve_owner_"):
                response = step.get("response") if isinstance(step.get("response"), dict) else {}
                owner_ids.update(collect_ids(response.get("body")))
        for s in steps:
            body = s.get("response", {}).get("body", {}) if isinstance(s.get("response"), dict) else {}
            if isinstance(body, dict):
                if body.get("tenant_id") and scenario.get("actor_tenant") and body["tenant_id"] != scenario["actor_tenant"]:
                    return OracleResult(False, "TenantIsolationOracle", "L4", "cross_tenant_access",
                        "不应访问其他租户数据", f"tenant={body['tenant_id']}", "P0", 0.95)
            action = str(s.get("action") or "")
            if owner_ids and "isolation_probe" in action:
                leaked_ids = sorted(owner_ids & collect_ids(body))
                if leaked_ids:
                    return OracleResult(
                        False,
                        "TenantIsolationOracle",
                        "L4",
                        "cross_user_collection_leak",
                        "restricted actor must not observe owner-scoped identities",
                        f"overlapping_identity_count={len(leaked_ids)}",
                        "P0",
                        0.95,
                    )
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

def _contract_activation_for_business_oracle(scenario: dict) -> bool:
    try:
        from .contract_oracles import scenario_has_contract_activation
        return scenario_has_contract_activation(scenario if isinstance(scenario, dict) else {})
    except Exception:
        return False


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
            if statuses <= {200, 201}:  # all succeeded — non-idempotent signal
                # Dual/multi-2xx without contract effect is clue-tier only (Spec §5.4 / Phase 3).
                if not _contract_activation_for_business_oracle(scenario):
                    return OracleResult(
                        False, "IdempotencyOracle", "L5", "non_idempotent_heuristic",
                        f"重复{method} {path} 应返回幂等响应(409/相同结果)",
                        f"{len(group)}次请求均返回成功", "P2", 0.45,
                        explanation="http_status_heuristic_insufficient_for_customer_delivery",
                        oracle_tier="internal_clue",
                        customer_deliverable=False,
                        demotion_reason="heuristic_idempotency_without_contract_effect",
                    )
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
                if not _contract_activation_for_business_oracle(scenario):
                    return OracleResult(
                        False, "ConcurrencyOracle", "L5", "race_condition_heuristic",
                        "并发操作应互斥", f"{len(successes)}次成功", "P2", 0.45,
                        explanation="http_status_heuristic_insufficient_for_customer_delivery",
                        oracle_tier="internal_clue",
                        customer_deliverable=False,
                        demotion_reason="heuristic_concurrency_without_contract_effect",
                    )
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
            DataIntegrityOracle(), ConsistencyOracle(), VisibilityOracle(), TransactionOracle(), CacheConsistencyOracle(),
            # L3
            MoneyOracle(), InventoryOracle(), StateOracle(), WorkflowOracle(), QuotaOracle(), TemporalOracle(), CouponOracle(),
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
        # Industry-shaped categories (money/inventory/coupon) are not auto-mapped.
        # Those oracles attach only from explicit source oracle_rules.
        mapping = {
            "state_machine": ["StateOracle", "WorkflowOracle"],
            "permission": ["PermissionOracle", "TenantIsolationOracle"],
            "isolation": ["TenantIsolationOracle", "PermissionOracle"],
            "concurrency": ["ConcurrencyOracle", "IdempotencyOracle"],
            "invariant": ["ConsistencyOracle", "DataIntegrityOracle", "VisibilityOracle"],
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
        for name in ("StateOracle", "ConsistencyOracle", "DataIntegrityOracle", "VisibilityOracle", "ErrorCodeOracle"):
            o = self._oracles.get(name)
            if o and o not in matched: matched.append(o)
        return matched

    def get_for_scenario(self, scenario: dict[str, Any]) -> list[BaseOracle]:
        """Select oracles from category + explicit source rules only.

        Path tokens, entity labels, and industry tags must never auto-attach
        domain oracles (Money/Inventory/Coupon). Customer delivery uses
        contract_oracles on the experiment mainline; this registry is diagnostic.
        """
        category = str(scenario.get("category") or "").strip().lower()
        oracles = self.get_for_category(category)
        seen = {o.name for o in oracles}
        for rule in scenario.get("oracle_rules") or []:
            oracle_name = str(rule or "").split(".", 1)[0].strip()
            oracle = self._oracles.get(oracle_name)
            if oracle and oracle.name not in seen:
                oracles.append(oracle)
                seen.add(oracle.name)
        return oracles

    def auto_detect(self, prd_text: str, limit: int = 15) -> list[BaseOracle]:
        """Source-grounded detection only — no industry keyword scoring."""
        from .oracle_dsl import DSLParser, DSLCompiler

        result: list[BaseOracle] = []
        parser = DSLParser()
        compiler = DSLCompiler()
        dsl_rules = parser.parse_prd(prd_text)
        if dsl_rules:
            dsl_oracle_families: set[str] = set()
            for rule in dsl_rules:
                compiled = compiler.compile_to_oracle_object(rule)
                dsl_oracle_families.add(compiled.oracle_family)
            for o in self._oracles.values():
                o_family = getattr(o, "oracle_family", "")
                if o_family in dsl_oracle_families and o not in result:
                    result.append(o)
                    if len(result) >= max(0, int(limit)):
                        break
        return self.get_by_layer("L1") + result

    def get_all_names(self) -> list[str]:
        return list(self._oracles.keys())


# ═══════════════════════════════════════════════════
# Oracle Engine
# ═══════════════════════════════════════════════════

class OracleEngine:
    """Diagnostic multi-oracle stack — not the customer-delivery authority.

    Product experiment findings are scored by ``contract_oracles`` on the
    discovery mainline. This engine must never invent industry oracles from
    path/entity/domain heuristics.
    """

    def __init__(self):
        self.registry = OracleRegistry()

    def evaluate(self, scenario: dict, trace: dict, snapshots: Any = None) -> list[OracleResult]:
        results = []
        oracles = self.registry.get_for_scenario(scenario)
        for oracle in oracles:
            try:
                results.append(oracle.evaluate(scenario, trace, snapshots))
            except Exception as e:
                _log.warning("Oracle %s crashed on scenario %s: %s",
                    oracle.name, scenario.get("title", "?")[:80], e)
                # Generate a failed result so the crash is visible in the output
                results.append(OracleResult(
                    passed=False, oracle_name=oracle.name, layer=oracle.layer,
                    violated_rule="",
                    expected="Oracle 正常执行",
                    actual=f"Oracle 崩溃: {type(e).__name__}: {str(e)[:200]}",
                    severity="", confidence=0.0,
                    explanation="Oracle 执行崩溃：属诊断引擎内部错误，不是业务违规发现；is_finding=False，不应计入已确认 finding",
                    is_finding=False,
                ))
        if _ORACLE_EVALUATE_HOOK is not None:
            results = list(_ORACLE_EVALUATE_HOOK(self, scenario, trace, snapshots, results) or results)
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
                        violated_rule="",
                        expected="Oracle 正常执行",
                        actual=f"Oracle 崩溃: {type(e).__name__}: {str(e)[:200]}",
                        severity="", confidence=0.0,
                        explanation="Oracle 执行崩溃：属诊断引擎内部错误，不是业务违规发现；is_finding=False，不应计入已确认 finding",
                        is_finding=False,
                    ))
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
        if _EVIDENCE_SCENARIO_HOOK is not None:
            scenario = _EVIDENCE_SCENARIO_HOOK(scenario, trace, snapshots, oracle_results)
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
        # 主链 7: a reproducible evidence chain needs a STABLE id. Derive it from
        # the defect signature (scenario id + behavior slice + sorted violated
        # rules) so the SAME defect yields the SAME evidence_id across reruns —
        # enabling dedup and reproducible retrieval (主链 9 regression / 主链 8
        # frontend). Fall back to a random id only when no stable signature exists.
        violated_rules = sorted(
            str(r.violated_rule) for r in oracle_results if not r.passed and str(r.violated_rule).strip()
        )
        _sig = "|".join([
            str(scenario.get("id") or ""),
            str(scenario.get("behavior_slice_id") or ""),
            *violated_rules,
        ])
        evidence_id = (
            "EVID_" + hashlib.sha1(_sig.encode("utf-8")).hexdigest()[:16]
            if _sig.strip("|") else "EVID_" + uuid.uuid4().hex[:16]
        )
        return BugEvidenceGraph(
            bug_id=bug_id, title=scenario.get("title", ""), scenario=scenario,
            execution_trace=trace,
            before_snapshot=getattr(snapshots, "before", None).__dict__ if snapshots and hasattr(snapshots, "before") else {},
            after_snapshot=getattr(snapshots, "after", None).__dict__ if snapshots and hasattr(snapshots, "after") else {},
            oracle_results=oracle_results,
            reproduction_steps="\n".join(repro),
            severity=worst.severity, confidence=round(agg_confidence, 2),
            evidence_id=evidence_id, layers_triggered=layers,
            vote_summary={
                "total_votes": len(oracle_results),
                "failed_votes": sum(1 for r in oracle_results if not r.passed),
                "passed_votes": sum(1 for r in oracle_results if r.passed),
                "failure_weight": round(failure_weight, 2),
                "total_weight": round(total_weight, 2),
                "confirmation_threshold_met": failure_weight / total_weight >= 0.5 if total_weight > 0 else False,
            },
        )
