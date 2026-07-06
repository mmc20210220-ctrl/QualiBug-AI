from __future__ import annotations

import json

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


def test_scan_blocks_target_traffic_without_campaign_execution_approval(tmp_path):
    manifest = register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    result = scan(
        project="enterprise-project",
        root=tmp_path,
        base_url="https://example.invalid",
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": manifest,
        },
    )

    assert result["grade"] == "blocked"
    assert result["runtime_contract"]["status"] == "blocked"
    assert result["runtime_contract"]["execution_approval"]["code"] == "EXECUTION_APPROVAL_MISSING"
    assert result["execution_status"] == "blocked"
    assert result["auto_har"]["status"] == "no_traffic"
    assert result["release_gate"]["verdict"] == "fail"
