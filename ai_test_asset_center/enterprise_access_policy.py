"""Policy-driven tenant and project authorization primitives.

Token and JWT identity verification are deliberately separate from permission
assignment. Deployment policy supplies opaque permission strings and project
scope; a JWT role claim is never treated as an authorization decision.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any


class AccessPolicyError(ValueError):
    """Authorization policy cannot be parsed or applied safely."""


@dataclass(frozen=True)
class AccessPrincipal:
    principal_id: str
    tenant_id: str
    permissions: frozenset[str]
    project_ids: frozenset[str]


def _text(value: Any, limit: int = 160) -> str:
    return str(value or "").strip()[:limit]


def _strings(value: Any, limit: int = 200) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    return frozenset(_text(item, limit) for item in value if _text(item, limit))


def _parse_principal(record: Any) -> AccessPrincipal:
    data = record if isinstance(record, dict) else {}
    principal_id = _text(data.get("principal_id"))
    tenant_id = _text(data.get("tenant_id"))
    permissions = _strings(data.get("permissions"))
    project_ids = _strings(data.get("project_ids"))
    if not principal_id or not tenant_id or not permissions:
        raise AccessPolicyError("access_policy_principal_invalid")
    return AccessPrincipal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        permissions=permissions,
        project_ids=project_ids,
    )


def _as_policy_object(value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise AccessPolicyError("access_policy_json_invalid") from exc
    else:
        payload = value
    if not isinstance(payload, dict):
        raise AccessPolicyError("access_policy_object_required")
    return payload


def parse_token_policy(value: str | dict[str, Any]) -> dict[str, AccessPrincipal]:
    """Parse a deployment-owned opaque-token map without retaining raw tokens."""
    payload = _as_policy_object(value)
    result: dict[str, AccessPrincipal] = {}
    for token, record in payload.items():
        token_value = _text(token, 1024)
        if not token_value:
            raise AccessPolicyError("access_policy_principal_invalid")
        result[token_value] = _parse_principal(record)
    return result


def parse_identity_policy(value: str | dict[str, Any]) -> dict[str, AccessPrincipal]:
    """Parse subject-to-principal mappings for a trusted identity provider."""
    payload = _as_policy_object(value)
    result: dict[str, AccessPrincipal] = {}
    for subject, record in payload.items():
        subject_value = _text(subject, 240)
        if not subject_value:
            raise AccessPolicyError("access_policy_principal_invalid")
        result[subject_value] = _parse_principal(record)
    return result


def authenticate_token(token: str, policy: dict[str, AccessPrincipal]) -> AccessPrincipal | None:
    """Constant-time lookup across configured opaque credentials."""
    supplied = _text(token, 1024)
    for configured, principal in policy.items():
        if secrets.compare_digest(supplied, configured):
            return principal
    return None


def authenticate_jwt_identity(token: str, policy: dict[str, AccessPrincipal]) -> AccessPrincipal | None:
    """Verify a QualiBug JWT then resolve its subject through deployment policy.

    The JWT's ``role`` claim is intentionally ignored. It is not a permission
    source and cannot expand access without an explicit identity policy mapping.
    """
    try:
        from .jwt_auth import verify_token
        payload = verify_token(_text(token, 4096))
    except Exception:
        return None
    subject = _text((payload or {}).get("sub"), 240)
    return policy.get(subject) if subject else None


def is_authorized(principal: AccessPrincipal, *, permission: str, project_id: str = "") -> bool:
    requested = _text(permission, 160)
    project = _text(project_id, 160)
    if not requested:
        return False
    permission_allowed = "*" in principal.permissions or requested in principal.permissions
    project_allowed = not project or "*" in principal.project_ids or project in principal.project_ids
    return permission_allowed and project_allowed


def require_authorized(principal: AccessPrincipal | None, *, permission: str, project_id: str = "") -> AccessPrincipal:
    if principal is None:
        raise AccessPolicyError("access_principal_missing")
    if not is_authorized(principal, permission=permission, project_id=project_id):
        raise AccessPolicyError("access_permission_denied")
    return principal
