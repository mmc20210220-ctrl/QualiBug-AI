"""Tenant authentication helpers for the private-pilot HTTP surface."""
from __future__ import annotations

import os
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


def _actor(headers: Any) -> dict[str, str] | None:
    name = str(headers.get("X-QualiBug-Actor") or headers.get("x-qualibug-actor") or "").strip()
    role = str(headers.get("X-QualiBug-Role") or headers.get("x-qualibug-role") or "").strip()
    if not name or not role:
        return None
    return {"name": name[:120], "role": role[:64]}


def _parse_project_scopes(raw: str) -> tuple[set[str], bool]:
    items = [item.strip() for item in str(raw or "").replace(";", ",").split(",") if item.strip()]
    wildcard = any(item == "*" for item in items)
    return {_safe_project_id(item) for item in items if item != "*"}, wildcard


def _tenant_from_headers(headers: dict, *, root: Path | None = None) -> str:
    """Resolve tenant identity, rejecting invalid explicit credentials."""
    # Credential precedence is fail-closed: once a caller supplies a higher-
    # priority credential, it must authenticate and cannot fall through to a
    # lower-priority credential or the local development tenant.
    auth_present = "Authorization" in headers or "authorization" in headers
    auth = str(headers.get("Authorization") or headers.get("authorization") or "").strip()
    if auth_present:
        if not auth.startswith("Bearer ") or not auth[7:].strip():
            raise TenantAuthenticationError("invalid bearer token")
        try:
            payload = jwt_auth.verify_token(auth[7:].strip())
        except Exception as exc:
            raise TenantAuthenticationError(f"bearer token verification failed: {exc}") from exc
        tenant_id = str(payload.get("sub") or "").strip() if isinstance(payload, dict) else ""
        if not tenant_id:
            raise TenantAuthenticationError("invalid bearer token")
        return tenant_id
    # 2. HttpOnly Cookie (set by /api/auth/login) — preferred over localStorage
    #    because it is not readable by JavaScript, mitigating XSS token theft.
    cookie = headers.get("Cookie") or headers.get("cookie") or ""
    if cookie:
        from http.cookies import SimpleCookie
        try:
            ck = SimpleCookie()
            ck.load(cookie)
            morsel = ck.get("qualibug_token")
            if morsel:
                payload = jwt_auth.verify_token(morsel.value)
                tenant_id = str(payload.get("sub") or "").strip() if isinstance(payload, dict) else ""
                if not tenant_id:
                    raise TenantAuthenticationError("invalid cookie token")
                return tenant_id
        except TenantAuthenticationError:
            raise
        except Exception as exc:
            raise TenantAuthenticationError(f"cookie token verification failed: {exc}") from exc
    # 3. API Key
    api_key_present = "X-API-Key" in headers or "x-api-key" in headers
    api_key = str(headers.get("X-API-Key") or headers.get("x-api-key") or "").strip()
    if api_key_present:
        if not api_key:
            raise TenantAuthenticationError("invalid API key")
        try:
            tenant_id = str(db_persist.verify_api_key(root or _root(), api_key) or "").strip()
        except Exception as exc:
            raise TenantAuthenticationError(f"API key verification failed: {exc}") from exc
        if not tenant_id:
            raise TenantAuthenticationError("invalid API key")
        return tenant_id
    return _current_tenant()
