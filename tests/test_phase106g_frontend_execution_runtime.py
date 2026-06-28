
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ai_test_asset_center.phase106_frontend_execution_runtime import (
    FRONTEND_APP_DIR,
    PHASE106G_VERSION,
    EXECUTION_RUNTIME_MANIFEST_JSON,
    build_frontend_execution_runtime,
    run_frontend_execution_runtime_export,
    scan_frontend_execution_runtime_for_secret_leaks,
    validate_frontend_execution_runtime,
    verify_frontend_execution_runtime_checksums,
)


def test_phase106g_builds_execution_runtime_route_event_stream_and_evidence_feedback(tmp_path: Path) -> None:
    report = build_frontend_execution_runtime(tmp_path, scenario="manufacturing")

    assert report.passed
    assert report.score == 100
    app_dir = tmp_path / FRONTEND_APP_DIR
    assert (app_dir / "src/pages/ExecutionRuntimePage.tsx").exists()
    assert (app_dir / "src/hooks/useExecutionEventRuntime.ts").exists()
    assert (app_dir / "src/services/executionEventRuntime.ts").exists()
    assert (app_dir / "src/app/executionRuntimeTypes.ts").exists()
    assert (tmp_path / "phase106_frontend_execution_runtime.zip").exists()

    routes = (app_dir / "src/routes.ts").read_text(encoding="utf-8")
    assert "'/execution-runtime'" in routes
    assert "ExecutionRuntimePage" in routes

    service = (app_dir / "src/services/executionEventRuntime.ts").read_text(encoding="utf-8")
    for keyword in ("ExecutionEventRuntime", "loadLiveRun", "pollRunStatus", "loadEventStream", "loadRiskSignals", "loadEvidenceSnapshots", "openEvidenceDetail", "runId", "demo-fallback"):
        assert keyword in service

    page_text = "\n".join(
        (app_dir / relative).read_text(encoding="utf-8")
        for relative in (
            "src/pages/ExecutionRuntimePage.tsx",
            "src/components/LiveExecutionStatusPanel.tsx",
            "src/components/ExecutionEventStream.tsx",
            "src/components/RuntimeRiskSignalList.tsx",
            "src/components/EvidenceSnapshotPanel.tsx",
        )
    )
    for keyword in ("实时执行事件流", "风险证据回流", "runId", "执行状态", "风险信号", "证据快照", "跳转证据链", "项目级执行"):
        assert keyword in page_text

    manifest = json.loads((tmp_path / EXECUTION_RUNTIME_MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["version"] == PHASE106G_VERSION
    assert manifest["route"] == "/execution-runtime"
    assert len(manifest["runtime_endpoints"]) >= 5
    assert not verify_frontend_execution_runtime_checksums(tmp_path)


def test_phase106g_validate_only_detects_missing_execution_route(tmp_path: Path) -> None:
    build_frontend_execution_runtime(tmp_path, scenario="ecommerce")
    routes = tmp_path / FRONTEND_APP_DIR / "src/routes.ts"
    routes.write_text(routes.read_text(encoding="utf-8").replace("'/execution-runtime'", "'/execution-missing'"), encoding="utf-8")

    report = validate_frontend_execution_runtime(tmp_path, scenario="ecommerce")

    assert not report.passed
    failed_keys = {check.key for check in report.checks if not check.passed}
    assert "execution_runtime_route" in failed_keys
    assert "checksums" in failed_keys


def test_phase106g_secret_scan_and_validate_only_failure(tmp_path: Path) -> None:
    build_frontend_execution_runtime(tmp_path, scenario="manufacturing")
    readme = tmp_path / FRONTEND_APP_DIR / "README_FRONTEND_EXECUTION_RUNTIME.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nclient_secret=raw\n", encoding="utf-8")

    leaks = scan_frontend_execution_runtime_for_secret_leaks(tmp_path)
    assert any("client_secret=" in leak for leak in leaks)
    assert verify_frontend_execution_runtime_checksums(tmp_path)

    shutil.rmtree(tmp_path / FRONTEND_APP_DIR)
    report = run_frontend_execution_runtime_export(tmp_path, scenario="manufacturing", validate_only=True)
    assert not report.passed
