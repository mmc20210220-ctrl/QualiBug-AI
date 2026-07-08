from __future__ import annotations

"""Behavior Slice Generator — auto-generates testable behavior slices from context.

Takes extracted context (from context_extractor.py) and the bug ontology (from
bug_ontology_registry.py), then auto-generates behavior slices. Each slice is
a concrete, executable test scenario that checks a specific business invariant.

A behavior slice contains:
  - slice_id            — Unique hash-based identifier
  - risk_family         — Level-1 risk family
  - invariant           — The business invariant being checked
  - invariant_type      — Maps to invariant_engine evaluator
  - precondition        — What must be true before execution
  - actor               — Which role executes
  - action              — What operation (method + path)
  - target              — Endpoint/page/table under test
  - execution_plan      — Ordered list of execution steps
  - expected_result     — What should happen
  - assertion           — Machine-checkable assertion
  - evidence_requirements — Required evidence items
  - regression_probe    — Template for regression test

Design contract:
  - No hardcoded endpoint/entity/role lists
  - Driven entirely by extracted context + ontology entries
  - 100+ slices from a typical OpenAPI + PRD + DB schema
  - Each slice is traceable to its source (ontology entry + context item)
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BehaviorSlice:
    """A single executable test scenario generated from the ontology + context."""

    slice_id: str
    risk_family: str
    invariant: str
    invariant_type: str
    precondition: str
    actor: str
    action: str
    target: str
    execution_plan: list[str]
    expected_result: str
    assertion: str
    evidence_requirements: list[str]
    regression_probe: str
    # Traceability
    ontology_subtype: str = ""
    source_entity: str = ""
    source_endpoint: str = ""
    severity: str = "P2"
    confidence: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "slice_id": self.slice_id,
            "risk_family": self.risk_family,
            "invariant": self.invariant,
            "invariant_type": self.invariant_type,
            "precondition": self.precondition,
            "actor": self.actor,
            "action": self.action,
            "target": self.target,
            "execution_plan": self.execution_plan,
            "expected_result": self.expected_result,
            "assertion": self.assertion,
            "evidence_requirements": self.evidence_requirements,
            "regression_probe": self.regression_probe,
            "ontology_subtype": self.ontology_subtype,
            "source_entity": self.source_entity,
            "source_endpoint": self.source_endpoint,
            "severity": self.severity,
            "confidence": self.confidence,
        }


# ── Slice ID generator ────────────────────────────────────────────────────

def _make_slice_id(*parts: str) -> str:
    """Generate a stable, content-based slice ID."""
    key = "|".join(str(p) for p in parts if p)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ── Core Generator ────────────────────────────────────────────────────────


class BehaviorSliceGenerator:
    """Auto-generates behavior slices from context + ontology.

    Usage::

        from context_extractor import extract_context
        from bug_ontology_registry import get_ontology_registry

        ctx = extract_context(prd_text, api_spec_text)
        registry = get_ontology_registry()
        gen = BehaviorSliceGenerator(ctx, registry)
        slices = gen.generate()
        print(f"Generated {len(slices)} behavior slices")
    """

    def __init__(
        self,
        context: Any,  # ExtractedContext
        ontology: Any,  # BugOntologyRegistry
    ):
        self._ctx = context
        self._ontology = ontology
        self._slices: list[BehaviorSlice] = []

    def generate(self) -> list[BehaviorSlice]:
        """Generate all behavior slices."""
        self._slices = []

        self._generate_permission_slices()
        self._generate_tenant_isolation_slices()
        self._generate_state_machine_slices()
        self._generate_conservation_slices()
        self._generate_idempotency_slices()
        self._generate_concurrency_slices()
        self._generate_data_integrity_slices()
        self._generate_input_boundary_slices()
        self._generate_lifecycle_slices()
        self._generate_visibility_slices()
        self._generate_eventual_consistency_slices()
        self._generate_audit_trail_slices()

        return self._slices

    def count(self) -> int:
        return len(self._slices)

    # ── Slice generators per risk family ───────────────────────────────

    def _generate_permission_slices(self) -> None:
        """For each endpoint × role: check that unauthorized roles are rejected."""
        endpoints = getattr(self._ctx, "endpoints", []) or []
        roles = getattr(self._ctx, "roles", []) or []

        if not roles:
            roles = [{"name": "admin"}, {"name": "viewer"}, {"name": "anonymous"}]

        for ep in endpoints:
            path = ep.get("path", "")
            method = ep.get("method", "GET")
            entity = ep.get("tags", [""])[0] if ep.get("tags") else _entity_from_path(path)

            # Skip public/health endpoints
            if _is_public_path(path):
                continue

            for role in roles:
                role_name = role.get("name", role.get("role_id", "viewer"))
                if role_name in ("admin", "管理员"):
                    continue  # Admin should have access

                expected_status = "403" if method in ("POST", "PUT", "PATCH", "DELETE") else "403"
                slice_id = _make_slice_id("perm", entity, method, path, role_name)

                self._slices.append(BehaviorSlice(
                    slice_id=slice_id,
                    risk_family="authorization",
                    invariant=f"角色'{role_name}'不能未授权访问{method} {path}",
                    invariant_type="permission_invariant",
                    precondition=f"系统已配置{role_name}角色的token",
                    actor=role_name,
                    action=f"{method} {path}",
                    target=path,
                    execution_plan=[
                        f"step1: 以{role_name}身份登录获取token",
                        f"step2: 使用{role_name} token执行 {method} {path}",
                    ],
                    expected_result=f"HTTP {expected_status} Forbidden",
                    assertion=f"status_code in [401, 403]",
                    evidence_requirements=["request_raw", "response_raw", "token_role"],
                    regression_probe=f"curl -X {method} {path} -H 'Authorization: Bearer <{role_name}_token>'",
                    ontology_subtype="permission_bypass",
                    source_entity=entity,
                    source_endpoint=f"{method} {path}",
                    severity="P1",
                    confidence=0.8,
                ))

    def _generate_tenant_isolation_slices(self) -> None:
        """For each entity with tenant fields: check cross-tenant isolation."""
        endpoints = getattr(self._ctx, "endpoints", []) or []
        tenant_fields = getattr(self._ctx, "tenant_fields", []) or ["tenant_id"]

        for ep in endpoints:
            path = ep.get("path", "")
            method = ep.get("method", "GET")
            entity = ep.get("tags", [""])[0] if ep.get("tags") else _entity_from_path(path)

            if _is_public_path(path):
                continue

            has_tenant_param = any(
                p.get("name", "") in tenant_fields
                for p in (ep.get("parameters", []) or [])
            )

            slice_id = _make_slice_id("tenant", entity, method, path)

            self._slices.append(BehaviorSlice(
                slice_id=slice_id,
                risk_family="tenant_isolation",
                invariant=f"租户A的{entity}数据不能让租户B通过{method} {path}访问",
                invariant_type="tenant_isolation_invariant",
                precondition="系统有两个不同租户的账号(租户A和租户B)",
                actor="tenant_a",
                action=f"以租户A身份 {method} {path} (目标为租户B的数据)",
                target=path,
                execution_plan=[
                    "step1: 以租户A登录获取token",
                    "step2: 以租户B登录获取token并创建测试数据",
                    f"step3: 以租户A身份 {method} {path} (访问租户B的资源)",
                ],
                expected_result="HTTP 403 Forbidden",
                assertion="status_code == 403 and returned_data.tenant_id != 'tenant_b'",
                evidence_requirements=["request_raw", "response_raw", "tenant_a_token", "tenant_b_token"],
                regression_probe=f"curl -X {method} {path} -H 'Authorization: Bearer <tenant_a_token>'",
                ontology_subtype="cross_tenant_read" if method == "GET" else "cross_tenant_write",
                source_entity=entity,
                source_endpoint=f"{method} {path}",
                severity="P0",
                confidence=0.7,
            ))

    def _generate_state_machine_slices(self) -> None:
        """For each state transition pair: check illegal jump attempts."""
        states = getattr(self._ctx, "states", []) or []
        transitions = getattr(self._ctx, "transitions", []) or []
        endpoints = getattr(self._ctx, "endpoints", []) or []

        # Build allowed transitions set
        allowed: dict[str, set[str]] = {}
        for t in transitions:
            if isinstance(t, dict):
                f = t.get("from_state", "").upper()
                to = t.get("to_state", "").upper()
                if f and to:
                    allowed.setdefault(f, set()).add(to)

        # For write endpoints, generate invalid transition probes
        write_endpoints = [ep for ep in endpoints if ep.get("method") in ("PUT", "PATCH", "POST")]
        final_states = {"COMPLETED", "CANCELLED", "FAILED", "EXPIRED", "DELETED", "REFUNDED"}

        for ep in write_endpoints[:20]:  # Cap to prevent explosion
            path = ep.get("path", "")
            method = ep.get("method", "PUT")
            entity = ep.get("tags", [""])[0] if ep.get("tags") else _entity_from_path(path)

            slice_id = _make_slice_id("sm", entity, method, path)

            self._slices.append(BehaviorSlice(
                slice_id=slice_id,
                risk_family="state_machine",
                invariant=f"对已终态{entity}不能通过{method} {path}修改",
                invariant_type="state_machine_invariant",
                precondition=f"存在一个已完成/已取消的{entity}",
                actor="admin",
                action=f"{method} {path} (目标为终态实体)",
                target=path,
                execution_plan=[
                    f"step1: GET {path} 找到终态实体",
                    f"step2: {method} {path} 尝试修改终态实体",
                ],
                expected_result="HTTP 409 Conflict 或 400 Bad Request",
                assertion="status_code in [400, 409, 422]",
                evidence_requirements=["before_state", "response_raw", "final_state_check"],
                regression_probe=f"curl -X {method} {path} -d '{{\"status\": \"active\"}}'",
                ontology_subtype="final_state_modification",
                source_entity=entity,
                source_endpoint=f"{method} {path}",
                severity="P1",
                confidence=0.6,
            ))

    def _generate_conservation_slices(self) -> None:
        """For each endpoint with money/quantity fields: check conservation."""
        endpoints = getattr(self._ctx, "endpoints", []) or []
        money_fields = getattr(self._ctx, "money_fields", []) or ["amount", "price", "balance"]

        for ep in endpoints:
            method = ep.get("method", "")
            if method not in ("POST", "PUT", "PATCH"):
                continue
            path = ep.get("path", "")
            entity = ep.get("tags", [""])[0] if ep.get("tags") else _entity_from_path(path)

            if _is_auth_path(path):
                continue

            slice_id = _make_slice_id("cons", entity, method, path)

            self._slices.append(BehaviorSlice(
                slice_id=slice_id,
                risk_family="conservation",
                invariant=f"通过{method} {path}的操作前后，{entity}的金额/数量字段必须守恒",
                invariant_type="conservation_invariant",
                precondition=f"存在一个可修改的{entity}",
                actor="admin",
                action=f"{method} {path} (记录before/after快照)",
                target=path,
                execution_plan=[
                    f"step1: GET {path} 获取before快照",
                    f"step2: {method} {path} 执行修改操作",
                    f"step3: GET {path} 获取after快照",
                    "step4: 对比before/after中金额字段总和",
                ],
                expected_result="before.总金额 == after.总金额 (允许合理差值)",
                assertion="abs(sum(before.money_fields) - sum(after.money_fields)) < tolerance",
                evidence_requirements=["before_snapshot", "after_snapshot", "money_field_names"],
                regression_probe=f"curl -X {method} {path} -H 'Content-Type: application/json' -d '{{...}}'",
                ontology_subtype="money_leak",
                source_entity=entity,
                source_endpoint=f"{method} {path}",
                severity="P0",
                confidence=0.7,
            ))

    def _generate_idempotency_slices(self) -> None:
        """For each POST endpoint: check idempotency with duplicate requests."""
        endpoints = getattr(self._ctx, "endpoints", []) or []

        for ep in endpoints:
            method = ep.get("method", "")
            if method != "POST":
                continue
            path = ep.get("path", "")
            entity = ep.get("tags", [""])[0] if ep.get("tags") else _entity_from_path(path)

            if _is_auth_path(path):
                continue

            slice_id = _make_slice_id("idem", entity, method, path)

            self._slices.append(BehaviorSlice(
                slice_id=slice_id,
                risk_family="idempotency",
                invariant=f"对{method} {path}重复提交不应产生重复的{entity}",
                invariant_type="idempotency_invariant",
                precondition=f"系统支持{entity}的创建",
                actor="admin",
                action=f"连续两次 {method} {path} (相同数据)",
                target=path,
                execution_plan=[
                    f"step1: POST {path} 创建{entity} (记录响应ID)",
                    f"step2: POST {path} 使用相同数据再次创建",
                    "step3: 检查两次创建的ID是否不同或返回409",
                ],
                expected_result="第二次请求返回409 Conflict 或返回相同ID",
                assertion="response_2.id == response_1.id or status_code == 409",
                evidence_requirements=["request_1", "response_1", "request_2", "response_2"],
                regression_probe=f"curl -X POST {path} -d '{{...}}' (重复两次)",
                ontology_subtype="duplicate_create",
                source_entity=entity,
                source_endpoint=f"{method} {path}",
                severity="P1",
                confidence=0.7,
            ))

    def _generate_concurrency_slices(self) -> None:
        """For each write endpoint: generate concurrency race probes."""
        endpoints = getattr(self._ctx, "endpoints", []) or []

        write_endpoints = [ep for ep in endpoints if ep.get("method") in ("POST", "PUT", "PATCH")]
        for ep in write_endpoints[:15]:
            path = ep.get("path", "")
            method = ep.get("method", "PUT")
            entity = ep.get("tags", [""])[0] if ep.get("tags") else _entity_from_path(path)

            if _is_auth_path(path):
                continue

            slice_id = _make_slice_id("conc", entity, method, path)

            self._slices.append(BehaviorSlice(
                slice_id=slice_id,
                risk_family="concurrency",
                invariant=f"对{entity}的并发{method} {path}操作不能导致数据不一致",
                invariant_type="concurrency_invariant",
                precondition=f"存在一个可被并发修改的{entity}",
                actor="admin",
                action=f"同时发送两个 {method} {path} 请求",
                target=path,
                execution_plan=[
                    f"step1: GET {path} 获取{entity}当前状态",
                    f"step2: 同时发送两个 {method} {path} 修改同一{entity}",
                    f"step3: GET {path} 检查最终状态一致性",
                ],
                expected_result="后一个请求返回409 Conflict或数据一致性得到保证",
                assertion="final_state is consistent and not corrupted",
                evidence_requirements=["concurrent_responses", "final_state", "entity_version"],
                regression_probe=f"(并发执行) curl -X {method} {path} & curl -X {method} {path}",
                ontology_subtype="race_condition",
                source_entity=entity,
                source_endpoint=f"{method} {path}",
                severity="P1",
                confidence=0.5,
            ))

    def _generate_data_integrity_slices(self) -> None:
        """For each GET endpoint: cross-check with DB and cache."""
        endpoints = getattr(self._ctx, "endpoints", []) or []

        for ep in endpoints:
            if ep.get("method") != "GET":
                continue
            path = ep.get("path", "")
            entity = ep.get("tags", [""])[0] if ep.get("tags") else _entity_from_path(path)

            if _is_public_path(path):
                continue
            if "{" in path:  # Detail endpoints
                slice_id = _make_slice_id("di", entity, "GET", path)
                self._slices.append(BehaviorSlice(
                    slice_id=slice_id,
                    risk_family="data_integrity",
                    invariant=f"{entity}的API响应必须与DB数据一致",
                    invariant_type="data_consistency_invariant",
                    precondition=f"存在一个{entity}实例且可访问DB",
                    actor="admin",
                    action=f"GET {path} 并对比DB查询",
                    target=path,
                    execution_plan=[
                        f"step1: GET {path} 获取API响应",
                        f"step2: SELECT * FROM {entity} WHERE id=...",
                        "step3: 逐字段对比API与DB",
                    ],
                    expected_result="API响应字段与DB记录一致",
                    assertion="api_response.fields == db_record.fields",
                    evidence_requirements=["api_response", "db_snapshot", "entity_id"],
                    regression_probe=f"curl {path} | diff - <(db_query)",
                    ontology_subtype="api_db_mismatch",
                    source_entity=entity,
                    source_endpoint=f"GET {path}",
                    severity="P1",
                    confidence=0.6,
                ))

    def _generate_input_boundary_slices(self) -> None:
        """For each write endpoint: generate boundary value tests."""
        endpoints = getattr(self._ctx, "endpoints", []) or []

        boundary_values = {
            "negative": -1,
            "zero": 0,
            "empty_string": "",
            "null": None,
            "overflow": 999999999999,
            "sql_injection": "' OR '1'='1",
        }

        for ep in endpoints:
            if ep.get("method") not in ("POST", "PUT", "PATCH"):
                continue
            path = ep.get("path", "")
            method = ep.get("method", "POST")
            entity = ep.get("tags", [""])[0] if ep.get("tags") else _entity_from_path(path)

            if _is_auth_path(path):
                continue

            slice_id = _make_slice_id("bound", entity, method, path)

            self._slices.append(BehaviorSlice(
                slice_id=slice_id,
                risk_family="input_boundary",
                invariant=f"{method} {path}必须拒绝边界外/非法输入",
                invariant_type="input_boundary_invariant",
                precondition=f"{entity}的创建/修改接口可用",
                actor="admin",
                action=f"用多种边界值测试 {method} {path}",
                target=path,
                execution_plan=[
                    f"step1: 发送负数价格/数量到 {method} {path}",
                    f"step2: 发送空字符串到 {method} {path}",
                    f"step3: 发送SQL注入载荷到 {method} {path}",
                ],
                expected_result="所有非法输入返回400/422",
                assertion="all boundary requests return status_code in [400, 422]",
                evidence_requirements=["request_raw", "response_raw", "input_value", "expected_status"],
                regression_probe=f"curl -X {method} {path} -d '{{\"price\": -1}}'",
                ontology_subtype="negative_value",
                source_entity=entity,
                source_endpoint=f"{method} {path}",
                severity="P1",
                confidence=0.7,
            ))

    def _generate_lifecycle_slices(self) -> None:
        """For each entity: check lifecycle stage ordering."""
        endpoints = getattr(self._ctx, "endpoints", []) or []
        states = getattr(self._ctx, "states", []) or []

        for ep in endpoints:
            if ep.get("method") not in ("DELETE",):
                continue
            path = ep.get("path", "")
            entity = ep.get("tags", [""])[0] if ep.get("tags") else _entity_from_path(path)

            slice_id = _make_slice_id("life", entity, "DELETE", path)

            self._slices.append(BehaviorSlice(
                slice_id=slice_id,
                risk_family="lifecycle",
                invariant=f"活跃状态的{entity}不能被物理删除",
                invariant_type="lifecycle_invariant",
                precondition=f"存在一个状态为ACTIVE的{entity}",
                actor="admin",
                action=f"DELETE {path} (目标为活跃实体)",
                target=path,
                execution_plan=[
                    f"step1: GET {path} 找到活跃状态的{entity}",
                    f"step2: DELETE {path} 尝试删除",
                    f"step3: GET {path} 检查是否被删除",
                ],
                expected_result="HTTP 409 Conflict 或 400 Bad Request",
                assertion="status_code in [400, 409] and entity still exists",
                evidence_requirements=["before_state", "response_raw", "after_check"],
                regression_probe=f"curl -X DELETE {path}",
                ontology_subtype="active_entity_deletion",
                source_entity=entity,
                source_endpoint=f"DELETE {path}",
                severity="P1",
                confidence=0.6,
            ))

    def _generate_visibility_slices(self) -> None:
        """For each endpoint: check field visibility by role."""
        endpoints = getattr(self._ctx, "endpoints", []) or []
        sensitive_keywords = {"password", "token", "secret", "ssn", "id_number", "credit_card"}

        for ep in endpoints:
            if ep.get("method") != "GET":
                continue
            path = ep.get("path", "")
            entity = ep.get("tags", [""])[0] if ep.get("tags") else _entity_from_path(path)

            if _is_public_path(path):
                continue

            slice_id = _make_slice_id("vis", entity, "GET", path)

            self._slices.append(BehaviorSlice(
                slice_id=slice_id,
                risk_family="visibility",
                invariant=f"{entity}在不同角色视角下的可见字段必须符合PRD",
                invariant_type="visibility_invariant",
                precondition="系统有admin和viewer两个角色",
                actor="viewer",
                action=f"GET {path} 作为viewer",
                target=path,
                execution_plan=[
                    f"step1: 以admin身份 GET {path}",
                    f"step2: 以viewer身份 GET {path}",
                    "step3: 对比两个响应中的字段差异",
                ],
                expected_result="viewer不应看到admin专属字段或敏感字段",
                assertion="viewer_response does not contain admin-only or sensitive fields",
                evidence_requirements=["admin_response", "viewer_response", "field_diff"],
                regression_probe=f"curl {path} -H 'Authorization: Bearer <viewer_token>'",
                ontology_subtype="sensitive_field_leak",
                source_entity=entity,
                source_endpoint=f"GET {path}",
                severity="P1",
                confidence=0.6,
            ))

    def _generate_eventual_consistency_slices(self) -> None:
        """For async-flow endpoints: check eventual consistency."""
        # These are generated when async task information is available
        # from context. For now, add a minimal set.
        ctx_transitions = getattr(self._ctx, "transitions", []) or []
        async_keywords = {"async", "queue", "event", "callback", "webhook", "消息", "异步", "回调"}

        # If no async info in context, add at least one generic slice
        self._slices.append(BehaviorSlice(
            slice_id=_make_slice_id("ec", "system", "eventual_consistency"),
            risk_family="eventual_consistency",
            invariant="异步操作必须在合理时间内达到最终一致状态",
            invariant_type="eventual_consistency_invariant",
            precondition="系统存在异步操作(消息队列/回调/定时任务)",
            actor="system",
            action="触发异步操作 → 等待 → 验证结果",
            target="async_operations",
            execution_plan=[
                "step1: 触发异步操作(如创建订单)",
                "step2: 等待最大超时时间",
                "step3: 检查异步操作结果是否达成",
            ],
            expected_result="异步操作在超时前完成且结果正确",
            assertion="async_result matches expected within timeout",
            evidence_requirements=["operation_triggered", "timeout_duration", "final_state"],
            regression_probe="轮询检查异步操作状态直到超时或完成",
            ontology_subtype="message_loss",
            source_entity="system",
            source_endpoint="async_operations",
            severity="P1",
            confidence=0.4,
        ))

    def _generate_audit_trail_slices(self) -> None:
        """For each mutating endpoint: check audit trail."""
        endpoints = getattr(self._ctx, "endpoints", []) or []

        for ep in endpoints:
            if ep.get("method") not in ("POST", "PUT", "PATCH", "DELETE"):
                continue
            path = ep.get("path", "")
            method = ep.get("method", "POST")
            entity = ep.get("tags", [""])[0] if ep.get("tags") else _entity_from_path(path)

            if _is_auth_path(path) or _is_public_path(path):
                continue

            slice_id = _make_slice_id("audit", entity, method, path)

            self._slices.append(BehaviorSlice(
                slice_id=slice_id,
                risk_family="audit_trail",
                invariant=f"对{entity}的{method} {path}操作必须有审计日志",
                invariant_type="audit_trail_invariant",
                precondition=f"系统有审计日志表且{entity}可被修改",
                actor="admin",
                action=f"{method} {path} 并检查审计日志",
                target=path,
                execution_plan=[
                    f"step1: {method} {path} 执行操作",
                    "step2: 检查审计日志表中是否有对应记录",
                    "step3: 验证审计条目包含操作者、时间、操作、结果",
                ],
                expected_result="审计日志中存在对应的操作记录",
                assertion="audit_log contains entry with actor, timestamp, action, result",
                evidence_requirements=["operation_detail", "audit_log_query", "audit_entry"],
                regression_probe=f"执行 {method} {path} 后SELECT * FROM audit_log WHERE action LIKE '%{entity}%'",
                ontology_subtype="missing_audit_entry",
                source_entity=entity,
                source_endpoint=f"{method} {path}",
                severity="P1",
                confidence=0.6,
            ))


# ── Helpers ────────────────────────────────────────────────────────────────

def _entity_from_path(path: str) -> str:
    segments = [s for s in path.strip("/").split("/") if s and not s.startswith("{")]
    skip = {"api", "v1", "v2", "v3", "v4", "v5"}
    meaningful = [s for s in segments if s.lower() not in skip]
    return meaningful[-1] if meaningful else "unknown"


def _is_public_path(path: str) -> bool:
    public_keywords = {"health", "metrics", "docs", "swagger", "openapi", "login", "auth", "register", "public"}
    path_lower = path.lower()
    return any(kw in path_lower for kw in public_keywords)


def _is_auth_path(path: str) -> bool:
    auth_keywords = {"login", "auth", "signin", "signup", "register", "token", "oauth", "sso", "session"}
    path_lower = path.lower()
    return any(kw in path_lower for kw in auth_keywords)


# ── Persistence ────────────────────────────────────────────────────────────

def persist_slices(
    slices: list[BehaviorSlice],
    project: str,
    *,
    root: Path | None = None,
) -> Path:
    """Persist generated slices to platform_workspace."""
    root = Path(root or Path.cwd())
    out_dir = root / "platform_workspace" / project
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "behavior_slices.json"
    data = {
        "total_slices": len(slices),
        "slices": [s.to_dict() for s in slices],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def load_slices(project: str, *, root: Path | None = None) -> list[dict[str, Any]]:
    """Load previously persisted slices."""
    root = Path(root or Path.cwd())
    path = root / "platform_workspace" / project / "behavior_slices.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("slices", [])
    except Exception:
        return []
