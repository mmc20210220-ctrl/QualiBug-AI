"""Field-Level Golden Rule Set — 20+ industry-neutral business rules.

Rules are categorized into:
- causal (≥8): Field A change causes Field B change
- state (≥6): State field transition constraints
- conservation (≥6): Quantity/balance preservation rules

All rules are field-level grounded: every term references a bound field.
terms=[] is FORBIDDEN — every rule must have explicit field bindings.

Schema: qualibug.field-level-golden-rule-set.v1
"""
from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "qualibug.field-level-golden-rule-set.v1"

# ─── Golden Rule Definitions ──────────────────────────────────────────────────
# Each rule has:
# - rule_id: unique identifier
# - category: causal | state | conservation
# - description: human-readable explanation
# - terms: list of field references (NEVER empty)
# - expression: logical expression over terms
# - field_type_required: required field type classification for each term
# - severity: critical | high | medium

GOLDEN_RULES: list[dict[str, Any]] = [
    # ─── CAUSAL RULES (8) ─────────────────────────────────────────────────
    {
        "rule_id": "GR-CAUSAL-001",
        "category": "causal",
        "description": "Creating an entity with a foreign key must reference an existing parent entity",
        "terms": [
            {"field": "parent_entity_id", "field_type": "FOREIGN_KEY", "role": "trigger"},
            {"field": "parent_entity.id", "field_type": "IDENTITY", "role": "target"},
        ],
        "expression": "parent_entity_id IN parent_entity.id_set",
        "severity": "critical",
    },
    {
        "rule_id": "GR-CAUSAL-002",
        "category": "causal",
        "description": "Updating a quantity field must update the corresponding balance field",
        "terms": [
            {"field": "quantity_delta", "field_type": "QUANTITY_BALANCE", "role": "trigger"},
            {"field": "running_balance", "field_type": "QUANTITY_BALANCE", "role": "effect"},
        ],
        "expression": "running_balance_after == running_balance_before + quantity_delta",
        "severity": "critical",
    },
    {
        "rule_id": "GR-CAUSAL-003",
        "category": "causal",
        "description": "Changing owner_id must update the audit trail (updated_by, updated_at)",
        "terms": [
            {"field": "owner_id", "field_type": "OWNER_ID", "role": "trigger"},
            {"field": "updated_at", "field_type": "TEMPORAL", "role": "effect"},
        ],
        "expression": "owner_id_changed IMPLIES updated_at_changed",
        "severity": "high",
    },
    {
        "rule_id": "GR-CAUSAL-004",
        "category": "causal",
        "description": "Setting a temporal deadline in the past must be rejected",
        "terms": [
            {"field": "deadline", "field_type": "TEMPORAL", "role": "trigger"},
            {"field": "current_timestamp", "field_type": "TEMPORAL", "role": "reference"},
        ],
        "expression": "deadline >= current_timestamp",
        "severity": "high",
    },
    {
        "rule_id": "GR-CAUSAL-005",
        "category": "causal",
        "description": "Deleting a parent entity must cascade or block if children exist",
        "terms": [
            {"field": "parent.id", "field_type": "IDENTITY", "role": "trigger"},
            {"field": "child.parent_id", "field_type": "FOREIGN_KEY", "role": "dependent"},
        ],
        "expression": "DELETE(parent) IMPLIES (child_count == 0 OR cascade_delete)",
        "severity": "critical",
    },
    {
        "rule_id": "GR-CAUSAL-006",
        "category": "causal",
        "description": "Modifying a unique code field must not create duplicates",
        "terms": [
            {"field": "unique_code", "field_type": "UNIQUE_CODE", "role": "trigger"},
            {"field": "entity.id", "field_type": "IDENTITY", "role": "scope"},
        ],
        "expression": "UNIQUE(unique_code) WHERE entity.scope",
        "severity": "critical",
    },
    {
        "rule_id": "GR-CAUSAL-007",
        "category": "causal",
        "description": "Changing a money field must preserve currency consistency",
        "terms": [
            {"field": "amount", "field_type": "MONEY", "role": "trigger"},
            {"field": "currency_code", "field_type": "REFERENCE_CODE", "role": "constraint"},
        ],
        "expression": "amount_changed IMPLIES currency_code_present",
        "severity": "high",
    },
    {
        "rule_id": "GR-CAUSAL-008",
        "category": "causal",
        "description": "Writing to a restricted field requires specific role authorization",
        "terms": [
            {"field": "restricted_field", "field_type": "MONEY", "role": "trigger"},
            {"field": "actor.role", "field_type": "ENUM_STATUS", "role": "authorization"},
        ],
        "expression": "WRITE(restricted_field) REQUIRES actor.role IN authorized_roles",
        "severity": "critical",
    },
    # ─── STATE RULES (6) ──────────────────────────────────────────────────
    {
        "rule_id": "GR-STATE-001",
        "category": "state",
        "description": "State field must only transition along declared transition paths",
        "terms": [
            {"field": "status", "field_type": "STATE", "role": "state_field"},
            {"field": "transition_operation", "field_type": "IDENTITY", "role": "trigger"},
        ],
        "expression": "status_after IN allowed_transitions[status_before]",
        "severity": "critical",
    },
    {
        "rule_id": "GR-STATE-002",
        "category": "state",
        "description": "Terminal state must be immutable — no further transitions allowed",
        "terms": [
            {"field": "status", "field_type": "STATE", "role": "state_field"},
            {"field": "terminal_values", "field_type": "ENUM_STATUS", "role": "constraint"},
        ],
        "expression": "status IN terminal_values IMPLIES status_after == status_before",
        "severity": "critical",
    },
    {
        "rule_id": "GR-STATE-003",
        "category": "state",
        "description": "Initial state must be set on entity creation",
        "terms": [
            {"field": "status", "field_type": "STATE", "role": "state_field"},
            {"field": "initial_value", "field_type": "ENUM_STATUS", "role": "default"},
        ],
        "expression": "CREATE(entity) IMPLIES status == initial_value",
        "severity": "high",
    },
    {
        "rule_id": "GR-STATE-004",
        "category": "state",
        "description": "State transition must update the state timestamp field",
        "terms": [
            {"field": "status", "field_type": "STATE", "role": "trigger"},
            {"field": "status_changed_at", "field_type": "TEMPORAL", "role": "effect"},
        ],
        "expression": "status_changed IMPLIES status_changed_at_updated",
        "severity": "medium",
    },
    {
        "rule_id": "GR-STATE-005",
        "category": "state",
        "description": "Concurrent state transitions must be serialized (no lost updates)",
        "terms": [
            {"field": "status", "field_type": "STATE", "role": "state_field"},
            {"field": "version", "field_type": "QUANTITY_BALANCE", "role": "optimistic_lock"},
        ],
        "expression": "transition REQUIRES version_match",
        "severity": "high",
    },
    {
        "rule_id": "GR-STATE-006",
        "category": "state",
        "description": "State field value must be from declared enum set",
        "terms": [
            {"field": "status", "field_type": "STATE", "role": "state_field"},
            {"field": "allowed_values", "field_type": "ENUM_STATUS", "role": "constraint"},
        ],
        "expression": "status IN allowed_values",
        "severity": "critical",
    },
    # ─── CONSERVATION RULES (6) ───────────────────────────────────────────
    {
        "rule_id": "GR-CONSERV-001",
        "category": "conservation",
        "description": "Sum of line item quantities must equal parent order quantity",
        "terms": [
            {"field": "order.total_quantity", "field_type": "QUANTITY_BALANCE", "role": "aggregate"},
            {"field": "line_items[].quantity", "field_type": "QUANTITY_BALANCE", "role": "component"},
        ],
        "expression": "order.total_quantity == SUM(line_items[].quantity)",
        "severity": "critical",
    },
    {
        "rule_id": "GR-CONSERV-002",
        "category": "conservation",
        "description": "Sum of line item amounts must equal parent total amount",
        "terms": [
            {"field": "order.total_amount", "field_type": "MONEY", "role": "aggregate"},
            {"field": "line_items[].amount", "field_type": "MONEY", "role": "component"},
        ],
        "expression": "order.total_amount == SUM(line_items[].amount)",
        "severity": "critical",
    },
    {
        "rule_id": "GR-CONSERV-003",
        "category": "conservation",
        "description": "Inventory balance must equal received minus shipped",
        "terms": [
            {"field": "inventory.balance", "field_type": "QUANTITY_BALANCE", "role": "state"},
            {"field": "receipts.total_qty", "field_type": "QUANTITY_BALANCE", "role": "input"},
            {"field": "shipments.total_qty", "field_type": "QUANTITY_BALANCE", "role": "output"},
        ],
        "expression": "inventory.balance == receipts.total_qty - shipments.total_qty",
        "severity": "critical",
    },
    {
        "rule_id": "GR-CONSERV-004",
        "category": "conservation",
        "description": "Account balance must equal sum of all transactions",
        "terms": [
            {"field": "account.balance", "field_type": "MONEY", "role": "state"},
            {"field": "transactions[].amount", "field_type": "MONEY", "role": "component"},
        ],
        "expression": "account.balance == SUM(transactions[].amount)",
        "severity": "critical",
    },
    {
        "rule_id": "GR-CONSERV-005",
        "category": "conservation",
        "description": "Planned quantity must equal completed plus remaining",
        "terms": [
            {"field": "planned_qty", "field_type": "QUANTITY_BALANCE", "role": "total"},
            {"field": "completed_qty", "field_type": "QUANTITY_BALANCE", "role": "done"},
            {"field": "remaining_qty", "field_type": "QUANTITY_BALANCE", "role": "pending"},
        ],
        "expression": "planned_qty == completed_qty + remaining_qty",
        "severity": "high",
    },
    {
        "rule_id": "GR-CONSERV-006",
        "category": "conservation",
        "description": "Total discount must not exceed original amount",
        "terms": [
            {"field": "original_amount", "field_type": "MONEY", "role": "base"},
            {"field": "discount_amount", "field_type": "MONEY", "role": "deduction"},
        ],
        "expression": "discount_amount <= original_amount",
        "severity": "high",
    },
]


