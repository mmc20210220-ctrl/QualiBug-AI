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
            "mainline_authority": "experiment_candidate",
            "run_id": "RUN-NONPRODUCTION-AUTHORIZATION",
            "target_id": "TARGET-NONPRODUCTION",
            "environment_id": "ENV-NONPRODUCTION",
            "policy_version": "policy-nonproduction",
            "evaluation_mode": "operational",
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "environment_type": "test",
            "execution_mode": "approved_sandbox_write",
            "source_manifest": SOURCE_MANIFEST,
        },
    )

    assert result["runtime_contract"]["status"] == "approved"
    assert result["runtime_contract"]["target_policy_decision"]["write_allowed"] is True
    assert result["discovery_funnel"]["pipeline_health"]["status"] == "BLOCKED"
    assert result["auto_har"]["entry_count"] == 0
    assert result["campaign"]["source_hash"] == SOURCE_MANIFEST["source_hash"]
