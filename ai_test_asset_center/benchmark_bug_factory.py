"""Benchmark Bug Factory — Unified, industry-agnostic known-bug generation and seeding.

This module is the SINGLE source of truth for benchmark bug creation.
It accepts an industry specification and produces:
  1. Concrete bug instances with oracle rules and expected/actual behavior
  2. Private ground truth (stored under PRIVATE_BLOCKLIST-protected paths)
  3. Public artifacts (OpenAPI stub + PRD excerpt) for blind discovery
  4. Runtime seeding instructions for the benchmark_runtime target

Design principles (per AGENTS.md):
  - Industry-agnostic: no hardcoded domain assumptions; templates are parameterized
  - No fake data: every bug instance is derived from templates, not fabricated on the fly
  - Blind discipline: ground truth paths contain PRIVATE_BLOCKLIST tokens
  - Observable: every stage logs its output count and path
"""

from __future__ import annotations

import json
import os
import random
import time
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── PRIVATE_BLOCKLIST tokens ────────────────────────────────────────────────
# These tokens MUST appear in any path/file that contains ground truth data.
# The discovery engine is hard-blocked from reading files whose paths contain
# any of these tokens (see defect_discovery.py:PRIVATE_BLOCKLIST).
_PRIVATE_MARKER = "private_ground_truth"
_BLOCKLIST_TOKENS = ("private_ground_truth", "ground_truth_bugs", "bug_sets", "enabled_bugs", "bug_set")


# ═════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class BugTemplate:
    """Industry-agnostic bug template. Each template encodes one defect pattern.

    The template is NOT industry-specific; it is parameterized by entity names,
    role names, and API patterns that get filled in per industry.
    """
    template_id: str
    risk_type: str          # e.g. "permission_bypass", "idor", "tenant_isolation"
    severity: str           # P0, P1, P2
    title_template: str     # Format string: "{entity} {role} can {action} {target}"
    api_pattern: str        # Format string: "{method} /{prefix}/{entity}/{param}"
    trigger_template: str   # Human-readable trigger description
    expected_status: int    # HTTP status code the system SHOULD return
    oracle_signal: str      # What to check: "status_code == 200", "response_count > 1", etc.
    evidence_required: list[str] = field(default_factory=lambda: [
        "actor_role", "request", "response_status", "response_body", "expected", "actual"
    ])


@dataclass
class IndustryProfile:
    """Defines the vocabulary and topology of an industry domain.

    All fields are used to instantiate templates into concrete bugs.
    """
    industry_id: str
    display_name: str
    entities: list[str]           # e.g. ["order", "product", "payment", "refund"]
    roles: list[str]              # e.g. ["customer", "admin", "agent", "auditor"]
    api_prefix: str               # e.g. "/api/v1"
    auth_endpoints: list[str]     # e.g. ["POST /auth/login", "GET /auth/me"]
    business_invariants: list[str]  # Natural-language business rules
    tenant_aware: bool = False    # Whether this industry typically has multi-tenant concerns


# ═════════════════════════════════════════════════════════════════════════════
# Universal Bug Templates (industry-agnostic)
# ═════════════════════════════════════════════════════════════════════════════

