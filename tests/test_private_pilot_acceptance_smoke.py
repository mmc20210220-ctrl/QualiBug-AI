from __future__ import annotations

import json
from pathlib import Path


def test_acceptance_smoke_generates_report_and_checks_contract(tmp_path: Path) -> None:
    from ai_test_asset_center.private_pilot_acceptance_smoke import run_acceptance_smoke, write_acceptance_report
    from ai_test_asset_center.version import PRODUCT_VERSION

    payload = run_acceptance_smoke(root=tmp_path, install_patches=True, write_doctor=True)
    report_path = write_acceptance_report(payload, root=tmp_path)
    saved = json.loads(report_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "private-pilot-acceptance-smoke-v1"
    assert payload["product_version"] == PRODUCT_VERSION
    assert payload["checks"]["runtime_patches"]["ok"] is True
    assert payload["checks"]["scan_context_contract"]["ok"] is True
    assert payload["checks"]["credential_safety"]["ok"] is True
    assert payload["checks"]["http_health"]["status"] == "skipped"
    assert payload["acceptance"]["level"] in {"accepted", "warning", "blocked"}
    assert saved["acceptance_report_file"] == str(report_path)
    assert saved["support_bundle_manifest"]["policy"] == "diagnostics_only_no_customer_data"
    assert (tmp_path / "platform_outputs" / "private_pilot_doctor_report.json").exists()


def test_acceptance_smoke_blocks_when_runtime_patches_are_skipped(tmp_path: Path) -> None:
    from ai_test_asset_center.private_pilot_acceptance_smoke import run_acceptance_smoke
    from ai_test_asset_center.private_pilot_entrypoint import restore_deployment_contract_patch
    from ai_test_asset_center.private_pilot_server import restore_customer_delivery_gate_patch

    restore_deployment_contract_patch()
    restore_customer_delivery_gate_patch()
    payload = run_acceptance_smoke(root=tmp_path, install_patches=False, write_doctor=False)

    assert payload["install_patches"] is False
    assert payload["checks"]["runtime_patches"]["ok"] is False
    assert payload["acceptance"]["level"] == "blocked"
    assert any(str(item).startswith("runtime_patches_missing:") for item in payload["acceptance"]["blockers"])


def test_acceptance_smoke_blocks_failed_http_health(tmp_path: Path) -> None:
    from ai_test_asset_center.private_pilot_acceptance_smoke import run_acceptance_smoke

    payload = run_acceptance_smoke(
        root=tmp_path,
        server_url="http://127.0.0.1:9",
        install_patches=True,
        write_doctor=False,
    )

    assert payload["checks"]["http_health"]["enabled"] is True
    assert payload["checks"]["http_health"]["ok"] is False
    assert "http_health_check_failed" in payload["acceptance"]["blockers"]
    assert payload["acceptance"]["level"] == "blocked"


def test_acceptance_smoke_cli_writes_custom_report(tmp_path: Path, capsys) -> None:
    from ai_test_asset_center.private_pilot_acceptance_smoke import main

    report_path = tmp_path / "artifacts" / "acceptance.json"
    code = main(["--root", str(tmp_path), "--output", str(report_path), "--compact"])
    captured = capsys.readouterr().out
    saved = json.loads(report_path.read_text(encoding="utf-8"))

    assert code == 0
    assert report_path.exists()
    assert saved["acceptance_report_file"] == str(report_path)
    assert '"schema_version"' in captured
    assert '"acceptance"' in captured
    assert '"checks"' in captured


def test_acceptance_smoke_cli_script_is_registered() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'qualibug-acceptance-smoke = "ai_test_asset_center.private_pilot_acceptance_smoke:main"' in pyproject
