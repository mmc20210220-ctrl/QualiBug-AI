import json
from pathlib import Path

from ai_test_asset_center.private_pilot_regression_run_visibility_patch import (
    compact_regression_run,
    inject_regression_run,
)


def test_compact_regression_run_keeps_customer_verdict_and_failures() -> None:
    result = {
        "summary": {
            "generated_at": "2026-07-09 10:00:00",
            "suite_mode": "release",
            "suite_mode_label": "发布回归",
            "total_probe_count": 3,
            "executed_count": 3,
            "passed_count": 1,
            "failed_count": 1,
            "needs_review_count": 1,
            "p0_p1_failed_count": 1,
        },
        "ci_feedback": {"gate_status": "failed", "ci_message": "存在 P0/P1 回归失败", "exit_code": 2},
        "items": [
            {"regression_probe_id": "p_ok", "status": "passed", "passed": True, "path": "/api/a", "execution": {"status_code": 403}},
            {"regression_probe_id": "p_fail", "status": "failed", "passed": False, "severity": "P1", "title": "越权仍复现", "path": "/api/b", "reason": "HTTP 200", "execution": {"status_code": 200}, "regression_oracle": {"expected_status_code": 403}},
            {"regression_probe_id": "p_review", "status": "needs_review", "passed": False, "path": "/api/c", "reason": "缺少强断言"},
        ],
        "failures": [
            {"regression_probe_id": "p_fail", "status": "failed", "passed": False, "severity": "P1", "title": "越权仍复现", "method": "GET", "path": "/api/b", "reason": "HTTP 200", "execution": {"status_code": 200}, "regression_oracle": {"expected_status_code": 403}}
        ],
        "history_ref": "platform_outputs/demo/regression_run/regression_run_history.json",
        "history_size": 2,
    }

    compact = compact_regression_run(result)

    assert compact["status"] == "available"
    assert compact["gate_status"] == "failed"
    assert compact["failed_count"] == 1
    assert compact["needs_review_count"] == 1
    assert compact["failures"][0]["oracle"]["expected_status_code"] == 403
    assert compact["history_size"] == 2


def test_inject_regression_run_lifts_latest_run_to_command_center(tmp_path: Path) -> None:
    project = "demo_project"
    out_dir = tmp_path / "platform_outputs" / project / "regression_run"
    out_dir.mkdir(parents=True)
    (out_dir / "regression_run_result.json").write_text(
        json.dumps({
            "summary": {
                "generated_at": "2026-07-09 10:00:00",
                "suite_mode": "release",
                "suite_mode_label": "发布回归",
                "total_probe_count": 2,
                "executed_count": 2,
                "passed_count": 2,
                "failed_count": 0,
                "needs_review_count": 0,
            },
            "ci_feedback": {"gate_status": "passed", "ci_message": "回归通过", "exit_code": 0},
            "items": [
                {"regression_probe_id": "p1", "status": "passed", "passed": True, "path": "/api/a", "execution": {"status_code": 403}},
                {"regression_probe_id": "p2", "status": "passed", "passed": True, "path": "/api/b", "execution": {"status_code": 409}},
            ],
            "history_ref": "platform_outputs/demo_project/regression_run/regression_run_history.json",
            "history_size": 1,
        }),
        encoding="utf-8",
    )

    payload = {"data": {"project_id": project, "scan_meta": {}, "value_metrics": {}, "executive_summary": {}, "data_contract": {}}}
    injected = inject_regression_run(payload, root=tmp_path)
    data = injected["data"]

    assert data["regression_run"]["gate_status"] == "passed"
    assert data["scan_meta"]["regression_run"]["passed_count"] == 2
    assert data["value_metrics"]["regression_last_gate_status"] == "passed"
    assert data["value_metrics"]["regression_last_failed_count"] == 0
    assert data["executive_summary"]["regression_run_label"] == "最近回归通过：2 个探针通过"
    assert data["data_contract"]["regression_run"]["display_key"] == "regression_run"

    dashboard_summary = data["regression_summary"]
    assert dashboard_summary["suite_exists"] is True
    assert dashboard_summary["covered_defect_count"] == 2
    assert dashboard_summary["passed_defect_count"] == 2
    assert dashboard_summary["failed_defect_count"] == 0
    assert dashboard_summary["pending_defect_count"] == 0
    assert dashboard_summary["latest_run"]["gate_status"] == "passed"
    assert dashboard_summary["latest_run"]["suite_mode_label"] == "发布回归"
    assert dashboard_summary["release_recommendation"] == "candidate_release"
    assert dashboard_summary["release_recommendation_label"] == "可进入候选发布"
    assert data["data_contract"]["regression_summary"]["display_key"] == "regression_summary"


def test_failed_regression_run_blocks_dashboard_release_recommendation(tmp_path: Path) -> None:
    project = "failed_project"
    out_dir = tmp_path / "platform_outputs" / project / "regression_run"
    out_dir.mkdir(parents=True)
    (out_dir / "regression_run_result.json").write_text(
        json.dumps({
            "summary": {
                "generated_at": "2026-07-09 10:00:00",
                "suite_mode": "release",
                "total_probe_count": 3,
                "executed_count": 3,
                "passed_count": 1,
                "failed_count": 1,
                "needs_review_count": 1,
            },
            "ci_feedback": {"gate_status": "failed", "ci_message": "存在回归失败", "exit_code": 2},
            "items": [
                {"regression_probe_id": "p1", "status": "passed", "passed": True},
                {"regression_probe_id": "p2", "status": "failed", "passed": False},
                {"regression_probe_id": "p3", "status": "needs_review", "passed": False},
            ],
            "failures": [{"regression_probe_id": "p2", "status": "failed", "passed": False, "reason": "HTTP 200"}],
            "history_size": 2,
        }),
        encoding="utf-8",
    )

    injected = inject_regression_run({"data": {"project_id": project, "regression_summary": {}}}, root=tmp_path)
    dashboard_summary = injected["data"]["regression_summary"]

    assert dashboard_summary["latest_run"]["gate_status"] == "failed"
    assert dashboard_summary["failed_defect_count"] == 1
    assert dashboard_summary["pending_defect_count"] == 1
    assert dashboard_summary["trend_direction"] == "regressing"
    assert dashboard_summary["release_recommendation"] == "block_release"
    assert dashboard_summary["release_recommendation_label"] == "建议阻断发布"
    assert dashboard_summary["validation_summary"]["double_run_verified"] is True
    assert dashboard_summary["validation_summary"]["repeated_failure_defect_count"] == 1
