import json

import pytest

from ai_test_asset_center.enterprise_knowledge_center import _crud as knowledge_crud
from ai_test_asset_center.connector_acl_authority import (
    ConnectorAclError,
    connector_source_visibility_decision,
    filter_connector_asset_for_actor,
    fingerprint_connector_principal,
    normalize_connector_acl_snapshot,
    record_connector_project_share,
)
from ai_test_asset_center.connector_source_ingestion import build_connector_source_ref
from ai_test_asset_center.connector_sync_authority import (
    _load_connector_registry,
    list_connector_instances,
    register_connector_instance,
    sync_connector_snapshot_batch,
)
from ai_test_asset_center.enterprise_knowledge_center import (
    load_enterprise_business_knowledge_asset,
    list_enterprise_knowledge_sources,
)
from ai_test_asset_center.enterprise_knowledge_center.source_occurrence_observation import (
    list_source_occurrence_observations,
)
from ai_test_asset_center.private_pilot_connector_handlers import (
    _connector_resources_projection,
    _sanitize_sync_response,
)


PROJECT = "enterprise-project"
CONNECTOR = "generic-docs"
ACTOR = {"name": "qa-owner", "role": "qa_lead", "project_id": PROJECT}
PRINCIPAL = "alice@example.invalid"


def _acl(*, complete: bool = True, availability: str = "AVAILABLE") -> dict:
    return {
        "acl_version": "acl-v1",
        "principals": [{"principal_ref": PRINCIPAL, "type": "user"}],
        "visibility": "PRIVATE",
        "inherited_from": "space-root",
        "captured_at": "2026-08-02T00:00:00Z",
        "complete": complete,
        "availability": availability,
    }


def _item(
    acl: dict,
    *,
    remote_id: str = "doc-1",
    content: str = "source-backed content",
    revision: str = "1",
) -> dict:
    return {
        "remote_resource_id": remote_id,
        "resource_kind": "document",
        "source_type": "prd",
        "content": content,
        "filename": "requirements.md",
        "remote_revision": revision,
        "remote_updated_at": "2026-08-02T00:00:00Z",
        "acl": acl,
    }


def _register(tmp_path):
    return register_connector_instance(
        PROJECT,
        root=tmp_path,
        connector_instance_id=CONNECTOR,
        connector_type="generic-docs",
        display_name="Generic docs",
        resource_scope="declared-scope",
        connection_profile_ref="vault-ref://connectors/generic-docs",
        actor=ACTOR,
    )


def test_acl_snapshot_is_source_bound_and_raw_principals_never_persisted():
    snapshot = normalize_connector_acl_snapshot(
        PROJECT,
        CONNECTOR,
        source_ref="connector://generic-docs/document/doc-1",
        raw={"acl": _acl()},
        availability_default="AVAILABLE",
        captured_at_default="2026-08-02T00:00:00Z",
    )

    assert snapshot["propagation_allowed"] is True
    assert snapshot["evidence_status"] == "COMPLETE"
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    assert PRINCIPAL not in encoded
    assert snapshot["principals"][0]["principal_fingerprint"] == (
        fingerprint_connector_principal(PROJECT, CONNECTOR, PRINCIPAL)
    )

    incomplete = normalize_connector_acl_snapshot(
        PROJECT,
        CONNECTOR,
        source_ref="connector://generic-docs/document/doc-1",
        raw={"acl": _acl(complete=False)},
        availability_default="AVAILABLE",
        captured_at_default="2026-08-02T00:00:00Z",
    )
    assert incomplete["propagation_allowed"] is False
    assert incomplete["evidence_status"] == "INCOMPLETE"


