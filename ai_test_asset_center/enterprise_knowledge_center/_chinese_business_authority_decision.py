"""Operator authority decisions for unresolved Chinese business-fact conflicts.

Fail-closed contract:
- Never auto-pick by recency, filename, page order, confidence, or industry knowledge.
- Only an explicit operator SELECT_FACT or LEAVE_UNRESOLVED action may change
  ``authority_decision``.
- Understanding / comprehension gates stay blocked while any conflict remains
  UNRESOLVED (including an explicit LEAVE_UNRESOLVED acknowledgment).
- SELECT_FACT restores only the operator-chosen source-backed fact; losers stay
  non-promotable. Recompute reloads the durable ledger and re-applies matching
  decisions; participant-set drift fails closed back to UNRESOLVED.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ._chinese_business_conflicts import (
    AUTHORITY_ELIGIBLE_SCHEMAS,
    CONFLICT_SCHEMA,
    TECHNICAL_CONFLICT_SCHEMA,
    _fact_id,
    _span,
    _text,
    _dict,
    _list,
)
from ._common import ROOT, _load_json, _safe_project_id, _write_json
from ._utils import _now, _paths

DECISION_SCHEMA = "qualibug.operator-authority-decision.v1"
LEDGER_SCHEMA = "qualibug.operator-authority-decision-ledger.v1"
AUDIT_SCHEMA = "qualibug.operator-authority-decision-audit.v1"

ACTION_SELECT_FACT = "SELECT_FACT"
ACTION_LEAVE_UNRESOLVED = "LEAVE_UNRESOLVED"
_ALLOWED_ACTIONS = {ACTION_SELECT_FACT, ACTION_LEAVE_UNRESOLVED}

_DISALLOWED_SIGNALS = (
    "recency",
    "filename",
    "document_order",
    "model_confidence",
    "industry_default",
)


def _actor_identity(actor: Any) -> dict[str, str]:
    row = _dict(actor)
    name = _text(row.get("name") or row.get("username") or row.get("actor_id") or row.get("id"))
    role = _text(row.get("role"))
    tenant = _text(row.get("tenant_id") or row.get("tenant"))
    if not name:
        raise ValueError("authority_decision_actor_identity_required")
    return {"name": name, "role": role, "tenant_id": tenant}


def _participant_fact_ids(conflict: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for key in ("facts", "evidence"):
        for row in _list(conflict.get(key)):
            fact_id = _text(_dict(row).get("fact_id"))
            if fact_id and fact_id not in ids:
                ids.append(fact_id)
    return sorted(ids)


def _participant_fingerprint(fact_ids: list[str]) -> str:
    material = "|".join(sorted(_text(value) for value in fact_ids if _text(value)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _ledger_path(project: str, root: Path) -> Path:
    paths = _paths(project, root)
    workspace = paths["workspace"]
    return workspace / "operator_authority_decisions.json"


def _empty_ledger(project: str) -> dict[str, Any]:
    return {
        "schema": LEDGER_SCHEMA,
        "project_id": project,
        "updated_at_utc": _now(),
        "decisions": [],
        "audit_receipts": [],
    }


def load_authority_decision_ledger(
    project_id: str,
    root: Path | None = None,
) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    path = _ledger_path(project, resolved_root)
    loaded = _load_json(path, {})
    if not isinstance(loaded, dict) or _text(loaded.get("schema")) != LEDGER_SCHEMA:
        return _empty_ledger(project)
    decisions = [
        dict(row)
        for row in _list(loaded.get("decisions"))
        if isinstance(row, dict) and _text(row.get("schema")) == DECISION_SCHEMA
    ]
    receipts = [
        dict(row)
        for row in _list(loaded.get("audit_receipts"))
        if isinstance(row, dict) and _text(row.get("schema")) == AUDIT_SCHEMA
    ]
    return {
        "schema": LEDGER_SCHEMA,
        "project_id": project,
        "updated_at_utc": _text(loaded.get("updated_at_utc")) or _now(),
        "decisions": decisions,
        "audit_receipts": receipts,
    }


def save_authority_decision_ledger(
    ledger: dict[str, Any],
    project_id: str,
    root: Path | None = None,
) -> Path:
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    path = _ledger_path(project, resolved_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(ledger)
    payload["schema"] = LEDGER_SCHEMA
    payload["project_id"] = project
    payload["updated_at_utc"] = _now()
    _write_json(path, payload)
    return path


def _match_decision(
    conflict: dict[str, Any],
    decisions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    conflict_id = _text(conflict.get("conflict_id"))
    participants = _participant_fact_ids(conflict)
    fingerprint = _participant_fingerprint(participants)
    # Prefer the newest matching decision (ledger appends chronologically).
    for row in reversed(decisions):
        if not isinstance(row, dict):
            continue
        if _text(row.get("conflict_id")) != conflict_id:
            continue
        recorded_ids = sorted(
            _text(value)
            for value in _list(row.get("participant_fact_ids"))
            if _text(value)
        )
        recorded_fp = _text(row.get("participant_fingerprint")) or _participant_fingerprint(
            recorded_ids
        )
        # Participant drift fails closed: old decision must not silently bind a new pair.
        if recorded_ids and recorded_ids != participants:
            continue
        if recorded_fp and fingerprint and recorded_fp != fingerprint:
            continue
        return dict(row)
    return None


def _source_ref_for_fact(asset: dict[str, Any], fact_id: str) -> dict[str, Any]:
    ledger = _dict(asset.get("business_fact_ledger"))
    for fact in _list(ledger.get("items")):
        if not isinstance(fact, dict):
            continue
        if _fact_id(fact) != fact_id:
            continue
        span = _span(fact)
        return {
            "fact_id": fact_id,
            "source_id": _text(span.get("source_id") or fact.get("source_id")),
            "source_locator": _text(span.get("locator") or span.get("source_locator")),
            "quote_hash": _text(span.get("quote_hash")),
            "document_version": _text(
                fact.get("document_version") or span.get("document_version")
            ),
        }
    # Technical inventory participants may exist only on conflict rows.
    for conflict in _list(asset.get("cross_document_conflicts")):
        if not isinstance(conflict, dict):
            continue
        for key in ("facts", "evidence"):
            for row in _list(conflict.get(key)):
                if not isinstance(row, dict):
                    continue
                if _text(row.get("fact_id")) != fact_id:
                    continue
                return {
                    "fact_id": fact_id,
                    "source_id": _text(row.get("source_id")),
                    "source_locator": _text(
                        row.get("source_locator") or row.get("locator")
                    ),
                    "quote_hash": _text(row.get("quote_hash")),
                    "document_version": _text(row.get("document_version")),
                }
    return {"fact_id": fact_id, "source_id": "", "source_locator": "", "quote_hash": "", "document_version": ""}


def _authority_payload(
    *,
    status: str,
    decision: dict[str, Any] | None = None,
    selected_fact_id: str = "",
    authority_source_id: str = "",
    document_version: str = "",
) -> dict[str, Any]:
    row = _dict(decision)
    actor = _dict(row.get("actor"))
    return {
        "status": status,
        "selected_fact_id": selected_fact_id or _text(row.get("selected_fact_id")),
        "authority_source_id": authority_source_id or _text(row.get("authority_source_id")),
        "document_version": document_version or _text(row.get("document_version")),
        "operator_required": status == "UNRESOLVED",
        "automatic_resolution_allowed": False,
        "disallowed_authority_signals": list(_DISALLOWED_SIGNALS),
        "decision_id": _text(row.get("decision_id")),
        "action": _text(row.get("action")),
        "actor": {
            "name": _text(actor.get("name")),
            "role": _text(actor.get("role")),
            "tenant_id": _text(actor.get("tenant_id")),
        },
        "decided_at_utc": _text(row.get("decided_at_utc")),
        "rationale": _text(row.get("rationale")),
        "audit_receipt_id": _text(row.get("audit_receipt_id")),
        "selected_source_ref": _dict(row.get("selected_source_ref")),
        "explicit_leave_unresolved": _text(row.get("action")) == ACTION_LEAVE_UNRESOLVED,
    }


def _restore_rule_for_fact(asset: dict[str, Any], fact: dict[str, Any]) -> None:
    from ._chinese_business_comprehension import _rule_from_fact

    rule = _rule_from_fact(fact)
    if not rule:
        return
    rule_id = _text(rule.get("rule_id"))
    retained = [
        dict(row)
        for row in _list(asset.get("rule_library"))
        if isinstance(row, dict) and _text(row.get("rule_id")) != rule_id
    ]
    retained.append(rule)
    asset["rule_library"] = retained


def apply_authority_decisions_to_conflicts(
    asset: dict[str, Any],
    *,
    project_id: str = "",
    root: Path | None = None,
    ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Honor durable operator decisions on freshly detected conflicts.

    Design: unresolved conflicts (including explicit LEAVE_UNRESOLVED) keep the
    comprehension / understanding gates blocked. Only SELECT_FACT with an exact
    participant match may mark a conflict RESOLVED and restore the winning fact.
    """
    project = _safe_project_id(project_id or _text(_dict(asset).get("project_id")) or "real_project_demo")
    resolved_root = root or ROOT
    loaded = ledger or load_authority_decision_ledger(project, resolved_root)
    decisions = [
        dict(row)
        for row in _list(loaded.get("decisions"))
        if isinstance(row, dict)
    ]

    unresolved: list[dict[str, Any]] = []
    removed_rule_fact_ids: set[str] = set()
    restored_fact_ids: set[str] = set()

    conflicts = [
        dict(row)
        for row in _list(asset.get("cross_document_conflicts"))
        if isinstance(row, dict)
    ]
    updated: list[dict[str, Any]] = []
    for conflict in conflicts:
        schema = _text(conflict.get("schema"))
        if schema and schema not in AUTHORITY_ELIGIBLE_SCHEMAS:
            updated.append(conflict)
            continue
        # Legacy thin technical rows without schema stay visible but not selectable.
        if not schema and not _participant_fact_ids(conflict):
            updated.append(conflict)
            continue
        # An already-governed conflict carries an explicit inline operator decision
        # (a reused authority ledger entry). It must not be downgraded by a durable
        # ledger that predates or omits it; only freshly detected conflicts are
        # re-reconciled here.
        existing_decision = _dict(conflict.get("authority_decision"))
        if (
            _text(conflict.get("status")).upper()
            in {"RESOLVED", "SUPERSEDED", "DISMISSED"}
            and _text(existing_decision.get("decision_id"))
        ):
            updated.append(conflict)
            continue
        participants = _participant_fact_ids(conflict)
        decision = _match_decision(conflict, decisions)
        if not decision:
            conflict["status"] = "UNRESOLVED"
            conflict["authority_decision"] = _authority_payload(status="UNRESOLVED")
            for fact_id in participants:
                removed_rule_fact_ids.add(fact_id)
            unresolved.append(conflict)
            updated.append(conflict)
            continue

        action = _text(decision.get("action"))
        if action == ACTION_LEAVE_UNRESOLVED:
            conflict["status"] = "UNRESOLVED"
            conflict["authority_decision"] = _authority_payload(
                status="UNRESOLVED",
                decision=decision,
            )
            for fact_id in participants:
                removed_rule_fact_ids.add(fact_id)
            unresolved.append(conflict)
            updated.append(conflict)
            continue

        selected = _text(decision.get("selected_fact_id"))
        if action != ACTION_SELECT_FACT or selected not in participants:
            # Invalid or drifted decision: fail closed.
            conflict["status"] = "UNRESOLVED"
            conflict["authority_decision"] = _authority_payload(status="UNRESOLVED")
            for fact_id in participants:
                removed_rule_fact_ids.add(fact_id)
            unresolved.append(conflict)
            updated.append(conflict)
            continue

        source_ref = _dict(decision.get("selected_source_ref")) or _source_ref_for_fact(
            asset, selected
        )
        conflict["status"] = "RESOLVED"
        conflict["authority_decision"] = _authority_payload(
            status="RESOLVED",
            decision=decision,
            selected_fact_id=selected,
            authority_source_id=_text(
                source_ref.get("source_id") or decision.get("authority_source_id")
            ),
            document_version=_text(
                source_ref.get("document_version") or decision.get("document_version")
            ),
        )
        conflict["resolved_by_decision_id"] = _text(decision.get("decision_id"))
        restored_fact_ids.add(selected)
        for fact_id in participants:
            if fact_id != selected:
                removed_rule_fact_ids.add(fact_id)
        updated.append(conflict)

    asset["cross_document_conflicts"] = updated

    ledger_facts = [
        dict(row)
        for row in _list(_dict(asset.get("business_fact_ledger")).get("items"))
        if isinstance(row, dict)
    ]
    for fact in ledger_facts:
        fact_id = _fact_id(fact)
        if fact_id in restored_fact_ids:
            fact["status"] = "ACCEPTED"
            fact["formal_promotion_allowed"] = _text(fact.get("kind")) in {
                "RULE",
                "STATE_TRANSITION",
                "TERM_ALIAS",
            }
            fact["superseded_by_fact_id"] = ""
            fact["authority_resolution"] = "OPERATOR_SELECTED"
            if _text(fact.get("kind")) == "TERM_ALIAS":
                ambiguities = [
                    _text(value)
                    for value in _list(fact.get("ambiguities"))
                    if _text(value) and _text(value) != "TERM_ALIAS_IDENTITY_CONFLICT"
                ]
                fact["ambiguities"] = ambiguities
        elif fact_id in removed_rule_fact_ids:
            # Losers of a SELECT, or any side of an unresolved conflict.
            if any(
                fact_id in _participant_fact_ids(row)
                and _text(row.get("status")) == "RESOLVED"
                for row in updated
                if _text(row.get("schema")) in AUTHORITY_ELIGIBLE_SCHEMAS
                or (
                    not _text(row.get("schema"))
                    and _participant_fact_ids(row)
                )
            ):
                winner = ""
                for row in updated:
                    if (
                        (
                            _text(row.get("schema")) in AUTHORITY_ELIGIBLE_SCHEMAS
                            or _participant_fact_ids(row)
                        )
                        and _text(row.get("status")) == "RESOLVED"
                        and fact_id in _participant_fact_ids(row)
                    ):
                        winner = _text(
                            _dict(row.get("authority_decision")).get("selected_fact_id")
                        )
                        break
                fact["status"] = "SUPERSEDED"
                fact["formal_promotion_allowed"] = False
                fact["superseded_by_fact_id"] = winner
                fact["authority_resolution"] = "OPERATOR_SUPERSEDED"
            else:
                fact["status"] = "CONFLICTING"
                fact["formal_promotion_allowed"] = False
                fact["authority_resolution"] = "UNRESOLVED"

    # TERM_ALIAS SELECT must clear every same-alias sibling, not only conflict
    # participants. Extra PENDING rows with TERM_ALIAS_IDENTITY_CONFLICT would
    # otherwise reappear as builder unknowns after rebuild/apply.
    resolved_alias_winners: dict[str, str] = {}
    for row in updated:
        if (
            _text(row.get("kind")) != "TERM_ALIAS_IDENTITY_CONFLICT"
            or _text(row.get("status")) != "RESOLVED"
        ):
            continue
        alias = _text(row.get("entity"))
        selected = _text(_dict(row.get("authority_decision")).get("selected_fact_id"))
        if not alias or not selected:
            continue
        winner_fact = next(
            (item for item in ledger_facts if _fact_id(item) == selected),
            None,
        )
        winner_canonical = _text(_dict(winner_fact).get("canonical_term"))
        if winner_canonical:
            resolved_alias_winners[alias] = winner_canonical
    if resolved_alias_winners:
        for fact in ledger_facts:
            if _text(fact.get("kind")) != "TERM_ALIAS":
                continue
            alias = _text(fact.get("alias"))
            if alias not in resolved_alias_winners:
                continue
            winner_canonical = resolved_alias_winners[alias]
            winner_id = ""
            for row in updated:
                if (
                    _text(row.get("kind")) == "TERM_ALIAS_IDENTITY_CONFLICT"
                    and _text(row.get("entity")) == alias
                    and _text(row.get("status")) == "RESOLVED"
                ):
                    winner_id = _text(
                        _dict(row.get("authority_decision")).get("selected_fact_id")
                    )
                    break
            if _text(fact.get("canonical_term")) == winner_canonical:
                fact["status"] = "ACCEPTED"
                fact["formal_promotion_allowed"] = True
                fact["superseded_by_fact_id"] = ""
                fact["authority_resolution"] = "OPERATOR_SELECTED"
                ambiguities = [
                    _text(value)
                    for value in _list(fact.get("ambiguities"))
                    if _text(value) and _text(value) != "TERM_ALIAS_IDENTITY_CONFLICT"
                ]
                fact["ambiguities"] = ambiguities
            else:
                fact["status"] = "SUPERSEDED"
                fact["formal_promotion_allowed"] = False
                fact["superseded_by_fact_id"] = winner_id
                fact["authority_resolution"] = "OPERATOR_SUPERSEDED"
                ambiguities = [
                    _text(value)
                    for value in _list(fact.get("ambiguities"))
                    if _text(value) and _text(value) != "TERM_ALIAS_IDENTITY_CONFLICT"
                ]
                fact["ambiguities"] = ambiguities

    fact_ledger = _dict(asset.get("business_fact_ledger"))
    fact_ledger["items"] = ledger_facts
    fact_ledger["unresolved_conflict_count"] = len(unresolved)
    asset["business_fact_ledger"] = fact_ledger

    retained_rules: list[dict[str, Any]] = []
    for rule in _list(asset.get("rule_library")):
        if not isinstance(rule, dict):
            continue
        fact_id = _text(_dict(rule.get("semantic_contract")).get("fact_id"))
        if (
            _text(rule.get("derivation")) == "chinese_first_business_comprehension"
            and fact_id
            and fact_id in removed_rule_fact_ids
            and fact_id not in restored_fact_ids
        ):
            continue
        retained_rules.append(dict(rule))
    asset["rule_library"] = retained_rules
    for fact_id in sorted(restored_fact_ids):
        fact = next(
            (row for row in ledger_facts if _fact_id(row) == fact_id),
            None,
        )
        if isinstance(fact, dict) and _text(fact.get("kind")) in {
            "RULE",
            "STATE_TRANSITION",
        }:
            _restore_rule_for_fact(asset, fact)

    # Clear TERM_ALIAS critical unknowns when the alias conflict was SELECT_FACT resolved.
    gate = _dict(asset.get("enterprise_comprehension_gate"))
    resolved_alias_ids = {
        _text(row.get("entity"))
        for row in updated
        if isinstance(row, dict)
        and _text(row.get("kind")) == "TERM_ALIAS_IDENTITY_CONFLICT"
        and _text(row.get("status")) == "RESOLVED"
    }
    if resolved_alias_ids:
        critical = [
            dict(row)
            for row in _list(gate.get("critical_unknowns"))
            if isinstance(row, dict)
        ]
        kept_critical = []
        for row in critical:
            details = _dict(row.get("details"))
            alias = _text(details.get("alias"))
            ambiguities = {_text(value) for value in _list(row.get("ambiguities"))}
            if alias in resolved_alias_ids and "TERM_ALIAS_IDENTITY_CONFLICT" in ambiguities:
                continue
            if (
                not alias
                and ambiguities == {"TERM_ALIAS_IDENTITY_CONFLICT"}
                and resolved_alias_ids
            ):
                # Drop generic alias-conflict markers once all such conflicts resolved.
                if all(
                    _text(c.get("status")) == "RESOLVED"
                    for c in updated
                    if _text(c.get("kind")) == "TERM_ALIAS_IDENTITY_CONFLICT"
                ):
                    continue
            kept_critical.append(row)
        gate["critical_unknowns"] = kept_critical
        metrics = _dict(gate.get("metrics"))
        metrics["critical_ambiguity_count"] = len(kept_critical)
        gate["metrics"] = metrics
        if (
            not kept_critical
            and not unresolved
            and _text(gate.get("status"))
            in {
                "BLOCKED_BUSINESS_COMPREHENSION_INCOMPLETE",
                "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS",
            }
        ):
            gate["status"] = "PASS"
            gate["entry_allowed"] = True
            gate["required_operator_action"] = ""

        # Keep identity-merge receipt and understanding-model unknowns aligned with
        # the SELECT resolution so rebuild/apply does not leave stale unknown rows.
        identity_merge = _dict(asset.get("term_alias_identity_merge"))
        if identity_merge:
            remaining_conflicts = [
                dict(row)
                for row in _list(identity_merge.get("conflicts"))
                if isinstance(row, dict)
                and _text(row.get("alias")) not in resolved_alias_ids
            ]
            identity_merge["conflicts"] = remaining_conflicts
            identity_merge["conflict_count"] = len(remaining_conflicts)
            asset["term_alias_identity_merge"] = identity_merge

        model = _dict(asset.get("enterprise_understanding_model"))
        if model:
            unknowns = [
                dict(row)
                for row in _list(model.get("unknowns"))
                if isinstance(row, dict)
            ]
            kept_unknowns = []
            for row in unknowns:
                reason = _text(row.get("reason_code") or row.get("kind"))
                details = _dict(row.get("details"))
                alias = _text(details.get("alias"))
                if reason == "TERM_ALIAS_IDENTITY_CONFLICT" and (
                    alias in resolved_alias_ids or (not alias and resolved_alias_ids)
                ):
                    continue
                kept_unknowns.append(row)
            model["unknowns"] = kept_unknowns
            model_gate = _dict(model.get("gate"))
            if model_gate:
                model_critical = [
                    dict(row)
                    for row in _list(model_gate.get("critical_unknowns"))
                    if isinstance(row, dict)
                ]
                model_gate["critical_unknowns"] = [
                    row
                    for row in model_critical
                    if not (
                        _text(row.get("reason_code") or row.get("kind"))
                        == "TERM_ALIAS_IDENTITY_CONFLICT"
                        and (
                            _text(_dict(row.get("details")).get("alias"))
                            in resolved_alias_ids
                            or (
                                not _text(_dict(row.get("details")).get("alias"))
                                and resolved_alias_ids
                            )
                        )
                    )
                ]
                model["gate"] = model_gate
            asset["enterprise_understanding_model"] = model

    if unresolved:
        gate.update(
            {
                "status": "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS",
                "entry_allowed": False,
                "unresolved_business_fact_conflicts": unresolved,
                "required_operator_action": (
                    "resolve source authority/version for each conflicting fact via "
                    "explicit operator authority decision; do not choose by recency, "
                    "filename order or model confidence"
                ),
            }
        )
    else:
        # Clear only conflict fields. Do not invent PASS when another stage blocked the gate.
        gate["unresolved_business_fact_conflicts"] = []
        gate["removed_conflicting_rule_ids"] = []
        if _text(gate.get("status")) == "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS":
            # Only auto-clear to PASS when critical unknowns are also empty.
            if not _list(gate.get("critical_unknowns")):
                gate["status"] = "PASS"
                gate["entry_allowed"] = True
                gate["required_operator_action"] = ""
    asset["enterprise_comprehension_gate"] = gate

    gaps = [
        dict(row)
        for row in _list(asset.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("kind")) != "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS"
    ]
    if unresolved:
        gaps.append(
            {
                "kind": "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS",
                "gap_type": "unresolved_chinese_business_fact_conflict",
                "source_id": "*",
                "conflict_ids": [row.get("conflict_id") for row in unresolved],
                "operator_action": gate.get("required_operator_action"),
            }
        )
    asset["coverage_gaps"] = gaps

    summary = _dict(asset.get("summary"))
    summary.update(
        {
            "rule_count": len(_list(asset.get("rule_library"))),
            "chinese_business_fact_conflict_count": len(
                [
                    row
                    for row in updated
                    if isinstance(row, dict) and _text(row.get("schema")) == CONFLICT_SCHEMA
                ]
            ),
            "technical_cross_source_conflict_count": len(
                [
                    row
                    for row in updated
                    if isinstance(row, dict)
                    and _text(row.get("schema")) == TECHNICAL_CONFLICT_SCHEMA
                ]
            ),
            "chinese_business_unresolved_conflict_count": len(unresolved),
            "chinese_business_operator_authority_decision_count": len(decisions),
        }
    )
    asset["summary"] = summary
    asset["operator_authority_decision_ledger"] = {
        "schema": LEDGER_SCHEMA,
        "project_id": project,
        "decision_count": len(decisions),
        "audit_receipt_count": len(_list(loaded.get("audit_receipts"))),
        "updated_at_utc": _text(loaded.get("updated_at_utc")),
    }
    return asset


