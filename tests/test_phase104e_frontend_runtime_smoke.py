from __future__ import annotations

import json

from ai_test_asset_center.phase104_frontend_runtime_smoke import (
    PHASE104E_VERSION,
    render_runtime_smoke_markdown,
    run_frontend_runtime_smoke,
    main,
)


def test_phase104e_builds_workspace_and_runs_runtime_smoke(tmp_path):
    workspace_dir = tmp_path / "workspace"
    output_dir = tmp_path / "runtime_smoke"

    report = run_frontend_runtime_smoke(
        workspace_dir=workspace_dir,
        output_dir=output_dir,
        scenario="manufacturing",
        build_workspace=True,
    )

    assert report.version == PHASE104E_VERSION
    assert report.passed is True
    assert report.score == 100
    assert report.workspace_validation_passed is True
    assert report.redaction_status == "safe"
    assert report.secret_leak_findings == []
    assert report.seed_project_id == "demo_manufacturing_erp_v3"
    assert report.created_project_id
    assert report.step_count >= 20
    step_keys = {step.key for step in report.steps}
    assert "seed_dashboard" in step_keys
    assert "seed_risk_detail" in step_keys
    assert "create_project" in step_keys
    assert "run_environment_preflight" in step_keys
    assert "start_test_run" in step_keys
    assert "method_safety" in step_keys

    json_report = output_dir / "frontend_runtime_smoke_report.json"
    md_report = output_dir / "frontend_runtime_smoke_report.md"
    assert json_report.exists()
    assert md_report.exists()
    payload = json.loads(json_report.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["redaction_status"] == "safe"
    assert "raw-token" not in json_report.read_text(encoding="utf-8")
    assert "Traceback" not in json_report.read_text(encoding="utf-8")


def test_phase104e_markdown_summarizes_steps(tmp_path):
    report = run_frontend_runtime_smoke(
        workspace_dir=tmp_path / "workspace",
        output_dir=tmp_path / "out",
        build_workspace=True,
    )

    markdown = render_runtime_smoke_markdown(report)

    assert "Phase104E 前端运行时联调 Smoke 报告" in markdown
    assert "seed_live_map" in markdown
    assert "method_safety" in markdown
    assert "通过" in markdown


def test_phase104e_cli_build_workspace(tmp_path, capsys):
    code = main([
        "--workspace-dir",
        str(tmp_path / "workspace"),
        "--output-dir",
        str(tmp_path / "out"),
        "--build-workspace",
    ])

    captured = capsys.readouterr()

    assert code == 0
    assert "phase104e-frontend-runtime-smoke-v1" in captured.out
    assert (tmp_path / "out" / "frontend_runtime_smoke_report.json").exists()
