from __future__ import annotations

import json

from ai_test_asset_center.change_impact import compare_source_versions
from ai_test_asset_center.enterprise_source_registry import register_source_asset


def _spec(paths):
    return json.dumps({"openapi": "3.0.0", "paths": paths}, ensure_ascii=False)


def test_change_impact_emits_only_added_modified_removed_operations(tmp_path):
    base = register_source_asset(
        "enterprise-project",
        "api-contract",
        _spec({
            "/records/{record_id}": {"get": {"operationId": "readRecord"}},
            "/records": {"post": {"operationId": "createRecord"}},
        }),
        source_type="openapi",
        root=tmp_path,
    )
    head = register_source_asset(
        "enterprise-project",
        "api-contract",
        _spec({
            "/records/{record_id}": {"get": {"operationId": "readRecord", "parameters": [{"name": "include_history", "in": "query"}]}},
            "/records/{record_id}/review": {"post": {"operationId": "reviewRecord"}},
        }),
        source_type="openapi",
        root=tmp_path,
    )

    impact = compare_source_versions("enterprise-project", root=tmp_path, base_manifest=base, head_manifest=head)

    assert impact["summary"] == {
        "changed_operation_count": 3,
        "added_operation_count": 1,
        "modified_operation_count": 1,
        "removed_operation_count": 1,
        "review_required": True,
    }
    kinds = {(item["method"], item["path"]): item["change_kind"] for item in impact["impacts"]}
    assert kinds[("GET", "/records/{record_id}")] == "operation_modified"
    assert kinds[("POST", "/records/{record_id}/review")] == "operation_added"
    assert kinds[("POST", "/records")] == "operation_removed"


def test_change_impact_ignores_documentation_only_changes(tmp_path):
    base = register_source_asset("enterprise-project", "api-contract", _spec({"/records": {"get": {"operationId": "listRecords", "summary": "Old text"}}}), source_type="openapi", root=tmp_path)
    head = register_source_asset("enterprise-project", "api-contract", _spec({"/records": {"get": {"operationId": "listRecords", "summary": "New text", "description": "More details"}}}), source_type="openapi", root=tmp_path)

    impact = compare_source_versions("enterprise-project", root=tmp_path, base_manifest=base, head_manifest=head)

    assert impact["impacts"] == []
    assert impact["summary"]["review_required"] is False


def test_change_impact_reports_explicit_gap_for_non_openapi_sources(tmp_path):
    base = register_source_asset("enterprise-project", "requirements", "Requirement revision one", source_type="prd", root=tmp_path)
    head = register_source_asset("enterprise-project", "requirements", "Requirement revision two", source_type="prd", root=tmp_path)

    impact = compare_source_versions("enterprise-project", root=tmp_path, base_manifest=base, head_manifest=head)

    assert impact["impacts"] == []
    assert impact["coverage_gaps"][0]["code"] == "SOURCE_FORMAT_NOT_OPERATIONAL"
