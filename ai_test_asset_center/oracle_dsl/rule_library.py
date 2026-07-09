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


# ═════════════════════════════════════════════════════════════════════════════
# Rule Library
# ═════════════════════════════════════════════════════════════════════════════

class RuleLibrary:
    """Access pre-built industry DSL rules.

    Usage::

        lib = RuleLibrary()
        rules = lib.get_rules("ecommerce")
        # → list[ParsedRule]
    """

    def __init__(self):
        self._parser = DSLParser()
        self._cache: dict[str, list[ParsedRule]] = {}

    def get_rules(self, industry: str) -> list[ParsedRule]:
        """Get all pre-built DSL rules for an industry.

        Args:
            industry: Industry ID (crm, ecommerce, erp, finance, medical, education, saas).

        Returns:
            List of ParsedRule objects.
        """
        industry = industry.lower()
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
        return list(INDUSTRY_RULES.get(industry.lower(), []))

    def rule_count(self, industry: str | None = None) -> int:
        """Return the count of pre-built rules, optionally filtered by industry."""
        if industry:
            return len(INDUSTRY_RULES.get(industry.lower(), []))
        return sum(len(v) for v in INDUSTRY_RULES.values())


# ═════════════════════════════════════════════════════════════════════════════
# Convenience function
# ═════════════════════════════════════════════════════════════════════════════

def get_rules_for_industry(industry: str) -> list[ParsedRule]:
    """Convenience: get pre-built DSL rules for an industry."""
    return RuleLibrary().get_rules(industry)
