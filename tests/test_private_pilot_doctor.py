from __future__ import annotations

from pathlib import Path


def test_private_pilot_doctor_reports_delivery_contract(tmp_path: Path, monkeypatch) -> None:
    from ai_test_asset_center.private_pilot_doctor import diagnose_private_pilot
    from ai_test_asset_center.version import CANONICAL_HEALTH_PATH, DEFAULT_PRIVATE_PILOT_PORT, PRODUCT_VERSION

    monkeypatch.delenv("QUALIBUG_PORT", raising=False)
    payload = diagnose_private_pilot(root=tmp_path)

    assert payload["product"]["version"] == PRODUCT_VERSION
    assert payload["environment"]["port"] == DEFAULT_PRIVATE_PILOT_PORT
    assert payload["environment"]["canonical_health_path"] == CANONICAL_HEALTH_PATH
    assert payload["credential_security"]["returns_plaintext"] is False
    assert payload["credential_security"]["frontend_secret_policy"] == "masked_refs_only"
    assert payload["browser_ui_smoke"]["env_flag"] == "QUALIBUG_BROWSER_UI_SMOKE"
    assert payload["scan_context_contract"]["ok"] is True
    assert payload["scan_context_contract"]["helpers"]["prepare_scan_body_for_campaign"] is True
    assert payload["scan_context_contract"]["helpers"]["build_campaign_context_from_scan_body"] is True
    assert payload["health_payload_preview"]["version"] == PRODUCT_VERSION


def test_private_pilot_doctor_tolerates_invalid_port_env(tmp_path: Path, monkeypatch) -> None:
    from ai_test_asset_center.private_pilot_doctor import diagnose_private_pilot
    from ai_test_asset_center.version import DEFAULT_PRIVATE_PILOT_PORT

    monkeypatch.setenv("QUALIBUG_PORT", "not-a-port")
    payload = diagnose_private_pilot(root=tmp_path)

    assert payload["environment"]["port"] == DEFAULT_PRIVATE_PILOT_PORT


def test_private_pilot_doctor_checks_patch_modules(tmp_path: Path) -> None:
    from ai_test_asset_center.private_pilot_doctor import PRIVATE_PILOT_PATCH_MODULES, diagnose_private_pilot

    payload = diagnose_private_pilot(root=tmp_path)

    assert "ai_test_asset_center.private_pilot_scan_context_contract" in PRIVATE_PILOT_PATCH_MODULES
    assert "ai_test_asset_center.private_pilot_credentials_patch" in PRIVATE_PILOT_PATCH_MODULES
    assert all(item["ok"] for item in payload["modules"].values())


def test_private_pilot_doctor_cli_script_is_registered() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'qualibug-doctor = "ai_test_asset_center.private_pilot_doctor:main"' in pyproject


def test_private_pilot_doctor_main_prints_json(tmp_path: Path, capsys) -> None:
    from ai_test_asset_center.private_pilot_doctor import main

    code = main(["--root", str(tmp_path), "--compact"])
    captured = capsys.readouterr().out

    assert code == 0
    assert '"product"' in captured
    assert '"credential_security"' in captured
    assert '"scan_context_contract"' in captured
