from __future__ import annotations

import json

from ai_test_asset_center.phase103_preview_server import Phase103PreviewSite, main, serve_preview


def test_phase103v_serves_static_index_and_assets(tmp_path) -> None:
    site = Phase103PreviewSite(scenario="manufacturing", static_dir=tmp_path)

    index = site.route("/")
    css = site.route("/assets/phase103_ui.css")
    data = site.route("/assets/phase103_demo_data.js")

    assert index.status == 200
    assert "text/html" in index.headers["Content-Type"]
    assert "QualiBug AI" in index.body.decode("utf-8")
    assert css.status == 200
    assert "--red" in css.body.decode("utf-8")
    assert data.status == 200
    payload = data.body.decode("utf-8")
    assert "PHASE103_DEMO_DATA" in payload
    assert "raw-manufacturing-token" not in payload
    assert "DemoPasswordShouldBeRedacted" not in payload


def test_phase103v_exposes_customer_safe_api_routes(tmp_path) -> None:
    site = Phase103PreviewSite(scenario="ecommerce", static_dir=tmp_path)
    project_id = site.project_id

    health = site.route("/api/v1/preview/health").json_body()
    projects = site.route("/api/v1/projects").json_body()
    dashboard = site.route(f"/api/v1/projects/{project_id}/command-center").json_body()
    live_map = site.route(f"/api/v1/projects/{project_id}/live-map").json_body()
    risks = site.route(f"/api/v1/projects/{project_id}/risks").json_body()
    value = site.route(f"/api/v1/projects/{project_id}/value-metrics").json_body()
    report = site.route(f"/api/v1/projects/{project_id}/reports/executive").json_body()

    assert health["success"] is True
    assert health["data"]["redaction_status"] == "safe"
    assert projects["success"] is True
    assert projects["data"][0]["project_id"] == project_id
    assert dashboard["data"]["quality_health_score"] >= 0
    assert live_map["data"]["nodes"]
    assert risks["data"]
    assert value["data"]["estimated_hours_saved"] >= 0
    assert "上线" in report["data"]["executive_summary"]

    combined = json.dumps([projects, dashboard, live_map, risks, value, report], ensure_ascii=False)
    assert "raw-ecommerce-token" not in combined
    assert "DemoPasswordShouldBeRedacted" not in combined
    assert "SESSION=raw" not in combined


def test_phase103v_serves_risk_detail_and_filtered_events(tmp_path) -> None:
    site = Phase103PreviewSite(scenario="saas", static_dir=tmp_path)
    project_id = site.project_id
    risks = site.route(f"/api/v1/projects/{project_id}/risks").json_body()["data"]
    first_risk_id = risks[0]["risk_id"]

    detail = site.route(f"/api/v1/projects/{project_id}/risks/{first_risk_id}").json_body()
    events = site.route(f"/api/v1/projects/{project_id}/live-map/events?since=2000-01-01T00:00:00Z").json_body()

    assert detail["success"] is True
    assert detail["data"]["risk"]["risk_id"] == first_risk_id
    assert detail["data"]["evidence_bundle"]["redaction_status"] == "safe"
    assert events["success"] is True
    assert isinstance(events["data"], list)


def test_phase103v_rejects_unknown_paths_and_write_methods(tmp_path) -> None:
    site = Phase103PreviewSite(scenario="manufacturing", static_dir=tmp_path)

    missing = site.route("/does-not-exist.html")
    post = site.route("/api/v1/projects", method="POST")
    traversal = site.route("/../secret.txt")

    assert missing.status == 404
    assert missing.json_body()["error"]["code"] == "NOT_FOUND"
    assert post.status == 405
    assert post.json_body()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert traversal.status == 404


def test_phase103v_check_mode_builds_preview_without_serving(tmp_path, capsys) -> None:
    manifest = serve_preview(scenario="manufacturing", output_dir=tmp_path / "site", port=8791, serve_forever=False)

    assert manifest["scenario"] == "manufacturing"
    assert manifest["entrypoint"] == "/index.html"
    assert (tmp_path / "site" / "index.html").exists()
    assert "command_center" in manifest["api_routes"]

    exit_code = main(["--scenario", "saas", "--output-dir", str(tmp_path / "cli_site"), "--port", "8792", "--check"])
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "Phase103 preview site generated" in captured
    assert (tmp_path / "cli_site" / "dashboard.html").exists()
