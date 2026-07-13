"""Oracle DSL Rule Library — Pre-built business promise rules per industry.

Each industry has 5-10 core business promises expressed in WHEN-THEN DSL.
These are compiled at scan time and injected into the V12 oracle pipeline.
"""

from __future__ import annotations

from .dsl_parser import DSLParser, ParsedRule

# ═════════════════════════════════════════════════════════════════════════════
# Industry Rule Libraries
# ═════════════════════════════════════════════════════════════════════════════

INDUSTRY_RULES: dict[str, list[str]] = {
    "crm": [
        "WHEN sales_rep views lead THEN lead ownership must match actor SEVERITY P0",
        "WHEN lead transitions to opportunity THEN audit log must contain entry SEVERITY P1",
        "WHEN opportunity is closed THEN opportunity must not be modified SEVERITY P1",
        "WHEN report is exported THEN data must not leak across teams SEVERITY P0",
        "WHEN account is transferred THEN previous owner must lose access SEVERITY P0",
        "WHEN discount is applied THEN approval must be recorded SEVERITY P1",
    ],
    "ecommerce": [
        "WHEN customer places order THEN inventory must be deducted SEVERITY P0",
        "WHEN customer cancels order THEN inventory must be restored SEVERITY P0",
        "WHEN payment callback received twice THEN order amount must not change SEVERITY P0",
        "WHEN refund is processed THEN refund amount must not exceed paid amount SEVERITY P0",
        "WHEN coupon is applied THEN coupon must not be reusable SEVERITY P1",
        "WHEN order is paid THEN order status must transition to paid SEVERITY P1",
        "WHEN user views order THEN user must only see own orders SEVERITY P0",
    ],
    "erp": [
        "WHEN purchase_order is approved THEN invoice must match purchase_order amount SEVERITY P0",
        "WHEN inventory is received THEN stock must be updated atomically SEVERITY P0",
        "WHEN ledger_entry is created THEN ledger must balance SEVERITY P0",
        "WHEN warehouse transfer occurs THEN source and target stock must be consistent SEVERITY P1",
        "WHEN purchase_order is created THEN approval workflow must not be bypassed SEVERITY P0",
    ],
    "finance": [
        "WHEN transaction is executed THEN account balance must be updated atomically SEVERITY P0",
        "WHEN loan repayment is processed THEN loan balance must decrease by repayment amount SEVERITY P0",
        "WHEN transaction occurs THEN audit trail must contain actor action and timestamp SEVERITY P0",
        "WHEN compliance_report is generated THEN all required fields must be present SEVERITY P1",
        "WHEN callback is received twice THEN transaction must not be duplicated SEVERITY P0",
    ],
    "medical": [
        "WHEN doctor accesses patient_record THEN audit log must contain entry WITHIN 5 minutes SEVERITY P0",
        "WHEN prescription is issued THEN prescribing doctor must have valid license SEVERITY P0",
        "WHEN medical_record is modified THEN modification must be logged with actor and reason SEVERITY P0",
        "WHEN appointment is booked THEN capacity must not be exceeded SEVERITY P1",
        "WHEN patient views record THEN patient must only see own records SEVERITY P0",
        "WHEN lab_result is uploaded THEN ordering physician must be notified SEVERITY P1",
    ],
    "education": [
        "WHEN student views grade THEN student must only see own grades SEVERITY P0",
        "WHEN course enrollment occurs THEN enrollment must not exceed capacity SEVERITY P1",
        "WHEN grade is modified THEN modification must be authorized SEVERITY P0",
        "WHEN prerequisite is not met THEN enrollment must be rejected SEVERITY P1",
        "WHEN transcript is generated THEN all grades must be included SEVERITY P1",
    ],
    "saas": [
        "WHEN tenant_a accesses data THEN tenant_b data must not be visible SEVERITY P0",
        "WHEN subscription expires THEN service access must be restricted SEVERITY P0",
        "WHEN feature_flag is disabled THEN feature must not be accessible SEVERITY P1",
        "WHEN api_key is used THEN api_key must be scoped to owning tenant SEVERITY P0",
        "WHEN user is removed from workspace THEN access must be revoked immediately SEVERITY P0",
        "WHEN billing cycle changes THEN subscription status must update within 1 hour SEVERITY P1",
    ],
}

# Bridge keys used by multi_industry / phase103 onto the DSL catalog keys above.
# Aliases never invent an industry — they only resolve an already-recognized key.
INDUSTRY_KEY_ALIASES: dict[str, str] = {
    "healthcare": "medical",
    "saas_multitenant": "saas",
    "medical_healthcare": "medical",
}


