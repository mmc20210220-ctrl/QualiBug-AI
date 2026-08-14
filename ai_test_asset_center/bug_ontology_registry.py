from __future__ import annotations

"""Bug Ontology Registry — Extensible defect discovery architecture.

This module replaces the hardcoded "20 bug types" design with a pluggable,
ontology-driven approach. Every defect definition follows the standard shape:

    {
      risk_family: str         — Level-1 family (one of 12)
      subtype: str             — Level-2 specific bug family (80+)
      display_name: str        — Human-readable label
      applicable_entities: list[str]   — Entity types this applies to
      required_context: dict           — What context is needed to test
      invariant: str                   — The business invariant checked
      invariant_type: str              — Maps to invariant_engine
      scenario_generator: str          — How to generate test scenarios
      execution_strategy: dict         — How to execute the test
      evidence_required: list[str]     — Required evidence items
      regression_probe_template: str   — Template for regression test
      severity_default: str            — Default severity (P0-P3)
    }

Config format: Python dict (primary), JSON/YAML overrides supported.

Design contract:
  - No hardcoded industry-specific bug types
  - Deployments can add/remove families via config files
  - 12 risk families, 80+ subtypes defined as seed ontology
  - All entries are data, not code — no if/elif chains
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Level-1 Risk Families ────────────────────────────────────────────────

RISK_FAMILIES: dict[str, dict[str, Any]] = {

    # =====================================================================
    # 1. AUTHORIZATION — 权限与访问控制
    # =====================================================================
    "authorization": {
        "family_id": "authorization",
        "display_name": "权限与访问控制",
        "display_name_en": "Authorization & Access Control",
        "core_invariant": "未授权角色不能访问/操作受限资源",
        "core_invariant_en": "Unauthorized roles cannot access or operate on restricted resources",
        "invariant_type": "permission_invariant",
        "reporting_bucket": "security",
        "default_severity": "P1",
        "icon": "shield",
        "description": "Covers role-based access, privilege escalation, IDOR, and authentication bypass.",
    },

    # =====================================================================
    # 2. TENANT_ISOLATION — 租户数据隔离
    # =====================================================================
    "tenant_isolation": {
        "family_id": "tenant_isolation",
        "display_name": "租户数据隔离",
        "display_name_en": "Tenant Data Isolation",
        "core_invariant": "租户A不能访问租户B的数据",
        "core_invariant_en": "Tenant A cannot access Tenant B's data",
        "invariant_type": "tenant_isolation_invariant",
        "reporting_bucket": "security",
        "default_severity": "P0",
        "icon": "layers",
        "description": "Cross-tenant data access, tenant-ID manipulation, multi-organization isolation.",
    },

    # =====================================================================
    # 3. STATE_MACHINE — 状态机完整性
    # =====================================================================
    "state_machine": {
        "family_id": "state_machine",
        "display_name": "状态机完整性",
        "display_name_en": "State Machine Integrity",
        "core_invariant": "业务对象状态只能沿PRD定义的合法路径流转",
        "core_invariant_en": "Entity states can only transition along PRD-defined legal paths",
        "invariant_type": "state_machine_invariant",
        "reporting_bucket": "functional",
        "default_severity": "P1",
        "icon": "git-branch",
        "description": "Invalid state transitions, skipped states, final-state modification, state-order violations.",
    },

    # =====================================================================
    # 4. CONSERVATION — 金额/数量守恒
    # =====================================================================
    "conservation": {
        "family_id": "conservation",
        "display_name": "金额/数量守恒",
        "display_name_en": "Monetary / Quantity Conservation",
        "core_invariant": "系统内金额/数量总和在操作前后必须守恒",
        "core_invariant_en": "Total amount/quantity in the system must be conserved across operations",
        "invariant_type": "conservation_invariant",
        "reporting_bucket": "data",
        "default_severity": "P0",
        "icon": "calculator",
        "description": "Money loss, quantity leaks, double-spend, negative balances, rounding errors.",
    },

    # =====================================================================
    # 5. IDEMPOTENCY — 幂等性
    # =====================================================================
    "idempotency": {
        "family_id": "idempotency",
        "display_name": "幂等性",
        "display_name_en": "Idempotency",
        "core_invariant": "重复提交不产生重复业务结果",
        "core_invariant_en": "Repeated submissions do not produce duplicate business results",
        "invariant_type": "idempotency_invariant",
        "reporting_bucket": "data",
        "default_severity": "P1",
        "icon": "refresh",
        "description": "Duplicate resource creation, double processing, repeated approval, duplicate resource allocation.",
    },

    # =====================================================================
    # 6. CONCURRENCY — 并发一致性
    # =====================================================================
    "concurrency": {
        "family_id": "concurrency",
        "display_name": "并发一致性",
        "display_name_en": "Concurrency Consistency",
        "core_invariant": "并发操作不能导致数据不一致或竞态",
        "core_invariant_en": "Concurrent operations must not cause data inconsistency or race conditions",
        "invariant_type": "concurrency_invariant",
        "reporting_bucket": "data",
        "default_severity": "P1",
        "icon": "cpu",
        "description": "Race conditions, lost updates, deadlocks, optimistic-lock failures, resource oversell.",
    },

    # =====================================================================
    # 7. DATA_INTEGRITY — 数据一致性
    # =====================================================================
    "data_integrity": {
        "family_id": "data_integrity",
        "display_name": "数据一致性",
        "display_name_en": "Data Integrity",
        "core_invariant": "DB、缓存、API响应之间的数据必须一致",
        "core_invariant_en": "Data across DB, cache, and API responses must be consistent",
        "invariant_type": "data_consistency_invariant",
        "reporting_bucket": "data",
        "default_severity": "P1",
        "icon": "database",
        "description": "Cache drift, DB-API mismatch, read-write split inconsistency, foreign-key integrity.",
    },

    # =====================================================================
    # 8. INPUT_BOUNDARY — 输入边界
    # =====================================================================
    "input_boundary": {
        "family_id": "input_boundary",
        "display_name": "输入边界",
        "display_name_en": "Input Boundary",
        "core_invariant": "接口必须拒绝边界外/非法/注入输入",
        "core_invariant_en": "APIs must reject out-of-boundary / illegal / injection inputs",
        "invariant_type": "input_boundary_invariant",
        "reporting_bucket": "security",
        "default_severity": "P1",
        "icon": "filter",
        "description": "SQL injection, XSS, negative values, overflow, boundary bypass, type coercion.",
    },

    # =====================================================================
    # 9. LIFECYCLE — 生命周期完整性
    # =====================================================================
    "lifecycle": {
        "family_id": "lifecycle",
        "display_name": "生命周期完整性",
        "display_name_en": "Lifecycle Integrity",
        "core_invariant": "实体从创建到归档的所有阶段行为必须符合PRD",
        "core_invariant_en": "All entity lifecycle stages from creation to archival must conform to PRD",
        "invariant_type": "lifecycle_invariant",
        "reporting_bucket": "functional",
        "default_severity": "P2",
        "icon": "activity",
        "description": "Missing lifecycle stages, premature archival, active-entity deletion, cascade failures.",
    },

    # =====================================================================
    # 10. VISIBILITY — 可见性控制
    # =====================================================================
    "visibility": {
        "family_id": "visibility",
        "display_name": "可见性控制",
        "display_name_en": "Visibility Control",
        "core_invariant": "字段/实体在不同角色视角下的可见性必须符合PRD",
        "core_invariant_en": "Field/entity visibility across roles must conform to PRD",
        "invariant_type": "visibility_invariant",
        "reporting_bucket": "security",
        "default_severity": "P1",
        "icon": "eye",
        "description": "Sensitive field exposure, role-based field filtering, UI-API visibility mismatch.",
    },

    # =====================================================================
    # 11. EVENTUAL_CONSISTENCY — 异步最终一致性
    # =====================================================================
    "eventual_consistency": {
        "family_id": "eventual_consistency",
        "display_name": "异步最终一致性",
        "display_name_en": "Eventual Consistency",
        "core_invariant": "异步操作在合理时间内必须达到最终一致状态",
        "core_invariant_en": "Async operations must reach eventual consistency within a reasonable time",
        "invariant_type": "eventual_consistency_invariant",
        "reporting_bucket": "reliability",
        "default_severity": "P1",
        "icon": "clock",
        "description": "Message loss, duplicate delivery, order inversion, timeout without retry, dead-letter gaps.",
    },

    # =====================================================================
    # 12. AUDIT_TRAIL — 审计追踪
    # =====================================================================
    "audit_trail": {
        "family_id": "audit_trail",
        "display_name": "审计追踪",
        "display_name_en": "Audit Trail",
        "core_invariant": "关键操作必须有可追溯的审计日志",
        "core_invariant_en": "Critical operations must have traceable audit logs",
        "invariant_type": "audit_trail_invariant",
        "reporting_bucket": "security",
        "default_severity": "P1",
        "icon": "file-text",
        "description": "Missing audit entries, incomplete logs, log tampering, audit-field not updated.",
    },
}


# ── Level-2 Bug Families (80+ subtypes) ──────────────────────────────────

# Each entry: (family_id, subtype, display_name, description, applicable_entities, invariant_desc, scenario_desc, evidence_list, severity)

_SEED_BUG_FAMILIES: list[dict[str, Any]] = [
    # ──── Authorization (8 subtypes) ────
    {"family": "authorization", "subtype": "permission_bypass", "display": "权限绕过",
     "desc": "低权限角色可访问高权限接口", "entities": ["*"],
     "invariant": "actor.role >= endpoint.required_role",
     "scenario": "用低权限token访问管理端点",
     "evidence": ["request_raw", "response_raw", "token_role", "endpoint_required_role"],
     "severity": "P1"},
    {"family": "authorization", "subtype": "idor", "display": "不安全的直接对象引用(IDOR)",
     "desc": "通过替换资源ID访问他人数据", "entities": ["*"],
     "invariant": "resource.owner_id == actor.id for non-admin reads",
     "scenario": "用A的token访问B的资源ID",
     "evidence": ["request_raw", "response_raw", "actor_id", "resource_owner_id"],
     "severity": "P1"},
    {"family": "authorization", "subtype": "privilege_escalation", "display": "越权提升",
     "desc": "普通用户执行管理员操作", "entities": ["*"],
     "invariant": "actor.role in endpoint.allowed_roles for mutating operations",
     "scenario": "用普通用户token执行管理员写操作",
     "evidence": ["request_raw", "response_raw", "actor_role", "required_role"],
     "severity": "P0"},
    {"family": "authorization", "subtype": "auth_bypass", "display": "认证绕过",
     "desc": "未认证请求可访问受保护端点", "entities": ["*"],
     "invariant": "request.auth_valid == true for protected endpoints",
     "scenario": "不带token访问受保护端点",
     "evidence": ["request_raw", "response_raw", "auth_header_present"],
     "severity": "P0"},
    {"family": "authorization", "subtype": "role_confusion", "display": "角色混淆",
     "desc": "角色A获取角色B的token后可执行B的操作", "entities": ["*"],
     "invariant": "token.role == response.effective_role",
     "scenario": "交换两个角色的token测试",
     "evidence": ["request_raw", "response_raw", "token_role", "effective_role"],
     "severity": "P1"},
    {"family": "authorization", "subtype": "api_key_leak", "display": "API密钥泄露检测",
     "desc": "响应中意外包含密钥/token", "entities": ["*"],
     "invariant": "response.body does not contain api_key/token patterns",
     "scenario": "检查GET响应中是否泄露密钥",
     "evidence": ["response_raw", "pattern_match"],
     "severity": "P1"},
    {"family": "authorization", "subtype": "cors_misconfig", "display": "CORS配置错误",
     "desc": "跨域资源共享配置不安全", "entities": ["*"],
     "invariant": "Access-Control-Allow-Origin != * for authenticated endpoints",
     "scenario": "检查CORS头配置",
     "evidence": ["response_headers", "cors_config"],
     "severity": "P2"},
    {"family": "authorization", "subtype": "jwt_weakness", "display": "JWT弱点",
     "desc": "JWT签名算法none、弱密钥、过期未校验", "entities": ["*"],
     "invariant": "token.algorithm is strong and token.expiry is validated",
     "scenario": "用修改过的JWT测试",
     "evidence": ["token_decode", "response_raw"],
     "severity": "P1"},

    # ──── Tenant Isolation (6 subtypes) ────
    {"family": "tenant_isolation", "subtype": "cross_tenant_read", "display": "跨租户读取",
     "desc": "租户A可读取租户B的数据", "entities": ["*"],
     "invariant": "data.tenant_id == actor.tenant_id for all reads",
     "scenario": "租户A的token请求租户B的资源",
     "evidence": ["request_raw", "response_raw", "tenant_a_id", "tenant_b_id", "returned_tenant_id"],
     "severity": "P0"},
    {"family": "tenant_isolation", "subtype": "cross_tenant_write", "display": "跨租户写入",
     "desc": "租户A可修改租户B的数据", "entities": ["*"],
     "invariant": "write.target_tenant_id == actor.tenant_id",
     "scenario": "租户A的token修改租户B的资源",
     "evidence": ["request_raw", "response_raw", "before_snapshot", "after_snapshot"],
     "severity": "P0"},
    {"family": "tenant_isolation", "subtype": "tenant_id_injection", "display": "租户ID注入",
     "desc": "通过修改请求体中的tenant_id绕过隔离", "entities": ["*"],
     "invariant": "request.tenant_id is ignored; server uses token.tenant_id",
     "scenario": "在请求体中注入其他租户ID",
     "evidence": ["request_raw", "response_raw", "injected_tenant_id", "effective_tenant_id"],
     "severity": "P0"},
    {"family": "tenant_isolation", "subtype": "cross_org_access", "display": "跨组织访问",
     "desc": "组织A成员可访问组织B数据", "entities": ["*"],
     "invariant": "data.org_id in actor.org_ids",
     "scenario": "组织A的token请求组织B的资源",
     "evidence": ["request_raw", "response_raw", "actor_orgs", "resource_org"],
     "severity": "P1"},
    {"family": "tenant_isolation", "subtype": "export_isolation", "display": "导出数据隔离缺失",
     "desc": "导出功能返回了其他租户数据", "entities": ["*"],
     "invariant": "export.data.tenant_id == actor.tenant_id for all rows",
     "scenario": "租户A导出数据，检查是否包含租户B数据",
     "evidence": ["response_raw", "export_data", "tenant_markers"],
     "severity": "P0"},
    {"family": "tenant_isolation", "subtype": "cache_isolation", "display": "缓存隔离缺失",
     "desc": "缓存键未包含租户标识导致数据混淆", "entities": ["*"],
     "invariant": "cache.key contains tenant_id for tenant-scoped data",
     "scenario": "检查缓存键是否包含租户标识",
     "evidence": ["cache_key_pattern", "cache_config"],
     "severity": "P1"},

    # ──── State Machine (8 subtypes) ────
    {"family": "state_machine", "subtype": "invalid_transition", "display": "非法状态跳转",
     "desc": "实体从当前状态跳转到不允许的目标状态", "entities": ["*"],
     "invariant": "target_state in current_state.allowed_next_states",
     "scenario": "尝试从状态A直接跳转到PRD不允许的状态C",
     "evidence": ["before_state", "after_state", "allowed_transitions", "prd_source"],
     "severity": "P1"},
    {"family": "state_machine", "subtype": "final_state_modification", "display": "终态修改",
     "desc": "对已终态(已完成/已取消)的实体进行修改", "entities": ["*"],
     "invariant": "entity.state.is_final → entity is immutable",
     "scenario": "尝试修改已终态的实体",
     "evidence": ["before_state", "response_raw", "is_final_state"],
     "severity": "P1"},
    {"family": "state_machine", "subtype": "state_skip", "display": "状态跳过",
     "desc": "实体跳过必经过渡状态", "entities": ["*"],
     "invariant": "state_history contains all mandatory intermediate states",
     "scenario": "检查实体是否跳过了审批状态",
     "evidence": ["state_history", "prd_required_states"],
     "severity": "P2"},
    {"family": "state_machine", "subtype": "reverse_transition", "display": "非法反向流转",
     "desc": "实体从后置状态逆流到前置状态", "entities": ["*"],
     "invariant": "for state S → T: order(S) < order(T) unless explicit rollback",
     "scenario": "尝试将已完成实体改回初始状态",
     "evidence": ["before_state", "after_state", "state_order"],
     "severity": "P1"},
    {"family": "state_machine", "subtype": "concurrent_state", "display": "并发状态冲突",
     "desc": "并发操作导致实体进入不一致状态", "entities": ["*"],
     "invariant": "entity.state is deterministic after concurrent operations complete",
     "scenario": "两个请求同时修改同一实体状态",
     "evidence": ["concurrent_requests", "final_state", "expected_state"],
     "severity": "P1"},
    {"family": "state_machine", "subtype": "status_not_updated", "display": "状态更新遗漏",
     "desc": "操作成功但状态字段未更新", "entities": ["*"],
     "invariant": "operation_success → state_field.updated",
     "scenario": "操作成功后状态仍未更新",
     "evidence": ["before_status", "after_status", "operation_result"],
     "severity": "P2"},
    {"family": "state_machine", "subtype": "approval_bypass", "display": "审批绕过",
     "desc": "申请人可以自审或跳过审批节点", "entities": ["*"],
     "invariant": "approver.id != requester.id for each approval node",
     "scenario": "用自己的token审批自己的申请",
     "evidence": ["request_raw", "response_raw", "requester_id", "approver_id"],
     "severity": "P0"},
    {"family": "state_machine", "subtype": "workflow_hijack", "display": "工作流劫持",
     "desc": "非指定的审批人可审批", "entities": ["*"],
     "invariant": "approver.id in workflow.current_node.approver_ids",
     "scenario": "用不在审批列表的账号审批",
     "evidence": ["request_raw", "response_raw", "approver_id", "allowed_approvers"],
     "severity": "P1"},

    # ──── Conservation (8 subtypes) ────
    {"family": "conservation", "subtype": "money_leak", "display": "守恒量泄露",
     "desc": "操作导致守恒量不守恒", "entities": ["*"],
     "invariant": "sum(before) == sum(after) for conserved quantity",
     "scenario": "操作后检查系统守恒总量",
     "evidence": ["before_snapshot", "after_snapshot", "quantity_field"],
     "severity": "P0"},
    {"family": "conservation", "subtype": "negative_balance", "display": "负数数量/余额",
     "desc": "数量或余额字段变为负数", "entities": ["*"],
     "invariant": "balance >= 0 at all times (unless overdraft allowed)",
     "scenario": "超额扣减或超卖",
     "evidence": ["before_snapshot", "after_snapshot", "balance_field"],
     "severity": "P0"},
    {"family": "conservation", "subtype": "double_spend", "display": "重复扣减",
     "desc": "同一笔数量被扣减两次", "entities": ["*"],
     "invariant": "each debit has a unique transaction_id and is processed exactly once",
     "scenario": "发送两个相同的扣减请求",
     "evidence": ["request_1", "request_2", "balance_before", "balance_after"],
     "severity": "P0"},
    {"family": "conservation", "subtype": "refund_exceeds_payment", "display": "冲正超过原值",
     "desc": "冲正金额大于原始金额", "entities": ["*"],
     "invariant": "reversal_amount <= original_amount",
     "scenario": "发起超额冲正请求",
     "evidence": ["original_record", "reversal_request", "response_raw"],
     "severity": "P0"},
    {"family": "conservation", "subtype": "inventory_oversell", "display": "资源超卖",
     "desc": "售出数量超过可用量", "entities": ["*"],
     "invariant": "sold_count <= available_count for each resource",
     "scenario": "并发操作测试资源扣减",
     "evidence": ["before_count", "request_quantity", "after_count"],
     "severity": "P1"},
    {"family": "conservation", "subtype": "rounding_error", "display": "精度/舍入错误",
     "desc": "浮点运算导致金额精度丢失", "entities": ["*"],
     "invariant": "all monetary calculations use DECIMAL with sufficient precision",
     "scenario": "分账或比例计算时验证金额",
     "evidence": ["calculation_steps", "expected_sum", "actual_sum"],
     "severity": "P2"},
    {"family": "conservation", "subtype": "coupon_abuse", "display": "权益凭证滥用",
     "desc": "权益凭证可越权使用、重复使用或绕过门槛", "entities": ["*"],
     "invariant": "voucher.usage_count <= voucher.max_usage and voucher.valid_for(user, resource)",
     "scenario": "使用已过期/已用完/非本人的权益凭证",
     "evidence": ["voucher_record", "resource_record", "before_after_amount"],
     "severity": "P1"},
    {"family": "conservation", "subtype": "ledger_mismatch", "display": "流水不一致",
     "desc": "账户流水与余额不一致", "entities": ["*"],
     "invariant": "sum(ledger transactions) == current balance",
     "scenario": "核对流水总和与当前余额",
     "evidence": ["ledger_entries", "current_balance", "calculated_sum"],
     "severity": "P1"},

    # ──── Idempotency (6 subtypes) ────
    {"family": "idempotency", "subtype": "duplicate_create", "display": "重复创建",
     "desc": "同一请求发送两次创建了两个实体", "entities": ["*"],
     "invariant": "idempotency_key → exactly one created resource",
     "scenario": "发送两个相同的POST请求",
     "evidence": ["request_1", "request_2", "created_resources"],
     "severity": "P1"},
    {"family": "idempotency", "subtype": "duplicate_payment", "display": "重复记账/重复提交",
     "desc": "同一记账请求被处理两次", "entities": ["*"],
     "invariant": "resource_idempotency_key → at most one operation",
     "scenario": "重复发送操作回调",
     "evidence": ["callback_1", "callback_2", "account_balance"],
     "severity": "P0"},
    {"family": "idempotency", "subtype": "duplicate_approval", "display": "重复审批",
     "desc": "同一审批被通过两次", "entities": ["*"],
     "invariant": "approval.idempotency_key → at most one approval action",
     "scenario": "重复发送审批请求",
     "evidence": ["approval_1", "approval_2", "workflow_state"],
     "severity": "P1"},
    {"family": "idempotency", "subtype": "missing_idempotency_key", "display": "缺少幂等键",
     "desc": "写/创建等关键接口未提供幂等键机制", "entities": ["*"],
     "invariant": "POST endpoints for critical resources support Idempotency-Key header",
     "scenario": "检查关键POST端点是否支持幂等键",
     "evidence": ["api_spec", "header_support"],
     "severity": "P2"},
    {"family": "idempotency", "subtype": "retry_storm", "display": "重试风暴",
     "desc": "失败请求的重试导致重复操作", "entities": ["*"],
     "invariant": "retry_with_same_idempotency_key → idempotent result",
     "scenario": "模拟请求失败重试场景",
     "evidence": ["retry_attempts", "duplicate_results"],
     "severity": "P2"},
    {"family": "idempotency", "subtype": "non_idempotent_delete", "display": "非幂等删除",
     "desc": "重复DELETE导致意外删除其他资源", "entities": ["*"],
     "invariant": "DELETE on non-existent resource returns 404, not cascading delete",
     "scenario": "重复发送DELETE请求",
     "evidence": ["request_1", "request_2", "related_resources"],
     "severity": "P2"},

    # ──── Concurrency (8 subtypes) ────
    {"family": "concurrency", "subtype": "race_condition", "display": "竞态条件",
     "desc": "并发读写导致数据不一致", "entities": ["*"],
     "invariant": "concurrent reads and writes produce consistent results",
     "scenario": "同时发起读写操作",
     "evidence": ["concurrent_results", "final_state", "expected_state"],
     "severity": "P1"},
    {"family": "concurrency", "subtype": "lost_update", "display": "丢失更新",
     "desc": "后提交的更新覆盖先提交的更新", "entities": ["*"],
     "invariant": "optimistic lock version check prevents lost updates",
     "scenario": "两个请求同时更新同一实体",
     "evidence": ["request_a", "request_b", "final_entity_state"],
     "severity": "P1"},
    {"family": "concurrency", "subtype": "deadlock", "display": "死锁",
     "desc": "两个事务互相等待对方释放锁", "entities": ["*"],
     "invariant": "lock acquisition order is consistent across transactions",
     "scenario": "并发操作检测死锁",
     "evidence": ["transaction_logs", "lock_wait_timeout"],
     "severity": "P2"},
    {"family": "concurrency", "subtype": "dirty_read", "display": "脏读",
     "desc": "读到未提交的事务数据", "entities": ["*"],
     "invariant": "transactions run at READ COMMITTED or higher isolation",
     "scenario": "在事务未提交时读取数据",
     "evidence": ["transaction_state", "read_result", "committed_state"],
     "severity": "P2"},
    {"family": "concurrency", "subtype": "phantom_read", "display": "幻读",
     "desc": "同一事务内两次查询结果不同", "entities": ["*"],
     "invariant": "repeatable reads for critical business queries",
     "scenario": "事务内两次查询对比",
     "evidence": ["query_1", "query_2", "inserted_during_transaction"],
     "severity": "P2"},
    {"family": "concurrency", "subtype": "lock_timeout", "display": "锁超时处理缺失",
     "desc": "获取锁超时后未正确处理", "entities": ["*"],
     "invariant": "lock acquisition failure → graceful error, not silent skip",
     "scenario": "模拟锁竞争超时",
     "evidence": ["lock_attempt", "timeout_response", "business_state"],
     "severity": "P2"},
    {"family": "concurrency", "subtype": "queue_duplicate", "display": "消息队列重复消费",
     "desc": "消息被重复消费导致并发问题", "entities": ["*"],
     "invariant": "each message is processed exactly once (or at-least-once with idempotency)",
     "scenario": "模拟消息重复投递",
     "evidence": ["message_ids", "processing_log", "final_state"],
     "severity": "P1"},
    {"family": "concurrency", "subtype": "distributed_lock_failure", "display": "分布式锁失效",
     "desc": "分布式锁因网络分区或过期失效", "entities": ["*"],
     "invariant": "distributed lock holds exclusivity guarantee despite partial failures",
     "scenario": "模拟锁持有者网络中断",
     "evidence": ["lock_owner", "concurrent_operation", "data_state"],
     "severity": "P1"},

    # ──── Data Integrity (8 subtypes) ────
    {"family": "data_integrity", "subtype": "cache_drift", "display": "缓存漂移",
     "desc": "缓存数据与数据库不一致", "entities": ["*"],
     "invariant": "cache[key].data == db.record.data for cached entities",
     "scenario": "写操作后对比缓存与DB",
     "evidence": ["cache_value", "db_value", "write_operation"],
     "severity": "P1"},
    {"family": "data_integrity", "subtype": "api_db_mismatch", "display": "API与DB数据不一致",
     "desc": "API返回的数据与数据库中的不一致", "entities": ["*"],
     "invariant": "api_response.data == db_select_result for the same entity",
     "scenario": "对比API响应与DB查询结果",
     "evidence": ["api_response", "db_snapshot", "entity_id"],
     "severity": "P1"},
    {"family": "data_integrity", "subtype": "read_write_split_stale", "display": "读写分离读陈旧",
     "desc": "读写分离架构下读到旧数据", "entities": ["*"],
     "invariant": "replication_lag < acceptable_threshold for read replicas",
     "scenario": "写后立即读验证数据新鲜度",
     "evidence": ["write_response", "read_response", "lag_detected"],
     "severity": "P2"},
    {"family": "data_integrity", "subtype": "soft_delete_leak", "display": "软删除数据泄露",
     "desc": "软删除的数据仍可通过API访问", "entities": ["*"],
     "invariant": "deleted_at IS NOT NULL → not visible via standard GET",
     "scenario": "尝试GET已被软删除的实体",
     "evidence": ["request_raw", "response_raw", "deleted_at_field"],
     "severity": "P1"},
    {"family": "data_integrity", "subtype": "foreign_key_orphan", "display": "外键孤儿",
     "desc": "关联的父实体被删除导致孤儿子记录", "entities": ["*"],
     "invariant": "each foreign key references an existing parent record",
     "scenario": "删除父实体后检查子实体",
     "evidence": ["parent_deleted", "child_still_exists", "fk_constraint"],
     "severity": "P2"},
    {"family": "data_integrity", "subtype": "schema_drift", "display": "Schema 漂移",
     "desc": "API响应结构不符合OpenAPI定义", "entities": ["*"],
     "invariant": "response.body conforms to OpenAPI schema definition",
     "scenario": "对比实际响应与OpenAPI schema",
     "evidence": ["response_raw", "openapi_schema", "field_diff"],
     "severity": "P2"},
    {"family": "data_integrity", "subtype": "enum_violation", "display": "枚举值违规",
     "desc": "字段值不在定义的枚举集合内", "entities": ["*"],
     "invariant": "field_value in schema_enum_values",
     "scenario": "检查响应中的枚举字段值",
     "evidence": ["response_raw", "enum_definition", "violation_value"],
     "severity": "P2"},
    {"family": "data_integrity", "subtype": "null_violation", "display": "非空约束违规",
     "desc": "标记为required的字段为null", "entities": ["*"],
     "invariant": "required_fields are present and non-null in all responses",
     "scenario": "检查响应中必填字段",
     "evidence": ["response_raw", "schema_required", "null_fields"],
     "severity": "P2"},

    # ──── Input Boundary (8 subtypes) ────
    {"family": "input_boundary", "subtype": "sql_injection", "display": "SQL注入",
     "desc": "SQL注入载荷未正确过滤", "entities": ["*"],
     "invariant": "user input is parameterized; no raw SQL concatenation responds with injection pattern",
     "scenario": "发送SQL注入载荷",
     "evidence": ["request_raw", "response_raw", "injection_payload"],
     "severity": "P0"},
    {"family": "input_boundary", "subtype": "xss", "display": "跨站脚本(XSS)",
     "desc": "HTML/JS载荷在响应中未转义", "entities": ["*"],
     "invariant": "user input in response is HTML-entity-escaped",
     "scenario": "发送XSS载荷到输入字段",
     "evidence": ["request_raw", "response_raw", "xss_payload"],
     "severity": "P1"},
    {"family": "input_boundary", "subtype": "negative_value", "display": "负数输入",
     "desc": "接口接受金额/数量为负值", "entities": ["*"],
     "invariant": "amount >= 0 and quantity >= 0 for non-reversal operations",
     "scenario": "发送负数金额或数量",
     "evidence": ["request_raw", "response_status", "stored_value"],
     "severity": "P1"},
    {"family": "input_boundary", "subtype": "overflow", "display": "整数溢出",
     "desc": "超出字段范围的整数被接受", "entities": ["*"],
     "invariant": "input_value is within column type range",
     "scenario": "发送超大或超小整数",
     "evidence": ["request_raw", "response_raw", "overflow_value"],
     "severity": "P2"},
    {"family": "input_boundary", "subtype": "zero_division", "display": "零除错误",
     "desc": "分母为零导致服务端异常", "entities": ["*"],
     "invariant": "division operations guard against zero denominator",
     "scenario": "发送零值作为分母参数",
     "evidence": ["request_raw", "response_status", "error_message"],
     "severity": "P2"},
    {"family": "input_boundary", "subtype": "type_coercion", "display": "类型强转绕过",
     "desc": "字符串 0123 当作数字123处理", "entities": ["*"],
     "invariant": "input type matches schema type; no silent coercion",
     "scenario": "发送字符串代替数字",
     "evidence": ["request_raw", "response_raw", "coerced_value"],
     "severity": "P2"},
    {"family": "input_boundary", "subtype": "path_traversal", "display": "路径遍历",
     "desc": "路径参数中包含../可访问其他资源", "entities": ["*"],
     "invariant": "file/resource paths are sanitized against traversal",
     "scenario": "在路径参数中插入../",
     "evidence": ["request_raw", "response_raw", "traversed_path"],
     "severity": "P0"},
    {"family": "input_boundary", "subtype": "mass_assignment", "display": "批量赋值",
     "desc": "请求体可设置不应由用户控制的字段", "entities": ["*"],
     "invariant": "only whitelisted fields are accepted from request body",
     "scenario": "尝试设置is_admin=true",
     "evidence": ["request_raw", "response_raw", "unauthorized_field", "stored_value"],
     "severity": "P1"},

    # ──── Lifecycle (6 subtypes) ────
    {"family": "lifecycle", "subtype": "premature_archive", "display": "过早归档",
     "desc": "仍在使用的实体被归档", "entities": ["*"],
     "invariant": "entity can only archive when all dependent workflows are complete",
     "scenario": "尝试归档有关联未完成工作的实体",
     "evidence": ["entity_state", "dependent_entities", "archive_attempt"],
     "severity": "P2"},
    {"family": "lifecycle", "subtype": "active_entity_deletion", "display": "活跃实体删除",
     "desc": "处于活跃状态的实体被物理删除", "entities": ["*"],
     "invariant": "only CANCELLED/EXPIRED entities can be physically deleted",
     "scenario": "尝试删除状态为ACTIVE的实体",
     "evidence": ["entity_state", "delete_request", "result"],
     "severity": "P1"},
    {"family": "lifecycle", "subtype": "cascade_delete_unexpected", "display": "级联删除意外",
     "desc": "删除实体时意外删除了关联实体", "entities": ["*"],
     "invariant": "cascade rules are explicit; no surprise deletion",
     "scenario": "删除父实体时观察子实体",
     "evidence": ["before_snapshot", "after_snapshot", "cascade_rules"],
     "severity": "P1"},
    {"family": "lifecycle", "subtype": "expiry_not_enforced", "display": "过期未强制执行",
     "desc": "实体到期后仍可正常操作", "entities": ["*"],
     "invariant": "expired entities are rejected for all mutating operations",
     "scenario": "在实体过期后尝试修改",
     "evidence": ["expiry_time", "operation_time", "response_status"],
     "severity": "P2"},
    {"family": "lifecycle", "subtype": "creation_without_init", "display": "创建未初始化",
     "desc": "实体创建后缺少必要的初始化步骤", "entities": ["*"],
     "invariant": "on create: all mandatory child records and defaults are created",
     "scenario": "创建实体后检查关联记录",
     "evidence": ["created_entity", "missing_child_records"],
     "severity": "P2"},
    {"family": "lifecycle", "subtype": "event_order_violation", "display": "事件顺序违规",
     "desc": "生命周期事件顺序不符合PRD定义", "entities": ["*"],
     "invariant": "event_a.timestamp <= event_b.timestamp for ordered lifecycle events",
     "scenario": "检查事件时间线",
     "evidence": ["event_timeline", "prd_event_order"],
     "severity": "P2"},

    # ──── Visibility (6 subtypes) ────
    {"family": "visibility", "subtype": "sensitive_field_leak", "display": "敏感字段泄露",
     "desc": "响应中暴露了不应对外可见的字段", "entities": ["*"],
     "invariant": "sensitive fields (password, token, ssn) NOT in any API response",
     "scenario": "检查API响应中是否包含敏感字段",
     "evidence": ["response_raw", "sensitive_field_list", "exposed_fields"],
     "severity": "P0"},
    {"family": "visibility", "subtype": "role_field_filtering", "display": "角色字段过滤缺失",
     "desc": "不同角色看到的字段应该不同但没有过滤", "entities": ["*"],
     "invariant": "fields_visible_to(role_a) ∩ fields_visible_to(role_b) == allowed_overlap",
     "scenario": "对比管理员和普通用户看到的字段",
     "evidence": ["admin_response", "user_response", "field_diff"],
     "severity": "P1"},
    {"family": "visibility", "subtype": "data_masking_failure", "display": "数据脱敏失败",
     "desc": "手机号/身份证等应脱敏的字段明文显示", "entities": ["*"],
     "invariant": "PII fields are masked according to policy in non-admin responses",
     "scenario": "检查普通用户响应中的PII字段",
     "evidence": ["user_response", "pii_fields", "masking_check"],
     "severity": "P1"},
    {"family": "visibility", "subtype": "list_leak_count", "display": "列表计数泄露",
     "desc": "用户可看到不应访问的实体总数", "entities": ["*"],
     "invariant": "list.total returns count of visible-to-actor entities only",
     "scenario": "检查分页列表的总数",
     "evidence": ["response_total", "actual_accessible_count"],
     "severity": "P2"},
    {"family": "visibility", "subtype": "soft_deleted_visible", "display": "软删除记录可见",
     "desc": "普通用户可以看到已软删除的记录", "entities": ["*"],
     "invariant": "soft-deleted records are filtered from non-admin list views",
     "scenario": "检查普通用户列表是否包含已删除记录",
     "evidence": ["list_response", "deleted_flag"],
     "severity": "P2"},
    {"family": "visibility", "subtype": "internal_endpoint_public", "display": "内部端点公开",
     "desc": "内部/调试端点对外暴露", "entities": ["*"],
     "invariant": "endpoints tagged 'internal' are not reachable from public network",
     "scenario": "检查内部端点是否可从外部访问",
     "evidence": ["request_raw", "response_raw", "endpoint_tag"],
     "severity": "P1"},

    # ──── Eventual Consistency (6 subtypes) ────
    {"family": "eventual_consistency", "subtype": "message_loss", "display": "消息丢失",
     "desc": "异步消息在传递中丢失", "entities": ["*"],
     "invariant": "published_message_count == consumed_message_count over time window",
     "scenario": "检查消息发布与消费数量",
     "evidence": ["published_count", "consumed_count", "time_window"],
     "severity": "P1"},
    {"family": "eventual_consistency", "subtype": "duplicate_delivery", "display": "重复投递",
     "desc": "同一消息被投递多次", "entities": ["*"],
     "invariant": "each message_id is processed at most once (or idempotently)",
     "scenario": "检查消息消费日志中的重复",
     "evidence": ["message_ids", "processing_times", "duplicate_count"],
     "severity": "P1"},
    {"family": "eventual_consistency", "subtype": "order_inversion", "display": "顺序反转",
     "desc": "消息处理顺序与发送顺序不一致导致状态错误", "entities": ["*"],
     "invariant": "dependent events are processed in causal order",
     "scenario": "快速连续发送两个有依赖关系的事件",
     "evidence": ["event_timestamps", "processing_order", "expected_order"],
     "severity": "P1"},
    {"family": "eventual_consistency", "subtype": "timeout_no_retry", "display": "超时无重试",
     "desc": "异步任务超时后没有重试机制", "entities": ["*"],
     "invariant": "failed async tasks are retried with backoff",
     "scenario": "模拟异步任务超时",
     "evidence": ["task_timeout", "retry_attempts", "final_status"],
     "severity": "P2"},
    {"family": "eventual_consistency", "subtype": "dead_letter_no_handler", "display": "死信无处理",
     "desc": "死信队列的消息没有处理机制", "entities": ["*"],
     "invariant": "dead-letter queue has a monitored handler/alert",
     "scenario": "检查死信队列是否有消费者",
     "evidence": ["dlq_depth", "dlq_consumer", "alert_config"],
     "severity": "P2"},
    {"family": "eventual_consistency", "subtype": "saga_compensation_missing", "display": "Saga补偿缺失",
     "desc": "分布式事务失败后未执行补偿操作", "entities": ["*"],
     "invariant": "each saga step has a corresponding compensation action",
     "scenario": "模拟saga中某一步失败",
     "evidence": ["saga_steps", "compensation_executed", "final_state"],
     "severity": "P1"},

    # ──── Audit Trail (6 subtypes) ────
    {"family": "audit_trail", "subtype": "missing_audit_entry", "display": "审计日志缺失",
     "desc": "关键操作没有审计日志", "entities": ["*"],
     "invariant": "every mutating operation generates an audit log entry",
     "scenario": "执行写操作后检查审计表",
     "evidence": ["operation_detail", "audit_table_empty", "expected_entry"],
     "severity": "P1"},
    {"family": "audit_trail", "subtype": "audit_field_not_updated", "display": "审计字段未更新",
     "desc": "updated_at, updated_by等审计字段未正确更新", "entities": ["*"],
     "invariant": "update → updated_at and updated_by are set to current time and actor",
     "scenario": "执行更新操作后检查审计字段",
     "evidence": ["before_audit_fields", "after_audit_fields"],
     "severity": "P2"},
    {"family": "audit_trail", "subtype": "audit_log_tampering", "display": "审计日志可篡改",
     "desc": "审计日志可以被修改或删除", "entities": ["*"],
     "invariant": "audit log is append-only; no UPDATE or DELETE on audit table",
     "scenario": "尝试修改或删除审计日志",
     "evidence": ["request_raw", "response_raw", "audit_state"],
     "severity": "P1"},
    {"family": "audit_trail", "subtype": "incomplete_audit", "display": "审计信息不完整",
     "desc": "审计日志缺少操作者/时间/IP等关键信息", "entities": ["*"],
     "invariant": "audit entry contains: who, what, when, source_ip, result",
     "scenario": "检查审计日志字段完整性",
     "evidence": ["audit_entry", "missing_fields"],
     "severity": "P2"},
    {"family": "audit_trail", "subtype": "audit_retention_violation", "display": "审计保留期违规",
     "desc": "审计日志未按合规要求保留足够时间", "entities": ["*"],
     "invariant": "audit logs retained for >= policy minimum duration",
     "scenario": "检查最旧审计日志时间",
     "evidence": ["oldest_entry_time", "policy_retention_days"],
     "severity": "P1"},
    {"family": "audit_trail", "subtype": "read_audit_missing", "display": "读操作审计缺失",
     "desc": "敏感数据的读取操作未记录审计", "entities": ["*"],
     "invariant": "PII/sensitive data reads are logged when required by compliance",
     "scenario": "读取敏感数据后检查审计日志",
     "evidence": ["read_operation", "audit_log", "sensitivity_level"],
     "severity": "P2"},

    # ──── Contract / Schema (4 additional subtypes) ────
    {"family": "data_integrity", "subtype": "response_envelope_mismatch", "display": "响应信封不匹配",
     "desc": "响应结构不符合通用格式约定", "entities": ["*"],
     "invariant": "response follows standard envelope: {code, data, message} or {data, error}",
     "scenario": "检查不同端点的响应格式一致性",
     "evidence": ["responses_across_endpoints", "envelope_format"],
     "severity": "P3"},
    {"family": "data_integrity", "subtype": "error_code_inconsistency", "display": "错误码不一致",
     "desc": "相同错误在不同端点返回不同状态码", "entities": ["*"],
     "invariant": "same error scenario → same HTTP status code across endpoints",
     "scenario": "对比不同端点的错误响应",
     "evidence": ["error_responses", "status_codes", "error_scenario"],
     "severity": "P3"},

    # ──── Cross-cutting (2 additional subtypes) ────
    {"family": "eventual_consistency", "subtype": "webhook_delivery_failure", "display": "Webhook投递失败",
     "desc": "Webhook回调未重试或未通知", "entities": ["*"],
     "invariant": "failed webhooks are retried with exponential backoff",
     "scenario": "模拟Webhook投递失败",
     "evidence": ["webhook_attempts", "retry_count", "final_status"],
     "severity": "P2"},
    {"family": "lifecycle", "subtype": "version_conflict", "display": "版本冲突未检测",
     "desc": "基于旧版本的数据更新未检测到冲突", "entities": ["*"],
     "invariant": "optimistic locking via version/updated_at detects concurrent modifications",
     "scenario": "用旧version值更新实体",
     "evidence": ["old_version", "current_version", "response_status"],
     "severity": "P2"},
]


# ── Ontology Registry Class ───────────────────────────────────────────────

@dataclass
class OntologyEntry:
    """A single defect definition in the registry."""
    family_id: str
    subtype: str
    display_name: str
    description: str
    applicable_entities: list[str]
    invariant: str
    invariant_type: str
    scenario_generator: str
    execution_strategy: dict[str, Any]
    evidence_required: list[str]
    regression_probe_template: str
    severity_default: str
    source: str = "builtin"  # builtin | json_override | yaml_override | seed_analyzer


class BugOntologyRegistry:
    """Pluggable registry for all defect definitions.

    Usage::

        registry = BugOntologyRegistry()
        registry.load_builtin()
        registry.load_from_json("policies/bug_ontology.json")
        families = registry.list_families()
        entries = registry.get_entries_for_entity("resource")
    """

    def __init__(self):
        self._families: dict[str, dict[str, Any]] = {}
        self._entries: list[OntologyEntry] = []
        self._by_family: dict[str, list[OntologyEntry]] = {}
        self._by_subtype: dict[str, OntologyEntry] = {}
        self._loaded_sources: list[str] = []

    # ── Loading ────────────────────────────────────────────────────────

    def load_builtin(self) -> int:
        """Load the built-in 12-family × 80+-subtype ontology."""
        count = 0
        for family_id, family_def in RISK_FAMILIES.items():
            self._families[family_id] = dict(family_def)
            self._by_family.setdefault(family_id, [])

        for bug_def in _SEED_BUG_FAMILIES:
            family_id = bug_def["family"]
            subtype = bug_def["subtype"]
            if family_id not in self._families:
                continue

            entry = OntologyEntry(
                family_id=family_id,
                subtype=subtype,
                display_name=bug_def["display"],
                description=bug_def["desc"],
                applicable_entities=list(bug_def.get("entities", ["*"])),
                invariant=bug_def["invariant"],
                invariant_type=self._families[family_id]["invariant_type"],
                scenario_generator=bug_def["scenario"],
                execution_strategy={
                    "mode": "api_probe",
                    "description": bug_def["scenario"],
                },
                evidence_required=list(bug_def.get("evidence", [])),
                regression_probe_template="",
                severity_default=bug_def.get("severity", "P2"),
                source="builtin",
            )
            self._entries.append(entry)
            self._by_family.setdefault(family_id, []).append(entry)
            self._by_subtype[subtype] = entry
            count += 1

        self._loaded_sources.append("builtin")
        return count

    def load_from_json(self, path: str | Path) -> int:
        """Load additional/override entries from a JSON file."""
        path = Path(path)
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0
        return self._load_dict(data, source=f"json:{path.name}")

    def load_from_dict(self, data: dict[str, Any], *, source: str = "dict") -> int:
        """Load entries from a Python dict (for programmatic use)."""
        return self._load_dict(data, source=source)

    def _load_dict(self, data: dict[str, Any], *, source: str) -> int:
        """Parse a dict containing families and/or bug_definitions."""
        count = 0

        # Merge/override families
        families_data = data.get("families", data.get("risk_families", {}))
        if isinstance(families_data, dict):
            for fid, fdef in families_data.items():
                if isinstance(fdef, dict):
                    existing = self._families.get(fid, {})
                    merged = {**existing, **fdef}
                    self._families[fid] = merged
                    self._by_family.setdefault(fid, [])

        # Add bug definitions
        bugs = data.get("bug_definitions", data.get("bugs", []))
        if isinstance(bugs, list):
            for bug in bugs:
                if not isinstance(bug, dict):
                    continue
                family_id = str(bug.get("family", bug.get("risk_family", "")))
                subtype = str(bug.get("subtype", ""))
                if not family_id or not subtype:
                    continue

                family_def = self._families.get(family_id, {})
                entry = OntologyEntry(
                    family_id=family_id,
                    subtype=subtype,
                    display_name=str(bug.get("display_name", subtype)),
                    description=str(bug.get("description", "")),
                    applicable_entities=list(bug.get("applicable_entities", ["*"])),
                    invariant=str(bug.get("invariant", "")),
                    invariant_type=str(bug.get("invariant_type", family_def.get("invariant_type", ""))),
                    scenario_generator=str(bug.get("scenario_generator", "")),
                    execution_strategy=bug.get("execution_strategy", {}),
                    evidence_required=list(bug.get("evidence_required", [])),
                    regression_probe_template=str(bug.get("regression_probe_template", "")),
                    severity_default=str(bug.get("severity_default", "P2")),
                    source=source,
                )
                # Remove existing entry with same subtype if overriding
                if subtype in self._by_subtype:
                    old = self._by_subtype[subtype]
                    self._entries = [e for e in self._entries if e.subtype != subtype]
                    if old.family_id in self._by_family:
                        self._by_family[old.family_id] = [
                            e for e in self._by_family.get(old.family_id, []) if e.subtype != subtype
                        ]
                self._entries.append(entry)
                self._by_family.setdefault(family_id, []).append(entry)
                self._by_subtype[subtype] = entry
                count += 1

        self._loaded_sources.append(source)
        return count

    # ── Registration (for seed analyzers) ──────────────────────────────

    def register_seed_family(
        self,
        family_id: str,
        subtype: str,
        *,
        display_name: str,
        description: str,
        invariant: str,
        scenario: str,
        evidence: list[str],
        severity: str = "P2",
        source: str = "seed_analyzer",
    ) -> OntologyEntry:
        """Register an existing analyzer as a seed family in the ontology."""
        family_def = self._families.get(family_id, {
            "family_id": family_id,
            "invariant_type": "custom",
        })

        entry = OntologyEntry(
            family_id=family_id,
            subtype=subtype,
            display_name=display_name,
            description=description,
            applicable_entities=["*"],
            invariant=invariant,
            invariant_type=family_def.get("invariant_type", "custom"),
            scenario_generator=scenario,
            execution_strategy={"mode": "api_probe", "description": scenario},
            evidence_required=list(evidence),
            regression_probe_template="",
            severity_default=severity,
            source=source,
        )

        if subtype in self._by_subtype:
            return self._by_subtype[subtype]  # Already registered

        self._entries.append(entry)
        self._by_family.setdefault(family_id, []).append(entry)
        self._by_subtype[subtype] = entry
        return entry

    # ── Query ──────────────────────────────────────────────────────────

    def list_families(self) -> list[dict[str, Any]]:
        """Return all level-1 risk families with their metadata."""
        result = []
        for fid, fdef in self._families.items():
            subtypes = self._by_family.get(fid, [])
            result.append({
                **fdef,
                "subtype_count": len(subtypes),
                "subtypes": [e.subtype for e in subtypes],
            })
        return result

    def list_entries(self) -> list[OntologyEntry]:
        """Return all ontology entries."""
        return list(self._entries)

    def get_entry(self, subtype: str) -> OntologyEntry | None:
        """Look up an entry by its subtype identifier."""
        return self._by_subtype.get(subtype)

    def get_entries_for_entity(self, entity: str) -> list[OntologyEntry]:
        """Get all entries applicable to a given entity."""
        entity_lower = entity.lower()
        return [
            e for e in self._entries
            if "*" in e.applicable_entities or entity_lower in [x.lower() for x in e.applicable_entities]
        ]

    def get_entries_for_family(self, family_id: str) -> list[OntologyEntry]:
        """Get all entries in a given risk family."""
        return list(self._by_family.get(family_id, []))

    def get_entries_for_invariant(self, invariant_type: str) -> list[OntologyEntry]:
        """Get all entries that check a specific invariant type."""
        return [e for e in self._entries if e.invariant_type == invariant_type]

    def count_families(self) -> int:
        """Number of level-1 risk families loaded."""
        return len(self._families)

    def count_entries(self) -> int:
        """Total number of bug family entries (level-2)."""
        return len(self._entries)

    def coverage_summary(self) -> dict[str, Any]:
        """Return a coverage breakdown for the registry itself."""
        summary: dict[str, dict[str, Any]] = {}
        for fid, entries in self._by_family.items():
            fdef = self._families.get(fid, {})
            summary[fid] = {
                "display_name": fdef.get("display_name", fid),
                "invariant_type": fdef.get("invariant_type", "unknown"),
                "subtype_count": len(entries),
                "severities": {
                    "P0": sum(1 for e in entries if e.severity_default == "P0"),
                    "P1": sum(1 for e in entries if e.severity_default == "P1"),
                    "P2": sum(1 for e in entries if e.severity_default == "P2"),
                    "P3": sum(1 for e in entries if e.severity_default == "P3"),
                },
            }
        return summary

    def to_dict(self) -> dict[str, Any]:
        """Serialize full registry for persistence or API transport."""
        return {
            "families": self._families,
            "entries": [
                {
                    "family_id": e.family_id,
                    "subtype": e.subtype,
                    "display_name": e.display_name,
                    "description": e.description,
                    "invariant": e.invariant,
                    "invariant_type": e.invariant_type,
                    "evidence_required": e.evidence_required,
                    "severity_default": e.severity_default,
                    "source": e.source,
                }
                for e in self._entries
            ],
            "summary": self.coverage_summary(),
            "total_families": self.count_families(),
            "total_entries": self.count_entries(),
        }


# ── Singleton and Factory ─────────────────────────────────────────────────

_global_registry: BugOntologyRegistry | None = None


def get_ontology_registry() -> BugOntologyRegistry:
    """Get or create the global BugOntologyRegistry singleton."""
    global _global_registry
    if _global_registry is None:
        _global_registry = BugOntologyRegistry()
        _global_registry.load_builtin()
        # Try loading deployment overrides
        _try_load_overrides(_global_registry)
    return _global_registry


def _try_load_overrides(registry: BugOntologyRegistry) -> None:
    """Load deployment-level ontology overrides if present."""
    policy_dir = Path(__file__).resolve().parent / "policies"
    for filename in ("bug_ontology.json", "bug_ontology.yaml"):
        override_path = policy_dir / filename
        if override_path.exists():
            try:
                registry.load_from_json(override_path)
            except Exception:
                pass


def reset_ontology_registry() -> None:
    """Reset the global registry (useful for testing)."""
    global _global_registry
    _global_registry = None
