from __future__ import annotations

import json

from ai_test_asset_center.phase105_customer_intake_experience import (
    CORE_CUSTOMER_INTAKE_LABELS,
    build_customer_intake_experience,
    run_customer_intake_experience_export,
    scan_customer_intake_for_secret_leaks,
    validate_customer_intake_experience,
)


def test_phase105c_generates_customer_intake_page_and_phase104_payload(tmp_path) -> None:
    manifest = build_customer_intake_experience(tmp_path, scenario="manufacturing", api_base_url="http://127.0.0.1:8088")

    assert manifest["version"].startswith("phase105c")
    assert manifest["redaction_status"] == "safe"
    assert manifest["entrypoint"] == "customer_intake.html"
    assert (tmp_path / "customer_intake.html").exists()
    assert (tmp_path / "assets" / "qualibug_customer_intake.css").exists()
    assert (tmp_path / "assets" / "qualibug_customer_intake.js").exists()

    html = (tmp_path / "customer_intake.html").read_text(encoding="utf-8")
    for label in ["客户资料导入", "AI 分析入口", "业务链路草案", "角色与账号清单", "客户补料清单", "生成测试计划"]:
        assert label in html

    data = json.loads((tmp_path / "data" / "customer_intake_experience_data.json").read_text(encoding="utf-8"))
    assert data["project_draft"]["customer_name"]
    assert data["material_completion"]["completion_rate"] > 0
    assert data["ai_analysis_preview"]["industry_identification"]["detected_industry"] == "manufacturing"
    assert data["business_flow_candidates"]
    assert len(data["role_requirements"]) >= 3
    assert data["phase104_handoff_payload"]["project_payload"]["project_name"]
    assert set(manifest["core_labels"]) == set(CORE_CUSTOMER_INTAKE_LABELS)


def test_phase105c_acceptance_report_and_validate_only(tmp_path) -> None:
    result = run_customer_intake_experience_export(output_dir=tmp_path, scenario="saas", api_base_url="http://127.0.0.1:8088")

    assert result["acceptance"]["passed"] is True
    assert result["acceptance"]["score"] == 100
    assert (tmp_path / "customer_intake_experience_acceptance_report.json").exists()
    assert (tmp_path / "customer_intake_experience_acceptance_report.md").exists()
    assert "Phase105C 客户资料导入页体验验收报告" in (tmp_path / "customer_intake_experience_acceptance_report.md").read_text(encoding="utf-8")

    validate_result = run_customer_intake_experience_export(output_dir=tmp_path, validate_only=True)
    assert validate_result["acceptance"]["passed"] is True


def test_phase105c_detects_missing_page_and_secret_leak(tmp_path) -> None:
    build_customer_intake_experience(tmp_path)
    (tmp_path / "assets" / "qualibug_customer_intake.js").write_text("const bad = 'Bearer raw';", encoding="utf-8")
    (tmp_path / "customer_intake.html").unlink()

    report = validate_customer_intake_experience(tmp_path)
    assert report.passed is False
    details = "\n".join(check.detail for check in report.checks)
    assert "customer_intake.html" in details
    assert "Bearer raw" in details
    assert scan_customer_intake_for_secret_leaks(tmp_path)

