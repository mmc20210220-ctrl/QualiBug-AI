"""Fail-closed reconciliation for source-backed Chinese business facts."""
from __future__ import annotations

import hashlib
import re
from functools import wraps
from pathlib import Path
from typing import Any

CONFLICT_SCHEMA = "qualibug.chinese-business-fact-conflicts.v1"
TECHNICAL_CONFLICT_SCHEMA = "qualibug.technical-cross-source-conflict.v1"
AUTHORITY_ELIGIBLE_SCHEMAS = frozenset({CONFLICT_SCHEMA, TECHNICAL_CONFLICT_SCHEMA})
_CONFLICTING_MODALITIES = {
    frozenset({"MUST_NOT", "MAY"}),
    frozenset({"MUST_NOT", "MUST"}),
    frozenset({"MUST_NOT", "ASSERTS"}),
    frozenset({"MUST_NOT", "ONLY_IF"}),
}
_DISALLOWED_AUTHORITY_SIGNALS = (
    "recency",
    "filename",
    "document_order",
    "model_confidence",
    "industry_default",
)


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
    *,
    schema: str = CONFLICT_SCHEMA,
) -> dict[str, Any]:
    facts = [left, right]
    material = "|".join(sorted(_fact_id(fact) for fact in facts))
    sources: list[dict[str, Any]] = []
    for fact in facts:
        span = _span(fact)
        sources.append(
            {
                "fact_id": _fact_id(fact),
                "source_id": span.get("source_id") or fact.get("source_id"),
                "source_locator": span.get("locator") or fact.get("source_locator"),
                "quote": span.get("quote"),
                "quote_hash": span.get("quote_hash"),
                "normalized_evidence": span.get("normalized_evidence") or fact.get("normalized_evidence"),
                "evidence_kind": span.get("evidence_kind") or fact.get("evidence_kind"),
                "evidence_derivation": span.get("evidence_derivation") or fact.get("evidence_derivation"),
                "modality": fact.get("modality"),
                "statement": fact.get("raw_statement") or fact.get("statement"),
            }
        )
    source_ids = sorted({_text(row.get("source_id")) for row in sources if _text(row.get("source_id"))})
    resolution_policy = (
        "explicit source authority/version decision required; recency, filename, "
        "document order and model confidence are not authority"
    )
    operator_action = (
        "resolve source authority/version for each conflicting fact via explicit "
        "operator SELECT_FACT or LEAVE_UNRESOLVED; do not choose by recency, "
        "filename order, document appearance, or model confidence"
    )
    # Standard evidence/message fields so product receipts never silently drop opposing spans.
    evidence = [
        {
            "source_id": row.get("source_id"),
            "source_locator": row.get("source_locator"),
            "quote": row.get("quote"),
            "quote_hash": row.get("quote_hash"),
            "normalized_evidence": row.get("normalized_evidence"),
            "evidence_kind": row.get("evidence_kind"),
            "evidence_derivation": row.get("evidence_derivation"),
            "fact_id": row.get("fact_id"),
            "derivation": "unresolved_business_fact_conflict",
        }
        for row in sources
        if _text(row.get("quote") or row.get("normalized_evidence") or row.get("statement") or row.get("fact_id") or row.get("source_id"))
    ]
    return {
        "conflict_id": f"conflict:{kind.lower()}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}",
        "schema": schema,
        "kind": kind,
        "conflict_type": kind.lower(),
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
            "disallowed_authority_signals": list(_DISALLOWED_AUTHORITY_SIGNALS),
        },
        "resolution_policy": resolution_policy,
        "automatic_resolution_allowed": False,
    }


