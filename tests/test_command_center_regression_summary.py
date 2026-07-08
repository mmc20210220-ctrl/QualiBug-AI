from __future__ import annotations

import json
import os

os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")

import ai_test_asset_center.private_pilot_service as private_pilot_service
from ai_test_asset_center.private_pilot_server import install_customer_delivery_gate_patch
from ai_test_asset_center.private_pilot_service import PrivatePilotHandler


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_build_command_center_exposes_regression_summary_and_finding_status(monkeypatch, tmp_path) -> None:
    install_customer_delivery_gate_patch()
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    handler.headers = {}

    monkeypatch.setattr(handler, "_load_v12_report", lambda project_id, root: {
        "project_name": project_id,
        "generated_at_utc": "2026-07-07T21:00:00Z",
        "report_source_path": "aggregated:platform_workspace/demo/evidence_bundles/findings.json",
        "real_findings": [
            {"risk_id": "ISSUE-1", "title": "重复支付仍可触发", "severity": "P0", "bug_status": "reproduced", "gate_passed": True},
            {"risk_id": "ISSUE-2", "title": "非法取消订单", "severity": "P1", "bug_status": "reproduced", "gate_passed": True},
        ],
    })
    monkeypatch.setattr(handler, "_load_enterprise_docs", lambda project_id, root: [])
    monkeypatch.setattr(handler, "_load_knowledge_summary", lambda project_id, root: {})
    monkeypatch.setattr(handler, "_auto_discovery_payload", lambda project_id, root, report: {})
    monkeypatch.setattr(handler, "_v12_findings", lambda report, enterprise_docs=None: list(report.get("real_findings") or []))
    monkeypatch.setattr(handler, "_load_db_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_perf_regressions", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_spectrum_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_multi_layer_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_dedupe_risks", lambda risks: risks)
    monkeypatch.setattr(handler, "_scan_counter", lambda project_id, root: {})
    monkeypatch.setattr(private_pilot_service, "_load_real_project_discovery_payload", lambda root, project_id: {})
    monkeypatch.setattr(private_pilot_service, "_partition_delivery_tracks", lambda items: (items, []))

    _write_json(
        tmp_path / "platform_outputs" / "enterprise-project" / "regression_suite" / "regression_suite.json",
        {
            "summary": {"total_probe_count": 2, "smoke_count": 1, "release_count": 2, "full_count": 2},
            "modes": {
                "release": {
                    "items": [
                        {"issue_id": "ISSUE-1", "regression_probe_id": "REG-ISSUE-1"},
                        {"issue_id": "ISSUE-2", "regression_probe_id": "REG-ISSUE-2"},
                    ]
                }
            },
        },
    )
    _write_json(
        tmp_path / "platform_outputs" / "enterprise-project" / "regression_run" / "regression_run_result.json",
        {
            "summary": {
                "generated_at": "2026-07-07 21:15:00",
                "suite_mode": "release",
                "suite_mode_label": "Release 发布回归",
                "total_probe_count": 1,
                "executed_count": 1,
                "passed_count": 0,
                "failed_count": 1,
                "needs_review_count": 0,
                "skipped_count": 0,
            },
            "items": [
                {
                    "issue_id": "ISSUE-1",
                    "regression_probe_id": "REG-ISSUE-1",
                    "status": "failed",
                    "reason": "响应状态不符合预期。",
                }
            ],
            "ci_feedback": {
                "gate_status": "failed",
                "ci_message": "P0/P1 回归失败，建议阻断发布。",
                "reopen_issue_ids": ["ISSUE-1"],
            },
        },
    )
    _write_json(
        tmp_path / "platform_outputs" / "enterprise-project" / "regression_run" / "regression_run_history.json",
        [
            {
                "generated_at": "2026-07-06 20:00:00",
                "suite_mode": "smoke",
                "suite_mode_label": "Smoke 快速回归",
                "gate_status": "failed",
                "ci_message": "回归失败。",
                "summary": {
                    "total_probe_count": 2,
                    "executed_count": 2,
                    "passed_count": 1,
                    "failed_count": 1,
                    "needs_review_count": 0,
                    "skipped_count": 0,
                },
                "items": [
                    {
                        "issue_id": "ISSUE-1",
                        "regression_probe_id": "REG-ISSUE-1",
                        "status": "failed",
                        "reason": "首次回归仍然失败。",
                    }
                ],
            },
            {
                "generated_at": "2026-07-07 21:15:00",
                "suite_mode": "release",
                "suite_mode_label": "Release 发布回归",
                "gate_status": "passed",
                "ci_message": "回归套件通过，允许继续发布。",
                "summary": {
                    "total_probe_count": 2,
                    "executed_count": 2,
                    "passed_count": 2,
                    "failed_count": 0,
                    "needs_review_count": 0,
                    "skipped_count": 0,
                },
                "items": [
                    {
                        "issue_id": "ISSUE-1",
                        "regression_probe_id": "REG-ISSUE-1",
                        "status": "passed",
                        "reason": "修复后已通过回归。",
                    }
                ],
            },
        ],
    )

    payload = handler._build_command_center("enterprise-project", tmp_path)
    data = payload["data"]
    regression_summary = data["regression_summary"]
    defects = data["defects"]

    assert regression_summary["covered_defect_count"] == 2
    assert regression_summary["failed_defect_count"] == 1
    assert regression_summary["pending_defect_count"] == 1
    assert regression_summary["latest_run"]["gate_status"] == "failed"
    assert regression_summary["headline"].startswith("最近一次回归")
    assert defects[0]["regression"]["latest_status"] == "failed"
    assert defects[0]["regression"]["latest_status_label"] == "回归失败"
    assert defects[0]["regression"]["lifecycle_status"] == "regression_failed"
    assert defects[0]["regression"]["history_count"] == 2
    assert defects[0]["regression"]["history"][0]["status"] == "passed"
    assert defects[0]["regression"]["history"][1]["status"] == "failed"
    assert defects[1]["regression"]["latest_status"] == "pending"
    assert defects[1]["regression"]["lifecycle_status"] == "pending_regression"
    assert regression_summary["history_run_count"] == 2
    assert regression_summary["recent_runs"][0]["suite_mode"] == "release"
    assert regression_summary["trend_direction"] == "improving"
    assert "趋势向好" in regression_summary["trend_summary"]
    assert regression_summary["validation_summary"]["double_run_verified"] is True
    assert regression_summary["release_recommendation"] == "block_release"
    assert regression_summary["release_recommendation_label"] == "建议阻断发布"
    assert regression_summary["customer_delivery_readiness"] == "blocked"
    assert data["scan_meta"]["regression_gate_status"] == "failed"
    assert data["scan_meta"]["release_recommendation"] == "block_release"
    assert data["scan_meta"]["regression_double_run_verified"] == 1
    assert data["value_metrics"]["regression_pending_defect_count"] == 1
    assert data["value_metrics"]["release_recommendation"] == "block_release"
    assert data["executive_summary"]["regression_failed_defects"] == 1
    assert data["executive_summary"]["release_recommendation_label"] == "建议阻断发布"


def test_build_command_center_uses_continue_regression_when_history_is_insufficient(monkeypatch, tmp_path) -> None:
    install_customer_delivery_gate_patch()
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    handler.headers = {}

    monkeypatch.setattr(handler, "_load_v12_report", lambda project_id, root: {
        "project_name": project_id,
        "generated_at_utc": "2026-07-07T21:00:00Z",
        "report_source_path": "aggregated:platform_workspace/demo/evidence_bundles/findings.json",
        "real_findings": [
            {"risk_id": "ISSUE-1", "title": "重复支付仍可触发", "severity": "P0", "bug_status": "reproduced", "gate_passed": True},
        ],
        "commercial_assets": {
            "status": "materialized",
            "commercial_handoff": {"status": "ready_for_customer_acceptance"},
            "delivery_package": {"status": "created"},
        },
    })
    monkeypatch.setattr(handler, "_load_enterprise_docs", lambda project_id, root: [])
    monkeypatch.setattr(handler, "_load_knowledge_summary", lambda project_id, root: {})
    monkeypatch.setattr(handler, "_auto_discovery_payload", lambda project_id, root, report: {})
    monkeypatch.setattr(handler, "_v12_findings", lambda report, enterprise_docs=None: list(report.get("real_findings") or []))
    monkeypatch.setattr(handler, "_load_db_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_perf_regressions", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_spectrum_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_multi_layer_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_dedupe_risks", lambda risks: risks)
    monkeypatch.setattr(handler, "_scan_counter", lambda project_id, root: {})
    monkeypatch.setattr(private_pilot_service, "_load_real_project_discovery_payload", lambda root, project_id: {})
    monkeypatch.setattr(private_pilot_service, "_partition_delivery_tracks", lambda items: (items, []))

    _write_json(
        tmp_path / "platform_outputs" / "enterprise-project" / "regression_suite" / "regression_suite.json",
        {
            "summary": {"total_probe_count": 1, "smoke_count": 1, "release_count": 1, "full_count": 1},
            "modes": {"release": {"items": [{"issue_id": "ISSUE-1", "regression_probe_id": "REG-ISSUE-1"}]}},
        },
    )
    _write_json(
        tmp_path / "platform_outputs" / "enterprise-project" / "regression_run" / "regression_run_result.json",
        {
            "summary": {
                "generated_at": "2026-07-07 21:15:00",
                "suite_mode": "release",
                "suite_mode_label": "Release 发布回归",
                "total_probe_count": 1,
                "executed_count": 1,
                "passed_count": 1,
                "failed_count": 0,
                "needs_review_count": 0,
                "skipped_count": 0,
            },
            "items": [
                {
                    "issue_id": "ISSUE-1",
                    "regression_probe_id": "REG-ISSUE-1",
                    "status": "passed",
                    "reason": "修复后已通过回归。",
                }
            ],
            "ci_feedback": {
                "gate_status": "passed",
                "ci_message": "当前回归通过。",
                "reopen_issue_ids": [],
            },
        },
    )
    _write_json(
        tmp_path / "platform_outputs" / "enterprise-project" / "regression_run" / "regression_run_history.json",
        [
            {
                "generated_at": "2026-07-07 21:15:00",
                "suite_mode": "release",
                "suite_mode_label": "Release 发布回归",
                "gate_status": "passed",
                "ci_message": "当前回归通过。",
                "summary": {
                    "total_probe_count": 1,
                    "executed_count": 1,
                    "passed_count": 1,
                    "failed_count": 0,
                    "needs_review_count": 0,
                    "skipped_count": 0,
                },
                "items": [
                    {
                        "issue_id": "ISSUE-1",
                        "regression_probe_id": "REG-ISSUE-1",
                        "status": "passed",
                        "reason": "修复后已通过回归。",
                    }
                ],
            }
        ],
    )

    payload = handler._build_command_center("enterprise-project", tmp_path)
    regression_summary = payload["data"]["regression_summary"]

    assert regression_summary["latest_run"]["gate_status"] == "passed"
    assert regression_summary["validation_summary"]["double_run_verified"] is False
    assert regression_summary["release_recommendation"] == "continue_regression"
    assert regression_summary["release_recommendation_label"] == "建议继续执行真实回归"
    assert regression_summary["customer_delivery_readiness"] == "needs_more_validation"
