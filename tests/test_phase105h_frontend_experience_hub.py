from __future__ import annotations

import json

from ai_test_asset_center.phase105_frontend_experience_hub import (
    CORE_FRONTEND_HUB_LABELS,
    build_frontend_experience_hub,
    run_frontend_experience_hub_export,
    scan_frontend_hub_for_secret_leaks,
    validate_frontend_experience_hub,
)


def test_phase105h_builds_unified_frontend_hub(tmp_path) -> None:
    manifest = build_frontend_experience_hub(tmp_path, scenario="manufacturing", api_base_url="http://127.0.0.1:8790")

    assert manifest["version"].startswith("phase105h")
    assert manifest["redaction_status"] == "safe"
    assert manifest["entrypoint"] == "index.html"
    assert manifest["page_count"] == 7
    assert manifest["ready_page_count"] == 7
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "phase105_frontend_experience_hub.zip").exists()

    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    for label in CORE_FRONTEND_HUB_LABELS:
        assert label in html
    for rel in [
        "pages/dashboard/dashboard.html",
        "pages/customer_intake/customer_intake.html",
        "pages/environment_diagnosis/environment_diagnosis.html",
        "pages/business_flow_map/business_flow_map.html",
        "pages/risk_evidence/risk_evidence.html",
        "pages/report_roi/report_roi.html",
    ]:
        assert rel in html
        assert (tmp_path / rel).exists()

    data = json.loads((tmp_path / "data" / "frontend_experience_hub_data.json").read_text(encoding="utf-8"))
    assert data["readiness"]["page_count"] == 7
    assert data["readiness"]["readiness_rate"] == 100
    assert len(data["journey_steps"]) == 5
    assert {page["key"] for page in data["pages"]} >= {"dashboard", "customer_intake", "risk_evidence", "report_roi"}


def test_phase105h_acceptance_and_validate_only(tmp_path) -> None:
    result = run_frontend_experience_hub_export(output_dir=tmp_path, scenario="ecommerce", api_base_url="http://127.0.0.1:8790")

    assert result["acceptance"]["passed"] is True
    assert result["acceptance"]["score"] == 100
    assert (tmp_path / "frontend_experience_hub_acceptance_report.json").exists()
    assert "Phase105H 前端显示层总装验收报告" in (tmp_path / "frontend_experience_hub_acceptance_report.md").read_text(encoding="utf-8")

    validate_result = run_frontend_experience_hub_export(output_dir=tmp_path, validate_only=True)
    assert validate_result["acceptance"]["passed"] is True


def test_phase105h_detects_missing_page_and_secret_leak(tmp_path) -> None:
    build_frontend_experience_hub(tmp_path, create_zip=False)
    (tmp_path / "pages" / "dashboard" / "dashboard.html").unlink()
    (tmp_path / "assets" / "qualibug_frontend_hub.js").write_text("const bad = 'client_secret=raw';", encoding="utf-8")

    report = validate_frontend_experience_hub(tmp_path)
    assert report.passed is False
    details = "\n".join(check.detail for check in report.checks)
    assert "质量驾驶舱" in details or "dashboard" in details
    assert "client_secret=" in details
    assert scan_frontend_hub_for_secret_leaks(tmp_path)
