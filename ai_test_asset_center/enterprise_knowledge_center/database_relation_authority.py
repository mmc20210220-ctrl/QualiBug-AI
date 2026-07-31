"""Durable operator authority for exact FK-scoped database relation observers.

Relation decisions share the existing database mapping authority ledger and decision schemas, but
are applied by this dedicated domain adapter so table/field approval semantics remain unchanged.
Candidate drift invalidates approval. Approval grants only parameterized read-only child collection
observation and never grants write-target, business-mapping or Oracle authority.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from ._common import ROOT, _safe_project_id
from ._utils import _now, _require_manage_actor
from .database_mapping_authority import (
    ACTION_APPROVE_READ_ONLY_OBSERVER,
    ACTION_LEAVE_UNRESOLVED,
    ACTION_REJECT_MAPPING,
    DATABASE_MAPPING_AUDIT_SCHEMA,
    DATABASE_MAPPING_DECISION_SCHEMA,
    _load_current_asset,
    load_database_mapping_authority_ledger,
    save_database_mapping_authority_ledger,
)

DATABASE_RELATION_AUTHORITY_RECEIPT_SCHEMA = (
    "qualibug.database-relation-authority-application-receipt.v1"
)
_ALLOWED_ACTIONS = {
    ACTION_APPROVE_READ_ONLY_OBSERVER,
    ACTION_REJECT_MAPPING,
    ACTION_LEAVE_UNRESOLVED,
}
_COLLECTION = "database_relation_observer_candidates"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(value) for value in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _candidate_projection(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_kind": "relation",
        "candidate_id": _text(candidate.get("candidate_id")),
        "root_observer_id": _text(candidate.get("root_observer_id")),
        "operation_schema_binding_id": _text(candidate.get("operation_schema_binding_id")),
        "interface_id": _text(candidate.get("interface_id")),
        "method": _text(candidate.get("method")).upper(),
        "path": _text(candidate.get("path")),
        "database_relationship_id": _text(candidate.get("database_relationship_id")),
        "parent_table_id": _text(candidate.get("parent_table_id")),
        "parent_columns": [
            _text(value)
            for value in _list(candidate.get("parent_columns"))
            if _text(value)
        ],
        "root_selected_identity_key": [
            _text(value)
            for value in _list(candidate.get("root_selected_identity_key"))
            if _text(value)
        ],
        "child_table_id": _text(candidate.get("child_table_id")),
        "child_columns": [
            _text(value)
            for value in _list(candidate.get("child_columns"))
            if _text(value)
        ],
        "predicate_pairs": [
            {
                "ordinal": row.get("ordinal"),
                "child_database_field_name": _text(row.get("child_database_field_name")),
                "parent_database_field_name": _text(row.get("parent_database_field_name")),
                "parent_database_field_id": _text(row.get("parent_database_field_id")),
                "parent_field_binding_id": _text(row.get("parent_field_binding_id")),
                "value_source": _text(row.get("value_source")),
            }
            for row in _list(candidate.get("predicate_pairs"))
            if isinstance(row, dict)
        ],
        "available_child_fields": [
            {
                "database_field_id": _text(row.get("database_field_id")),
                "database_field_name": _text(row.get("database_field_name")),
                "database_declared_type": _text(row.get("database_declared_type")),
                "nullable": row.get("nullable"),
                "source_id": _text(row.get("source_id")),
                "source_locator": _text(row.get("source_locator")),
            }
            for row in _list(candidate.get("available_child_fields"))
            if isinstance(row, dict)
        ],
        "source_id": _text(candidate.get("source_id")),
        "source_locator": _text(candidate.get("source_locator")),
        "root_mapping_decision_refs": sorted(
            _text(value)
            for value in _list(candidate.get("root_mapping_decision_refs"))
            if _text(value)
        ),
    }


def database_relation_candidate_fingerprint(candidate: dict[str, Any]) -> str:
    return hashlib.sha256(
        _canonical(_candidate_projection(candidate)).encode("utf-8")
    ).hexdigest()


def _find_candidate(asset: dict[str, Any], candidate_id: str) -> dict[str, Any] | None:
    matches = [
        dict(row)
        for row in _list(asset.get(_COLLECTION))
        if isinstance(row, dict) and _text(row.get("candidate_id")) == candidate_id
    ]
    return matches[0] if len(matches) == 1 else None


def record_database_relation_authority_decision(
    project_id: str,
    *,
    candidate_id: str,
    action: str,
    actor: dict[str, Any],
    rationale: str = "",
    root: Path | None = None,
    asset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one explicit relation decision in the existing mapping ledger."""
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    selected_action = _text(action).upper()
    if selected_action not in _ALLOWED_ACTIONS:
        raise ValueError("database_relation_authority_action_invalid")
    identity = _require_manage_actor(actor)
    current_asset = dict(
        asset if isinstance(asset, dict) else _load_current_asset(project, resolved_root)
    )
    candidate = _find_candidate(current_asset, _text(candidate_id))
    if candidate is None:
        raise ValueError("database_relation_candidate_not_found_or_ambiguous")
    if _text(candidate.get("status")) != "PENDING_RELATION_AUTHORITY":
        raise ValueError("database_relation_candidate_not_approvable")

    fingerprint = database_relation_candidate_fingerprint(candidate)
    decided_at = _now()
    decision_id = _stable_id(
        "database_mapping_decision",
        project,
        "relation",
        candidate_id,
        fingerprint,
        selected_action,
        identity.get("name"),
        decided_at,
    )
    receipt_id = _stable_id("database_mapping_decision_audit", decision_id)
    decision = {
        "schema": DATABASE_MAPPING_DECISION_SCHEMA,
        "decision_id": decision_id,
        "project_id": project,
        "candidate_kind": "relation",
        "candidate_id": _text(candidate_id),
        "candidate_fingerprint": fingerprint,
        "candidate_projection": _candidate_projection(candidate),
        "action": selected_action,
        "actor": identity,
        "rationale": _text(rationale)[:2000],
        "decided_at_utc": decided_at,
        "audit_receipt_id": receipt_id,
        "read_only_observer_scope_only": True,
        "relation_scope_only": True,
        "write_target_authority_granted": False,
        "oracle_authority_granted": False,
        "business_mapping_authority_granted": False,
    }
    receipt = {
        "schema": DATABASE_MAPPING_AUDIT_SCHEMA,
        "audit_receipt_id": receipt_id,
        "decision_id": decision_id,
        "project_id": project,
        "candidate_kind": "relation",
        "candidate_id": _text(candidate_id),
        "candidate_fingerprint": fingerprint,
        "action": selected_action,
        "actor": identity,
        "recorded_at_utc": decided_at,
    }
    ledger = load_database_mapping_authority_ledger(project, resolved_root)
    ledger["decisions"] = [*_list(ledger.get("decisions")), decision]
    ledger["audit_receipts"] = [*_list(ledger.get("audit_receipts")), receipt]
    path = save_database_mapping_authority_ledger(ledger, project, resolved_root)
    return {"decision": decision, "audit_receipt": receipt, "ledger_path": str(path)}


