from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.phase105_frontend_preview_server import (
    PREVIEW_API_PREFIX,
    Phase105FrontendPreviewSite,
    main,
    serve_frontend_preview,
)


def test_phase105m_builds_preview_site_and_read_only_apis(tmp_path: Path) -> None:
    site = Phase105FrontendPreviewSite(bundle_dir=tmp_path, scenario="manufacturing")

    health = site.route(f"{PREVIEW_API_PREFIX}/health").json_body()
    assert health["success"] is True
    assert health["data"]["status"] == "ok"
    assert health["data"]["page_count"] == 8

    manifest = site.route(f"{PREVIEW_API_PREFIX}/manifest").json_body()
    assert manifest["data"]["delivery_passed"] is True
    assert manifest["data"]["redaction_status"] == "safe"

    pages = site.route(f"{PREVIEW_API_PREFIX}/pages").json_body()["data"]
    assert pages["count"] == 8
    assert any(page["key"] == "test_execution" for page in pages["pages"])

    acceptance = site.route(f"{PREVIEW_API_PREFIX}/acceptance").json_body()["data"]
    assert acceptance["delivery_report"]["passed"] is True
    assert acceptance["interaction_acceptance"]["passed"] is True


def test_phase105m_serves_static_bundle_and_blocks_unsafe_methods(tmp_path: Path) -> None:
    site = Phase105FrontendPreviewSite(bundle_dir=tmp_path, scenario="manufacturing")

    root = site.route("/")
    assert root.status == 200
    assert "text/html" in root.headers["Content-Type"]
    assert "前端显示层" in root.body.decode("utf-8")

    execution_page = site.route("/pages/test_execution/test_execution.html")
    assert execution_page.status == 200
    assert "实时测试执行" in execution_page.body.decode("utf-8")

    handoff = site.route(f"{PREVIEW_API_PREFIX}/handoff").json_body()["data"]
    assert any(doc["name"] == "DEMO_RUNBOOK.md" and doc["exists"] for doc in handoff["docs"])

    checksums = site.route(f"{PREVIEW_API_PREFIX}/checksums").json_body()["data"]
    assert checksums["count"] > 10
    assert any(entry["path"] == "hub_v2/index.html" for entry in checksums["entries"])

    assert site.route("/../../etc/passwd").status == 404
    assert site.route(f"{PREVIEW_API_PREFIX}/health", method="POST").status == 405
    assert site.route(f"{PREVIEW_API_PREFIX}/health", method="OPTIONS").status == 200


def test_phase105m_cli_check_and_existing_bundle_mode(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    assert main(["--check", "--bundle-dir", str(bundle_dir), "--scenario", "manufacturing"]) == 0

    manifest = serve_frontend_preview(
        bundle_dir=bundle_dir,
        scenario="manufacturing",
        build_bundle=False,
        serve_forever=False,
        port=8796,
    )

    assert manifest["url"] == "http://127.0.0.1:8796/"
    assert manifest["delivery_passed"] is True
    assert manifest["page_count"] == 8
    assert (bundle_dir / "frontend_preview_server_manifest.json").exists()