def get_golden_rules(*, category: str = "") -> list[dict[str, Any]]:
    """Get golden rules, optionally filtered by category."""
    if not category:
        return list(GOLDEN_RULES)
    return [r for r in GOLDEN_RULES if r.get("category") == category]


def validate_golden_rules() -> dict[str, Any]:
    """Validate the golden rule set meets SPEC requirements.

    Requirements:
    - At least 20 rules total
    - causal ≥ 8
    - state ≥ 6
    - conservation ≥ 6
    - terms=[] count = 0 (every rule must have terms)
    """
    rules = GOLDEN_RULES
    causal = [r for r in rules if r["category"] == "causal"]
    state = [r for r in rules if r["category"] == "state"]
    conservation = [r for r in rules if r["category"] == "conservation"]
    empty_terms = [r for r in rules if not r.get("terms")]

    return {
        "schema_version": SCHEMA_VERSION,
        "total_rules": len(rules),
        "causal_count": len(causal),
        "state_count": len(state),
        "conservation_count": len(conservation),
        "empty_terms_count": len(empty_terms),
        "meets_requirements": (
            len(rules) >= 20
            and len(causal) >= 8
            and len(state) >= 6
            and len(conservation) >= 6
            and len(empty_terms) == 0
        ),
        "violations": [
            f"total_rules={len(rules)}<20" if len(rules) < 20 else "",
            f"causal={len(causal)}<8" if len(causal) < 8 else "",
            f"state={len(state)}<6" if len(state) < 6 else "",
            f"conservation={len(conservation)}<6" if len(conservation) < 6 else "",
            f"empty_terms={len(empty_terms)}>0" if empty_terms else "",
        ],
    }


def build_field_level_rule_set_json() -> dict[str, Any]:
    """Build the field_level_golden_rule_set.json deliverable."""
    validation = validate_golden_rules()
    return {
        "schema_version": SCHEMA_VERSION,
        "title": "Field-Level Golden Rule Set",
        "validation": validation,
        "rules": GOLDEN_RULES,
        "field_type_coverage": _compute_field_type_coverage(),
    }


def _compute_field_type_coverage() -> dict[str, int]:
    """Count how many rules reference each field type."""
    coverage: dict[str, int] = {}
    for rule in GOLDEN_RULES:
        for term in rule.get("terms", []):
            ft = term.get("field_type", "")
            if ft:
                coverage[ft] = coverage.get(ft, 0) + 1
    return dict(sorted(coverage.items(), key=lambda x: -x[1]))
