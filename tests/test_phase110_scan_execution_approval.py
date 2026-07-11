from __future__ import annotations

import json
import os

from ai_test_asset_center.__main__ import scan
from ai_test_asset_center.enterprise_source_registry import register_source_asset


API_SPEC = json.dumps({
    "openapi": "3.0.0",
    "paths": {"/api/cases/{case_id}/approve": {"patch": {"operationId": "approveCase"}}},
    "components": {"schemas": {"Case": {"type": "object", "properties": {"state": {"type": "string", "enum": ["DRAFT", "APPROVED"]}}}}},
})


def test_scan_loads_registered_manifest_without_reuploading_source_text(tmp_path):
    manifest = register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    result = scan(
        project="enterprise-project",
        root=tmp_path,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": manifest,
        },
    )

    assert result["grade"] == "inconclusive"
    assert result["runtime_contract"]["source_manifest"]["source_id"] == "api-contract"
    assert result["runtime_contract"]["source_manifest"]["source_version_id"] == manifest["source_version_id"]


def test_scan_authorizes_nonproduction_without_per_probe_approval_but_requires_runtime_receipts(tmp_path):
    manifest = register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    result = scan(
        project="enterprise-project",
        root=tmp_path,
        base_url="https://example.invalid",
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "environment_type": "test",
            "execution_mode": "approved_sandbox_write",
            "source_manifest": manifest,
        },
    )

    assert result["grade"] == "blocked"
    assert result["runtime_contract"]["status"] == "approved"
    assert result["runtime_contract"]["execution_approval"]["authorization_basis"] == "source_bound_nonproduction_campaign"
    assert result["execution_status"] == "blocked"
    assert result["auto_har"]["status"] == "no_traffic"
    assert result["release_gate"]["verdict"] == "not_ready"


