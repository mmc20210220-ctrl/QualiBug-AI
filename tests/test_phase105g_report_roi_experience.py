from __future__ import annotations

import json

from ai_test_asset_center.phase105_report_roi_experience import (
    CORE_REPORT_ROI_LABELS,
    build_report_roi_experience,
    run_report_roi_experience_export,
    scan_report_roi_for_secret_leaks,
    validate_report_roi_experience,
)


def test_phase105g_generates_report_roi_page(tmp_path) -> None:
    manifest = build_report_roi_experience(tmp_path, scenario="manufacturing", api_base_url="http://127.0.0.1:8790")

    assert manifest["version"].startswith("phase105g")
    assert manifest["redaction_status"] == "safe"
    assert manifest["entrypoint"] == "report_roi.html"
    assert manifest["roi_card_count"] >= 4
    assert manifest["top_risk_count"] >= 1
    assert (tmp_path / "report_roi.html").exists()
    assert (tmp_path / "assets" / "qualibug_report_roi.css").exists()
    assert (tmp_path / "assets" / "qualibug_report_roi.js").exists()

    html = (tmp_path / "report_roi.html").read_text(encoding="utf-8")
    for label in ["领导层报告", "ROI 价值中心", "上线建议", "执行摘要", "风险价值", "节省工时", "业务影响区间", "可复制摘要", "默认脱敏"]:
        assert label in html

    data = json.loads((tmp_path / "data" / "report_roi_experience_data.json").read_text(encoding="utf-8"))
    assert data["decision_card"]["label"] in {"不建议上线", "暂缓上线", "可灰度上线", "建议上线"}
    assert data["executive_report"]["report_sections"]
    assert data["roi_value_center"]["roi_cards"]
    assert data["copy_blocks"]["meeting_note"]
    assert data["phase104_actions"]["read_executive_report"].endswith("/report")
    assert set(manifest["core_labels"]) == set(CORE_REPORT_ROI_LABELS)


def test_phase105g_acceptance_report_and_validate_only(tmp_path) -> None:
    result = run_report_roi_experience_export(output_dir=tmp_path, scenario="ecommerce", api_base_url="http://127.0.0.1:8790")

    assert result["acceptance"]["passed"] is True
    assert result["acceptance"]["score"] == 100
    assert (tmp_path / "report_roi_experience_acceptance_report.json").exists()
    assert (tmp_path / "report_roi_experience_acceptance_report.md").exists()
    assert "Phase105G 领导层报告 + ROI 价值中心验收报告" in (tmp_path / "report_roi_experience_acceptance_report.md").read_text(encoding="utf-8")

    validate_result = run_report_roi_experience_export(output_dir=tmp_path, validate_only=True)
    assert validate_result["acceptance"]["passed"] is True


def test_phase105g_detects_missing_page_and_secret_leak(tmp_path) -> None:
    build_report_roi_experience(tmp_path)
    (tmp_path / "assets" / "qualibug_report_roi.js").write_text("const bad = 'client_secret=raw';", encoding="utf-8")
    (tmp_path / "report_roi.html").unlink()

    report = validate_report_roi_experience(tmp_path)
    assert report.passed is False
    details = "\n".join(check.detail for check in report.checks)
    assert "report_roi.html" in details
    assert "client_secret=" in details
    assert scan_report_roi_for_secret_leaks(tmp_path)
