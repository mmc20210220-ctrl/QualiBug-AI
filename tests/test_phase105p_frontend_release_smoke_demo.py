from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ai_test_asset_center.phase105_frontend_release_smoke_demo import (
    DEMO_QUICKSTART_MD,
    FRONTEND_RELEASE_SMOKE_MANIFEST_JSON,
    FRONTEND_RELEASE_SMOKE_REPORT_JSON,
    FRONTEND_RELEASE_SMOKE_ZIP,
    RELEASE_PACKAGE_DIR,
    RUN_DEMO_PS1,
    SMOKE_ARTIFACT_DIR,
    SMOKE_DEMO_PS1,
    build_frontend_release_smoke_demo,
    run_frontend_release_route_smoke,
    scan_frontend_release_smoke_for_secret_leaks,
    validate_frontend_release_smoke_demo,
)
from ai_test_asset_center.phase105_frontend_preview_release_package import FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_phase105p_builds_release_smoke_demo_package(tmp_path: Path) -> None:
    output = tmp_path / "phase105p"

    result = build_frontend_release_smoke_demo(output, create_zip=True)

    assert result["smoke"]["passed"] is True
    assert result["smoke"]["score"] >= 90
    assert (output / RELEASE_PACKAGE_DIR / FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON).exists()
    assert (output / SMOKE_ARTIFACT_DIR / FRONTEND_RELEASE_SMOKE_REPORT_JSON).exists()
    assert (output / FRONTEND_RELEASE_SMOKE_MANIFEST_JSON).exists()
    assert (output / RUN_DEMO_PS1).exists()
    assert (output / SMOKE_DEMO_PS1).exists()
    assert (output / FRONTEND_RELEASE_SMOKE_ZIP).exists()

    manifest = _load_json(output / FRONTEND_RELEASE_SMOKE_MANIFEST_JSON)
    assert manifest["smoke_passed"] is True
    assert "test_execution" in manifest["route_summary"]["page_keys"]
    assert manifest["route_summary"]["all_api_ok"] is True
    assert manifest["route_summary"]["all_static_ok"] is True

    with zipfile.ZipFile(output / FRONTEND_RELEASE_SMOKE_ZIP) as archive:
        names = set(archive.namelist())
    assert RUN_DEMO_PS1 in names
    assert SMOKE_DEMO_PS1 in names
    assert DEMO_QUICKSTART_MD in names
    assert f"{SMOKE_ARTIFACT_DIR}/frontend_release_smoke_report.md" in names
    assert f"{RELEASE_PACKAGE_DIR}/{FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON}" in names


def test_phase105p_detects_broken_demo_script(tmp_path: Path) -> None:
    output = tmp_path / "phase105p"
    build_frontend_release_smoke_demo(output, create_zip=True)
    (output / RUN_DEMO_PS1).write_text("Write-Host 'broken'\n", encoding="utf-8")

    report = validate_frontend_release_smoke_demo(output, output_dir=tmp_path / "recheck")

    assert report.passed is False
    failed = {check.key for check in report.checks if not check.passed}
    assert "demo_scripts" in failed


def test_phase105p_route_smoke_scripts_and_redaction(tmp_path: Path) -> None:
    output = tmp_path / "phase105p"
    build_frontend_release_smoke_demo(output, create_zip=True)

    smoke = run_frontend_release_route_smoke(output / RELEASE_PACKAGE_DIR)
    assert smoke["all_api_ok"] is True
    assert smoke["all_static_ok"] is True
    assert smoke["write_blocked"] is True
    assert smoke["options_ok"] is True
    assert smoke["traversal_blocked"] is True

    quickstart = (output / DEMO_QUICKSTART_MD).read_text(encoding="utf-8")
    assert "一键演示" in quickstart
    assert "/health" in quickstart
    assert "checksum" in quickstart
    assert not scan_frontend_release_smoke_for_secret_leaks(output)
