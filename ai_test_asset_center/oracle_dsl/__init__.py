"""Oracle DSL — Turn business promises into strong, executable assertions.

This package provides:
  - DSLParser: Parse WHEN-THEN-WITHIN rules from natural language
  - DSLCompiler: Compile ParsedRule into oracle rules, invariants, and proof obligations
  - RuleLibrary: Pre-built industry rule sets (CRM, ERP, Finance, Medical, etc.)

Usage::

    from ai_test_asset_center.oracle_dsl import DSLParser, DSLCompiler

    parser = DSLParser()
    rules = parser.parse_prd("订单取消后库存必须恢复；支付回调必须幂等")
    
    compiler = DSLCompiler()
    for rule in rules:
        oracle_rules = compiler.compile_to_oracle_rules(rule)
        # → ["StateOracle.state_change_side_effect", "ConsistencyOracle.cross_entity_dependency"]
"""

from .dsl_parser import DSLParser, ParsedRule
from .dsl_compiler import DSLCompiler
from .rule_library import (
    RuleLibrary,
    get_rules_for_industry,
    get_rules_for_recognized_industries,
    normalize_industry_key,
)

__all__ = [
    "DSLParser", "ParsedRule",
    "DSLCompiler",
    "RuleLibrary", "get_rules_for_industry", "get_rules_for_recognized_industries",
    "normalize_industry_key",
]
