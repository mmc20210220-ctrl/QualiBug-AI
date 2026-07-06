from __future__ import annotations

import json
from pathlib import Path


def _hint_codes(payload: dict) -> set[str]:
    return {str(item.get("code")) for item in payload.get("remediation_hints", []) if isinstance(item, dict)}


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
    assert isinstance(payload["remediation_hints"], list)
    assert payload["readiness"]["level"] in {"ready", "warning", "blocked"}
    assert isinstance(payload["summary_lines"], list)
    assert payload["summary_text"].startswith("QualiBug private-pilot doctor:")
    assert payload["support_bundle_manifest"]["policy"] == "diagnostics_only_no_customer_data"


def test_private_pilot_doctor_tolerates_invalid_port_env(tmp_path: Path, monkeypatch) -> None:
    from ai_test_asset_center.private_pilot_doctor import diagnose_private_pilot
    from ai_test_asset_center.version import DEFAULT_PRIVATE_PILOT_PORT

    monkeypatch.setenv("QUALIBUG_PORT", "not-a-port")
    payload = diagnose_private_pilot(root=tmp_path)

    assert payload["environment"]["port"] == DEFAULT_PRIVATE_PILOT_PORT
    assert payload["environment"]["port_env"]["valid"] is False
    assert "invalid_qualibug_port_env" in payload["warnings"]
    assert "INVALID_QUALIBUG_PORT" in _hint_codes(payload)
    assert payload["readiness"]["level"] == "warning"
    assert "INVALID_QUALIBUG_PORT" in payload["readiness"]["warnings"]
    assert "INVALID_QUALIBUG_PORT" in payload["summary_text"]


def test_private_pilot_doctor_remediates_readonly_patch_status_and_missing_key(tmp_path: Path, monkeypatch) -> None:
    from ai_test_asset_center.private_pilot_doctor import diagnose_private_pilot

    monkeypatch.delenv("QUALIBUG_CRED_ENC_KEY", raising=False)
    payload = diagnose_private_pilot(root=tmp_path)
    codes = _hint_codes(payload)

    assert "CREDENTIAL_KEY_MISSING" in codes
    assert "RUNTIME_PATCHES_NOT_INSTALLED_IN_READONLY_MODE" in codes
    assert payload["readiness"]["level"] == "warning"
    assert payload["readiness"]["blocked"] is False
    assert any("qualibug-doctor --install-patches --output" in command for item in payload["remediation_hints"] for command in item.get("commands", []))
    assert "Suggested commands:" in payload["summary_text"]


def test_private_pilot_doctor_readiness_blocks_error_hints() -> None:
    from ai_test_asset_center.private_pilot_doctor import _collect_readiness

    payload = {"warnings": []}
    hints = [
        {
            "code": "PRIVATE_PILOT_MODULE_IMPORT_FAILED",
            "severity": "error",
            "title": "module failed",
            "action": "pip install -e .",
            "commands": ["pip install -e ."],
        }
    ]
    readiness = _collect_readiness(payload, hints)

    assert readiness["level"] == "blocked"
    assert readiness["blocked"] is True
    assert readiness["blockers"] == ["PRIVATE_PILOT_MODULE_IMPORT_FAILED"]


def test_private_pilot_doctor_human_summary_mentions_blockers() -> None:
    from ai_test_asset_center.private_pilot_doctor import _collect_summary

    payload = {
        "product": {"version": "95.0.0"},
        "environment": {"port": 8088},
        "readiness": {
            "level": "blocked",
            "label": "Blocked - fix required before customer pilot",
            "blockers": ["PRIVATE_PILOT_MODULE_IMPORT_FAILED"],
            "warnings": ["CREDENTIAL_KEY_MISSING"],
            "next_action": "Reinstall the package.",
        },
        "remediation_hints": [
            {"code": "PRIVATE_PILOT_MODULE_IMPORT_FAILED", "commands": ["pip install -e .", "qualibug-doctor --output"]}
        ],
    }
    summary = _collect_summary(payload)

    assert "Blocked - fix required before customer pilot" in summary["summary_text"]
    assert "PRIVATE_PILOT_MODULE_IMPORT_FAILED" in summary["summary_text"]
    assert "pip install -e ." in summary["summary_text"]