def _find_conflict(asset: dict[str, Any], conflict_id: str) -> dict[str, Any]:
    for row in _list(asset.get("cross_document_conflicts")):
        if isinstance(row, dict) and _text(row.get("conflict_id")) == conflict_id:
            return dict(row)
    gate = _dict(asset.get("enterprise_comprehension_gate"))
    for row in _list(gate.get("unresolved_business_fact_conflicts")):
        if isinstance(row, dict) and _text(row.get("conflict_id")) == conflict_id:
            return dict(row)
    model = _dict(asset.get("enterprise_understanding_model"))
    for row in _list(model.get("conflicts")):
        if isinstance(row, dict) and _text(row.get("conflict_id")) == conflict_id:
            return dict(row)
    for row in _list(_dict(model.get("gate")).get("unresolved_conflicts")):
        if isinstance(row, dict) and _text(row.get("conflict_id")) == conflict_id:
            return dict(row)
    raise KeyError("authority_decision_conflict_not_found")


def record_operator_authority_decision(
    project_id: str,
    *,
    conflict_id: str,
    action: str,
    actor: Any,
    root: Path | None = None,
    selected_fact_id: str = "",
    rationale: str = "",
    document_version: str = "",
    rebuild: bool = True,
) -> dict[str, Any]:
    """Record an explicit operator authority decision and refresh understanding."""
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    action_code = _text(action).upper()
    if action_code not in _ALLOWED_ACTIONS:
        raise ValueError("authority_decision_action_invalid")
    actor_row = _actor_identity(actor)
    rationale_text = _text(rationale)[:2000]
    selected = _text(selected_fact_id)
    if action_code == ACTION_SELECT_FACT and not selected:
        raise ValueError("authority_decision_selected_fact_id_required")
    if action_code == ACTION_LEAVE_UNRESOLVED and selected:
        raise ValueError("authority_decision_leave_unresolved_forbids_selected_fact")

    from . import _api

    asset = _api.load_enterprise_business_knowledge_asset(project, resolved_root)
    if not isinstance(asset, dict):
        raise KeyError("authority_decision_knowledge_asset_missing")
    conflict = _find_conflict(asset, _text(conflict_id))
    if _text(conflict.get("schema")) and _text(conflict.get("schema")) != CONFLICT_SCHEMA:
        # Still allow when product receipts omitted schema but keep fail-closed ids.
        pass
    participants = _participant_fact_ids(conflict)
    if not participants:
        raise ValueError("authority_decision_conflict_participants_missing")
    if action_code == ACTION_SELECT_FACT and selected not in participants:
        raise ValueError("authority_decision_selected_fact_not_in_conflict")

    source_ref = _source_ref_for_fact(asset, selected) if selected else {}
    decided_at = _now()
    decision_material = "|".join(
        [
            project,
            _text(conflict_id),
            action_code,
            selected,
            decided_at,
            actor_row["name"],
        ]
    )
    decision_id = "decision:" + hashlib.sha256(decision_material.encode("utf-8")).hexdigest()[:20]
    audit_receipt_id = "audit:" + hashlib.sha256(
        f"{decision_id}|{decided_at}".encode("utf-8")
    ).hexdigest()[:20]

    decision = {
        "schema": DECISION_SCHEMA,
        "decision_id": decision_id,
        "conflict_id": _text(conflict_id),
        "action": action_code,
        "status": "RESOLVED" if action_code == ACTION_SELECT_FACT else "UNRESOLVED",
        "selected_fact_id": selected if action_code == ACTION_SELECT_FACT else "",
        "authority_source_id": _text(source_ref.get("source_id")),
        "document_version": _text(document_version) or _text(source_ref.get("document_version")),
        "selected_source_ref": source_ref if action_code == ACTION_SELECT_FACT else {},
        "participant_fact_ids": participants,
        "participant_fingerprint": _participant_fingerprint(participants),
        "actor": actor_row,
        "decided_at_utc": decided_at,
        "rationale": rationale_text,
        "audit_receipt_id": audit_receipt_id,
        "automatic_resolution_allowed": False,
        "disallowed_authority_signals": list(_DISALLOWED_SIGNALS),
    }
    audit_receipt = {
        "schema": AUDIT_SCHEMA,
        "audit_receipt_id": audit_receipt_id,
        "decision_id": decision_id,
        "conflict_id": _text(conflict_id),
        "action": action_code,
        "selected_fact_id": decision["selected_fact_id"],
        "actor": actor_row,
        "decided_at_utc": decided_at,
        "rationale": rationale_text,
        "project_id": project,
    }

    ledger = load_authority_decision_ledger(project, resolved_root)
    ledger["decisions"] = [
        *(
            row
            for row in _list(ledger.get("decisions"))
            if isinstance(row, dict)
        ),
        decision,
    ]
    ledger["audit_receipts"] = [
        *(
            row
            for row in _list(ledger.get("audit_receipts"))
            if isinstance(row, dict)
        ),
        audit_receipt,
    ]
    save_authority_decision_ledger(ledger, project, resolved_root)

    refreshed: dict[str, Any]
    if rebuild:
        refreshed = _api.build_enterprise_business_knowledge_asset(project, resolved_root)
    else:
        from ._chinese_business_conflicts import reconcile_chinese_business_fact_conflicts

        refreshed = reconcile_chinese_business_fact_conflicts(
            asset,
            project_id=project,
            root=resolved_root,
        )
        from ._chinese_business_comprehension import _persist_enriched_asset

        _persist_enriched_asset(refreshed, project, resolved_root)

    resolved_conflict = None
    for row in _list(refreshed.get("cross_document_conflicts")):
        if isinstance(row, dict) and _text(row.get("conflict_id")) == _text(conflict_id):
            resolved_conflict = row
            break

    return {
        "ok": True,
        "schema": DECISION_SCHEMA,
        "decision": decision,
        "audit_receipt": audit_receipt,
        "conflict": resolved_conflict,
        "comprehension_gate": _dict(refreshed.get("enterprise_comprehension_gate")),
        "understanding_gate": _dict(
            _dict(refreshed.get("enterprise_understanding_model")).get("gate")
        ),
        "ledger": {
            "schema": LEDGER_SCHEMA,
            "project_id": project,
            "decision_count": len(_list(ledger.get("decisions"))),
            "audit_receipt_count": len(_list(ledger.get("audit_receipts"))),
        },
    }


def list_operator_authority_decisions(
    project_id: str,
    root: Path | None = None,
) -> dict[str, Any]:
    ledger = load_authority_decision_ledger(project_id, root)
    return {
        "ok": True,
        "schema": LEDGER_SCHEMA,
        "project_id": _safe_project_id(project_id),
        "decisions": _list(ledger.get("decisions")),
        "audit_receipts": _list(ledger.get("audit_receipts")),
        "updated_at_utc": _text(ledger.get("updated_at_utc")),
    }


__all__ = [
    "ACTION_LEAVE_UNRESOLVED",
    "ACTION_SELECT_FACT",
    "AUDIT_SCHEMA",
    "DECISION_SCHEMA",
    "LEDGER_SCHEMA",
    "apply_authority_decisions_to_conflicts",
    "list_operator_authority_decisions",
    "load_authority_decision_ledger",
    "record_operator_authority_decision",
    "save_authority_decision_ledger",
]
