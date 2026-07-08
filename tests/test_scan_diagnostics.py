from __future__ import annotations

import json
import urllib.request

from ai_test_asset_center import scan_diagnostics


def test_allow_internal_preflight_enabled_for_local_dev(monkeypatch):
    monkeypatch.setenv("QUALIBUG_LOCAL_DEV_ACTOR", "1")
    monkeypatch.delenv("QUALIBUG_SSRF_ALLOW_INTERNAL", raising=False)

    assert scan_diagnostics._allow_internal_preflight() is True


def test_allow_internal_preflight_disabled_by_default(monkeypatch):
    monkeypatch.delenv("QUALIBUG_LOCAL_DEV_ACTOR", raising=False)
    monkeypatch.delenv("QUALIBUG_SSRF_ALLOW_INTERNAL", raising=False)

    assert scan_diagnostics._allow_internal_preflight() is False


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
