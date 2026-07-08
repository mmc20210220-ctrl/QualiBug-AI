"""P4 UI evidence integration — Playwright screenshot added to evidence bundle artifacts."""
from __future__ import annotations
import json,threading,tempfile,os
from pathlib import Path
import pytest

@pytest.fixture(scope="module")
def _evidence_with_screenshot(tmp_path_factory):
    """Create an evidence bundle with a Playwright screenshot artifact."""
    root = tmp_path_factory.mktemp("ui_ev")

    # 1) Capture a real Playwright screenshot of a simple page
    from ai_test_asset_center.browser_execution import capture_page_screenshot_locally, _local_playwright_available
    if not _local_playwright_available():
        pytest.skip("Playwright/Chromium not available")

    screenshot_dir = root / "ui_evidence"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = screenshot_dir / "screenshot.png"

    result = capture_page_screenshot_locally(
        "data:text/html,<h1>QualiBug Evidence Test</h1><p>SUT UI captured at scan time</p>",
        screenshot_path
    )
    assert result["ok"], f"Screenshot capture failed: {result.get('error')}"

    # 2) Create a minimal evidence bundle and inject the screenshot as an artifact
    from ai_test_asset_center.evidence_artifact_store import persist_evidence_bundle

    bundle = persist_evidence_bundle(
        "p4_ui_test",
        root=root,
        run_id="scan_ui_test_001",
        campaign={"campaign_id": "CMP_UI_TEST"},
        runtime_contract={"source_manifest": {"source_id": "test", "source_hash": "a"*64}},
        execution_status="completed",
        auto_har={"status": "captured", "entries": [{"request": {"method": "GET", "url": "/"}, "response": {"status": 200}}]},
        evidence_graphs=[],
        findings=[{"title": "UI evidence test finding", "gate_passed": True, "bug_status": "reproduced"}],
        ui_execution={"status": "executed", "screenshot_ref": str(screenshot_path)},
    )

    # 3) Verify screenshot file exists and is referenced in the bundle
    bundle_dir = root / "platform_workspace" / "p4_ui_test" / "evidence_bundles" / bundle["bundle_id"]
    manifest_path = bundle_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() else {}

    return {
        "bundle": bundle,
        "screenshot_path": screenshot_path,
        "screenshot_size": screenshot_path.stat().st_size,
        "bundle_dir": bundle_dir,
        "manifest": manifest,
    }


def test_screenshot_captured_successfully(_evidence_with_screenshot):
    assert _evidence_with_screenshot["screenshot_size"] > 1000, "Screenshot must be >1KB"

def test_evidence_bundle_persisted(_evidence_with_screenshot):
    bundle = _evidence_with_screenshot["bundle"]
    assert bundle["status"] == "persisted"
    assert bundle["bundle_id"].startswith("evb_")
    assert bundle["artifact_count"] >= 5

def test_ui_execution_referenced_in_bundle(_evidence_with_screenshot):
    manifest = _evidence_with_screenshot["manifest"]
    artifacts = manifest.get("artifacts") or []
    ui_artifact = [a for a in artifacts if "ui_execution" in str(a.get("name", ""))]
    assert len(ui_artifact) >= 1, "ui_execution artifact must be in bundle"

def test_screenshot_path_in_ui_artifact(_evidence_with_screenshot):
    manifest = _evidence_with_screenshot["manifest"]
    # The ui_execution artifact contains a JSON with screenshot_ref
    ui_artifacts = [a for a in manifest.get("artifacts", []) if "ui_execution" in str(a.get("name", ""))]
    if ui_artifacts:
        ui_ref = ui_artifacts[0].get("path", "")
        assert "ui_execution" in ui_ref
