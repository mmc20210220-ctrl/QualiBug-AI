from __future__ import annotations

import hashlib
import json

from ai_test_asset_center.v12_pipeline import _runtime_contract, run_v12_pipeline


API_SPEC = json.dumps({
    "openapi": "3.0.0",
    "paths": {"/api/cases/{case_id}/approve": {"patch": {"operationId": "approveCase"}}},
    "components": {"schemas": {"Case": {"type": "object", "properties": {"state": {"type": "string", "enum": ["DRAFT", "APPROVED"]}}}}},
}, ensure_ascii=False)
PRD = "Case moves from DRAFT to APPROVED by approve."
DB_SCHEMA = "CREATE TABLE cases (id TEXT PRIMARY KEY, state TEXT);"
SOURCE_MANIFEST = {
    "source_id": "uploaded:api-spec-v1",
    "source_hash": hashlib.sha256(API_SPEC.encode("utf-8")).hexdigest(),
    "source_origin": "declared_manifest",
}


def test_direct_runtime_contract_accepts_verified_manifest_without_network_access():
    contract = _runtime_contract(
        {
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
        "https://example.invalid",
        API_SPEC,
    )

    assert contract["status"] == "approved"
    assert contract["approved_base_url"] == "https://example.invalid"
    assert contract["source_manifest"]["source_id"] == "uploaded:api-spec-v1"


def test_direct_runtime_contract_rejects_hash_mismatch_before_any_execution(tmp_path):
    result = run_v12_pipeline(
        project="enterprise-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        base_url="https://example.invalid",
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": {"source_id": "uploaded:api-spec-v1", "source_hash": "0" * 64},
        },
    )

    assert result["runtime_contract"]["status"] == "blocked"
    assert "SOURCE_HASH_MISMATCH" in result["runtime_contract"]["missing_requirements"]
    assert result["phases"]["execution"]["status"] == "blocked"
    assert result["auto_har"]["status"] == "no_traffic"


def test_campaign_persists_verified_source_identity_for_plan_only_runs(tmp_path):
    result = run_v12_pipeline(
        project="enterprise-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )

    assert "error" not in result
    assert result["runtime_contract"]["status"] == "plan_only"
    assert result["campaign"]["source_id"] == "uploaded:api-spec-v1"
    assert result["campaign"]["source_hash"] == SOURCE_MANIFEST["source_hash"]
    assert result["campaign"]["source_snapshot_hash"] != SOURCE_MANIFEST["source_hash"]
