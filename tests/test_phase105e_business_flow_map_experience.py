from __future__ import annotations

import json

from ai_test_asset_center.phase105_business_flow_map_experience import (
    CORE_BUSINESS_FLOW_MAP_LABELS,
    build_business_flow_map_experience,
    run_business_flow_map_experience_export,
    scan_business_flow_map_for_secret_leaks,
    validate_business_flow_map_experience,
)


def test_phase105e_generates_business_flow_map_page(tmp_path) -> None:
    manifest = build_business_flow_map_experience(tmp_path, scenario="manufacturing", api_base_url="http://127.0.0.1:8088")

    assert manifest["version"].startswith("phase105e")
    assert manifest["redaction_status"] == "safe"
    assert manifest["entrypoint"] == "business_flow_map.html"
    assert (tmp_path / "business_flow_map.html").exists()
    assert (tmp_path / "assets" / "qualibug_business_flow_map.css").exists()
    assert (tmp_path / "assets" / "qualibug_business_flow_map.js").exists()

    html = (tmp_path / "business_flow_map.html").read_text(encoding="utf-8")
    for label in ["业务流程地图", "AI 已理解的业务链路", "节点覆盖状态", "风险爆点", "环境阻断链路", "证据回流", "聚焦高危风险"]:
        assert label in html

    data = json.loads((tmp_path / "data" / "business_flow_map_experience_data.json").read_text(encoding="utf-8"))
    assert data["map_summary"]["total_flows"] >= 3
    assert data["flow_lanes"]
    assert all(lane["nodes"] for lane in data["flow_lanes"])
    assert data["risk_overlays"]
    assert data["event_timeline"]
    assert data["phase104_actions"]["read_live_map"].startswith("GET /api/v1/projects/")
    assert set(manifest["core_labels"]) == set(CORE_BUSINESS_FLOW_MAP_LABELS)


def test_phase105e_acceptance_report_and_validate_only(tmp_path) -> None:
    result = run_business_flow_map_experience_export(output_dir=tmp_path, scenario="ecommerce", api_base_url="http://127.0.0.1:8088")

    assert result["acceptance"]["passed"] is True
    assert result["acceptance"]["score"] == 100
    assert (tmp_path / "business_flow_map_experience_acceptance_report.json").exists()
    assert (tmp_path / "business_flow_map_experience_acceptance_report.md").exists()
    assert "Phase105E 业务流程地图体验验收报告" in (tmp_path / "business_flow_map_experience_acceptance_report.md").read_text(encoding="utf-8")

    validate_result = run_business_flow_map_experience_export(output_dir=tmp_path, validate_only=True)
    assert validate_result["acceptance"]["passed"] is True


def test_phase105e_detects_missing_page_and_secret_leak(tmp_path) -> None:
    build_business_flow_map_experience(tmp_path)
    (tmp_path / "assets" / "qualibug_business_flow_map.js").write_text("const bad = 'client_secret=raw';", encoding="utf-8")
    (tmp_path / "business_flow_map.html").unlink()

    report = validate_business_flow_map_experience(tmp_path)
    assert report.passed is False
    details = "\n".join(check.detail for check in report.checks)
    assert "business_flow_map.html" in details
    assert "client_secret=" in details
    assert scan_business_flow_map_for_secret_leaks(tmp_path)

