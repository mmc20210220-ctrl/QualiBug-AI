from __future__ import annotations

import json

from ai_test_asset_center.__main__ import scan
import ai_test_asset_center.__main__ as main_module
from ai_test_asset_center.enterprise_source_registry import register_source_asset


API_SPEC = json.dumps(
    {
        "openapi": "3.0.0",
        "paths": {
            "/api/orders/{id}/cancel": {
                "post": {"operationId": "cancelOrder"}
            }
        },
    }
)


def test_scan_updates_shared_scan_counter_before_customer_ready_snapshot(monkeypatch, tmp_path) -> None:
    manifest = register_source_asset(
        "enterprise-project",
        "api-contract",
        API_SPEC,
        source_type="openapi",
        root=tmp_path,
    )

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_persist(*args, **kwargs):
        return {"status": "persisted", "bundle_id": "bundle_cli_counter"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        return {
            "runtime_contract": {"status": "approved", "approved_base_url": base_url},
            "phases": {
                "execution": {"status": "completed", "executed": 0},
                "state_graph": {"coverage_gaps": []},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": {
                "campaign_id": "CMP_SCAN_COUNTER",
                "scope_id": "service-a",
                "environment_ref": "test-a",
                "source_hash": manifest["source_hash"],
            },
            "findings": [],
            "external_findings": [],
            "auto_har": {"status": "captured"},
            "total_duration_ms": 1,
        }

    def fake_customer_ready_snapshot(project, root):
        counter_path = root / "platform_outputs" / project / "scan_counter.json"
        counter = json.loads(counter_path.read_text(encoding="utf-8"))
        return {
            "project": project,
            "generated_at_utc": counter["last_scan_at"],
            "defects": [],
            "clues": [],
            "risks": [],
            "value_metrics": {},
            "executive_summary": {},
            "scan_meta": {
                "run_count": counter["count"],
                "first_scan_at": counter["first_scan_at"],
                "last_scan_at": counter["last_scan_at"],
            },
            "data_contract": {},
        }

    monkeypatch.setattr("ai_test_asset_center.scan_diagnostics.run_preflight", fake_run_preflight)
    monkeypatch.setattr("ai_test_asset_center.__main__._persist_execution_evidence", fake_persist)
    monkeypatch.setattr("ai_test_asset_center.__main__._evaluate_release_gate", fake_release_gate)
    monkeypatch.setattr("ai_test_asset_center.v12_pipeline.run_v12_pipeline", fake_v12_pipeline)
    monkeypatch.setattr(main_module, "_customer_ready_static_snapshot", fake_customer_ready_snapshot)

    first = scan(
        project="enterprise-project",
        root=tmp_path,
        api_doc_text=API_SPEC,
        campaign_context={"scope_id": "service-a", "environment_ref": "test-a", "source_manifest": manifest},
    )
    second = scan(
        project="enterprise-project",
        root=tmp_path,
        api_doc_text=API_SPEC,
        campaign_context={"scope_id": "service-a", "environment_ref": "test-a", "source_manifest": manifest},
    )

    counter_path = tmp_path / "platform_outputs" / "enterprise-project" / "scan_counter.json"
    saved_scan_path = tmp_path / "platform_outputs" / "enterprise-project" / "scan_result.json"
    counter = json.loads(counter_path.read_text(encoding="utf-8"))
    saved_scan = json.loads(saved_scan_path.read_text(encoding="utf-8"))

    assert counter["count"] == 2
    assert counter["first_scan_at"]
    assert counter["last_scan_at"]
    assert first["customer_ready_snapshot"]["scan_meta"]["run_count"] == 1
    assert second["customer_ready_snapshot"]["scan_meta"]["run_count"] == 2
    assert saved_scan["customer_ready_snapshot"]["scan_meta"]["run_count"] == 2
    assert saved_scan["customer_ready_snapshot"]["scan_meta"]["last_scan_at"] == counter["last_scan_at"]
