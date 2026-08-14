"""Exact source-role evidence used by business-object recognition."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .schema import as_dict, as_list, text, unique_text

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

ACCEPTED_FACT_STATES = frozenset({"ACCEPTED", "CONFIRMED"})
HARD_NON_OBJECT_ROLES = frozenset({"ACTOR", "ACTION", "STATE"})
NON_SEED_FACT_DERIVATIONS = frozenset(
    {
        "accepted_atomic_claim_projection",
        "structure_first_explicit_fact_compiler",
    }
)


def comparison_key(value: Any) -> str:
    """Formatting-only key. It is not semantic or fuzzy identity."""
    return re.sub(r"\s+", "", text(value)).casefold()


def _contains_declared_object(
    label: Any, declared_key: str, declared_raw: str
) -> bool:
    """Whether ``label`` embeds a declared object as a separate composite.

    CJK has no word boundaries, so a declared label is a substring match. Latin
    labels require a whole-token match so a compound entity such as ``OrderLine``
    is not misread as a phrase built from ``Order``.
    """
    raw = text(label)
    key = comparison_key(label)
    if not declared_key or not declared_raw or declared_key == key:
        return False
    if _CJK_RE.search(declared_raw):
        return declared_key in key
    pattern = r"(?<![A-Za-z0-9])" + re.escape(declared_raw) + r"(?![A-Za-z0-9])"
    return re.search(pattern, raw, flags=re.IGNORECASE) is not None


def accepted_facts(asset: dict[str, Any]) -> list[dict[str, Any]]:
    ledger = as_dict(asset.get("business_fact_ledger"))
    return [
        row
        for row in as_list(ledger.get("items"))
        if isinstance(row, dict)
        and text(row.get("status")).upper() in ACCEPTED_FACT_STATES
    ]


def object_slot_mentions(fact: dict[str, Any], side: str) -> list[str]:
    """Return only the fact compiler's entity-reference slot.

    ``entity_mentions`` intentionally remains a raw extraction trace.  It may
    contain an entire permission sentence, action phrase, exception fragment, or
    co-reference span.  Treating it as an object declaration made source-backed
    parser errors self-authorizing.  Business-object recognition consumes the
    narrower ``entity_refs`` contract; identity resolution may still retain the
    broader occurrence trace for audit and operator review.
    """

    return unique_text(as_list(as_dict(fact.get(side)).get("entity_refs")))


def positive_fact_mentions(fact: dict[str, Any]) -> list[tuple[str, str]]:
    if text(fact.get("kind")) not in {"RULE", "STATE_TRANSITION"}:
        return []
    return [
        (label, f"BUSINESS_FACT_{side.upper()}")
        for side in ("subject", "object")
        for label in object_slot_mentions(fact, side)
    ]


def fact_can_seed_object_type(fact: dict[str, Any]) -> bool:
    """Whether this fact may introduce a previously unknown object label.

    Downstream projections may repeat valid object references, but they are not
    source-level declaration authorities.  They may reinforce an object already
    declared elsewhere; they may never create a new object type.
    """

    if text(fact.get("parent_fact_ref")) or text(fact.get("atomic_claim_ref")):
        # An atomic claim projection materializes an explicit source-backed
        # DATA_EFFECT claim (object + predicate + source span) into a child fact.
        # Its object is source-attested, so it may seed an object type. A bare
        # derived marker (parent_fact_ref without a source-backed materialization
        # receipt) is not source authority and may not.
        return bool(fact.get("source_backed"))
    return text(fact.get("derivation")) not in NON_SEED_FACT_DERIVATIONS


def object_slot_rejection_reason(
    fact: dict[str, Any],
    label: Any,
    declared_object_labels: dict[str, str],
) -> str:
    """Return a structural rejection reason, never a vocabulary judgement."""

    key = comparison_key(label)
    if not key or key in declared_object_labels:
        return ""
    if not fact_can_seed_object_type(fact):
        return "DERIVED_FACT_CANNOT_DECLARE_BUSINESS_OBJECT"

    source_text_keys = [
        comparison_key(fact.get("normalized_statement") or fact.get("raw_statement")),
        *[
            comparison_key(span.get("quote"))
            for span in as_list(fact.get("source_spans"))
            if isinstance(span, dict)
        ],
    ]
    if not any(key in source_text for source_text in source_text_keys if source_text):
        return "OBJECT_SLOT_LABEL_NOT_SOURCE_ATTESTED"

    statement_key = comparison_key(
        fact.get("normalized_statement") or fact.get("raw_statement")
    )
    if statement_key and key == statement_key:
        return "WHOLE_STATEMENT_CANNOT_BE_BUSINESS_OBJECT_LABEL"

    # A novel label that strictly contains an already declared object is a
    # composite statement/qualified phrase unless the source separately declares
    # that longer label.  This keeps e.g. "create order" or "my order" from
    # becoming a second object while still allowing explicitly declared
    # "order attachment" as its own entity.
    if any(
        _contains_declared_object(label, declared_key, declared_raw)
        for declared_key, declared_raw in declared_object_labels.items()
    ):
        return "COMPOSITE_PHRASE_CONTAINS_DECLARED_BUSINESS_OBJECT"

    action = as_dict(fact.get("action"))
    resolved_behavior = bool(
        text(action.get("canonical"))
        or text(action.get("raw"))
        or text(fact.get("predicate"))
        or text(fact.get("from_state"))
        or text(fact.get("to_state"))
        or as_list(fact.get("state_effects"))
        or as_dict(fact.get("trigger"))
    )
    if text(fact.get("kind")) == "RULE" and not resolved_behavior:
        return "OBJECT_TYPE_SEED_REQUIRES_RESOLVED_BEHAVIOR"

    semantic_terms: list[Any] = []
    semantic_terms.extend([action.get("canonical"), action.get("raw")])
    semantic_terms.extend(as_list(as_dict(fact.get("subject")).get("actor_refs")))
    semantic_terms.extend([fact.get("from_state"), fact.get("to_state")])
    for effect in as_list(fact.get("state_effects")):
        if isinstance(effect, dict):
            semantic_terms.extend([effect.get("from_state"), effect.get("to_state")])
    if any(
        term_key and term_key != key and term_key in key
        for term_key in (comparison_key(value) for value in semantic_terms)
    ):
        return "OBJECT_SLOT_CONTAINS_ACTOR_ACTION_OR_STATE"
    return ""


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
    "NON_SEED_FACT_DERIVATIONS",
    "accepted_facts",
    "comparison_key",
    "fact_can_seed_object_type",
    "negative_role_index",
    "object_slot_mentions",
    "object_slot_rejection_reason",
    "positive_fact_mentions",
]
