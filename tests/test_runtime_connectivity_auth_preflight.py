from __future__ import annotations

import json
from typing import Any

from ai_test_asset_center.runtime_connectivity_auth_preflight import build_runtime_connectivity_auth_preflight
from ai_test_asset_center.runtime_onboarding_preflight import run_runtime_onboarding_preflight


def _resolver(host: str, port: int | None) -> list[Any]:
    return [(None, None, None, None, ("127.0.0.1", port or 80))]


def _auth_requester(method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> dict[str, Any]:
    if url.endswith("/api/login"):
        return {
            "status_code": 200,
            "headers": {"Set-Cookie": "sid=session-secret; Path=/; HttpOnly"},
            "payload": {"data": {"accessToken": "token-secret"}},
            "duration_ms": 3,
        }
    if url.endswith("/api/me"):
        assert "Authorization" in headers or "Cookie" in headers
        return {"status_code": 200, "payload": {"user": "qa"}, "duration_ms": 2}
    return {"status_code": 401, "duration_ms": 1}


def test_url_dns_http_auth_token_cookie_and_session_are_reported_without_secret_leak() -> None:
    report = build_runtime_connectivity_auth_preflight(
        config={
            "auth_flow": {
                "login_path": "/api/login",
                "token_json_path": "data.accessToken",
                "session_health_path": "/api/me",
            },
            "accounts": {"normal_user": {"username": "qa", "password": "pw", "role": "normal_user"}},
        },
        base_url="http://sandbox.example.test",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=_auth_requester,
        resolver=_resolver,
    )

    assert report["url_parse"]["ok"] is True
    assert report["dns_resolution"]["ok"] is True
    assert report["http_edge"]["ok"] is True
    auth = report["auth_runtime"]
    assert auth["mode"] == "account_login"
    assert auth["successful_session_count"] == 1
    assert auth["token_acquired_count"] == 1
    assert auth["cookie_acquired_count"] == 1
    assert auth["session_health_verified_count"] == 1
    assert report["ready_for_authenticated_runtime"] is True

    dumped = json.dumps(report, ensure_ascii=False)
    assert "token-secret" not in dumped
    assert "session-secret" not in dumped
    assert "pw" not in dumped


def test_static_cookie_or_token_headers_can_be_verified_by_session_health_path() -> None:
    report = build_runtime_connectivity_auth_preflight(
        config={
            "default_headers": {"Authorization": "Bearer configured-secret"},
            "session_health_path": "/api/me",
        },
        base_url="http://sandbox.example.test",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=_auth_requester,
        resolver=_resolver,
    )

    assert report["auth_runtime"]["mode"] == "static_headers"
    assert report["auth_runtime"]["successful_session_count"] == 1
    assert report["auth_runtime"]["session_health_verified_count"] == 1
    assert "configured-secret" not in json.dumps(report, ensure_ascii=False)


def test_malformed_url_blocks_runtime_before_auth_probe() -> None:
    calls: list[str] = []

    def requester(method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> dict[str, Any]:
        calls.append(url)
        return {"status_code": 200}

    report = build_runtime_connectivity_auth_preflight(
        config={"default_headers": {"Authorization": "Bearer x"}},
        base_url="ftp://example.test",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=requester,
        resolver=_resolver,
    )

    assert report["status"] == "blocked"
    assert "url_parse_ok" in report["blocking_reasons"]
    assert calls == []


def _grounded_read_plan() -> dict[str, Any]:
    return {
        "probes": [
            {
                "risk_type": "auth_boundary_probe",
                "endpoint": {"method": "GET", "path": "/api/v1/orders"},
                "source_refs": [
                    {"kind": "endpoint_contract", "source": "openapi.yaml"},
                    {"kind": "business_rule", "source": "PRD.md"},
                ],
            }
        ]
    }


def test_onboarding_preflight_exposes_connectivity_auth_details() -> None:
    report = run_runtime_onboarding_preflight(
        plan=_grounded_read_plan(),
        config={
            "environment_kind": "sandbox",
            "auth_flow": {
                "login_path": "/api/login",
                "token_json_path": "data.accessToken",
                "session_health_path": "/api/me",
            },
            "accounts": {"normal_user": {"username": "qa", "password": "pw", "role": "normal_user"}},
        },
        base_url="http://sandbox.example.test",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=_auth_requester,
        resolver=_resolver,
    )

    assert report["auth_readiness"]["ok"] is True
    assert report["auth_readiness"]["token_acquired_count"] == 1
    assert report["connectivity_auth_preflight"]["auth_runtime"]["session_health_verified_count"] == 1
    check_names = {check["name"] for check in report["checks"]}
    assert "url_parse_ok" in check_names
    assert "url_host_resolves" in check_names
    assert "token_cookie_or_session_acquired" in check_names
    assert "session_health_verified" in check_names


def _csrf_header_token_requester(method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> dict[str, Any]:
    if url.endswith("/auth/csrf"):
        return {
            "status_code": 200,
            "headers": {"Set-Cookie": "XSRF-TOKEN=csrf-cookie-secret; Path=/; SameSite=Lax"},
            "payload": {"csrfToken": "csrf-json-secret"},
            "duration_ms": 2,
        }
    if url.endswith("/api/login"):
        assert headers.get("Cookie") == "XSRF-TOKEN=csrf-cookie-secret"
        assert headers.get("X-CSRF-Token") == "csrf-json-secret"
        assert headers.get("Content-Type") == "application/json"
        assert body["username"] == "qa"
        assert body["password"] == "pw"
        return {
            "status_code": 200,
            "headers": {"X-Auth-Token": "header-token-secret", "Set-Cookie": "sid=session-secret; Path=/; HttpOnly"},
            "payload": {},
            "duration_ms": 4,
        }
    if url.endswith("/api/me"):
        assert headers.get("X-Auth-Token") == "header-token-secret"
        assert "sid=session-secret" in headers.get("Cookie", "")
        assert headers.get("X-CSRF-Token") == "csrf-json-secret"
        return {"status_code": 200, "payload": {"user": "qa"}, "duration_ms": 2}
    return {"status_code": 401, "duration_ms": 1}


def test_csrf_bootstrap_header_token_and_cookie_session_are_supported_without_secret_leak() -> None:
    report = build_runtime_connectivity_auth_preflight(
        config={
            "auth_flow": {
                "bootstrap_path": "/auth/csrf",
                "csrf_json_path": "csrfToken",
                "csrf_header_name": "X-CSRF-Token",
                "login_path": "/api/login",
                "token_response_header": "X-Auth-Token",
                "token_header_name": "X-Auth-Token",
                "session_health_path": "/api/me",
            },
            "accounts": {"normal_user": {"username": "qa", "password": "pw", "role": "normal_user"}},
        },
        base_url="http://sandbox.example.test",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=_csrf_header_token_requester,
        resolver=_resolver,
    )

    auth = report["auth_runtime"]
    assert auth["mode"] == "account_login"
    assert auth["successful_session_count"] == 1
    assert auth["token_acquired_count"] == 1
    assert auth["cookie_acquired_count"] == 1
    assert auth["csrf_token_acquired_count"] == 1
    assert auth["session_health_verified_count"] == 1
    event = auth["events"][0]
    assert event["bootstrap"]["csrf_acquired"] is True
    assert event["bootstrap"]["cookie_acquired"] is True
    assert event["token_source"] == "X-Auth-Token"
    assert report["ready_for_authenticated_runtime"] is True

    dumped = json.dumps(report, ensure_ascii=False)
    assert "csrf-json-secret" not in dumped
    assert "csrf-cookie-secret" not in dumped
    assert "header-token-secret" not in dumped
    assert "session-secret" not in dumped
    assert "pw" not in dumped


def _html_csrf_redirect_requester(method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> dict[str, Any]:
    if url.endswith("/login") and method == "GET":
        return {
            "status_code": 200,
            "headers": {"Set-Cookie": "csrftoken=html-cookie-secret; Path=/"},
            "text": '<html><head><meta name="csrf-token" content="html-csrf-secret"></head></html>',
            "duration_ms": 2,
        }
    if url.endswith("/login") and method == "POST":
        assert headers.get("Cookie") == "csrftoken=html-cookie-secret"
        assert headers.get("X-CSRF-Token") == "html-csrf-secret"
        assert headers.get("Content-Type") == "application/x-www-form-urlencoded"
        return {"status_code": 302, "headers": {"Set-Cookie": "sid=redirect-session-secret; Path=/"}, "duration_ms": 3}
    if url.endswith("/whoami"):
        assert "sid=redirect-session-secret" in headers.get("Cookie", "")
        return {"status_code": 204, "duration_ms": 1}
    return {"status_code": 404, "duration_ms": 1}


def test_html_csrf_bootstrap_form_login_and_redirect_cookie_session_are_supported() -> None:
    report = build_runtime_connectivity_auth_preflight(
        config={
            "auth_flow": {
                "bootstrap_path": "/login",
                "login_path": "/login",
                "body_format": "form",
                "login_expected_statuses": [200, 302],
                "session_health_path": "/whoami",
                "session_health_expected_statuses": [204],
            },
            "accounts": {"normal_user": {"username": "qa", "password": "pw", "role": "normal_user"}},
        },
        base_url="http://sandbox.example.test",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=_html_csrf_redirect_requester,
        resolver=_resolver,
    )

    auth = report["auth_runtime"]
    assert auth["successful_session_count"] == 1
    assert auth["token_acquired_count"] == 0
    assert auth["cookie_acquired_count"] == 1
    assert auth["csrf_token_acquired_count"] == 1
    assert auth["session_health_verified_count"] == 1
    assert auth["events"][0]["status_ok"] is True
    dumped = json.dumps(report, ensure_ascii=False)
    assert "html-csrf-secret" not in dumped
    assert "redirect-session-secret" not in dumped