def test_private_pilot_doctor_support_bundle_manifest_marks_sensitive_paths(tmp_path: Path) -> None:
    from ai_test_asset_center.private_pilot_doctor import diagnose_private_pilot

    payload = diagnose_private_pilot(root=tmp_path)
    manifest = payload["support_bundle_manifest"]
    safe_ids = {item["id"] for item in manifest["safe_to_share"]}
    review_ids = {item["id"] for item in manifest["requires_review"]}
    blocked_text = json.dumps(manifest["do_not_send"], ensure_ascii=False)

    assert manifest["schema_version"] == "support-bundle-manifest-v1"
    assert "private_pilot_doctor_report" in safe_ids
    assert "private_pilot_doctor_patched_report" in safe_ids
    assert "service_logs" in review_ids
    assert "browser_ui_artifacts" in review_ids
    assert ".env" in blocked_text
    assert ".secrets" in blocked_text
    assert "multi_service_config.json" in blocked_text
    assert "source_registry" in blocked_text
    assert any("masked refs" in rule for rule in manifest["redaction_rules"])


def test_private_pilot_doctor_checks_patch_modules(tmp_path: Path) -> None:
    from ai_test_asset_center.private_pilot_doctor import PRIVATE_PILOT_PATCH_MODULES, diagnose_private_pilot

    payload = diagnose_private_pilot(root=tmp_path)

    assert "ai_test_asset_center.private_pilot_scan_context_contract" in PRIVATE_PILOT_PATCH_MODULES
    assert "ai_test_asset_center.private_pilot_credentials_patch" in PRIVATE_PILOT_PATCH_MODULES
    assert all(item["ok"] for item in payload["modules"].values())


def test_private_pilot_doctor_writes_default_report(tmp_path: Path) -> None:
    from ai_test_asset_center.private_pilot_doctor import diagnose_private_pilot, write_doctor_report

    payload = diagnose_private_pilot(root=tmp_path)
    report_path = write_doctor_report(payload, root=tmp_path)
    saved = json.loads(report_path.read_text(encoding="utf-8"))

    assert report_path == tmp_path / "platform_outputs" / "private_pilot_doctor_report.json"
    assert saved["doctor_report_file"] == str(report_path)
    assert saved["product"]["version"] == payload["product"]["version"]
    assert isinstance(saved["remediation_hints"], list)
    assert saved["readiness"]["level"] in {"ready", "warning", "blocked"}
    assert saved["summary_text"] == payload["summary_text"]
    assert saved["support_bundle_manifest"]["policy"] == "diagnostics_only_no_customer_data"


def test_private_pilot_doctor_cli_writes_custom_report(tmp_path: Path, capsys) -> None:
    from ai_test_asset_center.private_pilot_doctor import main

    report_path = tmp_path / "artifacts" / "doctor.json"
    code = main(["--root", str(tmp_path), "--output", str(report_path), "--compact"])
    captured = capsys.readouterr().out
    saved = json.loads(report_path.read_text(encoding="utf-8"))

    assert code == 0
    assert report_path.exists()
    assert saved["doctor_report_file"] == str(report_path)
    assert '"doctor_report_file"' in captured
    assert '"remediation_hints"' in captured
    assert '"readiness"' in captured
    assert '"summary_text"' in captured
    assert '"support_bundle_manifest"' in captured


def test_private_pilot_doctor_cli_script_is_registered() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'qualibug-doctor = "ai_test_asset_center.private_pilot_doctor:main"' in pyproject


def test_private_pilot_doctor_report_guide_documents_output_workflow() -> None:
    guide = Path("docs/private_pilot_doctor.md").read_text(encoding="utf-8")

    assert "qualibug-doctor --output" in guide
    assert "platform_outputs/private_pilot_doctor_report.json" in guide
    assert "qualibug-doctor --install-patches --output" in guide
    assert "masked refs" in guide
    assert "remediation_hints" in guide
    assert "INVALID_QUALIBUG_PORT" in guide
    assert "CREDENTIAL_KEY_MISSING" in guide
    assert "RUNTIME_PATCHES_NOT_INSTALLED_IN_READONLY_MODE" in guide
    assert "## Human summary" in guide
    assert "summary_text" in guide
    assert "summary_lines" in guide
    assert "Suggested commands" in guide
    assert "## Readiness levels" in guide
    assert "`ready`" in guide
    assert "`warning`" in guide
    assert "`blocked`" in guide


def test_private_pilot_doctor_main_prints_json(tmp_path: Path, capsys) -> None:
    from ai_test_asset_center.private_pilot_doctor import main

    code = main(["--root", str(tmp_path), "--compact"])
    captured = capsys.readouterr().out

    assert code == 0
    assert '"product"' in captured
    assert '"credential_security"' in captured
    assert '"scan_context_contract"' in captured
    assert '"remediation_hints"' in captured
    assert '"readiness"' in captured
    assert '"summary_text"' in captured
    assert '"support_bundle_manifest"' in captured