def test_scan_auto_issues_local_execution_approval_for_loopback_runtime(tmp_path, monkeypatch):
    manifest = register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    calls: list[dict[str, object]] = []

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_persist(*args, **kwargs):
        return {"status": "persisted", "bundle_id": "bundle_1"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        context = dict(campaign_context or {})
        calls.append({"base_url": base_url, "campaign_context": context})
        campaign = {
            "campaign_id": "camp_local_1",
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_hash": manifest["source_hash"],
        }
        if not str(context.get("execution_approval_id") or "").strip():
            return {
                "runtime_contract": {
                    "status": "blocked",
                    "reason": "execution_approval_required",
                    "missing_requirements": ["EXECUTION_APPROVAL_MISSING"],
                    "execution_approval": {"code": "EXECUTION_APPROVAL_MISSING"},
                },
                "phases": {
                    "execution": {"status": "blocked", "missing_requirements": ["EXECUTION_APPROVAL_MISSING"]},
                    "state_graph": {},
                    "incremental_discovery": {"selected_slices": []},
                },
                "campaign": campaign,
                "findings": [],
                "auto_har": {"status": "no_traffic"},
            }
        return {
            "runtime_contract": {
                "status": "approved",
                "reason": "",
                "execution_approval": {"approval_id": str(context.get("execution_approval_id") or "")},
            },
            "phases": {
                "execution": {"status": "completed", "executed": 1},
                "state_graph": {},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": campaign,
            "findings": [],
            "auto_har": {"status": "captured"},
            "total_duration_ms": 1,
        }

    monkeypatch.setenv("QUALIBUG_LOCAL_DEV_ACTOR", "1")
    monkeypatch.delenv("QUALIBUG_ALLOW_PUBLIC_BIND", raising=False)
    monkeypatch.setattr("ai_test_asset_center.scan_diagnostics.run_preflight", fake_run_preflight)
    monkeypatch.setattr("ai_test_asset_center.__main__._persist_execution_evidence", fake_persist)
    monkeypatch.setattr("ai_test_asset_center.__main__._evaluate_release_gate", fake_release_gate)
    monkeypatch.setattr("ai_test_asset_center.v12_pipeline.run_v12_pipeline", fake_v12_pipeline)

    result = scan(
        project="enterprise-project",
        root=tmp_path,
        base_url="http://127.0.0.1:8080",
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "environment_type": "test",
            "source_manifest": manifest,
            "execution_mode": "approved_sandbox_write",
        },
    )

    assert result["grade"] == "inconclusive"
    assert result["execution_status"] == "completed"
    assert len(calls) == 2
    assert not calls[0]["campaign_context"].get("execution_approval_id")
    second_approval_id = str(calls[1]["campaign_context"].get("execution_approval_id") or "")
    assert second_approval_id.startswith("eap_")
    approvals_path = tmp_path / "platform_workspace" / "enterprise-project" / "execution_approvals" / "approvals.json"
    assert approvals_path.exists()


def test_scan_refreshes_stale_local_execution_approval_when_campaign_mismatch(tmp_path, monkeypatch):
    manifest = register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    calls: list[dict[str, object]] = []

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_persist(*args, **kwargs):
        return {"status": "persisted", "bundle_id": "bundle_1"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        context = dict(campaign_context or {})
        calls.append({"base_url": base_url, "campaign_context": context})
        campaign = {
            "campaign_id": "camp_local_2",
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_hash": manifest["source_hash"],
        }
        approval_id = str(context.get("execution_approval_id") or "").strip()
        if not approval_id:
            return {
                "runtime_contract": {
                    "status": "blocked",
                    "reason": "execution_approval_required",
                    "missing_requirements": ["EXECUTION_APPROVAL_MISSING"],
                    "execution_approval": {"code": "EXECUTION_APPROVAL_MISSING"},
                },
                "phases": {
                    "execution": {"status": "blocked", "missing_requirements": ["EXECUTION_APPROVAL_MISSING"]},
                    "state_graph": {},
                    "incremental_discovery": {"selected_slices": []},
                },
                "campaign": campaign,
                "findings": [],
                "auto_har": {"status": "no_traffic"},
            }
        if approval_id == "eap_stale":
            return {
                "runtime_contract": {
                    "status": "blocked",
                    "reason": "execution_approval_required",
                    "missing_requirements": ["EXECUTION_APPROVAL_CAMPAIGN_ID_MISMATCH"],
                    "execution_approval": {"code": "EXECUTION_APPROVAL_CAMPAIGN_ID_MISMATCH"},
                },
                "phases": {
                    "execution": {"status": "blocked", "missing_requirements": ["EXECUTION_APPROVAL_CAMPAIGN_ID_MISMATCH"]},
                    "state_graph": {},
                    "incremental_discovery": {"selected_slices": []},
                },
                "campaign": campaign,
                "findings": [],
                "auto_har": {"status": "no_traffic"},
            }
        return {
            "runtime_contract": {
                "status": "approved",
                "reason": "",
                "execution_approval": {"approval_id": approval_id},
            },
            "phases": {
                "execution": {"status": "completed", "executed": 1},
                "state_graph": {},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": campaign,
            "findings": [],
            "auto_har": {"status": "captured"},
            "total_duration_ms": 1,
        }

    monkeypatch.setenv("QUALIBUG_LOCAL_DEV_ACTOR", "1")
    monkeypatch.delenv("QUALIBUG_ALLOW_PUBLIC_BIND", raising=False)
    monkeypatch.setattr("ai_test_asset_center.scan_diagnostics.run_preflight", fake_run_preflight)
    monkeypatch.setattr("ai_test_asset_center.__main__._persist_execution_evidence", fake_persist)
    monkeypatch.setattr("ai_test_asset_center.__main__._evaluate_release_gate", fake_release_gate)
    monkeypatch.setattr("ai_test_asset_center.v12_pipeline.run_v12_pipeline", fake_v12_pipeline)

    result = scan(
        project="enterprise-project",
        root=tmp_path,
        base_url="http://127.0.0.1:8080",
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "environment_type": "test",
            "source_manifest": manifest,
            "execution_mode": "approved_sandbox_write",
            "execution_approval_id": "eap_stale",
        },
    )

    assert result["grade"] == "inconclusive"
    assert result["execution_status"] == "completed"
    assert len(calls) == 2
    assert str(calls[0]["campaign_context"].get("execution_approval_id") or "") == "eap_stale"
    second_approval_id = str(calls[1]["campaign_context"].get("execution_approval_id") or "")
    assert second_approval_id.startswith("eap_")
    assert second_approval_id != "eap_stale"


def test_scan_prefers_project_prd_asset_before_source_catalog_fallback(tmp_path, monkeypatch):
    manifest = register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    input_dir = tmp_path / "platform_workspace" / "enterprise-project" / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "PRD.md").write_text("### 支付\n支付成功后订单状态变为 PAID。", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_persist(*args, **kwargs):
        return {"status": "persisted", "bundle_id": "bundle_1"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        calls.append({"prd_text": prd_text, "api_spec_text": api_spec_text})
        return {
            "runtime_contract": {"status": "plan_only", "reason": ""},
            "phases": {
                "execution": {"status": "skipped", "executed": 0},
                "state_graph": {"coverage_gaps": []},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": {
                "campaign_id": "camp_prd_1",
                "scope_id": "case-lifecycle",
                "environment_ref": "approved-test",
                "source_hash": manifest["source_hash"],
            },
            "findings": [],
            "auto_har": {"status": "no_traffic"},
            "total_duration_ms": 1,
        }

    monkeypatch.setattr("ai_test_asset_center.scan_diagnostics.run_preflight", fake_run_preflight)
    monkeypatch.setattr("ai_test_asset_center.__main__._persist_execution_evidence", fake_persist)
    monkeypatch.setattr("ai_test_asset_center.__main__._evaluate_release_gate", fake_release_gate)
    monkeypatch.setattr("ai_test_asset_center.v12_pipeline.run_v12_pipeline", fake_v12_pipeline)

    result = scan(
        project="enterprise-project",
        root=tmp_path,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": manifest,
        },
    )

    assert result["grade"] == "inconclusive"
    assert len(calls) == 1
    assert "支付成功后订单状态变为 PAID" in str(calls[0]["prd_text"] or "")


def test_scan_aggregates_workspace_prd_and_template_business_rules_into_requirement_source(tmp_path, monkeypatch):
    manifest = register_source_asset("benchmark_mall_v05_p0probe", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    workspace_input = tmp_path / "platform_workspace" / "benchmark_mall_v05_p0probe" / "input"
    workspace_input.mkdir(parents=True, exist_ok=True)
    (workspace_input / "PRD.md").write_text("### 商品\n前台商品列表只展示可售商品。", encoding="utf-8")
    template_input = tmp_path / "projects" / "benchmark_mall" / "input"
    template_input.mkdir(parents=True, exist_ok=True)
    (template_input / "BUSINESS_RULES.md").write_text("用户端不展示下架商品、草稿商品、内部商品。", encoding="utf-8")
    registry_path = tmp_path / "platform_workspace" / "benchmark_mall_v05_p0probe" / "enterprise_pilot_runtime" / "connector_registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "project_id": "benchmark_mall_v05_p0probe",
                "connectors": [{"connector_id": "gateway", "system_name": "benchmark_mall", "enabled": True}],
                "test_profile": {"scope_id": "mall-scope", "environment_ref": "local-benchmark"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_persist(*args, **kwargs):
        return {"status": "persisted", "bundle_id": "bundle_1"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        calls.append({"prd_text": prd_text})
        return {
            "runtime_contract": {"status": "plan_only", "reason": ""},
            "phases": {
                "execution": {"status": "skipped", "executed": 0},
                "state_graph": {"coverage_gaps": []},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": {
                "campaign_id": "camp_prd_agg_1",
                "scope_id": "mall-scope",
                "environment_ref": "local-benchmark",
                "source_hash": manifest["source_hash"],
            },
            "findings": [],
            "auto_har": {"status": "no_traffic"},
            "total_duration_ms": 1,
        }

    monkeypatch.setattr("ai_test_asset_center.scan_diagnostics.run_preflight", fake_run_preflight)
    monkeypatch.setattr("ai_test_asset_center.__main__._persist_execution_evidence", fake_persist)
    monkeypatch.setattr("ai_test_asset_center.__main__._evaluate_release_gate", fake_release_gate)
    monkeypatch.setattr("ai_test_asset_center.v12_pipeline.run_v12_pipeline", fake_v12_pipeline)

    result = scan(
        project="benchmark_mall_v05_p0probe",
        root=tmp_path,
        campaign_context={
            "scope_id": "mall-scope",
            "environment_ref": "local-benchmark",
            "source_manifest": manifest,
        },
    )

    assert result["grade"] == "inconclusive"
    assert len(calls) == 1
    assert "## PRD.md" in str(calls[0]["prd_text"] or "")
    assert "前台商品列表只展示可售商品" in str(calls[0]["prd_text"] or "")
    assert "## BUSINESS_RULES.md" in str(calls[0]["prd_text"] or "")
    assert "用户端不展示下架商品、草稿商品、内部商品" in str(calls[0]["prd_text"] or "")


def test_scan_backfills_scope_and_environment_from_connector_registry_when_context_missing(tmp_path, monkeypatch):
    manifest = register_source_asset("benchmark_mall_v05_p0probe", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    registry_path = tmp_path / "platform_workspace" / "benchmark_mall_v05_p0probe" / "enterprise_pilot_runtime" / "connector_registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "project_id": "benchmark_mall_v05_p0probe",
                "connectors": [{"connector_id": "gateway", "system_name": "benchmark_mall", "enabled": True}],
                "test_profile": {
                    "scope_id": "benchmark-mall-checkout",
                    "environment_ref": "local-benchmark",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_persist(*args, **kwargs):
        return {"status": "persisted", "bundle_id": "bundle_1"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        calls.append({"campaign_context": dict(campaign_context or {})})
        return {
            "runtime_contract": {"status": "plan_only", "reason": ""},
            "phases": {
                "execution": {"status": "skipped", "executed": 0},
                "state_graph": {"coverage_gaps": []},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": {
                "campaign_id": "camp_registry_defaults_1",
                "scope_id": "benchmark-mall-checkout",
                "environment_ref": "local-benchmark",
                "source_hash": manifest["source_hash"],
            },
            "findings": [],
            "auto_har": {"status": "no_traffic"},
            "total_duration_ms": 1,
        }

    monkeypatch.setattr("ai_test_asset_center.scan_diagnostics.run_preflight", fake_run_preflight)
    monkeypatch.setattr("ai_test_asset_center.__main__._persist_execution_evidence", fake_persist)
    monkeypatch.setattr("ai_test_asset_center.__main__._evaluate_release_gate", fake_release_gate)
    monkeypatch.setattr("ai_test_asset_center.v12_pipeline.run_v12_pipeline", fake_v12_pipeline)

    result = scan(
        project="benchmark_mall_v05_p0probe",
        root=tmp_path,
        campaign_context={
            "source_manifest": manifest,
        },
    )

    assert result["grade"] == "inconclusive"
    assert len(calls) == 1
    assert calls[0]["campaign_context"]["scope_id"] == "benchmark-mall-checkout"
    assert calls[0]["campaign_context"]["environment_ref"] == "local-benchmark"
