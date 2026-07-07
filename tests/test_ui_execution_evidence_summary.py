from __future__ import annotations

import json

from ai_test_asset_center.__main__ import scan
from ai_test_asset_center.enterprise_source_registry import register_source_asset


API_SPEC = json.dumps(
    {
        "openapi": "3.0.0",
        "paths": {"/api/cases/{case_id}/approve": {"patch": {"operationId": "approveCase"}}},
    }
)


def test_scan_emits_ui_execution_evidence_summary(monkeypatch, tmp_path):
    manifest = register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_persist_execution_evidence(*args, **kwargs):
        return {
            "status": "persisted",
            "bundle_id": "evb_ui_summary",
            "manifest_ref": "platform_outputs/enterprise-project/evidence/manifest.json",
        }

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        return {
            "runtime_contract": {"status": "approved", "approved_base_url": base_url},
            "phases": {
                "execution": {"status": "completed", "executed": 1},
                "state_graph": {"coverage_gaps": []},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": {
                "campaign_id": "camp_ui_summary",
                "scope_id": "service-a",
                "environment_ref": "test-a",
                "source_hash": manifest["source_hash"],
            },
            "findings": [],
            "external_findings": [],
            "ui_findings": [],
            "ui_execution": {
                "status": "partial",
                "requested": 2,
                "executed": 1,
                "failed": 0,
                "blocked": 1,
                "provider_distribution": {"page_agent": 2},
                "artifacts": [
                    {
                        "request_id": "ui_req_1",
                        "provider": "page_agent",
                        "artifact_type": "screenshot",
                        "ref": "platform_workspace/enterprise-project/page_agent_runs/scan/ui_req_1/final.png",
                    }
                ],
                "results": [
                    {
                        "request_id": "ui_req_1",
                        "provider": "page_agent",
                        "bridge_provider": "page_agent_browser_plan",
                        "status": "executed",
                        "current_url": "http://127.0.0.1:8080/orders",
                        "created_data": {"entity": "order", "id": "ord_1"},
                        "artifacts": [{"ref": "platform_workspace/enterprise-project/page_agent_runs/scan/ui_req_1/final.png"}],
                    },
                    {
                        "request_id": "ui_req_2",
                        "provider": "page_agent",
                        "bridge_provider": "page_agent_browser_plan",
                        "status": "blocked",
                    },
                ],
                "duration_ms": 12,
            },
            "auto_har": {"status": "no_traffic"},
            "total_duration_ms": 1,
        }

    monkeypatch.setattr("ai_test_asset_center.scan_diagnostics.run_preflight", fake_run_preflight)
    monkeypatch.setattr("ai_test_asset_center.__main__._evaluate_release_gate", fake_release_gate)
    monkeypatch.setattr("ai_test_asset_center.__main__._persist_execution_evidence", fake_persist_execution_evidence)
    monkeypatch.setattr("ai_test_asset_center.v12_pipeline.run_v12_pipeline", fake_v12_pipeline)

    result = scan(
        project="enterprise-project",
        root=tmp_path,
        api_doc_text=API_SPEC,
        base_url="http://127.0.0.1:8080",
        campaign_context={"scope_id": "service-a", "environment_ref": "test-a", "source_manifest": manifest},
    )

    assert result["ui_execution_summary"]["status"] == "partial"
    assert result["ui_execution_summary"]["requested"] == 2
    assert result["ui_execution_summary"]["executed"] == 1
    assert result["ui_execution_summary"]["blocked"] == 1
    assert result["ui_execution_summary"]["artifact_count"] == 1
    assert result["ui_execution_summary"]["created_data_count"] == 1
    assert result["ui_execution_summary"]["evidence_captured_count"] == 1
    assert result["execution_evidence_summary"]["current_url_samples"] == ["http://127.0.0.1:8080/orders"]
    assert result["layers"]["ui_execution"]["evidence_captured_count"] == 1
