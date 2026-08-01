"""Reduce exact direct revocation over derived role permissions.

This authority does not parse permissions, role hierarchy or delegation. It consumes the
existing permission matrix and removes only a derived ALLOW superseded by a source-backed
direct DENY for the same role, bound interface, scope and overlapping action. Direct source
contradictions remain active so the existing Behavior IR conflict gate stays fail-closed.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any


RECEIPT_SCHEMA = "qualibug.authorization-precedence-receipt.v1"
_DERIVED_REASONS = {
    "delegated_permission": "DIRECT_DENY_REVOKES_DELEGATED_ALLOW",
    "role_inherited_permission": "DIRECT_DENY_REVOKES_INHERITED_ALLOW",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _norm(value: Any) -> str:
    return re.sub(r"[\s，,。；;：:（）()【】\[\]“”\"'、]+", "", _text(value)).lower()


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _decision(row: dict[str, Any]) -> str:
    raw = _text(row.get("decision") or row.get("effect")).lower()
    if raw in {"deny", "denied", "forbid", "forbidden", "prohibit", "prohibited"}:
        return "DENY"
    if raw in {"allow", "allowed", "grant", "granted", "permit", "permitted"}:
        return "ALLOW"
    return ""


def _actions(row: dict[str, Any], *, deny: bool) -> set[str]:
    values = (
        _list(
            row.get("denied_actions")
            or row.get("forbidden_actions")
            or row.get("prohibited_actions")
        )
        if deny and _decision(row) != "DENY"
        else _list(row.get("actions"))
    )
    if not values and _text(row.get("action")):
        values = [row.get("action")]
    return {_norm(value) for value in values if _norm(value)}


def _coordinate(row: dict[str, Any]) -> tuple[str, str, str] | None:
    role = _norm(row.get("role") or row.get("actor") or row.get("principal"))
    interface = _text(row.get("interface_id"))
    resource = _text(row.get("resource"))
    operation = interface or (resource if resource.startswith("/") else "")
    scope = _norm(row.get("scope") or row.get("data_scope") or "unspecified") or "unspecified"
    if not role or not operation:
        return None
    return role, operation, scope


def apply_effective_permission_precedence(asset: dict[str, Any]) -> dict[str, Any]:
    """Apply direct-DENY precedence only to exact derived-ALLOW coordinates."""
    rows = [dict(row) for row in _list(asset.get("permission_matrix")) if isinstance(row, dict)]
    direct_denies: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if _text(row.get("authorization_kind")) in _DERIVED_REASONS:
            continue
        denied_actions = _actions(row, deny=True)
        if _decision(row) != "DENY" and not denied_actions:
            continue
        coordinate = _coordinate(row)
        if coordinate is not None:
            direct_denies.setdefault(coordinate, []).append(row)

    active: list[dict[str, Any]] = []
    superseded: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    for row in rows:
        derived_kind = _text(row.get("authorization_kind"))
        reason = _DERIVED_REASONS.get(derived_kind, "")
        if not reason or _decision(row) != "ALLOW":
            active.append(row)
            continue
        coordinate = _coordinate(row)
        derived_actions = _actions(row, deny=False)
        overriding = []
        for candidate in direct_denies.get(coordinate or (), []):
            candidate_actions = _actions(candidate, deny=True)
            if not candidate_actions or derived_actions.intersection(candidate_actions):
                overriding.append(candidate)
        if not overriding:
            active.append(row)
            continue

        superseded_permission_id = _text(row.get("permission_id"))
        superseded.append(
            {
                **row,
                "status": "SUPERSEDED",
                "supersession_reason": reason,
            }
        )
        payload = {
            "schema_version": RECEIPT_SCHEMA,
            "status": "SUPERSEDED",
            "reason_code": reason,
            "superseded_authorization_kind": derived_kind,
            "role": _text(row.get("role")),
            "interface_id": _text(row.get("interface_id")),
            "resource": _text(row.get("resource")),
            "scope": _text(row.get("scope") or "unspecified"),
            "superseded_permission_id": superseded_permission_id,
            "superseded_fact_id": _text(row.get("fact_id")),
            "overriding_permission_ids": sorted(
                _text(value.get("permission_id"))
                for value in overriding
                if _text(value.get("permission_id"))
            ),
            "overriding_fact_ids": sorted(
                {
                    _text(value.get("fact_id"))
                    for value in overriding
                    if _text(value.get("fact_id"))
                }
            ),
        }
        if derived_kind == "delegated_permission":
            payload["delegated_permission_id"] = superseded_permission_id
            payload["delegated_fact_id"] = _text(row.get("fact_id"))
        elif derived_kind == "role_inherited_permission":
            payload["inherited_permission_id"] = superseded_permission_id
            payload["source_permission_id"] = _text(row.get("source_permission_id"))
            payload["inheritance_contract_ids"] = sorted(
                _text(value) for value in _list(row.get("inheritance_contract_ids"))
                if _text(value)
            )
        receipts.append(
            {
                **payload,
                "receipt_id": _stable_id(
                    "authorization_precedence",
                    derived_kind,
                    superseded_permission_id,
                    *payload["overriding_permission_ids"],
                ),
            }
        )

    existing_superseded = [
        dict(row)
        for row in _list(asset.get("superseded_permission_matrix_rows"))
        if isinstance(row, dict)
    ]
    superseded_by_id = {
        _text(row.get("permission_id")): row
        for row in [*existing_superseded, *superseded]
        if _text(row.get("permission_id"))
    }
    existing_receipts = [
        dict(row)
        for row in _list(asset.get("authorization_precedence_receipts"))
        if isinstance(row, dict)
    ]
    receipts_by_id = {
        _text(row.get("receipt_id")): row
        for row in [*existing_receipts, *receipts]
        if _text(row.get("receipt_id"))
    }

    asset["permission_matrix"] = active
    asset["superseded_permission_matrix_rows"] = sorted(
        superseded_by_id.values(),
        key=lambda row: _text(row.get("permission_id")),
    )
    asset["authorization_precedence_receipts"] = sorted(
        receipts_by_id.values(),
        key=lambda row: _text(row.get("receipt_id")),
    )
    summary = _dict(asset.get("summary"))
    delegated_generated = int(summary.get("source_fact_delegated_permission_count") or 0)
    summary["source_fact_delegated_permission_generated_count"] = delegated_generated
    summary["source_fact_delegated_permission_count"] = sum(
        _text(row.get("authorization_kind")) == "delegated_permission"
        for row in active
    )
    summary["source_fact_delegated_permission_superseded_count"] = sum(
        _text(row.get("authorization_kind")) == "delegated_permission"
        for row in superseded_by_id.values()
    )
    summary["role_inherited_permission_count"] = sum(
        _text(row.get("authorization_kind")) == "role_inherited_permission"
        for row in active
    )
    summary["role_inherited_permission_superseded_count"] = sum(
        _text(row.get("authorization_kind")) == "role_inherited_permission"
        for row in superseded_by_id.values()
    )
    asset["summary"] = summary
    governance = _dict(asset.get("governance"))
    governance.update(
        {
            "direct_deny_revokes_exact_delegated_allow": True,
            "direct_deny_revokes_exact_inherited_allow": True,
            "direct_permission_conflicts_remain_fail_closed": True,
            "derived_permission_precedence_requires_exact_scope_and_interface": True,
        }
    )
    asset["governance"] = governance
    return asset


def apply_delegated_permission_precedence(asset: dict[str, Any]) -> dict[str, Any]:
    """Compatibility alias for the generalized effective-permission authority."""
    return apply_effective_permission_precedence(asset)


__all__ = [
    "RECEIPT_SCHEMA",
    "apply_delegated_permission_precedence",
    "apply_effective_permission_precedence",
]
