from __future__ import annotations

import json

from ai_test_asset_center.phase104_frontend_integration_workspace import (
    build_frontend_integration_workspace,
    run_frontend_workspace_export,
    scan_workspace_for_secret_leaks,
    validate_frontend_integration_workspace,
)


def test_phase104d_generates_frontend_workspace_artifacts(tmp_path) -> None:
    manifest = build_frontend_integration_workspace(tmp_path, api_base_url="http://127.0.0.1:8790")

    assert manifest["version"].startswith("phase104d")
    assert manifest["redaction_status"] == "safe"
    assert (tmp_path / "contract" / "openapi.json").exists()
    assert (tmp_path / "contract" / "API_CONTRACT.md").exists()
    assert (tmp_path / "src" / "api" / "qualibugClient.ts").exists()
    assert (tmp_path / "src" / "api" / "pageDataAdapters.ts").exists()
    assert (tmp_path / "src" / "api" / "qualibugWorkflowSmoke.ts").exists()
    assert (tmp_path / ".env.example").read_text(encoding="utf-8").count("VITE_QUALIBUG_API_BASE_URL") == 1

    openapi = json.loads((tmp_path / "contract" / "openapi.json").read_text(encoding="utf-8"))
    assert "/api/v1/projects/{project_id}/command-center" in openapi["paths"]


def test_phase104d_workspace_validation_and_reports(tmp_path) -> None:
    result = run_frontend_workspace_export(output_dir=tmp_path, api_base_url="http://127.0.0.1:8790")

    assert result["acceptance"]["passed"] is True
    assert result["acceptance"]["score"] == 100
    assert (tmp_path / "frontend_workspace_acceptance_report.json").exists()
    assert (tmp_path / "frontend_workspace_acceptance_report.md").exists()
    assert "Phase104D 前端联调工作区验收报告" in (tmp_path / "frontend_workspace_acceptance_report.md").read_text(encoding="utf-8")

    report = validate_frontend_integration_workspace(tmp_path)
    keys = {check.key for check in report.checks}
    assert {"required_files", "client_methods", "page_adapters", "workflow_smoke", "redaction"}.issubset(keys)
    assert report.passed is True


def test_phase104d_detects_missing_or_leaked_workspace_files(tmp_path) -> None:
    build_frontend_integration_workspace(tmp_path)
    (tmp_path / "src" / "api" / "qualibugClient.ts").unlink()
    (tmp_path / "src" / "api" / "leaky.ts").write_text("const bad = 'Bearer raw';", encoding="utf-8")

    report = validate_frontend_integration_workspace(tmp_path)
    assert report.passed is False
    details = "\n".join(check.detail for check in report.checks)
    assert "src/api/qualibugClient.ts" in details
    assert "Bearer raw" in details
    assert scan_workspace_for_secret_leaks(tmp_path)
