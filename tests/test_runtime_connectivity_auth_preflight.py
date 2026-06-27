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
    if url.endswith("/api/v1/orders"):
        assert "Authorization" in headers or "Cookie" in headers
        return {"status_code": 200, "headers": {"Content-Type": "application/json"}, "payload": {"items": []}, "duration_ms": 2}
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


def _refresh_token_requester(method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> dict[str, Any]:
    if url.endswith("/api/login"):
        return {
            "status_code": 200,
            "payload": {
                "data": {
                    "accessToken": "short-lived-access-secret",
                    "refreshToken": "refresh-token-secret",
                    "expiresIn": 60,
                }
            },
            "headers": {"Set-Cookie": "sid=login-session-secret; Path=/; HttpOnly"},
            "duration_ms": 3,
        }
    if url.endswith("/api/refresh"):
        assert method == "POST"
        assert body["refresh_token"] == "refresh-token-secret"
        assert "sid=login-session-secret" in headers.get("Cookie", "")
        return {
            "status_code": 200,
            "payload": {"data": {"accessToken": "renewed-access-secret", "expiresIn": 3600}},
            "headers": {"Set-Cookie": "sid=renewed-session-secret; Path=/; HttpOnly"},
            "duration_ms": 4,
        }
    if url.endswith("/api/me"):
        assert headers.get("Authorization") == "Bearer renewed-access-secret"
        assert "sid=renewed-session-secret" in headers.get("Cookie", "")
        return {"status_code": 200, "payload": {"user": "qa"}, "duration_ms": 2}
    return {"status_code": 401, "duration_ms": 1}


def test_expiring_token_refresh_is_verified_without_secret_leak() -> None:
    report = build_runtime_connectivity_auth_preflight(
        config={
            "auth_flow": {
                "login_path": "/api/login",
                "token_json_path": "data.accessToken",
                "refresh_token_json_path": "data.refreshToken",
                "expires_in_json_path": "data.expiresIn",
                "refresh_path": "/api/refresh",
                "refresh_access_token_json_path": "data.accessToken",
                "session_health_path": "/api/me",
            },
            "accounts": {"normal_user": {"username": "qa", "password": "pw", "role": "normal_user"}},
        },
        base_url="http://sandbox.example.test",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=_refresh_token_requester,
        resolver=_resolver,
    )

    auth = report["auth_runtime"]
    assert auth["successful_session_count"] == 1
    assert auth["refresh_token_acquired_count"] == 1
    assert auth["expiring_token_count"] == 1
    assert auth["token_refresh_verified_count"] == 1
    assert auth["session_health_verified_count"] == 1
    check = {item["name"]: item for item in report["checks"]}["token_refresh_ready"]
    assert check["ok"] is True
    assert check["status"] == "passed"

    event = report["auth_runtime"]["events"][0]
    assert event["refresh_token_acquired"] is True
    assert event["token_expires_in_seconds"] == 60
    assert event["refresh"]["status_ok"] is True
    assert event["refresh"]["token_refreshed"] is True

    dumped = json.dumps(report, ensure_ascii=False)
    assert "short-lived-access-secret" not in dumped
    assert "refresh-token-secret" not in dumped
    assert "renewed-access-secret" not in dumped
    assert "login-session-secret" not in dumped
    assert "renewed-session-secret" not in dumped
    assert "pw" not in dumped


def test_onboarding_preflight_exposes_refresh_readiness() -> None:
    report = run_runtime_onboarding_preflight(
        plan=_grounded_read_plan(),
        config={
            "environment_kind": "sandbox",
            "auth_flow": {
                "login_path": "/api/login",
                "token_json_path": "data.accessToken",
                "refresh_token_json_path": "data.refreshToken",
                "expires_in_json_path": "data.expiresIn",
                "refresh_path": "/api/refresh",
                "refresh_access_token_json_path": "data.accessToken",
                "session_health_path": "/api/me",
            },
            "accounts": {"normal_user": {"username": "qa", "password": "pw", "role": "normal_user"}},
        },
        base_url="http://sandbox.example.test",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=_refresh_token_requester,
        resolver=_resolver,
    )

    assert report["auth_readiness"]["refresh_ready"] is True
    assert report["auth_readiness"]["token_refresh_verified_count"] == 1
    check_names = {check["name"] for check in report["checks"]}
    assert "auth_session_refresh_ready" in check_names


def _oauth_client_credentials_requester(method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> dict[str, Any]:
    if url.endswith("/"):
        return {
            "status_code": 401,
            "headers": {"WWW-Authenticate": 'Bearer realm="qualibug", error="invalid_token"'},
            "duration_ms": 1,
        }
    if url.endswith("/oauth/token"):
        assert method == "POST"
        assert headers.get("Content-Type") == "application/x-www-form-urlencoded"
        assert headers.get("Authorization", "").startswith("Basic ")
        assert body["grant_type"] == "client_credentials"
        assert body["scope"] == "orders:read inventory:read"
        assert "username" not in body
        assert "password" not in body
        return {
            "status_code": 200,
            "payload": {"access_token": "oauth-access-secret", "expires_in": 900},
            "duration_ms": 4,
        }
    if url.endswith("/api/me"):
        assert headers.get("Authorization") == "Bearer oauth-access-secret"
        return {"status_code": 200, "payload": {"client": "machine"}, "duration_ms": 2}
    return {"status_code": 404, "duration_ms": 1}


def test_oauth2_client_credentials_basic_auth_can_materialize_runtime_session_without_secret_leak() -> None:
    report = build_runtime_connectivity_auth_preflight(
        config={
            "auth_flow": {
                "auth_type": "oauth2_client_credentials",
                "token_endpoint": "/oauth/token",
                "client_auth_method": "basic",
                "client_id": "machine-client",
                "client_secret": "machine-client-secret",
                "scope": "orders:read inventory:read",
                "session_health_path": "/api/me",
            },
            "accounts": {"service_account": {"role": "service_account"}},
        },
        base_url="http://sandbox.example.test",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=_oauth_client_credentials_requester,
        resolver=_resolver,
    )

    auth = report["auth_runtime"]
    assert auth["mode"] == "account_login"
    assert auth["successful_session_count"] == 1
    assert auth["token_acquired_count"] == 1
    assert auth["session_health_verified_count"] == 1
    assert auth["oauth_session_count"] == 1
    assert report["http_edge"]["auth_challenge"]["present"] is True
    assert report["http_edge"]["auth_challenge"]["scheme"] == "bearer"
    event = auth["events"][0]
    assert event["oauth_grant_type"] == "client_credentials"
    assert event["oauth_client_auth_method"] == "basic"
    assert event["request_body_format"] == "form"

    dumped = json.dumps(report, ensure_ascii=False)
    assert "oauth-access-secret" not in dumped
    assert "machine-client-secret" not in dumped


def _oauth_password_grant_requester(method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> dict[str, Any]:
    if url.endswith("/oauth/token"):
        assert body["grant_type"] == "password"
        assert body["username"] == "qa-user"
        assert body["password"] == "qa-pass-secret"
        assert body["client_id"] == "public-client"
        assert body["client_secret"] == "public-client-secret"
        return {"status_code": 200, "payload": {"access_token": "password-grant-secret"}, "duration_ms": 3}
    if url.endswith("/api/me"):
        assert headers.get("Authorization") == "Bearer password-grant-secret"
        return {"status_code": 200, "payload": {"user": "qa-user"}, "duration_ms": 2}
    return {"status_code": 200, "duration_ms": 1}


def test_oauth2_password_grant_uses_account_credentials_and_remains_redacted() -> None:
    report = build_runtime_connectivity_auth_preflight(
        config={
            "auth_flow": {
                "grant_type": "password",
                "token_endpoint": "/oauth/token",
                "client_id": "public-client",
                "client_secret": "public-client-secret",
                "session_health_path": "/api/me",
            },
            "accounts": {"normal_user": {"username": "qa-user", "password": "qa-pass-secret"}},
        },
        base_url="http://sandbox.example.test",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=_oauth_password_grant_requester,
        resolver=_resolver,
    )

    auth = report["auth_runtime"]
    assert auth["oauth_session_count"] == 1
    assert auth["session_health_verified_count"] == 1
    assert auth["events"][0]["oauth_grant_type"] == "password"

    dumped = json.dumps(report, ensure_ascii=False)
    assert "password-grant-secret" not in dumped
    assert "qa-pass-secret" not in dumped
    assert "public-client-secret" not in dumped


def _saml_browser_sso_requester(method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> dict[str, Any]:
    if url.endswith("/"):
        return {"status_code": 200, "duration_ms": 1}
    if url.endswith("/api/login"):
        return {
            "status_code": 302,
            "headers": {"Location": "https://idp.example.com/sso/saml?SAMLRequest=saml-browser-secret&RelayState=session-secret"},
            "duration_ms": 2,
        }
    if url.endswith("/api/me"):
        return {"status_code": 401, "duration_ms": 1}
    return {"status_code": 404, "duration_ms": 1}


def test_browser_sso_redirect_is_classified_as_interactive_auth_blocker_without_leaking_location_query() -> None:
    report = build_runtime_connectivity_auth_preflight(
        config={
            "auth_flow": {
                "login_path": "/api/login",
                "session_health_path": "/api/me",
            },
            "accounts": {"normal_user": {"username": "qa", "password": "pw", "role": "normal_user"}},
        },
        base_url="http://sandbox.example.test",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=_saml_browser_sso_requester,
        resolver=_resolver,
    )

    auth = report["auth_runtime"]
    assert auth["successful_session_count"] == 0
    assert auth["interactive_auth_blocker_count"] == 1
    blocker = auth["events"][0]["interactive_auth_blocker"]
    assert blocker["present"] is True
    assert blocker["auth_blocker_type"] == "saml_browser_sso"
    assert blocker["auth_blocker_location"] == "https://idp.example.com/sso/saml"
    checks = {item["name"]: item for item in report["checks"]}
    assert checks["interactive_auth_not_blocked"]["status"] == "warning"
    assert "service account" in report["recommended_next_step"]

    dumped = json.dumps(report, ensure_ascii=False)
    assert "saml-browser-secret" not in dumped
    assert "RelayState=session-secret" not in dumped
    assert "pw" not in dumped


def test_onboarding_preflight_surfaces_interactive_auth_blockers() -> None:
    report = run_runtime_onboarding_preflight(
        plan=_grounded_read_plan(),
        config={
            "environment_kind": "sandbox",
            "auth_flow": {"login_path": "/api/login", "session_health_path": "/api/me"},
            "accounts": {"normal_user": {"username": "qa", "password": "pw", "role": "normal_user"}},
        },
        base_url="http://sandbox.example.test",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=_saml_browser_sso_requester,
        resolver=_resolver,
    )

    assert report["auth_readiness"]["interactive_auth_blocker_count"] == 1
    checks = {item["name"]: item for item in report["checks"]}
    assert checks["interactive_auth_not_blocked"]["status"] == "warning"


def _api_smoke_success_requester(method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> dict[str, Any]:
    if url.endswith("/"):
        return {"status_code": 200, "duration_ms": 1}
    if url.endswith("/api/login"):
        return {"status_code": 200, "payload": {"access_token": "api-smoke-token-secret"}, "duration_ms": 2}
    if url.endswith("/api/me"):
        assert headers.get("Authorization") == "Bearer api-smoke-token-secret"
        return {"status_code": 200, "payload": {"user": "qa"}, "duration_ms": 1}
    if url.endswith("/api/v1/orders"):
        assert headers.get("Authorization") == "Bearer api-smoke-token-secret"
        return {"status_code": 200, "headers": {"Content-Type": "application/json; charset=utf-8"}, "payload": {"items": []}, "duration_ms": 2}
    return {"status_code": 404, "duration_ms": 1}


def test_authenticated_api_smoke_verifies_real_api_base_path_without_secret_leak() -> None:
    report = build_runtime_connectivity_auth_preflight(
        config={
            "auth_flow": {"login_path": "/api/login", "session_health_path": "/api/me"},
            "accounts": {"normal_user": {"username": "qa", "password": "pw", "role": "normal_user"}},
            "api_smoke_paths": ["/api/v1/orders"],
        },
        base_url="http://sandbox.example.test",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=_api_smoke_success_requester,
        resolver=_resolver,
    )

    smoke = report["authenticated_api_smoke"]
    assert smoke["configured"] is True
    assert smoke["ok"] is True
    assert smoke["passed_count"] == 1
    assert smoke["events"][0]["content_type"] == "application/json"
    assert report["ready_for_authenticated_runtime"] is True
    checks = {item["name"]: item for item in report["checks"]}
    assert checks["authenticated_api_smoke_verified"]["status"] == "passed"

    dumped = json.dumps(report, ensure_ascii=False)
    assert "api-smoke-token-secret" not in dumped
    assert "pw" not in dumped


def _api_smoke_html_login_requester(method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> dict[str, Any]:
    if url.endswith("/"):
        return {"status_code": 200, "duration_ms": 1}
    if url.endswith("/api/login"):
        return {"status_code": 200, "payload": {"access_token": "wrong-base-token-secret"}, "duration_ms": 2}
    if url.endswith("/api/me"):
        return {"status_code": 200, "payload": {"user": "qa"}, "duration_ms": 1}
    if "/api/v1/orders" in url:
        return {
            "status_code": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "text": "<html><form action='/login'>login</form></html>",
            "duration_ms": 2,
        }
    return {"status_code": 404, "duration_ms": 1}


def test_authenticated_api_smoke_flags_html_login_or_wrong_api_base_path() -> None:
    report = build_runtime_connectivity_auth_preflight(
        config={
            "auth_flow": {"login_path": "/api/login", "session_health_path": "/api/me"},
            "accounts": {"normal_user": {"username": "qa", "password": "pw", "role": "normal_user"}},
            "api_smoke_paths": ["/api/v1/orders?customer=secret-customer-id"],
        },
        base_url="http://sandbox.example.test",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=_api_smoke_html_login_requester,
        resolver=_resolver,
    )

    smoke = report["authenticated_api_smoke"]
    assert smoke["configured"] is True
    assert smoke["ok"] is False
    assert smoke["failed_count"] == 1
    assert smoke["html_or_browser_response_count"] == 1
    assert smoke["events"][0]["path"] == "/api/v1/orders"
    assert smoke["events"][0]["issue"] == "html_or_browser_page_returned_instead_of_api"
    assert report["ready_for_authenticated_runtime"] is False
    checks = {item["name"]: item for item in report["checks"]}
    assert checks["authenticated_api_smoke_verified"]["status"] == "warning"
    assert "api base path" in report["recommended_next_step"]

    dumped = json.dumps(report, ensure_ascii=False)
    assert "wrong-base-token-secret" not in dumped
    assert "secret-customer-id" not in dumped


def test_onboarding_preflight_derives_authenticated_smoke_paths_from_read_probe_plan() -> None:
    report = run_runtime_onboarding_preflight(
        plan=_grounded_read_plan(),
        config={
            "environment_kind": "sandbox",
            "auth_flow": {"login_path": "/api/login", "session_health_path": "/api/me"},
            "accounts": {"normal_user": {"username": "qa", "password": "pw", "role": "normal_user"}},
        },
        base_url="http://sandbox.example.test",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=_api_smoke_success_requester,
        resolver=_resolver,
    )

    smoke = report["connectivity_auth_preflight"]["authenticated_api_smoke"]
    assert smoke["configured"] is True
    assert smoke["passed_count"] == 1
    assert smoke["events"][0]["path"] == "/api/v1/orders"
    checks = {item["name"]: item for item in report["checks"]}
    assert checks["authenticated_api_smoke_verified"]["status"] == "passed"
