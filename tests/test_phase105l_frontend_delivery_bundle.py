from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ai_test_asset_center.phase105_frontend_delivery_bundle import (
    FRONTEND_DELIVERY_REPORT_JSON,
    FRONTEND_DELIVERY_ZIP,
    build_frontend_delivery_bundle,
    main,
    validate_frontend_delivery_bundle,
)


def test_phase105l_builds_frontend_delivery_bundle(tmp_path: Path) -> None:
    result = build_frontend_delivery_bundle(tmp_path, scenario="manufacturing")

    assert result["passed"] is True
    assert result["acceptance"]["passed"] is True
    assert (tmp_path / "hub_v2" / "index.html").exists()
    assert (tmp_path / "hub_v2" / "pages" / "test_execution" / "test_execution.html").exists()
    assert (tmp_path / "interaction_acceptance" / "frontend_interaction_acceptance_report.md").exists()
    assert (tmp_path / "handoff" / "DEMO_RUNBOOK.md").read_text(encoding="utf-8").find("实时测试执行") >= 0
    assert (tmp_path / FRONTEND_DELIVERY_ZIP).exists()

    with zipfile.ZipFile(tmp_path / FRONTEND_DELIVERY_ZIP) as archive:
        names = set(archive.namelist())
    assert "hub_v2/index.html" in names
    assert "hub_v2/pages/test_execution/test_execution.html" in names
    assert "handoff/CUSTOMER_WALKTHROUGH_SCRIPT.md" in names


def test_phase105l_validation_detects_checksum_tamper(tmp_path: Path) -> None:
    build_frontend_delivery_bundle(tmp_path, scenario="manufacturing")
    index = tmp_path / "hub_v2" / "index.html"
    index.write_text(index.read_text(encoding="utf-8") + "\n<!-- tampered -->\n", encoding="utf-8")

    report = validate_frontend_delivery_bundle(tmp_path)

    assert report.passed is False
    assert any(check.key == "checksums" and not check.passed for check in report.checks)


def test_phase105l_cli_build_and_validate_only(tmp_path: Path) -> None:
    build_dir = tmp_path / "bundle"
    assert main(["--output-dir", str(build_dir), "--scenario", "manufacturing"]) == 0
    assert main(["--validate-only", "--bundle-dir", str(build_dir), "--output-dir", str(build_dir / "recheck")]) == 0

    report = json.loads((build_dir / "recheck" / FRONTEND_DELIVERY_REPORT_JSON).read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["score"] >= 90
