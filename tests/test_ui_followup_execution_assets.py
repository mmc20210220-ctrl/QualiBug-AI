from __future__ import annotations

import json

import ai_test_asset_center.__main__ as main_module
from ai_test_asset_center.__main__ import scan
from ai_test_asset_center.enterprise_source_registry import register_source_asset
from tests.mainline_test_support import authoritative_v12_double


API_SPEC = json.dumps(
    {
        "openapi": "3.0.0",
        "paths": {"/api/cases/{case_id}/approve": {"patch": {"operationId": "approveCase"}}},
    }
)


def test_scan_preserves_existing_ui_followup_execution_asset_when_current_run_has_no_ui_candidates(monkeypatch, tmp_path):
    manifest = register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    workspace_dir = tmp_path / "platform_workspace" / "enterprise-project" / "defect_discovery"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    existing_execution_requests = {
        "version": "ui_followup_execution_requests_v1",
        "project_id": "enterprise-project",
        "scan_id": "scan_old",
        "campaign_id": "camp_old",
        "generated_at_utc": "2026-07-07T10:00:00Z",
        "items": [
            {
                "request_template_id": "UIFOLLOW_old",
                "title": "历史 UI 候选复现任务",
                "severity": "P1",
                "risk_type": "ui_execution",
                "method": "GET",
                "path": "/orders/123",
                "generated_at_utc": "2026-07-07T10:00:00Z",
            }
        ],
    }
    (workspace_dir / "ui_followup_execution_requests.json").write_text(
        json.dumps(existing_execution_requests, ensure_ascii=False),
        encoding="utf-8",
    )

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_persist_execution_evidence(*args, **kwargs):
        return {
            "status": "persisted",
            "bundle_id": "evb_ui_preserve",
            "manifest_ref": "platform_outputs/enterprise-project/evidence/manifest.json",
        }

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        return {
            "runtime_contract": {"status": "approved", "approved_base_url": base_url},
            "phases": {
                "execution": {"status": "completed", "executed": 0},
                "state_graph": {"coverage_gaps": []},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": {
                "campaign_id": "camp_ui_preserve",
                "scope_id": "service-a",
                "environment_ref": "test-a",
                "source_hash": manifest["source_hash"],
            },
            "findings": [],
            "external_findings": [],
            "ui_findings": [],
            "ui_execution": {"status": "not_requested", "artifacts": [], "duration_ms": 0},
            "auto_har": {"status": "no_traffic"},
            "total_duration_ms": 1,
        }

    monkeypatch.setattr("ai_test_asset_center.scan_diagnostics.run_preflight", fake_run_preflight)
    monkeypatch.setattr("ai_test_asset_center.__main__._evaluate_release_gate", fake_release_gate)
    monkeypatch.setattr("ai_test_asset_center.__main__._persist_execution_evidence", fake_persist_execution_evidence)
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

    payload = json.loads((workspace_dir / "ui_followup_execution_requests.json").read_text(encoding="utf-8"))

    assert result["ui_followup_assets"]["status"] == "preserved"
    assert result["ui_followup_assets"]["execution_request_count"] == 1
    assert payload["items"][0]["title"] == "历史 UI 候选复现任务"


def test_materialize_ui_followup_assets_creates_followup_execution_requests(tmp_path):
    assets = main_module._materialize_ui_followup_assets(
        project="enterprise-project",
        root=tmp_path,
        scan_id="scan_ui_followup",
        campaign={"campaign_id": "camp_ui_followup"},
        items=[
            {
                "title": "订单详情页状态异常",
                "severity": "P1",
                "risk_type": "ui_execution",
                "candidate_tier": "ui_candidate",
                "verification_badge": "ui_candidate",
                "confidence_score": 0.71,
                "path": "/orders/123",
                "reproduction_steps": ["打开订单详情页", "观察状态标签与按钮是否一致"],
                "evidence": {
                    "target": "http://127.0.0.1:8080/orders/123",
                    "request": {"method": "GET"},
                },
                "raw_evidence": {
                    "created_data": {
                        "object_type": "order",
                        "object_id": "ord_123",
                    },
                    "ui_execution_result": {
                        "current_url": "http://127.0.0.1:8080/orders/123",
                    }
                },
            }
        ],
    )

    payload = json.loads(
        (
            tmp_path
            / "platform_workspace"
            / "enterprise-project"
            / "defect_discovery"
            / "ui_followup_execution_requests.json"
        ).read_text(encoding="utf-8")
    )

    assert assets["status"] == "materialized"
    assert assets["execution_request_count"] == 1
    assert payload["items"][0]["request_template_id"].startswith("UIFOLLOW_")
    assert payload["items"][0]["browser_plan"]["steps"][0]["url"] == "/orders/123"
    assert payload["items"][0]["metadata"]["bridge_mode"] == "page_agent_browser_plan"
    assert payload["items"][0]["metadata"]["followup_kind"] == "reproduction_assistant"
    assert payload["items"][0]["metadata"]["verification"]["kind"] == "http_get"
    assert payload["items"][0]["metadata"]["verification"]["path"] == "/orders/123"
    assert payload["items"][0]["metadata"]["verification"]["body_contains"] == "{object_id}"


