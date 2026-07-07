from __future__ import annotations

from ai_test_asset_center.private_pilot_service import (
    PrivatePilotHandler,
    _normalize_command_center_envelope,
)
import ai_test_asset_center.private_pilot_service as private_pilot_service


def _ready_item() -> dict:
    return {
        "id": "BUG-1",
        "risk_id": "BUG-1",
        "title": "重复支付",
        "severity": "P1",
        "bug_status": "reproduced",
        "gate_passed": True,
        "status": "confirmed",
        "confidence_score": 0.95,
    }


def test_command_center_envelope_preserves_nested_commercial_assets_on_reread() -> None:
    payload = _normalize_command_center_envelope({
        "ok": True,
        "data": {
            "risks": [_ready_item()],
            "commercial_assets": {
                "status": "materialized",
                "finding_count": 1,
                "customer_ready_reproduction_count": 1,
                "commercial_handoff": {
                    "status": "commercial_handoff_ready_with_validated_findings",
                    "acceptance_status": "ready_for_customer_acceptance",
                    "safe_for_customer": True,
                },
                "tracker_sync": {
                    "payload_status": "external_tracker_sync_payloads_blocked_or_empty",
                    "payload_gate_status": "external_tracker_sync_payload_gate_hold_only",
                },
                "delivery_package": {
                    "status": "created",
                    "package_id": "delivery_nested_bundle",
                    "package_ref": "platform_outputs/demo/delivery_packages/delivery_nested_bundle.zip",
                    "release_verdict": "not_ready",
                    "evidence_bundle_id": "evb_nested",
                },
                "artifact_refs": {
                    "commercial_handoff_bundle_ref": "platform_outputs/demo/defect_discovery/external_commercial_handoff_bundle.json",
                },
            },
        },
    })
    data = payload["data"]

    assert data["commercial_assets"]["commercial_handoff"]["status"] == "commercial_handoff_ready_with_validated_findings"
    assert data["commercial_assets"]["commercial_handoff"]["acceptance_status"] == "ready_for_customer_acceptance"
    assert data["commercial_assets"]["tracker_sync"]["payload_status"] == "external_tracker_sync_payloads_blocked_or_empty"
    assert data["commercial_assets"]["tracker_sync"]["payload_gate_status"] == "external_tracker_sync_payload_gate_hold_only"
    assert data["commercial_assets"]["delivery_package"]["status"] == "created"
    assert data["scan_meta"]["commercial_handoff_status"] == "commercial_handoff_ready_with_validated_findings"
    assert data["scan_meta"]["external_tracker_sync_payload_status"] == "external_tracker_sync_payloads_blocked_or_empty"


def test_build_command_center_prefers_materialized_commercial_assets_over_empty_external_payload(monkeypatch, tmp_path) -> None:
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    handler.headers = {}

    monkeypatch.setattr(handler, "_load_v12_report", lambda project_id, root: {
        "project_name": project_id,
        "generated_at_utc": "2026-07-07T18:20:00Z",
        "external_commercial_assets": {
            "status": "empty",
            "finding_count": 0,
        },
        "commercial_assets": {
            "status": "materialized",
            "finding_count": 2,
            "customer_ready_reproduction_count": 2,
            "commercial_handoff": {
                "status": "commercial_handoff_ready_with_validated_findings",
                "acceptance_status": "ready_for_customer_acceptance",
                "safe_for_customer": True,
            },
            "tracker_sync": {
                "payload_status": "external_tracker_sync_payloads_blocked_or_empty",
                "payload_gate_status": "external_tracker_sync_payload_gate_hold_only",
            },
            "delivery_package": {
                "status": "created",
                "package_id": "delivery_materialized_bundle",
                "package_ref": "platform_outputs/demo/delivery_packages/delivery_materialized_bundle.zip",
                "release_verdict": "not_ready",
                "evidence_bundle_id": "evb_materialized",
            },
        },
        "real_findings": [_ready_item()],
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

    assert data["commercial_assets"]["status"] == "materialized"
    assert data["commercial_assets"]["commercial_handoff"]["status"] == "commercial_handoff_ready_with_validated_findings"
    assert data["commercial_assets"]["delivery_package"]["status"] == "created"
    assert data["value_metrics"]["commercial_asset_materialized"] == 1
    assert data["value_metrics"]["commercial_delivery_package_created"] == 1
