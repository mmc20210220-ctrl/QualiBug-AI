from __future__ import annotations

from pathlib import Path


def test_browser_ui_smoke_disabled_report_is_stable() -> None:
    from ai_test_asset_center.browser_ui_smoke import E_BROWSER_UI_DISABLED, run_browser_ui_smoke

    report = run_browser_ui_smoke(project="demo", root=Path("."), base_url="http://127.0.0.1:8000", enabled=False)

    assert report["schema_version"] == "browser-ui-smoke-v1"
    assert report["enabled"] is False
    assert report["status"] == "disabled"
    assert report["reason_code"] == E_BROWSER_UI_DISABLED
    assert report["page_count"] == 0
    assert report["pages"] == []


def test_browser_ui_smoke_requires_target_when_enabled(tmp_path: Path) -> None:
    from ai_test_asset_center.browser_ui_smoke import E_BROWSER_UI_NO_TARGET, run_browser_ui_smoke

    report = run_browser_ui_smoke(project="demo", root=tmp_path, base_url="", enabled=True)

    assert report["enabled"] is False
    assert report["status"] == "disabled"
    assert report["reason_code"] == E_BROWSER_UI_NO_TARGET


def test_attach_browser_ui_health_adds_ui_gap_when_disabled(tmp_path: Path) -> None:
    from ai_test_asset_center.browser_ui_smoke import E_BROWSER_UI_DISABLED, attach_browser_ui_health

    result = {"ok": True, "coverage_gaps": []}
    updated = attach_browser_ui_health(result, project="demo", root=tmp_path, base_url="http://127.0.0.1:8000", enabled=False)

    assert updated["browser_ui_health"]["reason_code"] == E_BROWSER_UI_DISABLED
    assert updated["coverage_gaps"]
    assert updated["coverage_gaps"][0]["family"] == "ui_visual"
    assert updated["coverage_gaps"][0]["reason_code"] == E_BROWSER_UI_DISABLED


def test_derive_ui_targets_normalizes_paths() -> None:
    from ai_test_asset_center.browser_ui_smoke import derive_ui_targets

    targets = derive_ui_targets("http://example.test/app", explicit_paths=["/", "login", "/login"])

    assert targets == ["http://example.test/app/", "http://example.test/app/login"]


def test_browser_ui_smoke_patch_attaches_health_to_scan(tmp_path: Path, monkeypatch) -> None:
    from ai_test_asset_center import __main__ as scanner_module
    from ai_test_asset_center.private_pilot_entrypoint import install_browser_ui_smoke_patch, restore_browser_ui_smoke_patch

    restore_browser_ui_smoke_patch()
    original_scan = scanner_module.scan

    def fake_scan(*args, **kwargs):
        return {"ok": True, "coverage_gaps": []}

    monkeypatch.setattr(scanner_module, "scan", fake_scan)
    monkeypatch.delenv("QUALIBUG_BROWSER_UI_SMOKE", raising=False)
    install_browser_ui_smoke_patch()

    result = scanner_module.scan(project="demo", root=tmp_path, base_url="http://127.0.0.1:8000")

    assert result["browser_ui_health"]["enabled"] is False
    assert result["browser_ui_health"]["schema_version"] == "browser-ui-smoke-v1"
    assert result["coverage_gaps"][0]["family"] == "ui_visual"

    restore_browser_ui_smoke_patch()
    scanner_module.scan = original_scan


def test_browser_ui_scan_bridge_is_extracted_from_entrypoint() -> None:
    entrypoint = Path("ai_test_asset_center/private_pilot_entrypoint.py").read_text(encoding="utf-8")
    bridge = Path("ai_test_asset_center/private_pilot_browser_bridge.py").read_text(encoding="utf-8")

    assert "from ai_test_asset_center.private_pilot_browser_bridge import" in entrypoint
    assert "from ai_test_asset_center.private_pilot_scan_context_contract import current_scan_campaign_context" in bridge
    assert "private_pilot_server" not in bridge
    assert "def scan_base_url_from_context" in bridge
    assert "def _scan_base_url_from_context" not in entrypoint
    assert "def _scan_with_browser_ui_smoke" not in entrypoint
    assert "browser_ui_error_report" in bridge
