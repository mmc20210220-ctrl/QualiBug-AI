from __future__ import annotations

import json
import shutil
from pathlib import Path

from ai_test_asset_center.phase106_frontend_project_routes import (
    FRONTEND_APP_DIR,
    PHASE106D_VERSION,
    PROJECT_ROUTES_MANIFEST_JSON,
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
    assert (app_dir / "src/services/projectWorkspace.ts").exists()
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

    manifest = json.loads((tmp_path / PROJECT_ROUTES_MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["version"] == PHASE106D_VERSION
    assert len(manifest["project_routes"]) == 2
    assert len(manifest["project_scoped_api_paths"]) >= 7
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
