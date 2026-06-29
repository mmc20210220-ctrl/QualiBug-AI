from __future__ import annotations

from typing import Any

from ai_test_asset_center.runtime_onboarding_preflight import run_runtime_onboarding_preflight


def _requester(method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> dict[str, Any]:
    return {"status_code": 200, "duration_ms": 1}


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


def test_default_auth_headers_are_configured_but_not_verified_sessions() -> None:
    report = run_runtime_onboarding_preflight(
        plan=_grounded_read_plan(),
        config={
            "environment_kind": "sandbox",
            "default_headers": {"Authorization": "Bearer configured-token"},
        },
        base_url="http://127.0.0.1:8011",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=_requester,
    )

    auth = report["auth_readiness"]
    auth_check = next(check for check in report["checks"] if check["name"] == "auth_session_ready")

    assert auth["ok"] is False
    assert auth["configured"] is True
    assert auth["verified"] is False
    assert auth["successful_session_count"] == 0
    assert auth["mode"] == "headers_configured_unverified"
    assert auth_check["ok"] is False
    assert "configured but no login/session health check" in auth_check["message"]
    assert report["ready_for_p0_p1_runtime_validation"] is False


def test_account_login_runtime_marks_auth_verified() -> None:
    report = run_runtime_onboarding_preflight(
        plan=_grounded_read_plan(),
        config={
            "environment_kind": "sandbox",
            "accounts": {"normal_user": {"username": "qa-user", "role": "normal_user"}},
            "_auth_runtime": {"mode": "account_login", "successful_session_count": 1},
        },
        base_url="http://127.0.0.1:8011",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=_requester,
    )

    auth = report["auth_readiness"]
    auth_check = next(check for check in report["checks"] if check["name"] == "auth_session_ready")

    assert auth["ok"] is True
    assert auth["configured"] is True
    assert auth["verified"] is True
    assert auth["successful_session_count"] == 1
    assert auth_check["ok"] is True


def _benchmark_auth_requester(method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> dict[str, Any]:
    if url.endswith("/api/login"):
        role_map = {
            "qb_normal_user": ("normal_user", "t-a", "qb-token-normal-user", "qb_sid_normal_user"),
            "qb_admin_user": ("admin_user", "t-a", "qb-token-admin-user", "qb_sid_admin_user"),
            "qb_owner_user": ("owner_user", "t-a", "qb-token-owner-user", "qb_sid_owner_user"),
            "qb_cross_tenant_user": ("cross_tenant_user", "t-b", "qb-token-cross-tenant-user", "qb_sid_cross_tenant_user"),
        }
        username = str(body.get("username") or "")
        role, tenant_id, token, cookie = role_map[username]
        assert body.get("password") == "benchmark-demo-password"
        return {
            "status_code": 200,
            "headers": {"Set-Cookie": f"sid={cookie}; Path=/; HttpOnly"},
            "payload": {"ok": True, "data": {"accessToken": token, "role": role, "tenant_id": tenant_id}},
            "duration_ms": 2,
        }
    if url.endswith("/api/me"):
        assert headers.get("Authorization", "").startswith("Bearer qb-token-")
        return {"status_code": 200, "payload": {"ok": True, "user": {"role": "normal_user"}}, "duration_ms": 1}
    if "/api/v1/orders" in url:
        assert headers.get("Authorization", "").startswith("Bearer qb-token-")
        return {"status_code": 200, "headers": {"Content-Type": "application/json"}, "payload": {"items": []}, "duration_ms": 1}
    return {"status_code": 404, "duration_ms": 1}


def test_benchmark_style_account_login_can_make_preflight_ready() -> None:
    report = run_runtime_onboarding_preflight(
        plan=_grounded_read_plan(),
        config={
            "environment_kind": "sandbox",
            "auto_fixture": {"enabled": True},
            "disposable_sandbox": {"enabled": True, "cleanup_strategy": "benchmark_reset"},
            "_auth_runtime": {"mode": "account_login", "successful_session_count": 1, "session_health_verified_count": 0},
            "auth_flow": {
                "login_path": "/api/login",
                "token_json_path": "data.accessToken",
                "session_health_path": "/api/me",
                "tenant_field": "tenant_id",
            },
            "default_account": "normal_user",
            "accounts": {
                "normal_user": {"username": "qb_normal_user", "password": "benchmark-demo-password", "role": "normal_user", "tenant_id": "t-a"},
                "admin_user": {"username": "qb_admin_user", "password": "benchmark-demo-password", "role": "admin_user", "tenant_id": "t-a"},
                "owner_user": {"username": "qb_owner_user", "password": "benchmark-demo-password", "role": "owner_user", "tenant_id": "t-a"},
                "cross_tenant_user": {"username": "qb_cross_tenant_user", "password": "benchmark-demo-password", "role": "cross_tenant_user", "tenant_id": "t-b"},
            },
        },
        base_url="http://127.0.0.1:8011",
        execute_readonly=True,
        allow_write_sandbox=False,
        requester=_benchmark_auth_requester,
    )

    assert report["status"] == "ready"
    assert report["auth_readiness"]["ok"] is True
    assert report["auth_readiness"]["session_health_verified"] is True
    assert report["connectivity_auth_preflight"]["authenticated_api_smoke"]["ok"] is True
    role_check = next(check for check in report["checks"] if check["name"] == "role_coverage")
    assert role_check["ok"] is True