def test_sync_records_acl_visibility_and_executes_incremental_refresh(tmp_path):
    _register(tmp_path)
    first = sync_connector_snapshot_batch(
        PROJECT,
        root=tmp_path,
        connector_instance_id=CONNECTOR,
        items=[_item(_acl())],
        next_cursor="cursor-1",
        actor=ACTOR,
    )

    source_ref = build_connector_source_ref(
        CONNECTOR,
        "doc-1",
        resource_kind="document",
    )
    assert first["acl_propagation_status"] == "READY"
    assert first["acl_snapshot_receipt"]["snapshot_count"] == 1
    assert first["acl_snapshot_receipt"]["propagation_allowed_count"] == 1
    assert first["semantic_refresh_status"] == "EXECUTED"
    assert first["semantic_refresh_receipt"]["incremental_executor_installed"] is True
    assert first["semantic_refresh_receipt"]["completion_reason"] == (
        "INITIAL_ASSET_BUILD_EXECUTED"
    )
    events = first["semantic_refresh_receipt"]["source_occurrence_diff"]["events"]
    assert [row["event"] for row in events] == ["SOURCE_CREATED"]
    assert all(
        row["executed"] is True
        for row in first["semantic_refresh_receipt"]["downstream"]
    )

    observations = list_source_occurrence_observations(PROJECT, root=tmp_path)
    occurrence = observations["source_occurrences"][0]
    metadata_text = json.dumps(occurrence["source_metadata"], ensure_ascii=False)
    assert "acl_fingerprint" in occurrence["source_metadata"]
    assert PRINCIPAL not in metadata_text

    source_inventory = list_enterprise_knowledge_sources(PROJECT, root=tmp_path)
    source = source_inventory["sources"][0]
    assert source["source_origin"] == "ONLINE_CONNECTOR"
    assert source["source_updated_at"] == "2026-08-02T00:00:00Z"
    assert source["updated_at_utc"]
    assert source["permission_scope"] == {
        "visibility": "PRIVATE",
        "availability": "AVAILABLE",
        "evidence_status": "COMPLETE",
        "acl_version": "acl-v1",
        "complete": True,
        "propagation_allowed": True,
        "raw_remote_principals_returned": False,
    }
    assert PRINCIPAL not in json.dumps(source, ensure_ascii=False)

    resource_projection = _connector_resources_projection(
        PROJECT,
        CONNECTOR,
        tmp_path,
    )
    resource = resource_projection["resources"][0]
    assert resource["updated_at_utc"] == source["updated_at_utc"]
    assert resource["permission_scope"]["visibility"] == "PRIVATE"
    assert "remote_resource_id" not in json.dumps(resource, ensure_ascii=False)

    principal_fp = fingerprint_connector_principal(PROJECT, CONNECTOR, PRINCIPAL)
    allowed = connector_source_visibility_decision(
        PROJECT,
        source_ref=source_ref,
        actor={"name": "alice", "connector_principal_fingerprints": [principal_fp]},
        root=tmp_path,
    )
    denied = connector_source_visibility_decision(
        PROJECT,
        source_ref=source_ref,
        actor={"name": "bob", "connector_principal_fingerprints": []},
        root=tmp_path,
    )
    assert allowed["allowed"] is True
    assert denied["allowed"] is False
    assert denied["reason_code"] == "REMOTE_PRINCIPAL_NOT_MATCHED"

    asset = {
        "source_inventory": [
            {"source_ref": source_ref, "source_id": "canonical-doc-1", "name": "Private"},
            {"source_ref": "upload://public", "source_id": "public", "name": "Public"},
        ],
        "content_blocks": [
            {"source_ref": source_ref, "text": "private source-backed block"},
            {"source_ref": "upload://public", "text": "public block"},
        ],
        "document_structure_assets": [
            {
                "blocks": [
                    {"source_ref": source_ref, "text": "private nested block"},
                    {"source_ref": "upload://public", "text": "public nested block"},
                ]
            }
        ],
        "summary": {"active_source_count": 2},
    }
    projected = filter_connector_asset_for_actor(
        PROJECT,
        asset,
        actor={"name": "bob", "connector_principal_fingerprints": []},
        root=tmp_path,
    )
    projected_text = json.dumps(projected, ensure_ascii=False)
    assert len(projected["source_inventory"]) == 1
    assert "private source-backed block" not in projected_text
    assert "private nested block" not in projected_text
    assert "public nested block" in projected_text

    visible_asset = filter_connector_asset_for_actor(
        PROJECT,
        asset,
        actor={
            "name": "alice",
            "connector_principal_fingerprints": [principal_fp],
        },
        root=tmp_path,
    )
    visible_text = json.dumps(visible_asset, ensure_ascii=False)
    assert "connector://generic-docs/document/doc-1" not in visible_text
    assert "source_identity_fingerprints" in visible_text
    assert '"remote_resource_id":' not in visible_text
    visible_source = visible_asset["source_inventory"][0]
    assert visible_source["source_origin"] == "ONLINE_CONNECTOR"
    assert len(visible_source["source_identity_fingerprints"]) == 1
    assert visible_source["remote_resource_identities_returned"] is False

    shared = record_connector_project_share(
        PROJECT,
        source_ref=source_ref,
        root=tmp_path,
        actor=ACTOR,
    )
    assert shared["visibility"] == "PROJECT"
    shared_decision = connector_source_visibility_decision(
        PROJECT,
        source_ref=source_ref,
        actor={"name": "project-user", "project_id": PROJECT},
        root=tmp_path,
    )
    assert shared_decision["allowed"] is True
    assert first["sync_epoch_id"]