def normalize_industry_key(industry: str | None) -> str:
    """Normalize an industry id for DSL lookup. Empty/unknown stay empty."""
    key = str(industry or "").strip().lower()
    if not key or key in {"unknown", "unknown_general_business", "general", "general_business", "auto"}:
        return ""
    return INDUSTRY_KEY_ALIASES.get(key, key)


# ═════════════════════════════════════════════════════════════════════════════
# Rule Library
# ═════════════════════════════════════════════════════════════════════════════

class RuleLibrary:
    """Access pre-built industry DSL rules.

    Usage::

        lib = RuleLibrary()
        rules = lib.get_rules("finance")  # only when industry is evidence-selected
        # → list[ParsedRule]

    Never call get_rules with a guessed default industry. Prefer
    ``get_rules_for_recognized_industries`` so unknown projects do not inherit
    ecommerce inventory/coupon invariants.
    """

    def __init__(self):
        self._parser = DSLParser()
        self._cache: dict[str, list[ParsedRule]] = {}

    def get_rules(self, industry: str) -> list[ParsedRule]:
        """Get all pre-built DSL rules for an industry.

        Args:
            industry: Industry ID (crm, ecommerce, erp, finance, medical, education, saas)
                or an alias (healthcare→medical, saas_multitenant→saas).
                Empty / unknown industries return [] — never fall back to ecommerce.

        Returns:
            List of ParsedRule objects.
        """
        industry = normalize_industry_key(industry)
        if not industry:
            return []
        if industry in self._cache:
            return self._cache[industry]

        rule_texts = INDUSTRY_RULES.get(industry, [])
        rules: list[ParsedRule] = []
        for text in rule_texts:
            rule = self._parser.parse(text)
            if rule:
                rules.append(rule)

        self._cache[industry] = rules
        return rules

    def get_rules_for_recognized_industries(
        self,
        industries: list[str] | None,
        *,
        min_confidence: float = 0.58,
        confidences: dict[str, float] | None = None,
    ) -> list[ParsedRule]:
        """Return DSL rules only for evidence-gated recognized industries.

        Unknown / low-confidence recognition yields an empty pack so vertical
        ecommerce/finance oracles cannot activate by default.
        """
        confidences = confidences or {}
        collected: list[ParsedRule] = []
        seen_industries: set[str] = set()
        seen_rules: set[str] = set()
        for industry in industries or []:
            raw_key = str(industry or "").strip().lower()
            key = normalize_industry_key(raw_key)
            if not key or key in seen_industries:
                continue
            seen_industries.add(key)
            confidence = float(
                confidences.get(raw_key, confidences.get(key, 1.0)) or 0.0
            )
            if confidence < float(min_confidence):
                continue
            for rule in self.get_rules(key):
                marker = str(getattr(rule, "raw_text", None) or getattr(rule, "text", None) or id(rule))
                if marker in seen_rules:
                    continue
                seen_rules.add(marker)
                collected.append(rule)
        return collected

    def list_industries(self) -> list[str]:
        """Return the list of industries with pre-built rules."""
        return sorted(INDUSTRY_RULES.keys())

    def get_all_rules(self) -> dict[str, list[ParsedRule]]:
        """Return all industry rules as a dict."""
        result: dict[str, list[ParsedRule]] = {}
        for industry in INDUSTRY_RULES:
            result[industry] = self.get_rules(industry)
        return result

    def get_rule_texts(self, industry: str) -> list[str]:
        """Return raw rule text strings for an industry."""
        industry = normalize_industry_key(industry)
        if not industry:
            return []
        return list(INDUSTRY_RULES.get(industry, []))

    def rule_count(self, industry: str | None = None) -> int:
        """Return the count of pre-built rules, optionally filtered by industry."""
        if industry:
            return len(self.get_rule_texts(industry))
        return sum(len(v) for v in INDUSTRY_RULES.values())


# ═════════════════════════════════════════════════════════════════════════════
# Convenience function
# ═════════════════════════════════════════════════════════════════════════════

def get_rules_for_industry(industry: str) -> list[ParsedRule]:
    """Convenience: get pre-built DSL rules for an industry (no ecommerce default)."""
    return RuleLibrary().get_rules(industry)


def get_rules_for_recognized_industries(
    industries: list[str] | None,
    *,
    min_confidence: float = 0.58,
    confidences: dict[str, float] | None = None,
) -> list[ParsedRule]:
    """Convenience: evidence-gated multi-industry DSL rule pack."""
    return RuleLibrary().get_rules_for_recognized_industries(
        industries,
        min_confidence=min_confidence,
        confidences=confidences,
    )
