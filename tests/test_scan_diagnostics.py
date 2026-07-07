from __future__ import annotations

from ai_test_asset_center import scan_diagnostics


def test_allow_internal_preflight_enabled_for_local_dev(monkeypatch):
    monkeypatch.setenv("QUALIBUG_LOCAL_DEV_ACTOR", "1")
    monkeypatch.delenv("QUALIBUG_SSRF_ALLOW_INTERNAL", raising=False)

    assert scan_diagnostics._allow_internal_preflight() is True


def test_allow_internal_preflight_disabled_by_default(monkeypatch):
    monkeypatch.delenv("QUALIBUG_LOCAL_DEV_ACTOR", raising=False)
    monkeypatch.delenv("QUALIBUG_SSRF_ALLOW_INTERNAL", raising=False)

    assert scan_diagnostics._allow_internal_preflight() is False