def test_unchanged_acl_does_not_emit_permission_change_or_reanalyze(tmp_path):
    _register(tmp_path)
    first = sync_connector_snapshot_batch(
        PROJECT,
        root=tmp_path,
        connector_instance_id=CONNECTOR,
        items=[_item(_acl())],
        next_cursor="cursor-1",
        actor=ACTOR,
    )
    second = sync_connector_snapshot_batch(
        PROJECT,
        root=tmp_path,
        connector_instance_id=CONNECTOR,
        items=[],
        unchanged_observations=[
            {
                "remote_resource_id": "doc-1",
                "resource_kind": "document",
                "acl": _acl(),
            }
        ],
        previous_cursor="cursor-1",
        next_cursor="cursor-2",
        actor=ACTOR,
    )

    assert second["acl_snapshot_receipt"]["changed_count"] == 0
    assert second["semantic_refresh_status"] == "NO_CHANGE"
    assert second["semantic_refresh_receipt"]["unchanged_materials_reanalyzed"] is False
    assert second["semantic_refresh_receipt"]["source_occurrence_diff"]["event_count"] == 0
    assert first["sync_epoch_id"] != second["sync_epoch_id"]


def test_revision_refresh_reextracts_only_changed_source(tmp_path, monkeypatch):
    _register(tmp_path)
    first_doc = _item(
        _acl(), remote_id="doc-1", content="first source", revision="1"
    )
    first_doc["filename"] = "requirements-1.md"
    second_doc = _item(
        _acl(), remote_id="doc-2", content="second source", revision="1"
    )
    second_doc["filename"] = "requirements-2.md"
    first = sync_connector_snapshot_batch(
        PROJECT,
        root=tmp_path,
        connector_instance_id=CONNECTOR,
        items=[first_doc, second_doc],
        next_cursor="cursor-1",
        actor=ACTOR,
    )
    parse_calls: list[str] = []
    original_record_parse = knowledge_crud._record_parse

    def tracked_record_parse(record, root):
        parse_calls.append(str(record.get("source_id") or ""))
        return original_record_parse(record, root)

    monkeypatch.setattr(knowledge_crud, "_record_parse", tracked_record_parse)
    second = sync_connector_snapshot_batch(
        PROJECT,
        root=tmp_path,
        connector_instance_id=CONNECTOR,
        items=[
            _item(_acl(), remote_id="doc-1", content="updated source", revision="2"),
        ],
        unchanged_observations=[
            {
                "remote_resource_id": "doc-2",
                "resource_kind": "document",
                "acl": _acl(),
                "remote_revision": "1",
            }
        ],
        previous_cursor="cursor-1",
        next_cursor="cursor-2",
        actor=ACTOR,
    )

    assert first["semantic_refresh_status"] == "EXECUTED"
    receipt = second["semantic_refresh_receipt"]
    assert second["semantic_refresh_status"] == "EXECUTED"
    assert receipt["incremental_execution_mode"] == "INCREMENTAL"
    assert receipt["incremental_parsed_source_count"] == 1
    assert receipt["unchanged_materials_reanalyzed"] is False
    assert receipt["full_project_recompute_requested"] is False
    assert len(parse_calls) == 1
    events = receipt["source_occurrence_diff"]["events"]
    assert [row["event"] for row in events] == ["SOURCE_REVISION_CHANGED"]
    assert all(row["executed"] is True for row in receipt["downstream"])


