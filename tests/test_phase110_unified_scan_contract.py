from __future__ import annotations

import hashlib
import json
import sys

from ai_test_asset_center.__main__ import scan
import ai_test_asset_center.__main__ as main_module
from ai_test_asset_center.enterprise_source_registry import register_source_asset
from ai_test_asset_center.enterprise_test_data_receipts import issue_test_data_receipt
from ai_test_asset_center.evidence_artifact_store import load_evidence_bundle, verify_evidence_bundle
from ai_test_asset_center.enterprise_test_data_plan import validate_test_data_contract
from tests.mainline_test_support import authoritative_v12_double


API_SPEC = json.dumps({
    "openapi": "3.0.0",
    "paths": {"/api/cases/{case_id}/approve": {"patch": {"operationId": "approveCase"}}},
    "components": {"schemas": {"Case": {"type": "object", "properties": {"state": {"type": "string", "enum": ["DRAFT", "APPROVED"]}}}}},
})
SOURCE_MANIFEST = {"source_id": "api-spec-v1", "source_hash": hashlib.sha256(API_SPEC.encode("utf-8")).hexdigest()}


def test_persist_customer_ready_static_artifacts_adds_cumulative_snapshot_without_rewriting_current_run_totals(tmp_path, monkeypatch):
    project = "enterprise-project"
    scan_result_path = tmp_path / "platform_outputs" / project / "scan_result.json"
    scan_result_path.parent.mkdir(parents=True, exist_ok=True)
    scan_result_path.write_text(
        json.dumps({"project": project, "total_findings": 3, "total_candidates": 0}, ensure_ascii=False),
        encoding="utf-8",
    )
    real_project_path = tmp_path / "platform_outputs" / project / "real_project" / "real_project_defect_data.json"
    real_project_path.parent.mkdir(parents=True, exist_ok=True)
    real_project_path.write_text(
        json.dumps(
            {
                "continuous_discovery_campaign": {
                    "summary": {"confirmed_slice_count": 18},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    snapshot = {
        "project": project,
        "generated_at_utc": "2026-07-07T18:20:00Z",
        "defects": [{"id": "BUG-1", "title": "重复支付"}],
        "clues": [{"id": "CLUE-1", "title": "退款链路待补证"}],
        "risks": [{"id": "BUG-1", "title": "重复支付"}],
        "value_metrics": {"ready_bug_count": 1, "clue_count": 1},
        "executive_summary": {"ready_bugs": 1, "internal_clues": 1},
        "scan_meta": {"ready_bug_count": 1},
        "data_contract": {"display_key": "defects"},
        "commercial_assets": {
            "status": "materialized",
            "commercial_handoff": {"status": "commercial_handoff_ready_with_validated_findings"},
            "delivery_package": {"status": "created", "package_ref": "platform_outputs/enterprise-project/delivery_packages/demo.zip"},
        },
    }
    import ai_test_asset_center.scan_customer_ready_artifacts as customer_ready_mod

    monkeypatch.setattr(customer_ready_mod, "_customer_ready_static_snapshot", lambda project_id, root: dict(snapshot))

    result = {"project": project, "total_findings": 3}
    persisted = main_module._persist_customer_ready_static_artifacts(project, tmp_path, result)

    saved_scan = json.loads(scan_result_path.read_text(encoding="utf-8"))
    saved_real_project = json.loads(real_project_path.read_text(encoding="utf-8"))

    assert persisted["value_metrics"]["ready_bug_count"] == 1
    assert saved_scan["total_findings"] == 3
    assert saved_scan["customer_ready_defect_count"] == 1
    assert saved_scan["customer_ready_clue_count"] == 1
    assert saved_scan["customer_ready_snapshot"]["defects"][0]["id"] == "BUG-1"
    assert saved_scan["customer_ready_snapshot"]["commercial_assets"]["status"] == "materialized"
    assert saved_real_project["continuous_discovery_campaign"]["summary"]["confirmed_slice_count"] == 18
    assert saved_real_project["defects"][0]["id"] == "BUG-1"
    assert saved_real_project["clues"][0]["id"] == "CLUE-1"
    assert saved_real_project["commercial_assets"]["delivery_package"]["status"] == "created"
    assert result["customer_ready_defect_count"] == 1
    assert result["customer_ready_clue_count"] == 1


def test_unified_scan_requires_a_real_source_asset(tmp_path):
    result = scan(project="enterprise-project", root=tmp_path)
    assert result["success"] is False
    assert "api_doc" in result["error"]


def test_inline_source_without_provenance_is_blocked_before_campaign_planning(tmp_path):
    result = scan(project="enterprise-project", root=tmp_path, api_doc_text=API_SPEC)
    assert result["success"] is True
    assert result["grade"] == "blocked"
    assert result["campaign"]["campaign_status"] == "blocked"
    assert result["campaign"]["coverage_deferred_reason"] == "source_provenance_missing"
    assert any(gap["code"] == "SOURCE_PROVENANCE_MISSING" for gap in result["input_gaps"])
    assert result["execution_status"] == "blocked"
    assert result["evidence_bundle"]["status"] == "not_created"
    assert result["release_gate"]["verdict"] == "fail"
    assert result["release_gate"]["status"] == "blocked"


def test_external_api_doc_path_without_manifest_is_not_implicitly_trusted(tmp_path):
    external_path = tmp_path / "supplier_export.json"
    external_path.write_text(API_SPEC, encoding="utf-8")
    result = scan(project="enterprise-project", root=tmp_path, api_doc_path=str(external_path), campaign_context={"scope_id": "service-a", "environment_ref": "test-a"})
    assert result["grade"] == "blocked"
    assert result["runtime_contract"]["source_manifest"]["source_origin"] == "external_path_unregistered"
    assert any(gap["code"] == "SOURCE_PROVENANCE_MISSING" for gap in result["input_gaps"])


def test_external_api_doc_path_is_allowed_with_complete_explicit_manifest(tmp_path):
    external_path = tmp_path / "supplier_export.json"
    external_path.write_text(API_SPEC, encoding="utf-8")
    result = scan(project="enterprise-project", root=tmp_path, api_doc_path=str(external_path), campaign_context={"scope_id": "service-a", "environment_ref": "test-a", "source_manifest": SOURCE_MANIFEST})
    assert result["grade"] == "blocked"
    assert result["runtime_contract"]["source_manifest"]["source_id"] == "api-spec-v1"
    assert result["runtime_contract"]["source_manifest"]["source_origin"] == "declared_manifest"
    assert result["evidence_bundle"]["status"] == "persisted"
    assert verify_evidence_bundle("enterprise-project", result["evidence_bundle"]["bundle_id"], root=tmp_path)["valid"] is True
    assert result["release_gate"]["verdict"] == "not_ready"


def test_declared_source_hash_must_match_submitted_content(tmp_path):
    result = scan(project="enterprise-project", root=tmp_path, api_doc_text=API_SPEC, campaign_context={"source_manifest": {"source_id": "api-spec-v1", "source_hash": "0" * 64}})
    assert result["grade"] == "blocked"
    assert any(gap["code"] == "SOURCE_HASH_MISMATCH" for gap in result["input_gaps"])


def test_declared_source_hash_must_use_sha256_format(tmp_path):
    result = scan(project="enterprise-project", root=tmp_path, api_doc_text=API_SPEC, campaign_context={"source_manifest": {"source_id": "api-spec-v1", "source_hash": "not-a-sha256"}})
    assert result["grade"] == "blocked"
    assert any(gap["code"] == "SOURCE_HASH_INVALID" for gap in result["input_gaps"])


def test_registered_source_registry_asset_is_preferred_and_keeps_version_identity(tmp_path):
    registered = register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    result = scan(project="enterprise-project", root=tmp_path, api_doc_text=API_SPEC, campaign_context={"scope_id": "service-a", "environment_ref": "test-a"})
    manifest = result["runtime_contract"]["source_manifest"]
    assert result["grade"] == "blocked"
    assert manifest["source_id"] == "api-contract"
    assert manifest["source_hash"] == registered["source_hash"]
    assert manifest["source_version_id"] == registered["source_version_id"]
    assert manifest["source_origin"] == "registered_source_registry"
    assert result["evidence_bundle"]["status"] == "persisted"


def test_scan_can_fallback_to_latest_registered_source_when_api_doc_is_omitted(tmp_path):
    register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    result = scan(
        project="enterprise-project",
        root=tmp_path,
        campaign_context={"scope_id": "service-a", "environment_ref": "test-a"},
    )

    assert result["success"] is True
    assert result["grade"] == "blocked"
    assert result["runtime_contract"]["source_manifest"]["source_id"] == "api-contract"
    assert result["runtime_contract"]["source_manifest"]["source_origin"] == "registered_source_registry"


def test_scan_validates_test_data_receipts_against_campaign_identity(tmp_path):
    register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    first = scan(project="enterprise-project", root=tmp_path, api_doc_text=API_SPEC, campaign_context={"scope_id": "service-a", "environment_ref": "test-a"})
    campaign_id = first["campaign"]["campaign_id"]
    creation = issue_test_data_receipt("enterprise-project", root=tmp_path, kind="creation", campaign_id=campaign_id, scope_id="service-a", environment_ref="test-a", actor={"name": "qa", "role": "qa_lead"}, data_scope_ref="sandbox-a")
    cleanup = issue_test_data_receipt("enterprise-project", root=tmp_path, kind="cleanup", campaign_id=campaign_id, scope_id="service-a", environment_ref="test-a", actor={"name": "qa", "role": "qa_lead"}, operation_ref="cleanup-a")
    second = scan(
        project="enterprise-project",
        root=tmp_path,
        api_doc_text=API_SPEC,
        campaign_context={
            "scope_id": "service-a",
            "environment_ref": "test-a",
            "test_data_contract": {
                "strategy": "create_disposable",
                "write_approved": True,
                "disposable_scope_ref": "sandbox-a",
                "creation_receipt_ref": creation["receipt_id"],
                "cleanup_receipt_ref": cleanup["receipt_id"],
            },
        },
    )
    assert second["campaign"]["campaign_id"] == campaign_id
    assert second["test_data_plan"]["status"] == "ready"
    assert second["test_data_plan"]["receipt_validation"] == "verified"
    assert second["release_gate"]["verdict"] == "not_ready"


def test_scan_can_start_a_new_auditable_campaign_rerun_for_same_input(tmp_path):
    register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    first = scan(project="enterprise-project", root=tmp_path, api_doc_text=API_SPEC, campaign_context={"scope_id": "service-a", "environment_ref": "test-a"})
    second = scan(
        project="enterprise-project",
        root=tmp_path,
        api_doc_text=API_SPEC,
        campaign_context={
            "scope_id": "service-a",
            "environment_ref": "test-a",
            "campaign_rerun_key": "priority-strategy-v2",
            "campaign_rerun_reason": "verify generic slice ranking",
        },
    )
    third = scan(
        project="enterprise-project",
        root=tmp_path,
        api_doc_text=API_SPEC,
        campaign_context={
            "scope_id": "service-a",
            "environment_ref": "test-a",
            "campaign_rerun_key": "priority-strategy-v2",
            "campaign_rerun_reason": "verify generic slice ranking",
        },
    )

    assert second["campaign"]["campaign_id"] != first["campaign"]["campaign_id"]
    assert second["campaign"]["lineage_campaign_id"] == first["campaign"]["campaign_id"]
    assert second["campaign"]["rerun_key"] == "priority-strategy-v2"
    assert second["campaign"]["rerun_reason"] == "verify generic slice ranking"
    assert third["campaign"]["campaign_id"] == second["campaign"]["campaign_id"]


def test_registered_project_asset_supplies_provenance_without_client_supplied_manifest(tmp_path):
    input_dir = tmp_path / "platform_workspace" / "enterprise-project" / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "api_spec.json").write_text(API_SPEC, encoding="utf-8")
    result = scan(project="enterprise-project", root=tmp_path, api_doc_text=API_SPEC, campaign_context={"scope_id": "service-a", "environment_ref": "test-a"})
    assert result["grade"] == "blocked"
    assert result["runtime_contract"]["source_manifest"]["source_id"].startswith("project_asset:")
    assert result["runtime_contract"]["source_manifest"]["source_origin"] == "registered_project_asset"
    assert result["campaign"]["confirmed_slice_count"] == 0


def test_registered_project_api_doc_path_is_identified_by_its_content_hash(tmp_path):
    input_dir = tmp_path / "platform_workspace" / "enterprise-project" / "input"
    input_dir.mkdir(parents=True)
    asset_path = input_dir / "api_spec.json"
    asset_path.write_text(API_SPEC, encoding="utf-8")
    result = scan(project="enterprise-project", root=tmp_path, api_doc_path=str(asset_path), campaign_context={"scope_id": "service-a", "environment_ref": "test-a"})
    assert result["grade"] == "blocked"
    assert result["runtime_contract"]["source_manifest"]["source_id"].endswith("platform_workspace/enterprise-project/input/api_spec.json")


def test_unified_scan_reports_gaps_instead_of_running_fixed_domain_checks(tmp_path):
    result = scan(project="enterprise-project", root=tmp_path, api_doc_text=API_SPEC, campaign_context={"scope_id": "service-a", "environment_ref": "test-a", "source_manifest": SOURCE_MANIFEST})
    assert result["success"] is True
    assert result["grade"] == "blocked"
    assert result["total_findings"] == 0
    assert result["db_findings"] == []
    assert result["e2e_findings"] == []
    assert result["ui_findings"] == []
    assert result["layers"]["legacy_domain_layers"]["tool"] == "disabled"
    assert result["runtime_contract"]["status"] == "plan_only"
    assert result["campaign"]["scope_id"] == "service-a"
    assert result["campaign"]["environment_ref"] == "test-a"
    assert result["campaign"]["confirmed_slice_count"] == 0
    assert result["test_data_plan"]["status"] == "blocked_with_testability_gap"


def test_runtime_target_is_blocked_without_explicit_enterprise_contract(tmp_path):
    result = scan(project="enterprise-project", root=tmp_path, api_doc_text=API_SPEC, base_url="https://example.invalid", campaign_context={})
    codes = {gap["code"] for gap in result["input_gaps"]}
    assert result["success"] is True
    assert result["runtime_contract"]["status"] == "blocked"
    assert "SOURCE_PROVENANCE_MISSING" in codes
    assert result["execution_status"] == "blocked"
    assert result["total_findings"] == 0


def test_test_data_contract_requires_receipts_for_disposable_setup():
    blocked = validate_test_data_contract({"strategy": "create_disposable", "write_approved": True}, environment_ref="env-a", scope_id="scope-a")
    ready = validate_test_data_contract({"strategy": "create_disposable", "write_approved": True, "environment_ref": "env-a", "scope_id": "scope-a", "disposable_scope_ref": "isolated-scope", "creation_receipt_ref": "created", "cleanup_receipt_ref": "cleaned"}, environment_ref="", scope_id="")
    assert blocked["status"] == "blocked_with_testability_gap"
    assert "DISPOSABLE_SCOPE_MISSING" in blocked["missing_requirements"]
    assert ready["status"] == "ready"


def test_cli_main_infers_write_approved_test_data_contract_for_non_prod_runtime(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_scan(*, project, api_doc_path="", api_doc_text="", prd_text="", base_url="", ci_gate=False, multi_layer=True, output_dir=None, save_report=True, campaign_context=None, root=None):
        captured["project"] = project
        captured["base_url"] = base_url
        captured["campaign_context"] = campaign_context
        return {
            "success": True,
            "project": project,
            "total_findings": 0,
            "total_candidates": 0,
            "execution_status": "stopped",
            "release_gate": {"verdict": "not_ready"},
            "campaign": {"campaign_id": "CMP_TEST", "campaign_status": "ready"},
        }

    monkeypatch.setattr(main_module, "scan", fake_scan)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "qualibug",
            "scan",
            "--project",
            "enterprise-project",
            "--base-url",
            "http://127.0.0.1:8080",
            "--scope-id",
            "checkout-scope",
            "--environment-ref",
            "staging-sandbox",
            "--execution-mode",
            "approved_sandbox_write",
            "--test-data-strategy",
            "create_disposable",
        ],
    )

    try:
        main_module.main()
    except SystemExit as exc:
        assert exc.code == 0


def test_scan_promotes_external_evidence_backed_finding_to_validated_candidate(monkeypatch, tmp_path):
    manifest = register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_persist(*args, **kwargs):
        return {"status": "persisted", "bundle_id": "bundle_ext_1"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        return {
            "runtime_contract": {"status": "approved", "approved_base_url": base_url},
            "phases": {
                "execution": {"status": "completed", "executed": 1},
                "state_graph": {"coverage_gaps": []},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": {
                "campaign_id": "camp_ext_1",
                "scope_id": "service-a",
                "environment_ref": "test-a",
                "source_hash": manifest["source_hash"],
            },
            "findings": [],
            "external_findings": [
                {
                    "title": "refund write violated order invariant",
                    "source": "external_signal:schemathesis",
                    "runtime_replay": {"status": "executed", "http_status": 500},
                    "db_evidence": {
                        "before_db_snapshot": {"row_count": 0},
                        "after_db_snapshot": {"row_count": 1},
                        "db_assertion": "refund rows changed 0->1",
                        "business_operation": "POST /api/refunds",
                        "table": "refunds",
                    },
                    "business_invariant_evaluation": {
                        "verdict": "failed",
                        "reason": "status changed unexpectedly",
                    },
                }
            ],
            "auto_har": {"status": "captured"},
            "total_duration_ms": 1,
        }

    monkeypatch.setattr("ai_test_asset_center.scan_diagnostics.run_preflight", fake_run_preflight)
    monkeypatch.setattr("ai_test_asset_center.__main__._persist_execution_evidence", fake_persist)
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

    assert result["total_findings"] == 0
    assert len(result["candidate_findings"]) == 1
    candidate = result["candidate_findings"][0]
    assert candidate["confirmation_status"] == "validated_candidate"
    assert candidate["external_evidence_adjudication"]["status"] == "validated_candidate"
    assert candidate["evidence_quality"]["level"] == "validated"
    assert candidate["business_evidence_status"] == "VALIDATED"


def test_scan_keeps_external_finding_as_candidate_when_hard_evidence_is_incomplete(monkeypatch, tmp_path):
    manifest = register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_persist(*args, **kwargs):
        return {"status": "persisted", "bundle_id": "bundle_ext_2"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        return {
            "runtime_contract": {"status": "approved", "approved_base_url": base_url},
            "phases": {
                "execution": {"status": "completed", "executed": 1},
                "state_graph": {"coverage_gaps": []},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": {
                "campaign_id": "camp_ext_2",
                "scope_id": "service-a",
                "environment_ref": "test-a",
                "source_hash": manifest["source_hash"],
            },
            "findings": [],
            "external_findings": [
                {
                    "title": "refund write may be risky",
                    "source": "external_signal:schemathesis",
                    "runtime_replay": {"status": "executed", "http_status": 500},
                    "db_evidence": {
                        "before_db_snapshot": {"row_count": 0},
                        "after_db_snapshot": {"row_count": 1},
                        "db_assertion": "refund rows changed 0->1",
                        "business_operation": "POST /api/refunds",
                        "table": "refunds",
                    },
                    "business_invariant_evaluation": {
                        "verdict": "passed",
                        "reason": "no invariant failure",
                    },
                }
            ],
            "auto_har": {"status": "captured"},
            "total_duration_ms": 1,
        }

    monkeypatch.setattr("ai_test_asset_center.scan_diagnostics.run_preflight", fake_run_preflight)
    monkeypatch.setattr("ai_test_asset_center.__main__._persist_execution_evidence", fake_persist)
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

    candidate = result["candidate_findings"][0]
    assert candidate["confirmation_status"] == "candidate"
    assert candidate["external_evidence_adjudication"]["status"] == "candidate"
    assert candidate["external_evidence_adjudication"]["has_failed_invariant"] is False
    assert result["external_reproduction_assets"]["status"] == "empty"


def test_scan_persists_external_validated_candidate_evidence_package_into_bundle(monkeypatch, tmp_path):
    manifest = register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        return {
            "runtime_contract": {"status": "approved", "approved_base_url": base_url},
            "phases": {
                "execution": {"status": "completed", "executed": 1},
                "state_graph": {"coverage_gaps": []},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": {
                "campaign_id": "camp_ext_pkg_1",
                "scope_id": "service-a",
                "environment_ref": "test-a",
                "source_hash": manifest["source_hash"],
            },
            "findings": [],
            "external_findings": [
                {
                    "risk_id": "EXT-PKG-1",
                    "title": "refund write violated order invariant",
                    "category": "business_invariant",
                    "source": "external_signal:schemathesis",
                    "runtime_replay": {
                        "status": "executed",
                        "http_status": 500,
                        "method": "POST",
                        "path": "/api/refunds",
                        "duration_ms": 42,
                        "trace": {
                            "steps": [
                                {
                                    "method": "POST",
                                    "path": "/api/refunds",
                                    "response": {"status_code": 500, "body": {"error": "boom"}},
                                }
                            ]
                        },
                    },
                    "before_after_snapshot": {
                        "before": {"method": "POST", "path": "/api/refunds", "status_code": 500, "body": {"phase": "before"}},
                        "after": {"method": "POST", "path": "/api/refunds", "status_code": 500, "body": {"phase": "after"}},
                    },
                    "db_evidence": {
                        "before_db_snapshot": {"row_count": 0},
                        "after_db_snapshot": {"row_count": 1},
                        "db_assertion": "refund rows changed 0->1",
                        "business_operation": "POST /api/refunds",
                        "table": "refunds",
                    },
                    "business_invariant_evaluation": {
                        "verdict": "failed",
                        "reason": "status changed unexpectedly",
                        "results": [
                            {
                                "kind": "business_invariant",
                                "name": "订单状态守恒",
                                "verdict": "failed",
                                "reason": "status changed unexpectedly",
                                "failed_fields": ["status"],
                            }
                        ],
                    },
                }
            ],
            "auto_har": {"status": "no_traffic"},
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

    candidate = result["candidate_findings"][0]
    assert candidate["confirmation_status"] == "validated_candidate"
    assert candidate["evidence_package"]["engine"] == "runtime_finding_evidence_packager_v1_phase92t"
    assert candidate["evidence_package"]["customer_ready_summary"]["endpoint"] == "POST /api/refunds"
    assert candidate["evidence_grade"] in {"strong", "moderate", "partial", "weak"}
    assert verify_evidence_bundle("enterprise-project", result["evidence_bundle"]["bundle_id"], root=tmp_path)["valid"] is True

    manifest_data = load_evidence_bundle("enterprise-project", result["evidence_bundle"]["bundle_id"], root=tmp_path)
    # Single-Write (artifactized) bundles store findings/candidates inside the
    # content-addressed execution_output part, not as legacy files next to the
    # manifest. Read them back through the same store the scan wrote to.
    from ai_test_asset_center.artifact_store import default_artifact_store

    store = default_artifact_store(tmp_path)
    execution_output = store.get_json(manifest_data["parts"]["execution_output_ref"])
    findings_payload = execution_output["findings"]
    candidate_payload = execution_output["candidate_findings"]

    assert findings_payload == []
    assert len(candidate_payload) == 1
    persisted = candidate_payload[0]
    assert persisted["risk_id"] == "EXT-PKG-1"
    assert persisted["evidence_package"]["engine"] == "runtime_finding_evidence_packager_v1_phase92t"
    assert persisted["delta_summary"]["failed_fields"] == ["status"]


def test_scan_materializes_external_reproduction_assets_and_links(monkeypatch, tmp_path):
    manifest = register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        return {
            "runtime_contract": {"status": "approved", "approved_base_url": base_url},
            "phases": {
                "execution": {"status": "completed", "executed": 1},
                "state_graph": {"coverage_gaps": []},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": {
                "campaign_id": "camp_ext_repro_1",
                "scope_id": "service-a",
                "environment_ref": "test-a",
                "source_hash": manifest["source_hash"],
            },
            "findings": [],
            "external_findings": [
                {
                    "risk_id": "EXT-REPRO-1",
                    "title": "refund write violated order invariant",
                    "category": "business_invariant",
                    "source": "external_signal:schemathesis",
                    "runtime_replay": {
                        "status": "executed",
                        "http_status": 500,
                        "method": "POST",
                        "path": "/api/refunds",
                        "duration_ms": 42,
                    },
                    "raw_evidence": {
                        "request_raw": {"method": "POST", "path": "/api/refunds", "body": {"order_id": "ord_1"}},
                        "response_raw": {"status_code": 500, "body": {"error": "boom"}},
                    },
                    "db_evidence": {
                        "before_db_snapshot": {"row_count": 0},
                        "after_db_snapshot": {"row_count": 1},
                        "db_assertion": "refund rows changed 0->1",
                        "business_operation": "POST /api/refunds",
                        "table": "refunds",
                    },
                    "business_invariant_evaluation": {
                        "verdict": "failed",
                        "reason": "status changed unexpectedly",
                        "results": [
                            {"kind": "business_invariant", "name": "订单状态守恒", "verdict": "failed", "reason": "status changed unexpectedly", "failed_fields": ["status"]}
                        ],
                    },
                }
            ],
            "auto_har": {"status": "no_traffic"},
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

    assets = result["external_reproduction_assets"]
    assert assets["status"] == "materialized"
    assert assets["customer_ready_reproduction_count"] == 1
    assert assets["runtime_customer_reproduction_pack"]["status"] == "ready"
    candidate = result["candidate_findings"][0]
    assert candidate["candidate_id"] == "EXT-REPRO-1"
    assert candidate["evidence_package"]["reproduction_assets"]["primary_repro_asset"]["kind"] == "repro_ps1"
    assert len(candidate["reproduction_artifact_links"]) >= 2

    workspace_root = tmp_path / "platform_workspace" / "enterprise-project" / "defect_discovery"
    assert (workspace_root / "external_runtime_customer_reproduction_pack.json").exists()
    assert (workspace_root / "external_runtime_customer_reproduction_pack.md").exists()
    assert (workspace_root / "external_validated_bug_repro.ps1").exists()
    assert (workspace_root / "external_validated_bug_regression_pytest.py").exists()

    pack = json.loads((workspace_root / "external_runtime_customer_reproduction_pack.json").read_text(encoding="utf-8"))
    assert pack["engine"] == "runtime_customer_reproduction_pack_v1_phase95"
    assert pack["customer_ready_reproduction_count"] == 1
    assert pack["packages"][0]["candidate_id"] == "EXT-REPRO-1"
    assert pack["packages"][0]["customer_ready"] is True


def test_scan_materializes_external_commercial_assets_with_conservative_tracker_sync(monkeypatch, tmp_path):
    manifest = register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        return {
            "runtime_contract": {"status": "approved", "approved_base_url": base_url},
            "phases": {
                "execution": {"status": "completed", "executed": 1},
                "state_graph": {"coverage_gaps": []},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": {
                "campaign_id": "camp_ext_commercial_1",
                "scope_id": "service-a",
                "environment_ref": "test-a",
                "source_hash": manifest["source_hash"],
            },
            "findings": [],
            "external_findings": [
                {
                    "risk_id": "EXT-COMM-1",
                    "title": "refund write violated order invariant",
                    "category": "business_invariant",
                    "severity": "P1",
                    "source": "external_signal:schemathesis",
                    "runtime_replay": {
                        "status": "executed",
                        "http_status": 500,
                        "method": "POST",
                        "path": "/api/refunds",
                        "duration_ms": 42,
                    },
                    "raw_evidence": {
                        "request_raw": {"method": "POST", "path": "/api/refunds", "body": {"order_id": "ord_1"}},
                        "response_raw": {"status_code": 500, "body": {"error": "boom"}},
                    },
                    "db_evidence": {
                        "before_db_snapshot": {"row_count": 0},
                        "after_db_snapshot": {"row_count": 1},
                        "db_assertion": "refund rows changed 0->1",
                        "business_operation": "POST /api/refunds",
                        "table": "refunds",
                    },
                    "business_invariant_evaluation": {
                        "verdict": "failed",
                        "reason": "status changed unexpectedly",
                        "results": [
                            {"kind": "business_invariant", "name": "订单状态守恒", "verdict": "failed", "reason": "status changed unexpectedly", "failed_fields": ["status"]}
                        ],
                    },
                }
            ],
            "auto_har": {"status": "no_traffic"},
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

    assets = result["external_commercial_assets"]
    assert assets["status"] == "materialized"
    assert assets["finding_count"] == 1
    assert assets["commercial_handoff_status"] == "commercial_handoff_ready_with_validated_findings"
    assert assets["commercial_handoff_acceptance_status"] == "ready_for_customer_acceptance"
    assert assets["external_tracker_sync_payload_status"] == "external_tracker_sync_payloads_blocked_or_empty"
    assert assets["external_tracker_sync_payload_gate_status"] == "external_tracker_sync_payload_gate_hold_only"
    assert assets["delivery_package"]["status"] == "created"

    output_root = tmp_path / "platform_outputs" / "enterprise-project" / "defect_discovery"
    assert (output_root / "external_commercial_handoff_bundle.json").exists()
    assert (output_root / "external_commercial_handoff_acceptance_gate.json").exists()
    assert (output_root / "external_handoff_archive_manifest.json").exists()
    assert (output_root / "external_tracker_sync_payloads.json").exists()
    assert (output_root / "external_immutable_run_receipt.json").exists()

    payloads = json.loads((output_root / "external_tracker_sync_payloads.json").read_text(encoding="utf-8"))
    assert payloads["status"] == "external_tracker_sync_payloads_blocked_or_empty"
    assert payloads["jira_transition_payloads"] == []
    assert payloads["linear_update_payloads"] == []
    assert payloads["csv_status_updates"] == []

    package_ref = assets["delivery_package"]["package_ref"]
    assert (tmp_path / package_ref).exists()


def test_scan_preserves_existing_ui_followup_assets_when_current_run_has_no_high_confidence_ui(monkeypatch, tmp_path):
    manifest = register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    workspace_dir = tmp_path / "platform_workspace" / "enterprise-project" / "defect_discovery"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    existing_intake = {
        "version": "ui_high_confidence_defect_intake_candidates_v1",
        "project_id": "enterprise-project",
        "scan_id": "scan_old",
        "campaign_id": "camp_old",
        "generated_at_utc": "2026-07-07T10:00:00Z",
        "items": [
            {
                "intake_id": "UIINTAKE_old",
                "title": "历史 UI 高可信候选",
                "severity": "P1",
                "risk_type": "ui_execution",
                "method": "POST",
                "path": "/ui/orders/1/cancel",
                "generated_at_utc": "2026-07-07T10:00:00Z",
            }
        ],
    }
    existing_regression = {
        "version": "ui_high_confidence_regression_candidates_v1",
        "project_id": "enterprise-project",
        "scan_id": "scan_old",
        "campaign_id": "camp_old",
        "generated_at_utc": "2026-07-07T10:00:00Z",
        "items": [
            {
                "regression_probe_id": "UIREG_old",
                "title": "历史 UI 高可信候选",
                "severity": "P1",
                "risk_type": "ui_execution",
                "method": "POST",
                "path": "/ui/orders/1/cancel",
                "approved": True,
                "generated_at_utc": "2026-07-07T10:00:00Z",
            }
        ],
    }
    (workspace_dir / "internal_defect_intake_candidates.json").write_text(json.dumps(existing_intake, ensure_ascii=False), encoding="utf-8")
    (workspace_dir / "ui_high_confidence_regression_candidates.json").write_text(json.dumps(existing_regression, ensure_ascii=False), encoding="utf-8")

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_persist_execution_evidence(*args, **kwargs):
        return {"status": "persisted", "bundle_id": "evb_ui_preserve", "manifest_ref": "platform_outputs/enterprise-project/evidence/manifest.json"}

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

    intake_payload = json.loads((workspace_dir / "internal_defect_intake_candidates.json").read_text(encoding="utf-8"))
    regression_payload = json.loads((workspace_dir / "ui_high_confidence_regression_candidates.json").read_text(encoding="utf-8"))

    assert result["ui_followup_assets"]["status"] == "preserved"
    assert result["ui_followup_assets"]["defect_intake_candidate_count"] == 1
    assert result["ui_followup_assets"]["regression_candidate_count"] == 1
    assert intake_payload["items"][0]["title"] == "历史 UI 高可信候选"
    assert regression_payload["items"][0]["title"] == "历史 UI 高可信候选"