def test_materialize_ui_followup_assets_creates_source_bound_slice_requests(tmp_path):
    assets = main_module._materialize_ui_followup_assets(
        project="enterprise-project",
        root=tmp_path,
        scan_id="scan_slice_followup",
        campaign={"campaign_id": "camp_slice_followup"},
        items=[],
        selected_slices=[
            {
                "slice_id": "BHV_ORDER_CANCEL",
                "entity": "order",
                "kind": "invariant",
                "endpoints": ["/api/orders/{id}/cancel"],
                "source_refs": [{"source_type": "requirement", "quote": "已支付订单不能直接取消"}],
            }
        ],
        plan_only_scenarios=[
            {
                "id": "SCN_ORDER_CANCEL",
                "title": "[来源约束不变量] order: PAID -> /api/orders/{id}/cancel",
                "category": "invariant",
                "severity": "P1",
                "expected_state": "PAID",
                "behavior_slice_id": "BHV_ORDER_CANCEL",
                "steps": [
                    {
                        "method": "GET",
                        "path": "/api/orders/{id}",
                    }
                ],
            }
        ],
    )

    payload = json.loads(
        (
            tmp_path
            / "platform_workspace"
            / "enterprise-project"
            / "defect_discovery"
            / "ui_followup_execution_requests.json"
        ).read_text(encoding="utf-8")
    )

    assert assets["execution_request_count"] == 1
    assert payload["items"][0]["request_template_id"] == "UISLICE_BHV_ORDER_CANCEL"
    assert payload["items"][0]["path"] == "/"
    assert "source endpoint: /api/orders/{id}/cancel" in payload["items"][0]["page_hints"]
    assert payload["items"][0]["metadata"]["followup_kind"] == "reproduction_assistant"
    assert payload["items"][0]["metadata"]["verification"]["kind"] == "http_get"
    assert payload["items"][0]["metadata"]["verification"]["path"] == "/api/orders/{object_id}"
    assert payload["items"][0]["metadata"]["verification"]["body_contains"] == "PAID"


