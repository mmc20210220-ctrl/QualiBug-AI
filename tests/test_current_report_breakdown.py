from __future__ import annotations

import os

os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")

import ai_test_asset_center.private_pilot_service as private_pilot_service
from ai_test_asset_center.private_pilot_server import install_customer_delivery_gate_patch
from ai_test_asset_center.private_pilot_service import PrivatePilotHandler


def test_build_command_center_exposes_current_report_breakdown(monkeypatch, tmp_path) -> None:
    install_customer_delivery_gate_patch()
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    handler.headers = {}

    monkeypatch.setattr(handler, "_load_v12_report", lambda project_id, root: {
        "project_name": project_id,
        "generated_at_utc": "2026-07-07T18:20:00Z",
        "report_source_path": "aggregated:platform_workspace/demo/evidence_bundles/findings.json",
        "real_findings": [
            {"risk_id": "BUG-1", "category": "state_machine", "title": "非法取消", "bug_status": "reproduced", "gate_passed": True},
            {"risk_id": "BUG-2", "category": "state_machine", "title": "重复支付", "bug_status": "reproduced", "gate_passed": True},
            {"risk_id": "BUG-3", "category": "concurrency", "title": "并发支付", "bug_status": "reproduced", "gate_passed": True},
        ],
    })
    monkeypatch.setattr(handler, "_load_enterprise_docs", lambda project_id, root: [])
    monkeypatch.setattr(handler, "_load_knowledge_summary", lambda project_id, root: {})
    monkeypatch.setattr(handler, "_auto_discovery_payload", lambda project_id, root, report: {})
    monkeypatch.setattr(handler, "_v12_findings", lambda report, enterprise_docs=None: [])
    monkeypatch.setattr(handler, "_load_db_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_perf_regressions", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_spectrum_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_multi_layer_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_dedupe_risks", lambda risks: risks)
    monkeypatch.setattr(handler, "_scan_counter", lambda project_id, root: {})
    monkeypatch.setattr(private_pilot_service, "_load_real_project_discovery_payload", lambda root, project_id: {})

    payload = handler._build_command_center("enterprise-project", tmp_path)
    data = payload["data"]
    breakdown = data["scan_meta"]["current_report_breakdown"]

    assert breakdown["total_findings"] == 0
    assert breakdown["category_counts"] == {}
    assert breakdown["report_source_path"].startswith("aggregated:")
    assert data["canonical_scope"]["status"] == "BLOCKED"
    assert data["legacy_product_path_diagnostics"][
        "legacy_rows_loaded_for_diagnostics"
    ] == 3
    assert data["legacy_product_path_diagnostics"][
        "affects_current_counts_or_readiness"
    ] is False
    assert data["value_metrics"]["current_report_breakdown"] == breakdown
    assert data["executive_summary"]["current_report_breakdown"] == breakdown
    assert data["data_contract"]["current_report_breakdown"] == breakdown
