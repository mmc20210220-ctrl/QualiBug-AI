from __future__ import annotations

import json
import shutil
from pathlib import Path

from ai_test_asset_center.phase106_frontend_api_runtime import (
    API_RUNTIME_MANIFEST_JSON,
    FRONTEND_APP_DIR,
    PHASE106C_VERSION,
    build_frontend_api_runtime,
    run_frontend_api_runtime_export,
    scan_frontend_api_runtime_for_secret_leaks,
    validate_frontend_api_runtime,
    verify_frontend_api_runtime_checksums,
)


def test_phase106c_builds_real_api_runtime_boundary(tmp_path: Path) -> None:
    report = build_frontend_api_runtime(tmp_path, scenario="manufacturing")

    assert report.passed
    assert report.score == 100
    app_dir = tmp_path / FRONTEND_APP_DIR
    assert (app_dir / "src/api/runtimeApi.ts").exists()
    assert (app_dir / "src/app/runtimeConfig.ts").exists()
    assert (app_dir / "src/services/realApiRuntime.ts").exists()
    assert (app_dir / "src/hooks/useRuntimeHealth.ts").exists()
    assert (app_dir / "src/pages/ApiRuntimeWorkbenchPage.tsx").exists()
    assert (tmp_path / "phase106_frontend_api_runtime.zip").exists()

    runtime_api = (app_dir / "src/api/runtimeApi.ts").read_text(encoding="utf-8")
    for keyword in (
        "AbortController",
        "requestTimeoutMs",
        "normalizeEnvelope",
        "redactApiError",
        "loadRuntimeHealth",
        "backendStatus",
        "serviceReachable",
        "fallbackActive",
        "/api/health",
        "/api/v1/health",
        "error-envelope",
        "timeout",
    ):
        assert keyword in runtime_api

    client = (app_dir / "src/api/qualibugClient.ts").read_text(encoding="utf-8")
    for method in ("runEnvironmentPreflight", "generateTestPlan", "startTestRun", "listRisks", "getExecutiveReport"):
        assert f"{method}(" in client

    adapter = (app_dir / "src/services/realApiRuntime.ts").read_text(encoding="utf-8")
    for keyword in ("RuntimeApiAdapter", "safeApiCall", "demo-fallback", "fallbackToDemo", "fallbackActive", "loadReportRoi"):
        assert keyword in adapter

    page = (app_dir / "src/pages/ApiRuntimeWorkbenchPage.tsx").read_text(encoding="utf-8")
    for keyword in ("backend status", "fallback status", "provider status", "offline state", "error state", "demo fallback"):
        assert keyword in page

    routes = (app_dir / "src/routes.ts").read_text(encoding="utf-8")
    assert "'/api-runtime'" in routes
    assert "ApiRuntimeWorkbenchPage" in routes

    manifest = json.loads((tmp_path / API_RUNTIME_MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["version"] == PHASE106C_VERSION
    assert manifest["runtime_route"] == "/api-runtime"
    assert len(manifest["runtime_endpoint_contract"]) >= 16
    health_paths = {item["path"] for item in manifest["runtime_endpoint_contract"] if item["client"] == "health"}
    assert "/api/health" in health_paths
    assert "/api/v1/health" in health_paths
    assert not verify_frontend_api_runtime_checksums(tmp_path)


def test_phase106c_validate_only_detects_missing_api_runtime_route(tmp_path: Path) -> None:
    build_frontend_api_runtime(tmp_path, scenario="ecommerce")
    routes = tmp_path / FRONTEND_APP_DIR / "src/routes.ts"
    routes.write_text(routes.read_text(encoding="utf-8").replace("'/api-runtime'", "'/api-runtime-missing'"), encoding="utf-8")

    report = validate_frontend_api_runtime(tmp_path, scenario="ecommerce")

    assert not report.passed
    failed_keys = {check.key for check in report.checks if not check.passed}
    assert "api_runtime_route" in failed_keys
    assert "checksums" in failed_keys


def test_phase106c_secret_scan_and_validate_only_failure(tmp_path: Path) -> None:
    build_frontend_api_runtime(tmp_path, scenario="manufacturing")
    readme = tmp_path / FRONTEND_APP_DIR / "README_FRONTEND_API_RUNTIME.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nclient_secret=raw\n", encoding="utf-8")

    leaks = scan_frontend_api_runtime_for_secret_leaks(tmp_path)
    assert any("client_secret=" in leak for leak in leaks)
    assert verify_frontend_api_runtime_checksums(tmp_path)

    shutil.rmtree(tmp_path / FRONTEND_APP_DIR)
    report = run_frontend_api_runtime_export(tmp_path, scenario="manufacturing", validate_only=True)
    assert not report.passed
