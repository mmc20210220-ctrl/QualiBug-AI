from __future__ import annotations

import json

from ai_test_asset_center.phase105_test_execution_experience import (
    CORE_TEST_EXECUTION_LABELS,
    build_test_execution_experience,
    run_test_execution_experience_export,
    scan_test_execution_for_secret_leaks,
    validate_test_execution_experience,
)


def test_phase105i_builds_ai_test_execution_page(tmp_path) -> None:
    manifest = build_test_execution_experience(tmp_path, scenario="manufacturing", api_base_url="http://127.0.0.1:8088")

    assert manifest["version"].startswith("phase105i")
    assert manifest["entrypoint"] == "test_execution.html"
    assert manifest["redaction_status"] == "safe"
    assert manifest["probe_group_count"] >= 1
    assert (tmp_path / "test_execution.html").exists()

    html = (tmp_path / "test_execution.html").read_text(encoding="utf-8")
    for label in CORE_TEST_EXECUTION_LABELS:
        assert label in html

    data = json.loads((tmp_path / "data" / "test_execution_experience_data.json").read_text(encoding="utf-8"))
    assert data["test_plan"]["coverage_summary"]["business_flow_total"] >= 1
    assert data["realtime_execution"]["summary"]["probe_executable"] > 0
    assert data["realtime_execution"]["summary"]["risk_event_count"] >= 1
    assert data["realtime_execution"]["evidence_events"]
    assert "start_test_run" in data["phase104_actions"]


def test_phase105i_acceptance_and_validate_only(tmp_path) -> None:
    result = run_test_execution_experience_export(output_dir=tmp_path, scenario="ecommerce", api_base_url="http://127.0.0.1:8088")

    assert result["acceptance"]["passed"] is True
    assert result["acceptance"]["score"] == 100
    assert (tmp_path / "test_execution_experience_acceptance_report.json").exists()
    assert "Phase105I AI 测试计划与实时执行页验收报告" in (
        tmp_path / "test_execution_experience_acceptance_report.md"
    ).read_text(encoding="utf-8")

    validate_result = run_test_execution_experience_export(output_dir=tmp_path, validate_only=True)
    assert validate_result["acceptance"]["passed"] is True


def test_phase105i_detects_missing_page_data_and_secret_leak(tmp_path) -> None:
    build_test_execution_experience(tmp_path, scenario="saas")
    (tmp_path / "data" / "test_execution_experience_data.json").unlink()
    (tmp_path / "assets" / "qualibug_test_execution.js").write_text("const bad = 'client_secret=raw';", encoding="utf-8")

    report = validate_test_execution_experience(tmp_path)
    assert report.passed is False
    details = "\n".join(check.detail for check in report.checks)
    assert "探针组" in details or "时间线" in details
    assert "client_secret=" in details
    assert scan_test_execution_for_secret_leaks(tmp_path)

