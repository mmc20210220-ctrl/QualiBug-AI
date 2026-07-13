from __future__ import annotations

import json

import ai_test_asset_center.__main__ as main_module
import ai_test_asset_center.private_pilot_service as private_pilot_service
from ai_test_asset_center.private_pilot_service import PrivatePilotHandler


def test_build_command_center_projects_augmented_campaign_governance_counts(monkeypatch, tmp_path) -> None:
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    handler.headers = {}

    monkeypatch.setattr(handler, "_load_v12_report", lambda project_id, root: {
        "project_name": project_id,
        "generated_at_utc": "2026-07-07T18:20:00Z",
        "report_source_path": "aggregated:demo",
        "real_findings": [
            {"risk_id": "BUG-CURRENT", "title": "重复支付", "method": "POST", "path": "/api/payments/pay"},
            {"risk_id": "BUG-HISTORY", "title": "非法取消", "method": "POST", "path": "/api/orders/{id}/cancel"},
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
    monkeypatch.setattr(
        private_pilot_service,
        "_partition_delivery_tracks",
        lambda items: (
            [
                {"id": "BUG-1", "title": "重复支付", "severity": "P0", "bug_status": "reproduced", "gate_passed": True},
                {"id": "BUG-2", "title": "非法取消", "severity": "P1", "bug_status": "reproduced", "gate_passed": True},
            ],
            [],
        ),
    )
    monkeypatch.setattr(
        private_pilot_service,
        "_load_real_project_discovery_payload",
        lambda root, project_id: {
            "continuous_discovery_campaign": {
                "campaign": {
                    "campaign_id": "CMP_ACTIVE",
                    "lineage_campaign_id": "CMP_BASE",
                    "confirmed_slice_count": 43,
                },
                "summary": {"campaign_state": "active"},
                "current_run": {},
            }
        },
    )

    bundle_dir = tmp_path / "platform_workspace" / "enterprise-project" / "evidence_bundles" / "evb_active"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "campaign.json").write_text(
        json.dumps({"campaign_id": "CMP_ACTIVE"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (bundle_dir / "findings.json").write_text(
        json.dumps(
            [
                {"risk_id": "BUG-1A", "title": "重复支付", "method": "POST", "path": "/api/payments/pay"},
                {"risk_id": "BUG-1B", "title": "重复支付", "method": "POST", "path": "/api/payments/pay"},
                {"risk_id": "BUG-2", "title": "非法取消", "method": "POST", "path": "/api/orders/{id}/cancel"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = handler._build_command_center("enterprise-project", tmp_path)
    summary = payload["data"]["continuous_discovery_campaign"]["summary"]
    current_run = payload["data"]["continuous_discovery_campaign"]["current_run"]

    assert summary["current_campaign_confirmed_slice_count"] == 43
    assert summary["current_campaign_customer_ready_defect_count"] == 0
    assert summary["current_campaign_bundle_finding_count_raw"] == 0
    assert summary["family_customer_ready_defect_count"] == 0
    assert summary["family_report_real_finding_count"] == 0
    assert summary["family_historical_carryover_defect_count"] == 0
    assert (
        summary["confirmed_shelf_alignment_status"]
        == "current_campaign_exceeds_family_shelf"
    )
    assert current_run["current_campaign_confirmed_slice_count"] == 43
    assert current_run["current_campaign_customer_ready_defect_count"] == 0
    assert current_run["current_campaign_bundle_finding_count_raw"] == 0


def test_static_snapshot_and_real_project_preserve_command_center_campaign_projection(monkeypatch, tmp_path) -> None:
    project = "enterprise-project"
    campaign_projection = {
        "campaign": {
            "campaign_id": "CMP_ACTIVE",
            "lineage_campaign_id": "CMP_LINEAGE",
            "scope_id": "checkout-scope",
            "environment_ref": "local-benchmark",
            "source_hash": "a" * 64,
            "source_snapshot_hash": "b" * 64,
            "confirmed_slice_count": 43,
        },
        "summary": {
            "confirmed_slice_count": 43,
            "current_campaign_confirmed_slice_count": 43,
            "current_campaign_customer_ready_defect_count": 25,
            "current_campaign_bundle_finding_count_raw": 43,
            "family_customer_ready_defect_count": 27,
            "family_report_real_finding_count": 27,
            "family_historical_carryover_defect_count": 2,
            "confirmed_shelf_alignment_status": "current_campaign_exceeds_family_shelf",
        },
        "current_run": {
            "confirmed_slice_count": 43,
            "current_campaign_confirmed_slice_count": 43,
            "current_campaign_customer_ready_defect_count": 25,
            "current_campaign_bundle_finding_count_raw": 43,
            "family_customer_ready_defect_count": 27,
            "family_report_real_finding_count": 27,
        },
    }

    monkeypatch.setattr(
        PrivatePilotHandler,
        "_build_command_center",
        lambda self, project_id, root: {
            "ok": True,
            "data": {
                "defects": [{"id": "BUG-1", "title": "重复支付"}],
                "clues": [],
                "value_metrics": {"ready_bug_count": 1},
                "executive_summary": {"ready_bugs": 1},
                "scan_meta": {"ready_bug_count": 1},
                "data_contract": {"display_key": "defects"},
                "current_campaign_scope": {
                    "campaign_id": "CMP_ACTIVE",
                    "lineage_campaign_id": "CMP_LINEAGE",
                    "scope_id": "checkout-scope",
                    "environment_ref": "local-benchmark",
                    "source_hash": "a" * 64,
                    "source_snapshot_hash": "b" * 64,
                },
                "continuous_discovery_campaign": campaign_projection,
            },
        },
    )

    scan_result_path = tmp_path / "platform_outputs" / project / "scan_result.json"
    scan_result_path.parent.mkdir(parents=True, exist_ok=True)
    scan_result_path.write_text(json.dumps({"project": project}, ensure_ascii=False), encoding="utf-8")
    real_project_path = tmp_path / "platform_outputs" / project / "real_project" / "real_project_defect_data.json"
    real_project_path.parent.mkdir(parents=True, exist_ok=True)
    real_project_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")

    snapshot = main_module._customer_ready_static_snapshot(project, tmp_path)
    assert snapshot["continuous_discovery_campaign"]["summary"]["current_campaign_confirmed_slice_count"] == 43
    assert snapshot["continuous_discovery_campaign"]["summary"]["current_campaign_customer_ready_defect_count"] == 25
    assert snapshot["continuous_discovery_campaign"]["summary"]["family_customer_ready_defect_count"] == 27
    assert snapshot["continuous_discovery_campaign"]["summary"]["family_historical_carryover_defect_count"] == 2
    assert snapshot["current_campaign_scope"]["campaign_id"] == "CMP_ACTIVE"
    assert snapshot["current_campaign_scope"]["scope_id"] == "checkout-scope"
    assert snapshot["current_campaign_scope"]["environment_ref"] == "local-benchmark"

    main_module._persist_customer_ready_static_artifacts(project, tmp_path, {"project": project})
    saved_scan = json.loads(scan_result_path.read_text(encoding="utf-8"))
    saved_real_project = json.loads(real_project_path.read_text(encoding="utf-8"))

    assert saved_scan["customer_ready_snapshot"]["continuous_discovery_campaign"]["summary"]["current_campaign_confirmed_slice_count"] == 43
    assert saved_scan["customer_ready_snapshot"]["continuous_discovery_campaign"]["summary"]["current_campaign_customer_ready_defect_count"] == 25
    assert saved_scan["customer_ready_snapshot"]["current_campaign_scope"]["lineage_campaign_id"] == "CMP_LINEAGE"
    assert saved_real_project["continuous_discovery_campaign"]["summary"]["current_campaign_confirmed_slice_count"] == 43
    assert saved_real_project["continuous_discovery_campaign"]["summary"]["current_campaign_customer_ready_defect_count"] == 25
    assert saved_real_project["continuous_discovery_campaign"]["summary"]["family_customer_ready_defect_count"] == 27
    assert saved_real_project["continuous_discovery_campaign"]["summary"]["family_historical_carryover_defect_count"] == 2
    assert saved_real_project["current_campaign_scope"]["source_hash"] == "a" * 64
    assert saved_real_project["current_campaign_scope"]["source_snapshot_hash"] == "b" * 64
