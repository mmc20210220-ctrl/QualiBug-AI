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