def make_authority_eligible_conflict(
    kind: str,
    participants: list[dict[str, Any]],
    reason: str,
    *,
    schema: str = TECHNICAL_CONFLICT_SCHEMA,
    entity: str = "",
) -> dict[str, Any]:
    """Build a fail-closed SELECT_FACT / LEAVE_UNRESOLVED conflict row.

    Participants must each expose a stable ``fact_id``. Never auto-picks a winner.
    """
    rows = [dict(row) for row in participants if isinstance(row, dict) and _fact_id(row)]
    if len(rows) < 2:
        raise ValueError("authority_eligible_conflict_requires_two_participants")
    # Pairwise expand via first two for stable id; retain all participants in facts.
    base = _conflict(kind, rows[0], rows[1], reason, schema=schema)
    if len(rows) > 2:
        sources: list[dict[str, Any]] = []
        for fact in rows:
            span = _span(fact)
            sources.append(
                {
                    "fact_id": _fact_id(fact),
                    "source_id": span.get("source_id") or fact.get("source_id"),
                    "source_locator": span.get("locator") or fact.get("source_locator"),
                    "quote": span.get("quote"),
                    "quote_hash": span.get("quote_hash"),
                    "normalized_evidence": span.get("normalized_evidence") or fact.get("normalized_evidence"),
                    "evidence_kind": span.get("evidence_kind") or fact.get("evidence_kind"),
                    "evidence_derivation": span.get("evidence_derivation") or fact.get("evidence_derivation"),
                    "modality": fact.get("modality"),
                    "statement": fact.get("raw_statement") or fact.get("statement"),
                }
            )
        material = "|".join(sorted(_fact_id(fact) for fact in rows))
        base["conflict_id"] = (
            f"conflict:{kind.lower()}:"
            f"{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"
        )
        base["facts"] = sources
        base["evidence"] = [
            {
                "source_id": row.get("source_id"),
                "source_locator": row.get("source_locator"),
                "quote": row.get("quote"),
                "quote_hash": row.get("quote_hash"),
                "normalized_evidence": row.get("normalized_evidence"),
                "evidence_kind": row.get("evidence_kind"),
                "evidence_derivation": row.get("evidence_derivation"),
                "fact_id": row.get("fact_id"),
                "derivation": "unresolved_technical_cross_source_conflict",
            }
            for row in sources
            if _text(row.get("quote") or row.get("normalized_evidence") or row.get("statement") or row.get("fact_id") or row.get("source_id"))
        ]
        base["source_ids"] = sorted(
            {_text(row.get("source_id")) for row in sources if _text(row.get("source_id"))}
        )
        base["source_scope"] = (
            "CROSS_SOURCE" if len(base["source_ids"]) > 1 else "INTRA_SOURCE"
        )
    if entity:
        base["entity"] = entity
    return base


