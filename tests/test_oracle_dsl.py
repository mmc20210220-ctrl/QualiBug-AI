"""Tests for Oracle DSL — parser, compiler, and rule library."""

from __future__ import annotations

import pytest

from ai_test_asset_center.oracle_dsl.dsl_parser import DSLParser, ParsedRule
from ai_test_asset_center.oracle_dsl.dsl_compiler import (
    DSLCompiler,
    CompiledOracle,
    CompiledInvariant,
    CompiledProofObligation,
)
from ai_test_asset_center.oracle_dsl.rule_library import RuleLibrary, get_rules_for_industry


# ═════════════════════════════════════════════════════════════════════════════
# Parser Tests — English
# ═════════════════════════════════════════════════════════════════════════════

def test_parse_simple_english() -> None:
    parser = DSLParser()
    rule = parser.parse("WHEN customer cancels order THEN inventory must be restored SEVERITY P0")
    assert rule is not None
    assert rule.actor == "customer"
    assert rule.action == "cancels"
    assert rule.entity == "order"
    assert "inventory" in rule.assertion
    assert rule.severity == "P0"
    assert rule.rule_type == "state_change"


def test_parse_english_with_timeout() -> None:
    parser = DSLParser()
    rule = parser.parse("WHEN admin accesses patient_record THEN audit log must contain entry WITHIN 5 minutes SEVERITY P1")
    assert rule is not None
    assert rule.timeout_minutes == 5
    assert rule.rule_type == "audit"


def test_parse_english_idempotency() -> None:
    parser = DSLParser()
    rule = parser.parse("WHEN system receives callback twice THEN order amount must not change SEVERITY P0")
    assert rule is not None
    assert rule.rule_type == "idempotency" or rule.rule_type == "conservation"


def test_parse_english_permission() -> None:
    parser = DSLParser()
    rule = parser.parse("WHEN user views order THEN user must only see own orders SEVERITY P0")
    assert rule is not None
    assert rule.rule_type == "permission" or rule.rule_type == "audit"


def test_parse_english_default_severity() -> None:
    parser = DSLParser()
    rule = parser.parse("WHEN customer places order THEN inventory must be deducted")
    assert rule is not None
    assert rule.severity == "P1"  # Default


# ═════════════════════════════════════════════════════════════════════════════
# Parser Tests — Chinese
# ═════════════════════════════════════════════════════════════════════════════

def test_parse_prd_chinese() -> None:
    parser = DSLParser()
    prd = "下单后扣库存；支付回调幂等；退款不得超过已支付金额；优惠券不能跨用户"
    rules = parser.parse_prd(prd)
    assert len(rules) >= 3


def test_parse_chinese_state_change() -> None:
    parser = DSLParser()
    rule = parser.parse("订单取消后库存必须恢复")
    assert rule is not None
    assert "订单" in rule.entity or "order" in rule.entity


def test_parse_chinese_conservation() -> None:
    parser = DSLParser()
    rule = parser.parse("退款金额不能超过支付金额")
    assert rule is not None
    assert rule.rule_type in ("conservation", "state_change")


def test_parse_empty_text() -> None:
    parser = DSLParser()
    assert parser.parse("") is None
    assert parser.parse("   ") is None


# ═════════════════════════════════════════════════════════════════════════════
# Compiler Tests — Oracle Rules
# ═════════════════════════════════════════════════════════════════════════════

def test_compile_state_change_to_oracle_rules() -> None:
    compiler = DSLCompiler()
    parser = DSLParser()
    rule = parser.parse("WHEN customer cancels order THEN inventory must be restored SEVERITY P0")
    assert rule is not None

    oracle_rules = compiler.compile_to_oracle_rules(rule)
    assert len(oracle_rules) >= 1
    assert any("StateOracle" in r for r in oracle_rules)


def test_compile_conservation_to_oracle_rules() -> None:
    compiler = DSLCompiler()
    parser = DSLParser()
    rule = parser.parse("WHEN refund is processed THEN refund amount must not exceed paid amount SEVERITY P0")
    assert rule is not None

    oracle_rules = compiler.compile_to_oracle_rules(rule)
    assert len(oracle_rules) >= 1
    assert any("MoneyOracle" in r or "ConsistencyOracle" in r for r in oracle_rules)


