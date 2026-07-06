from __future__ import annotations

import hashlib
import json

from ai_test_asset_center.__main__ import scan
from ai_test_asset_center.enterprise_source_registry import register_source_asset
from ai_test_asset_center.enterprise_test_data_receipts import issue_test_data_receipt
from ai_test_asset_center.evidence_artifact_store import verify_evidence_bundle
from ai_test_asset_center.enterprise_test_data_plan import validate_test_data_contract


API_SPEC = json.dumps({
    "openapi": "3.0.0",
    "paths": {"/api/cases/{case_id}/approve": {"patch": {"operationId": "approveCase"}}},
    "components": {"schemas": {"Case": {"type": "object", "properties": {"state": {"type": "string", "enum": ["DRAFT", "APPROVED"]}}}}},
})
SOURCE_MANIFEST = {"source_id": "api-spec-v1", "source_hash": hashlib.sha256(API_SPEC.encode("utf-8")).hexdigest()}


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
    assert result["grade"] == "inconclusive"
    assert result["runtime_contract"]["source_manifest"]["source_id"] == "api-spec-v1"
    assert result["runtime_contract"]["source_manifest"]["source_origin"] == "declared_manifest"
    assert result["evidence_bundle"]["status"] == "persisted"
    assert verify_evidence_bundle("enterprise-project", result["evidence_bundle"]["bundle_id"], root=tmp_path)["valid"] is True


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
    assert result["grade"] == "inconclusive"
    assert manifest["source_id"] == "api-contract"
    assert manifest["source_hash"] == registered["source_hash"]
    assert manifest["source_version_id"] == registered["source_version_id"]
    assert manifest["source_origin"] == "registered_source_registry"
    assert result["evidence_bundle"]["status"] == "persisted"


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


def test_registered_project_asset_supplies_provenance_without_client_supplied_manifest(tmp_path):
    input_dir = tmp_path / "platform_workspace" / "enterprise-project" / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "api_spec.json").write_text(API_SPEC, encoding="utf-8")
    result = scan(project="enterprise-project", root=tmp_path, api_doc_text=API_SPEC, campaign_context={"scope_id": "service-a", "environment_ref": "test-a"})
    assert result["grade"] == "inconclusive"
    assert result["runtime_contract"]["source_manifest"]["source_id"].startswith("project_asset:")
    assert result["runtime_contract"]["source_manifest"]["source_origin"] == "registered_project_asset"
    assert result["campaign"]["confirmed_slice_count"] == 0


def test_registered_project_api_doc_path_is_identified_by_its_content_hash(tmp_path):
    input_dir = tmp_path / "platform_workspace" / "enterprise-project" / "input"
    input_dir.mkdir(parents=True)
    asset_path = input_dir / "api_spec.json"
    asset_path.write_text(API_SPEC, encoding="utf-8")
    result = scan(project="enterprise-project", root=tmp_path, api_doc_path=str(asset_path), campaign_context={"scope_id": "service-a", "environment_ref": "test-a"})
    assert result["grade"] == "inconclusive"
    assert result["runtime_contract"]["source_manifest"]["source_id"].endswith("platform_workspace/enterprise-project/input/api_spec.json")


def test_unified_scan_reports_gaps_instead_of_running_fixed_domain_checks(tmp_path):
    result = scan(project="enterprise-project", root=tmp_path, api_doc_text=API_SPEC, campaign_context={"scope_id": "service-a", "environment_ref": "test-a", "source_manifest": SOURCE_MANIFEST})
    assert result["success"] is True
    assert result["grade"] == "inconclusive"
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
