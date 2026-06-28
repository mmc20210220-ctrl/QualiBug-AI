"""
QualiBug AI 分析器模块

提供多种专业的分析器，用于提升bug发现能力。
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
]
