"""Exact source-role evidence used by business-object recognition."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .identity_types import fact_mentions
from .schema import as_dict, as_list, text

ACCEPTED_FACT_STATES = frozenset({"ACCEPTED", "CONFIRMED"})
HARD_NON_OBJECT_ROLES = frozenset({"ACTOR", "ACTION", "STATE"})


def comparison_key(value: Any) -> str:
    """Formatting-only key. It is not semantic or fuzzy identity."""
    return re.sub(r"\s+", "", text(value)).casefold()


def accepted_facts(asset: dict[str, Any]) -> list[dict[str, Any]]:
    ledger = as_dict(asset.get("business_fact_ledger"))
    return [
        row
        for row in as_list(ledger.get("items"))
        if isinstance(row, dict)
        and text(row.get("status")).upper() in ACCEPTED_FACT_STATES
    ]


def positive_fact_mentions(fact: dict[str, Any]) -> list[tuple[str, str]]:
    if text(fact.get("kind")) not in {"RULE", "STATE_TRANSITION"}:
        return []
    return [
        (label, f"BUSINESS_FACT_{side.upper()}")
        for side in ("subject", "object")
        for label in fact_mentions(fact, side)
    ]


def negative_role_index(
    asset: dict[str, Any], facts: list[dict[str, Any]]
) -> dict[str, set[str]]:
    roles: dict[str, set[str]] = defaultdict(set)

    def add(value: Any, role: str) -> None:
        key = comparison_key(value)
        if key:
            roles[key].add(role)

    for fact in facts:
        for value in as_list(as_dict(fact.get("subject")).get("actor_refs")):
            add(value, "ACTOR")
        action = as_dict(fact.get("action"))
        add(action.get("canonical"), "ACTION")
        add(action.get("raw"), "ACTION")
        add(fact.get("from_state"), "STATE")
        add(fact.get("to_state"), "STATE")
        for effect in as_list(fact.get("state_effects")):
            if isinstance(effect, dict):
                add(effect.get("from_state"), "STATE")
                add(effect.get("to_state"), "STATE")

    for row in as_list(asset.get("roles")):
        if isinstance(row, dict):
            add(row.get("role") or row.get("name"), "ACTOR")
    for machine in as_list(asset.get("state_machines")):
        if not isinstance(machine, dict):
            continue
        for state in as_list(machine.get("states")):
            if isinstance(state, dict):
                add(state.get("name") or state.get("state"), "STATE")
            else:
                add(state, "STATE")
        for transition in as_list(machine.get("transitions")):
            if isinstance(transition, dict):
                add(transition.get("from_state"), "STATE")
                add(transition.get("to_state"), "STATE")
    for row in as_list(asset.get("field_dictionary")):
        if isinstance(row, dict):
            add(row.get("field") or row.get("name"), "FIELD")
    return roles


__all__ = [
    "HARD_NON_OBJECT_ROLES",
    "accepted_facts",
    "comparison_key",
    "negative_role_index",
    "positive_fact_mentions",
]
