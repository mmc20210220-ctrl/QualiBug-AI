from __future__ import annotations

import json
import os

os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")

import ai_test_asset_center.private_pilot_service as private_pilot_service
import ai_test_asset_center.display_ready_formatter as display_ready_formatter
from ai_test_asset_center.private_pilot_server import install_customer_delivery_gate_patch
from ai_test_asset_center.private_pilot_service import PrivatePilotHandler


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _ready_finding(item_id: str, title: str, severity: str) -> dict:
    path = f"/source-derived-findings/{item_id}"
    return {
        "risk_id": item_id,
        "id": item_id,
        "candidate_id": f"candidate-{item_id}",
        "slice_id": f"slice-{item_id}",
        "obligation_id": f"obligation-{item_id}",
        "experiment_id": f"experiment-{item_id}",
        "execution_id": f"execution-{item_id}",
        "evidence_id": f"evidence-{item_id}",
        "finding_id": f"finding-{item_id}",
        "title": title,
        "severity": severity,
        "bug_status": "reproduced",
        "gate_passed": True,
        "execution_status": "executed",
        "confirmation_status": "confirmed",
        "customer_delivery_status": "defect",
        "expected": "source invariant holds",
        "actual": "source invariant violated",
        "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
        "evidence_status": {
            "semantic_verdict": "SEMANTIC_CONFIRMED",
            "business_evidence_status": "VALIDATED",
            "final_review_status": "VALIDATED_CANDIDATE",
            "missing_requirements": [],
        },
        "raw_evidence": {
            "has_real_evidence": True,
            "timestamp": "2026-07-07T21:00:00Z",
            "request_raw": {"method": "GET", "path": path},
            "response_raw": {"status_code": 200, "body": {"violated": True}},
        },
        "reproduction": {
            "method": "GET",
            "path": path,
            "is_synthetic": False,
            "har_evidence": {"status_code": 200, "response_body": {"violated": True}},
        },
    }


def test_build_command_center_exposes_regression_summary_and_finding_status(monkeypatch, tmp_path) -> None:
    install_customer_delivery_gate_patch()
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    handler.headers = {}

    monkeypatch.setattr(handler, "_load_v12_report", lambda project_id, root: {
        "project_name": project_id,
        "generated_at_utc": "2026-07-07T21:00:00Z",
        "report_source_path": "aggregated:platform_workspace/demo/evidence_bundles/findings.json",
        "real_findings": [
            _ready_finding("ISSUE-1", "重复支付仍可触发", "P0"),
            _ready_finding("ISSUE-2", "非法取消订单", "P1"),
        ],
    })
    monkeypatch.setattr(handler, "_load_enterprise_docs", lambda project_id, root: [])
    monkeypatch.setattr(handler, "_load_knowledge_summary", lambda project_id, root: {})
    monkeypatch.setattr(handler, "_auto_discovery_payload", lambda project_id, root, report: {})
    monkeypatch.setattr(display_ready_formatter, "format_findings_display_ready", lambda risks, enterprise_ctx, report: (risks, {}))
    monkeypatch.setattr(display_ready_formatter, "sanitize_customer_evidence_payload", lambda payload: payload)
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
    defects = data["project_history"]["deliverable_findings"]

    assert data["defects"] == []
    assert data["risks"] == []
    assert data["formal_count_projection"]["formal_customer_deliverable_count"] == 0
    assert data["finding_classification"]["deliverable"] == []
    assert data["scope_counts"]["current_run_formal_deliverable"] == 0
    assert data["value_metrics"]["defect_count"] == 0
    assert data["value_metrics"]["p0_count"] == 0
    assert data["value_metrics"]["p1_count"] == 0
    assert data["executive_summary"]["critical_bugs"] == 0
    assert data["executive_summary"]["high_priority_bugs"] == 0
    assert data["defect_grouped_summary"]["total_defects"] == 0
    assert data["defect_priority_summary"]["total_defects"] == 0
    assert data["defect_repro_summary"]["total_defects"] == 0
    assert data["defect_delivery_cards"]["total_cards"] == 0
    assert data["data_contract"]["materialized_risk_count"] == 0
    assert data["data_contract"]["ready_bug_count"] == 0
    assert data["delivery_tracks"]["defects"]["ready_bug_count"] == 0
    assert defects == []
    assert regression_summary["covered_defect_count"] == 0
    assert regression_summary["failed_defect_count"] == 0
    assert regression_summary["pending_defect_count"] == 0
    assert data["scan_meta"]["regression_failed_defect_count"] == 0
    assert data["value_metrics"]["regression_pending_defect_count"] == 0
    assert data["executive_summary"]["regression_failed_defects"] == 0


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
