"""Oracle DSL Compiler — Compile ParsedRule into executable oracle formats.

Maps ParsedRule objects to:
  1. Oracle rule strings (CouponOracle.xxx format, consumable by oracle_engine.py)
  2. Invariant configurations (consumable by invariant_engine.py)
  3. Proof obligation dicts (consumable by business_invariant_evaluator.py)

Design: zero-destruction — output formats match existing engine expectations exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .dsl_parser import ParsedRule


# ═════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class CompiledOracle:
    """A compiled oracle ready for the V12 pipeline."""
    oracle_rules: list[str]           # e.g. ["StateOracle.state_change_side_effect", "orders->inventory"]
    oracle_family: str                # e.g. "state_consistency"
    layer: str                        # L1-L6
    trigger_keywords: list[str]       # Keywords that activate this oracle
    expected_behavior: str
    severity: str


@dataclass
class CompiledInvariant:
    """A compiled invariant ready for invariant_engine.py."""
    invariant_type: str               # e.g. "conservation_invariant"
    config: dict[str, Any]            # Invariant-specific configuration
    expected: str
    severity: str


@dataclass
class CompiledProofObligation:
    """A compiled proof obligation ready for business_invariant_evaluator.py."""
    kind: str                         # e.g. "conservation", "lifecycle_transition"
    entity: str
    fields: dict[str, Any]
    expected_delta: dict[str, float] | None = None
    allowed_transitions: dict[str, list[str]] | None = None
    expression: str = ""
    severity: str = "P1"


# ═════════════════════════════════════════════════════════════════════════════
# Rule Type → Oracle Mapping
# ═════════════════════════════════════════════════════════════════════════════

RULE_TYPE_TO_ORACLE: dict[str, dict[str, Any]] = {
    "state_change": {
        "oracle_class": "StateOracle",
        "oracle_family": "state_consistency",
        "layer": "L3",
        "trigger_keywords": ["状态", "流转", "state", "transition", "cancel", "取消", "关闭"],
    },
    "conservation": {
        "oracle_class": "MoneyOracle",
        "oracle_family": "money_consistency",
        "layer": "L3",
        "trigger_keywords": ["金额", "库存", "数量", "守恒", "amount", "stock", "quantity", "balance"],
    },
    "audit": {
        "oracle_class": "AuditOracle",
        "oracle_family": "audit_traceability",
        "layer": "L6",
        "trigger_keywords": ["审计", "日志", "留痕", "audit", "log", "trace"],
    },
    "idempotency": {
        "oracle_class": "IdempotencyOracle",
        "oracle_family": "idempotency",
        "layer": "L5",
        "trigger_keywords": ["幂等", "重复", "idempotent", "duplicate"],
    },
    "permission": {
        "oracle_class": "PermissionOracle",
        "oracle_family": "permission_bypass",
        "layer": "L4",
        "trigger_keywords": ["权限", "越权", "角色", "permission", "role", "auth"],
    },
}


# ═════════════════════════════════════════════════════════════════════════════
# DSL Compiler
# ═════════════════════════════════════════════════════════════════════════════

class DSLCompiler:
    """Compile ParsedRule into executable oracle formats.

    Usage::

        compiler = DSLCompiler()
        rule = parser.parse("WHEN customer cancels order THEN inventory must be restored")
        oracle = compiler.compile_to_oracle_rules(rule)
        invariant = compiler.compile_to_invariant(rule)
    """

    # ── Oracle Rules Compilation ───────────────────────────────────────

    def compile_to_oracle_rules(self, rule: ParsedRule) -> list[str]:
        """Compile a ParsedRule into oracle_rules strings for the V12 pipeline.

        Format: ["OracleClass.rule_identifier", "detail_1", "detail_2"]
        This is the exact format expected by oracle_engine.py oracles.
        """
        oracle_info = RULE_TYPE_TO_ORACLE.get(rule.rule_type, RULE_TYPE_TO_ORACLE["state_change"])
        oracle_class = oracle_info["oracle_class"]
        rules: list[str] = []

        if rule.rule_type == "state_change":
            # StateOracle rules: entity transition + side effect check
            rules.append(f"{oracle_class}.source_grounded_transition")
            if rule.action:
                rules.append(f"{rule.entity}:{rule.action}")
            if rule.assertion:
                # Extract target entity from assertion if cross-entity
                target = self._extract_target_entity(rule.assertion, rule.entity)
                if target and target != rule.entity:
                    rules.append(f"{rule.entity}->{target}:side_effect_consistency")
                else:
                    rules.append(f"expected:{rule.assertion[:80]}")
            else:
                rules.append(f"expected:{rule.assertion[:80]}" if rule.assertion else f"{rule.entity}:state_transition")

        elif rule.rule_type == "conservation":
            # MoneyOracle / ConservationOracle rules
            rules.append(f"{oracle_class}.source_grounded_invariant")
            rules.append(rule.assertion[:300] if rule.assertion else f"{rule.entity}_conservation")
            if "restore" in rule.assertion.lower() or "恢复" in rule.assertion:
                rules.append("cross_entity_conservation_check")

        elif rule.rule_type == "audit":
            # AuditOracle rules
            rules.append(f"{oracle_class}.source_grounded_invariant")
            rules.append(f"audit_required_for:{rule.entity}:{rule.action}")
            if rule.timeout_minutes > 0:
                rules.append(f"timeout:{rule.timeout_minutes}min")

        elif rule.rule_type == "idempotency":
            # IdempotencyOracle rules
            rules.append(f"{oracle_class}.source_grounded_invariant")
            rules.append(f"idempotency:{rule.entity}:{rule.action or 'submit'}")

        elif rule.rule_type == "permission":
            # PermissionOracle rules
            rules.append(f"{oracle_class}.source_grounded_invariant")
            if rule.actor:
                rules.append(f"actor:{rule.actor}:{rule.entity}")
            rules.append(rule.assertion[:150] if rule.assertion else f"permission_boundary:{rule.entity}")

        else:
            # Generic fallback
            rules.append(f"ConsistencyOracle.source_grounded_invariant")
            rules.append(rule.assertion[:300] if rule.assertion else rule.raw_text[:300])

        return rules

    def compile_to_oracle_object(self, rule: ParsedRule) -> CompiledOracle:
        """Compile into a structured CompiledOracle with metadata."""
        oracle_rules = self.compile_to_oracle_rules(rule)
        oracle_info = RULE_TYPE_TO_ORACLE.get(rule.rule_type, RULE_TYPE_TO_ORACLE["state_change"])

        return CompiledOracle(
            oracle_rules=oracle_rules,
            oracle_family=oracle_info["oracle_family"],
            layer=oracle_info["layer"],
            trigger_keywords=list(oracle_info["trigger_keywords"]),
            expected_behavior=rule.assertion,
            severity=rule.severity,
        )

    # ── Invariant Compilation ──────────────────────────────────────────

    def compile_to_invariant(self, rule: ParsedRule) -> CompiledInvariant | None:
        """Compile into an invariant configuration for invariant_engine.py."""
        if rule.rule_type == "state_change":
            return CompiledInvariant(
                invariant_type="state_machine_invariant",
                config={
                    "entity": rule.entity,
                    "action": rule.action,
                    "assertion": rule.assertion,
                },
                expected=rule.assertion,
                severity=rule.severity,
            )

        elif rule.rule_type == "conservation":
            return CompiledInvariant(
                invariant_type="conservation_invariant",
                config={
                    "entity": rule.entity,
                    "assertion": rule.assertion,
                    "check_type": "cross_entity" if "->" in rule.assertion else "same_entity",
                },
                expected=rule.assertion,
                severity=rule.severity,
            )

        elif rule.rule_type == "audit":
            return CompiledInvariant(
                invariant_type="audit_trail_invariant",
                config={
                    "entity": rule.entity,
                    "action": rule.action,
                    "timeout_minutes": rule.timeout_minutes,
                },
                expected=f"Every {rule.action} on {rule.entity} must create an audit entry",
                severity=rule.severity,
            )

        elif rule.rule_type == "idempotency":
            return CompiledInvariant(
                invariant_type="idempotency_invariant",
                config={
                    "entity": rule.entity,
                    "action": rule.action or "submit",
                },
                expected=f"{rule.action or 'Submit'} on {rule.entity} must be idempotent",
                severity=rule.severity,
            )

        elif rule.rule_type == "permission":
            return CompiledInvariant(
                invariant_type="permission_invariant",
                config={
                    "entity": rule.entity,
                    "actor": rule.actor,
                    "assertion": rule.assertion,
                },
                expected=rule.assertion,
                severity=rule.severity,
            )

        return None

    # ── Proof Obligation Compilation ───────────────────────────────────

    def compile_to_proof_obligation(self, rule: ParsedRule) -> CompiledProofObligation | None:
        """Compile into a proof obligation for business_invariant_evaluator.py."""
        if rule.rule_type == "state_change":
            return CompiledProofObligation(
                kind="lifecycle_transition",
                entity=rule.entity,
                fields={"action": rule.action},
                severity=rule.severity,
            )

        elif rule.rule_type == "conservation":
            # Check if assertion suggests a specific expression
            expr = self._extract_conservation_expression(rule.assertion)
            return CompiledProofObligation(
                kind="conservation",
                entity=rule.entity,
                fields={"assertion": rule.assertion},
                expression=expr or rule.assertion,
                severity=rule.severity,
            )

        elif rule.rule_type == "audit" and rule.timeout_minutes > 0:
            return CompiledProofObligation(
                kind="eventually",
                entity=rule.entity,
                fields={"condition": f"audit_entry_exists({rule.entity}, {rule.action})"},
                severity=rule.severity,
            )

        elif rule.rule_type == "idempotency":
            return CompiledProofObligation(
                kind="idempotency_replay",
                entity=rule.entity,
                fields={"action": rule.action or "submit"},
                severity=rule.severity,
            )

        elif rule.rule_type == "permission":
            return CompiledProofObligation(
                kind="authorization_non_mutation",
                entity=rule.entity,
                fields={"actor": rule.actor, "assertion": rule.assertion},
                severity=rule.severity,
            )

        return None

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_target_entity(assertion: str, source_entity: str) -> str:
        """Extract a cross-entity target from an assertion string.

        Uses generic word-boundary matching instead of hardcoded entity lists.
        """
        import re
        # Match any word-like token that could be an entity name
        # (2-20 chars, may include hyphens and underscores)
        candidates = re.findall(r'\b([a-zA-Z][a-zA-Z0-9_-]{1,19}|[\u4e00-\u9fff]{2,6})\b', assertion)
        for candidate in candidates:
            candidate_lower = candidate.lower()
            if candidate_lower != source_entity.lower() and len(candidate) >= 2:
                # Skip common non-entity words
                skip_words = {
                    'the', 'a', 'an', 'is', 'be', 'to', 'of', 'in', 'on', 'at', 'by', 'for',
                    'must', 'not', 'and', 'or', 'if', 'it', 'as', 'no', 'so', 'we', 'he',
                    'should', 'will', 'can', 'may', 'has', 'had', 'been', 'was', 'are',
                    'with', 'from', 'that', 'this', 'when', 'then', 'after', 'before',
                    '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一',
                    '一个', '这个', '那个', '什么', '怎么', '如何', '可以', '需要', '应该',
                }
                if candidate_lower not in skip_words:
                    return candidate
        return ""

    @staticmethod
    def _extract_conservation_expression(assertion: str) -> str:
        """Try to extract a conservation expression like 'A + B == C'."""
        # Simple heuristic: if assertion mentions two quantities
        import re
        quantities = re.findall(r"(?:amount|balance|quantity|stock|total|sum|金额|余额|数量|库存|总计)", assertion, re.IGNORECASE)
        if len(quantities) >= 2:
            return "before.total == after.total"
        if "restore" in assertion.lower() or "恢复" in assertion:
            return "before.stock == after.stock"
        return ""
