"""Enforce one structure-first authority for formal explicit business facts.

The compatibility parser remains necessary for legacy enterprise fixtures, while the
structure-first compiler owns exact relation/cardinality/formula coordinates. Authority
identity is the complete semantic coordinate, not merely fact type plus source sentence:
multiple atomic relations may legitimately share one sentence and locator.

A compatibility row is retired in either of two conservative cases:

* it has the exact same complete formal coordinate as one structure-first fact; or
* it is an empty compatibility wrapper for the same exact source statement while one or
  more structure-first formal atoms carry the actual semantics.

Different statements or locators never compete. Rows with an independent governed
action, actor, condition, effect, exception or constraint are never treated as wrappers.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

RECEIPT_SCHEMA = "qualibug.typed-fact-authority-retirement.v2"
_STRUCTURE_DERIVATION = "structure_first_explicit_fact_compiler"
_FORMAL_TYPES = frozenset(
    {"OBJECT_RELATION", "CARDINALITY_CONSTRAINT", "DERIVED_VALUE"}
)
_EXACT_ADDRESS_KINDS = frozenset({"EXACT_SOURCE_LOCATOR", "PAGE_BBOX"})
_REASON = "SUPERSEDED_BY_STRUCTURE_FIRST_TYPED_AUTHORITY"
_BLOCK_KIND = "BLOCKED_MULTIPLE_STRUCTURE_FIRST_TYPED_AUTHORITIES"
_WRAPPER_CLAIM_TYPES = frozenset({"ATOMIC_OPERATION", "PRIMARY_OPERATION"})
_FORMAL_WRAPPER_ACTIONS = frozenset(
    {"关联", "对应", "包含", "拥有", "组成", "构成", "属于", "归属于", "依赖"}
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _text(value).lower())


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


def _statement_key(fact: dict[str, Any]) -> tuple[str, str] | None:
    locator = _exact_locator(fact)
    statement = _statement_identity(fact.get("raw_statement"))
    if not locator or not statement:
        return None
    return locator, statement


def _refs(value: Any) -> tuple[str, ...]:
    return tuple(sorted({_norm(item) for item in _list(value) if _norm(item)}))


def _typed_value_identity(fact: dict[str, Any], fact_type: str) -> tuple[Any, ...]:
    candidates: list[dict[str, Any]] = []
    top_level = _dict(fact.get("value"))
    if top_level:
        candidates.append(top_level)
    field = (
        "formula_constraints"
        if fact_type == "DERIVED_VALUE"
        else "quantity_constraints"
    )
    candidates.extend(
        dict(row)
        for row in _list(fact.get(field))
        if isinstance(row, dict) and row
    )
    for claim in _list(fact.get("claims")):
        if not isinstance(claim, dict):
            continue
        claim_type = _text(claim.get("claim_type")).upper()
        if fact_type == "DERIVED_VALUE" and claim_type not in {
            "FORMULA_CONSTRAINT",
            "DERIVED_VALUE",
        }:
            continue
        if fact_type == "CARDINALITY_CONSTRAINT" and claim_type not in {
            "CARDINALITY_CONSTRAINT",
            "QUANTITY_CONSTRAINT",
        }:
            continue
        value = _dict(claim.get("value"))
        if value:
            candidates.append(value)

    if fact_type == "DERIVED_VALUE":
        return tuple(
            sorted(
                {
                    (_norm(value.get("lhs")), _norm(value.get("rhs")))
                    for value in candidates
                    if _norm(value.get("lhs")) and _norm(value.get("rhs"))
                }
            )
        )
    if fact_type == "CARDINALITY_CONSTRAINT":
        return tuple(
            sorted(
                {
                    (
                        _text(value.get("cardinality")).upper(),
                        _text(value.get("minimum")),
                        _text(value.get("maximum")),
                    )
                    for value in candidates
                    if _text(value.get("cardinality"))
                    or _text(value.get("minimum"))
                    or _text(value.get("maximum"))
                }
            )
        )
    return ()


def _authority_key(fact: dict[str, Any]) -> tuple[Any, ...] | None:
    fact_type = _text(fact.get("fact_type")).upper()
    statement_key = _statement_key(fact)
    if fact_type not in _FORMAL_TYPES or statement_key is None:
        return None
    subject = _dict(fact.get("subject"))
    obj = _dict(fact.get("object"))
    action = _dict(fact.get("action"))
    predicate = _norm(
        fact.get("predicate")
        or fact.get("relation_type")
        or action.get("canonical")
        or action.get("raw")
    )
    value_identity = _typed_value_identity(fact, fact_type)
    if fact_type == "DERIVED_VALUE":
        return (
            fact_type,
            statement_key[0],
            statement_key[1],
            value_identity,
        )
    return (
        fact_type,
        statement_key[0],
        statement_key[1],
        predicate,
        _refs(subject.get("entity_refs")),
        _refs(obj.get("entity_refs")),
        value_identity,
    )


def _has_rows(fact: dict[str, Any], *fields: str) -> bool:
    return any(bool(_list(fact.get(field))) for field in fields)


def _is_compatibility_wrapper(fact: dict[str, Any]) -> bool:
    """Return true only when the row carries no independent executable semantics."""
    if _text(fact.get("derivation")) == _STRUCTURE_DERIVATION:
        return False
    if _statement_key(fact) is None:
        return False
    subject = _dict(fact.get("subject"))
    action = _dict(fact.get("action"))
    frame = _dict(fact.get("condition_frame"))
    if _list(subject.get("actor_refs")):
        return False
    governed_action = _text(action.get("canonical") or action.get("raw"))
    if governed_action and governed_action not in _FORMAL_WRAPPER_ACTIONS:
        return False
    if _list(fact.get("conditions")) or _list(frame.get("conditions")):
        return False
    if _has_rows(
        fact,
        "exception_scope",
        "exceptions",
        "state_effects",
        "data_effects",
        "postconditions",
        "compensation",
        "compensations",
        "quantity_constraints",
        "time_window_constraints",
        "formula_constraints",
    ):
        return False
    claim_types = {
        _text(claim.get("claim_type")).upper()
        for claim in _list(fact.get("claims"))
        if isinstance(claim, dict) and _text(claim.get("claim_type"))
    }
    return not claim_types or claim_types.issubset(_WRAPPER_CLAIM_TYPES)


def retire_duplicate_compatibility_typed_facts(asset: dict[str, Any]) -> dict[str, Any]:
    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)]
    authorities: defaultdict[tuple[Any, ...], list[str]] = defaultdict(list)
    authority_by_statement: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
    key_by_fact_id: dict[str, tuple[Any, ...]] = {}

    for fact in facts:
        if _text(fact.get("status")) != "ACCEPTED":
            continue
        if _text(fact.get("derivation")) != _STRUCTURE_DERIVATION:
            continue
        key = _authority_key(fact)
        statement_key = _statement_key(fact)
        fact_id = _text(fact.get("fact_id"))
        if key is None or statement_key is None or not fact_id:
            continue
        authorities[key].append(fact_id)
        authority_by_statement[statement_key].append(fact_id)
        key_by_fact_id[fact_id] = key

    ambiguous_authorities: list[dict[str, Any]] = []
    ambiguous_fact_ids: set[str] = set()
    for key, fact_ids in authorities.items():
        unique_ids = sorted(set(fact_ids))
        if len(unique_ids) <= 1:
            continue
        ambiguous_fact_ids.update(unique_ids)
        ambiguous_authorities.append(
            {
                "fact_type": key[0],
                "source_locator": key[1],
                "statement_identity": key[2],
                "semantic_coordinate": list(key[3:]),
                "authority_fact_ids": unique_ids,
                "reason": "MULTIPLE_STRUCTURE_FIRST_TYPED_AUTHORITIES",
            }
        )

    unambiguous = {
        key: sorted(set(fact_ids))[0]
        for key, fact_ids in authorities.items()
        if len(set(fact_ids)) == 1
    }
    retired: list[dict[str, Any]] = []
    for fact in facts:
        if _text(fact.get("status")) != "ACCEPTED":
            continue
        if _text(fact.get("derivation")) == _STRUCTURE_DERIVATION:
            continue

        authority_refs: list[str] = []
        retirement_mode = ""
        key = _authority_key(fact)
        exact_authority = unambiguous.get(key) if key is not None else None
        if exact_authority:
            authority_refs = [exact_authority]
            retirement_mode = "EXACT_FORMAL_COORDINATE"
        elif _is_compatibility_wrapper(fact):
            statement_key = _statement_key(fact)
            candidates = sorted(
                {
                    fact_id
                    for fact_id in authority_by_statement.get(statement_key, [])
                    if fact_id not in ambiguous_fact_ids
                }
            )
            if candidates:
                authority_refs = candidates
                retirement_mode = "SAME_STATEMENT_EMPTY_COMPATIBILITY_WRAPPER"

        if not authority_refs:
            continue
        fact["status"] = "REJECTED"
        fact["formal_promotion_allowed"] = False
        ambiguities = [
            _text(value) for value in _list(fact.get("ambiguities")) if _text(value)
        ]
        if _REASON not in ambiguities:
            ambiguities.append(_REASON)
        fact["ambiguities"] = ambiguities
        authority = {
            "status": "RETIRED_COMPATIBILITY_SHELL",
            "authority_fact_refs": authority_refs,
            "retirement_mode": retirement_mode,
            "matching_contract": [
                "EXACT_SOURCE_LOCATOR",
                "NORMALIZED_SOURCE_STATEMENT_EXACT",
                (
                    "COMPLETE_FORMAL_SEMANTIC_COORDINATE"
                    if retirement_mode == "EXACT_FORMAL_COORDINATE"
                    else "NO_INDEPENDENT_EXECUTABLE_SEMANTICS"
                ),
            ],
            "automatic_winner_used": False,
        }
        if len(authority_refs) == 1:
            authority["authority_fact_ref"] = authority_refs[0]
        fact["typed_fact_authority"] = authority
        retired.append(
            {
                "fact_id": fact.get("fact_id"),
                "authority_fact_refs": authority_refs,
                "retirement_mode": retirement_mode,
                "source_locator": (_statement_key(fact) or ("", ""))[0],
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
        "structure_first_fact_count": len(key_by_fact_id),
        "unique_structure_first_coordinate_count": len(unambiguous),
        "retired_compatibility_shell_count": len(retired),
        "retired_compatibility_shells": retired,
        "ambiguous_structure_authorities": ambiguous_authorities,
        "matching_requires_complete_semantic_coordinate": True,
        "wrapper_retirement_requires_exact_statement_locator": True,
        "wrapper_retirement_requires_no_independent_executable_semantics": True,
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
            "typed_fact_authority_uses_complete_semantic_coordinate": True,
            "same_statement_multiple_atomic_relations_are_valid": True,
            "duplicate_compatibility_typed_shells_are_retired": True,
            "empty_cross_type_compatibility_wrappers_are_retired": True,
            "typed_fact_authority_retirement_merges_cross_statement": False,
            "typed_fact_authority_retirement_merges_cross_locator": False,
            "typed_fact_authority_ambiguity_is_visible_coverage_gap": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["RECEIPT_SCHEMA", "retire_duplicate_compatibility_typed_facts"]