def _term_alias_authority_conflicts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Promote TERM_ALIAS identity clashes into authority-eligible conflict rows."""
    by_alias: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        if _text(fact.get("kind")) != "TERM_ALIAS":
            continue
        if _text(fact.get("status")) not in {"ACCEPTED", "PENDING", "CONFLICTING", "SUPERSEDED"}:
            continue
        alias = _text(fact.get("alias"))
        canonical = _text(fact.get("canonical_term"))
        if not alias or not canonical:
            continue
        by_alias.setdefault(alias, []).append(fact)

    conflicts: list[dict[str, Any]] = []
    for alias, group in sorted(by_alias.items()):
        canons = sorted(
            {
                _text(row.get("canonical_term"))
                for row in group
                if _text(row.get("canonical_term"))
            }
        )
        if len(canons) < 2:
            continue
        # One representative fact per canonical candidate (stable SELECT targets).
        representatives: list[dict[str, Any]] = []
        seen_canons: set[str] = set()
        for row in group:
            canon = _text(row.get("canonical_term"))
            if canon in seen_canons:
                continue
            seen_canons.add(canon)
            representatives.append(row)
        if len(representatives) < 2:
            continue
        conflicts.append(
            make_authority_eligible_conflict(
                "TERM_ALIAS_IDENTITY_CONFLICT",
                representatives,
                (
                    f"alias '{alias}' is declared as multiple canonical terms: "
                    f"{', '.join(canons)}"
                ),
                schema=TECHNICAL_CONFLICT_SCHEMA,
                entity=alias,
            )
        )
    return conflicts


def _pairs(rows: list[dict[str, Any]]):
    for index, left in enumerate(rows):
        for right in rows[index + 1 :]:
            yield left, right


def reconcile_chinese_business_fact_conflicts(
    asset: dict[str, Any],
    *,
    project_id: str = "",
    root: Path | None = None,
) -> dict[str, Any]:
    """Detect contradictions, then honor durable operator authority decisions.

    Detection never auto-picks a winner. Until an operator SELECT_FACT decision
    matches the conflict participants, facts stay non-promotable and gates stay
    blocked. Explicit LEAVE_UNRESOLVED also stays blocked.
    """
    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)]
    # Recompute may re-emit previously SUPERSEDED/CONFLICTING facts as ACCEPTED from
    # source extraction; only ACCEPTED (+ still-conflicting prior) participate.
    accepted = [
        fact for fact in facts
        if _text(fact.get("status")) in {"ACCEPTED", "CONFLICTING", "SUPERSEDED"}
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
    for key, group in by_rule_key.items():
        # A modality contradiction requires the two rules to govern the SAME
        # business object. The action slot alone is not object evidence —
        # the extractor normalizes the same verb across unrelated rules
        # (支付幂等键 and 响应不得返回支付密钥 both canonicalize to 付款), so
        # action-only grouping would freeze real constraints against
        # unrelated rules and drop them from discovery. At least one object
        # dimension (entity/actor/condition/scope) must be shared, else the
        # pair cannot be proven contradictory — fail-safe, no conflict.
        _has_object_evidence = any(
            (
                (isinstance(part, (list, tuple)) and bool(part))
                or (isinstance(part, str) and part.strip())
            )
            for index, part in enumerate(key)
            if index != 2  # the action slot is not object evidence
        )
        if not _has_object_evidence:
            continue
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

    for conflict in _term_alias_authority_conflicts(facts):
        conflicts.append(conflict)
        for row in _list(conflict.get("facts")):
            fact_id = _text(_dict(row).get("fact_id"))
            if fact_id:
                conflicting_ids.add(fact_id)

    conflicts = list({_text(row.get("conflict_id")): row for row in conflicts}.values())
    current_conflict_ids = {
        _text(row.get("conflict_id")) for row in conflicts if _text(row.get("conflict_id"))
    }
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
        elif (
            _text(fact.get("status")) == "CONFLICTING"
            and _text(fact.get("kind")) in {"RULE", "STATE_TRANSITION"}
            and not any(
                ref in current_conflict_ids
                for ref in _list(fact.get("conflict_refs"))
            )
        ):
            # No longer involved in any detected conflict: the previous
            # CONFLICTING mark was an artifact of an outdated detection
            # (e.g. action-only modality pairing). Restore promotability —
            # silently keeping the frozen mark would drop a real constraint
            # from discovery.
            fact.update(
                {
                    "status": "ACCEPTED",
                    "formal_promotion_allowed": True,
                }
            )
            fact.pop("conflict_refs", None)
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
            and _text(
                next(
                    (row.get("kind") for row in facts if _fact_id(row) == fact_id),
                    "",
                )
            )
            in {"RULE", "STATE_TRANSITION"}
        ):
            removed_rule_ids.append(_text(rule.get("rule_id")))
        else:
            retained_rules.append(dict(rule))
    asset["rule_library"] = retained_rules

    # Preserve prior technical inventory conflicts (non-Chinese schema). Chinese and
    # TERM_ALIAS rows are re-emitted above and must not be double-counted.
    prior = [
        dict(row)
        for row in _list(asset.get("cross_document_conflicts"))
        if isinstance(row, dict)
        and _text(row.get("schema")) not in AUTHORITY_ELIGIBLE_SCHEMAS
        and _text(row.get("kind")) != "TERM_ALIAS_IDENTITY_CONFLICT"
    ]
    # Deduplicate technical TERM_ALIAS if a thin legacy row somehow used no schema.
    prior = [
        row
        for row in prior
        if _text(row.get("conflict_type") or row.get("kind")).upper()
        != "TERM_ALIAS_IDENTITY_CONFLICT"
    ]
    # Keep already-normalized technical rows from _detect_cross_document_conflicts.
    prior_technical = [
        dict(row)
        for row in _list(asset.get("cross_document_conflicts"))
        if isinstance(row, dict)
        and _text(row.get("schema")) == TECHNICAL_CONFLICT_SCHEMA
        and _text(row.get("kind")) != "TERM_ALIAS_IDENTITY_CONFLICT"
    ]
    asset["cross_document_conflicts"] = [*prior, *prior_technical, *conflicts]

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
            "chinese_business_fact_conflict_count": len(
                [
                    row
                    for row in conflicts
                    if _text(row.get("schema")) == CONFLICT_SCHEMA
                ]
            ),
            "chinese_business_conflicting_fact_count": len(conflicting_ids),
            "chinese_business_conflicting_rules_removed": len(removed_rule_ids),
            "technical_cross_source_conflict_count": len(prior_technical)
            + len(
                [
                    row
                    for row in conflicts
                    if _text(row.get("schema")) == TECHNICAL_CONFLICT_SCHEMA
                ]
            ),
        }
    )
    asset["summary"] = summary

    # Durable operator ledger is the only authority that may resolve conflicts.
    from ._chinese_business_authority_decision import apply_authority_decisions_to_conflicts

    return apply_authority_decisions_to_conflicts(
        asset,
        project_id=project_id or _text(asset.get("project_id")),
        root=root,
    )


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
            original(project, resolved_root, options or {}),
            project_id=project,
            root=resolved_root,
        )
        _persist_enriched_asset(reconciled, project, resolved_root)
        return reconciled

    wrapped._qualibug_chinese_conflict_reconciliation = True  # type: ignore[attr-defined]
    wrapped._qualibug_original_builder = original  # type: ignore[attr-defined]
    _api.build_enterprise_business_knowledge_asset = wrapped
    return wrapped


__all__ = [
    "AUTHORITY_ELIGIBLE_SCHEMAS",
    "CONFLICT_SCHEMA",
    "TECHNICAL_CONFLICT_SCHEMA",
    "make_authority_eligible_conflict",
    "reconcile_chinese_business_fact_conflicts",
    "install_chinese_business_conflict_reconciliation",
]
