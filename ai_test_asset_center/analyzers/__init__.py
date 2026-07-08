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
]
