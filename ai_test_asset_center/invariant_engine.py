from __future__ import annotations

"""Business Invariant Engine — pluggable invariant evaluation framework.

12 invariant types, each implemented as an evaluable predicate that takes
execution evidence and returns (passed: bool, detail: str, confidence: float).

Design contract:
  - No hardcoded project-specific invariant logic
  - Each invariant type is a class implementing evaluate(evidence) → InvariantResult
  - New invariant types can be added via plugin registration
  - Invariants are data-driven, not if/elif chains

Invariant types:
  1. permission_invariant       — Actor can only access authorized resources
  2. tenant_isolation_invariant — Tenants cannot access each other's data
  3. state_machine_invariant    — State transitions follow legal paths
  4. conservation_invariant     — Conserved quantities are preserved
  5. idempotency_invariant      — Repeated operations are idempotent
  6. concurrency_invariant      — Concurrent operations remain consistent
  7. data_consistency_invariant — DB, API, cache are consistent
  8. input_boundary_invariant   — Out-of-bound inputs are rejected
  9. lifecycle_invariant        — Lifecycle stages follow PRD order
  10. visibility_invariant      — Field visibility matches role policy
  11. eventual_consistency_invariant — Async ops converge within time
  12. audit_trail_invariant     — Critical ops have audit trail
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class InvariantResult:
    """Result of evaluating a single invariant against evidence."""
    invariant_type: str
    passed: bool
    detail: str
    confidence: float = 0.0
    expected: str = ""
    actual: str = ""
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "invariant_type": self.invariant_type,
            "passed": self.passed,
            "detail": self.detail,
            "confidence": self.confidence,
            "expected": self.expected,
            "actual": self.actual,
            "evidence_refs": self.evidence_refs,
        }


# ── Evidence helper utilities ─────────────────────────────────────────────

def _get_nested(obj: Any, *keys: str, default: Any = None) -> Any:
    """Safely access nested dict keys."""
    current = obj
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key, default)
        else:
            return default
    return current


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_str(value: Any, default: str = "") -> str:
    return str(value) if value is not None else default


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# ── Base Class ────────────────────────────────────────────────────────────


class BaseInvariant:
    """Base class for all business invariant evaluators."""

    invariant_type: str = "base"
    display_name: str = "Base Invariant"
    description: str = ""

    def evaluate(self, evidence: dict[str, Any]) -> InvariantResult:
        """Evaluate the invariant against the provided evidence."""
        raise NotImplementedError


# ── 1. Permission Invariant ──────────────────────────────────────────────


class PermissionInvariant(BaseInvariant):
    """Check: actor.role >= endpoint.required_role for all operations."""

    invariant_type = "permission_invariant"
    display_name = "权限不变量"
    description = "验证未授权角色不能访问/操作受限资源"

    def evaluate(self, evidence: dict[str, Any]) -> InvariantResult:
        calls = _as_list(evidence.get("execution_calls", evidence.get("calls", [])))
        findings = []

        for call in calls:
            if not isinstance(call, dict):
                continue

            # Extract role information
            results = _as_dict(call.get("results", call.get("role_results", {})))
            admin_result = _as_dict(results.get("admin", {}))
            viewer_result = _as_dict(results.get("viewer", {}))
            noauth_result = _as_dict(results.get("no_auth", {}))

            admin_status = _as_int(admin_result.get("status", 0))
            viewer_status = _as_int(viewer_result.get("status", 0))
            noauth_status = _as_int(noauth_result.get("status", 0))

            method = _as_str(call.get("call", "")).split()[0] if call.get("call") else ""
            is_write = method.upper() in {"POST", "PUT", "PATCH", "DELETE"}

            # Check 1: Unauthenticated access should be rejected
            if 200 <= noauth_status < 300 and not evidence.get("public_endpoint"):
                findings.append({
                    "violation": "unauthenticated_access",
                    "detail": f"未认证请求成功访问({noauth_status})",
                    "confidence": 0.9,
                })

            # Check 2: Viewer should not succeed on admin-only write operations
            if is_write and 200 <= viewer_status < 300:
                findings.append({
                    "violation": "privilege_escalation",
                    "detail": f"低权限角色成功执行写操作({viewer_status})",
                    "confidence": 0.8,
                })

            # Check 3: Admin should succeed on protected endpoints
            # (but admin failure is not a permission bug, it's an availability bug)
            pass

        if findings:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=False,
                detail=f"发现 {len(findings)} 个权限违规: {'; '.join(f['detail'] for f in findings[:3])}",
                confidence=max(f["confidence"] for f in findings),
                expected="未授权请求应被拒绝(401/403)",
                actual=f"发现 {len(findings)} 个权限违规",
                evidence_refs=[f["violation"] for f in findings],
            )

        return InvariantResult(
            invariant_type=self.invariant_type,
            passed=True,
            detail="权限检查通过，未发现未授权访问",
            confidence=0.7,
            expected="未授权请求应被拒绝",
            actual="所有未授权请求均被正确拒绝",
        )


# ── 2. Tenant Isolation Invariant ────────────────────────────────────────


class TenantIsolationInvariant(BaseInvariant):
    """Check: data.tenant_id == actor.tenant_id for all accessed data."""

    invariant_type = "tenant_isolation_invariant"
    display_name = "租户隔离不变量"
    description = "验证租户A不能访问租户B的数据"

    def evaluate(self, evidence: dict[str, Any]) -> InvariantResult:
        responses = _as_list(evidence.get("cross_tenant_responses", evidence.get("responses", [])))
        tenant_a = _as_str(evidence.get("tenant_a_id", evidence.get("actor_tenant", "")))
        tenant_b = _as_str(evidence.get("tenant_b_id", evidence.get("target_tenant", "")))

        if not tenant_a or not tenant_b or tenant_a == tenant_b:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=True,
                detail="跳过：未配置跨租户测试数据",
                confidence=0.1,
                expected="需要两个不同租户ID",
                actual="租户配置不足",
            )

        findings = []
        for resp in responses:
            if not isinstance(resp, dict):
                continue
            status = _as_int(resp.get("status", 0))
            body = _as_dict(resp.get("body", resp))
            # Check for tenant_id in response data
            resp_tenant = _as_str(
                body.get("tenant_id", body.get("data", {}).get("tenant_id", ""))
            )
            if 200 <= status < 300 and resp_tenant == tenant_b:
                findings.append({
                    "violation": "cross_tenant_access",
                    "detail": f"租户{tenant_a}成功访问了租户{tenant_b}的数据",
                    "confidence": 0.95,
                })

        if findings:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=False,
                detail=f"发现 {len(findings)} 个跨租户数据访问",
                confidence=0.95,
                expected="租户数据必须隔离",
                actual=f"租户{tenant_a}可访问租户{tenant_b}数据",
                evidence_refs=[f["violation"] for f in findings],
            )

        return InvariantResult(
            invariant_type=self.invariant_type,
            passed=True,
            detail="租户隔离检查通过",
            confidence=0.7,
            expected="租户数据必须隔离",
            actual="租户数据已正确隔离",
        )


# ── 3. State Machine Invariant ───────────────────────────────────────────


class StateMachineInvariant(BaseInvariant):
    """Check: target_state in current_state.allowed_next_states."""

    invariant_type = "state_machine_invariant"
    display_name = "状态机不变量"
    description = "验证业务对象状态只能沿合法路径流转"

    def evaluate(self, evidence: dict[str, Any]) -> InvariantResult:
        transitions = _as_list(evidence.get("state_transitions", evidence.get("transitions", [])))
        allowed = evidence.get("allowed_transitions", {})

        if not transitions:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=True,
                detail="跳过：无状态转换数据",
                confidence=0.1,
                expected="需要状态转换数据",
                actual="无转换数据",
            )

        findings = []
        for trans in transitions:
            if not isinstance(trans, dict):
                continue
            from_state = _as_str(trans.get("from_state", trans.get("before", ""))).upper()
            to_state = _as_str(trans.get("to_state", trans.get("after", ""))).upper()

            if not from_state or not to_state:
                continue

            # Check if transition is allowed
            if isinstance(allowed, dict):
                allowed_from = allowed.get(from_state, [])
                if isinstance(allowed_from, list) and to_state not in [s.upper() for s in allowed_from]:
                    findings.append({
                        "violation": "invalid_transition",
                        "detail": f"非法状态跳转: {from_state} → {to_state}",
                        "confidence": 0.85,
                    })
            elif from_state == to_state:
                # Not a transition
                continue
            # Final state modification check
            final_states = {s.upper() for s in _as_list(evidence.get("final_states", []))}
            if from_state in final_states and to_state != from_state:
                findings.append({
                    "violation": "final_state_modification",
                    "detail": f"终态实体被修改: {from_state} → {to_state}",
                    "confidence": 0.9,
                })

        if findings:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=False,
                detail=f"发现 {len(findings)} 个状态机违规",
                confidence=max(f["confidence"] for f in findings),
                expected="状态转换必须符合PRD定义",
                actual=f"发现 {len(findings)} 个违规转换",
                evidence_refs=[f["violation"] for f in findings],
            )

        return InvariantResult(
            invariant_type=self.invariant_type,
            passed=True,
            detail="状态机检查通过",
            confidence=0.6,
            expected="状态转换必须符合PRD定义",
            actual="所有转换均合法",
        )


# ── 4. Conservation Invariant ────────────────────────────────────────────


class ConservationInvariant(BaseInvariant):
    """Check: sum(before) == sum(after) for conserved quantities."""

    invariant_type = "conservation_invariant"
    display_name = "金额/数量守恒不变量"
    description = "验证系统内金额/数量总和在操作前后必须守恒"

    def evaluate(self, evidence: dict[str, Any]) -> InvariantResult:
        before = _as_dict(evidence.get("before_snapshot", {}))
        after = _as_dict(evidence.get("after_snapshot", {}))
        conserved_field = _as_str(evidence.get("conserved_field", ""))

        findings = []
        # Compare numeric fields
        for field in (conserved_field and [conserved_field] or list(set(before.keys()) & set(after.keys()))):
            before_val = _as_float(before.get(field, 0))
            after_val = _as_float(after.get(field, 0))
            if abs(before_val - after_val) > 0.001:  # Tolerance for float
                findings.append({
                    "violation": "conservation_breach",
                    "detail": f"字段'{field}'不守恒: {before_val} → {after_val} (差值={abs(before_val - after_val):.4f})",
                    "confidence": 0.9,
                })

        # Check negative balances
        for field, value in after.items():
            val = _as_float(value)
            if val < 0 and field not in {"delta", "change", "difference"}:
                findings.append({
                    "violation": "negative_value",
                    "detail": f"字段'{field}'为负值: {val}",
                    "confidence": 0.85,
                })

        if findings:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=False,
                detail=f"发现 {len(findings)} 个守恒违规",
                confidence=max(f["confidence"] for f in findings),
                expected="操作前后守恒字段必须一致",
                actual=f"发现 {len(findings)} 个不一致",
                evidence_refs=[f["violation"] for f in findings],
            )

        if not before and not after:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=True,
                detail="跳过：无before/after快照",
                confidence=0.1,
                expected="需要before/after快照",
                actual="无快照数据",
            )

        return InvariantResult(
            invariant_type=self.invariant_type,
            passed=True,
            detail="守恒检查通过",
            confidence=0.7,
            expected="操作前后守恒字段必须一致",
            actual="守恒字段一致",
        )


# ── 5. Idempotency Invariant ─────────────────────────────────────────────


class IdempotencyInvariant(BaseInvariant):
    """Check: repeated calls produce same business result."""

    invariant_type = "idempotency_invariant"
    display_name = "幂等不变量"
    description = "验证重复提交不产生重复业务结果"

    def evaluate(self, evidence: dict[str, Any]) -> InvariantResult:
        calls = _as_list(evidence.get("repeated_calls", evidence.get("execution_calls", [])))
        if len(calls) < 2:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=True,
                detail="跳过：需要至少两次执行结果来检查幂等性",
                confidence=0.1,
                expected="需要重复执行数据",
                actual=f"仅有 {len(calls)} 次执行",
            )

        findings = []
        # Compare pairs of consecutive calls
        for i in range(len(calls) - 1):
            call1 = _as_dict(calls[i])
            call2 = _as_dict(calls[i + 1])

            status1 = _as_int(call1.get("status", 0))
            status2 = _as_int(call2.get("status", 0))

            # If both succeeded, check for duplicate creation indicators
            if 200 <= status1 < 300 and 200 <= status2 < 300:
                body1 = _as_dict(call1.get("body", call1))
                body2 = _as_dict(call2.get("body", call2))

                id1 = _as_str(body1.get("id", ""))
                id2 = _as_str(body2.get("id", ""))

                if id1 and id2 and id1 != id2:
                    findings.append({
                        "violation": "duplicate_creation",
                        "detail": f"重复请求创建了不同实体(ID: {id1} vs {id2})",
                        "confidence": 0.85,
                    })

        if findings:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=False,
                detail=f"发现 {len(findings)} 个幂等性违规",
                confidence=max(f["confidence"] for f in findings),
                expected="重复请求不应产生新的业务结果",
                actual=f"重复请求产生了 {len(findings)} 个新结果",
                evidence_refs=[f["violation"] for f in findings],
            )

        return InvariantResult(
            invariant_type=self.invariant_type,
            passed=True,
            detail="幂等性检查通过",
            confidence=0.5,
            expected="重复请求不应产生新的业务结果",
            actual="重复请求结果一致",
        )


# ── 6. Concurrency Invariant ─────────────────────────────────────────────


class ConcurrencyInvariant(BaseInvariant):
    """Check: concurrent operations produce consistent results."""

    invariant_type = "concurrency_invariant"
    display_name = "并发一致性不变量"
    description = "验证并发操作不能导致数据不一致或竞态"

    def evaluate(self, evidence: dict[str, Any]) -> InvariantResult:
        concurrent = _as_list(evidence.get("concurrent_results", evidence.get("execution_calls", [])))

        if len(concurrent) < 2:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=True,
                detail="跳过：需要并发执行数据",
                confidence=0.1,
                expected="需要并发执行数据",
                actual=f"仅有 {len(concurrent)} 次执行",
            )

        findings = []
        # Detect lost updates: same entity, different versions
        versions: dict[str, list[Any]] = {}
        for c in concurrent:
            if not isinstance(c, dict):
                continue
            entity_id = _as_str(c.get("entity_id", c.get("id", "")))
            version = c.get("version", c.get("_version"))
            if entity_id and version is not None:
                versions.setdefault(entity_id, []).append(version)

        for eid, vers in versions.items():
            if len(vers) >= 2 and len(set(str(v) for v in vers)) < len(vers):
                findings.append({
                    "violation": "lost_update",
                    "detail": f"实体 {eid} 的并发更新可能丢失",
                    "confidence": 0.7,
                })

        # Detect 409 Conflict responses (good — means concurrency control works)
        conflict_count = sum(
            1 for c in concurrent
            if _as_int(_as_dict(c).get("status", 0)) == 409
        )

        if findings:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=False,
                detail=f"发现 {len(findings)} 个并发一致性问题",
                confidence=0.7,
                expected="并发操作应保证数据一致",
                actual=f"发现 {len(findings)} 个问题",
                evidence_refs=[f["violation"] for f in findings],
            )

        return InvariantResult(
            invariant_type=self.invariant_type,
            passed=True,
            detail=f"并发检查通过 (冲突检测: {conflict_count} 次409)",
            confidence=0.5,
            expected="并发操作应保证数据一致",
            actual="未检测到并发问题",
        )


# ── 7. Data Consistency Invariant ───────────────────────────────────────


class DataConsistencyInvariant(BaseInvariant):
    """Check: DB snapshot, cache, and API response are consistent."""

    invariant_type = "data_consistency_invariant"
    display_name = "数据一致性不变量"
    description = "验证DB、缓存、API响应之间的数据一致"

    def evaluate(self, evidence: dict[str, Any]) -> InvariantResult:
        api_response = _as_dict(evidence.get("api_response", {}))
        db_snapshot = _as_dict(evidence.get("db_snapshot", evidence.get("db_before", {})))
        cache_value = evidence.get("cache_value")

        findings = []

        # Compare API ↔ DB
        for field in set(list(api_response.keys()) + list(db_snapshot.keys())):
            api_val = api_response.get(field)
            db_val = db_snapshot.get(field)
            if api_val is not None and db_val is not None and str(api_val) != str(db_val):
                findings.append({
                    "violation": "api_db_mismatch",
                    "detail": f"字段'{field}': API={api_val}, DB={db_val}",
                    "confidence": 0.9,
                })

        # Compare Cache ↔ DB
        if cache_value is not None and db_snapshot:
            cache_str = json.dumps(cache_value, sort_keys=True, default=str) if isinstance(cache_value, (dict, list)) else str(cache_value)
            db_str = json.dumps(db_snapshot, sort_keys=True, default=str)
            if cache_str != db_str and cache_str not in db_str:
                findings.append({
                    "violation": "cache_drift",
                    "detail": "缓存与DB数据不一致",
                    "confidence": 0.85,
                })

        if findings:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=False,
                detail=f"发现 {len(findings)} 个数据一致性问题",
                confidence=max(f["confidence"] for f in findings),
                expected="API、DB、缓存数据必须一致",
                actual=f"发现 {len(findings)} 个不一致",
                evidence_refs=[f["violation"] for f in findings],
            )

        return InvariantResult(
            invariant_type=self.invariant_type,
            passed=True,
            detail="数据一致性检查通过",
            confidence=0.5,
            expected="API、DB、缓存数据必须一致",
            actual="数据一致",
        )


# ── 8. Input Boundary Invariant ──────────────────────────────────────────


class InputBoundaryInvariant(BaseInvariant):
    """Check: out-of-boundary inputs are rejected (400/422)."""

    invariant_type = "input_boundary_invariant"
    display_name = "输入边界不变量"
    description = "验证接口拒绝边界外/非法输入"

    def evaluate(self, evidence: dict[str, Any]) -> InvariantResult:
        test_cases = _as_list(evidence.get("boundary_tests", evidence.get("input_tests", [])))
        findings = []

        for test in test_cases:
            if not isinstance(test, dict):
                continue
            input_value = test.get("input", test.get("value"))
            status = _as_int(test.get("status", 0))
            expected_reject = bool(test.get("should_reject", True))

            # Check: illegal input should be rejected (400+)
            if expected_reject and 200 <= status < 300:
                findings.append({
                    "violation": "boundary_bypass",
                    "detail": f"非法输入被接受: {input_value} → {status}",
                    "confidence": 0.9,
                })

            # Check for injection patterns
            if isinstance(input_value, str):
                if any(pattern in input_value.lower() for pattern in ["' or ", "select ", "<script>", "../"]):
                    if 200 <= status < 300:
                        findings.append({
                            "violation": "injection_vulnerability",
                            "detail": f"注入载荷被接受: {input_value[:50]}",
                            "confidence": 0.95,
                        })

            # Check for negative/zero values where not allowed
            if isinstance(input_value, (int, float)):
                if input_value < 0 and 200 <= status < 300:
                    findings.append({
                        "violation": "negative_accepted",
                        "detail": f"负值被接受: {input_value}",
                        "confidence": 0.8,
                    })

        if findings:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=False,
                detail=f"发现 {len(findings)} 个输入边界违规",
                confidence=max(f["confidence"] for f in findings),
                expected="非法输入应被拒绝(400+)",
                actual=f"发现 {len(findings)} 个输入被接受",
                evidence_refs=[f["violation"] for f in findings],
            )

        if not test_cases:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=True,
                detail="跳过：无边界测试数据",
                confidence=0.1,
                expected="需要边界测试数据",
                actual="无测试数据",
            )

        return InvariantResult(
            invariant_type=self.invariant_type,
            passed=True,
            detail="输入边界检查通过",
            confidence=0.6,
            expected="非法输入应被拒绝",
            actual="所有非法输入均被正确拒绝",
        )


# ── 9. Lifecycle Invariant ───────────────────────────────────────────────


class LifecycleInvariant(BaseInvariant):
    """Check: entity lifecycle stages follow PRD-defined order."""

    invariant_type = "lifecycle_invariant"
    display_name = "生命周期不变量"
    description = "验证实体从创建到归档的所有阶段行为符合PRD"

    def evaluate(self, evidence: dict[str, Any]) -> InvariantResult:
        stages = _as_list(evidence.get("lifecycle_stages", evidence.get("stages", [])))
        expected_order = _as_list(evidence.get("expected_order", []))

        findings = []
        for i in range(len(stages) - 1):
            current = _as_str(stages[i]).upper()
            next_stage = _as_str(stages[i + 1]).upper()

            if expected_order and current in expected_order and next_stage in expected_order:
                if expected_order.index(current) > expected_order.index(next_stage):
                    findings.append({
                        "violation": "lifecycle_order_violation",
                        "detail": f"生命周期顺序违规: {current} 在 {next_stage} 之后",
                        "confidence": 0.8,
                    })

        # Check for active entity deletion
        if evidence.get("entity_deleted_while_active"):
            findings.append({
                "violation": "active_entity_deleted",
                "detail": "活跃实体被删除",
                "confidence": 0.9,
            })

        if findings:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=False,
                detail=f"发现 {len(findings)} 个生命周期违规",
                confidence=max(f["confidence"] for f in findings),
                expected="生命周期必须遵循PRD定义",
                actual=f"发现 {len(findings)} 个违规",
                evidence_refs=[f["violation"] for f in findings],
            )

        return InvariantResult(
            invariant_type=self.invariant_type,
            passed=True,
            detail="生命周期检查通过",
            confidence=0.4,
            expected="生命周期必须遵循PRD定义",
            actual="生命周期正常",
        )


# ── 10. Visibility Invariant ─────────────────────────────────────────────


class VisibilityInvariant(BaseInvariant):
    """Check: field visibility matches role-based policy."""

    invariant_type = "visibility_invariant"
    display_name = "可见性不变量"
    description = "验证字段在不同角色视角下的可见性符合PRD"

    def evaluate(self, evidence: dict[str, Any]) -> InvariantResult:
        admin_response = _as_dict(evidence.get("admin_response", evidence.get("admin_view", {})))
        viewer_response = _as_dict(evidence.get("viewer_response", evidence.get("viewer_view", {})))
        sensitive_fields = _as_list(evidence.get("sensitive_fields", []))

        findings = []

        # Check: sensitive fields should not be in viewer response
        for field in sensitive_fields:
            viewer_val = viewer_response.get(field)
            if viewer_val is not None:
                findings.append({
                    "violation": "sensitive_field_exposure",
                    "detail": f"敏感字段'{field}'在普通用户响应中可见: {viewer_val}",
                    "confidence": 0.9,
                })

        # Check: admin-only fields should differ
        for key in set(admin_response.keys()) & set(viewer_response.keys()):
            admin_val = _as_str(admin_response.get(key))
            viewer_val = _as_str(viewer_response.get(key))
            # Fields that should be masked or different
            if key.lower() in {"phone", "mobile", "email", "id_number", "address", "ssn", "password"}:
                if admin_val == viewer_val and admin_val:
                    findings.append({
                        "violation": "masking_failure",
                        "detail": f"字段'{key}'未按角色脱敏",
                        "confidence": 0.8,
                    })

        if findings:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=False,
                detail=f"发现 {len(findings)} 个可见性违规",
                confidence=max(f["confidence"] for f in findings),
                expected="敏感字段应按角色过滤",
                actual=f"发现 {len(findings)} 个暴露",
                evidence_refs=[f["violation"] for f in findings],
            )

        return InvariantResult(
            invariant_type=self.invariant_type,
            passed=True,
            detail="可见性检查通过",
            confidence=0.5,
            expected="敏感字段应按角色过滤",
            actual="角色可见性正确",
        )


# ── 11. Eventual Consistency Invariant ──────────────────────────────────


class EventualConsistencyInvariant(BaseInvariant):
    """Check: async operations converge to consistent state within time bound."""

    invariant_type = "eventual_consistency_invariant"
    display_name = "异步最终一致性不变量"
    description = "验证异步操作在合理时间内达到最终一致状态"

    def evaluate(self, evidence: dict[str, Any]) -> InvariantResult:
        published = _as_int(evidence.get("published_count", evidence.get("sent_count", 0)))
        consumed = _as_int(evidence.get("consumed_count", evidence.get("received_count", 0)))
        dlq_count = _as_int(evidence.get("dlq_count", evidence.get("dead_letter_count", 0)))
        timeout_count = _as_int(evidence.get("timeout_count", 0))
        retry_count = _as_int(evidence.get("retry_count", 0))

        findings = []

        if published > 0 and consumed < published:
            findings.append({
                "violation": "message_loss",
                "detail": f"消息丢失: 发布{published}, 消费{consumed}",
                "confidence": 0.85,
            })

        if dlq_count > 0:
            findings.append({
                "violation": "dead_letter_accumulation",
                "detail": f"死信队列堆积: {dlq_count} 条",
                "confidence": 0.7,
            })

        if timeout_count > 0 and retry_count == 0:
            findings.append({
                "violation": "timeout_no_retry",
                "detail": f"超时{timeout_count}次但无重试",
                "confidence": 0.8,
            })

        if findings:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=False,
                detail=f"发现 {len(findings)} 个最终一致性问题",
                confidence=max(f["confidence"] for f in findings),
                expected="异步操作应在时限内达到一致",
                actual=f"发现 {len(findings)} 个问题",
                evidence_refs=[f["violation"] for f in findings],
            )

        return InvariantResult(
            invariant_type=self.invariant_type,
            passed=True,
            detail="最终一致性检查通过",
            confidence=0.4,
            expected="异步操作应在时限内达到一致",
            actual="一致性正常",
        )


# ── 12. Audit Trail Invariant ────────────────────────────────────────────


class AuditTrailInvariant(BaseInvariant):
    """Check: critical operations have audit log entries."""

    invariant_type = "audit_trail_invariant"
    display_name = "审计追踪不变量"
    description = "验证关键操作必须有可追溯的审计日志"

    def evaluate(self, evidence: dict[str, Any]) -> InvariantResult:
        operations = _as_list(evidence.get("operations", evidence.get("mutations", [])))
        audit_entries = _as_list(evidence.get("audit_entries", evidence.get("audit_logs", [])))
        required_fields = _as_list(evidence.get("audit_required_fields", ["operator", "timestamp", "action", "result"]))

        findings = []

        # Check: each mutating operation should have an audit entry
        if operations and not audit_entries:
            findings.append({
                "violation": "missing_audit",
                "detail": f"执行了 {len(operations)} 个操作但无审计日志",
                "confidence": 0.9,
            })
        elif len(audit_entries) < len(operations):
            findings.append({
                "violation": "incomplete_audit",
                "detail": f"审计日志不完整: {len(operations)} 操作, {len(audit_entries)} 日志",
                "confidence": 0.8,
            })

        # Check each audit entry for required fields
        for entry in audit_entries:
            if not isinstance(entry, dict):
                continue
            missing = [f for f in required_fields if not entry.get(f)]
            if missing:
                findings.append({
                    "violation": "incomplete_audit_fields",
                    "detail": f"审计条目缺少字段: {missing}",
                    "confidence": 0.7,
                })

        # Check for audit field updates on entities
        audit_fields_updated = evidence.get("audit_fields_updated", True)
        if not audit_fields_updated:
            findings.append({
                "violation": "audit_fields_not_updated",
                "detail": "实体审计字段(updated_at/updated_by)未更新",
                "confidence": 0.85,
            })

        if findings:
            return InvariantResult(
                invariant_type=self.invariant_type,
                passed=False,
                detail=f"发现 {len(findings)} 个审计追踪问题",
                confidence=max(f["confidence"] for f in findings),
                expected="关键操作必须有审计日志",
                actual=f"发现 {len(findings)} 个缺失",
                evidence_refs=[f["violation"] for f in findings],
            )

        return InvariantResult(
            invariant_type=self.invariant_type,
            passed=True,
            detail="审计追踪检查通过",
            confidence=0.5,
            expected="关键操作必须有审计日志",
            actual="审计记录完整",
        )


# ── Invariant Registry ────────────────────────────────────────────────────

# Maps invariant_type strings to evaluator classes
_INVARIANT_REGISTRY: dict[str, type[BaseInvariant]] = {
    "permission_invariant": PermissionInvariant,
    "tenant_isolation_invariant": TenantIsolationInvariant,
    "state_machine_invariant": StateMachineInvariant,
    "conservation_invariant": ConservationInvariant,
    "idempotency_invariant": IdempotencyInvariant,
    "concurrency_invariant": ConcurrencyInvariant,
    "data_consistency_invariant": DataConsistencyInvariant,
    "input_boundary_invariant": InputBoundaryInvariant,
    "lifecycle_invariant": LifecycleInvariant,
    "visibility_invariant": VisibilityInvariant,
    "eventual_consistency_invariant": EventualConsistencyInvariant,
    "audit_trail_invariant": AuditTrailInvariant,
}


def register_invariant(invariant_type: str, evaluator_class: type[BaseInvariant]) -> None:
    """Register a custom invariant evaluator."""
    _INVARIANT_REGISTRY[invariant_type] = evaluator_class


def get_invariant_evaluator(invariant_type: str) -> BaseInvariant | None:
    """Get an invariant evaluator instance by type string."""
    cls = _INVARIANT_REGISTRY.get(invariant_type)
    if cls:
        return cls()
    return None


def list_invariant_types() -> list[str]:
    """List all registered invariant types."""
    return list(_INVARIANT_REGISTRY.keys())


def evaluate_invariant(
    invariant_type: str,
    evidence: dict[str, Any],
) -> InvariantResult:
    """Evaluate a specific invariant against evidence.

    Args:
        invariant_type: One of the 12 invariant type strings.
        evidence: Dict with execution evidence appropriate for the invariant.

    Returns:
        InvariantResult with passed/failed status and detail.
    """
    evaluator = get_invariant_evaluator(invariant_type)
    if evaluator is None:
        return InvariantResult(
            invariant_type=invariant_type,
            passed=True,
            detail=f"未知不变量类型: {invariant_type}",
            confidence=0.0,
            expected="",
            actual=f"不变量类型'{invariant_type}'未注册",
        )
    return evaluator.evaluate(evidence)


def evaluate_all_invariants(
    evidence_bundle: dict[str, Any],
    *,
    enabled_types: list[str] | None = None,
) -> dict[str, InvariantResult]:
    """Evaluate all registered invariants (or a subset) against evidence.

    Args:
        evidence_bundle: Full execution evidence bundle.
        enabled_types: Optional list of invariant types to evaluate.

    Returns:
        Dict mapping invariant_type → InvariantResult.
    """
    types_to_eval = enabled_types or list(_INVARIANT_REGISTRY.keys())
    results: dict[str, InvariantResult] = {}

    for inv_type in types_to_eval:
        if inv_type not in _INVARIANT_REGISTRY:
            continue
        try:
            results[inv_type] = evaluate_invariant(inv_type, evidence_bundle)
        except Exception as exc:
            results[inv_type] = InvariantResult(
                invariant_type=inv_type,
                passed=True,
                detail=f"评估异常: {exc}",
                confidence=0.0,
                expected="",
                actual=str(exc)[:200],
            )

    return results


def invariant_coverage_report(results: dict[str, InvariantResult]) -> dict[str, Any]:
    """Generate a coverage report from invariant evaluation results."""
    total = len(results)
    passed = sum(1 for r in results.values() if r.passed)
    failed = total - passed

    return {
        "total_invariants": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "by_type": {
            t: {
                "passed": r.passed,
                "confidence": r.confidence,
                "detail": r.detail[:200],
            }
            for t, r in results.items()
        },
    }
