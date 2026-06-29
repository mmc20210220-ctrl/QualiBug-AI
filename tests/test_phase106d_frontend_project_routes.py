from __future__ import annotations

import json
import shutil
from pathlib import Path

from ai_test_asset_center.phase106_frontend_project_routes import (
    FRONTEND_APP_DIR,
    PHASE106D_VERSION,
    PROJECT_ROUTES_MANIFEST_JSON,
    TASK_JOURNEYS_MANIFEST_JSON,
    UI_DESIGN_ORACLE_MANIFEST_JSON,
    build_frontend_project_routes,
    run_frontend_project_routes_export,
    scan_frontend_project_routes_for_secret_leaks,
    validate_frontend_project_routes,
    verify_frontend_project_routes_checksums,
)


def test_phase106d_builds_project_list_and_detail_routes(tmp_path: Path) -> None:
    report = build_frontend_project_routes(tmp_path, scenario="manufacturing")

    assert report.passed
    assert report.score == 100
    app_dir = tmp_path / FRONTEND_APP_DIR
    assert (app_dir / "src/pages/ProjectListPage.tsx").exists()
    assert (app_dir / "src/pages/ProjectDetailPage.tsx").exists()
    assert (app_dir / "src/hooks/useProjectWorkspace.ts").exists()
    assert (app_dir / "src/hooks/useSelectedProjectId.ts").exists()
    assert (app_dir / "src/services/projectWorkspace.ts").exists()
    assert (app_dir / "src/components/WorkspaceStateGate.tsx").exists()
    assert (app_dir / "src/components/DangerConfirmButton.tsx").exists()
    assert (tmp_path / "phase106_frontend_project_routes.zip").exists()

    routes = (app_dir / "src/routes.ts").read_text(encoding="utf-8")
    assert "'/projects'" in routes
    assert "'/projects/:projectId'" in routes
    assert "ProjectListPage" in routes
    assert "ProjectDetailPage" in routes

    app = (app_dir / "src/App.tsx").read_text(encoding="utf-8")
    assert "pathname.startsWith('/projects/')" in app
    assert "project-routes.css" in app

    workspace = (app_dir / "src/services/projectWorkspace.ts").read_text(encoding="utf-8")
    for keyword in ("ProjectWorkspace", "listProjects", "createProjectDraft", "demo fallback", "projectScopedApiPaths", "getExecutiveReport"):
        assert keyword in workspace

    topbar = (app_dir / "src/components/Topbar.tsx").read_text(encoding="utf-8")
    for keyword in ("顶部状态区", "运行模式", "后端状态", "ProjectSwitcher"):
        assert keyword in topbar

    manifest = json.loads((tmp_path / PROJECT_ROUTES_MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["version"] == PHASE106D_VERSION
    assert len(manifest["project_routes"]) == 2
    assert len(manifest["project_scoped_api_paths"]) >= 7
    assert len(manifest["frontend_task_journeys"]) >= 5
    assert len(manifest["ui_screen_oracles"]) >= 2
    assert len(manifest["ui_journey_oracles"]) >= 2
    assert manifest["project_routes"][1]["requires_project_context"] is True
    assert manifest["project_routes"][0]["design_screen_id"] == "project_list"
    assert "project_switcher" in manifest["project_routes"][0]["expected_components"]
    assert "open_command_center" in manifest["project_routes"][1]["primary_actions"]
    assert "enter_command_center" in manifest["project_routes"][1]["journey_entry"]
    task_journeys = json.loads((tmp_path / TASK_JOURNEYS_MANIFEST_JSON).read_text(encoding="utf-8"))
    assert task_journeys["version"] == PHASE106D_VERSION
    assert any(item["journey_id"] == "select_project" for item in task_journeys["journeys"])
    assert any(item["journey_id"] == "enter_command_center" for item in task_journeys["journeys"])
    assert any(item["required_components"] for item in task_journeys["journeys"])
    ui_design_oracle = json.loads((tmp_path / UI_DESIGN_ORACLE_MANIFEST_JSON).read_text(encoding="utf-8"))
    assert ui_design_oracle["version"] == "ui-design-oracle-v1"
    assert any(item["screen_id"] == "project_list" for item in ui_design_oracle["screens"])
    assert any(item["journey_id"] == "enter_command_center" for item in ui_design_oracle["journeys"])
    assert "match_hints" in ui_design_oracle
    assert "project_switcher" in ui_design_oracle["match_hints"]
    assert "当前项目切换" in (ui_design_oracle["match_hints"]["project_switcher"].get("tokens") or [])
    assert "project-switcher" in (ui_design_oracle["match_hints"]["project_switcher"].get("testids") or [])
    assert not verify_frontend_project_routes_checksums(tmp_path)


def test_phase106d_validate_only_detects_missing_project_route(tmp_path: Path) -> None:
    build_frontend_project_routes(tmp_path, scenario="ecommerce")
    routes = tmp_path / FRONTEND_APP_DIR / "src/routes.ts"
    routes.write_text(routes.read_text(encoding="utf-8").replace("'/projects/:projectId'", "'/project-missing/:projectId'"), encoding="utf-8")

    report = validate_frontend_project_routes(tmp_path, scenario="ecommerce")

    assert not report.passed
    failed_keys = {check.key for check in report.checks if not check.passed}
    assert "project_routes" in failed_keys
    assert "checksums" in failed_keys


def test_phase106d_secret_scan_and_validate_only_failure(tmp_path: Path) -> None:
    build_frontend_project_routes(tmp_path, scenario="manufacturing")
    readme = tmp_path / FRONTEND_APP_DIR / "README_FRONTEND_PROJECT_ROUTES.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nclient_secret=raw\n", encoding="utf-8")

    leaks = scan_frontend_project_routes_for_secret_leaks(tmp_path)
    assert any("client_secret=" in leak for leak in leaks)
    assert verify_frontend_project_routes_checksums(tmp_path)

    shutil.rmtree(tmp_path / FRONTEND_APP_DIR)
    report = run_frontend_project_routes_export(tmp_path, scenario="manufacturing", validate_only=True)
    assert not report.passed
