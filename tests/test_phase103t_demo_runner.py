from __future__ import annotations

import json

from ai_test_asset_center.phase103_command_center_api import EnterpriseCommandCenterAPI
from ai_test_asset_center.phase103_demo_runner import (
    build_and_export_demo,
    export_demo_bundle,
    get_demo_scenario,
    list_demo_scenarios,
    main,
    render_demo_markdown,
    seed_demo_project,
)


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def test_phase103t_lists_supported_demo_scenarios() -> None:
    scenarios = list_demo_scenarios()
    keys = {item["scenario"] for item in scenarios}

    assert {"manufacturing", "ecommerce", "saas"}.issubset(keys)
    assert all(item["display_name"] for item in scenarios)
    assert get_demo_scenario("manufacturing")["project"]["password"] == "****" if "password" in get_demo_scenario("manufacturing")["project"] else True


def test_phase103t_seed_manufacturing_demo_project_end_to_end() -> None:
    api = EnterpriseCommandCenterAPI()
    bundle = seed_demo_project(api, scenario="manufacturing")
    dumped = _dump(bundle)

    assert bundle["project_id"] == "demo_manufacturing_erp_v3"
    assert bundle["business_model"]["industry"] == "manufacturing"
    assert bundle["environment_readiness"]["status"] in {"ready", "partial_ready", "needs_customer_input"}
    assert bundle["test_plan"]["estimated_value"]["equivalent_test_points"] > 0
    assert bundle["test_run"]["risk_found"] == 3
    assert bundle["command_center"]["risk_summary"]["launch_blocking"] >= 2
    assert bundle["command_center"]["launch_decision"]["recommendation"] in {"NO_GO", "HOLD"}
    assert bundle["live_map"]["nodes"]
    assert bundle["live_map"]["risk_overlays"]
    assert len(bundle["risk_details"]) == len(bundle["risks"])
    assert "AI 价值量化" in bundle["executive_report"]["markdown"]
    assert bundle["value_metrics"]["estimated_hours_saved"] > 0
    assert "raw-manufacturing-token" not in dumped
    assert "DemoPasswordShouldBeRedacted" not in dumped
    assert "raw-manufacturing-session" not in dumped


def test_phase103t_seed_ecommerce_demo_has_payment_and_auth_risks() -> None:
    bundle = seed_demo_project(EnterpriseCommandCenterAPI(), scenario="ecommerce")
    titles = " ".join(risk["title"] for risk in bundle["risks"])

    assert bundle["business_model"]["industry"] == "ecommerce"
    assert "支付" in titles
    assert any(risk["launch_blocking"] for risk in bundle["risks"])
    assert bundle["command_center"]["business_flow_summary"]["total"] >= 1


def test_phase103t_render_markdown_is_customer_safe_and_executive_friendly() -> None:
    bundle = seed_demo_project(EnterpriseCommandCenterAPI(), scenario="saas")
    markdown = render_demo_markdown(bundle)

    assert "上线建议" in markdown
    assert "当前最需关注风险" in markdown
    assert "跨租户" in markdown
    assert "token" in markdown.lower()  # appears only in safety notice, not as raw secret
    assert "raw-saas-token" not in markdown


def test_phase103t_export_demo_bundle_writes_page_ready_json(tmp_path) -> None:
    bundle = seed_demo_project(EnterpriseCommandCenterAPI(), scenario="manufacturing")
    manifest = export_demo_bundle(bundle, tmp_path)

    expected = {
        "project.json",
        "business_model.json",
        "environment_readiness.json",
        "test_plan.json",
        "command_center.json",
        "live_map.json",
        "risks.json",
        "risk_details.json",
        "value_metrics.json",
        "executive_report.json",
        "frontend_pages.json",
        "README_demo_summary.md",
        "manifest.json",
    }
    assert expected.issubset(set(manifest["files"]))
    command_center = json.loads((tmp_path / "command_center.json").read_text(encoding="utf-8"))
    summary = (tmp_path / "README_demo_summary.md").read_text(encoding="utf-8")
    all_text = (tmp_path / "frontend_pages.json").read_text(encoding="utf-8") + summary

    assert command_center["quality_health_score"] >= 0
    assert "上线建议" in summary
    assert "raw-manufacturing-token" not in all_text
    assert "DemoPasswordShouldBeRedacted" not in all_text


def test_phase103t_build_and_export_demo_and_cli(tmp_path, capsys) -> None:
    output = tmp_path / "cli_demo"
    result = build_and_export_demo(scenario="ecommerce", output_dir=output)

    assert result["manifest"]["scenario"] == "ecommerce"
    assert (output / "manifest.json").exists()

    exit_code = main(["--scenario", "saas", "--output-dir", str(tmp_path / "saas_demo")])
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "Phase103 demo generated" in captured
    assert (tmp_path / "saas_demo" / "frontend_pages.json").exists()
