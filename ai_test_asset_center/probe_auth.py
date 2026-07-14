"""Probe authentication and fixture utilities.
Extracted from grounded_probe_executor.py.
"""
from __future__ import annotations

import json, os, re, time, urllib.parse
from pathlib import Path
from typing import Any

from .probe_http import (
    _join_url, _http_request, _json_path_get, _cookie_header_from_response,
    _redact, _safe_payload_summary, _now, _read_json, _write_json,
    SENSITIVE_FIELD_RE,
)

# Re-exported from grounded_probe_executor constants
AUTH_BOUNDARY_RISKS = {"auth_boundary_probe", "anonymous_auth_boundary_probe", "cross_tenant_auth_boundary_probe", "role_downgrade_auth_boundary_probe"}
FIXTURE_BACKED_READ_RISKS = AUTH_BOUNDARY_RISKS | {"ownership_scope_probe"}
AUTH_HEADER_NAMES = ["Authorization", "Cookie", "X-Tenant-Id", "X-Org-Id", "X-Workspace-Id"]
READ_METHODS = {"GET", "HEAD"}


def _headers_from_config(config: dict[str, Any]) -> dict[str, str]:
    headers = dict(config.get("default_headers") or {})
    token = str(config.get("bearer_token") or os.environ.get("QUALIBUG_BEARER_TOKEN") or "")
    tenant = str(config.get("tenant_id") or os.environ.get("QUALIBUG_TENANT_ID") or "")
    if token and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {token}"
    if tenant and "X-Tenant-Id" not in headers:
        headers["X-Tenant-Id"] = tenant
    return {str(k): str(v) for k, v in headers.items()}


def _fixture_control_headers(config: dict[str, Any]) -> dict[str, str]:
    """Return privileged/test-environment headers for fixture setup and cleanup."""
    headers = _headers_from_config(config)
    fixture_token = str(config.get("fixture_bearer_token") or config.get("admin_token") or "")
    if fixture_token:
        headers["Authorization"] = f"Bearer {fixture_token}"
    return headers


def _negative_headers(headers: dict[str, str], names: list[str]) -> dict[str, str]:
    """Strip auth headers to simulate an unauthenticated or restricted request."""
    result = dict(headers or {})
    for name in names:
        result.pop(name, None)
    return result


def _auth_boundary_plan(probe: dict[str, Any]) -> dict[str, Any]:
    """Extract auth boundary testing configuration from a probe."""
    plan = {}
    for key in ("actor_pair", "control_actor", "treatment_actor", "auth_boundary_kind"):
        value = probe.get(key)
        if value:
            plan[key] = value
    return plan


def _is_auth_boundary_risk(probe: dict[str, Any] | None = None, risk_type: str = "") -> bool:
    risk = str(risk_type or "").lower()
    if not risk and probe:
        risk = str(probe.get("risk_type") or "").lower()
    return risk in AUTH_BOUNDARY_RISKS or bool(_auth_boundary_plan(probe or {}))


def _fixture_backed_read_probe(probe: dict[str, Any], method: str = "", path: str = "") -> bool:
    m = str(method or probe.get("method", "")).upper()
    p = str(path or probe.get("path", ""))
    if m not in READ_METHODS or not re.search(r"\{[^{}]+\}", p):
        return False
    risk = str(probe.get("risk_type") or "").lower()
    return risk in FIXTURE_BACKED_READ_RISKS or _is_auth_boundary_risk(probe, risk)


def _read_fixture_setup_approval(config: dict[str, Any], base_url: str, options: dict[str, Any]) -> tuple[bool, str]:
    """Check if fixture setup is approved for this target."""
    approved = bool(options.get("approved_sandbox_execution") or options.get("allow_write_sandbox"))
    if not approved:
        return False, "fixture_setup_not_approved"
    base = str(base_url or "").strip()
    approved_urls = config.get("approved_base_urls") or []
    if base and approved_urls and base not in approved_urls:
        return False, f"base_url_not_approved:{base}"
    return True, ""


def _login_account(
    base_url: str,
    auth_flow: dict[str, Any],
    account: dict[str, Any],
    timeout: float,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Execute a login flow and return auth headers + response data."""
    path = str(auth_flow.get("path") or "/api/auth/login")
    method = str(auth_flow.get("method") or "POST").upper()
    body = dict(auth_flow.get("body") or {})
    username_field = str(auth_flow.get("username_field") or "username")
    password_field = str(auth_flow.get("password_field") or "password")
    body[username_field] = str(account.get("username") or account.get("email") or "")
    body[password_field] = str(account.get("password") or "")
    
    url = _join_url(base_url, path)
    resp = _http_request(method, url, {}, body, timeout=timeout)
    
    headers: dict[str, str] = {}
    token_path = str(auth_flow.get("token_path") or "token")
    token = _json_path_get(resp.get("payload"), token_path)
    if token and isinstance(token, str) and token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    
    cookie = _cookie_header_from_response(resp)
    if cookie:
        headers["Cookie"] = cookie.split("=", 1)[1] if "=" in cookie else cookie
    
    return headers, resp


def _materialize_account_auth(config: dict[str, Any], base_url: str, timeout: float) -> dict[str, Any]:
    """Authenticate all configured test accounts and return token map."""
    accounts = config.get("test_accounts") or config.get("accounts") or []
    if not isinstance(accounts, list):
        accounts = []
    auth_flow = dict(config.get("auth_flow") or config.get("login_flow") or {})
    if not auth_flow.get("path"):
        return {"tokens": {}, "accounts": accounts}
    
    tokens: dict[str, str] = {}
    for account in accounts:
        if not isinstance(account, dict):
            continue
        role = str(account.get("role") or account.get("username") or "")
        try:
            headers, _ = _login_account(base_url, auth_flow, account, timeout)
            token = headers.get("Authorization", "").replace("Bearer ", "")
            if token:
                tokens[role] = token
        except Exception:
            pass
    
    return {"tokens": tokens, "accounts": accounts}


def _has_business_data(value: Any) -> bool:
    """Check if a response contains meaningful business data."""
    if not value or value in (None, "", [], {}):
        return False
    if isinstance(value, str):
        return len(value.strip()) > 2
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


def _find_sensitive_keys(value: Any, prefix: str = "") -> list[str]:
    """Recursively find keys matching sensitive field patterns."""
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if SENSITIVE_FIELD_RE.search(str(k)):
                found.append(path)
            found.extend(_find_sensitive_keys(v, path))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            found.extend(_find_sensitive_keys(item, f"{prefix}[{i}]"))
    return found
