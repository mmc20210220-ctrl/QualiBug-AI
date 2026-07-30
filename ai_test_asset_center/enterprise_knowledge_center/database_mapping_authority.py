"""Durable operator authority for API-operation to database observer mappings.

This module extends the project's fail-closed authority pattern without reusing business-fact
SELECT_FACT semantics for a different decision domain. Storage candidates stay non-authoritative
until an explicit operator decision approves them for read-only observation. Approval never grants
write-target or oracle authority, and candidate drift invalidates old decisions.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from ._common import ROOT, _load_json, _safe_project_id, _write_json
from ._utils import _now, _paths, _require_manage_actor

DATABASE_MAPPING_DECISION_SCHEMA = "qualibug.database-mapping-authority-decision.v1"
DATABASE_MAPPING_LEDGER_SCHEMA = "qualibug.database-mapping-authority-ledger.v1"
DATABASE_MAPPING_AUDIT_SCHEMA = "qualibug.database-mapping-authority-audit.v1"
DATABASE_MAPPING_AUTHORITY_RECEIPT_SCHEMA = (
    "qualibug.database-mapping-authority-application-receipt.v1"
)

ACTION_APPROVE_READ_ONLY_OBSERVER = "APPROVE_READ_ONLY_OBSERVER"
ACTION_REJECT_MAPPING = "REJECT_MAPPING"
ACTION_LEAVE_UNRESOLVED = "LEAVE_UNRESOLVED"
_ALLOWED_ACTIONS = {
    ACTION_APPROVE_READ_ONLY_OBSERVER,
    ACTION_REJECT_MAPPING,
    ACTION_LEAVE_UNRESOLVED,
}
_ALLOWED_KINDS = {"table", "field"}

_TABLE_COLLECTION = "api_operation_database_table_candidates"
_FIELD_COLLECTION = "api_operation_database_field_candidates"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = "\x1f".join(_text(value) for value in parts)
    return f"{prefix}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _candidate_projection(candidate: dict[str, Any], candidate_kind: str) -> dict[str, Any]:
    common = {
        "candidate_kind": candidate_kind,
        "candidate_id": _text(candidate.get("candidate_id")),
        "operation_schema_binding_id": _text(
            candidate.get("operation_schema_binding_id")
        ),
        "interface_id": _text(candidate.get("interface_id")),
        "method": _text(candidate.get("method")).upper(),
        "path": _text(candidate.get("path")),
        "direction": _text(candidate.get("direction")),
        "response_status": _text(candidate.get("response_status")),
        "media_type": _text(candidate.get("media_type")),
        "api_schema_entity_id": _text(candidate.get("api_schema_entity_id")),
        "database_table_id": _text(candidate.get("database_table_id")),
    }
    if candidate_kind == "table":
        common.update(
            {
                "api_schema_id": _text(candidate.get("api_schema_id")),
                "api_schema_name": _text(candidate.get("api_schema_name")),
                "entity_alignment_candidate_id": _text(
                    candidate.get("entity_alignment_candidate_id")
                ),
                "database_schema_name": _text(
                    candidate.get("database_schema_name")
                ),
                "database_qualified_name": _text(
                    candidate.get("database_qualified_name")
                ),
            }
        )
    else:
        compatibility = _dict(candidate.get("type_compatibility"))
        common.update(
            {
                "api_field_id": _text(candidate.get("api_field_id")),
                "api_field_name": _text(candidate.get("api_field_name")),
                "api_property_path": [
                    _text(value)
                    for value in _list(candidate.get("api_property_path"))
                    if _text(value)
                ],
                "field_alignment_candidate_id": _text(
                    candidate.get("field_alignment_candidate_id")
                ),
                "database_field_id": _text(candidate.get("database_field_id")),
                "database_field_name": _text(
                    candidate.get("database_field_name")
                ),
                "type_compatibility": {
                    "status": _text(compatibility.get("status")),
                    "api_declared_type": _text(
                        compatibility.get("api_declared_type")
                    ),
                    "database_declared_type": _text(
                        compatibility.get("database_declared_type")
                    ),
                },
            }
        )
    return common


def database_mapping_candidate_fingerprint(
    candidate: dict[str, Any], candidate_kind: str
) -> str:
    """Return the immutable decision binding fingerprint for one current candidate."""
    kind = _text(candidate_kind).lower()
    if kind not in _ALLOWED_KINDS:
        raise ValueError("database_mapping_candidate_kind_invalid")
    return hashlib.sha256(
        _canonical_json(_candidate_projection(candidate, kind)).encode("utf-8")
    ).hexdigest()


def _ledger_path(project: str, root: Path) -> Path:
    return _paths(project, root)["workspace"] / "database_mapping_authority_decisions.json"


def _empty_ledger(project: str) -> dict[str, Any]:
    return {
        "schema": DATABASE_MAPPING_LEDGER_SCHEMA,
        "project_id": project,
        "updated_at_utc": _now(),
        "decisions": [],
        "audit_receipts": [],
    }


def load_database_mapping_authority_ledger(
    project_id: str, root: Path | None = None
) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    loaded = _load_json(_ledger_path(project, resolved_root), {})
    if (
        not isinstance(loaded, dict)
        or _text(loaded.get("schema")) != DATABASE_MAPPING_LEDGER_SCHEMA
    ):
        return _empty_ledger(project)
    return {
        "schema": DATABASE_MAPPING_LEDGER_SCHEMA,
        "project_id": project,
        "updated_at_utc": _text(loaded.get("updated_at_utc")) or _now(),
        "decisions": [
            dict(row)
            for row in _list(loaded.get("decisions"))
            if isinstance(row, dict)
            and _text(row.get("schema")) == DATABASE_MAPPING_DECISION_SCHEMA
        ],
        "audit_receipts": [
            dict(row)
            for row in _list(loaded.get("audit_receipts"))
            if isinstance(row, dict)
            and _text(row.get("schema")) == DATABASE_MAPPING_AUDIT_SCHEMA
        ],
    }


def save_database_mapping_authority_ledger(
    ledger: dict[str, Any], project_id: str, root: Path | None = None
) -> Path:
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    path = _ledger_path(project, resolved_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(ledger or {})
    payload.update(
        {
            "schema": DATABASE_MAPPING_LEDGER_SCHEMA,
            "project_id": project,
            "updated_at_utc": _now(),
        }
    )
    _write_json(path, payload)
    return path


def _load_current_asset(project: str, root: Path) -> dict[str, Any]:
    paths = _paths(project, root)
    loaded = _load_json(paths["asset"], {})
    if not isinstance(loaded, dict) or not loaded:
        loaded = _load_json(paths["asset_copy"], {})
    return dict(loaded) if isinstance(loaded, dict) else {}


def _candidate_collection(candidate_kind: str) -> str:
    return _TABLE_COLLECTION if candidate_kind == "table" else _FIELD_COLLECTION


def _find_candidate(
    asset: dict[str, Any], candidate_kind: str, candidate_id: str
) -> dict[str, Any] | None:
    matches = [
        dict(row)
        for row in _list(asset.get(_candidate_collection(candidate_kind)))
        if isinstance(row, dict) and _text(row.get("candidate_id")) == candidate_id
    ]
    return matches[0] if len(matches) == 1 else None


def record_database_mapping_authority_decision(
    project_id: str,
    *,
    candidate_kind: str,
    candidate_id: str,
    action: str,
    actor: dict[str, Any],
    rationale: str = "",
    root: Path | None = None,
    asset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one explicit mapping decision after validating the current candidate."""
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    kind = _text(candidate_kind).lower()
    selected_action = _text(action).upper()
    if kind not in _ALLOWED_KINDS:
        raise ValueError("database_mapping_candidate_kind_invalid")
    if selected_action not in _ALLOWED_ACTIONS:
        raise ValueError("database_mapping_authority_action_invalid")
    identity = _require_manage_actor(actor)
    current_asset = dict(asset or _load_current_asset(project, resolved_root))
    candidate = _find_candidate(current_asset, kind, _text(candidate_id))
    if candidate is None:
        raise ValueError("database_mapping_candidate_not_found_or_ambiguous")
    compatibility = _text(
        _dict(candidate.get("type_compatibility")).get("status")
    )
    if (
        kind == "field"
        and selected_action == ACTION_APPROVE_READ_ONLY_OBSERVER
        and compatibility == "INCOMPATIBLE"
    ):
        raise ValueError("incompatible_database_field_mapping_cannot_be_approved")

    fingerprint = database_mapping_candidate_fingerprint(candidate, kind)
    decided_at = _now()
    decision_id = _stable_id(
        "database_mapping_decision",
        project,
        kind,
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
        "candidate_kind": kind,
        "candidate_id": _text(candidate_id),
        "candidate_fingerprint": fingerprint,
        "candidate_projection": _candidate_projection(candidate, kind),
        "action": selected_action,
        "actor": identity,
        "rationale": _text(rationale)[:2000],
        "decided_at_utc": decided_at,
        "audit_receipt_id": receipt_id,
        "read_only_observer_scope_only": True,
        "write_target_authority_granted": False,
        "oracle_authority_granted": False,
    }
    receipt = {
        "schema": DATABASE_MAPPING_AUDIT_SCHEMA,
        "audit_receipt_id": receipt_id,
        "decision_id": decision_id,
        "project_id": project,
        "candidate_kind": kind,
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


def list_database_mapping_authority_decisions(
    project_id: str, root: Path | None = None
) -> list[dict[str, Any]]:
    ledger = load_database_mapping_authority_ledger(project_id, root)
    return [dict(row) for row in _list(ledger.get("decisions")) if isinstance(row, dict)]


def _latest_decision(
    decisions: Iterable[Any], candidate_kind: str, candidate_id: str
) -> dict[str, Any] | None:
    for raw in reversed(list(decisions)):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        if (
            _text(row.get("candidate_kind")) == candidate_kind
            and _text(row.get("candidate_id")) == candidate_id
        ):
            return row
    return None


def _authority_payload(
    status: str,
    *,
    decision: dict[str, Any] | None = None,
    current_fingerprint: str = "",
) -> dict[str, Any]:
    row = _dict(decision)
    return {
        "status": status,
        "decision_id": _text(row.get("decision_id")),
        "action": _text(row.get("action")),
        "actor": deepcopy(_dict(row.get("actor"))),
        "rationale": _text(row.get("rationale")),
        "decided_at_utc": _text(row.get("decided_at_utc")),
        "recorded_candidate_fingerprint": _text(
            row.get("candidate_fingerprint")
        ),
        "current_candidate_fingerprint": current_fingerprint,
        "candidate_drift_detected": bool(
            row
            and current_fingerprint
            and _text(row.get("candidate_fingerprint")) != current_fingerprint
        ),
        "automatic_resolution_allowed": False,
        "read_only_observer_scope_only": True,
        "write_target_authority_granted": False,
        "oracle_authority_granted": False,
    }


def _apply_candidate_decision(
    candidate: dict[str, Any],
    candidate_kind: str,
    decisions: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    row = deepcopy(candidate)
    candidate_id = _text(row.get("candidate_id"))
    fingerprint = database_mapping_candidate_fingerprint(row, candidate_kind)
    decision = _latest_decision(decisions, candidate_kind, candidate_id)
    if not decision:
        row["mapping_authority"] = _authority_payload(
            "UNRESOLVED", current_fingerprint=fingerprint
        )
        return row, None
    if _text(decision.get("candidate_fingerprint")) != fingerprint:
        row["mapping_authority"] = _authority_payload(
            "STALE_DECISION", decision=decision, current_fingerprint=fingerprint
        )
        row["status"] = "PENDING_STORAGE_AUTHORITY"
        row["observer_authority_allowed"] = False
        row["storage_mapping_confirmed"] = False
        return row, decision

    action = _text(decision.get("action"))
    if action == ACTION_REJECT_MAPPING:
        row["status"] = "REJECTED_BY_OPERATOR"
        row["observer_candidate_only"] = False
        row["observer_authority_allowed"] = False
        row["storage_mapping_confirmed"] = False
        row["mapping_authority"] = _authority_payload(
            "REJECTED", decision=decision, current_fingerprint=fingerprint
        )
    elif action == ACTION_LEAVE_UNRESOLVED:
        row["status"] = (
            "PENDING_STORAGE_AUTHORITY"
            if candidate_kind == "table"
            else "PENDING_STORAGE_FIELD_AUTHORITY"
        )
        row["observer_authority_allowed"] = False
        row["storage_mapping_confirmed"] = False
        row["mapping_authority"] = _authority_payload(
            "UNRESOLVED", decision=decision, current_fingerprint=fingerprint
        )
    elif action == ACTION_APPROVE_READ_ONLY_OBSERVER:
        row["status"] = (
            "APPROVED_READ_ONLY_OBSERVER_TABLE"
            if candidate_kind == "table"
            else "APPROVED_READ_ONLY_OBSERVER_FIELD"
        )
        row["observer_candidate_only"] = False
        row["observer_authority_allowed"] = True
        row["storage_mapping_confirmed"] = True
        row["write_target_allowed"] = False
        row["oracle_authority_allowed"] = False
        row["mapping_authority"] = _authority_payload(
            "APPROVED", decision=decision, current_fingerprint=fingerprint
        )
    return row, decision


def apply_database_mapping_authority_decisions(
    asset: dict[str, Any],
    *,
    project_id: str = "",
    root: Path | None = None,
    ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply durable decisions to freshly rebuilt candidates, failing closed on drift."""
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

    stale_rows: list[dict[str, Any]] = []
    table_rows: list[dict[str, Any]] = []
    approved_table_keys: set[tuple[str, str]] = set()
    applied_decision_ids: set[str] = set()
    rejected_count = 0
    unresolved_count = 0

    for raw in _list(result.get(_TABLE_COLLECTION)):
        if not isinstance(raw, dict):
            continue
        row, decision = _apply_candidate_decision(dict(raw), "table", decisions)
        authority = _dict(row.get("mapping_authority"))
        if authority.get("candidate_drift_detected"):
            stale_rows.append(
                {
                    "kind": "DATABASE_MAPPING_AUTHORITY_DECISION_STALE",
                    "gap_type": "database_mapping_candidate_drift",
                    "candidate_kind": "table",
                    "candidate_id": row.get("candidate_id"),
                    "decision_id": authority.get("decision_id"),
                    "blocks_database_observer_compilation": True,
                }
            )
        elif _text(row.get("status")) == "APPROVED_READ_ONLY_OBSERVER_TABLE":
            approved_table_keys.add(
                (
                    _text(row.get("operation_schema_binding_id")),
                    _text(row.get("database_table_id")),
                )
            )
        elif _text(row.get("status")) == "REJECTED_BY_OPERATOR":
            rejected_count += 1
        else:
            unresolved_count += 1
        if decision and _text(decision.get("decision_id")):
            applied_decision_ids.add(_text(decision.get("decision_id")))
        table_rows.append(row)

    field_rows: list[dict[str, Any]] = []
    for raw in _list(result.get(_FIELD_COLLECTION)):
        if not isinstance(raw, dict):
            continue
        row, decision = _apply_candidate_decision(dict(raw), "field", decisions)
        authority = _dict(row.get("mapping_authority"))
        key = (
            _text(row.get("operation_schema_binding_id")),
            _text(row.get("database_table_id")),
        )
        compatibility = _text(_dict(row.get("type_compatibility")).get("status"))
        if authority.get("candidate_drift_detected"):
            stale_rows.append(
                {
                    "kind": "DATABASE_MAPPING_AUTHORITY_DECISION_STALE",
                    "gap_type": "database_mapping_candidate_drift",
                    "candidate_kind": "field",
                    "candidate_id": row.get("candidate_id"),
                    "decision_id": authority.get("decision_id"),
                    "blocks_database_observer_compilation": True,
                }
            )
        elif _text(row.get("status")) == "APPROVED_READ_ONLY_OBSERVER_FIELD":
            if compatibility == "INCOMPATIBLE":
                row["status"] = "BLOCKED_INCOMPATIBLE_TYPE_APPROVAL"
                row["observer_authority_allowed"] = False
                row["storage_mapping_confirmed"] = False
                stale_rows.append(
                    {
                        "kind": "DATABASE_MAPPING_INCOMPATIBLE_FIELD_APPROVAL_BLOCKED",
                        "gap_type": "database_field_type_incompatible",
                        "candidate_kind": "field",
                        "candidate_id": row.get("candidate_id"),
                        "decision_id": authority.get("decision_id"),
                        "blocks_database_observer_compilation": True,
                    }
                )
            elif key not in approved_table_keys:
                row["status"] = "BLOCKED_PARENT_TABLE_AUTHORITY_REQUIRED"
                row["observer_authority_allowed"] = False
                row["storage_mapping_confirmed"] = False
                stale_rows.append(
                    {
                        "kind": "DATABASE_MAPPING_PARENT_TABLE_AUTHORITY_REQUIRED",
                        "gap_type": "database_table_mapping_not_approved",
                        "candidate_kind": "field",
                        "candidate_id": row.get("candidate_id"),
                        "decision_id": authority.get("decision_id"),
                        "database_table_id": row.get("database_table_id"),
                        "blocks_database_observer_compilation": True,
                    }
                )
        elif _text(row.get("status")) == "REJECTED_BY_OPERATOR":
            rejected_count += 1
        else:
            unresolved_count += 1
        if decision and _text(decision.get("decision_id")):
            applied_decision_ids.add(_text(decision.get("decision_id")))
        field_rows.append(row)

    existing_gaps = [
        deepcopy(row)
        for row in _list(result.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("kind"))
        not in {
            "DATABASE_MAPPING_AUTHORITY_DECISION_STALE",
            "DATABASE_MAPPING_INCOMPATIBLE_FIELD_APPROVAL_BLOCKED",
            "DATABASE_MAPPING_PARENT_TABLE_AUTHORITY_REQUIRED",
        }
    ]
    result[_TABLE_COLLECTION] = table_rows
    result[_FIELD_COLLECTION] = field_rows
    result["coverage_gaps"] = [*existing_gaps, *stale_rows]

    approved_table_count = sum(
        1
        for row in table_rows
        if _text(row.get("status")) == "APPROVED_READ_ONLY_OBSERVER_TABLE"
    )
    approved_field_count = sum(
        1
        for row in field_rows
        if _text(row.get("status")) == "APPROVED_READ_ONLY_OBSERVER_FIELD"
        and bool(row.get("observer_authority_allowed"))
    )
    result["database_mapping_authority_receipt"] = {
        "schema": DATABASE_MAPPING_AUTHORITY_RECEIPT_SCHEMA,
        "status": (
            "NOT_APPLICABLE"
            if not table_rows and not field_rows
            else "PARTIAL"
            if stale_rows or unresolved_count
            else "COMPLETE"
        ),
        "decision_count": len(decisions),
        "applied_decision_count": len(applied_decision_ids),
        "approved_table_mapping_count": approved_table_count,
        "approved_field_mapping_count": approved_field_count,
        "rejected_mapping_count": rejected_count,
        "unresolved_mapping_count": unresolved_count,
        "stale_or_blocked_decision_count": len(stale_rows),
        "automatic_approval_count": 0,
        "write_target_authority_count": 0,
        "oracle_authority_count": 0,
        "candidate_drift_fails_closed": True,
        "read_only_observer_scope_only": True,
    }
    summary = _dict(result.get("summary"))
    summary.update(
        {
            "approved_database_table_mapping_count": approved_table_count,
            "approved_database_field_mapping_count": approved_field_count,
            "database_mapping_authority_gap_count": len(stale_rows),
        }
    )
    result["summary"] = summary
    governance = _dict(result.get("governance"))
    governance.update(
        {
            "database_mapping_requires_explicit_operator_authority": True,
            "database_mapping_decisions_are_durable": True,
            "database_mapping_candidate_drift_invalidates_decision": True,
            "database_mapping_approval_is_read_only_observer_scope": True,
            "database_mapping_approval_never_authorizes_writes": True,
            "database_mapping_approval_never_authorizes_oracles": True,
        }
    )
    result["governance"] = governance
    return result


__all__ = [
    "DATABASE_MAPPING_DECISION_SCHEMA",
    "DATABASE_MAPPING_LEDGER_SCHEMA",
    "DATABASE_MAPPING_AUDIT_SCHEMA",
    "DATABASE_MAPPING_AUTHORITY_RECEIPT_SCHEMA",
    "ACTION_APPROVE_READ_ONLY_OBSERVER",
    "ACTION_REJECT_MAPPING",
    "ACTION_LEAVE_UNRESOLVED",
    "database_mapping_candidate_fingerprint",
    "load_database_mapping_authority_ledger",
    "save_database_mapping_authority_ledger",
    "record_database_mapping_authority_decision",
    "list_database_mapping_authority_decisions",
    "apply_database_mapping_authority_decisions",
]
