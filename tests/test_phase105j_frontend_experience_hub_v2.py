from __future__ import annotations

import json

from ai_test_asset_center.phase105_frontend_experience_hub_v2 import (
    CORE_FRONTEND_HUB_V2_LABELS,
    build_frontend_experience_hub_v2,
    run_frontend_experience_hub_v2_export,
    scan_frontend_hub_v2_for_secret_leaks,
    validate_frontend_experience_hub_v2,
)


def test_phase105j_builds_hub_v2_with_test_execution_page(tmp_path) -> None:
    manifest = build_frontend_experience_hub_v2(tmp_path, scenario="manufacturing", api_base_url="http://127.0.0.1:8790")

    assert manifest["version"].startswith("phase105j")
    assert manifest["redaction_status"] == "safe"
    assert manifest["entrypoint"] == "index.html"
    assert manifest["page_count"] == 8
    assert manifest["ready_page_count"] == 8
    assert manifest["execution_page"] == "pages/test_execution/test_execution.html"
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "phase105_frontend_experience_hub_v2.zip").exists()

    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    for label in CORE_FRONTEND_HUB_V2_LABELS:
        assert label in html
    for rel in [
        "pages/dashboard/dashboard.html",
        "pages/customer_intake/customer_intake.html",
        "pages/environment_diagnosis/environment_diagnosis.html",
        "pages/business_flow_map/business_flow_map.html",
        "pages/test_execution/test_execution.html",
        "pages/risk_evidence/risk_evidence.html",
        "pages/report_roi/report_roi.html",
    ]:
        assert rel in html
        assert (tmp_path / rel).exists()

    data = json.loads((tmp_path / "data" / "frontend_experience_hub_v2_data.json").read_text(encoding="utf-8"))
    assert data["readiness"]["page_count"] == 8
    assert data["readiness"]["readiness_rate"] == 100
    assert len(data["journey_steps"]) == 6
    page_keys = {page["key"] for page in data["pages"]}
    assert {"dashboard", "customer_intake", "business_flow_map", "test_execution", "risk_evidence", "report_roi"} <= page_keys


def test_phase105j_acceptance_and_validate_only(tmp_path) -> None:
    result = run_frontend_experience_hub_v2_export(output_dir=tmp_path, scenario="ecommerce", api_base_url="http://127.0.0.1:8790")

    assert result["acceptance"]["passed"] is True
    assert result["acceptance"]["score"] == 100
    assert (tmp_path / "frontend_experience_hub_v2_acceptance_report.json").exists()
    assert "Phase105J 前端显示层总装 V2 验收报告" in (tmp_path / "frontend_experience_hub_v2_acceptance_report.md").read_text(encoding="utf-8")

    validate_result = run_frontend_experience_hub_v2_export(output_dir=tmp_path, validate_only=True)
    assert validate_result["acceptance"]["passed"] is True


def test_phase105j_detects_missing_execution_page_and_secret_leak(tmp_path) -> None:
    build_frontend_experience_hub_v2(tmp_path, create_zip=False)
    (tmp_path / "pages" / "test_execution" / "test_execution.html").unlink()
    (tmp_path / "assets" / "qualibug_frontend_hub_v2.js").write_text("const bad = 'client_secret=raw';", encoding="utf-8")

    report = validate_frontend_experience_hub_v2(tmp_path)
    assert report.passed is False
    details = "\n".join(check.detail for check in report.checks)
    assert "AI 测试" in details or "test_execution" in details
    assert "client_secret=" in details
    assert scan_frontend_hub_v2_for_secret_leaks(tmp_path)
