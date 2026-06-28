from __future__ import annotations

import json

from ai_test_asset_center.phase105_frontend_product_shell import (
    PAGE_NAV,
    build_frontend_product_shell,
    run_frontend_product_shell_export,
    scan_product_shell_for_secret_leaks,
    validate_frontend_product_shell,
)


def test_phase105a_generates_product_shell_pages_and_data(tmp_path) -> None:
    manifest = build_frontend_product_shell(tmp_path, scenario="manufacturing", api_base_url="http://127.0.0.1:8790")

    assert manifest["version"].startswith("phase105a")
    assert manifest["redaction_status"] == "safe"
    assert manifest["page_count"] == len(PAGE_NAV)
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "assets" / "qualibug_product_shell.css").exists()
    assert (tmp_path / "assets" / "qualibug_product_shell.js").exists()

    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    for label in ["质量驾驶舱", "客户资料导入", "环境诊断中心", "业务流程地图", "证据链详情", "ROI 价值中心"]:
        assert label in index

    data = json.loads((tmp_path / "data" / "product_shell_data.json").read_text(encoding="utf-8"))
    assert data["dashboard"]["quality_health_score"] >= 0
    assert data["dashboard"]["launch_decision"]["recommendation"]
    assert data["risks"]
    assert data["risk_detail"]["evidence_bundle"]["redaction_status"] == "safe"
    assert data["value_metrics"]["estimated_hours_saved"] >= 0
    assert {item["id"] for item in data["page_nav"]} >= {"dashboard", "environment", "risks", "evidence", "roi"}


def test_phase105a_acceptance_report_and_validate_only(tmp_path) -> None:
    result = run_frontend_product_shell_export(output_dir=tmp_path, scenario="saas", api_base_url="http://127.0.0.1:8790")

    assert result["acceptance"]["passed"] is True
    assert result["acceptance"]["score"] == 100
    assert (tmp_path / "product_shell_acceptance_report.json").exists()
    assert (tmp_path / "product_shell_acceptance_report.md").exists()
    assert "Phase105A 前端产品壳验收报告" in (tmp_path / "product_shell_acceptance_report.md").read_text(encoding="utf-8")

    validate_result = run_frontend_product_shell_export(output_dir=tmp_path, validate_only=True)
    assert validate_result["acceptance"]["passed"] is True


def test_phase105a_detects_missing_renderer_and_secret_leak(tmp_path) -> None:
    build_frontend_product_shell(tmp_path)
    (tmp_path / "assets" / "qualibug_product_shell.js").write_text("const bad = 'Bearer raw';", encoding="utf-8")
    (tmp_path / "index.html").unlink()

    report = validate_frontend_product_shell(tmp_path)
    assert report.passed is False
    details = "\n".join(check.detail for check in report.checks)
    assert "index.html" in details
    assert "Bearer raw" in details
    assert scan_product_shell_for_secret_leaks(tmp_path)
