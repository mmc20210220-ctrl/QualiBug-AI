from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center.enterprise_knowledge_center import (
    ingest_enterprise_knowledge_documents,
)
from ai_test_asset_center.ui_upload_fixture_registry import (
    approve_upload_fixture,
    register_upload_fixture,
    revoke_upload_fixture,
)
from ai_test_asset_center.ui_upload_fixture_registry_integrity import (
    install_upload_fixture_registry_integrity,
)
from ai_test_asset_center.ui_upload_scenario_registry import (
    approve_upload_scenario,
    approved_upload_scenario,
    list_upload_scenarios,
    register_upload_scenario,
    revoke_upload_scenario,
)
from ai_test_asset_center.ui_upload_scenario_runtime_binding import _hydrate_scenarios
from ai_test_asset_center.ui_upload_scenario_semantic_authority import (
    install_ui_upload_scenario_semantic_authority,
)
from ai_test_asset_center.ui_upload_scenario_source_authority import (
    install_ui_upload_scenario_source_authority,
)

_PROJECT = "upload-scenario-test"
_ACTOR = {"name": "qa-owner", "role": "qa_lead"}
_SAFE_INTERFACE = "api:GET:/customers/upload"
_WRITE_INTERFACE = "api:POST:/customers/upload"
_SOURCE_FILENAME = "upload-ui.md"


def _seed_knowledge(tmp_path: Path) -> dict[str, object]:
    openapi = {
        "openapi": "3.0.0",
        "info": {"title": "Upload API", "version": "1.0.0"},
        "paths": {
            "/customers/upload": {
                "get": {
                    "operationId": "getCustomerUploadPage",
                    "summary": "Read upload page state",
                    "responses": {"200": {"description": "ok"}},
                },
                "post": {
                    "operationId": "submitCustomerUpload",
                    "summary": "Submit upload",
                    "responses": {"200": {"description": "ok"}},
                },
            }
        },
    }
    permissions = {
        "permissions": [
            {
                "role": "admin",
                "resource": "/customers/upload",
                "actions": ["read"],
                "decision": "allow",
            }
        ]
    }
    result = ingest_enterprise_knowledge_documents(
        _PROJECT,
        [
            {
                "text": "# Bulk upload\nAdmin uploads a CSV and sees 上传成功.",
                "filename": _SOURCE_FILENAME,
                "source_type": "uiux_spec",
            },
            {
                "text": json.dumps(openapi),
                "filename": "upload-openapi.json",
                "source_type": "openapi",
            },
            {
                "text": json.dumps(permissions),
                "filename": "upload-permissions.json",
                "source_type": "permission_matrix",
            },
        ],
        root=tmp_path,
        actor=_ACTOR,
    )
    assert result["ok"] is True
    return next(
        row
        for row in result["created"]
        if row.get("original_name") == _SOURCE_FILENAME
    )


def _replace_ui_source(tmp_path: Path) -> dict[str, object]:
    result = ingest_enterprise_knowledge_documents(
        _PROJECT,
        [
            {
                "text": "# Bulk upload v2\nThe upload contract has changed.",
                "filename": _SOURCE_FILENAME,
                "source_type": "uiux_spec",
            }
        ],
        root=tmp_path,
        actor=_ACTOR,
    )
    assert result["ok"] is True
    return result["created"][0]


def _fixture(tmp_path: Path) -> dict[str, object]:
    source = tmp_path / "platform_inputs" / _PROJECT / "inbox" / "sample.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("id,name\n1,Alice\n", encoding="utf-8")
    registered = register_upload_fixture(
        _PROJECT,
        file_path=source,
        fixture_name="sample-csv",
        root=tmp_path,
        actor=_ACTOR,
    )
    return approve_upload_fixture(
        _PROJECT,
        fixture_id=registered["fixture"]["fixture_id"],
        root=tmp_path,
        actor=_ACTOR,
    )["fixture"]


def _payload(source_id: str, binding_ref: str) -> dict[str, object]:
    return {
        "title": "客户批量上传",
        "source_id": source_id,
        "source_locator": "heading:Bulk upload",
        "operation_ref": _SAFE_INTERFACE,
        "actor_role": "admin",
        "start_url": "/customers/upload",
        "fixture_binding_refs": [binding_ref],
        "upload_selector": "input[type=file]",
        "submission_mode": "click_submit",
        "submit_selector": "#upload-submit",
        "cleanup_selector": "#remove-upload",
        "assertion_selector": "#upload-result",
        "assertion_text": "上传成功",
        "rendered_probe_selector": "#upload-result",
        "persistent_probe_url": "/api/customers/import/state",
        "persistent_json_pointer": "/count",
    }


