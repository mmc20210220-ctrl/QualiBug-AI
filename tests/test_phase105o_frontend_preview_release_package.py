from __future__ import annotations

import zipfile
from pathlib import Path

from ai_test_asset_center.phase105_frontend_preview_release_package import (
    DELIVERY_BUNDLE_DIR,
    FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON,
    FRONTEND_PREVIEW_RELEASE_REPORT_JSON,
    FRONTEND_PREVIEW_RELEASE_ZIP,
    RELEASE_HANDOFF_DIR,
    build_frontend_preview_release_package,
    scan_frontend_preview_release_for_secret_leaks,
    validate_frontend_preview_release_package,
)


def test_phase105o_builds_release_package(tmp_path: Path) -> None:
    output = tmp_path / "release_package"
    result = build_frontend_preview_release_package(output, port=8895)
    acceptance = result["acceptance"]

    assert acceptance["passed"] is True
    assert acceptance["score"] >= 90
    assert (output / DELIVERY_BUNDLE_DIR / "hub_v2" / "index.html").exists()
    assert (output / DELIVERY_BUNDLE_DIR / "frontend_preview_server_manifest.json").exists()
    assert (output / "preview_acceptance" / "frontend_preview_acceptance_report.md").exists()
    assert (output / RELEASE_HANDOFF_DIR / "START_PREVIEW_SERVER.ps1").exists()
    assert (output / RELEASE_HANDOFF_DIR / "PREVIEW_API_CONTRACT.md").exists()
    assert (output / FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON).exists()
    assert (output / FRONTEND_PREVIEW_RELEASE_REPORT_JSON).exists()
    assert (output / FRONTEND_PREVIEW_RELEASE_ZIP).exists()

    with zipfile.ZipFile(output / FRONTEND_PREVIEW_RELEASE_ZIP) as archive:
        names = set(archive.namelist())
    assert f"{DELIVERY_BUNDLE_DIR}/hub_v2/index.html" in names
    assert "preview_acceptance/frontend_preview_acceptance_report.md" in names
    assert f"{RELEASE_HANDOFF_DIR}/START_PREVIEW_SERVER.ps1" in names


def test_phase105o_validate_detects_checksum_tampering(tmp_path: Path) -> None:
    output = tmp_path / "release_package"
    build_frontend_preview_release_package(output)

    runbook = output / RELEASE_HANDOFF_DIR / "DEMO_PREVIEW_RELEASE_RUNBOOK.md"
    runbook.write_text(runbook.read_text(encoding="utf-8") + "\n篡改内容\n", encoding="utf-8")

    report = validate_frontend_preview_release_package(output, output_dir=tmp_path / "recheck")
    assert report.passed is False
    checksum_check = next(check for check in report.checks if check.key == "release_checksums")
    assert checksum_check.passed is False
    assert "checksum mismatch" in checksum_check.detail


def test_phase105o_release_docs_scripts_and_redaction(tmp_path: Path) -> None:
    output = tmp_path / "release_package"
    build_frontend_preview_release_package(output, port=8799)

    ps1 = (output / RELEASE_HANDOFF_DIR / "START_PREVIEW_SERVER.ps1").read_text(encoding="utf-8")
    api_contract = (output / RELEASE_HANDOFF_DIR / "PREVIEW_API_CONTRACT.md").read_text(encoding="utf-8")
    readme = (output / RELEASE_HANDOFF_DIR / "README_FRONTEND_PREVIEW_RELEASE.md").read_text(encoding="utf-8")

    assert "phase105_frontend_preview_server" in ps1
    assert "--no-build-bundle" in ps1
    assert "8799" in ps1
    assert "/api/v1/frontend-preview/health" in api_contract
    assert "/api/v1/frontend-preview/checksums" in api_contract
    assert "客户演示" in readme
    assert "只读 API" in readme
    assert scan_frontend_preview_release_for_secret_leaks(output) == []
