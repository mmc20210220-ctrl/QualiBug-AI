"""Enforce one structure-first authority for duplicate formal typed facts.

The compatibility parser remains necessary for legacy enterprise fixtures, while the
structure-first compiler owns exact formal relation/cardinality/formula coordinates.
When both paths describe the same formal fact type from the same exact source locator
and the same normalized source statement, the structure-first row is authoritative and
the compatibility shell is deterministically retired. Different statements, locators,
or fact types never compete here and no automatic winner is chosen among real source
conflicts.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

RECEIPT_SCHEMA = "qualibug.typed-fact-authority-retirement.v1"
_STRUCTURE_DERIVATION = "structure_first_explicit_fact_compiler"
_FORMAL_TYPES = frozenset(
    {"OBJECT_RELATION", "CARDINALITY_CONSTRAINT", "DERIVED_VALUE"}
)
_EXACT_ADDRESS_KINDS = frozenset({"EXACT_SOURCE_LOCATOR", "PAGE_BBOX"})
_REASON = "SUPERSEDED_BY_STRUCTURE_FIRST_TYPED_AUTHORITY"
_BLOCK_KIND = "BLOCKED_MULTIPLE_STRUCTURE_FIRST_TYPED_AUTHORITIES"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _statement_identity(value: Any) -> str:
    return re.sub(r"[\s，,；;。！？!?]+", "", _text(value)).lower()


def _exact_locator(fact: dict[str, Any]) -> str:
    spans = [row for row in _list(fact.get("source_spans")) if isinstance(row, dict)]
    for span in spans:
        if _text(span.get("document_block_id")) or _text(
            span.get("address_kind")
        ) in _EXACT_ADDRESS_KINDS:
            locator = _text(span.get("locator") or span.get("source_locator"))
            if locator:
                return locator
    return ""


def _authority_key(fact: dict[str, Any]) -> tuple[str, str, str] | None:
    fact_type = _text(fact.get("fact_type")).upper()
    locator = _exact_locator(fact)
    statement = _statement_identity(fact.get("raw_statement"))
    if fact_type not in _FORMAL_TYPES or not locator or not statement:
        return None
    return fact_type, locator, statement


def retire_duplicate_compatibility_typed_facts(asset: dict[str, Any]) -> dict[str, Any]:
    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)]
    authorities: defaultdict[tuple[str, str, str], list[str]] = defaultdict(list)
    for fact in facts:
        if _text(fact.get("status")) != "ACCEPTED":
            continue
        if _text(fact.get("derivation")) != _STRUCTURE_DERIVATION:
            continue
        key = _authority_key(fact)
        fact_id = _text(fact.get("fact_id"))
        if key is not None and fact_id:
            authorities[key].append(fact_id)

    retired: list[dict[str, Any]] = []
    ambiguous_authorities: list[dict[str, Any]] = []
    for key, fact_ids in authorities.items():
        unique_ids = sorted(set(fact_ids))
        if len(unique_ids) > 1:
            ambiguous_authorities.append(
                {
                    "fact_type": key[0],
                    "source_locator": key[1],
                    "statement_identity": key[2],
                    "authority_fact_ids": unique_ids,
                    "reason": "MULTIPLE_STRUCTURE_FIRST_TYPED_AUTHORITIES",
                }
            )

    unambiguous = {
        key: fact_ids[0]
        for key, fact_ids in authorities.items()
        if len(set(fact_ids)) == 1
    }
    for fact in facts:
        if _text(fact.get("status")) != "ACCEPTED":
            continue
        if _text(fact.get("derivation")) == _STRUCTURE_DERIVATION:
            continue
        key = _authority_key(fact)
        authority_id = unambiguous.get(key) if key is not None else None
        if not authority_id or authority_id == _text(fact.get("fact_id")):
            continue
        fact["status"] = "REJECTED"
        fact["formal_promotion_allowed"] = False
        ambiguities = [
            _text(value) for value in _list(fact.get("ambiguities")) if _text(value)
        ]
        if _REASON not in ambiguities:
            ambiguities.append(_REASON)
        fact["ambiguities"] = ambiguities
        fact["typed_fact_authority"] = {
            "status": "RETIRED_COMPATIBILITY_SHELL",
            "authority_fact_ref": authority_id,
            "matching_contract": [
                "FACT_TYPE_EXACT",
                "EXACT_SOURCE_LOCATOR",
                "NORMALIZED_SOURCE_STATEMENT_EXACT",
            ],
            "automatic_winner_used": False,
        }
        retired.append(
            {
                "fact_id": fact.get("fact_id"),
                "authority_fact_ref": authority_id,
                "fact_type": key[0] if key else "",
                "source_locator": key[1] if key else "",
                "reason": _REASON,
            }
        )

    ledger["items"] = facts
    ledger["typed_compatibility_shell_retired_count"] = len(retired)
    asset["business_fact_ledger"] = ledger
    asset["typed_fact_authority_retirement_receipt"] = {
        "schema": RECEIPT_SCHEMA,
        "status": "BLOCKED" if ambiguous_authorities else "PASS",
        "structure_first_authority_count": len(unambiguous),
        "retired_compatibility_shell_count": len(retired),
        "retired_compatibility_shells": retired,
        "ambiguous_structure_authorities": ambiguous_authorities,
        "matching_requires_same_fact_type": True,
        "matching_requires_exact_source_locator": True,
        "matching_requires_exact_normalized_statement": True,
        "cross_statement_merge_allowed": False,
        "cross_locator_merge_allowed": False,
        "automatic_winner_used": False,
        "silent_authority_ambiguity_allowed": False,
    }
    gaps = [
        dict(row)
        for row in _list(asset.get("coverage_gaps"))
        if isinstance(row, dict) and _text(row.get("kind")) != _BLOCK_KIND
    ]
    if ambiguous_authorities:
        gate = _dict(asset.get("enterprise_comprehension_gate"))
        gate["status"] = _BLOCK_KIND
        gate["entry_allowed"] = False
        gate["required_operator_action"] = (
            "resolve duplicate structure-first typed authorities before promotion"
        )
        asset["enterprise_comprehension_gate"] = gate
        gaps.append(
            {
                "kind": _BLOCK_KIND,
                "gap_type": "multiple_structure_first_typed_authorities",
                "source_id": "*",
                "ambiguous_structure_authorities": ambiguous_authorities,
                "operator_action": gate["required_operator_action"],
            }
        )
    asset["coverage_gaps"] = gaps
    governance = _dict(asset.get("governance"))
    governance.update(
        {
            "structure_first_typed_fact_is_single_formal_authority": True,
            "duplicate_compatibility_typed_shells_are_retired": True,
            "typed_fact_authority_retirement_merges_cross_statement": False,
            "typed_fact_authority_retirement_merges_cross_locator": False,
            "typed_fact_authority_ambiguity_is_visible_coverage_gap": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["RECEIPT_SCHEMA", "retire_duplicate_compatibility_typed_facts"]
