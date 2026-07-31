"""Tenant authentication helpers for the private-pilot HTTP surface.

Tenant identity, actor identity and role are resolved from one authenticated
principal. JWT/cookie claims are accepted only when their role, username and
session version still match the current server-side account state.
"""
from __future__ import annotations

import os
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

from . import db_persistence as db_persist
from . import jwt_auth
from .private_pilot_project_assets import _root
from .real_project_onboarding import _safe_project_id

PROJECT_SCOPE_HEADER = "X-QualiBug-Project-Scopes"


class TenantAuthenticationError(Exception):
    """An explicitly supplied tenant credential could not be authenticated."""


def _current_tenant() -> str:
    return os.environ.get("QUALIBUG_TENANT", "default")


def _local_development_principal() -> dict[str, str]:
    enabled = os.environ.get("QUALIBUG_LOCAL_DEV_ACTOR", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not enabled or os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") == "1":
        raise TenantAuthenticationError("authentication credential is required")
    tenant_id = _current_tenant().strip()
    if not tenant_id:
        raise TenantAuthenticationError("local development tenant is not configured")
    return {
        "tenant_id": tenant_id,
        "name": os.environ.get("QUALIBUG_LOCAL_ACTOR", "local_dev").strip()[:120]
        or "local_dev",
        "role": os.environ.get("QUALIBUG_LOCAL_ROLE", "viewer").strip()[:64]
        or "viewer",
        "auth_type": "local_development",
        "session_version": "0",
    }


def _validated_token_principal(
    token: str,
    *,
    auth_type: str,
    root: Path,
) -> dict[str, str]:
    payload = jwt_auth.verify_token(token)
    if not isinstance(payload, dict):
        raise TenantAuthenticationError(f"invalid {auth_type} token")
    tenant_id = str(payload.get("sub") or "").strip()
    try:
        token_version = int(payload.get("ver") or 0)
    except (TypeError, ValueError) as exc:
        raise TenantAuthenticationError(
            f"{auth_type} token session version is invalid"
        ) from exc
    state = db_persist.get_tenant_auth_state(root, tenant_id)
    if not isinstance(state, dict):
        raise TenantAuthenticationError(f"{auth_type} account no longer exists")
    current_version = int(state.get("session_version") or 0)
    if token_version != current_version:
        raise TenantAuthenticationError(f"{auth_type} session has been revoked")
    token_role = str(payload.get("role") or "").strip()
    current_role = str(state.get("role") or "viewer").strip()
    token_username = str(payload.get("username") or "").strip()
    current_username = str(state.get("username") or tenant_id).strip()
    if token_role != current_role or token_username != current_username:
        raise TenantAuthenticationError(
            f"{auth_type} account authorization changed; sign in again"
        )
    return {
        "tenant_id": tenant_id,
        "name": current_username[:120] or tenant_id[:120],
        "role": current_role[:64],
        "auth_type": auth_type,
        "session_version": str(current_version),
    }


def _principal_from_headers(
    headers: Any,
    *,
    root: Path | None = None,
) -> dict[str, str]:
    """Authenticate one request and return its current canonical principal."""

    resolved_root = (root or _root()).resolve()
    mapping = dict(headers)
    auth_present = "Authorization" in mapping or "authorization" in mapping
    auth = str(
        mapping.get("Authorization") or mapping.get("authorization") or ""
    ).strip()
    if auth_present:
        if not auth.startswith("Bearer ") or not auth[7:].strip():
            raise TenantAuthenticationError("invalid bearer token")
        return _validated_token_principal(
            auth[7:].strip(),
            auth_type="bearer",
            root=resolved_root,
        )

    cookie = str(mapping.get("Cookie") or mapping.get("cookie") or "")
    if cookie:
        try:
            parsed_cookie = SimpleCookie()
            parsed_cookie.load(cookie)
        except Exception as exc:
            raise TenantAuthenticationError("cookie header is invalid") from exc
        morsel = parsed_cookie.get("qualibug_token")
        if morsel is not None:
            return _validated_token_principal(
                morsel.value,
                auth_type="cookie",
                root=resolved_root,
            )

    api_key_present = "X-API-Key" in mapping or "x-api-key" in mapping
    api_key = str(
        mapping.get("X-API-Key") or mapping.get("x-api-key") or ""
    ).strip()
    if api_key_present:
        if not api_key:
            raise TenantAuthenticationError("invalid API key")
        account = db_persist.authenticate_tenant(resolved_root, api_key, "")
        if not isinstance(account, dict):
            raise TenantAuthenticationError("invalid API key")
        tenant_id = str(account.get("tenant_id") or "").strip()
        role = str(account.get("role") or "").strip()
        if not tenant_id or not role:
            raise TenantAuthenticationError("API key principal is incomplete")
        return {
            "tenant_id": tenant_id,
            "name": str(account.get("username") or tenant_id).strip()[:120]
            or tenant_id[:120],
            "role": role[:64],
            "auth_type": "api_key",
            "session_version": str(account.get("session_version") or 1),
        }

    return _local_development_principal()


def _actor(headers: Any, *, root: Path | None = None) -> dict[str, str] | None:
    principal = _principal_from_headers(headers, root=root)
    return {
        "name": principal["name"],
        "role": principal["role"],
    }


def _parse_project_scopes(raw: str) -> tuple[set[str], bool]:
    """Parse legacy scope text for diagnostics only."""

    items = [
        item.strip()
        for item in str(raw or "").replace(";", ",").split(",")
        if item.strip()
    ]
    wildcard = any(item == "*" for item in items)
    return {_safe_project_id(item) for item in items if item != "*"}, wildcard


def _tenant_from_headers(headers: dict, *, root: Path | None = None) -> str:
    return _principal_from_headers(headers, root=root)["tenant_id"]


__all__ = [
    "PROJECT_SCOPE_HEADER",
    "TenantAuthenticationError",
    "_actor",
    "_current_tenant",
    "_parse_project_scopes",
    "_principal_from_headers",
    "_tenant_from_headers",
]