UNIVERSAL_TEMPLATES: list[BugTemplate] = [
    # ── Permission / Authorization ──
    # NOTE: title_template and trigger_template support both languages.
# Use {entity}, {role}, {action}, {prefix} placeholders for industry-agnostic instantiation.
BugTemplate(
        template_id="AUTH_VERTICAL_BYPASS",
        risk_type="permission_bypass",
        severity="P0",
        title_template="{low_role} 可访问 {entity} 管理接口",
        api_pattern="GET /{prefix}/admin/{entity}s",
        trigger_template="{low_role} 请求 {api}",
        expected_status=403,
        oracle_signal="status_code == 200",
    ),
    BugTemplate(
        template_id="AUTH_UNAUTH_ACCESS",
        risk_type="auth_bypass",
        severity="P0",
        title_template="未认证用户可访问受保护 {entity} 接口",
        api_pattern="POST /{prefix}/{entity}s",
        trigger_template="anonymous 请求 {api}",
        expected_status=401,
        oracle_signal="status_code in [200, 201]",
    ),
    BugTemplate(
        template_id="AUTH_PRIVILEGE_ESCALATION",
        risk_type="privilege_escalation",
        severity="P0",
        title_template="{low_role} 可修改 {high_role} 的 {entity} 数据",
        api_pattern="PUT /{prefix}/{entity}s/{id}",
        trigger_template="{low_role} 修改他人 {entity} 数据",
        expected_status=403,
        oracle_signal="status_code in [200, 201]",
    ),

    # ── IDOR / Cross-User Access ──
    BugTemplate(
        template_id="IDOR_CROSS_USER_READ",
        risk_type="idor",
        severity="P0",
        title_template="用户可查看他人 {entity}",
        api_pattern="GET /{prefix}/{entity}s/{id}",
        trigger_template="{role_a} 请求 {role_b} 的 {entity}",
        expected_status=403,
        oracle_signal="status_code == 200",
    ),
    BugTemplate(
        template_id="IDOR_CROSS_USER_WRITE",
        risk_type="idor",
        severity="P0",
        title_template="用户可修改他人 {entity}",
        api_pattern="PUT /{prefix}/{entity}s/{id}",
        trigger_template="{role_a} 修改 {role_b} 的 {entity}",
        expected_status=403,
        oracle_signal="status_code in [200, 201]",
    ),

    # ── Tenant Isolation ──
    BugTemplate(
        template_id="TENANT_CROSS_ACCESS",
        risk_type="tenant_isolation",
        severity="P0",
        title_template="跨租户 {entity} 数据泄露",
        api_pattern="GET /{prefix}/{entity}s",
        trigger_template="租户 A 用户请求租户 B {entity} 数据",
        expected_status=403,
        oracle_signal="status_code == 200",
    ),

    # ── State Machine / Lifecycle ──
    BugTemplate(
        template_id="STATE_INVALID_TRANSITION",
        risk_type="state_flow",
        severity="P1",
        title_template="{entity} 非法状态流转",
        api_pattern="POST /{prefix}/{entity}s/{id}/{action}",
        trigger_template="对已终态 {entity} 执行 {action}",
        expected_status=409,
        oracle_signal="status_code in [200, 201]",
    ),
    BugTemplate(
        template_id="STATE_TERMINAL_MUTATION",
        risk_type="state_flow",
        severity="P1",
        title_template="已终态 {entity} 仍可被修改",
        api_pattern="PUT /{prefix}/{entity}s/{id}",
        trigger_template="修改已关闭/取消的 {entity}",
        expected_status=409,
        oracle_signal="status_code in [200, 201]",
    ),

    # ── Data Consistency ──
    BugTemplate(
        template_id="DATA_CREATE_MISSING",
        risk_type="data_consistency",
        severity="P1",
        title_template="{entity} 创建成功但查询不到",
        api_pattern="POST /{prefix}/{entity}s",
        trigger_template="创建 {entity} 后立即查询",
        expected_status=200,
        oracle_signal="created entity not found on subsequent GET",
    ),
    BugTemplate(
        template_id="DATA_UPDATE_STALE",
        risk_type="data_consistency",
        severity="P1",
        title_template="{entity} 修改后查询仍返回旧数据",
        api_pattern="PUT /{prefix}/{entity}s/{id}",
        trigger_template="修改 {entity} 后立即查询",
        expected_status=200,
        oracle_signal="response shows pre-update values",
    ),

    # ── Idempotency ──
    BugTemplate(
        template_id="IDEMPOTENCY_DUPLICATE_CREATE",
        risk_type="idempotency",
        severity="P1",
        title_template="重复提交创建多个 {entity}",
        api_pattern="POST /{prefix}/{entity}s",
        trigger_template="相同 idempotency key 创建多个 {entity}",
        expected_status=200,
        oracle_signal="duplicate entity ids for same key",
    ),

    # ── Input Validation ──
    BugTemplate(
        template_id="INPUT_NEGATIVE_QUANTITY",
        risk_type="input_validation",
        severity="P1",
        title_template="{entity} 接受负数/非法值",
        api_pattern="POST /{prefix}/{entity}s",
        trigger_template="提交负数/超限 {entity} 参数",
        expected_status=400,
        oracle_signal="status_code in [200, 201]",
    ),
    BugTemplate(
        template_id="INPUT_BOUNDARY_OVERFLOW",
        risk_type="input_validation",
        severity="P1",
        title_template="{entity} 数值溢出未校验",
        api_pattern="POST /{prefix}/{entity}s",
        trigger_template="提交超大数值参数",
        expected_status=400,
        oracle_signal="status_code == 200 with overflow value",
    ),

    # ── Audit / Traceability ──
    BugTemplate(
        template_id="AUDIT_MISSING_LOG",
        risk_type="audit_traceability",
        severity="P2",
        title_template="敏感 {entity} 操作未记录审计日志",
        api_pattern="POST /{prefix}/{entity}s/{id}/{action}",
        trigger_template="执行敏感 {entity} 操作后检查审计日志",
        expected_status=200,
        oracle_signal="no audit event created for sensitive action",
    ),

    # ── Workflow / Approval ──
    BugTemplate(
        template_id="WORKFLOW_APPROVAL_BYPASS",
        risk_type="workflow_bypass",
        severity="P1",
        title_template="{entity} 审批流程可被绕过",
        api_pattern="POST /{prefix}/{entity}s/{id}/approve",
        trigger_template="未授权角色审批 {entity}",
        expected_status=403,
        oracle_signal="status_code == 200",
    ),

    # ── Concurrency ──
    BugTemplate(
        template_id="CONCURRENCY_RACE_CONDITION",
        risk_type="concurrency",
        severity="P1",
        title_template="并发操作导致 {entity} 数据不一致",
        api_pattern="POST /{prefix}/{entity}s",
        trigger_template="并发提交多个 {entity} 请求",
        expected_status=200,
        oracle_signal="invariant violated after concurrent mutations",
    ),

    # ── Money / Quantity Conservation ──
    BugTemplate(
        template_id="MONEY_NEGATIVE_AMOUNT",
        risk_type="money_consistency",
        severity="P0",
        title_template="{entity} 接受负金额/负数量",
        api_pattern="POST /{prefix}/{entity}s",
        trigger_template="提交负数 {entity} 金额/数量",
        expected_status=400,
        oracle_signal="status_code in [200, 201]",
    ),
    BugTemplate(
        template_id="MONEY_BALANCE_MISMATCH",
        risk_type="money_consistency",
        severity="P0",
        title_template="{entity} 金额/数量与关联记录不一致",
        api_pattern="GET /{prefix}/{entity}s/{id}",
        trigger_template="比较 {entity} 记录与关联账目",
        expected_status=200,
        oracle_signal="balance != sum of ledger entries",
    ),

    # ── Visibility / Data Disclosure ──
    BugTemplate(
        template_id="VISIBILITY_LIST_LEAK",
        risk_type="visibility_disclosure",
        severity="P1",
        title_template="{entity} 列表接口泄露受限数据",
        api_pattern="GET /{prefix}/{entity}s",
        trigger_template="低权限角色请求 {entity} 列表",
        expected_status=200,
        oracle_signal="response contains entities outside actor scope",
    ),
    BugTemplate(
        template_id="VISIBILITY_EXPORT_LEAK",
        risk_type="visibility_disclosure",
        severity="P1",
        title_template="{entity} 导出接口泄露跨范围数据",
        api_pattern="GET /{prefix}/{entity}s/export",
        trigger_template="导出 {entity} 数据",
        expected_status=200,
        oracle_signal="export contains data from unauthorized scope",
    ),

    # ── Async / Eventual Consistency ──
    BugTemplate(
        template_id="ASYNC_CALLBACK_LOST",
        risk_type="async_eventual_consistency",
        severity="P1",
        title_template="{entity} 异步回调丢失导致状态不一致",
        api_pattern="POST /{prefix}/{entity}s/callback",
        trigger_template="模拟异步回调丢失场景",
        expected_status=200,
        oracle_signal="state not updated after expected callback window",
    ),
    BugTemplate(
        template_id="ASYNC_DUPLICATE_CONSUME",
        risk_type="async_eventual_consistency",
        severity="P1",
        title_template="{entity} 消息重复消费",
        api_pattern="POST /{prefix}/{entity}s",
        trigger_template="重复发送相同消息",
        expected_status=200,
        oracle_signal="duplicate side effects observed",
    ),

    # ── Cache / Stale State ──
    BugTemplate(
        template_id="CACHE_STALE_AFTER_WRITE",
        risk_type="cache_staleness",
        severity="P1",
        title_template="{entity} 修改后缓存未失效",
        api_pattern="PUT /{prefix}/{entity}s/{id}",
        trigger_template="修改 {entity} 后立即查询",
        expected_status=200,
        oracle_signal="response shows pre-update values (cache hit)",
    ),

    # ── Configuration / Environment ──
    BugTemplate(
        template_id="CONFIG_SECRET_EXPOSURE",
        risk_type="configuration_environment",
        severity="P0",
        title_template="{entity} 接口泄露密钥/配置信息",
        api_pattern="GET /{prefix}/{entity}s",
        trigger_template="请求 {entity} 接口检查响应",
        expected_status=200,
        oracle_signal="response contains secret/token/credential",
    ),

    # ── UI/API Contract Drift ──
    BugTemplate(
        template_id="UI_API_FIELD_MISMATCH",
        risk_type="ui_api_contract_drift",
        severity="P1",
        title_template="{entity} UI 字段与 API 响应不一致",
        api_pattern="GET /{prefix}/{entity}s/{id}",
        trigger_template="对比 UI 展示字段与 API 响应字段",
        expected_status=200,
        oracle_signal="UI field missing or different from API response",
    ),

    # ── Regression / Historical Bug ──
    BugTemplate(
        template_id="REGRESSION_PREVIOUS_BUG",
        risk_type="regression_historical_bug",
        severity="P1",
        title_template="已修复 {entity} 缺陷回归",
        api_pattern="GET /{prefix}/{entity}s/{id}",
        trigger_template="回归测试已修复的 {entity} 缺陷",
        expected_status=200,
        oracle_signal="previously fixed bug behavior reappears",
    ),
]


