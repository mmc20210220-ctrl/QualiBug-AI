"""Fail-closed reconciliation for source-backed Chinese business facts."""
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
    frozenset({"MUST_NOT", "ONLY_IF"}),
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _norm(value: Any) -> str:
    return re.sub(r"[\s，,。；;：:（）()【】\[\]“”\"'、]+", "", _text(value)).lower()


def _span(fact: dict[str, Any]) -> dict[str, Any]:
    spans = _list(fact.get("source_spans"))
    return _dict(spans[0]) if spans else {}


def _fact_id(fact: dict[str, Any]) -> str:
    explicit = _text(fact.get("fact_id"))
    if explicit:
        return explicit
    material = f"{_key(fact)!r}|{_text(fact.get('raw_statement'))}"
    return "fact:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _key(fact: dict[str, Any]) -> tuple[Any, ...]:
    subject = _dict(fact.get("subject"))
    action = _dict(fact.get("action"))
    scope = _dict(fact.get("scope"))
    return (
        tuple(sorted(_norm(v) for v in _list(subject.get("entity_refs")) if _norm(v))),
        tuple(sorted(_norm(v) for v in _list(subject.get("actor_refs")) if _norm(v))),
        _norm(action.get("canonical") or action.get("raw")),
        tuple(sorted(_norm(v) for v in _list(fact.get("conditions")) if _norm(v))),
        tuple(sorted((str(k), _norm(v)) for k, v in scope.items() if _norm(v))),
    )


def _transition_key(fact: dict[str, Any]) -> tuple[Any, ...]:
    from_states = tuple(
        sorted(
            _norm(_dict(effect).get("from_state"))
            for effect in _list(fact.get("state_effects"))
            if _norm(_dict(effect).get("from_state"))
        )
    )
    return (*_key(fact), from_states)


def _targets(fact: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        sorted(
            _norm(_dict(effect).get("to_state"))
            for effect in _list(fact.get("state_effects"))
            if _norm(_dict(effect).get("to_state"))
        )
    )


