from __future__ import annotations

"""Customer URL / authentication / session preflight.

This module sits before runtime probe execution.  It answers the practical
onboarding questions customers usually ask first:

* can the configured environment URL be parsed and safely normalized?
* can its host be resolved from the runner environment?
* does the HTTP edge respond at all, even if it returns 401/403?
* can QualiBug obtain Authorization/Cookie/session material from configured
  test accounts or static headers?
* can an authenticated session be verified against a lightweight health/me
  endpoint when the customer provides one?

No raw tokens, cookies or passwords are returned in reports.
"""

import json
import re
import socket
import time
import urllib.parse
from typing import Any, Callable

Requester = Callable[[str, str, dict[str, str], Any, float], dict[str, Any]]
Resolver = Callable[[str, int | None], list[Any]]

AUTH_HEADER_NAMES = {"authorization", "cookie", "x-api-key", "x-auth-token", "x-session-id", "x-access-token"}
TOKEN_RESPONSE_HEADER_NAMES = ("authorization", "x-auth-token", "x-access-token", "x-session-id")
CSRF_RESPONSE_HEADER_NAMES = ("x-csrf-token", "x-xsrf-token", "csrf-token", "x-csrf")
CSRF_COOKIE_NAMES = {"xsrf-token", "csrf-token", "csrftoken", "x-csrf-token"}
TOKEN_PATHS = (
    "token",
    "access_token",
    "jwt",
    "id_token",
    "data.token",
    "data.access_token",
    "data.accessToken",
    "result.token",
    "result.access_token",
    "auth.token",
    "authentication.token",
)
SESSION_HEALTH_PATH_KEYS = (
    "session_health_path",
    "health_check_path",
    "verify_path",
    "me_path",
    "profile_path",
    "whoami_path",
)
CSRF_TOKEN_PATHS = (
    "csrf_token",
    "csrfToken",
    "xsrfToken",
    "data.csrf_token",
    "data.csrfToken",
    "result.csrf_token",
    "result.csrfToken",
)
PLACEHOLDER_RE = re.compile(r"<\s*(?:FILL|TODO|REQUIRED|SANDBOX|REPLACE)[^>]*>", re.I)
SECRET_KEY_RE = re.compile(r"(?:password|passwd|secret|token|authorization|cookie|api[_-]?key|session)", re.I)
SAFE_METADATA_KEYS = {
    "token_json_path",
    "token_source",
    "csrf_source",
    "derived_header_names",
    "injected_header_names",
    "injected_body_fields",
    "header_names",
}


def _redact(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, key) for v in value[:50]]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    if str(key) in SAFE_METADATA_KEYS:
        return text[:500]
    if SECRET_KEY_RE.search(str(key)) or SECRET_KEY_RE.search(text):
        return f"<REDACTED:{len(text)}>"
    return text[:500]