# ═════════════════════════════════════════════════════════════════════════════
# Built-in Industry Profiles
# ═════════════════════════════════════════════════════════════════════════════

BUILTIN_INDUSTRIES: dict[str, IndustryProfile] = {
    "crm": IndustryProfile(
        industry_id="crm",
        display_name="CRM 客户关系管理",
        entities=["contact", "lead", "opportunity", "account", "activity", "report"],
        roles=["sales_rep", "sales_manager", "admin", "marketing", "auditor"],
        api_prefix="/api/v1",
        auth_endpoints=["POST /auth/login", "GET /auth/me"],
        business_invariants=[
            "销售代表只能查看自己的客户和商机",
            "客户转移必须记录审计日志",
            "报表数据不能跨团队泄露",
        ],
        tenant_aware=True,
    ),
    "ecommerce": IndustryProfile(
        industry_id="ecommerce",
        display_name="电商平台",
        entities=["order", "product", "payment", "refund", "cart", "coupon", "inventory"],
        roles=["customer", "seller", "admin", "auditor"],
        api_prefix="/api/v1",
        auth_endpoints=["POST /auth/login", "GET /auth/me"],
        business_invariants=[
            "客户只能查看和操作自己的订单",
            "库存不能为负数",
            "支付金额必须等于订单金额",
            "退款金额不能超过支付金额",
            "重复回调不能重复入账",
        ],
        tenant_aware=False,
    ),
    "erp": IndustryProfile(
        industry_id="erp",
        display_name="ERP 企业资源计划",
        entities=["purchase_order", "invoice", "inventory_item", "warehouse", "vendor", "ledger_entry"],
        roles=["operator", "supervisor", "finance", "admin", "auditor"],
        api_prefix="/api/v1",
        auth_endpoints=["POST /auth/login", "GET /auth/me"],
        business_invariants=[
            "采购单审批必须经授权人",
            "库存入库/出库必须原子操作",
            "财务分类账必须平衡",
            "发票金额必须与采购单匹配",
        ],
        tenant_aware=True,
    ),
    "finance": IndustryProfile(
        industry_id="finance",
        display_name="金融系统",
        entities=["transaction", "account", "loan", "repayment", "ledger", "compliance_report"],
        roles=["customer", "teller", "manager", "compliance_officer", "admin", "auditor"],
        api_prefix="/api/v1",
        auth_endpoints=["POST /auth/login", "GET /auth/me"],
        business_invariants=[
            "交易必须原子完成，不能部分成功",
            "账户余额不能为负（除非有授信）",
            "所有交易必须有完整的审计追踪",
            "合规报告必须包含所有必要字段",
        ],
        tenant_aware=True,
    ),
    "medical": IndustryProfile(
        industry_id="medical",
        display_name="医疗系统",
        entities=["patient", "appointment", "prescription", "medical_record", "lab_result", "billing"],
        roles=["patient", "doctor", "nurse", "admin", "auditor", "billing_staff"],
        api_prefix="/api/v1",
        auth_endpoints=["POST /auth/login", "GET /auth/me"],
        business_invariants=[
            "患者数据只能由授权医护人员访问",
            "处方必须由有资质的医生开具",
            "病历修改必须记录完整审计日志",
            "预约不能超容量",
        ],
        tenant_aware=True,
    ),
    "education": IndustryProfile(
        industry_id="education",
        display_name="教育系统",
        entities=["student", "course", "enrollment", "grade", "assignment", "transcript"],
        roles=["student", "teacher", "admin", "registrar", "auditor"],
        api_prefix="/api/v1",
        auth_endpoints=["POST /auth/login", "GET /auth/me"],
        business_invariants=[
            "学生只能查看自己的成绩",
            "课程注册不能超容量",
            "成绩修改必须经授权",
            "先修课要求必须满足",
        ],
        tenant_aware=True,
    ),
    "saas": IndustryProfile(
        industry_id="saas",
        display_name="SaaS 多租户平台",
        entities=["workspace", "user", "subscription", "feature_flag", "audit_log", "api_key"],
        roles=["member", "workspace_admin", "platform_admin", "billing_admin", "auditor"],
        api_prefix="/api/v1",
        auth_endpoints=["POST /auth/login", "GET /auth/me"],
        business_invariants=[
            "租户之间数据必须完全隔离",
            "功能开关不能绕过鉴权",
            "订阅到期必须限制访问",
            "API Key 不能跨租户使用",
        ],
        tenant_aware=True,
    ),
}


