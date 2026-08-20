"""Recall authority for deciding which accepted facts deserve Behavior-IR lineage.

This module does not infer operations or execution bindings. It only prevents
source-backed, test-relevant facts from being silently dropped before later
fail-closed binding stages can account for them.
"""
from __future__ import annotations

from typing import Any

BEHAVIOR_WORTHY_FACT_KINDS = frozenset({
    "RULE",
    "STATE_TRANSITION",
    "BUSINESS_RULE",
    "AUTHORIZATION",
    "PERMISSION",
    "INVARIANT",
    "CONSTRAINT",
    "LIFECYCLE",
    "VISIBILITY",
    "ISOLATION",
    "CONCURRENCY",
    "IDEMPOTENCY",
    "TEMPORAL",
    "APPROVAL",
    "CARDINALITY_CONSTRAINT",
})


def normalized_fact_kind(fact: dict[str, Any]) -> str:
    return str(fact.get("fact_type") or fact.get("kind") or "").strip().upper()


def is_behavior_worthy_fact(fact: dict[str, Any]) -> bool:
    if str(fact.get("status") or "").strip().upper() != "ACCEPTED":
        return False
    return normalized_fact_kind(fact) in BEHAVIOR_WORTHY_FACT_KINDS
