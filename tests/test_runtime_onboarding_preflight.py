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