def _approved_scenario(
    tmp_path: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    install_upload_fixture_registry_integrity()
    install_ui_upload_scenario_source_authority()
    install_ui_upload_scenario_semantic_authority()
    source = _seed_knowledge(tmp_path)
    fixture = _fixture(tmp_path)
    candidate = register_upload_scenario(
        _PROJECT,
        _payload(str(source["source_id"]), str(fixture["binding_ref"])),
        root=tmp_path,
        actor=_ACTOR,
    )["scenario"]
    approved = approve_upload_scenario(
        _PROJECT,
        scenario_id=str(candidate["scenario_id"]),
        root=tmp_path,
        actor=_ACTOR,
    )["scenario"]
    return source, candidate, approved


def test_approved_scenario_materializes_semantically_bound_request(tmp_path: Path) -> None:
    source, _candidate, approved = _approved_scenario(tmp_path)

    materialized = approved_upload_scenario(
        _PROJECT,
        str(approved["scenario_ref"]),
        root=tmp_path,
    )

    request = materialized["ui_execution_request"]
    assert request["provider"] == "playwright_browser_plan"
    assert request["execution_mode"] == "approved_sandbox_write"
    assert request["source_refs"][0]["source_id"] == source["source_id"]
    assert request["source_refs"][0]["version"] == (
        f"knowledge-source:{source['source_id']}:v{source['version']}"
    )
    assert request["operation_ref"] == _SAFE_INTERFACE
    assert request["actor_role"] == "admin"
    assert "actor_ref" not in request
    assert request["metadata"]["safe_prerequisite_operation_bound"] is True
    assert request["metadata"]["prerequisite_method"] == "GET"
    assert request["metadata"]["upload_submission_mode"] == "click_submit"
    assert request["metadata"]["upload_persistent_compensation_required"] is True
    assert request["browser_plan"]["interaction_contract"]["equivalence_scope"] == (
        "rendered_and_persistent_state"
    )
    steps = request["browser_plan"]["steps"]
    assert [(row["phase"], row["action"]) for row in steps] == [
        ("setup", "goto"),
        ("treatment", "set_input_files"),
        ("treatment", "click"),
        ("assertion", "expect_text"),
        ("cleanup", "click"),
        ("cleanup", "set_input_files"),
    ]
    assert steps[2]["selector"] == "#upload-submit"
    assert steps[4]["selector"] == "#remove-upload"
    assert steps[-1]["file_refs"] == []
    assert materialized["raw_fixture_paths_embedded"] is False


def test_auto_submission_omits_submit_click_but_keeps_compensation(
    tmp_path: Path,
) -> None:
    install_upload_fixture_registry_integrity()
    install_ui_upload_scenario_source_authority()
    install_ui_upload_scenario_semantic_authority()
    source = _seed_knowledge(tmp_path)
    fixture = _fixture(tmp_path)
    payload = _payload(str(source["source_id"]), str(fixture["binding_ref"]))
    payload["submission_mode"] = "auto_on_file_selection"
    payload.pop("submit_selector")

    candidate = register_upload_scenario(
        _PROJECT, payload, root=tmp_path, actor=_ACTOR
    )["scenario"]
    approved = approve_upload_scenario(
        _PROJECT,
        scenario_id=str(candidate["scenario_id"]),
        root=tmp_path,
        actor=_ACTOR,
    )["scenario"]
    request = approved_upload_scenario(
        _PROJECT, str(approved["scenario_ref"]), root=tmp_path
    )["ui_execution_request"]
    steps = request["browser_plan"]["steps"]

    assert request["metadata"]["upload_submission_mode"] == "auto_on_file_selection"
    assert [(row["phase"], row["action"]) for row in steps] == [
        ("setup", "goto"),
        ("treatment", "set_input_files"),
        ("assertion", "expect_text"),
        ("cleanup", "click"),
        ("cleanup", "set_input_files"),
    ]
    assert steps[3]["selector"] == "#remove-upload"


def test_submission_and_business_cleanup_are_explicitly_required(tmp_path: Path) -> None:
    install_upload_fixture_registry_integrity()
    install_ui_upload_scenario_source_authority()
    install_ui_upload_scenario_semantic_authority()
    source = _seed_knowledge(tmp_path)
    fixture = _fixture(tmp_path)
    base = _payload(str(source["source_id"]), str(fixture["binding_ref"]))

    missing_cleanup = dict(base)
    missing_cleanup.pop("cleanup_selector")
    with pytest.raises(ValueError, match="cleanup_selector_required"):
        register_upload_scenario(
            _PROJECT, missing_cleanup, root=tmp_path, actor=_ACTOR
        )

    invalid_auto = dict(base)
    invalid_auto["submission_mode"] = "auto_on_file_selection"
    with pytest.raises(ValueError, match="submit_selector_not_allowed_for_auto"):
        register_upload_scenario(
            _PROJECT, invalid_auto, root=tmp_path, actor=_ACTOR
        )


def test_runtime_hydration_merges_formal_request_and_fixture_refs(tmp_path: Path) -> None:
    _source_row, _candidate, approved = _approved_scenario(tmp_path)

    prepared = _hydrate_scenarios(
        _PROJECT,
        tmp_path,
        {"ui_upload_scenario_ids": [approved["scenario_ref"]]},
    )

    assert len(prepared["ui_execution_requests"]) == 1
    assert prepared["ui_execution_requests"][0]["request_id"].startswith("ui_upload_")
    assert prepared["ui_upload_fixture_ids"] == approved["fixture_binding_refs"]
    summary = prepared["ui_upload_scenario_binding_summary"]
    assert summary["scenario_count"] == 1
    assert summary["registry_derived"] is True
    assert summary["request_ids"] == [
        prepared["ui_execution_requests"][0]["request_id"]
    ]


def test_write_prerequisite_operation_is_rejected_at_registration(tmp_path: Path) -> None:
    install_ui_upload_scenario_source_authority()
    install_ui_upload_scenario_semantic_authority()
    source = _seed_knowledge(tmp_path)
    fixture = _fixture(tmp_path)
    payload = _payload(str(source["source_id"]), str(fixture["binding_ref"]))
    payload["operation_ref"] = _WRITE_INTERFACE

    with pytest.raises(ValueError, match="must_be_safe_read"):
        register_upload_scenario(
            _PROJECT, payload, root=tmp_path, actor=_ACTOR
        )


def test_unknown_role_and_actor_ref_are_rejected_at_registration(tmp_path: Path) -> None:
    install_ui_upload_scenario_source_authority()
    install_ui_upload_scenario_semantic_authority()
    source = _seed_knowledge(tmp_path)
    fixture = _fixture(tmp_path)
    payload = _payload(str(source["source_id"]), str(fixture["binding_ref"]))
    payload["actor_role"] = "invented_role"
    with pytest.raises(ValueError, match="actor_role_not_source_declared"):
        register_upload_scenario(
            _PROJECT, payload, root=tmp_path, actor=_ACTOR
        )

    payload["actor_role"] = "admin"
    payload["actor_ref"] = "bir_invented_actor"
    with pytest.raises(ValueError, match="actor_ref_not_source_stable"):
        register_upload_scenario(
            _PROJECT, payload, root=tmp_path, actor=_ACTOR
        )


def test_source_version_change_blocks_old_approved_scenario(tmp_path: Path) -> None:
    _source_row, _candidate, approved = _approved_scenario(tmp_path)
    _replace_ui_source(tmp_path)

    with pytest.raises(RuntimeError, match="source_version_changed"):
        approved_upload_scenario(
            _PROJECT,
            str(approved["scenario_ref"]),
            root=tmp_path,
        )


def test_fixture_revocation_blocks_old_approved_scenario(tmp_path: Path) -> None:
    _source_row, _candidate, approved = _approved_scenario(tmp_path)
    fixture_ref = str(approved["fixture_binding_refs"][0])
    from ai_test_asset_center.ui_upload_fixture_registry import active_approved_upload_fixture

    fixture = active_approved_upload_fixture(_PROJECT, fixture_ref, root=tmp_path)
    assert fixture is not None
    revoke_upload_fixture(
        _PROJECT,
        fixture_id=str(fixture["fixture_id"]),
        reason="fixture retired",
        root=tmp_path,
        actor=_ACTOR,
    )

    with pytest.raises(KeyError, match="active_approved_upload_fixture_not_found"):
        approved_upload_scenario(
            _PROJECT,
            str(approved["scenario_ref"]),
            root=tmp_path,
        )


def test_candidate_revocation_cascades_to_approved_copy(tmp_path: Path) -> None:
    _source_row, candidate, approved = _approved_scenario(tmp_path)

    result = revoke_upload_scenario(
        _PROJECT,
        scenario_id=str(candidate["scenario_id"]),
        reason="source contract replaced",
        root=tmp_path,
        actor=_ACTOR,
    )

    assert result["status"] == "REVOKED"
    assert len(result["revoked_records"]) == 2
    listing = list_upload_scenarios(
        _PROJECT,
        root=tmp_path,
        include_revoked=True,
    )
    row = next(
        item
        for item in listing["scenarios"]
        if item.get("scenario_id") == approved["scenario_id"]
    )
    assert row["status"] == "revoked"
