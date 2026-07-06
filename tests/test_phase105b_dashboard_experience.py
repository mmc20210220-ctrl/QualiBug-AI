from __future__ import annotations

import json

from ai_test_asset_center.phase105_dashboard_experience import (
    CORE_DASHBOARD_LABELS,
    build_dashboard_experience,
    run_dashboard_experience_export,
    scan_dashboard_experience_for_secret_leaks,
    validate_dashboard_experience,
)


def test_phase105b_generates_dashboard_experience_blocks(tmp_path) -> None:
    manifest = build_dashboard_experience(tmp_path, scenario="manufacturing", api_base_url="http://127.0.0.1:8088")

    assert manifest["version"].startswith("phase105b")
    assert manifest["redaction_status"] == "safe"
    assert (tmp_path / "dashboard.html").exists()
    assert (tmp_path / "assets" / "qualibug_dashboard_experience.css").exists()
    assert (tmp_path / "assets" / "qualibug_dashboard_experience.js").exists()

    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    for label in CORE_DASHBOARD_LABELS:
        assert label in html

    data = json.loads((tmp_path / "data" / "dashboard_experience_data.json").read_text(encoding="utf-8"))
    assert data["dashboard_title"] == "QualiBug AI 企业质量驾驶舱"
    assert len(data["kpis"]) >= 6
    assert {item["key"] for item in data["kpis"]} >= {"quality_score", "launch_decision", "blocking_risks", "environment_score"}
    assert data["business_flow_cards"]
    assert data["top_risks"]
    assert data["action_queue"]
    assert data["roi"]["estimated_hours_saved"] >= 0


def test_phase105b_acceptance_report_and_validate_only(tmp_path) -> None:
    result = run_dashboard_experience_export(output_dir=tmp_path, scenario="saas", api_base_url="http://127.0.0.1:8088")

    assert result["acceptance"]["passed"] is True
    assert result["acceptance"]["score"] == 100
    assert (tmp_path / "dashboard_experience_acceptance_report.json").exists()
    md = (tmp_path / "dashboard_experience_acceptance_report.md").read_text(encoding="utf-8")
    assert "Phase105B 质量驾驶舱体验验收报告" in md

    validate_result = run_dashboard_experience_export(output_dir=tmp_path, validate_only=True)
    assert validate_result["acceptance"]["passed"] is True


def test_phase105b_detects_missing_dashboard_and_secret_leak(tmp_path) -> None:
    build_dashboard_experience(tmp_path)
    (tmp_path / "assets" / "qualibug_dashboard_experience.js").write_text("const leaked = 'Bearer raw';", encoding="utf-8")
    (tmp_path / "dashboard.html").unlink()

    report = validate_dashboard_experience(tmp_path)
    assert report.passed is False
    details = "\n".join(check.detail for check in report.checks)
    assert "dashboard.html" in details
    assert "Bearer raw" in details
    assert scan_dashboard_experience_for_secret_leaks(tmp_path)