def test_compile_audit_to_oracle_rules() -> None:
    compiler = DSLCompiler()
    parser = DSLParser()
    rule = parser.parse("WHEN admin accesses patient_record THEN audit log must contain entry WITHIN 5 minutes")
    assert rule is not None

    oracle = compiler.compile_to_oracle_object(rule)
    assert oracle.layer in ("L6", "L3")
    assert len(oracle.oracle_rules) >= 1


def test_compile_idempotency_to_oracle_rules() -> None:
    compiler = DSLCompiler()
    # Build a rule with idempotency type directly
    rule = ParsedRule("T1", "", rule_type="idempotency", entity="order", action="submit", assertion="must be idempotent")

    oracle_rules = compiler.compile_to_oracle_rules(rule)
    assert any("IdempotencyOracle" in r for r in oracle_rules)


def test_compile_permission_to_oracle_rules() -> None:
    compiler = DSLCompiler()
    rule = ParsedRule("T1", "", rule_type="permission", entity="order", actor="user", assertion="must be rejected")

    oracle_rules = compiler.compile_to_oracle_rules(rule)
    assert any("PermissionOracle" in r for r in oracle_rules)


def test_compile_to_oracle_object() -> None:
    compiler = DSLCompiler()
    parser = DSLParser()
    rule = parser.parse("WHEN customer cancels order THEN inventory must be restored SEVERITY P0")
    assert rule is not None

    oracle = compiler.compile_to_oracle_object(rule)
    assert isinstance(oracle, CompiledOracle)
    assert oracle.oracle_family in ("state_consistency", "money_consistency")
    assert oracle.layer.startswith("L")
    assert oracle.severity == "P0"


# ═════════════════════════════════════════════════════════════════════════════
# Compiler Tests — Invariants
# ═════════════════════════════════════════════════════════════════════════════

def test_compile_to_invariant_state() -> None:
    compiler = DSLCompiler()
    rule = ParsedRule("T1", "", rule_type="state_change", entity="order", action="cancel", assertion="inventory restored")

    inv = compiler.compile_to_invariant(rule)
    assert inv is not None
    assert inv.invariant_type == "state_machine_invariant"
    assert inv.config["entity"] == "order"


def test_compile_to_invariant_conservation() -> None:
    compiler = DSLCompiler()
    rule = ParsedRule("T1", "", rule_type="conservation", entity="payment", assertion="amount must match")

    inv = compiler.compile_to_invariant(rule)
    assert inv is not None
    assert inv.invariant_type == "conservation_invariant"


def test_compile_to_invariant_audit() -> None:
    compiler = DSLCompiler()
    rule = ParsedRule("T1", "", rule_type="audit", entity="patient_record", action="access", timeout_minutes=5)

    inv = compiler.compile_to_invariant(rule)
    assert inv is not None
    assert inv.invariant_type == "audit_trail_invariant"


def test_compile_to_invariant_idempotency() -> None:
    compiler = DSLCompiler()
    rule = ParsedRule("T1", "", rule_type="idempotency", entity="order", action="submit")

    inv = compiler.compile_to_invariant(rule)
    assert inv is not None
    assert inv.invariant_type == "idempotency_invariant"


def test_compile_to_invariant_permission() -> None:
    compiler = DSLCompiler()
    rule = ParsedRule("T1", "", rule_type="permission", entity="order", actor="user")

    inv = compiler.compile_to_invariant(rule)
    assert inv is not None
    assert inv.invariant_type == "permission_invariant"


# ═════════════════════════════════════════════════════════════════════════════
# Compiler Tests — Proof Obligations
# ═════════════════════════════════════════════════════════════════════════════

def test_compile_to_proof_obligation() -> None:
    compiler = DSLCompiler()
    rule = ParsedRule("T1", "", rule_type="state_change", entity="order", action="cancel")

    po = compiler.compile_to_proof_obligation(rule)
    assert po is not None
    assert po.kind == "lifecycle_transition"
    assert po.entity == "order"