def _latest_decision(decisions: Iterable[Any], candidate_id: str) -> dict[str, Any] | None:
    for raw in reversed(list(decisions)):
        row = _dict(raw)
        if (
            _text(row.get("candidate_kind")) == "relation"
            and _text(row.get("candidate_id")) == candidate_id
        ):
            return dict(row)
    return None


def _authority_payload(
    status: str,
    *,
    decision: dict[str, Any] | None,
    current_fingerprint: str,
) -> dict[str, Any]:
    row = _dict(decision)
    return {
        "status": status,
        "decision_id": _text(row.get("decision_id")),
        "action": _text(row.get("action")),
        "actor": deepcopy(_dict(row.get("actor"))),
        "rationale": _text(row.get("rationale")),
        "decided_at_utc": _text(row.get("decided_at_utc")),
        "recorded_candidate_fingerprint": _text(row.get("candidate_fingerprint")),
        "current_candidate_fingerprint": current_fingerprint,
        "candidate_drift_detected": bool(
            row and _text(row.get("candidate_fingerprint")) != current_fingerprint
        ),
        "automatic_resolution_allowed": False,
        "read_only_relation_observer_scope_only": True,
        "write_target_authority_granted": False,
        "oracle_authority_granted": False,
        "business_mapping_authority_granted": False,
    }


