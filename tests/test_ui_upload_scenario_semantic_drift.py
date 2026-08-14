from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center.enterprise_knowledge_center import (
    ingest_enterprise_knowledge_documents,
)
from ai_test_asset_center.private_pilot_ui_upload_scenario_health_patch import (
    upload_scenario_health_status,
)
from ai_test_asset_center.ui_upload_fixture_registry import (
    approve_upload_fixture,
    register_upload_fixture,
)
from ai_test_asset_center.ui_upload_fixture_registry_integrity import (
    install_upload_fixture_registry_integrity,
)
from ai_test_asset_center.ui_upload_scenario_registry import (
    approve_upload_scenario,
    approved_upload_scenario,
    register_upload_scenario,
)
from ai_test_asset_center.ui_upload_scenario_semantic_authority import (
    install_ui_upload_scenario_semantic_authority,
)
from ai_test_asset_center.ui_upload_scenario_source_authority import (
    install_ui_upload_scenario_source_authority,
)

_PROJECT = "upload-scenario-semantic-drift"
_ACTOR = {"name": "qa-owner", "role": "qa_lead"}
_OPENAPI_FILE = "upload-openapi.json"
_PERMISSION_FILE = "upload-permissions.json"


def _openapi(summary: str) -> str:
    return json.dumps({
        "openapi": "3.0.0",
        "info": {"title": "Upload API", "version": "1.0.0"},
        "paths": {
            "/upload": {
                "get": {
                    "operationId": "getUploadState",
                    "summary": summary,
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    })


def _permissions(role: str) -> str:
    return json.dumps({
        "permissions": [{
            "role": role,
            "resource": "/upload",
            "actions": ["read"],
            "decision": "allow",
        }]
    })


def _ingest_initial(tmp_path: Path) -> dict[str, object]:
    result = ingest_enterprise_knowledge_documents(
        _PROJECT,
        [
            {
                "text": "# Upload\nThe operator uploads a fixture and sees Ready.",
                "filename": "upload-ui.md",
                "source_type": "uiux_spec",
            },
            {
                "text": _openapi("Read upload state"),
                "filename": _OPENAPI_FILE,
                "source_type": "openapi",
            },
            {
                "text": _permissions("admin"),
                "filename": _PERMISSION_FILE,
                "source_type": "permission_matrix",
            },
        ],
        root=tmp_path,
        actor=_ACTOR,
    )
    return next(
        row for row in result["created"]
        if row.get("original_name") == "upload-ui.md"
    )


def _approved(tmp_path: Path) -> dict[str, object]:
    install_upload_fixture_registry_integrity()
    install_ui_upload_scenario_source_authority()
    install_ui_upload_scenario_semantic_authority()
    source = _ingest_initial(tmp_path)
    fixture_path = tmp_path / "platform_inputs" / _PROJECT / "inbox" / "upload.csv"
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text("id\n1\n", encoding="utf-8")
    registered_fixture = register_upload_fixture(
        _PROJECT,
        file_path=fixture_path,
        fixture_name="upload-csv",
        root=tmp_path,
        actor=_ACTOR,
    )
    fixture = approve_upload_fixture(
        _PROJECT,
        fixture_id=registered_fixture["fixture"]["fixture_id"],
        root=tmp_path,
        actor=_ACTOR,
    )["fixture"]
    candidate = register_upload_scenario(
        _PROJECT,
        {
            "title": "上传场景",
            "source_id": source["source_id"],
            "source_locator": "heading:Upload",
            "operation_ref": "api:GET:/upload",
            "actor_role": "admin",
            "start_url": "/upload",
            "fixture_binding_refs": [fixture["binding_ref"]],
            "upload_selector": "input[type=file]",
            "submission_mode": "click_submit",
            "submit_selector": "#upload-submit",
            "cleanup_selector": "#remove-upload",
            "assertion_selector": "#result",
            "assertion_text": "Ready",
            "rendered_probe_selector": "#result",
            "persistent_probe_url": "/api/upload/state",
            "persistent_json_pointer": "/count",
        },
        root=tmp_path,
        actor=_ACTOR,
    )["scenario"]
    return approve_upload_scenario(
        _PROJECT,
        scenario_id=candidate["scenario_id"],
        root=tmp_path,
        actor=_ACTOR,
    )["scenario"]


def test_openapi_version_change_invalidates_approved_scenario(tmp_path: Path) -> None:
    approved = _approved(tmp_path)
    ingest_enterprise_knowledge_documents(
        _PROJECT,
        [{
            "text": _openapi("Read upload state v2"),
            "filename": _OPENAPI_FILE,
            "source_type": "openapi",
        }],
        root=tmp_path,
        actor=_ACTOR,
    )

    with pytest.raises(RuntimeError, match="prerequisite_operation_version_changed"):
        approved_upload_scenario(
            _PROJECT,
            str(approved["scenario_ref"]),
            root=tmp_path,
        )


def test_role_removal_invalidates_approved_scenario(tmp_path: Path) -> None:
    approved = _approved(tmp_path)
    ingest_enterprise_knowledge_documents(
        _PROJECT,
        [{
            "text": _permissions("auditor"),
            "filename": _PERMISSION_FILE,
            "source_type": "permission_matrix",
        }],
        root=tmp_path,
        actor=_ACTOR,
    )

    with pytest.raises(RuntimeError, match="actor_role_changed"):
        approved_upload_scenario(
            _PROJECT,
            str(approved["scenario_ref"]),
            root=tmp_path,
        )


def test_health_reports_source_and_semantic_authorities() -> None:
    install_ui_upload_scenario_source_authority()
    install_ui_upload_scenario_semantic_authority()

    status = upload_scenario_health_status()

    assert status["checks"]["knowledge_source_authority_installed"] is True
    assert status["checks"]["safe_operation_role_authority_installed"] is True
    assert status["governance"]["safe_prerequisite_methods"] == [
        "GET",
        "HEAD",
        "OPTIONS",
    ]
    assert status["governance"]["write_prerequisite_operation_supported"] is False