def test_compile_to_proof_obligation_conservation() -> None:
    compiler = DSLCompiler()
    rule = ParsedRule("T1", "", rule_type="conservation", entity="payment", assertion="amount must not change")

    po = compiler.compile_to_proof_obligation(rule)
    assert po is not None
    assert po.kind == "conservation"


def test_compile_to_proof_obligation_idempotency() -> None:
    compiler = DSLCompiler()
    rule = ParsedRule("T1", "", rule_type="idempotency", entity="order", action="submit")

    po = compiler.compile_to_proof_obligation(rule)
    assert po is not None
    assert po.kind == "idempotency_replay"


# ═════════════════════════════════════════════════════════════════════════════
# Rule Library Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_rule_library_all_industries() -> None:
    lib = RuleLibrary()
    industries = lib.list_industries()
    assert len(industries) == 7
    assert "ecommerce" in industries
    assert "crm" in industries
    assert "finance" in industries
    assert "medical" in industries


def test_rule_library_get_rules() -> None:
    lib = RuleLibrary()
    rules = lib.get_rules("ecommerce")
    assert len(rules) >= 5  # Each industry has at least 5 rules
    for rule in rules:
        assert isinstance(rule, ParsedRule)
        assert rule.rule_id.startswith("DSL-")


def test_rule_library_caching() -> None:
    lib = RuleLibrary()
    rules1 = lib.get_rules("crm")
    rules2 = lib.get_rules("crm")
    assert rules1 is rules2  # Same cached object


def test_rule_library_rule_count() -> None:
    lib = RuleLibrary()
    assert lib.rule_count("ecommerce") >= 5
    assert lib.rule_count() >= 35  # 7 industries × ~5 rules each


def test_rule_library_all_rules_parse() -> None:
    """Every pre-built rule should parse successfully."""
    lib = RuleLibrary()
    all_rules = lib.get_all_rules()
    for industry, rules in all_rules.items():
        for rule in rules:
            assert rule.rule_type != "", f"Rule {rule.rule_id} in {industry} has no rule_type"
            assert rule.entity != "", f"Rule {rule.rule_id} in {industry} has no entity"


def test_get_rules_for_industry_convenience() -> None:
    rules = get_rules_for_industry("medical")
    assert len(rules) >= 5


def test_rule_library_compiles_all() -> None:
    """Every pre-built rule should compile to oracle rules."""
    compiler = DSLCompiler()
    lib = RuleLibrary()
    for industry in lib.list_industries():
        for rule in lib.get_rules(industry):
            oracle_rules = compiler.compile_to_oracle_rules(rule)
            assert len(oracle_rules) >= 1, f"Rule {rule.rule_id} ({industry}) compiled to empty oracle_rules"


# ═════════════════════════════════════════════════════════════════════════════
# Integration Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_parse_and_compile_full_pipeline() -> None:
    """Parse → Compile → Oracle rules → Invariant → Proof obligation."""
    parser = DSLParser()
    compiler = DSLCompiler()

    text = "WHEN customer cancels order THEN inventory must be restored SEVERITY P0"
    rule = parser.parse(text)
    assert rule is not None

    # All three compilation paths should work
    oracle_rules = compiler.compile_to_oracle_rules(rule)
    assert len(oracle_rules) >= 1

    inv = compiler.compile_to_invariant(rule)
    assert inv is not None

    po = compiler.compile_to_proof_obligation(rule)
    assert po is not None


def test_prd_to_oracle_rules_pipeline() -> None:
    """Simulate a complete PRD → DSL → Oracle rules pipeline."""
    parser = DSLParser()
    compiler = DSLCompiler()

    prd = "下单后扣库存；支付回调幂等；退款不得超过已支付金额；优惠券不能跨用户；订单取消后库存恢复"
    rules = parser.parse_prd(prd)

    all_oracle_rules: list[str] = []
    for rule in rules:
        oracle_rules = compiler.compile_to_oracle_rules(rule)
        all_oracle_rules.extend(oracle_rules)

    assert len(all_oracle_rules) >= len(rules)
    # Should have at least StateOracle, MoneyOracle, IdempotencyOracle rules
    oracle_classes = set()
    for r in all_oracle_rules:
        if "." in r:
            oracle_classes.add(r.split(".")[0])
    assert len(oracle_classes) >= 2  # Multiple oracle types triggered
