"""Tenant authentication helpers for the private-pilot HTTP surface.

Tenant identity, actor identity and role are resolved from one authenticated
principal. Raw actor/role/project-scope request headers are never authorization
authorities.
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


# Retained as a compatibility constant for clients/proxies. The private service
# no longer trusts this header as an authorization source.
PROJECT_SCOPE_HEADER = "X-QualiBug-Project-Scopes"


class TenantAuthenticationError(Exception):
    """An explicitly supplied tenant credential could not be authenticated."""


def _current_tenant() -> str:
    return os.environ.get("QUALIBUG_TENANT", "default")


def _local_development_principal() -> dict[str, str] | None:
    enabled = os.environ.get("QUALIBUG_LOCAL_DEV_ACTOR", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not enabled or os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") == "1":
        return None
    tenant_id = _current_tenant().strip()
    if not tenant_id:
        return None
    return {
        "tenant_id": tenant_id,
        "name": os.environ.get("QUALIBUG_LOCAL_ACTOR", "local_dev").strip()[:120]
        or "local_dev",
        "role": os.environ.get("QUALIBUG_LOCAL_ROLE", "viewer").strip()[:64]
        or "viewer",
        "auth_type": "local_development",
    }


def _principal_from_headers(
    headers: Any,
    *,
    root: Path | None = None,
) -> dict[str, str]:
    """Authenticate one request and return its canonical principal.

    Credential precedence is fail-closed. Once a higher-priority credential is
    present, it must authenticate and cannot fall through to another mechanism.
    """

    mapping = dict(headers)
    auth_present = "Authorization" in mapping or "authorization" in mapping
    auth = str(mapping.get("Authorization") or mapping.get("authorization") or "").strip()
    if auth_present:
        if not auth.startswith("Bearer ") or not auth[7:].strip():
            raise TenantAuthenticationError("invalid bearer token")
        try:
            payload = jwt_auth.verify_token(auth[7:].strip())
        except Exception as exc:
            raise TenantAuthenticationError(f"bearer token verification failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise TenantAuthenticationError("invalid bearer token")
        tenant_id = str(payload.get("sub") or "").strip()
        role = str(payload.get("role") or "").strip()
        if not tenant_id or not role:
            raise TenantAuthenticationError("bearer token principal is incomplete")
        return {
            "tenant_id": tenant_id,
            "name": str(payload.get("username") or payload.get("actor") or tenant_id).strip()[:120]
            or tenant_id[:120],
            "role": role[:64],
            "auth_type": "bearer",
        }

    cookie = str(mapping.get("Cookie") or mapping.get("cookie") or "")
    if cookie:
        try:
            parsed_cookie = SimpleCookie()
            parsed_cookie.load(cookie)
            morsel = parsed_cookie.get("qualibug_token")
            if morsel is not None:
                payload = jwt_auth.verify_token(morsel.value)
                if not isinstance(payload, dict):
                    raise TenantAuthenticationError("invalid cookie token")
                tenant_id = str(payload.get("sub") or "").strip()
                role = str(payload.get("role") or "").strip()
                if not tenant_id or not role:
                    raise TenantAuthenticationError("cookie token principal is incomplete")
                return {
                    "tenant_id": tenant_id,
                    "name": str(
                        payload.get("username") or payload.get("actor") or tenant_id
                    ).strip()[:120]
                    or tenant_id[:120],
                    "role": role[:64],
                    "auth_type": "cookie",
                }
        except TenantAuthenticationError:
            raise
        except Exception as exc:
            raise TenantAuthenticationError(f"cookie token verification failed: {exc}") from exc

    api_key_present = "X-API-Key" in mapping or "x-api-key" in mapping
    api_key = str(mapping.get("X-API-Key") or mapping.get("x-api-key") or "").strip()
    if api_key_present:
        if not api_key:
            raise TenantAuthenticationError("invalid API key")
        try:
            account = db_persist.authenticate_tenant(root or _root(), api_key, "")
        except Exception as exc:
            raise TenantAuthenticationError(f"API key verification failed: {exc}") from exc
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
        }

    local = _local_development_principal()
    if local is not None:
        return local
    raise TenantAuthenticationError("authentication credential is required")


def _actor(headers: Any, *, root: Path | None = None) -> dict[str, str] | None:
    principal = _principal_from_headers(headers, root=root)
    return {
        "name": principal["name"],
        "role": principal["role"],
    }


def _parse_project_scopes(raw: str) -> tuple[set[str], bool]:
    """Parse legacy scope text for diagnostics only.

    Authorization no longer consumes caller-provided scope headers. Keeping the
    parser avoids breaking reporting surfaces that display a trusted proxy's
    declared scope, while strict project-id validation prevents traversal.
    """

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