# ═════════════════════════════════════════════════════════════════════════════
# Variant Dimensions (cross-industry)
# ═════════════════════════════════════════════════════════════════════════════

VARIANT_AUTH_STATES = ["logged_in", "anonymous", "expired_token", "locked"]
VARIANT_DATA_CONDITIONS = ["normal", "boundary", "expired", "duplicate", "mismatch", "negative", "over_limit"]


# ═════════════════════════════════════════════════════════════════════════════
# Benchmark Bug Factory
# ═════════════════════════════════════════════════════════════════════════════

class BenchmarkBugFactory:
    """Generate known bugs for any industry, with blind-discipline ground truth.

    Usage::

        factory = BenchmarkBugFactory("ecommerce")
        bugs = factory.generate(count=50, seed=42)
        gt_path = factory.write_ground_truth(bugs)
        public = factory.generate_public_artifacts(bugs)
        seeding = factory.build_runtime_seeds(bugs)
    """

    def __init__(
        self,
        industry: str,
        *,
        extra_templates: list[BugTemplate] | None = None,
        custom_profile: IndustryProfile | None = None,
    ) -> None:
        """Initialise the factory for a given industry.

        Args:
            industry: Industry ID (one of BUILTIN_INDUSTRIES keys or custom).
            extra_templates: Additional BugTemplate objects beyond UNIVERSAL_TEMPLATES.
            custom_profile: Override or extend the built-in industry profile.
        """
        if custom_profile is not None:
            self.profile = custom_profile
        elif industry in BUILTIN_INDUSTRIES:
            self.profile = BUILTIN_INDUSTRIES[industry]
        else:
            raise ValueError(
                f"Unknown industry '{industry}'. "
                f"Available: {sorted(BUILTIN_INDUSTRIES.keys())}. "
                f"Or provide custom_profile."
            )

        self.industry = industry
        self.templates: list[BugTemplate] = list(UNIVERSAL_TEMPLATES)
        if extra_templates:
            self.templates.extend(extra_templates)

        # ── Log ──
        self._log: list[str] = []
        self._log.append(
            f"BenchmarkBugFactory init: industry={industry}, "
            f"templates={len(self.templates)}, "
            f"entities={self.profile.entities}, "
            f"roles={self.profile.roles}"
        )

    # ── Bug Generation ──────────────────────────────────────────────────

    def generate(
        self,
        count: int = 50,
        seed: int | None = None,
        *,
        min_severity: str = "P2",
    ) -> list[dict[str, Any]]:
        """Generate a catalog of concrete bug instances.

        Args:
            count: Target number of bug instances.
            seed: Random seed for reproducibility.
            min_severity: Minimum severity to include (P0 > P1 > P2).

        Returns:
            List of bug dicts, each containing oracle data for ground truth.
        """
        rng = random.Random(seed if seed is not None else int(time.time() * 1000))
        severity_order = {"P0": 0, "P1": 1, "P2": 2}
        min_level = severity_order.get(min_severity, 2)

        # Filter templates by severity floor
        eligible = [
            t for t in self.templates
            if severity_order.get(t.severity, 2) <= min_level
        ]
        if not eligible:
            eligible = list(self.templates)

        bugs: list[dict[str, Any]] = []
        seq = 1
        max_attempts = count * 3  # safety valve

        while len(bugs) < count and max_attempts > 0:
            max_attempts -= 1
            rng.shuffle(eligible)
            for template in eligible:
                bug = self._instantiate(template, seq, rng)
                bugs.append(bug)
                seq += 1
                if len(bugs) >= count:
                    break

        self._log.append(
            f"generate: requested={count}, produced={len(bugs)}, seed={seed}"
        )
        if len(bugs) < count:
            self._log.append(
                f"WARNING: generate() produced only {len(bugs)}/{count} bugs "
                f"(max_attempts exhausted). Consider increasing template variety "
                f"or lowering min_severity filter."
            )
        return bugs

    def _instantiate(
        self, template: BugTemplate, sequence: int, rng: random.Random
    ) -> dict[str, Any]:
        """Create one concrete bug instance from a template and industry profile."""
        profile = self.profile

        # Pick variant dimensions
        entity = rng.choice(profile.entities)
        role_low = rng.choice(profile.roles[: len(profile.roles) // 2] or profile.roles[:1])
        role_high = rng.choice(profile.roles[len(profile.roles) // 2 :] or profile.roles[-1:])
        role_a, role_b = rng.sample(profile.roles, 2) if len(profile.roles) >= 2 else (profile.roles[0], profile.roles[0])

        # Pick an action based on the API pattern
        method = template.api_pattern.split(" ", 1)[0]
        action = self._action_for_method(method)

        # Fill template strings
        title = template.title_template.format(
            entity=entity,
            low_role=role_low,
            high_role=role_high,
            role_a=role_a,
            role_b=role_b,
            action=action,
            target=f"{role_high}'s {entity}",
        )

        api = template.api_pattern.format(
            prefix=profile.api_prefix.lstrip("/"),
            entity=entity,
            param="{id}",
            id="{id}",
            action=action,
        )

        trigger = template.trigger_template.format(
            entity=entity,
            api=api,
            low_role=role_low,
            high_role=role_high,
            role_a=role_a,
            role_b=role_b,
            action=action,
        )

        # Build bug ID
        bug_id = f"{template.template_id}_{sequence:04d}"
        bug_instance_id = f"{self.industry}_{template.template_id}_{entity}_{sequence:04d}"

        return {
            "bug_id": bug_id,
            "bug_instance_id": bug_instance_id,
            "template_id": template.template_id,
            "industry": self.industry,
            "industry_display": profile.display_name,
            "risk_type": template.risk_type,
            "severity": template.severity,
            "title": title,
            "trigger": trigger,
            "api": api,
            "method": method,
            "expected_status": template.expected_status,
            "expected_behavior": f"{api} 应返回 {template.expected_status} 或拒绝操作",
            "actual_bug_behavior": f"缺陷实例违反 {template.risk_type} 不变量",
            "oracle": {
                "type": template.risk_type,
                "expected_status": template.expected_status,
                "bug_signal": template.oracle_signal,
                "entity": entity,
                "method": method,
                "path_pattern": api,
            },
            "evidence_required": list(template.evidence_required),
            "variant_dimensions": {
                "actor": role_low,
                "entity": entity,
                "operation": action,
                "auth_state": rng.choice(VARIANT_AUTH_STATES),
                "data_condition": rng.choice(VARIANT_DATA_CONDITIONS),
                "tenant_scope": "cross_tenant" if profile.tenant_aware and template.risk_type == "tenant_isolation" else "same_tenant",
                "variant_index": str(sequence),
            },
            "enabled": True,
        }

    @staticmethod
    def _action_for_method(method: str) -> str:
        mapping = {"GET": "view", "POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}
        return mapping.get(method.upper(), "access")

    # ── Ground Truth Storage ────────────────────────────────────────────

    def write_ground_truth(
        self,
        bugs: list[dict[str, Any]],
        output_dir: str | Path | None = None,
    ) -> Path:
        """Write ground truth bugs to a PRIVATE_BLOCKLIST-protected location.

        The output path will contain 'private_ground_truth' as a path component,
        ensuring the discovery engine's PRIVATE_BLOCKLIST filter blocks access.

        Args:
            bugs: Bug instances from generate().
            output_dir: Base directory. Defaults to platform_workspace.

        Returns:
            Path to the written ground_truth_bugs.json file.
        """
        if output_dir is None:
            output_dir = Path(os.environ.get(
                "QUALIBUG_WORKSPACE_ROOT",
                str(Path(__file__).resolve().parents[1])
            ))

        base = Path(output_dir)
        # Path MUST include a PRIVATE_BLOCKLIST token
        gt_dir = base / "platform_workspace" / self.industry / _PRIVATE_MARKER
        gt_dir.mkdir(parents=True, exist_ok=True)

        # Write full ground truth (with oracle data)
        gt_path = gt_dir / "ground_truth_bugs.json"
        payload = {
            "schema_version": "benchmark_bug_factory.v1",
            "industry": self.industry,
            "industry_display": self.profile.display_name,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_bugs": len(bugs),
            "templates_used": sorted({b["template_id"] for b in bugs}),
            "bugs": bugs,
        }
        try:
            gt_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, IOError) as e:
            raise IOError(f"Failed to write ground truth to {gt_path}: {e}") from e

        self._log.append(
            f"write_ground_truth: {len(bugs)} bugs -> {gt_path} "
            f"(BLOCKLIST token '{_PRIVATE_MARKER}' in path)"
        )
        return gt_path

    # ── Public Artifacts ────────────────────────────────────────────────

    def generate_public_artifacts(
        self,
        bugs: list[dict[str, Any]],
        output_dir: str | Path | None = None,
    ) -> dict[str, Path]:
        """Generate public (non-oracle) artifacts for blind discovery.

        Produces:
          - openapi_stub.json: minimal OpenAPI 3.0 spec covering the bug APIs
          - prd_excerpt.md: business rules derived from industry invariants
          - accounts_stub.json: role definitions (no real credentials)

        These files contain NO oracle data and are safe for the discovery engine.

        Args:
            bugs: Bug instances (used to extract API surface).
            output_dir: Base directory.

        Returns:
            Dict mapping artifact name to file Path.
        """
        if output_dir is None:
            output_dir = Path(os.environ.get(
                "QUALIBUG_WORKSPACE_ROOT",
                str(Path(__file__).resolve().parents[1])
            ))

        base = Path(output_dir)
        public_dir = base / "platform_inputs" / self.industry / "input"
        public_dir.mkdir(parents=True, exist_ok=True)

        result: dict[str, Path] = {}

        # ── OpenAPI stub ──
        openapi_path = public_dir / "openapi.json"
        openapi_spec = self._build_openapi_stub(bugs)
        try:
            openapi_path.write_text(json.dumps(openapi_spec, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, IOError) as e:
            raise IOError(f"Failed to write OpenAPI stub to {openapi_path}: {e}") from e
        result["openapi"] = openapi_path

        # ── PRD excerpt ──
        prd_path = public_dir / "PRD.md"
        prd_content = self._build_prd_excerpt(bugs)
        try:
            prd_path.write_text(prd_content, encoding="utf-8")
        except (OSError, IOError) as e:
            raise IOError(f"Failed to write PRD to {prd_path}: {e}") from e
        result["prd"] = prd_path

        # ── Accounts stub ──
        accounts_path = public_dir / "accounts.json"
        accounts_stub = self._build_accounts_stub()
        try:
            accounts_path.write_text(json.dumps(accounts_stub, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, IOError) as e:
            raise IOError(f"Failed to write accounts stub to {accounts_path}: {e}") from e
        result["accounts"] = accounts_path

        self._log.append(
            f"generate_public_artifacts: {len(result)} files -> {public_dir}"
        )
        return result

    def _build_openapi_stub(self, bugs: list[dict[str, Any]]) -> dict[str, Any]:
        """Build a minimal OpenAPI 3.0 spec exposing the API surface from bugs."""
        # Collect unique (method, path) pairs
        paths: dict[str, dict[str, Any]] = {}
        seen = set()
        for bug in bugs:
            method = bug.get("method", "GET").lower()
            path = bug.get("api", "/").split("?", 1)[0].strip()
            # Normalize path params to OpenAPI style
            path = path.replace("{id}", "{id}")  # keep as-is
            key = (method, path)
            if key in seen:
                continue
            seen.add(key)

            paths.setdefault(path, {})[method] = {
                "summary": bug.get("title", f"{method.upper()} {path}"),
                "operationId": f"{method}_{bug.get('template_id', 'op').lower()}_{len(seen)}",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                ] if "{id}" in path else [],
                "responses": {
                    "200": {"description": "成功"},
                    "400": {"description": "请求错误"},
                    "401": {"description": "未认证"},
                    "403": {"description": "无权限"},
                    "409": {"description": "业务冲突"},
                },
            }

        spec = {
            "openapi": "3.0.3",
            "info": {
                "title": f"{self.profile.display_name} API (Benchmark Stub)",
                "version": "1.0.0",
                "description": (
                    f"Auto-generated API stub for {self.profile.display_name} benchmark. "
                    f"This is a minimal surface description for blind discovery; "
                    f"it does NOT contain oracle data or ground truth."
                ),
            },
            "servers": [{"url": "http://localhost:8000", "description": "Benchmark runtime target"}],
            "paths": paths,
        }
        return spec

    def _build_prd_excerpt(self, bugs: list[dict[str, Any]]) -> str:
        """Build a PRD excerpt from industry invariants and bug risk types."""
        lines = [
            f"# {self.profile.display_name} - 产品需求摘要",
            "",
            "> 此文档由 Benchmark Bug Factory 自动生成，用于盲测发现。",
            "> 不包含 Oracle 数据或隐藏缺陷信息。",
            "",
            "## 核心业务规则",
            "",
        ]
        for i, invariant in enumerate(self.profile.business_invariants, 1):
            lines.append(f"{i}. {invariant}")
        lines.append("")

        # Add entity descriptions
        lines.append("## 业务实体")
        lines.append("")
        for entity in self.profile.entities:
            lines.append(f"- **{entity}**: {self.profile.display_name}核心实体")
        lines.append("")

        # Add role descriptions
        lines.append("## 用户角色")
        lines.append("")
        for role in self.profile.roles:
            lines.append(f"- **{role}**: {self.profile.display_name}系统角色")
        lines.append("")

        # Add risk summary (from bug types, NOT individual bugs)
        risk_types = sorted({b["risk_type"] for b in bugs})
        lines.append("## 质量风险关注点")
        lines.append("")
        for rt in risk_types:
            lines.append(f"- {rt}")
        lines.append("")

        return "\n".join(lines)

    def _build_accounts_stub(self) -> dict[str, Any]:
        """Build a role/account stub with NO real credentials."""
        accounts = []
        for i, role in enumerate(self.profile.roles, 1):
            accounts.append({
                "role_id": role,
                "display_name": role.replace("_", " ").title(),
                "permission_level": "read" if i <= len(self.profile.roles) // 2 else "write",
                "note": f"Benchmark identity for {role} — no real credentials",
            })
        return {
            "industry": self.industry,
            "auth_endpoints": self.profile.auth_endpoints,
            "tenant_aware": self.profile.tenant_aware,
            "accounts": accounts,
        }

    # ── Runtime Seeding ─────────────────────────────────────────────────

    def build_runtime_seeds(
        self,
        bugs: list[dict[str, Any]],
        target_url: str = "http://localhost:8000",
    ) -> dict[str, Any]:
        """Build a seeding manifest for benchmark_runtime.

        The runtime target reads this manifest and makes every bug surface
        exhibit deliberately flawed behavior when probed.

        Args:
            bugs: Bug instances.
            target_url: URL of the benchmark runtime target.

        Returns:
            Seeding manifest dict, suitable for writing to BUG_GROUND_TRUTH.json.
        """
        seed_bugs = []
        for bug in bugs:
            method = bug.get("method", "GET")
            api = bug.get("api", "/")
            seed_bugs.append({
                "bug_id": bug["bug_id"],
                "bug_instance_id": bug["bug_instance_id"],
                "template_id": bug["template_id"],
                "risk_type": bug["risk_type"],
                "severity": bug["severity"],
                "title": bug["title"],
                "method": method,
                "path_pattern": api,
                "expected_status": bug["expected_status"],
                "oracle_signal": bug["oracle"]["bug_signal"],
                "trigger_description": bug["trigger"],
            })

        manifest = {
            "schema_version": "benchmark_runtime_seeds.v1",
            "industry": self.industry,
            "target_url": target_url,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_seeds": len(seed_bugs),
            "seeds": seed_bugs,
        }

        self._log.append(
            f"build_runtime_seeds: {len(seed_bugs)} seeds for {target_url}"
        )
        return manifest

    # ── Log ─────────────────────────────────────────────────────────────

    def get_log(self) -> list[str]:
        """Return the internal operation log for observability."""
        return list(self._log)

    def print_log(self) -> None:
        """Print the operation log to stdout."""
        for line in self._log:
            print(f"[BenchmarkBugFactory] {line}")


# ═════════════════════════════════════════════════════════════════════════════
# Convenience: end-to-end benchmark preparation for a given industry
# ═════════════════════════════════════════════════════════════════════════════

def prepare_industry_benchmark(
    industry: str,
    bug_count: int = 50,
    seed: int | None = None,
    *,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    """One-shot: generate bugs, write ground truth, and produce public artifacts.

    Returns a dict with paths and counts for downstream consumption.
    """
    factory = BenchmarkBugFactory(industry)
    bugs = factory.generate(count=bug_count, seed=seed)
    gt_path = factory.write_ground_truth(bugs, output_dir=output_root)
    public_paths = factory.generate_public_artifacts(bugs, output_dir=output_root)
    seeds = factory.build_runtime_seeds(bugs)

    result = {
        "industry": industry,
        "bug_count": len(bugs),
        "ground_truth_path": str(gt_path),
        "public_artifacts": {k: str(v) for k, v in public_paths.items()},
        "runtime_seeds": seeds,
        "log": factory.get_log(),
    }
    return result


# ═════════════════════════════════════════════════════════════════════════════
# Module-level helpers
# ═════════════════════════════════════════════════════════════════════════════

def list_industries() -> list[str]:
    """Return the list of built-in industry IDs."""
    return sorted(BUILTIN_INDUSTRIES.keys())


def get_industry_profile(industry: str) -> IndustryProfile | None:
    """Get a built-in industry profile by ID."""
    return BUILTIN_INDUSTRIES.get(industry)


def validate_ground_truth_integrity(ground_truth_path: Path) -> dict[str, Any]:
    """Validate that a ground truth file exists and its path contains BLOCKLIST tokens.

    Returns a dict with 'valid' (bool) and 'reason' (str).
    """
    if not ground_truth_path.exists():
        return {"valid": False, "reason": f"File not found: {ground_truth_path}"}

    path_str = str(ground_truth_path).lower().replace("\\", "/")
    has_blocklist_token = any(token.lower() in path_str for token in _BLOCKLIST_TOKENS)
    if not has_blocklist_token:
        return {
            "valid": False,
            "reason": (
                f"Path does not contain any PRIVATE_BLOCKLIST token. "
                f"Required tokens: {_BLOCKLIST_TOKENS}. "
                f"Ground truth MUST be stored under a path containing one of these tokens "
                f"to ensure the discovery engine cannot read it."
            ),
        }

    try:
        data = json.loads(ground_truth_path.read_text(encoding="utf-8"))
        bugs = data.get("bugs", [])
        if not bugs:
            return {"valid": False, "reason": "Ground truth file contains no bugs"}
        if not isinstance(bugs, list):
            return {"valid": False, "reason": f"Expected 'bugs' to be a list, got {type(bugs).__name__}"}

        # Validate individual bug structures
        required_bug_fields = {"bug_id", "template_id", "risk_type", "severity", "oracle"}
        invalid_bugs = []
        for i, bug in enumerate(bugs):
            if not isinstance(bug, dict):
                invalid_bugs.append(f"bug[{i}]: not a dict")
                continue
            missing = required_bug_fields - set(bug.keys())
            if missing:
                invalid_bugs.append(f"bug[{i}] {bug.get('bug_id', '?')}: missing {missing}")
        if invalid_bugs:
            return {
                "valid": False,
                "reason": f"{len(invalid_bugs)} bugs have invalid structure: {'; '.join(invalid_bugs[:5])}",
            }

        return {"valid": True, "bug_count": len(bugs), "industry": data.get("industry", "unknown")}
    except json.JSONDecodeError as e:
        return {"valid": False, "reason": f"Invalid JSON: {e}"}
    except Exception as e:
        return {"valid": False, "reason": f"Read error: {e}"}
