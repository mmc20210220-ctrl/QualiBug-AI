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
    assert "scenario_readiness" in payload["checks"]
    assert payload["acceptance"]["level"] in {"accepted", "warning", "blocked"}
    assert payload["customer_acceptance_summary"]["schema_version"] == "customer-acceptance-summary-v1"
    assert payload["customer_acceptance_summary"]["zh_text"]
    assert payload["customer_acceptance_summary"]["en_text"]
    assert saved["acceptance_report_file"] == str(report_path)
    assert saved["support_bundle_manifest"]["policy"] == "diagnostics_only_no_customer_data"
    assert saved["customer_acceptance_summary"]["en_text"] == payload["customer_acceptance_summary"]["en_text"]
    assert (tmp_path / "platform_outputs" / "private_pilot_doctor_report.json").exists()


def test_acceptance_smoke_warns_when_scenario_readiness_is_optional(tmp_path: Path) -> None:
    from ai_test_asset_center.private_pilot_acceptance_smoke import run_acceptance_smoke

    payload = run_acceptance_smoke(root=tmp_path, install_patches=True, write_doctor=False)
    scenario = payload["checks"]["scenario_readiness"]
    summary = payload["customer_acceptance_summary"]

    assert scenario["ready"] is False
    assert scenario["required"] is False
    assert "source_registry_asset" in scenario["missing"]
    assert "base_url" in scenario["missing"]
    assert payload["acceptance"]["level"] == "warning"
    assert any(str(item).startswith("scenario_readiness_missing:") for item in payload["acceptance"]["warnings"])
    assert "source_registry_asset" in summary["en_text"]
    assert "qualibug-acceptance-smoke --project" in summary["next_commands"][0]


def test_acceptance_smoke_blocks_when_required_scenario_readiness_is_missing(tmp_path: Path) -> None:
    from ai_test_asset_center.private_pilot_acceptance_smoke import run_acceptance_smoke

    payload = run_acceptance_smoke(root=tmp_path, install_patches=True, write_doctor=False, require_scenario_ready=True)

    assert payload["checks"]["scenario_readiness"]["required"] is True
    assert payload["acceptance"]["level"] == "blocked"
    assert payload["customer_acceptance_summary"]["accepted"] is False
    assert any(str(item).startswith("scenario_readiness_missing:") for item in payload["acceptance"]["blockers"])


def test_acceptance_smoke_accepts_complete_scenario_preflight(tmp_path: Path) -> None:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.private_pilot_acceptance_smoke import run_acceptance_smoke

    register_source_asset(
        "demo",
        "openapi-main",
        "openapi: 3.0.0\npaths:\n  /api/orders:\n    get:\n      responses:\n        '200': {description: ok}\n",
        source_type="openapi",
        root=tmp_path,
        actor={"name": "tester", "role": "qa_lead"},
    )
    payload = run_acceptance_smoke(
        root=tmp_path,
        install_patches=True,
        write_doctor=False,
        project="demo",
        scan_base_url="http://127.0.0.1:8000",
        scope_id="orders-scope",
        environment_ref="staging-env",
        test_data_strategy="synthetic_only",
        require_scenario_ready=True,
    )

    scenario = payload["checks"]["scenario_readiness"]
    assert scenario["ready"] is True
    assert scenario["missing"] == []
    assert scenario["source_registry"]["asset_count"] == 1
    assert scenario["source_registry"]["assets"][0]["source_id"] == "openapi-main"
    assert payload["acceptance"]["level"] in {"accepted", "warning"}
    assert "private_pilot_acceptance_smoke_report.json" in payload["customer_acceptance_summary"]["safe_report_paths"][0]
    assert not any(str(item).startswith("scenario_readiness_missing:") for item in payload["acceptance"].get("blockers", []))


def test_acceptance_summary_contains_bilingual_copyable_text(tmp_path: Path) -> None:
    from ai_test_asset_center.private_pilot_acceptance_smoke import run_acceptance_smoke

    payload = run_acceptance_smoke(root=tmp_path, install_patches=True, write_doctor=False, require_scenario_ready=True)
    summary = payload["customer_acceptance_summary"]

    assert summary["schema_version"] == "customer-acceptance-summary-v1"
    assert summary["level"] == payload["acceptance"]["level"]
    assert "客户验收结果" in summary["zh_text"]
    assert "Customer acceptance result" in summary["en_text"]
    assert "private_pilot_doctor_report.json" in "\n".join(summary["safe_report_paths"])
    assert summary["next_commands"]


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
    assert '"scenario_readiness"' in captured
    assert '"customer_acceptance_summary"' in captured


def test_acceptance_smoke_cli_script_is_registered() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'qualibug-acceptance-smoke = "ai_test_asset_center.private_pilot_acceptance_smoke:main"' in pyproject
