from __future__ import annotations

import json
import shutil
from pathlib import Path

from ai_test_asset_center.phase106_frontend_app_scaffold import (
    FRONTEND_APP_DIR,
    FRONTEND_APP_MANIFEST_JSON,
    PHASE106A_VERSION,
    build_frontend_app_scaffold,
    run_frontend_app_scaffold_export,
    scan_frontend_app_scaffold_for_secret_leaks,
    validate_frontend_app_scaffold,
    verify_frontend_app_checksums,
)


def test_phase106a_builds_real_vite_react_frontend_scaffold(tmp_path: Path) -> None:
    report = build_frontend_app_scaffold(tmp_path, scenario="manufacturing")

    assert report.passed
    assert report.score == 100
    app_dir = tmp_path / FRONTEND_APP_DIR
    assert (app_dir / "package.json").exists()
    assert (app_dir / "src/api/qualibugClient.ts").exists()
    assert (app_dir / "src/pages/TestExecutionPage.tsx").exists()
    assert (app_dir / "src/__tests__/frontend-contract.test.ts").exists()
    assert (tmp_path / "phase106_frontend_app_scaffold.zip").exists()

    package = json.loads((app_dir / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["dev"].startswith("vite")
    assert "react" in package["dependencies"]
    assert "typescript" in package["dependencies"]
    assert "vitest" in package["devDependencies"]

    api_client = (app_dir / "src/api/qualibugClient.ts").read_text(encoding="utf-8")
    for method in ("runEnvironmentPreflight", "generateTestPlan", "startTestRun", "getRiskDetail", "getExecutiveReport"):
        assert f"{method}(" in api_client

    app_source = "\n".join(path.read_text(encoding="utf-8") for path in (app_dir / "src").rglob("*.tsx"))
    for label in ("客户资料导入", "环境诊断", "AI 测试计划", "实时测试执行", "风险证据链", "领导层报告", "ROI"):
        assert label in app_source

    manifest = json.loads((tmp_path / FRONTEND_APP_MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["version"] == PHASE106A_VERSION
    assert len(manifest["routes"]) >= 7
    assert len(manifest["api_contract"]) >= 15
    assert not verify_frontend_app_checksums(tmp_path)


def test_phase106a_validate_only_detects_missing_api_client(tmp_path: Path) -> None:
    build_frontend_app_scaffold(tmp_path, scenario="ecommerce")
    (tmp_path / FRONTEND_APP_DIR / "src/api/qualibugClient.ts").unlink()

    report = validate_frontend_app_scaffold(tmp_path, scenario="ecommerce")

    assert not report.passed
    failed_keys = {check.key for check in report.checks if not check.passed}
    assert "required_files" in failed_keys
    assert "api_client_methods" in failed_keys


def test_phase106a_secret_scan_and_checksum_detect_tamper(tmp_path: Path) -> None:
    build_frontend_app_scaffold(tmp_path, scenario="manufacturing")
    readme = tmp_path / FRONTEND_APP_DIR / "README_FRONTEND_APP.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nclient_secret=raw\n", encoding="utf-8")

    leaks = scan_frontend_app_scaffold_for_secret_leaks(tmp_path)
    assert any("client_secret=" in leak for leak in leaks)
    assert verify_frontend_app_checksums(tmp_path)

    shutil.rmtree(tmp_path / FRONTEND_APP_DIR)
    report = run_frontend_app_scaffold_export(tmp_path, scenario="manufacturing", validate_only=True)
    assert not report.passed
