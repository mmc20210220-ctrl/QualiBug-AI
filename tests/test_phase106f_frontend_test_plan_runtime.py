from __future__ import annotations

import json
import shutil
from pathlib import Path

from ai_test_asset_center.phase106_frontend_test_plan_runtime import (
    FRONTEND_APP_DIR,
    PHASE106F_VERSION,
    TEST_PLAN_RUNTIME_MANIFEST_JSON,
    build_frontend_test_plan_runtime,
    run_frontend_test_plan_runtime_export,
    scan_frontend_test_plan_runtime_for_secret_leaks,
    validate_frontend_test_plan_runtime,
    verify_frontend_test_plan_runtime_checksums,
)


def test_phase106f_builds_test_plan_runtime_route_and_execution_controls(tmp_path: Path) -> None:
    report = build_frontend_test_plan_runtime(tmp_path, scenario="manufacturing")

    assert report.passed
    assert report.score == 100
    app_dir = tmp_path / FRONTEND_APP_DIR
    assert (app_dir / "src/pages/TestPlanRuntimePage.tsx").exists()
    assert (app_dir / "src/hooks/useTestPlanExecutionRuntime.ts").exists()
    assert (app_dir / "src/services/testPlanExecutionRuntime.ts").exists()
    assert (app_dir / "src/app/testPlanRuntimeTypes.ts").exists()
    assert (tmp_path / "phase106_frontend_test_plan_runtime.zip").exists()

    routes = (app_dir / "src/routes.ts").read_text(encoding="utf-8")
    assert "'/test-plan-runtime'" in routes
    assert "TestPlanRuntimePage" in routes

    service = (app_dir / "src/services/testPlanExecutionRuntime.ts").read_text(encoding="utf-8")
    for keyword in ("TestPlanExecutionRuntime", "generateTestPlan", "startReadOnlyExecution", "pollExecutionRun", "loadExecutionEvents", "runId", "demo-fallback"):
        assert keyword in service

    page_text = "\n".join(
        (app_dir / relative).read_text(encoding="utf-8")
        for relative in (
            "src/pages/TestPlanRuntimePage.tsx",
            "src/components/TestPlanSummaryPanel.tsx",
            "src/components/ProbeExecutionTable.tsx",
            "src/components/ExecutionLaunchPanel.tsx",
        )
    )
    for keyword in ("AI 测试计划真实生成", "执行启动", "可执行探针", "阻断探针", "只读安全执行", "项目级测试计划"):
        assert keyword in page_text

    manifest = json.loads((tmp_path / TEST_PLAN_RUNTIME_MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["version"] == PHASE106F_VERSION
    assert manifest["route"] == "/test-plan-runtime"
    assert len(manifest["runtime_endpoints"]) >= 5
    assert not verify_frontend_test_plan_runtime_checksums(tmp_path)


def test_phase106f_validate_only_detects_missing_runtime_route(tmp_path: Path) -> None:
    build_frontend_test_plan_runtime(tmp_path, scenario="ecommerce")
    routes = tmp_path / FRONTEND_APP_DIR / "src/routes.ts"
    routes.write_text(routes.read_text(encoding="utf-8").replace("'/test-plan-runtime'", "'/test-plan-missing'"), encoding="utf-8")

    report = validate_frontend_test_plan_runtime(tmp_path, scenario="ecommerce")

    assert not report.passed
    failed_keys = {check.key for check in report.checks if not check.passed}
    assert "test_plan_runtime_route" in failed_keys
    assert "checksums" in failed_keys


def test_phase106f_secret_scan_and_validate_only_failure(tmp_path: Path) -> None:
    build_frontend_test_plan_runtime(tmp_path, scenario="manufacturing")
    readme = tmp_path / FRONTEND_APP_DIR / "README_FRONTEND_TEST_PLAN_RUNTIME.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nclient_secret=raw\n", encoding="utf-8")

    leaks = scan_frontend_test_plan_runtime_for_secret_leaks(tmp_path)
    assert any("client_secret=" in leak for leak in leaks)
    assert verify_frontend_test_plan_runtime_checksums(tmp_path)

    shutil.rmtree(tmp_path / FRONTEND_APP_DIR)
    report = run_frontend_test_plan_runtime_export(tmp_path, scenario="manufacturing", validate_only=True)
    assert not report.passed
