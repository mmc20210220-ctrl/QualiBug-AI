from __future__ import annotations

import json

from ai_test_asset_center.phase105_environment_diagnosis_experience import (
    CORE_ENVIRONMENT_DIAGNOSIS_LABELS,
    build_environment_diagnosis_experience,
    run_environment_diagnosis_experience_export,
    scan_environment_diagnosis_for_secret_leaks,
    validate_environment_diagnosis_experience,
)


def test_phase105d_generates_environment_diagnosis_page(tmp_path) -> None:
    manifest = build_environment_diagnosis_experience(tmp_path, scenario="manufacturing", api_base_url="http://127.0.0.1:8790")

    assert manifest["version"].startswith("phase105d")
    assert manifest["redaction_status"] == "safe"
    assert manifest["entrypoint"] == "environment_diagnosis.html"
    assert (tmp_path / "environment_diagnosis.html").exists()
    assert (tmp_path / "assets" / "qualibug_environment_diagnosis.css").exists()
    assert (tmp_path / "assets" / "qualibug_environment_diagnosis.js").exists()

    html = (tmp_path / "environment_diagnosis.html").read_text(encoding="utf-8")
    for label in ["环境诊断中心", "可测性评分", "阻断原因", "URL / DNS / HTTP", "认证与会话", "API Smoke", "客户补料清单", "重新预检"]:
        assert label in html

    data = json.loads((tmp_path / "data" / "environment_diagnosis_experience_data.json").read_text(encoding="utf-8"))
    assert data["readiness_summary"]["score"] >= 0
    assert data["readiness_summary"]["status_label"]
    assert data["check_cards"]
    assert data["api_smoke"]["items"]
    assert data["required_customer_inputs"]
    assert data["phase104_actions"]["run_preflight"].startswith("POST /api/v1/projects/")
    assert set(manifest["core_labels"]) == set(CORE_ENVIRONMENT_DIAGNOSIS_LABELS)


def test_phase105d_acceptance_report_and_validate_only(tmp_path) -> None:
    result = run_environment_diagnosis_experience_export(output_dir=tmp_path, scenario="saas", api_base_url="http://127.0.0.1:8790")

    assert result["acceptance"]["passed"] is True
    assert result["acceptance"]["score"] == 100
    assert (tmp_path / "environment_diagnosis_experience_acceptance_report.json").exists()
    assert (tmp_path / "environment_diagnosis_experience_acceptance_report.md").exists()
    assert "Phase105D 环境诊断中心体验验收报告" in (tmp_path / "environment_diagnosis_experience_acceptance_report.md").read_text(encoding="utf-8")

    validate_result = run_environment_diagnosis_experience_export(output_dir=tmp_path, validate_only=True)
    assert validate_result["acceptance"]["passed"] is True


def test_phase105d_detects_missing_page_and_secret_leak(tmp_path) -> None:
    build_environment_diagnosis_experience(tmp_path)
    (tmp_path / "assets" / "qualibug_environment_diagnosis.js").write_text("const bad = 'client_secret=raw';", encoding="utf-8")
    (tmp_path / "environment_diagnosis.html").unlink()

    report = validate_environment_diagnosis_experience(tmp_path)
    assert report.passed is False
    details = "\n".join(check.detail for check in report.checks)
    assert "environment_diagnosis.html" in details
    assert "client_secret=" in details
    assert scan_environment_diagnosis_for_secret_leaks(tmp_path)
