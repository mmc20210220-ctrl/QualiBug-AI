"""Append-only authority decisions for implicit-rule counterexamples.

Raw runtime evidence is never an authority transition.  A rule may be confirmed,
marked stale, rejected, or superseded only by an explicit source/operator governance
decision that references immutable evidence.  The decision ledger is stored inside the
existing enterprise knowledge asset so normal asset persistence remains the sole store.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA_VERSION = "qualibug.implicit-rule-authority-decisions.v1"
_ALLOWED_AUTHORITIES = frozenset(
    {
        "operator_approved",
        "governance_approved",
        "source_contract",
        "runtime_contract_approved",
    }
)
_ALLOWED_DECISIONS = frozenset(
    {"CONFIRM_ACTIVE", "MARK_STALE", "REJECT", "SUPERSEDE"}
)
_ALLOWED_CLASSIFICATIONS = frozenset(
    {"", "TARGET_BUG", "RULE_COUNTEREXAMPLE", "EVIDENCE_INVALID", "SOURCE_DRIFT"}
)
_TRANSITION_DECISIONS = frozenset({"MARK_STALE", "REJECT", "SUPERSEDE"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "\x1f".join(_canonical(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def _decision_id(row: dict[str, Any]) -> str:
    supplied = _text(row.get("decision_id"))
    if supplied:
        return supplied
    return _stable_id(
        "implicit_rule_decision",
        row.get("rule_ref"),
        row.get("decision_type"),
        row.get("counterexample_classification"),
        row.get("authority_type"),
        row.get("decided_by"),
        row.get("reason"),
        row.get("evidence_refs"),
        row.get("runtime_evidence_refs"),
    )


def _runtime_evidence_ids(asset: dict[str, Any]) -> tuple[set[str], bool]:
    ids: set[str] = set()
    found_receipt = False
    for field in (
        "implicit_rule_runtime_evolution",
        "latest_implicit_rule_runtime_evolution",
    ):
        root = _dict(asset.get(field))
        if not root:
            continue
        found_receipt = True
        receipt_id = _text(root.get("receipt_id"))
        if receipt_id:
            ids.add(receipt_id)
        for rule in _list(root.get("rules")):
            if not isinstance(rule, dict):
                continue
            for evidence in _list(rule.get("evidence")):
                if not isinstance(evidence, dict):
                    continue
                evidence_id = _text(evidence.get("evidence_id"))
                if evidence_id:
                    ids.add(evidence_id)
    return ids, found_receipt


def _known_rule_ids(
    active_rules: list[dict[str, Any]], lifecycle: dict[str, Any]
) -> set[str]:
    return {
        _text(row.get("rule_id"))
        for row in [*active_rules, *_list(lifecycle.get("items"))]
        if isinstance(row, dict) and _text(row.get("rule_id"))
    }


def _normalize_decision(row: dict[str, Any]) -> dict[str, Any]:
    decision = dict(row)
    decision["decision_id"] = _decision_id(decision)
    decision["rule_ref"] = _text(decision.get("rule_ref") or decision.get("rule_id"))
    decision["decision_type"] = _text(decision.get("decision_type")).upper()
    decision["counterexample_classification"] = _text(
        decision.get("counterexample_classification")
    ).upper()
    decision["authority_type"] = _text(decision.get("authority_type")).lower()
    decision["reason"] = _text(decision.get("reason"))
    decision["evidence_refs"] = sorted(
        {_text(value) for value in _list(decision.get("evidence_refs")) if _text(value)}
    )
    decision["runtime_evidence_refs"] = sorted(
        {
            _text(value)
            for value in _list(decision.get("runtime_evidence_refs"))
            if _text(value)
        }
    )
    decision["replacement_rule_ref"] = _text(
        decision.get("replacement_rule_ref")
    )
    decision["decided_by"] = dict(_dict(decision.get("decided_by")))
    return decision


def _validate_decision(
    decision: dict[str, Any],
    *,
    known_rule_ids: set[str],
    active_rule_ids: set[str],
    known_runtime_evidence_ids: set[str],
    runtime_receipt_present: bool,
) -> dict[str, Any]:
    rule_ref = _text(decision.get("rule_ref"))
    decision_type = _text(decision.get("decision_type")).upper()
    classification = _text(decision.get("counterexample_classification")).upper()
    authority_type = _text(decision.get("authority_type")).lower()
    actor = _dict(decision.get("decided_by"))
    actor_name = _text(actor.get("name") or actor.get("actor"))
    actor_role = _text(actor.get("role"))
    evidence_refs = list(decision.get("evidence_refs") or [])
    runtime_refs = list(decision.get("runtime_evidence_refs") or [])

    if not rule_ref or rule_ref not in known_rule_ids:
        return {"status": "REJECTED", "reason_code": "RULE_REFERENCE_UNKNOWN"}
    if decision_type not in _ALLOWED_DECISIONS:
        return {"status": "REJECTED", "reason_code": "DECISION_TYPE_INVALID"}
    if classification not in _ALLOWED_CLASSIFICATIONS:
        return {
            "status": "REJECTED",
            "reason_code": "COUNTEREXAMPLE_CLASSIFICATION_INVALID",
        }
    if authority_type not in _ALLOWED_AUTHORITIES:
        return {
            "status": "REJECTED",
            "reason_code": "DECISION_AUTHORITY_NOT_APPROVED",
        }
    if not actor_name or not actor_role:
        return {"status": "REJECTED", "reason_code": "DECISION_ACTOR_MISSING"}
    if not _text(decision.get("reason")):
        return {"status": "REJECTED", "reason_code": "DECISION_REASON_MISSING"}
    if not evidence_refs:
        return {"status": "REJECTED", "reason_code": "DECISION_EVIDENCE_MISSING"}

    if classification == "RULE_COUNTEREXAMPLE":
        if decision_type not in _TRANSITION_DECISIONS:
            return {
                "status": "REJECTED",
                "reason_code": "RULE_COUNTEREXAMPLE_REQUIRES_AUTHORITY_TRANSITION",
            }
        if not runtime_refs:
            return {
                "status": "REJECTED",
                "reason_code": "RULE_COUNTEREXAMPLE_RUNTIME_EVIDENCE_MISSING",
            }
    if classification in {"TARGET_BUG", "EVIDENCE_INVALID"}:
        if decision_type != "CONFIRM_ACTIVE":
            return {
                "status": "REJECTED",
                "reason_code": "NON_RULE_COUNTEREXAMPLE_CANNOT_DEMOTE_RULE",
            }
        if rule_ref not in active_rule_ids:
            return {
                "status": "REJECTED",
                "reason_code": "NON_RULE_COUNTEREXAMPLE_CANNOT_REACTIVATE_RULE",
            }
        if not runtime_refs:
            return {
                "status": "REJECTED",
                "reason_code": "RUNTIME_CLASSIFICATION_EVIDENCE_MISSING",
            }
    if classification == "SOURCE_DRIFT" and decision_type not in _TRANSITION_DECISIONS:
        return {
            "status": "REJECTED",
            "reason_code": "SOURCE_DRIFT_REQUIRES_AUTHORITY_TRANSITION",
        }
    if decision_type == "SUPERSEDE":
        replacement = _text(decision.get("replacement_rule_ref"))
        if not replacement or replacement == rule_ref or replacement not in active_rule_ids:
            return {
                "status": "REJECTED",
                "reason_code": "SUPERSEDE_REPLACEMENT_RULE_INVALID",
            }

    if runtime_refs:
        if not runtime_receipt_present:
            return {
                "status": "PENDING_EVIDENCE",
                "reason_code": "RUNTIME_EVIDENCE_RECEIPT_NOT_IMPORTED",
            }
        missing = sorted(set(runtime_refs) - known_runtime_evidence_ids)
        if missing:
            return {
                "status": "PENDING_EVIDENCE",
                "reason_code": "RUNTIME_EVIDENCE_REFERENCE_UNKNOWN",
                "missing_runtime_evidence_refs": missing,
            }

    return {
        "status": "APPLIED",
        "reason_code": "AUTHORITY_DECISION_VALIDATED",
        "authority_transition_allowed": True,
    }


def _lifecycle_event(
    *,
    rule_ref: str,
    from_status: str,
    to_status: str,
    reason: str,
    decision_id: str,
    source_version_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    event_id = _stable_id(
        "implicit_rule_lifecycle_event",
        rule_ref,
        from_status,
        to_status,
        reason,
        source_version_refs,
        decision_id,
    )
    return {
        "event_id": event_id,
        "rule_id": rule_ref,
        "from_status": from_status,
        "to_status": to_status,
        "reason": reason,
        "previous_source_versions": source_version_refs,
        "current_source_versions": source_version_refs,
        "authority_decision_ref": decision_id,
    }


def _recount_lifecycle(lifecycle: dict[str, Any]) -> None:
    items = [row for row in _list(lifecycle.get("items")) if isinstance(row, dict)]
    for status, field in (
        ("ACTIVE", "active_rule_count"),
        ("STALE", "stale_rule_count"),
        ("REJECTED", "rejected_rule_count"),
        ("SUPERSEDED", "superseded_rule_count"),
    ):
        lifecycle[field] = sum(
            1 for row in items if _text(row.get("status")).upper() == status
        )


def apply_implicit_rule_authority_decisions(
    asset: dict[str, Any],
    *,
    active_rules: list[dict[str, Any]],
    lifecycle: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Apply persisted, validated decisions and return active rules/lifecycle/ledger."""

    active_by_id = {
        _text(row.get("rule_id")): dict(row)
        for row in active_rules
        if isinstance(row, dict) and _text(row.get("rule_id"))
    }
    lifecycle = dict(lifecycle)
    lifecycle_items = {
        _text(row.get("rule_id")): dict(row)
        for row in _list(lifecycle.get("items"))
        if isinstance(row, dict) and _text(row.get("rule_id"))
    }
    lifecycle_events = [
        dict(row)
        for row in _list(lifecycle.get("events"))
        if isinstance(row, dict) and _text(row.get("event_id"))
    ]
    lifecycle_event_ids = {_text(row.get("event_id")) for row in lifecycle_events}

    existing_ledger = _dict(asset.get("implicit_rule_authority_decision_ledger"))
    ledger_items = [
        dict(row)
        for row in _list(existing_ledger.get("items"))
        if isinstance(row, dict) and _text(row.get("decision_id"))
    ]
    by_decision_id = {_text(row.get("decision_id")): row for row in ledger_items}
    conflicts = [
        dict(row)
        for row in _list(existing_ledger.get("conflicts"))
        if isinstance(row, dict)
    ]

    known_ids = _known_rule_ids(list(active_by_id.values()), lifecycle)
    runtime_ids, runtime_receipt_present = _runtime_evidence_ids(asset)
    for raw in _list(asset.get("implicit_rule_authority_decisions")):
        if not isinstance(raw, dict):
            continue
        decision = _normalize_decision(raw)
        decision_id = decision["decision_id"]
        existing = by_decision_id.get(decision_id)
        if existing is not None:
            existing_payload = {
                key: value
                for key, value in existing.items()
                if key not in {"status", "reason_code", "authority_transition_allowed"}
            }
            if _canonical(existing_payload) != _canonical(decision):
                conflict = {
                    "decision_id": decision_id,
                    "reason_code": "DECISION_ID_CONTENT_MISMATCH",
                }
                if conflict not in conflicts:
                    conflicts.append(conflict)
            continue
        validation = _validate_decision(
            decision,
            known_rule_ids=known_ids,
            active_rule_ids=set(active_by_id),
            known_runtime_evidence_ids=runtime_ids,
            runtime_receipt_present=runtime_receipt_present,
        )
        ledger_row = {**decision, **validation}
        ledger_items.append(ledger_row)
        by_decision_id[decision_id] = ledger_row

    for decision in ledger_items:
        if _text(decision.get("status")).upper() != "APPLIED":
            continue
        rule_ref = _text(decision.get("rule_ref"))
        decision_type = _text(decision.get("decision_type")).upper()
        classification = _text(decision.get("counterexample_classification")).upper()
        decision_id = _text(decision.get("decision_id"))
        item = lifecycle_items.get(rule_ref, {})
        from_status = _text(item.get("status")).upper() or (
            "ACTIVE" if rule_ref in active_by_id else "ABSENT"
        )
        rule_snapshot = active_by_id.get(rule_ref) or _dict(item.get("rule_snapshot"))
        source_refs = [
            dict(row)
            for row in _list(item.get("source_version_refs"))
            if isinstance(row, dict)
        ]

        if decision_type == "CONFIRM_ACTIVE":
            if classification in {"TARGET_BUG", "EVIDENCE_INVALID"} and rule_ref not in active_by_id:
                continue
            if rule_snapshot:
                rule = dict(rule_snapshot)
                rule["status"] = "accepted"
                rule["authority_decision_ref"] = decision_id
                semantic = dict(_dict(rule.get("semantic_contract")))
                semantic["latest_authority_decision_ref"] = decision_id
                semantic["counterexample_classification"] = classification
                rule["semantic_contract"] = semantic
                active_by_id[rule_ref] = rule
            to_status = "ACTIVE"
            reason = (
                "RUNTIME_EVIDENCE_CLASSIFIED_AS_TARGET_BUG"
                if classification == "TARGET_BUG"
                else "RUNTIME_EVIDENCE_CLASSIFIED_INVALID"
                if classification == "EVIDENCE_INVALID"
                else "AUTHORITY_DECISION_CONFIRMED_ACTIVE"
            )
            execution_allowed = True
        elif decision_type == "MARK_STALE":
            active_by_id.pop(rule_ref, None)
            to_status = "STALE"
            reason = "AUTHORITY_DECISION_MARKED_RULE_STALE"
            execution_allowed = False
        elif decision_type == "REJECT":
            active_by_id.pop(rule_ref, None)
            to_status = "REJECTED"
            reason = "AUTHORITY_DECISION_REJECTED_RULE"
            execution_allowed = False
        else:
            active_by_id.pop(rule_ref, None)
            to_status = "SUPERSEDED"
            reason = "AUTHORITY_DECISION_SUPERSEDED_RULE"
            execution_allowed = False

        event = _lifecycle_event(
            rule_ref=rule_ref,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            decision_id=decision_id,
            source_version_refs=source_refs,
        )
        if event["event_id"] not in lifecycle_event_ids:
            lifecycle_events.append(event)
            lifecycle_event_ids.add(event["event_id"])
        lifecycle_items[rule_ref] = {
            **item,
            "rule_id": rule_ref,
            "status": to_status,
            "execution_allowed": execution_allowed,
            "reason": reason,
            "rule_snapshot": rule_snapshot,
            "authority_decision_ref": decision_id,
            "counterexample_classification": classification,
            "last_event_id": event["event_id"],
        }
        decision["authority_transition"] = f"{from_status}_TO_{to_status}"

    lifecycle["items"] = [lifecycle_items[key] for key in sorted(lifecycle_items)]
    lifecycle["events"] = lifecycle_events
    _recount_lifecycle(lifecycle)

    applied = sum(1 for row in ledger_items if _text(row.get("status")).upper() == "APPLIED")
    pending = sum(
        1 for row in ledger_items if _text(row.get("status")).upper() == "PENDING_EVIDENCE"
    )
    rejected = sum(
        1 for row in ledger_items if _text(row.get("status")).upper() == "REJECTED"
    )
    ledger = {
        "schema_version": SCHEMA_VERSION,
        "items": ledger_items,
        "conflicts": conflicts,
        "applied_count": applied,
        "pending_evidence_count": pending,
        "rejected_count": rejected,
        "raw_runtime_observation_can_mutate_authority": False,
        "append_only": True,
        "persisted_in_existing_knowledge_asset": True,
    }
    return [active_by_id[key] for key in sorted(active_by_id)], lifecycle, ledger


__all__ = [
    "SCHEMA_VERSION",
    "apply_implicit_rule_authority_decisions",
]
