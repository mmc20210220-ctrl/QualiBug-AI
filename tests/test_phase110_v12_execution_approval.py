from __future__ import annotations

import hashlib
import json

from ai_test_asset_center.v12_pipeline import run_v12_pipeline


API_SPEC = json.dumps({
    "openapi": "3.0.0",
    "paths": {"/api/cases/{case_id}/approve": {"patch": {"operationId": "approveCase"}}},
    "components": {"schemas": {"Case": {"type": "object", "properties": {"state": {"type": "string", "enum": ["DRAFT", "APPROVED"]}}}}},
})
SOURCE_MANIFEST = {
    "source_id": "uploaded:api-contract",
    "source_hash": hashlib.sha256(API_SPEC.encode("utf-8")).hexdigest(),
    "source_origin": "declared_manifest",
}


def test_v12_authorizes_source_bound_nonproduction_without_per_probe_approval(tmp_path):
    result = run_v12_pipeline(
        project="enterprise-project",
        root=tmp_path,
        prd_text="DRAFT -> APPROVED by approve",
        api_spec_text=API_SPEC,
        db_schema_text="CREATE TABLE cases (id TEXT PRIMARY KEY, state TEXT);",
        base_url="https://example.invalid",
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "execution_mode": "approved_sandbox_write",
            "source_manifest": SOURCE_MANIFEST,
        },
    )

    assert result["runtime_contract"]["status"] == "approved"
    assert result["runtime_contract"]["execution_approval"]["authorization_basis"] == "source_bound_nonproduction_campaign"
    assert result["phases"]["execution"]["status"] == "blocked"
    assert result["phases"]["execution"]["reason"] == "test_account_token_missing"
    assert result["auto_har"]["status"] == "no_traffic"
    assert result["campaign"]["source_hash"] == SOURCE_MANIFEST["source_hash"]
