"""Conservative conflicts for typed atomic business-fact slots."""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .._chinese_business_authority_decision import apply_authority_decisions_to_conflicts
from .._chinese_business_conflicts import (
    TECHNICAL_CONFLICT_SCHEMA,
    make_authority_eligible_conflict,
)

RECEIPT_SCHEMA = "qualibug.typed-business-fact-conflicts.v1"
_DERIVATION = "typed_business_fact_conflict"
_BLOCKED_STATUS = "BLOCKED_TYPED_BUSINESS_FACT_CONFLICTS"
_ACTIVE_STATUSES = frozenset({"ACCEPTED", "CONFLICTING", "SUPERSEDED"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _text(value).lower())


def _subject(fact: dict[str, Any]) -> tuple[Any, ...]:
    subject = _dict(fact.get("subject"))
    obj = _dict(fact.get("object"))
    actors = tuple(sorted(_norm(v) for v in _list(subject.get("actor_refs")) if _norm(v)))
    entities = tuple(
        sorted(
            {
                _norm(v)
                for v in [*_list(subject.get("entity_refs")), *_list(obj.get("entity_refs"))]
                if _norm(v)
            }
        )
    )
    return actors, entities


def _predicate(fact: dict[str, Any]) -> str:
    action = _dict(fact.get("action"))
    return _norm(fact.get("predicate") or action.get("canonical") or action.get("raw"))


def _frame(fact: dict[str, Any]) -> dict[str, Any]:
    frame = _dict(fact.get("condition_frame"))
    return {
        "conditions": tuple(
            sorted(
                {
                    _norm(v)
                    for v in (_list(frame.get("conditions")) or _list(fact.get("conditions")))
                    if _norm(v)
                }
            )
        ),
        "combinator": _text(frame.get("combinator") or fact.get("condition_combinator")).upper(),
        "branch": _text(frame.get("branch")).upper(),
        "exceptions": tuple(
            sorted(
                {
                    _norm(v)
                    for v in [*_list(frame.get("exception_scopes")), *_list(fact.get("exception_scope"))]
                    if _norm(v)
                }
            )
        ),
    }


def _coordinate(fact: dict[str, Any]) -> tuple[Any, ...]:
    frame = _frame(fact)
    scope = tuple(
        sorted(
            (_text(k), _norm(v))
            for k, v in _dict(fact.get("scope")).items()
            if _norm(v)
        )
    )
    return (
        _text(fact.get("fact_type") or fact.get("kind")).upper(),
        *_subject(fact),
        _predicate(fact),
        frame["conditions"],
        frame["branch"],
        frame["exceptions"],
        scope,
    )


def _claim_values(fact: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [
        dict(_dict(row.get("value")))
        for row in _list(fact.get("claims"))
        if isinstance(row, dict)
        and _text(row.get("claim_type")).upper() == kind
        and _dict(row.get("value"))
    ]


def _make(kind: str, left: dict[str, Any], right: dict[str, Any], reason: str) -> dict[str, Any]:
    row = make_authority_eligible_conflict(
        kind,
        [left, right],
        reason,
        schema=TECHNICAL_CONFLICT_SCHEMA,
    )
    row["derivation"] = _DERIVATION
    row["typed_fact_slots"] = True
    return row


def _condition_conflicts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        frame = _frame(fact)
        if len(frame["conditions"]) >= 2:
            groups[_coordinate(fact)].append(fact)
    rows: list[dict[str, Any]] = []
    for group in groups.values():
        for index, left in enumerate(group):
            for right in group[index + 1 :]:
                logic = {_frame(left)["combinator"], _frame(right)["combinator"]}
                if logic == {"AND", "OR"}:
                    rows.append(
                        _make(
                            "CONDITION_LOGIC_CONTRADICTION",
                            left,
                            right,
                            "same typed rule coordinates declare incompatible AND/OR condition logic",
                        )
                    )
    return rows


def _value_conflicts(facts: list[dict[str, Any]], kind: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[tuple[dict[str, Any], str]]] = defaultdict(list)
    for fact in facts:
        values = _claim_values(fact, kind)
        if kind == "FORMULA_CONSTRAINT":
            values.extend(
                dict(row)
                for row in _list(fact.get("formula_constraints"))
                if isinstance(row, dict)
            )
        for value in values:
            identity = tuple(_norm(value.get(field)) for field in fields[:-1])
            outcome = _norm(value.get(fields[-1]))
            identity_required = kind == "FORMULA_CONSTRAINT"
            if outcome and (not identity_required or any(identity)):
                groups[(_subject(fact), identity)].append((fact, outcome))
    rows: list[dict[str, Any]] = []
    conflict_kind = (
        "FORMULA_CONTRADICTION" if kind == "FORMULA_CONSTRAINT" else "CARDINALITY_CONTRADICTION"
    )
    for group in groups.values():
        for index, (left, left_value) in enumerate(group):
            for right, right_value in group[index + 1 :]:
                if left_value != right_value:
                    rows.append(
                        _make(
                            conflict_kind,
                            left,
                            right,
                            f"same typed coordinates declare different {kind.lower()} values",
                        )
                    )
    return rows


def _typed_conflicts(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(asset.get("cross_document_conflicts"))
        if isinstance(row, dict) and _text(row.get("derivation")) == _DERIVATION
    ]


def _finalize_after_authority(asset: dict[str, Any]) -> dict[str, Any]:
    typed = _typed_conflicts(asset)
    unresolved = [row for row in typed if _text(row.get("status")) != "RESOLVED"]
    receipt = _dict(asset.get("typed_business_fact_conflict_receipt"))
    receipt.update(
        {
            "status": "BLOCKED" if unresolved else "PASS",
            "unresolved_conflict_count": len(unresolved),
            "resolved_conflict_count": len(typed) - len(unresolved),
        }
    )
    asset["typed_business_fact_conflict_receipt"] = receipt

    gaps = [
        dict(row)
        for row in _list(asset.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("kind")) != _BLOCKED_STATUS
    ]
    gate = _dict(asset.get("enterprise_comprehension_gate"))
    if unresolved:
        gaps.append(
            {
                "kind": _BLOCKED_STATUS,
                "gap_type": "typed_business_fact_conflict",
                "source_id": "*",
                "conflict_ids": [_text(row.get("conflict_id")) for row in unresolved],
                "operator_action": "resolve typed fact source authority explicitly",
            }
        )
    elif _text(gate.get("status")) == _BLOCKED_STATUS:
        other_unresolved = [
            row
            for row in _list(asset.get("cross_document_conflicts"))
            if isinstance(row, dict)
            and _text(row.get("derivation")) != _DERIVATION
            and _text(row.get("status")) != "RESOLVED"
        ]
        upstream_allowed = bool(gate.get("entry_allowed_before_typed_conflicts", True))
        if not other_unresolved and upstream_allowed:
            gate["status"] = _text(gate.get("status_before_typed_conflicts")) or "PASS"
            gate["entry_allowed"] = True
            gate["required_operator_action"] = ""
    asset["coverage_gaps"] = gaps
    asset["enterprise_comprehension_gate"] = gate
    return asset


def reconcile_typed_fact_conflicts(
    asset: dict[str, Any],
    *,
    project_id: str,
    root: Path,
) -> dict[str, Any]:
    ledger = _dict(asset.get("business_fact_ledger"))
    all_facts = [
        dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)
    ]
    active_facts = [
        fact
        for fact in all_facts
        if _text(fact.get("fact_id"))
        and _text(fact.get("status")) in _ACTIVE_STATUSES
    ]
    statusless_fact_count = sum(
        1 for fact in all_facts if _text(fact.get("fact_id")) and not _text(fact.get("status"))
    )
    conflicts = [
        *_condition_conflicts(active_facts),
        *_value_conflicts(active_facts, "FORMULA_CONSTRAINT", ("lhs", "rhs")),
        *_value_conflicts(active_facts, "CARDINALITY_CONSTRAINT", ("maximum",)),
    ]
    conflicts = list({_text(row.get("conflict_id")): row for row in conflicts}.values())
    ids = {
        _text(participant.get("fact_id"))
        for conflict in conflicts
        for participant in _list(conflict.get("facts"))
        if isinstance(participant, dict)
    }
    for fact in all_facts:
        if _text(fact.get("fact_id")) in ids:
            fact["status"] = "CONFLICTING"
            fact["formal_promotion_allowed"] = False
    ledger["items"] = all_facts
    ledger["typed_unresolved_conflict_count"] = len(conflicts)
    ledger["typed_conflict_reconciliation_preserved_all_facts"] = True
    asset["business_fact_ledger"] = ledger
    prior = [
        dict(row)
        for row in _list(asset.get("cross_document_conflicts"))
        if isinstance(row, dict) and _text(row.get("derivation")) != _DERIVATION
    ]
    asset["cross_document_conflicts"] = [*prior, *conflicts]
    asset["typed_business_fact_conflict_receipt"] = {
        "schema": RECEIPT_SCHEMA,
        "status": "BLOCKED" if conflicts else "PASS",
        "fact_count_before_reconciliation": len(all_facts),
        "active_fact_count": len(active_facts),
        "statusless_fact_count": statusless_fact_count,
        "statusless_fact_defaulted_to_accepted": False,
        "fact_count_after_reconciliation": len(all_facts),
        "all_fact_statuses_preserved": True,
        "conflict_count": len(conflicts),
        "conflicting_fact_count": len(ids),
        "automatic_resolution_allowed": False,
        "condition_ast_compared": True,
        "exception_and_scope_coordinates_compared": True,
        "formula_and_cardinality_compared": True,
    }
    if conflicts:
        gate = _dict(asset.get("enterprise_comprehension_gate"))
        gate["status_before_typed_conflicts"] = _text(gate.get("status")) or "PASS"
        gate["entry_allowed_before_typed_conflicts"] = bool(
            gate.get("entry_allowed", True)
        )
        gate["status"] = _BLOCKED_STATUS
        gate["entry_allowed"] = False
        gate["required_operator_action"] = "resolve typed fact source authority explicitly"
        asset["enterprise_comprehension_gate"] = gate
    governed = apply_authority_decisions_to_conflicts(
        asset,
        project_id=project_id,
        root=root,
    )
    return _finalize_after_authority(governed)


__all__ = ["RECEIPT_SCHEMA", "reconcile_typed_fact_conflicts"]
