from __future__ import annotations

import json

from ai_test_asset_center.enterprise_source_registry import register_source_asset
from ai_test_asset_center.__main__ import scan
from tests.mainline_test_support import authoritative_v12_double

API_SPEC = """
openapi: 3.0.0
info:
  title: Demo API
  version: '1.0'
servers:
  - url: http://example.test
paths:
  /api/orders/{orderId}/cancel:
    post:
      parameters:
        - in: path
          name: orderId
          required: true
          schema:
            type: string
      responses:
        '200':
          description: cancelled
"""


def test_scan_materializes_commercial_assets_for_customer_ready_validated_findings(monkeypatch, tmp_path):
    manifest = register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_release_gate(**kwargs):
        return {"verdict": "pass", "status": "ready"}

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        return {
            "runtime_contract": {"status": "approved", "approved_base_url": base_url},
            "phases": {
                "execution": {"status": "completed", "executed": 1},
                "state_graph": {"coverage_gaps": []},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": {
                "campaign_id": "camp_commercial_1",
                "scope_id": "service-a",
                "environment_ref": "test-a",
                "source_hash": manifest["source_hash"],
            },
            "findings": [
                {
                    "candidate_id": "candidate-BUG-COM-1",
                    "slice_id": "slice-BUG-COM-1",
                    "obligation_id": "obligation-BUG-COM-1",
                    "experiment_id": "experiment-BUG-COM-1",
                    "execution_id": "execution-BUG-COM-1",
                    "evidence_id": "evidence-BUG-COM-1",
                    "finding_id": "BUG-COM-1",
                    "title": "cancel endpoint violates paid-order invariant",
                    "risk_type": "business_invariant",
                    "severity": "P1",
                    "method": "POST",
                    "path": "/api/orders/ord_1/cancel",
                    "bug_status": "reproduced",
                    "execution_status": "executed",
                    "confirmation_status": "confirmed",
                    "customer_delivery_status": "defect",
                    "gate_passed": True,
                    "actor": "qa_lead",
                    "semantic_verdict": "SEMANTIC_CONFIRMED",
                    "business_evidence_status": "VALIDATED",
                    "timestamp": "2026-07-07T12:00:00Z",
                    "failed_assertions": ["支付后订单不可取消"],
                    "reproduction": {
                        "method": "POST",
                        "path": "/api/orders/ord_1/cancel",
                        "is_synthetic": False,
                        "har_evidence": {"status_code": 200, "response_body": {"status": "cancelled"}},
                    },
                    "reproduction_steps": ["POST /api/orders/ord_1/cancel", "observe paid order became cancelled"],
                    "evidence_quality": {
                        "level": "validated",
                        "score": 96,
                        "missing": [],
                        "next_actions": [],
                        "can_reproduce": True,
                    },
                    "evidence_status": {
                        "semantic_verdict": "SEMANTIC_CONFIRMED",
                        "business_evidence_status": "VALIDATED",
                        "final_review_status": "VALIDATED_CANDIDATE",
                        "missing_requirements": [],
                    },
                    "evidence": {
                        "request": "POST /api/orders/ord_1/cancel",
                        "response": "HTTP 200",
                        "assertion": "支付后订单不可取消",
                        "timestamp": "2026-07-07T12:00:00Z",
                        "target": "/api/orders/ord_1/cancel",
                        "actor": "qa_lead",
                    },
                    "raw_evidence": {
                        "has_real_evidence": True,
                        "timestamp": "2026-07-07T12:00:00Z",
                        "request_raw": {"method": "POST", "path": "/api/orders/ord_1/cancel", "actor": "qa_lead", "body": {"actor": "buyer"}},
                        "response_raw": {"status_code": 200, "body": {"status": "cancelled"}},
                        "sandbox_write": {
                            "cleanup": {
                                "status": "completed",
                                "receipt_ref": "audit://cleanup/BUG-COM-1",
                            }
                        },
                    },
                    "evidence_package": {
                        "status": "packaged",
                        "runtime": {"http_status": 200},
                    },
                    "source_refs": [{"type": "prd", "ref": "支付后订单不可取消"}],
                }
            ],
            "external_findings": [],
            "auto_har": {"status": "captured"},
            "total_duration_ms": 1,
        }

    monkeypatch.setattr("ai_test_asset_center.scan_diagnostics.run_preflight", fake_run_preflight)
    monkeypatch.setattr("ai_test_asset_center.__main__._evaluate_release_gate", fake_release_gate)
    monkeypatch.setattr(
        "ai_test_asset_center.v12_pipeline.run_v12_pipeline",
        authoritative_v12_double(fake_v12_pipeline),
    )

    result = scan(
        project="enterprise-project",
        root=tmp_path,
        api_doc_text=API_SPEC,
        base_url="http://127.0.0.1:8080",
        campaign_context={"scope_id": "service-a", "environment_ref": "test-a", "source_manifest": manifest},
    )

    assets = result["commercial_assets"]
    assert assets["status"] == "materialized"
    assert assets["finding_count"] == 1
    assert assets["commercial_handoff_status"] == "commercial_handoff_ready_with_validated_findings"
    assert assets["commercial_handoff_acceptance_status"] == "ready_for_customer_acceptance"
    assert assets["external_tracker_sync_payload_status"] == "external_tracker_sync_payloads_blocked_or_empty"
    assert assets["external_tracker_sync_payload_gate_status"] == "external_tracker_sync_payload_gate_hold_only"
    assert assets["delivery_package"]["status"] == "created"

    output_root = tmp_path / "platform_outputs" / "enterprise-project" / "defect_discovery"
    assert (output_root / "commercial_commercial_handoff_bundle.json").exists()
    assert (output_root / "commercial_commercial_handoff_acceptance_gate.json").exists()
    assert (output_root / "commercial_handoff_archive_manifest.json").exists()
    assert (output_root / "commercial_tracker_sync_payloads.json").exists()
    assert (output_root / "commercial_immutable_run_receipt.json").exists()
    assert (output_root / "commercial_runtime_customer_reproduction_pack.json").exists()

    payloads = json.loads((output_root / "commercial_tracker_sync_payloads.json").read_text(encoding="utf-8"))
    assert payloads["status"] == "external_tracker_sync_payloads_blocked_or_empty"
    assert payloads["jira_transition_payloads"] == []
    assert payloads["linear_update_payloads"] == []
    assert payloads["csv_status_updates"] == []

    package_ref = assets["delivery_package"]["package_ref"]
    assert (tmp_path / package_ref).exists()
