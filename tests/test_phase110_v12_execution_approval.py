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


def test_v12_requires_campaign_bound_execution_approval_before_target_traffic(tmp_path):
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
            "source_manifest": SOURCE_MANIFEST,
        },
    )

    assert result["runtime_contract"]["status"] == "blocked"
    assert result["runtime_contract"]["execution_approval"]["code"] == "EXECUTION_APPROVAL_MISSING"
    assert result["phases"]["execution"]["status"] == "blocked"
    assert result["auto_har"]["status"] == "no_traffic"
    assert result["campaign"]["source_hash"] == SOURCE_MANIFEST["source_hash"]