def test_retired_source_keeps_related_behavior_pending_and_records_impact(tmp_path):
    _register(tmp_path)
    api = (
        '{"openapi":"3.0.0","info":{"title":"Orders","version":"1"},'
        '"paths":{"/orders":{"get":{"operationId":"listOrders",'
        '"responses":{"200":{"description":"ok"}}}}}}'
    )
    item = _item(_acl(), content=api, revision="1")
    item["source_type"] = "openapi"
    item["filename"] = "orders.json"
    first = sync_connector_snapshot_batch(
        PROJECT,
        root=tmp_path,
        connector_instance_id=CONNECTOR,
        items=[item],
        next_cursor="cursor-1",
        actor=ACTOR,
    )
    second = sync_connector_snapshot_batch(
        PROJECT,
        root=tmp_path,
        connector_instance_id=CONNECTOR,
        items=[],
        previous_cursor="cursor-1",
        next_cursor="cursor-2",
        sync_mode="FULL",
        snapshot_complete=True,
        deletion_policy="RETIRE_MISSING",
        max_retire_ratio=1.0,
        actor=ACTOR,
    )

    receipt = second["semantic_refresh_receipt"]
    assert first["semantic_refresh_status"] == "EXECUTED"
    assert second["status"] == "COMPLETE"
    assert second["semantic_refresh_status"] == "EXECUTED"
    assert receipt["incremental_execution_mode"] == "METADATA_ONLY"
    assert receipt["pending_validation_count"] >= 1
    assert receipt["affected_behaviors"] >= 1
    assert receipt["semantic_impact_relation_count"] >= 1
    assert receipt["unchanged_materials_reanalyzed"] is False

    asset = load_enterprise_business_knowledge_asset(PROJECT, tmp_path)
    assert asset is not None
    assert asset["source_inventory"] == []
    interfaces = asset["interfaces"]
    assert interfaces
    assert all(
        row.get("semantic_validation_status") == "PENDING_SOURCE_VALIDATION"
        for row in interfaces
    )
    assert asset["incremental_refresh_receipt"]["status"] == "EXECUTED"
    assert asset["incremental_semantic_impact"]["affected_counts"]["behavior"] >= 1


