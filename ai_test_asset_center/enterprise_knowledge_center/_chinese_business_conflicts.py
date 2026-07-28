"""Source-backed conflict reconciliation for Chinese business facts.

This stage never guesses which document is right. It detects contradictory
Chinese facts, records exact source spans, removes conflicting derived rules from
formal downstream input, and blocks the comprehension gate until an explicit
authority/version decision resolves the conflict.
"""
from __future__ import annotations

import hashlib
import re
from functools import wraps
from pathlib import Path
from typing import Any


CONFLICT_SCHEMA = "qualibug.chinese-business-fact-conflicts.v1"
_CONFLICTING_MODALITIES = {
    frozenset({"MUST_NOT", "MAY"}),
    frozenset({"MUST_NOT", "MUST"}),
    frozenset({"MUST_NOT", "ASSERTS"}),
    frozenset({"ONLY_IF", "MAY"}),
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _norm(value: Any) -> str:
    return re.sub(r"[\s，,。；;：:（）()【】\[\]“”\"'、]+", "", _text(value)).lower()


def _source_span(fact: dict[str, Any]) -> dict[str, Any]:
    spans = _list(fact.get("source_spans"))
    return _dict(spans[0]) if spans else {}


def _semantic_key(fact: dict[str, Any]) -> tuple[Any, ...]:
    subject = _dict(fact.get("subject"))
    action = _dict(fact.get("action"))
    scope = _dict(fact.get("scope"))
    entities = tuple(sorted(_norm(value) for value in _list(subject.get("entity_refs")) if _norm(value)))
    actors = tuple(sorted(_norm(value) for value in _list(subject.get("actor_refs")) if _norm(value)))
    conditions = tuple(sorted(_norm(value) for value in _list(fact.get("conditions")) if _norm(value)))
    scope_key = tuple(
        sorted(
            (str(key), _norm(value))
            for key, value in scope.items()
            if _norm(value)
        )
    )
    action_name = _norm(action.get("canonical") or action.get("raw"))
    return (entities, actors, action_name, conditions, scope_key)


def _transition_key(fact: dict[str, Any]) -> tuple[Any, ...]:
    base = _semantic_key(fact)
    effects = _list(fact.get("state_effects"))
    from_states = tuple(
        sorted(
            _norm(_dict(effect).get("from_state"))
            for effect in effects
            if _norm(_dict(effect).get("from_state"))
        )
    )
    return (*base, from_states)


def _transition_targets(fact: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        sorted(
            _norm(_dict(effect).get("to_state"))
            for effect in _list(fact.get("state_effects"))
            if _norm(_dict(effect).get("to_state"))
        )
    )


def _fact_identity(fact: dict[str, Any]) -> str:
    fact_id = _text(fact.get("fact_id"))
    if fact_id:
        return fact_id
    return "fact:" + hashlib.sha256(
        repr((_semantic_key(fact), _text(fact.get("raw_statement")))).encode("utf-8")
    ).hexdigest()[:20]


def _conflict_id(kind: str, facts: list[dict[str, Any]]) -> str:
    material = "|".join(sorted(_fact_identity(fact) for fact in facts))
    return f"conflict:{kind.lower()}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _modality_conflict(left: dict[str, Any], right: dict[str, Any]) -> bool:
    pair = frozenset({_text(left.get("modality")), _text(right.get("modality"))})
    return pair in _CONFLICTING_MODALITIES


def _transition_conflict(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if _text(left.get("kind")) != "STATE_TRANSITION":
        return False
    if _text(right.get("kind")) != "STATE_TRANSITION":
        return False
    left_targets = _transition_targets(left)
    right_targets = _transition_targets(right)
    return bool(left_targets and right_targets and left_targets != right_targets)


def _conflict_row(
    kind: str,
    facts: list[dict[str, Any]],
    *,
    reason: str,
) -> dict[str, Any]:
    sources = []
    for fact in facts:
        span = _source_span(fact)
        sources.append(
            {
                "fact_id": _fact_identity(fact),
                "source_id": span.get("source_id"),
                "source_locator": span.get("locator"),
                "quote": span.get("quote"),
                "quote_hash": span.get("quote_hash"),
                "modality": fact.get("modality"),
                "statement": fact.get("raw_statement"),
            }
        )
    distinct_source_ids = sorted(
        {
            _text(row.get("source_id"))
            for row in sources
            if _text(row.get("source_id"))
        }
    )
    return {
        "conflict_id": _conflict_id(kind, facts),
        "schema": CONFLICT_SCHEMA,
        "kind": kind,
        "status": "UNRESOLVED",
        "reason": reason,
        "source_scope": (
            "CROSS_SOURCE"
            if len(distinct_source_ids) > 1
            else "INTRA_SOURCE"
        ),
        "source_ids": distinct_source_ids,
        "facts": sources,
        "resolution_policy": (
            "explicit source authority/version decision required; "
            "recency, filename, document order and model confidence are not authority"
        ),
        "automatic_resolution_allowed": False,
    }


def reconcile_chinese_business_fact_conflicts(
    asset: dict[str, Any],
) -> dict[str, Any]:
    """Detect contradictions and fail closed without choosing a winner."""
    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)]
    formal_facts = [
        fact
        for fact in facts
        if _text(fact.get("status")) == "ACCEPTED"
        and _text(fact.get("kind")) in {"RULE", "STATE_TRANSITION"}
    ]

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    transition_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for fact in formal_facts:
        groups.setdefault(_semantic_key(fact), []).append(fact)
        if _text(fact.get("kind")) == "STATE_TRANSITION":
            transition_groups.setdefault(_transition_key(fact), []).append(fact)

    conflicts: list[dict[str, Any]] = []
    conflicting_fact_ids: set[str] = set()

    for group in groups.values():
        for index, left in enumerate(group):
            for right in group[index + 1 :]:
                if not _modality_conflict(left, right):
                    continue
                row = _conflict_row(
                    "BUSINESS_MODALITY_CONTRADICTION",
                    [left, right],
                    reason=(
                        f"same subject/action/condition/scope has incompatible "
                        f"modalities {_text(left.get('modality'))} and "
                        f"{_text(right.get('modality'))}"
                    ),
                )
                conflicts.append(row)
                conflicting_fact_ids.update(
                    {_fact_identity(left), _fact_identity(right)}
                )

    for group in transition_groups.values():
        for index, left in enumerate(group):
            for right in group[index + 1 :]:
                if not _transition_conflict(left, right):
                    continue
                row = _conflict_row(
                    "STATE_TRANSITION_TARGET_CONTRADICTION",
                    [left, right],
                    reason=(
                        "same entity/action/from-state context declares different "
                        "target states"
                    ),
                )
                conflicts.append(row)
                conflicting_fact_ids.update(
                    {_fact_identity(left), _fact_identity(right)}
                )

    unique_conflicts = {
        _text(row.get("conflict_id")): row
        for row in conflicts
        if _text(row.get("conflict_id"))
    }
    conflicts = list(unique_conflicts.values())

    updated_facts: list[dict[str, Any]] = []
    conflict_refs_by_fact: dict[str, list[str]] = {}
    for conflict in conflicts:
        for row in _list(conflict.get("facts")):
            fact_id = _text(_dict(row).get("fact_id"))
            if fact_id:
                conflict_refs_by_fact.setdefault(fact_id, []).append(
                    _text(conflict.get("conflict_id"))
                )
    for fact in facts:
        fact_id = _fact_identity(fact)
        if fact_id in conflicting_fact_ids:
            fact["status"] = "CONFLICTING"
            fact["conflict_refs"] = sorted(set(conflict_refs_by_fact.get(fact_id, [])))
            fact["formal_promotion_allowed"] = False
        updated_facts.append(fact)
    ledger["items"] = updated_facts
    ledger["conflict_schema"] = CONFLICT_SCHEMA
    ledger["unresolved_conflict_count"] = len(conflicts)
    asset["business_fact_ledger"] = ledger

    existing_rules = [
        dict(row)
        for row in _list(asset.get("rule_library"))
        if isinstance(row, dict)
    ]
    removed_rule_ids: list[str] = []
    retained_rules: list[dict[str, Any]] = []
    for rule in existing_rules:
        semantic_contract = _dict(rule.get("semantic_contract"))
        fact_id = _text(semantic_contract.get("fact_id"))
        if (
            _text(rule.get("derivation"))
            == "chinese_first_business_comprehension"
            and fact_id in conflicting_fact_ids
        ):
            removed_rule_ids.append(_text(rule.get("rule_id")))
            continue
        retained_rules.append(rule)
    asset["rule_library"] = retained_rules

    existing_conflicts = [
        dict(row)
        for row in _list(asset.get("cross_document_conflicts"))
        if isinstance(row, dict)
        and _text(row.get("schema")) != CONFLICT_SCHEMA
    ]
    asset["cross_document_conflicts"] = [*existing_conflicts, *conflicts]

    gate = _dict(asset.get("enterprise_comprehension_gate"))
    if conflicts:
        gate["status"] = "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS"
        gate["entry_allowed"] = False
        gate["unresolved_business_fact_conflicts"] = conflicts
        gate["removed_conflicting_rule_ids"] = sorted(
            rule_id for rule_id in removed_rule_ids if rule_id
        )
        gate["required_operator_action"] = (
            "resolve source authority/version for each conflicting Chinese business "
            "fact; do not choose by recency, filename order or model confidence"
        )
    else:
        gate["unresolved_business_fact_conflicts"] = []
        gate["removed_conflicting_rule_ids"] = []
    asset["enterprise_comprehension_gate"] = gate

    gaps = [
        dict(row)
        for row in _list(asset.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("kind"))
        != "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS"
    ]
    if conflicts:
        gaps.append(
            {
                "kind": "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS",
                "gap_type": "unresolved_chinese_business_fact_conflict",
                "source_id": "*",
                "conflict_ids": [
                    conflict.get("conflict_id") for conflict in conflicts
                ],
                "operator_action": gate["required_operator_action"],
            }
        )
    asset["coverage_gaps"] = gaps

    summary = _dict(asset.get("summary"))
    summary.update(
        {
            "rule_count": len(retained_rules),
            "chinese_business_fact_conflict_count": len(conflicts),
            "chinese_business_conflicting_fact_count": len(conflicting_fact_ids),
            "chinese_business_conflicting_rules_removed": len(removed_rule_ids),
        }
    )
    asset["summary"] = summary
    return asset


def install_chinese_business_conflict_reconciliation():
    """Install reconciliation after fact extraction and before downstream binding."""
    from . import _api
    from ._common import ROOT, _safe_project_id
    from ._chinese_business_comprehension import _persist_enriched_asset

    current = _api.build_enterprise_business_knowledge_asset
    if getattr(current, "_qualibug_chinese_conflict_reconciliation", False):
        return current
    original = current

    @wraps(original)
    def wrapped(
        project_id: str = "real_project_demo",
        root: Path | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_root = root or ROOT
        project = _safe_project_id(project_id)
        asset = original(project, resolved_root, options or {})
        reconciled = reconcile_chinese_business_fact_conflicts(asset)
        _persist_enriched_asset(reconciled, project, resolved_root)
        return reconciled

    wrapped._qualibug_chinese_conflict_reconciliation = True  # type: ignore[attr-defined]
    wrapped._qualibug_original_builder = original  # type: ignore[attr-defined]
    _api.build_enterprise_business_knowledge_asset = wrapped
    return wrapped


__all__ = [
    "CONFLICT_SCHEMA",
    "reconcile_chinese_business_fact_conflicts",
    "install_chinese_business_conflict_reconciliation",
]
