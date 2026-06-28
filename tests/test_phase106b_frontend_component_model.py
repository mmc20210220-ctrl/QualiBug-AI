from __future__ import annotations

import json
import shutil
from pathlib import Path

from ai_test_asset_center.phase106_frontend_component_model import (
    COMPONENT_MODEL_MANIFEST_JSON,
    FRONTEND_APP_DIR,
    PHASE106B_VERSION,
    build_frontend_component_model,
    run_frontend_component_model_export,
    scan_frontend_component_model_for_secret_leaks,
    validate_frontend_component_model,
    verify_frontend_component_model_checksums,
)


def test_phase106b_builds_component_model_and_data_mode_boundary(tmp_path: Path) -> None:
    report = build_frontend_component_model(tmp_path, scenario="manufacturing")

    assert report.passed
    assert report.score == 100
    app_dir = tmp_path / FRONTEND_APP_DIR
    assert (app_dir / "src/app/appConfig.ts").exists()
    assert (app_dir / "src/app/dataMode.ts").exists()
    assert (app_dir / "src/services/qualibugDataSource.ts").exists()
    assert (app_dir / "src/hooks/useQualiBugData.ts").exists()
    assert (app_dir / "src/pages/ComponentModelWorkbenchPage.tsx").exists()
    assert (tmp_path / "phase106_frontend_component_model.zip").exists()

    routes = (app_dir / "src/routes.ts").read_text(encoding="utf-8")
    assert "/component-model" in routes
    assert "ComponentModelWorkbenchPage" in routes

    data_source = (app_dir / "src/services/qualibugDataSource.ts").read_text(encoding="utf-8")
    for method in ("loadDashboard", "loadEnvironment", "loadTestExecution", "loadRiskEvidence", "loadReportRoi"):
        assert f"{method}(" in data_source
    assert "resolveDataMode() === 'demo'" in data_source
    assert "mode: 'real'" in data_source

    component_source = "\n".join(path.read_text(encoding="utf-8") for path in (app_dir / "src/components").glob("*.tsx"))
    for component in ("PageShell", "DataModeBadge", "KpiRail", "FlowNodeCard", "ProbeTable", "RiskList", "ActionQueue"):
        assert component in component_source

    manifest = json.loads((tmp_path / COMPONENT_MODEL_MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["version"] == PHASE106B_VERSION
    assert manifest["component_route"] == "/component-model"
    assert len(manifest["component_inventory"]) >= 7
    assert not verify_frontend_component_model_checksums(tmp_path)


def test_phase106b_validate_only_detects_missing_component_route(tmp_path: Path) -> None:
    build_frontend_component_model(tmp_path, scenario="ecommerce")
    routes = tmp_path / FRONTEND_APP_DIR / "src/routes.ts"
    routes.write_text(routes.read_text(encoding="utf-8").replace("/component-model", "/component-model-missing"), encoding="utf-8")

    report = validate_frontend_component_model(tmp_path, scenario="ecommerce")

    assert not report.passed
    failed_keys = {check.key for check in report.checks if not check.passed}
    assert "component_route" in failed_keys
    assert "checksums" in failed_keys


def test_phase106b_secret_scan_and_tamper_detection(tmp_path: Path) -> None:
    build_frontend_component_model(tmp_path, scenario="manufacturing")
    readme = tmp_path / FRONTEND_APP_DIR / "README_FRONTEND_COMPONENT_MODEL.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nclient_secret=raw\n", encoding="utf-8")

    leaks = scan_frontend_component_model_for_secret_leaks(tmp_path)
    assert any("client_secret=" in leak for leak in leaks)
    assert verify_frontend_component_model_checksums(tmp_path)

    shutil.rmtree(tmp_path / FRONTEND_APP_DIR)
    report = run_frontend_component_model_export(tmp_path, scenario="manufacturing", validate_only=True)
    assert not report.passed
