from __future__ import annotations

import json

from ai_test_asset_center.__main__ import scan


API_SPEC = json.dumps({
    "openapi": "3.0.0",
    "paths": {"/api/cases/{case_id}/approve": {"patch": {"operationId": "approveCase"}}},
    "components": {"schemas": {"Case": {"type": "object", "properties": {"state": {"type": "string", "enum": ["DRAFT", "APPROVED"]}}}}},
})


def test_unified_scan_requires_a_real_source_asset(tmp_path):
    result = scan(project="enterprise-project", root=tmp_path)

    assert result["success"] is False
    assert "api_doc" in result["error"]


def test_unified_scan_reports_gaps_instead_of_running_fixed_domain_checks(tmp_path):
    result = scan(
        project="enterprise-project",
        root=tmp_path,
        api_doc_text=API_SPEC,
        campaign_context={"scope_id": "service-a", "environment_ref": "test-a"},
    )

    assert result["success"] is True
    assert result["total_findings"] == 0
    assert result["db_findings"] == []
    assert result["e2e_findings"] == []
    assert result["ui_findings"] == []
    assert result["layers"]["legacy_domain_layers"]["tool"] == "disabled"
    assert any(gap["code"] == "RUNTIME_TARGET_MISSING" for gap in result["input_gaps"])
    assert result["campaign"]["scope_id"] == "service-a"
    assert result["campaign"]["environment_ref"] == "test-a"
    assert result["campaign"]["confirmed_slice_count"] == 0
