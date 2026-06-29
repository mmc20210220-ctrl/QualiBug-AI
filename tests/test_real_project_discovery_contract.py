from __future__ import annotations

import json
from pathlib import Path


def test_real_project_discovery_returns_stable_summary_contract(tmp_path: Path) -> None:
    from ai_test_asset_center.real_project_defect_discovery import run_real_project_discovery

    project = "contract_probe"
    input_dir = tmp_path / "platform_inputs" / project
    input_dir.mkdir(parents=True)
    (input_dir / "real_project_config.json").write_text(
        json.dumps(
            {
                "project_name": "Contract Probe",
                "environment": "test",
                "discovery_mode": "safe",
                "max_probe_count": 10,
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
                    "/api/orders/{id}": {
                        "get": {
                            "parameters": [
                                {
                                    "name": "id",
                                    "in": "path",
                                    "required": True,
                                    "schema": {"type": "string"},
                                }
                            ],
                            "responses": {"200": {"description": "ok"}},
                        }
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_real_project_discovery(project, tmp_path)
    summary = result["summary"]

    assert result["status"] == "succeeded"
    assert result["project_id"] == project
    assert result["issue_count"] == len(result["issues"])
    assert result["probe_count"] == len(result["probes"])
    assert result["network_requests"] == 0
    assert result["http_request_count"] == 0
    assert result["http_blocked_count"] == 0
    assert result["output_dir"].endswith("platform_outputs\\contract_probe\\real_project") or result[
        "output_dir"
    ].endswith("platform_outputs/contract_probe/real_project")
    assert summary["issue_count"] == result["issue_count"]
    assert summary["probe_count"] == result["probe_count"]
    assert summary["candidate_only_issue_count"] <= result["issue_count"]
    assert summary["high_confidence_issue_count"] == 0
    assert result["enterprise_testops_control_plane"]["defect_quality_summary"]["candidate_only_count"] >= 1
    assert result["metrics"]["issue_count"] == result["issue_count"]
    assert result["metrics"]["probe_count"] == result["probe_count"]
    assert "bug_family_coverage" in result
    assert result["metrics"]["api_contract_probe_count"] >= 1
    assert result["metrics"]["browser_ui_replay_probe_count"] >= 1
    assert result["metrics"]["frontend_runtime_probe_count"] >= 1
    assert result["metrics"]["performance_stability_probe_count"] >= 1
    assert result["metrics"]["covered_bug_family_count"] >= 1
    assert "browser_ui_health" in result
    assert result["browser_ui_health"]["enabled"] is False
    assert result["browser_ui_health"]["reason_code"] == "E_BROWSER_UI_DISABLED"
    assert result["metrics"]["browser_ui_reason_code"] == "E_BROWSER_UI_DISABLED"
    assert result["metrics"]["browser_ui_blocked_probe_count"] >= 1
    assert result["bug_family_coverage"]["missing_family_reasons"]["ui"]["reason_code"] == "E_BROWSER_UI_DISABLED"
    planner_summary = result["risk_based_plan_summary"]
    assert planner_summary["browser_ui_budget_constrained"] is True
    assert planner_summary["browser_ui_reason_code"] == "E_BROWSER_UI_DISABLED"
    assert planner_summary["browser_ui_blocked_probe_count"] >= 1