def _conflict(
    kind: str,
    left: dict[str, Any],
    right: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    facts = [left, right]
    material = "|".join(sorted(_fact_id(fact) for fact in facts))
    sources: list[dict[str, Any]] = []
    for fact in facts:
        span = _span(fact)
        sources.append(
            {
                "fact_id": _fact_id(fact),
                "source_id": span.get("source_id"),
                "source_locator": span.get("locator"),
                "quote": span.get("quote"),
                "quote_hash": span.get("quote_hash"),
                "modality": fact.get("modality"),
                "statement": fact.get("raw_statement"),
            }
        )
    source_ids = sorted({_text(row.get("source_id")) for row in sources if _text(row.get("source_id"))})
    resolution_policy = (
        "explicit source authority/version decision required; recency, filename, "
        "document order and model confidence are not authority"
    )
    operator_action = (
        "resolve source authority/version for each conflicting Chinese business fact; "
        "do not choose by recency, filename order, document appearance, or model confidence"
    )
    # Standard evidence/message fields so product receipts never silently drop opposing spans.
    evidence = [
        {
            "source_id": row.get("source_id"),
            "source_locator": row.get("source_locator"),
            "quote": row.get("quote") or row.get("statement"),
            "quote_hash": row.get("quote_hash"),
            "fact_id": row.get("fact_id"),
            "derivation": "unresolved_business_fact_conflict",
        }
        for row in sources
        if _text(row.get("quote") or row.get("statement") or row.get("fact_id") or row.get("source_id"))
    ]
    return {
        "conflict_id": f"conflict:{kind.lower()}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}",
        "schema": CONFLICT_SCHEMA,
        "kind": kind,
        "status": "UNRESOLVED",
        "reason": reason,
        "message": reason,
        "operator_action": operator_action,
        "source_scope": "CROSS_SOURCE" if len(source_ids) > 1 else "INTRA_SOURCE",
        "source_ids": source_ids,
        "facts": sources,
        "evidence": evidence,
        "authority_decision": {
            "status": "UNRESOLVED",
            "selected_fact_id": "",
            "authority_source_id": "",
            "document_version": "",
            "operator_required": True,
            "automatic_resolution_allowed": False,
            "disallowed_authority_signals": [
                "recency",
                "filename",
                "document_order",
                "model_confidence",
                "industry_default",
            ],
        },
        "resolution_policy": resolution_policy,
        "automatic_resolution_allowed": False,
    }


def _pairs(rows: list[dict[str, Any]]):
    for index, left in enumerate(rows):
        for right in rows[index + 1 :]:
            yield left, right


def reconcile_chinese_business_fact_conflicts(asset: dict[str, Any]) -> dict[str, Any]:
    """Mark contradictory facts CONFLICTING and remove their derived rules."""
    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)]
    accepted = [
        fact for fact in facts
        if _text(fact.get("status")) == "ACCEPTED"
        and _text(fact.get("kind")) in {"RULE", "STATE_TRANSITION"}
    ]
    by_rule_key: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    by_transition_key: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for fact in accepted:
        by_rule_key.setdefault(_key(fact), []).append(fact)
        if _text(fact.get("kind")) == "STATE_TRANSITION":
            by_transition_key.setdefault(_transition_key(fact), []).append(fact)

    conflicts: list[dict[str, Any]] = []
    conflicting_ids: set[str] = set()
    for group in by_rule_key.values():
        for left, right in _pairs(group):
            modalities = frozenset({_text(left.get("modality")), _text(right.get("modality"))})
            if modalities not in _CONFLICTING_MODALITIES:
                continue
            conflicts.append(
                _conflict(
                    "BUSINESS_MODALITY_CONTRADICTION",
                    left,
                    right,
                    f"same subject/action/condition/scope has incompatible modalities "
                    f"{_text(left.get('modality'))} and {_text(right.get('modality'))}",
                )
            )
            conflicting_ids.update({_fact_id(left), _fact_id(right)})

    for group in by_transition_key.values():
        for left, right in _pairs(group):
            left_targets, right_targets = _targets(left), _targets(right)
            if not left_targets or not right_targets or left_targets == right_targets:
                continue
            conflicts.append(
                _conflict(
                    "STATE_TRANSITION_TARGET_CONTRADICTION",
                    left,
                    right,
                    "same entity/action/from-state context declares different target states",
                )
            )
            conflicting_ids.update({_fact_id(left), _fact_id(right)})

    conflicts = list({_text(row.get("conflict_id")): row for row in conflicts}.values())
    refs: dict[str, list[str]] = {}
    for conflict in conflicts:
        for row in _list(conflict.get("facts")):
            fact_id = _text(_dict(row).get("fact_id"))
            if fact_id:
                refs.setdefault(fact_id, []).append(_text(conflict.get("conflict_id")))

    for fact in facts:
        fact_id = _fact_id(fact)
        if fact_id in conflicting_ids:
            fact.update(
                {
                    "status": "CONFLICTING",
                    "conflict_refs": sorted(set(refs.get(fact_id, []))),
                    "formal_promotion_allowed": False,
                }
            )
    ledger.update(
        {
            "items": facts,
            "conflict_schema": CONFLICT_SCHEMA,
            "unresolved_conflict_count": len(conflicts),
        }
    )
    asset["business_fact_ledger"] = ledger

    removed_rule_ids: list[str] = []
    retained_rules: list[dict[str, Any]] = []
    for rule in _list(asset.get("rule_library")):
        if not isinstance(rule, dict):
            continue
        fact_id = _text(_dict(rule.get("semantic_contract")).get("fact_id"))
        if (
            _text(rule.get("derivation")) == "chinese_first_business_comprehension"
            and fact_id in conflicting_ids
        ):
            removed_rule_ids.append(_text(rule.get("rule_id")))
        else:
            retained_rules.append(dict(rule))
    asset["rule_library"] = retained_rules

    prior = [
        dict(row) for row in _list(asset.get("cross_document_conflicts"))
        if isinstance(row, dict) and _text(row.get("schema")) != CONFLICT_SCHEMA
    ]
    asset["cross_document_conflicts"] = [*prior, *conflicts]

    gate = _dict(asset.get("enterprise_comprehension_gate"))
    if conflicts:
        gate.update(
            {
                "status": "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS",
                "entry_allowed": False,
                "unresolved_business_fact_conflicts": conflicts,
                "removed_conflicting_rule_ids": sorted(v for v in removed_rule_ids if v),
                "required_operator_action": (
                    "resolve source authority/version for each conflicting Chinese "
                    "business fact; do not choose by recency, filename order or model confidence"
                ),
            }
        )
    else:
        gate["unresolved_business_fact_conflicts"] = []
        gate["removed_conflicting_rule_ids"] = []
    asset["enterprise_comprehension_gate"] = gate

    gaps = [
        dict(row) for row in _list(asset.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("kind")) != "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS"
    ]
    if conflicts:
        gaps.append(
            {
                "kind": "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS",
                "gap_type": "unresolved_chinese_business_fact_conflict",
                "source_id": "*",
                "conflict_ids": [row.get("conflict_id") for row in conflicts],
                "operator_action": gate["required_operator_action"],
            }
        )
    asset["coverage_gaps"] = gaps
    summary = _dict(asset.get("summary"))
    summary.update(
        {
            "rule_count": len(retained_rules),
            "chinese_business_fact_conflict_count": len(conflicts),
            "chinese_business_conflicting_fact_count": len(conflicting_ids),
            "chinese_business_conflicting_rules_removed": len(removed_rule_ids),
        }
    )
    asset["summary"] = summary
    return asset


def install_chinese_business_conflict_reconciliation():
    """Install after fact extraction and before downstream binding."""
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
        reconciled = reconcile_chinese_business_fact_conflicts(
            original(project, resolved_root, options or {})
        )
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
