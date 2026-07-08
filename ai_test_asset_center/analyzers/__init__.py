"""
QualiBug AI 分析器模块

提供多种专业的分析器，用于提升bug发现能力。

第1阶段：
- business_rules - 业务规则分析 (C01, C08, C09, C13)
- state_machine - 状态机分析 (C06, C07)
- multi_tenant - 多租户隔离分析 (C05)
- conservation - 守恒规则分析 (C08)

第2阶段：
- concurrency - 并发与竞态分析 (C11)
- async_task - 异步任务分析 (C20)
- cache_consistency - 缓存一致性分析 (C21)
- authorization - 认证授权分析 (C03, C04)
"""

from .business_rules import (
    BusinessRulesAnalyzer,
    analyze_prd_rules
)

from .state_machine import (
    StateMachineAnalyzer,
    analyze_state_machine
)

from .multi_tenant import (
    MultiTenantAnalyzer,
    analyze_multi_tenant_isolation
)

from .conservation import (
    ConservationAnalyzer,
    analyze_conservation_rules
)

from .concurrency import (
    ConcurrencyAnalyzer,
    analyze_concurrency
)

from .async_task import (
    AsyncTaskAnalyzer,
    analyze_async_tasks
)

from .cache_consistency import (
    CacheConsistencyAnalyzer,
    analyze_cache_consistency
)

from .authorization import (
    AuthorizationAnalyzer,
    analyze_authorization
)

from .ui_api_availability import (
    UIApiAvailabilityCheck,
    check_api_ui_availability,
    scan_api_ui_availability,
    build_findings_from_checks,
)

__all__ = [
    # Business Rules
    'BusinessRulesAnalyzer',
    'analyze_prd_rules',
    # State Machine
    'StateMachineAnalyzer',
    'analyze_state_machine',
    # Multi-Tenant
    'MultiTenantAnalyzer',
    'analyze_multi_tenant_isolation',
    # Conservation
    'ConservationAnalyzer',
    'analyze_conservation_rules',
    # Concurrency
    'ConcurrencyAnalyzer',
    'analyze_concurrency',
    # Async Task
    'AsyncTaskAnalyzer',
    'analyze_async_tasks',
    # Cache Consistency
    'CacheConsistencyAnalyzer',
    'analyze_cache_consistency',
    # Authorization
    'AuthorizationAnalyzer',
    'analyze_authorization',
    # UI/API Availability (P3-13)
    'UIApiAvailabilityCheck',
    'check_api_ui_availability',
    'scan_api_ui_availability',
    'build_findings_from_checks',
    # Ontology seed family registration
    'register_analyzer_seed_families',
]


# ── Bug Ontology Seed Family Registration ─────────────────────────────
# P3+: Register all existing analyzers as seed families in the Bug
# Ontology Registry. This preserves the existing 20+ bug types as
# seed families while the system expands to 80+ subtypes via the
# ontology-driven architecture. Legacy C-codes become aliases;
# each analyzer maps to one or more risk_family + subtype entries.

