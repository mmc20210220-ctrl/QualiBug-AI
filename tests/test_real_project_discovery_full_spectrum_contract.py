from __future__ import annotations

import json
from pathlib import Path


def test_real_project_discovery_exposes_full_spectrum_coverage_contract(tmp_path: Path) -> None:
    from ai_test_asset_center.real_project_defect_discovery import run_real_project_discovery

    project = "full_spectrum_probe"
    input_dir = tmp_path / "platform_inputs" / project
    input_dir.mkdir(parents=True)
    task_journey_manifest = tmp_path / "frontend_task_journeys_manifest.json"
    task_journey_manifest.write_text(
        json.dumps(
            {
                "version": "phase106d-frontend-project-routes-v1",
                "journeys": [
                    {
                        "journey_id": "enter_command_center",
                        "title": "进入质量驾驶舱",
                        "entry_route": "/projects/:projectId",
                        "required_project_context": True,
                        "steps": ["load_project_detail", "open_command_center"],
                        "success_signals": ["command_center_link_visible", "route_navigation_success"],
                        "failure_signals": ["missing_cta", "project_context_lost"],
                        "defect_family": "uiux",
                        "risk_tags": ["journey_break", "project_scope_binding"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (input_dir / "real_project_config.json").write_text(
        json.dumps(
            {
                "project_name": "Full Spectrum Probe",
                "environment": "test",
                "discovery_mode": "safe",
                "request_timeout_seconds": 5,
                "max_probe_count": 30,
                "deployment_mode": "private_deployment",
                "environment_class": "sandbox",
                "frontend_task_journeys_manifest": str(task_journey_manifest),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (input_dir / "openapi.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "paths": {
                    "/api/orders": {"get": {"responses": {"200": {"description": "ok"}}}},
                    "/api/orders/{id}": {"get": {"responses": {"200": {"description": "ok"}}}},
                    "/api/admin/orders": {"get": {"responses": {"200": {"description": "ok"}}}},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_real_project_discovery(project, tmp_path)
    metrics = result["metrics"]
    coverage = result["bug_family_coverage"]
    matrix = result["full_spectrum_capability_matrix"]

    assert result["status"] == "succeeded"
    assert metrics["api_contract_probe_count"] >= 1
    assert metrics["browser_ui_replay_probe_count"] >= 1
    assert metrics["frontend_task_journey_probe_count"] >= 1
    assert metrics["frontend_runtime_probe_count"] >= 1
    assert metrics["frontend_ux_probe_count"] >= 1
    assert metrics["compatibility_probe_count"] >= 1
    assert metrics["performance_stability_probe_count"] >= 1
    assert "ui_design_oracle_issue_count" in metrics
    assert "ui_design_oracle_strong_signal_count" in metrics
    assert "ui_design_oracle_weak_signal_count" in metrics
    assert "ui_design_oracle_signal_basis_distribution" in metrics
    assert "ui_design_oracle_signal_basis_legend" in metrics
    assert "ui_design_oracle_signal_basis_recommended_actions" in metrics
    assert "ui_design_oracle_signal_basis_action_reasons" in metrics
    assert "ui_design_oracle_role_signal_count" in metrics
    assert "ui_design_oracle_keyword_signal_count" in metrics
    assert "ui_design_oracle_token_signal_count" in metrics
    assert "ui_design_oracle_none_signal_count" in metrics
    assert "ui_design_oracle_journey_oracle_count" in metrics
    assert "ui_design_oracle_journey_covered_count" in metrics
    assert "ui_design_oracle_journey_missing_count" in metrics
    assert "ui_design_oracle_journey_issue_count" in metrics
    assert "ui_design_oracle_missing_component_count" in metrics
    assert "ui_design_oracle_missing_feedback_count" in metrics
    assert "browser_ui_health" in result
    assert "browser_ui_reason_code" in metrics
    assert "browser_ui_severity" in metrics
    assert "browser_ui_blocked_probe_count" in metrics
    planner_summary = result["risk_based_plan_summary"]
    assert "browser_ui_budget_constrained" in planner_summary
    assert "browser_ui_fallback_families" in planner_summary
    assert "ui_design_oracle_issue_count" in planner_summary
    assert "ui_design_oracle_strong_signal_count" in planner_summary
    assert "ui_design_oracle_weak_signal_count" in planner_summary
    assert "ui_design_oracle_signal_basis_distribution" in planner_summary
    assert "ui_design_oracle_signal_basis_legend" in planner_summary
    assert "ui_design_oracle_signal_basis_recommended_actions" in planner_summary
    assert "ui_design_oracle_signal_basis_action_reasons" in planner_summary
    assert "ui_design_oracle_role_signal_count" in planner_summary
    assert "ui_design_oracle_keyword_signal_count" in planner_summary
    assert "ui_design_oracle_token_signal_count" in planner_summary
    assert "ui_design_oracle_none_signal_count" in planner_summary
    assert "ui_design_oracle_journey_oracle_count" in planner_summary
    assert "ui_design_oracle_journey_covered_count" in planner_summary
    assert "ui_design_oracle_journey_missing_count" in planner_summary
    assert "ui_design_oracle_journey_issue_count" in planner_summary
    assert "ui_design_oracle_missing_component_count" in planner_summary
    assert "ui_design_oracle_missing_feedback_count" in planner_summary
    assert coverage["covered_family_count"] >= 5
    assert coverage["declared_source_count"] >= coverage["materialized_source_count"]
    assert coverage["missing_source_count"] >= 1
    assert matrix["source_row_count"] >= coverage["declared_source_count"]
    assert matrix["summary"]["declared_source_count"] >= matrix["summary"]["materialized_source_count"]
    covered_families = {row["family_id"] for row in coverage["rows"] if row["probe_count"] > 0 or row["issue_count"] > 0}
    assert {"api_contract", "ui", "uiux", "compatibility", "performance"}.issubset(covered_families)
    api_contract_row = next(row for row in coverage["rows"] if row["family_id"] == "api_contract")
    assert api_contract_row["declared_source_count"] >= 2
    assert api_contract_row["materialized_source_count"] >= 1
    assert api_contract_row["missing_declared_sources"]
