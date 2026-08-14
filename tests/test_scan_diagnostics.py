from __future__ import annotations

import json
import urllib.request

from ai_test_asset_center import scan_diagnostics, ssrf_guard


def test_allow_internal_preflight_enabled_for_local_dev(monkeypatch):
    monkeypatch.setenv("QUALIBUG_LOCAL_DEV_ACTOR", "1")
    monkeypatch.delenv("QUALIBUG_SSRF_ALLOW_INTERNAL", raising=False)

    assert scan_diagnostics._allow_internal_preflight() is True


def test_allow_internal_preflight_disabled_by_default(monkeypatch):
    monkeypatch.delenv("QUALIBUG_LOCAL_DEV_ACTOR", raising=False)
    monkeypatch.delenv("QUALIBUG_SSRF_ALLOW_INTERNAL", raising=False)

    assert scan_diagnostics._allow_internal_preflight() is False


def test_allow_internal_preflight_for_exact_approved_nonproduction_target(monkeypatch):
    monkeypatch.delenv("QUALIBUG_LOCAL_DEV_ACTOR", raising=False)
    monkeypatch.delenv("QUALIBUG_SSRF_ALLOW_INTERNAL", raising=False)
    config = {
        "environment_kind": "test",
        "api_base_url": "http://127.0.0.1:8080",
        "approved_base_url": "http://127.0.0.1:8080",
    }

    assert scan_diagnostics._allow_internal_preflight(
        config,
        config["api_base_url"],
    ) is True
    assert scan_diagnostics._allow_internal_preflight(
        {**config, "approved_base_url": "http://127.0.0.1:8081"},
        config["api_base_url"],
    ) is False
    assert scan_diagnostics._allow_internal_preflight(
        {**config, "environment_kind": "production"},
        config["api_base_url"],
    ) is False


def test_redirect_handler_preserves_call_specific_internal_grant(monkeypatch):
    validations: list[tuple[str, bool | None, str]] = []

    def record_validation(
        url: str,
        *,
        allow_internal: bool | None = None,
        approved_host: str = "",
    ) -> str:
        validations.append((url, allow_internal, approved_host))
        return url

    monkeypatch.setattr(ssrf_guard, "validate_url", record_validation)
    monkeypatch.setattr(
        urllib.request.HTTPRedirectHandler,
        "redirect_request",
        lambda self, req, fp, code, msg, headers, newurl: "redirect-ok",
    )
    handler = ssrf_guard._SsrfSafeRedirectHandler(allow_internal=True)
    result = handler.redirect_request(
        urllib.request.Request("http://127.0.0.1:8080/start"),
        None,
        302,
        "Found",
        {},
        "http://127.0.0.1:8080/next",
    )

    assert result == "redirect-ok"
    assert validations == [("http://127.0.0.1:8080/next", True, "")]


def test_run_preflight_prefers_configured_default_test_credential(monkeypatch):
    calls: list[dict[str, str]] = []

    class _Response:
        def __init__(self, payload: dict[str, object], status: int = 200) -> None:
            self.status = status
            self._payload = payload

        def read(self) -> bytes:
            return json.dumps(self._payload).encode("utf-8")

    def fake_safe_urlopen(target, timeout=5, allow_internal=False):
        if isinstance(target, urllib.request.Request):
            payload = json.loads(target.data.decode("utf-8"))
            calls.append(payload)
            return _Response({"token": "token-1"})
        return _Response({}, status=200)

    monkeypatch.setattr(scan_diagnostics, "safe_urlopen", fake_safe_urlopen)

    result = scan_diagnostics.run_preflight(
        {
            "api_base_url": "http://sandbox.local",
            "test_credentials": {
                "ops_reader": {"email": "ops-reader@example.com", "password": "Reader@123456"},
                "portal_primary": {
                    "email": "portal-primary@example.com",
                    "password": "Portal@123456",
                    "default": True,
                },
            },
        }
    )

    assert calls[0]["email"] == "portal-primary@example.com"
    auth_check = next(item for item in result["checks"] if item["name"].startswith("测试凭证("))
    assert auth_check["passed"] is True
