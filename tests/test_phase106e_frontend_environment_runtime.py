from __future__ import annotations

import json
import shutil
from pathlib import Path

from ai_test_asset_center.phase106_frontend_environment_runtime import (
    ENVIRONMENT_RUNTIME_MANIFEST_JSON,
    FRONTEND_APP_DIR,
    PHASE106E_VERSION,
    build_frontend_environment_runtime,
    run_frontend_environment_runtime_export,
    scan_frontend_environment_runtime_for_secret_leaks,
    validate_frontend_environment_runtime,
    verify_frontend_environment_runtime_checksums,
)


def test_phase106e_builds_environment_runtime_route_and_polling(tmp_path: Path) -> None:
    report = build_frontend_environment_runtime(tmp_path, scenario="manufacturing")

    assert report.passed
    assert report.score == 100
    app_dir = tmp_path / FRONTEND_APP_DIR
    assert (app_dir / "src/pages/EnvironmentRuntimePage.tsx").exists()
    assert (app_dir / "src/hooks/useEnvironmentDiagnosisRuntime.ts").exists()
    assert (app_dir / "src/services/environmentDiagnosisRuntime.ts").exists()
    assert (app_dir / "src/app/environmentRuntimeTypes.ts").exists()
    assert (tmp_path / "phase106_frontend_environment_runtime.zip").exists()

    routes = (app_dir / "src/routes.ts").read_text(encoding="utf-8")
    assert "'/environment-runtime'" in routes
    assert "EnvironmentRuntimePage" in routes

    service = (app_dir / "src/services/environmentDiagnosisRuntime.ts").read_text(encoding="utf-8")
    for keyword in ("EnvironmentDiagnosisRuntime", "triggerEnvironmentPreflight", "pollEnvironmentDiagnosis", "loadEnvironmentBlockers", "runEnvironmentPreflight", "demo-fallback"):
        assert keyword in service

    page = (app_dir / "src/pages/EnvironmentRuntimePage.tsx").read_text(encoding="utf-8")
    for keyword in ("环境诊断真实触发", "轮询状态", "阻断原因", "客户补料动作", "项目级预检"):
        assert keyword in page or keyword in (app_dir / "src/components/EnvironmentReadinessPanel.tsx").read_text(encoding="utf-8") or keyword in (app_dir / "src/components/EnvironmentBlockerList.tsx").read_text(encoding="utf-8")

    manifest = json.loads((tmp_path / ENVIRONMENT_RUNTIME_MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["version"] == PHASE106E_VERSION
    assert manifest["route"] == "/environment-runtime"
    assert len(manifest["runtime_endpoints"]) >= 4
    assert not verify_frontend_environment_runtime_checksums(tmp_path)


def test_phase106e_validate_only_detects_missing_runtime_route(tmp_path: Path) -> None:
    build_frontend_environment_runtime(tmp_path, scenario="ecommerce")
    routes = tmp_path / FRONTEND_APP_DIR / "src/routes.ts"
    routes.write_text(routes.read_text(encoding="utf-8").replace("'/environment-runtime'", "'/environment-missing'"), encoding="utf-8")

    report = validate_frontend_environment_runtime(tmp_path, scenario="ecommerce")

    assert not report.passed
    failed_keys = {check.key for check in report.checks if not check.passed}
    assert "environment_runtime_route" in failed_keys
    assert "checksums" in failed_keys


def test_phase106e_secret_scan_and_validate_only_failure(tmp_path: Path) -> None:
    build_frontend_environment_runtime(tmp_path, scenario="manufacturing")
    readme = tmp_path / FRONTEND_APP_DIR / "README_FRONTEND_ENVIRONMENT_RUNTIME.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nclient_secret=raw\n", encoding="utf-8")

    leaks = scan_frontend_environment_runtime_for_secret_leaks(tmp_path)
    assert any("client_secret=" in leak for leak in leaks)
    assert verify_frontend_environment_runtime_checksums(tmp_path)

    shutil.rmtree(tmp_path / FRONTEND_APP_DIR)
    report = run_frontend_environment_runtime_export(tmp_path, scenario="manufacturing", validate_only=True)
    assert not report.passed