def apply_database_relation_authority_decisions(
    asset: dict[str, Any],
    *,
    project_id: str = "",
    root: Path | None = None,
    ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply current relation decisions to freshly rebuilt FK candidates."""
    result = dict(asset or {})
    project = _safe_project_id(
        project_id or _text(result.get("project_id")) or "real_project_demo"
    )
    loaded = ledger or load_database_mapping_authority_ledger(project, root)
    decisions = [
        dict(row)
        for row in _list(loaded.get("decisions"))
        if isinstance(row, dict)
    ]
    rows: list[dict[str, Any]] = []
    stale_gaps: list[dict[str, Any]] = []
    approved = rejected = unresolved = 0
    applied_ids: set[str] = set()

    for raw in _list(result.get(_COLLECTION)):
        if not isinstance(raw, dict):
            continue
        row = deepcopy(raw)
        candidate_id = _text(row.get("candidate_id"))
        fingerprint = database_relation_candidate_fingerprint(row)
        decision = _latest_decision(decisions, candidate_id)
        if _text(row.get("status")) != "PENDING_RELATION_AUTHORITY":
            row["relation_authority"] = _authority_payload(
                "NOT_APPLICABLE", decision=None, current_fingerprint=fingerprint
            )
            rows.append(row)
            continue
        if decision is None:
            row["relation_authority"] = _authority_payload(
                "UNRESOLVED", decision=None, current_fingerprint=fingerprint
            )
            unresolved += 1
        elif _text(decision.get("candidate_fingerprint")) != fingerprint:
            row["status"] = "PENDING_RELATION_AUTHORITY"
            row["observer_authority_allowed"] = False
            row["relation_mapping_confirmed"] = False
            row["relation_authority"] = _authority_payload(
                "STALE_DECISION", decision=decision, current_fingerprint=fingerprint
            )
            stale_gaps.append(
                {
                    "kind": "DATABASE_RELATION_AUTHORITY_DECISION_STALE",
                    "gap_type": "database_relation_candidate_drift",
                    "candidate_kind": "relation",
                    "candidate_id": candidate_id,
                    "decision_id": _text(decision.get("decision_id")),
                    "blocks_database_relation_observer_compilation": True,
                }
            )
            unresolved += 1
        else:
            action = _text(decision.get("action"))
            applied_ids.add(_text(decision.get("decision_id")))
            if action == ACTION_APPROVE_READ_ONLY_OBSERVER:
                row["status"] = "APPROVED_READ_ONLY_RELATION_OBSERVER"
                row["observer_candidate_only"] = False
                row["observer_authority_allowed"] = True
                row["relation_mapping_confirmed"] = True
                row["write_target_allowed"] = False
                row["oracle_authority_allowed"] = False
                row["relation_authority"] = _authority_payload(
                    "APPROVED", decision=decision, current_fingerprint=fingerprint
                )
                approved += 1
            elif action == ACTION_REJECT_MAPPING:
                row["status"] = "REJECTED_BY_OPERATOR"
                row["observer_candidate_only"] = False
                row["observer_authority_allowed"] = False
                row["relation_mapping_confirmed"] = False
                row["relation_authority"] = _authority_payload(
                    "REJECTED", decision=decision, current_fingerprint=fingerprint
                )
                rejected += 1
            else:
                row["status"] = "PENDING_RELATION_AUTHORITY"
                row["observer_authority_allowed"] = False
                row["relation_mapping_confirmed"] = False
                row["relation_authority"] = _authority_payload(
                    "UNRESOLVED", decision=decision, current_fingerprint=fingerprint
                )
                unresolved += 1
        rows.append(row)

    retained_gaps = [
        deepcopy(row)
        for row in _list(result.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("kind")) != "DATABASE_RELATION_AUTHORITY_DECISION_STALE"
    ]
    result[_COLLECTION] = rows
    result["coverage_gaps"] = [*retained_gaps, *stale_gaps]
    result["database_relation_authority_receipt"] = {
        "schema": DATABASE_RELATION_AUTHORITY_RECEIPT_SCHEMA,
        "status": (
            "NOT_APPLICABLE"
            if not rows
            else "PARTIAL"
            if stale_gaps or unresolved
            else "COMPLETE"
        ),
        "decision_count": sum(
            1 for row in decisions if _text(row.get("candidate_kind")) == "relation"
        ),
        "applied_decision_count": len(applied_ids),
        "approved_relation_count": approved,
        "rejected_relation_count": rejected,
        "unresolved_relation_count": unresolved,
        "stale_decision_count": len(stale_gaps),
        "automatic_approval_count": 0,
        "write_target_authority_count": 0,
        "oracle_authority_count": 0,
        "business_mapping_authority_count": 0,
        "candidate_drift_fails_closed": True,
    }
    summary = _dict(result.get("summary"))
    summary.update(
        {
            "approved_database_relation_observer_count": approved,
            "database_relation_authority_gap_count": len(stale_gaps) + unresolved,
        }
    )
    result["summary"] = summary
    governance = _dict(result.get("governance"))
    governance.update(
        {
            "database_relation_authority_uses_existing_mapping_ledger": True,
            "database_relation_decisions_are_durable": True,
            "database_relation_candidate_drift_invalidates_decision": True,
            "database_relation_root_identity_changes_invalidate_decision": True,
            "database_relation_approval_is_read_only_scope": True,
            "database_relation_approval_never_authorizes_writes": True,
            "database_relation_approval_never_authorizes_oracles": True,
        }
    )
    result["governance"] = governance
    return result


__all__ = [
    "DATABASE_RELATION_AUTHORITY_RECEIPT_SCHEMA",
    "apply_database_relation_authority_decisions",
    "database_relation_candidate_fingerprint",
    "record_database_relation_authority_decision",
]
