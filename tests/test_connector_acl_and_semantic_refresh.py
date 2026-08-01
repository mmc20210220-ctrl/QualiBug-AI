import json

import pytest

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
    list_enterprise_knowledge_sources,
)
from ai_test_asset_center.enterprise_knowledge_center.source_occurrence_observation import (
    list_source_occurrence_observations,
)
from ai_test_asset_center.private_pilot_connector_handlers import _sanitize_sync_response


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


def _item(acl: dict) -> dict:
    return {
        "remote_resource_id": "doc-1",
        "resource_kind": "document",
        "source_type": "prd",
        "content": "source-backed content",
        "filename": "requirements.md",
        "remote_revision": "1",
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


def test_sync_records_acl_visibility_and_semantic_pending_receipt(tmp_path):
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
    assert first["semantic_refresh_status"] == "PENDING_VALIDATION"
    events = first["semantic_refresh_receipt"]["source_occurrence_diff"]["events"]
    assert [row["event"] for row in events] == ["SOURCE_CREATED"]

    observations = list_source_occurrence_observations(PROJECT, root=tmp_path)
    occurrence = observations["source_occurrences"][0]
    metadata_text = json.dumps(occurrence["source_metadata"], ensure_ascii=False)
    assert "acl_fingerprint" in occurrence["source_metadata"]
    assert PRINCIPAL not in metadata_text

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