def test_shared_api_artifact_keeps_unchanged_source_record_on_revision(tmp_path):
    _register(tmp_path)

    def _api(description: str) -> str:
        return (
            '{"openapi":"3.0.0","info":{"title":"API","version":"1"},'
            '"paths":{"/shared":{"get":{"operationId":"shared",'
            f'"description":"{description}",'
            '"responses":{"200":{"description":"ok"}}}}}}'
        )

    first_items = []
    for remote_id, description in (("doc-a", "first"), ("doc-b", "second")):
        item = _item(
            _acl(),
            remote_id=remote_id,
            content=_api(description),
            revision="1",
        )
        item["source_type"] = "openapi"
        item["filename"] = f"{remote_id}.json"
        first_items.append(item)
    sync_connector_snapshot_batch(
        PROJECT,
        root=tmp_path,
        connector_instance_id=CONNECTOR,
        items=first_items,
        next_cursor="cursor-1",
        actor=ACTOR,
    )

    changed = _item(
        _acl(),
        remote_id="doc-a",
        content=_api("first changed"),
        revision="2",
    )
    changed["source_type"] = "openapi"
    changed["filename"] = "doc-a.json"
    second = sync_connector_snapshot_batch(
        PROJECT,
        root=tmp_path,
        connector_instance_id=CONNECTOR,
        items=[changed],
        unchanged_observations=[
            {
                "remote_resource_id": "doc-b",
                "resource_kind": "document",
                "acl": _acl(),
                "remote_revision": "1",
            }
        ],
        previous_cursor="cursor-1",
        next_cursor="cursor-2",
        actor=ACTOR,
    )

    assert second["semantic_refresh_status"] == "EXECUTED"
    asset = load_enterprise_business_knowledge_asset(PROJECT, tmp_path)
    assert asset is not None
    interface = next(row for row in asset["interfaces"] if row.get("path") == "/shared")
    source_ids = {
        row.get("source_id")
        for row in interface["api_artifact_source_records"]
    }
    assert len(source_ids) == 2
    assert interface["unchanged_source_records_preserved"] is True


def test_public_sync_projection_does_not_return_acl_identity_details():
    response = _sanitize_sync_response(
        {
            "status": "COMPLETE",
            "semantic_refresh_receipt": {
                "schema": "qualibug.connector-semantic-refresh.v1",
                "status": "PENDING_VALIDATION",
                "sync_epoch_id": "sync-1",
                "source_occurrence_diff": {
                    "event_count": 1,
                    "changed_source_count": 1,
                    "unchanged_source_count": 0,
                    "events": [
                        {
                            "event": "SOURCE_CREATED",
                            "source_ref": "connector://generic-docs/document/doc-1",
                            "source_label": "Requirements",
                        }
                    ],
                },
                "downstream": [],
            },
            "acl_snapshot_receipt": {
                "schema": "qualibug.connector-acl-snapshot.v1",
                "status": "RECORDED",
                "sync_epoch_id": "sync-1",
                "snapshot_count": 1,
                "changed_count": 1,
                "changed": [{"source_ref": "connector://generic-docs/document/doc-1"}],
                "raw_principals_persisted": False,
            },
            "successful_items": [
                {
                    "remote_resource_id": "doc-1",
                    "source_ref": "connector://generic-docs/document/doc-1",
                    "content": "private content",
                }
            ],
            "coverage_observations": [
                {
                    "remote_resource_id": "secret-coverage-id",
                    "source_ref": "connector://generic-docs/document/secret-coverage-id",
                    "resource_kind": "document",
                    "reason_code": "UNSUPPORTED",
                }
            ],
            "errors": [
                {
                    "code": "SYNC_FAILED",
                    "detail": "connector://generic-docs/document/private-error",
                }
            ],
            "run_receipt_path": "private/secret-run-receipt.json",
        }
    )
    encoded = json.dumps(response, ensure_ascii=False)
    assert "connector://generic-docs" not in encoded
    assert "source_ref" not in json.dumps(response["semantic_refresh_receipt"])
    assert "changed" not in response["acl_snapshot_receipt"]
    assert "private content" not in encoded
    assert "secret-coverage-id" not in encoded
    assert "private-error" not in encoded
    assert "secret-run-receipt" not in encoded
    assert response["errors_returned"] is False
    assert response["run_receipt_path_returned"] is False


def test_project_share_requires_current_complete_acl(tmp_path):
    _register(tmp_path)
    source_ref = build_connector_source_ref(
        CONNECTOR,
        "unobserved",
        resource_kind="document",
    )
    with pytest.raises(
        ConnectorAclError,
        match="snapshot_required_for_project_share",
    ):
        record_connector_project_share(
            PROJECT,
            source_ref=source_ref,
            root=tmp_path,
            actor=ACTOR,
        )