def test_materialize_ui_followup_assets_creates_ui_test_data_backfill_asset(tmp_path):
    assets = main_module._materialize_ui_followup_assets(
        project="enterprise-project",
        root=tmp_path,
        scan_id="scan_test_data_followup",
        campaign={"campaign_id": "camp_test_data_followup"},
        items=[],
        selected_slices=[
            {
                "slice_id": "BHV_ORDER_CREATE",
                "entity": "order",
                "kind": "transition",
                "endpoints": ["/api/orders"],
            }
        ],
        plan_only_scenarios=[
            {
                "id": "SCN_ORDER_CREATE",
                "title": "[来源约束状态流转] order: create",
                "category": "state_machine",
                "severity": "P1",
                "behavior_slice_id": "BHV_ORDER_CREATE",
                "evidence_gaps": ["FIXTURE_CONTRACT_MISSING", "CLEANUP_CONTRACT_MISSING"],
                "steps": [
                    {
                        "method": "POST",
                        "path": "/api/orders",
                        "body": {"customerId": "cust_001", "orderType": "standard", "amount": 99},
                    }
                ],
            }
        ],
    )

    payload = json.loads(
        (
            tmp_path
            / "platform_workspace"
            / "enterprise-project"
            / "defect_discovery"
            / "ui_followup_test_data_requests.json"
        ).read_text(encoding="utf-8")
    )

    assert assets["test_data_request_count"] == 1
    assert payload["items"][0]["request_template_id"] == "UITESTDATA_BHV_ORDER_CREATE"
    assert payload["items"][0]["execution_mode"] == "approved_sandbox_write"
    assert payload["items"][0]["metadata"]["followup_kind"] == "ui_test_data_backfill"
    assert payload["items"][0]["metadata"]["executable"] is False
    assert payload["items"][0]["review_contract"]["status"] == "needs_selector_confirmation"
    assert payload["items"][0]["review_contract"]["promotion_target"] == "ui_test_data_requests"
    assert payload["items"][0]["promotion"]["status"] == "draft"
    assert payload["items"][0]["promotion"]["approved_browser_plan"] == {}
    assert payload["items"][0]["browser_plan"] == {}
    assert payload["items"][0]["browser_plan_draft"]["execution_mode"] == "approved_sandbox_write"
    assert payload["items"][0]["browser_plan_draft"]["draft_actions"][0]["source_api_method"] == "POST"
    assert payload["items"][0]["browser_plan_draft"]["draft_actions"][0]["source_api_path"] == "/api/orders"
    assert payload["items"][0]["browser_plan_draft"]["draft_actions"][0]["field_bindings"][0]["field"] == "customerId"
    assert payload["items"][0]["browser_plan_draft"]["draft_actions"][0]["field_bindings"][0]["selector_candidates"][0] == '[name="customerId"]'
    assert payload["items"][0]["browser_plan_draft"]["draft_actions"][0]["field_bindings"][1]["ui_action_candidates"][0]["action"] == "select_option"
    assert payload["items"][0]["browser_plan_draft"]["field_bindings_draft"][2]["ui_action_candidates"][0]["action"] == "fill"
    assert "FORM_FIELD_MAPPING_MISSING" in payload["items"][0]["browser_plan_draft"]["missing_requirements"]


def test_scan_materializes_source_bound_slice_followup_requests_without_ui_candidates(monkeypatch, tmp_path):
    manifest = register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_persist_execution_evidence(*args, **kwargs):
        return {
            "status": "persisted",
            "bundle_id": "evb_ui_slice",
            "manifest_ref": "platform_outputs/enterprise-project/evidence/manifest.json",
        }

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        return {
            "runtime_contract": {"status": "approved", "approved_base_url": base_url},
            "phases": {
                "execution": {"status": "skipped", "executed": 0},
                "state_graph": {"coverage_gaps": []},
                "incremental_discovery": {
                    "selected_slices": [
                        {
                            "slice_id": "BHV_ORDER_CANCEL",
                            "entity": "order",
                            "kind": "invariant",
                            "endpoints": ["/api/orders/{id}/cancel"],
                            "source_refs": [{"source_type": "requirement", "quote": "已支付订单不能直接取消"}],
                        }
                    ]
                },
            },
            "plan_only_scenarios": [
                {
                    "id": "SCN_ORDER_CANCEL",
                    "title": "[来源约束不变量] order: PAID -> /api/orders/{id}/cancel",
                    "category": "invariant",
                    "severity": "P1",
                    "expected_state": "PAID",
                    "behavior_slice_id": "BHV_ORDER_CANCEL",
                }
            ],
            "campaign": {
                "campaign_id": "camp_ui_slice",
                "scope_id": "service-a",
                "environment_ref": "test-a",
                "source_hash": manifest["source_hash"],
            },
            "findings": [],
            "external_findings": [],
            "ui_findings": [],
            "ui_execution": {"status": "not_requested", "artifacts": [], "duration_ms": 0},
            "auto_har": {"status": "no_traffic"},
            "total_duration_ms": 1,
        }

    monkeypatch.setattr("ai_test_asset_center.scan_diagnostics.run_preflight", fake_run_preflight)
    monkeypatch.setattr("ai_test_asset_center.__main__._evaluate_release_gate", fake_release_gate)
    monkeypatch.setattr("ai_test_asset_center.__main__._persist_execution_evidence", fake_persist_execution_evidence)
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

    payload = json.loads(
        (
            tmp_path
            / "platform_workspace"
            / "enterprise-project"
            / "defect_discovery"
            / "ui_followup_execution_requests.json"
        ).read_text(encoding="utf-8")
    )

    assert result["ui_followup_assets"]["status"] == "materialized"
    assert result["ui_followup_assets"]["execution_request_count"] == 1
    assert payload["items"][0]["request_template_id"] == "UISLICE_BHV_ORDER_CANCEL"
    assert payload["items"][0]["severity"] == "P1"
