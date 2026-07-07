from __future__ import annotations

import os

os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")

import ai_test_asset_center.display_ready_formatter as display_ready_formatter
import ai_test_asset_center.private_pilot_service as private_pilot_service
from ai_test_asset_center.private_pilot_server import install_customer_delivery_gate_patch
from ai_test_asset_center.private_pilot_service import PrivatePilotHandler, _normalize_command_center_envelope


def _ready_item(item_id: str) -> dict:
    return {
        "id": item_id,
        "risk_id": item_id,
        "title": "支付金额守恒失败",
        "severity": "P1",
        "bug_status": "reproduced",
        "gate_passed": True,
        "execution_status": "executed",
        "confirmation_status": "validated_candidate",
        "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
        "evidence_status": {
            "semantic_verdict": "SEMANTIC_CONFIRMED",
            "business_evidence_status": "VALIDATED",
            "final_review_status": "VALIDATED_CANDIDATE",
            "missing_requirements": [],
        },
        "expected": "订单金额应等于支付金额",
        "actual": "订单金额 100，支付金额 1",
        "raw_evidence": {
            "has_real_evidence": True,
            "timestamp": "2026-07-06T12:00:00Z",
            "request_raw": {"method": "POST", "path": "/api/payments"},
            "response_raw": {"status_code": 200, "body": {"paid_amount": 1}},
        },
        "reproduction": {
            "method": "POST",
            "path": "/api/payments",
            "is_synthetic": False,
            "har_evidence": {"status_code": 200, "response_body": {"paid_amount": 1}},
        },
    }


def test_command_center_envelope_preserves_current_scan_scope_when_family_shelf_is_larger() -> None:
    install_customer_delivery_gate_patch()
    payload = _normalize_command_center_envelope(
        {
            "ok": True,
            "data": {
                "defects": [_ready_item("BUG-1"), _ready_item("BUG-2")],
                "clues": [],
                "scan_meta": {
                    "total_findings": 1,
                    "materialized_findings": 1,
                    "customer_ready_defects": 1,
                    "ready_bug_count": 1,
                    "current_report_breakdown": {"total_findings": 1, "category_counts": {"state_machine": 1}},
                },
                "executive_summary": {
                    "total_findings": 1,
                    "materialized_findings": 1,
                    "customer_ready_defects": 1,
                    "ready_bugs": 1,
                },
                "value_metrics": {
                    "canonical_risk_count": 1,
                    "materialized_risk_count": 1,
                    "ready_bug_count": 1,
                },
            },
        }
    )
    data = payload["data"]

    assert len(data["defects"]) == 2
    assert data["scan_meta"]["total_findings"] == 1
    assert data["scan_meta"]["customer_ready_defects"] == 1
    assert data["scan_meta"]["family_customer_ready_defect_count"] == 2
    assert data["executive_summary"]["total_findings"] == 1
    assert data["executive_summary"]["family_customer_ready_defects"] == 2
    assert data["value_metrics"]["current_report_total_findings"] == 1
    assert data["value_metrics"]["family_customer_ready_defect_count"] == 2


def test_build_command_center_separates_current_scan_counts_from_family_defect_shelf(monkeypatch, tmp_path) -> None:
    install_customer_delivery_gate_patch()
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    handler.headers = {}

    monkeypatch.setattr(
        handler,
        "_load_v12_report",
        lambda project_id, root: {
            "project_name": project_id,
            "generated_at_utc": "2026-07-07T18:20:00Z",
            "report_source_path": "aggregated:platform_workspace/demo/evidence_bundles/findings.json",
            "real_findings": [
                {
                    "risk_id": "FAM-1",
                    "category": "state_machine",
                    "title": "family finding 1",
                    "bug_status": "reproduced",
                    "gate_passed": True,
                },
                {
                    "risk_id": "FAM-2",
                    "category": "state_machine",
                    "title": "family finding 2",
                    "bug_status": "reproduced",
                    "gate_passed": True,
                }
            ],
            "scan_id": "scan_family_demo",
            "total_findings": 2,
        },
    )
    monkeypatch.setattr(
        handler,
        "_load_current_scan_report",
        lambda project_id, root: {
            "scan_id": "scan_scope_demo",
            "total_findings": 1,
            "real_findings": [
                {
                    "risk_id": "CUR-1",
                    "category": "state_machine",
                    "title": "当前轮次真实 finding",
                    "bug_status": "reproduced",
                    "gate_passed": True,
                }
            ],
            "report_source_path": "platform_outputs/enterprise-project/scan_result.json",
        },
    )
    monkeypatch.setattr(handler, "_load_enterprise_docs", lambda project_id, root: [])
    monkeypatch.setattr(handler, "_load_knowledge_summary", lambda project_id, root: {})
    monkeypatch.setattr(
        handler,
        "_auto_discovery_payload",
        lambda project_id, root, report: {"continuous_discovery_campaign": {"campaign": {"campaign_id": "CMP_SCOPE"}}},
    )
    monkeypatch.setattr(display_ready_formatter, "format_findings_display_ready", lambda risks, enterprise_ctx, report: (risks, {}))
    monkeypatch.setattr(display_ready_formatter, "sanitize_customer_evidence_payload", lambda payload: payload)
    monkeypatch.setattr(handler, "_v12_findings", lambda report, enterprise_docs=None: [_ready_item("BUG-1"), _ready_item("BUG-2")])
    monkeypatch.setattr(handler, "_load_db_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_perf_regressions", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_spectrum_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_multi_layer_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_dedupe_risks", lambda risks: risks)
    monkeypatch.setattr(handler, "_scan_counter", lambda project_id, root: {"count": 3, "last_scan_at": "2026-07-07T18:21:00Z"})
    monkeypatch.setattr(private_pilot_service, "_load_real_project_discovery_payload", lambda root, project_id: {"continuous_discovery_campaign": {"campaign": {"campaign_id": "CMP_SCOPE"}}})
    monkeypatch.setattr(private_pilot_service, "_current_campaign_bundle_finding_stats", lambda project_id, root, campaign_payload: {"raw": 1, "deduped": 1})

    payload = handler._build_command_center("enterprise-project", tmp_path)
    data = payload["data"]

    assert len(data["defects"]) == 2
    assert data["scan_meta"]["scan_id"] == "scan_scope_demo"
    assert data["scan_meta"]["total_findings"] == 1
    assert data["scan_meta"]["materialized_findings"] == 1
    assert data["scan_meta"]["customer_ready_defects"] == 1
    assert data["scan_meta"]["family_customer_ready_defect_count"] == 2
    assert data["scan_meta"]["current_campaign_bundle_finding_count_raw"] == 1
    assert data["executive_summary"]["total_findings"] == 1
    assert data["executive_summary"]["customer_ready_defects"] == 1
    assert data["executive_summary"]["family_customer_ready_defects"] == 2
    assert data["value_metrics"]["current_report_total_findings"] == 1
    assert data["value_metrics"]["family_customer_ready_defect_count"] == 2
