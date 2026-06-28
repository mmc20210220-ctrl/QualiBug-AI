from __future__ import annotations

import json

from ai_test_asset_center.phase103_command_center_api import EnterpriseCommandCenterAPI
from ai_test_asset_center.phase103_demo_runner import seed_demo_project
from ai_test_asset_center.phase103_static_frontend_exporter import (
    build_and_export_static_frontend,
    export_static_frontend_bundle,
    main,
    render_css,
    render_dashboard_page,
    render_static_pages,
)


def test_phase103u_renders_dashboard_with_executive_metrics_and_safe_content() -> None:
    bundle = seed_demo_project(EnterpriseCommandCenterAPI(), scenario="manufacturing")
    html = render_dashboard_page(bundle)

    assert "企业质量驾驶舱" in html
    assert "质量健康分" in html
    assert "上线建议" in html
    assert "预计节省工时" in html
    assert "当前最需关注风险" in html
    assert "raw-manufacturing-token" not in html
    assert "DemoPasswordShouldBeRedacted" not in html
    assert "raw-manufacturing-session" not in html


def test_phase103u_renders_all_v1_pages() -> None:
    bundle = seed_demo_project(EnterpriseCommandCenterAPI(), scenario="ecommerce")
    pages = render_static_pages(bundle)

    expected = {
        "index.html",
        "dashboard.html",
        "environment.html",
        "test_plan.html",
        "live_map.html",
        "risks.html",
        "report.html",
        "value.html",
    }
    assert expected == set(pages)
    assert "客户环境适配中心" in pages["environment.html"]
    assert "AI 测试计划中心" in pages["test_plan.html"]
    assert "实时 AI 测试地图" in pages["live_map.html"]
    assert "AI 风险发现中心" in pages["risks.html"]
    assert "领导层成果战报" in pages["report.html"]
    assert "AI 质量价值分析" in pages["value.html"]


def test_phase103u_exports_static_frontend_bundle(tmp_path) -> None:
    bundle = seed_demo_project(EnterpriseCommandCenterAPI(), scenario="saas")
    manifest = export_static_frontend_bundle(bundle, tmp_path)

    expected_files = {
        "index.html",
        "dashboard.html",
        "environment.html",
        "test_plan.html",
        "live_map.html",
        "risks.html",
        "report.html",
        "value.html",
        "assets/phase103_ui.css",
        "assets/phase103_demo_data.js",
        "README_static_frontend.md",
        "static_frontend_manifest.json",
    }
    assert expected_files.issubset(set(manifest["files"]))
    assert manifest["entrypoint"] == "index.html"
    assert manifest["redaction_status"] == "safe"

    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    data_js = (tmp_path / "assets" / "phase103_demo_data.js").read_text(encoding="utf-8")
    css = (tmp_path / "assets" / "phase103_ui.css").read_text(encoding="utf-8")
    manifest_json = json.loads((tmp_path / "static_frontend_manifest.json").read_text(encoding="utf-8"))

    assert "QualiBug AI" in index
    assert "PHASE103_DEMO_DATA" in data_js
    assert "raw-saas-token" not in index + data_js
    assert "client_secret" not in index + data_js or "****" in data_js
    assert "--red" in css
    assert manifest_json["pages"]["dashboard"] == "dashboard.html"


def test_phase103u_build_and_export_cli(tmp_path, capsys) -> None:
    output = tmp_path / "frontend"
    result = build_and_export_static_frontend(scenario="manufacturing", output_dir=output)

    assert result["manifest"]["scenario"] == "manufacturing"
    assert (output / "index.html").exists()

    exit_code = main(["--scenario", "ecommerce", "--output-dir", str(tmp_path / "cli_frontend")])
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "Phase103 static frontend generated" in captured
    assert (tmp_path / "cli_frontend" / "dashboard.html").exists()


def test_phase103u_css_contains_enterprise_command_center_tokens() -> None:
    css = render_css()

    assert ":root" in css
    assert "--blue" in css
    assert "--green" in css
    assert "--red" in css
    assert ".risk-card" in css
    assert ".map-node" in css
