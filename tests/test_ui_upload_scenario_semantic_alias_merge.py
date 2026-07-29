from __future__ import annotations

import pytest

from ai_test_asset_center.ui_upload_scenario_semantic_authority import (
    _safe_prerequisite_operation,
    _source_actor_role,
)


_SOURCE = {
    "source_id": "src-openapi",
    "status": "active",
    "version": "3",
    "content_hash": "a" * 64,
}


def test_empty_primary_catalogs_do_not_hide_populated_alias_catalogs() -> None:
    asset = {
        "sources": [],
        "source_inventory": [_SOURCE],
        "interfaces": [],
        "operations": [
            {
                "interface_id": "api:GET:/upload",
                "operation_id": "getUpload",
                "method": "GET",
                "path": "/upload",
                "source_id": "src-openapi",
            }
        ],
        "roles": [],
        "permission_matrix": [],
        "permissions": [
            {
                "role": "admin",
                "resource": "/upload",
                "actions": ["read"],
            }
        ],
    }

    operation = _safe_prerequisite_operation(asset, "api:GET:/upload")

    assert operation == {
        "interface_id": "api:GET:/upload",
        "operation_id": "getUpload",
        "method": "GET",
        "path": "/upload",
        "source_id": "src-openapi",
        "source_hash": "a" * 64,
        "source_version": "3",
    }
    assert _source_actor_role(asset, "ADMIN") == "admin"


def test_exact_duplicate_alias_rows_are_collapsed() -> None:
    operation = {
        "interface_id": "api:GET:/upload",
        "operation_id": "getUpload",
        "method": "GET",
        "path": "/upload",
        "source_id": "src-openapi",
    }
    asset = {
        "sources": [_SOURCE],
        "source_inventory": [dict(_SOURCE)],
        "interfaces": [operation],
        "operations": [dict(operation)],
    }

    assert _safe_prerequisite_operation(asset, "getUpload")["interface_id"] == (
        "api:GET:/upload"
    )


def test_conflicting_duplicate_operation_identity_remains_ambiguous() -> None:
    asset = {
        "sources": [_SOURCE],
        "interfaces": [
            {
                "interface_id": "api:GET:/upload",
                "operation_id": "getUpload",
                "method": "GET",
                "path": "/upload",
                "source_id": "src-openapi",
            }
        ],
        "operations": [
            {
                "interface_id": "api:GET:/upload",
                "operation_id": "getUploadV2",
                "method": "GET",
                "path": "/upload-v2",
                "source_id": "src-openapi",
            }
        ],
    }

    with pytest.raises(
        ValueError,
        match="ui_upload_scenario_prerequisite_operation_ambiguous",
    ):
        _safe_prerequisite_operation(asset, "api:GET:/upload")