def _contains_placeholder(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_placeholder(k) or _contains_placeholder(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_placeholder(v) for v in value)
    if value is None:
        return False
    return bool(PLACEHOLDER_RE.search(str(value)))


def _join_url(base_url: str, path: str) -> str:
    base = str(base_url or "").rstrip("/")
    p = str(path or "")
    if re.match(r"^https?://", p, re.I):
        return p
    if not base:
        return p
    return base + "/" + p.lstrip("/")


def _parse_url(base_url: str) -> dict[str, Any]:
    raw = str(base_url or "").strip()
    if not raw:
        return {"ok": False, "message": "base_url is empty", "raw_present": False}
    if _contains_placeholder(raw):
        return {"ok": False, "message": "base_url contains unresolved placeholder", "raw_present": True}
    parsed = urllib.parse.urlparse(raw)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    path = parsed.path or "/"
    reasons: list[str] = []
    if scheme not in {"http", "https"}:
        reasons.append("scheme_must_be_http_or_https")
    if not host:
        reasons.append("host_missing")
    ok = not reasons
    normalized = urllib.parse.urlunparse((scheme, parsed.netloc, path, "", "", "")) if ok else raw
    return {
        "ok": ok,
        "message": "base_url parsed" if ok else ";".join(reasons),
        "scheme": scheme,
        "host": host,
        "port": port,
        "path": path,
        "normalized_url": normalized,
        "raw_present": True,
    }


def _resolve_host(host: str, port: int | None, resolver: Resolver | None = None) -> dict[str, Any]:
    if not host:
        return {"ok": False, "skipped": True, "message": "host missing"}
    started = time.time()
    try:
        if resolver is not None:
            answers = resolver(host, port)
        else:
            answers = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
        ips: list[str] = []
        for answer in answers or []:
            if isinstance(answer, tuple) and len(answer) >= 5 and isinstance(answer[4], tuple):
                ip = str(answer[4][0])
            else:
                ip = str(answer)
            if ip and ip not in ips:
                ips.append(ip)
        return {
            "ok": bool(ips),
            "message": "host resolved" if ips else "host resolver returned no address",
            "address_count": len(ips),
            "sample_addresses": ips[:5],
            "duration_ms": int((time.time() - started) * 1000),
        }
    except Exception as exc:  # pragma: no cover - defensive guard for real resolver
        return {
            "ok": False,
            "message": "host resolution failed",
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": int((time.time() - started) * 1000),
        }


def _http_edge_probe(base_url: str, timeout_seconds: float, requester: Requester | None, *, safety_skip_http: bool = False, skip_reason: str = "") -> dict[str, Any]:
    if safety_skip_http:
        return {"ok": False, "skipped": True, "message": skip_reason or "http probe skipped by safety policy"}
    if not base_url:
        return {"ok": False, "skipped": True, "message": "base_url not configured"}
    if requester is None:
        return {"ok": False, "skipped": True, "message": "requester not provided"}
    try:
        resp = requester("GET", _join_url(base_url, "/"), {}, None, min(float(timeout_seconds or 10.0), 5.0))
    except Exception as exc:  # pragma: no cover - defensive guard around injected requester
        return {"ok": False, "status_code": None, "error": f"{type(exc).__name__}: {exc}", "message": "http edge probe raised an exception"}
    code = resp.get("status_code")
    reachable = isinstance(code, int) and 100 <= int(code) < 500
    return {
        "ok": reachable,
        "status_code": code,
        "error": resp.get("error"),
        "duration_ms": resp.get("duration_ms"),
        "message": "HTTP edge is reachable" if reachable else "HTTP edge is not reachable",
    }


def _jsonish_payload(resp: dict[str, Any]) -> Any:
    if "payload" in resp:
        return resp.get("payload")
    raw = resp.get("body") or resp.get("text") or resp.get("content")
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return raw[:2000]
    return None


def _json_path_get(payload: Any, dotted_path: str) -> Any:
    cur = payload
    for raw_part in [p for p in str(dotted_path or "").split(".") if p]:
        part = raw_part
        while part:
            m = re.match(r"^([^\[]+)(?:\[(\d+)\])?(.*)$", part)
            if not m:
                return None
            key, idx, rest = m.group(1), m.group(2), m.group(3)
            if key:
                if not isinstance(cur, dict) or key not in cur:
                    return None
                cur = cur[key]
            if idx is not None:
                if not isinstance(cur, list):
                    return None
                i = int(idx)
                if i >= len(cur):
                    return None
                cur = cur[i]
            part = rest
    return cur


def _extract_token(payload: Any, preferred_path: str = "") -> tuple[str | None, str | None]:
    paths = [preferred_path] if preferred_path else []
    paths.extend(p for p in TOKEN_PATHS if p not in paths)
    for path in paths:
        value = _json_path_get(payload, path)
        if value:
            return str(value), path
    return None, None


def _headers(resp: dict[str, Any]) -> dict[str, str]:
    raw = resp.get("headers") if isinstance(resp.get("headers"), dict) else {}
    return {str(k): str(v) for k, v in raw.items()}


def _header_get(headers: dict[str, str], name: str) -> str:
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return ""


def _cookie_header_from_response(resp: dict[str, Any]) -> str:
    headers = _headers(resp)
    raw_values = [v for k, v in headers.items() if k.lower() == "set-cookie"]
    cookies: list[str] = []
    for raw in raw_values:
        for chunk in re.split(r",\s*(?=[A-Za-z0-9_\-]+=)", raw):
            first = chunk.split(";", 1)[0].strip()
            if first:
                cookies.append(first)
    return "; ".join(dict.fromkeys(cookies))


def _cookie_map(cookie_header: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in str(cookie_header or "").split(";"):
        if "=" not in chunk:
            continue
        name, value = chunk.split("=", 1)
        name = name.strip()
        if name:
            out[name.lower()] = value.strip()
    return out


def _merge_cookie_headers(*cookie_headers: str) -> str:
    merged: dict[str, str] = {}
    order: list[str] = []
    for header in cookie_headers:
        for chunk in str(header or "").split(";"):
            if "=" not in chunk:
                continue
            name, value = chunk.split("=", 1)
            clean = name.strip()
            if not clean:
                continue
            key = clean.lower()
            if key not in merged:
                order.append(key)
            merged[key] = f"{clean}={value.strip()}"
    return "; ".join(merged[key] for key in order if key in merged)


def _extract_header_token(resp: dict[str, Any], preferred_header: str = "") -> tuple[str | None, str | None]:
    headers = _headers(resp)
    candidates = [preferred_header.lower()] if preferred_header else []
    candidates.extend(h for h in TOKEN_RESPONSE_HEADER_NAMES if h not in candidates)
    for wanted in candidates:
        if not wanted:
            continue
        for name, value in headers.items():
            if name.lower() == wanted and value:
                return str(value), name
    return None, None


def _format_token_header_value(token: str, token_header: str, token_prefix: str) -> str:
    value = str(token or "")
    if not value:
        return ""
    if token_header.lower() != "authorization" or not token_prefix:
        return value
    if re.match(r"^[A-Za-z]+\s+", value):
        return value
    return f"{token_prefix} {value}"


def _extract_csrf_from_html(text: str) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    patterns = (
        r"<meta[^>]+name=[\"'](?:csrf-token|_csrf|csrf)[\"'][^>]+content=[\"']([^\"']+)[\"']",
        r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+name=[\"'](?:csrf-token|_csrf|csrf)[\"']",
        r"<input[^>]+name=[\"'](?:_csrf|csrf_token|csrf)[\"'][^>]+value=[\"']([^\"']+)[\"']",
        r"<input[^>]+value=[\"']([^\"']+)[\"'][^>]+name=[\"'](?:_csrf|csrf_token|csrf)[\"']",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1), "html"
    return None, None


def _extract_csrf_token(resp: dict[str, Any], preferred_path: str = "") -> tuple[str | None, str | None]:
    payload = _jsonish_payload(resp)
    paths = [preferred_path] if preferred_path else []
    paths.extend(path for path in CSRF_TOKEN_PATHS if path not in paths)
    for path in paths:
        value = _json_path_get(payload, path)
        if value:
            return str(value), path
    headers = _headers(resp)
    for wanted in CSRF_RESPONSE_HEADER_NAMES:
        for name, value in headers.items():
            if name.lower() == wanted and value:
                return str(value), name
    if isinstance(payload, str):
        html_token, html_source = _extract_csrf_from_html(payload)
        if html_token:
            return html_token, html_source
    cookie_header = _cookie_header_from_response(resp)
    cookies = _cookie_map(cookie_header)
    for name in CSRF_COOKIE_NAMES:
        if cookies.get(name):
            return urllib.parse.unquote(cookies[name]), f"cookie:{name}"
    return None, None


def _login_expected_statuses(auth_flow: dict[str, Any]) -> set[int]:
    raw = auth_flow.get("login_expected_statuses") or auth_flow.get("login_success_statuses")
    if isinstance(raw, list):
        out = {int(x) for x in raw if str(x).isdigit()}
        return out or set(range(200, 300))
    return set(range(200, 300))


def _bootstrap_auth_context(
    base_url: str,
    auth_flow: dict[str, Any],
    timeout_seconds: float,
    requester: Requester | None,
) -> tuple[dict[str, str], dict[str, Any], dict[str, Any]]:
    path = str(
        auth_flow.get("bootstrap_path")
        or auth_flow.get("csrf_path")
        or auth_flow.get("csrf_token_path")
        or auth_flow.get("handshake_path")
        or ""
    )
    if not path:
        return {}, {}, {"attempted": False, "configured": False}
    if requester is None:
        return {}, {}, {"attempted": False, "configured": True, "error": "requester_not_provided"}

    method = str(auth_flow.get("bootstrap_method") or "GET").upper()
    headers = dict(auth_flow.get("bootstrap_headers") or {}) if isinstance(auth_flow.get("bootstrap_headers"), dict) else {}
    started = time.time()
    try:
        resp = requester(method, _join_url(base_url, path), {str(k): str(v) for k, v in headers.items()}, None, min(float(timeout_seconds or 10.0), 10.0))
    except Exception as exc:  # pragma: no cover - defensive guard around injected requester
        return {}, {}, {
            "attempted": True,
            "configured": True,
            "path": path,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": int((time.time() - started) * 1000),
        }

    csrf_token, csrf_source = _extract_csrf_token(resp, str(auth_flow.get("csrf_json_path") or auth_flow.get("csrf_path_json") or ""))
    cookie = _cookie_header_from_response(resp)
    out_headers: dict[str, str] = {}
    out_body: dict[str, Any] = {}
    if cookie:
        out_headers["Cookie"] = cookie
    csrf_header_name = str(auth_flow.get("csrf_header_name") or "X-CSRF-Token")
    csrf_body_field = str(auth_flow.get("csrf_body_field") or "")
    inject_body = bool(auth_flow.get("csrf_in_body") or csrf_body_field)
    if csrf_token and csrf_header_name:
        out_headers[csrf_header_name] = csrf_token
    if csrf_token and inject_body:
        out_body[csrf_body_field or "csrf_token"] = csrf_token

    code = resp.get("status_code")
    return out_headers, out_body, {
        "attempted": True,
        "configured": True,
        "path": path,
        "status_code": code,
        "status_ok": isinstance(code, int) and 100 <= int(code) < 500,
        "csrf_acquired": bool(csrf_token),
        "csrf_source": csrf_source,
        "cookie_acquired": bool(cookie),
        "injected_header_names": sorted(out_headers.keys()),
        "injected_body_fields": sorted(out_body.keys()),
        "duration_ms": resp.get("duration_ms") or int((time.time() - started) * 1000),
    }


def _configured_auth_headers(config: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    raw_headers = config.get("default_headers") if isinstance(config.get("default_headers"), dict) else {}
    headers.update({str(k): str(v) for k, v in raw_headers.items()})
    bearer = str(config.get("bearer_token") or "")
    api_key = str(config.get("api_key") or "")
    cookie = str(config.get("cookie") or config.get("session_cookie") or "")
    if bearer and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {bearer}"
    if api_key and "X-API-Key" not in headers:
        headers["X-API-Key"] = api_key
    if cookie and "Cookie" not in headers:
        headers["Cookie"] = cookie
    return {k: v for k, v in headers.items() if v}


def _auth_header_summary(headers: dict[str, str]) -> dict[str, Any]:
    names = sorted(k for k, v in headers.items() if v and k.lower() in AUTH_HEADER_NAMES)
    return {
        "configured": bool(names),
        "header_names": names,
        "authorization_present": any(k.lower() == "authorization" for k in names),
        "cookie_present": any(k.lower() == "cookie" for k in names),
        "api_key_present": any(k.lower() == "x-api-key" for k in names),
    }


def _session_health_path(auth_flow: dict[str, Any], config: dict[str, Any]) -> str:
    for key in SESSION_HEALTH_PATH_KEYS:
        if auth_flow.get(key):
            return str(auth_flow.get(key))
    session = config.get("session") if isinstance(config.get("session"), dict) else {}
    for key in SESSION_HEALTH_PATH_KEYS:
        if session.get(key):
            return str(session.get(key))
    return str(config.get("session_health_path") or config.get("auth_health_path") or "")


def _expected_statuses(auth_flow: dict[str, Any], config: dict[str, Any]) -> set[int]:
    raw = auth_flow.get("session_health_expected_statuses") or auth_flow.get("expected_statuses") or config.get("session_health_expected_statuses")
    if isinstance(raw, list):
        out = {int(x) for x in raw if str(x).isdigit()}
        return out or set(range(200, 400))
    return set(range(200, 400))


def _verify_session(base_url: str, headers: dict[str, str], auth_flow: dict[str, Any], config: dict[str, Any], timeout_seconds: float, requester: Requester | None) -> dict[str, Any]:
    path = _session_health_path(auth_flow, config)
    if not path:
        return {"ok": False, "skipped": True, "message": "session health path not configured"}
    if requester is None:
        return {"ok": False, "skipped": True, "message": "requester not provided"}
    if not headers:
        return {"ok": False, "skipped": True, "message": "no auth headers available for session health probe", "path": path}
    try:
        resp = requester("GET", _join_url(base_url, path), headers, None, min(float(timeout_seconds or 10.0), 5.0))
    except Exception as exc:  # pragma: no cover - defensive guard around injected requester
        return {"ok": False, "path": path, "status_code": None, "error": f"{type(exc).__name__}: {exc}", "message": "session health probe raised an exception"}
    code = resp.get("status_code")
    expected = _expected_statuses(auth_flow, config)
    ok = isinstance(code, int) and int(code) in expected
    return {
        "ok": ok,
        "path": path,
        "status_code": code,
        "duration_ms": resp.get("duration_ms"),
        "message": "authenticated session health check passed" if ok else "authenticated session health check failed",
    }


def _login_account(
    base_url: str,
    auth_flow: dict[str, Any],
    account_name: str,
    account: dict[str, Any],
    timeout_seconds: float,
    requester: Requester | None,
) -> tuple[dict[str, str], dict[str, Any]]:
    if requester is None:
        return {}, {"account": account_name, "login_attempted": False, "error": "requester_not_provided"}
    login_path = str(auth_flow.get("login_path") or auth_flow.get("path") or "/login")
    method = str(auth_flow.get("method") or "POST").upper()
    username_field = str(auth_flow.get("username_field") or "username")
    password_field = str(auth_flow.get("password_field") or "password")
    tenant_field = str(auth_flow.get("tenant_field") or "")
    token_json_path = str(auth_flow.get("token_json_path") or auth_flow.get("token_path") or "")
    token_header = str(auth_flow.get("token_header_name") or "Authorization")
    token_prefix = str(auth_flow.get("token_header_prefix") or "Bearer")
    response_token_header = str(auth_flow.get("token_response_header") or auth_flow.get("token_header_response_name") or "")
    body_format = str(auth_flow.get("body_format") or auth_flow.get("request_body_format") or "json").lower()
    extra_body = dict(auth_flow.get("extra_body") or {}) if isinstance(auth_flow.get("extra_body"), dict) else {}
    bootstrap_headers, bootstrap_body, bootstrap_meta = _bootstrap_auth_context(base_url, auth_flow, timeout_seconds, requester)
    body = dict(extra_body)
    body.update(bootstrap_body)
    body[username_field] = account.get("username") or account.get("user") or account.get("login") or ""
    body[password_field] = account.get("password") or ""
    if tenant_field and account.get("tenant_id"):
        body[tenant_field] = account.get("tenant_id")
    headers = dict(auth_flow.get("headers") or {}) if isinstance(auth_flow.get("headers"), dict) else {}
    headers = {str(k): str(v) for k, v in headers.items()}
    bootstrap_cookie = bootstrap_headers.pop("Cookie", "")
    if bootstrap_cookie:
        headers["Cookie"] = _merge_cookie_headers(headers.get("Cookie", ""), bootstrap_cookie)
    headers.update({str(k): str(v) for k, v in bootstrap_headers.items()})
    if body_format in {"form", "urlencoded", "x-www-form-urlencoded", "form-urlencoded"}:
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif body_format == "json":
        headers.setdefault("Content-Type", "application/json")
    if not body[username_field] or not body[password_field]:
        return {}, {"account": account_name, "login_attempted": False, "error": "missing_username_or_password", "bootstrap": bootstrap_meta}
    started = time.time()
    try:
        resp = requester(method, _join_url(base_url, login_path), headers, body, min(float(timeout_seconds or 10.0), 10.0))
    except Exception as exc:  # pragma: no cover - defensive guard around injected requester
        return {}, {"account": account_name, "login_attempted": True, "error": f"{type(exc).__name__}: {exc}", "bootstrap": bootstrap_meta, "duration_ms": int((time.time() - started) * 1000)}
    payload = _jsonish_payload(resp)
    token, token_path = _extract_token(payload, token_json_path)
    token_source = token_path
    if not token:
        token, token_source = _extract_header_token(resp, response_token_header)
    response_cookie = _cookie_header_from_response(resp)
    cookie = _merge_cookie_headers(headers.get("Cookie", ""), response_cookie)
    derived_headers: dict[str, str] = {}
    if token:
        derived_headers[token_header] = _format_token_header_value(token, token_header, token_prefix)
    if cookie:
        derived_headers["Cookie"] = cookie
    csrf_header_name = str(auth_flow.get("csrf_header_name") or "X-CSRF-Token")
    if headers.get(csrf_header_name):
        derived_headers.setdefault(csrf_header_name, headers[csrf_header_name])
    if account.get("tenant_id"):
        tenant_header = str(auth_flow.get("tenant_header_name") or "X-Tenant-Id")
        derived_headers.setdefault(tenant_header, str(account.get("tenant_id")))
    code = resp.get("status_code")
    expected_login_statuses = _login_expected_statuses(auth_flow)
    status_ok = isinstance(code, int) and int(code) in expected_login_statuses
    meta = {
        "account": account_name,
        "role": account.get("role") or account_name,
        "login_attempted": True,
        "status_code": code,
        "status_ok": status_ok,
        "expected_statuses": sorted(expected_login_statuses),
        "request_body_format": body_format,
        "token_acquired": bool(token),
        "token_source": token_source,
        "token_json_path": token_path if token_path and token_path != token_source else token_path,
        "cookie_acquired": bool(response_cookie),
        "bootstrap_cookie_reused": bool(headers.get("Cookie") and not response_cookie),
        "csrf_acquired": bool(bootstrap_meta.get("csrf_acquired")),
        "bootstrap": bootstrap_meta,
        "derived_header_names": sorted(derived_headers.keys()),
        "duration_ms": resp.get("duration_ms") or int((time.time() - started) * 1000),
    }
    if not derived_headers:
        meta["error"] = "login_response_did_not_contain_token_or_cookie"
    return derived_headers, meta


def _probe_static_headers(base_url: str, config: dict[str, Any], auth_flow: dict[str, Any], timeout_seconds: float, requester: Requester | None) -> dict[str, Any]:
    headers = _configured_auth_headers(config)
    summary = _auth_header_summary(headers)
    health = _verify_session(base_url, headers, auth_flow, config, timeout_seconds, requester) if summary["configured"] else {"ok": False, "skipped": True, "message": "no static auth headers configured"}
    return {
        "mode": "static_headers",
        "configured": summary["configured"],
        "header_summary": summary,
        "session_health": health,
        "successful_session_count": 1 if bool(health.get("ok")) else 0,
        "session_health_verified_count": 1 if bool(health.get("ok")) else 0,
    }


def _probe_account_login(base_url: str, config: dict[str, Any], auth_flow: dict[str, Any], timeout_seconds: float, requester: Requester | None) -> dict[str, Any]:
    accounts = config.get("accounts") or config.get("test_accounts") or {}
    if not isinstance(accounts, dict) or not accounts:
        return {"mode": "account_login", "configured": False, "login_attempted": False, "blocked_reason": "no_accounts_configured", "events": []}
    if _contains_placeholder({"accounts": accounts, "auth_flow": auth_flow}):
        return {"mode": "account_login", "configured": True, "login_attempted": False, "blocked_reason": "auth_config_contains_unresolved_placeholders", "events": []}
    events: list[dict[str, Any]] = []
    resolved_headers: dict[str, dict[str, str]] = {}
    verified_count = 0
    token_count = 0
    cookie_count = 0
    csrf_count = 0
    for name, raw_account in accounts.items():
        if not isinstance(raw_account, dict):
            continue
        account = dict(raw_account)
        if account.get("anonymous") is True:
            resolved_headers[str(name)] = {}
            events.append({"account": str(name), "role": account.get("role") or str(name), "anonymous": True, "login_attempted": False})
            continue
        headers, meta = _login_account(base_url, auth_flow, str(name), account, timeout_seconds, requester)
        token_count += 1 if meta.get("token_acquired") else 0
        cookie_count += 1 if meta.get("cookie_acquired") else 0
        csrf_count += 1 if meta.get("csrf_acquired") else 0
        health = _verify_session(base_url, headers, auth_flow, config, timeout_seconds, requester) if headers else {"ok": False, "skipped": True, "message": "no auth headers derived"}
        if health.get("ok"):
            verified_count += 1
        meta["session_health"] = health
        events.append(meta)
        resolved_headers[str(name)] = headers
    successful = sum(1 for h in resolved_headers.values() if h)
    default_account = str(config.get("default_account") or config.get("default_role") or "")
    if not default_account or default_account not in resolved_headers or not resolved_headers.get(default_account):
        for name, headers in resolved_headers.items():
            if headers:
                default_account = name
                break
    derived_default_headers = dict(resolved_headers.get(default_account) or {})
    return {
        "mode": "account_login",
        "configured": True,
        "login_attempted": True,
        "default_account": default_account,
        "account_count": len([a for a in accounts.values() if isinstance(a, dict)]),
        "successful_session_count": successful,
        "token_acquired_count": token_count,
        "cookie_acquired_count": cookie_count,
        "csrf_token_acquired_count": csrf_count,
        "session_health_verified_count": verified_count,
        "derived_default_header_names": sorted(derived_default_headers.keys()),
        "events": events,
        "_resolved_account_headers": resolved_headers,
        "_derived_default_headers": derived_default_headers,
    }


def _auth_materialization_probe(
    *,
    base_url: str,
    config: dict[str, Any],
    timeout_seconds: float,
    requester: Requester | None,
    safety_skip_http: bool,
    skip_reason: str,
) -> dict[str, Any]:
    auth_flow = config.get("auth_flow") or config.get("login") or {}
    auth_flow = auth_flow if isinstance(auth_flow, dict) else {}
    static_headers = _configured_auth_headers(config)
    has_accounts = isinstance(config.get("accounts") or config.get("test_accounts"), dict) and bool(config.get("accounts") or config.get("test_accounts"))
    if safety_skip_http:
        return {
            "mode": "skipped",
            "configured": bool(static_headers or has_accounts or auth_flow),
            "blocked_reason": skip_reason or "auth probe skipped by safety policy",
            "static_headers": _auth_header_summary(static_headers),
            "events": [],
        }
    if not base_url:
        return {"mode": "none", "configured": False, "blocked_reason": "base_url_not_configured", "events": []}
    if has_accounts and auth_flow:
        return _probe_account_login(base_url, config, auth_flow, timeout_seconds, requester)
    static = _probe_static_headers(base_url, config, auth_flow, timeout_seconds, requester)
    if static.get("configured"):
        return static
    return {"mode": "none", "configured": False, "blocked_reason": "no_accounts_auth_flow_or_static_headers_configured", "events": []}


def _check(name: str, ok: bool, message: str, *, severity: str = "info", skipped: bool = False, **extra: Any) -> dict[str, Any]:
    status = "skipped" if skipped else ("passed" if ok else ("failed" if severity == "blocking" else "warning"))
    out = {"name": name, "ok": bool(ok), "status": status, "severity": severity, "message": message}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def build_runtime_connectivity_auth_preflight(
    *,
    config: dict[str, Any],
    base_url: str,
    execute_readonly: bool,
    allow_write_sandbox: bool,
    timeout_seconds: float = 10.0,
    requester: Requester | None = None,
    resolver: Resolver | None = None,
    safety_skip_http: bool = False,
    safety_skip_reason: str = "",
) -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else {}
    parsed = _parse_url(base_url)
    dns = _resolve_host(str(parsed.get("host") or ""), parsed.get("port"), resolver) if parsed.get("ok") else {"ok": False, "skipped": True, "message": "url parse failed"}
    http_edge = _http_edge_probe(base_url, timeout_seconds, requester, safety_skip_http=safety_skip_http or not bool(parsed.get("ok")), skip_reason=safety_skip_reason or "url parse failed")
    auth = _auth_materialization_probe(base_url=base_url, config=cfg, timeout_seconds=timeout_seconds, requester=requester, safety_skip_http=safety_skip_http or not bool(parsed.get("ok")), skip_reason=safety_skip_reason or "url parse failed")

    configured = bool(auth.get("configured"))
    token_or_cookie = int(auth.get("successful_session_count") or 0) > 0
    health_verified = int(auth.get("session_health_verified_count") or 0) > 0
    needs_runtime = bool(execute_readonly or allow_write_sandbox)
    checks = [
        _check("url_parse_ok", bool(parsed.get("ok")), parsed.get("message") or "url parse unknown", severity="blocking" if needs_runtime else "warning", url=parsed),
        _check("url_host_resolves", bool(dns.get("ok")), dns.get("message") or "dns resolution unknown", severity="blocking" if needs_runtime else "warning", skipped=bool(dns.get("skipped")), dns=dns),
        _check("http_edge_reachable", bool(http_edge.get("ok")), http_edge.get("message") or "http reachability unknown", severity="blocking" if needs_runtime else "warning", skipped=bool(http_edge.get("skipped")), status_code=http_edge.get("status_code"), error=http_edge.get("error"), duration_ms=http_edge.get("duration_ms")),
        _check("auth_configured", configured, "authentication inputs are configured" if configured else "no auth_flow/accounts/static auth headers configured", severity="warning"),
        _check("token_cookie_or_session_acquired", token_or_cookie, "token/cookie/session material was acquired" if token_or_cookie else "no token/cookie/session material acquired yet", severity="warning", mode=auth.get("mode"), successful_session_count=auth.get("successful_session_count")),
        _check("session_health_verified", health_verified, "authenticated session was verified by health/me endpoint" if health_verified else "session health endpoint was not verified", severity="warning", session_health_verified_count=auth.get("session_health_verified_count")),
    ]
    blocking = [c for c in checks if c.get("severity") == "blocking" and not c.get("ok") and not c.get("skipped")]
    warnings = [c for c in checks if c.get("severity") != "blocking" and not c.get("ok")]
    if not base_url and not needs_runtime:
        status = "plan_only"
    elif blocking:
        status = "blocked"
    elif warnings:
        status = "degraded"
    else:
        status = "ready"

    auth_runtime = {
        "mode": auth.get("mode"),
        "login_attempted": bool(auth.get("login_attempted")),
        "configured": configured,
        "successful_session_count": int(auth.get("successful_session_count") or 0),
        "token_acquired_count": int(auth.get("token_acquired_count") or 0),
        "cookie_acquired_count": int(auth.get("cookie_acquired_count") or 0),
        "csrf_token_acquired_count": int(auth.get("csrf_token_acquired_count") or 0),
        "session_health_verified_count": int(auth.get("session_health_verified_count") or 0),
        "default_account": auth.get("default_account"),
        "blocked_reason": auth.get("blocked_reason"),
        "events": _redact(auth.get("events") or []),
    }
    return {
        "engine": "runtime_connectivity_auth_preflight_v1_phase98",
        "status": status,
        "ready_for_authenticated_runtime": bool(status in {"ready", "degraded"} and token_or_cookie and not blocking),
        "url_parse": parsed,
        "dns_resolution": dns,
        "http_edge": http_edge,
        "auth_materialization": _redact({k: v for k, v in auth.items() if not str(k).startswith("_")}),
        "auth_runtime": auth_runtime,
        "resolved_runtime_overrides": {
            "default_header_names": sorted((auth.get("_derived_default_headers") or {}).keys()) if isinstance(auth.get("_derived_default_headers"), dict) else [],
            "resolved_account_names": sorted((auth.get("_resolved_account_headers") or {}).keys()) if isinstance(auth.get("_resolved_account_headers"), dict) else [],
            "note": "raw Authorization/Cookie/session values are intentionally not emitted",
        },
        "checks": checks,
        "blocking_reasons": [str(c.get("name")) for c in blocking],
        "warning_reasons": [str(c.get("name")) for c in warnings],
        "recommended_next_step": _recommended_next_step(status, blocking, warnings, token_or_cookie, health_verified),
        "secret_redaction_policy": "No raw token, cookie, password or session secret is returned in this report.",
    }


def _recommended_next_step(status: str, blocking: list[dict[str, Any]], warnings: list[dict[str, Any]], token_or_cookie: bool, health_verified: bool) -> str:
    if blocking:
        names = ", ".join(str(c.get("name")) for c in blocking[:4])
        return f"Fix environment connectivity blockers before runtime probes: {names}."
    if not token_or_cookie:
        return "Provide auth_flow plus test accounts, or static Authorization/Cookie headers, then rerun connectivity/auth preflight."
    if not health_verified:
        return "Token/cookie was acquired, but session health is not verified; add session_health_path/me_path for stronger readiness proof."
    if warnings:
        names = ", ".join(str(c.get("name")) for c in warnings[:4])
        return f"Authenticated runtime can continue in degraded mode; improve: {names}."
    if status == "ready":
        return "URL, DNS, HTTP edge, token/cookie acquisition and session verification are ready."
    return "Connectivity/auth preflight completed."