def register_analyzer_seed_families():
    """Register the 8 existing analyzers as seed families in the Bug Ontology Registry.

    This function is idempotent — it can be called multiple times safely.
    Returns the number of seed families registered.
    """
    try:
        from ..bug_ontology_registry import get_ontology_registry
    except ImportError:
        return 0

    registry = get_ontology_registry()
    count = 0

    # ── business_rules → state_machine + conservation + business rules ──
    count += 1
    registry.register_seed_family(
        family_id="state_machine",
        subtype="business_rule_c01",
        display_name="业务规则校验 (C01)",
        description="PRD定义的业务规则未在后端强制校验",
        invariant="后端必须强制校验PRD定义的业务规则",
        scenario="用违反PRD规则的数据发送请求",
        evidence=["prd_rule_ref", "request_raw", "response_raw"],
        severity="P2",
        source="seed_analyzer:business_rules",
    )

    # ── state_machine → C06, C07 ──
    count += 1
    registry.register_seed_family(
        family_id="state_machine",
        subtype="invalid_transition_c06",
        display_name="非法状态跳转 (C06)",
        description="实体跳转到PRD不允许的状态",
        invariant="target_state in current_state.allowed_next_states",
        scenario="从当前状态尝试非法跳转到PRD禁止的状态",
        evidence=["before_state", "after_state", "allowed_transitions"],
        severity="P1",
        source="seed_analyzer:state_machine",
    )
    count += 1
    registry.register_seed_family(
        family_id="state_machine",
        subtype="final_state_modification_c07",
        display_name="终态修改 (C07)",
        description="对已完成/已取消的实体进行修改操作",
        invariant="entity.state.is_final → entity is immutable",
        scenario="尝试修改已取消或已完成的订单",
        evidence=["before_state", "response_raw", "is_final"],
        severity="P1",
        source="seed_analyzer:state_machine",
    )

    # ── multi_tenant → C05 ──
    count += 1
    registry.register_seed_family(
        family_id="tenant_isolation",
        subtype="tenant_isolation_c05",
        display_name="多租户隔离 (C05)",
        description="API端点未包含租户隔离参数导致跨租户数据访问",
        invariant="data.tenant_id == actor.tenant_id for all reads",
        scenario="用租户A的token访问租户B的资源端点",
        evidence=["request_raw", "response_raw", "tenant_a_token", "tenant_b_token"],
        severity="P0",
        source="seed_analyzer:multi_tenant",
    )

    # ── conservation → C08 ──
    count += 1
    registry.register_seed_family(
        family_id="conservation",
        subtype="conservation_c08",
        display_name="守恒规则违规 (C08)",
        description="库存/余额/积分等守恒规则被破坏",
        invariant="sum(before) == sum(after) for conserved quantity",
        scenario="执行写操作后对比前后守恒字段",
        evidence=["before_snapshot", "after_snapshot", "conserved_field"],
        severity="P0",
        source="seed_analyzer:conservation",
    )

    # ── concurrency → C11 ──
    count += 1
    registry.register_seed_family(
        family_id="concurrency",
        subtype="concurrency_c11",
        display_name="并发竞态 (C11)",
        description="并发操作导致数据不一致或竞态条件",
        invariant="concurrent operations produce consistent results",
        scenario="同时发送两个修改同一实体的请求",
        evidence=["concurrent_requests", "final_state", "expected_state"],
        severity="P1",
        source="seed_analyzer:concurrency",
    )

    # ── async_task → C20 ──
    count += 1
    registry.register_seed_family(
        family_id="eventual_consistency",
        subtype="async_task_c20",
        display_name="异步任务失败处理 (C20)",
        description="异步任务缺少重试或死信队列处理",
        invariant="failed async tasks are retried with backoff",
        scenario="模拟异步任务超时或失败",
        evidence=["task_timeout", "retry_attempts", "dlq_status"],
        severity="P1",
        source="seed_analyzer:async_task",
    )

    # ── cache_consistency → C21 ──
    count += 1
    registry.register_seed_family(
        family_id="data_integrity",
        subtype="cache_consistency_c21",
        display_name="缓存一致性 (C21)",
        description="写操作后缓存与数据库不一致",
        invariant="cache[key].data == db.record.data after write",
        scenario="写操作后立即对比缓存与DB数据",
        evidence=["cache_value", "db_value", "write_operation"],
        severity="P1",
        source="seed_analyzer:cache_consistency",
    )

    # ── authorization → C03, C04 ──
    count += 1
    registry.register_seed_family(
        family_id="authorization",
        subtype="auth_missing_c03",
        display_name="认证缺失 (C03)",
        description="敏感端点缺少认证保护",
        invariant="protected endpoints require valid authentication",
        scenario="不带token访问受保护端点",
        evidence=["request_raw", "response_raw", "auth_header_present"],
        severity="P1",
        source="seed_analyzer:authorization",
    )

    # ── ui_api_availability → P3-12, P3-13 ──
    count += 1
    registry.register_seed_family(
        family_id="data_integrity",
        subtype="ui_api_mismatch_p3",
        display_name="UI/API可用性不匹配 (P3-12/13)",
        description="前端页面可达但对应API不可用，或反之",
        invariant="UI page and its backing API have consistent availability",
        scenario="检查前端页面和对应API端点的HTTP状态",
        evidence=["ui_response", "api_response", "mismatch_kind"],
        severity="P2",
        source="seed_analyzer:ui_api_availability",
    )

    return count


# Seed family registration is triggered lazily by the pipeline.
# Call ``register_analyzer_seed_families()`` explicitly when needed,
# or use ``_ensure_seed_families_registered()`` for safe one-shot init.
