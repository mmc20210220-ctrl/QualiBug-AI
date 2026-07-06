"""Policy-driven tenant and project authorization primitives.

The module intentionally has no fixed role names. A deployment supplies opaque
permission strings and project scope through configuration or an identity
provider adapter. This keeps authorization policy customer-controlled.
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


def parse_token_policy(value: str | dict[str, Any]) -> dict[str, AccessPrincipal]:
    """Parse a deployment-owned token map without retaining raw token values."""
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise AccessPolicyError("access_policy_json_invalid") from exc
    else:
        payload = value
    if not isinstance(payload, dict):
        raise AccessPolicyError("access_policy_object_required")
    result: dict[str, AccessPrincipal] = {}
    for token, record in payload.items():
        token_value = _text(token, 1024)
        data = record if isinstance(record, dict) else {}
        principal_id = _text(data.get("principal_id"))
        tenant_id = _text(data.get("tenant_id"))
        permissions = _strings(data.get("permissions"))
        project_ids = _strings(data.get("project_ids"))
        if not token_value or not principal_id or not tenant_id or not permissions:
            raise AccessPolicyError("access_policy_principal_invalid")
        result[token_value] = AccessPrincipal(
            principal_id=principal_id,
            tenant_id=tenant_id,
            permissions=permissions,
            project_ids=project_ids,
        )
    return result


def authenticate_token(token: str, policy: dict[str, AccessPrincipal]) -> AccessPrincipal | None:
    """Constant-time lookup across configured opaque credentials."""
    supplied = _text(token, 1024)
    for configured, principal in policy.items():
        if secrets.compare_digest(supplied, configured):
            return principal
    return None


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
