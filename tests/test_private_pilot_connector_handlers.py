from __future__ import annotations

import hashlib
import json

import pytest

from ai_test_asset_center.connector_auto_sync import validate_connector_checkpoint
from ai_test_asset_center.connector_connection_profiles import ConnectorProfileError
from ai_test_asset_center.private_pilot_connector_handlers import (
    KnowledgeConnectorHandlersMixin,
    _connector_route,
    _connector_type_route,
    _sanitize_sync_response,
)

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"
ACTOR = {"name": "qa-owner", "role": "qa_lead"}


class DummyHandler(KnowledgeConnectorHandlersMixin):
    def _json(self, body, status=200, extra_headers=None):
        return {"status": status, "body": body, "headers": extra_headers or {}}


def test_connector_route_is_project_scoped_and_non_connector_routes_fall_through():
    assert _connector_route(
        "/api/v1/projects/enterprise-project/knowledge-connectors"
    ) == (PROJECT, [])
    assert _connector_route(
        "/api/v1/projects/enterprise-project/knowledge-connectors/feishu-prod/sync"
    ) == (PROJECT, ["feishu-prod", "sync"])
    assert _connector_route("/api/knowledge/asset") is None


def test_connector_type_route_is_global_and_manifest_lookup_is_metadata_only():
    assert _connector_type_route("/api/v1/connector-types") == []
    assert _connector_type_route("/api/v1/connector-types/feishu") == ["feishu"]
    assert _connector_type_route("/api/v1/projects/enterprise-project/knowledge-connectors") is None

    catalog = DummyHandler()._handle_connector_type_get([])
    assert catalog["status"] == 200
    assert catalog["body"]["ok"] is True
    assert catalog["body"]["data"]["schema"] == "qualibug.connector-type-catalog.v1"
    assert [row["connector_type"] for row in catalog["body"]["data"]["connector_types"]] == [
        "feishu"
    ]
    assert catalog["body"]["data"]["governance"]["network_access_performed"] is False

    detail = DummyHandler()._handle_connector_type_get(["feishu"])
    assert detail["status"] == 200
    assert detail["body"]["data"]["connector_type"]["connector_type"] == "feishu"


def test_unknown_connector_type_is_fail_visible():
    result = DummyHandler()._handle_connector_type_get(["does-not-exist"])

    assert result["status"] == 404
    assert result["body"] == {
        "ok": False,
        "error": "CONNECTOR_MANIFEST_ERROR",
        "message": "connector_adapter_not_registered:does-not-exist",
    }


def test_sync_response_never_returns_checkpoint_or_source_content():
    sanitized = _sanitize_sync_response(
        {
            "status": "COMPLETE",
            "sync_epoch_id": "sync_1",
            "next_cursor": "feishu-snapshot-v1:" + "a" * 64,
            "source_content_persisted_in_run_receipt": False,
        }
    )
    serialized = json.dumps(sanitized, ensure_ascii=False, sort_keys=True)
    assert "feishu-snapshot-v1" not in serialized
    assert sanitized["next_cursor_returned_to_client"] is False
    assert sanitized["checkpoint_storage"] == "encrypted_connection_profile"
    assert sanitized["source_content_returned"] is False


def test_sync_action_delegates_to_managed_sync_and_sanitizes_response(
    tmp_path,
    monkeypatch,
):
    """Handler delegates to run_managed_feishu_sync and sanitizes the response."""
    import ai_test_asset_center.private_pilot_connector_handlers as handlers

    observed = {}
    new_checkpoint = "feishu-snapshot-v1:" + "2" * 64

    def fake_managed_sync(project, connector, **kwargs):
        observed["project"] = project
        observed["connector"] = connector
        observed.update(kwargs)
        return {
            "status": "COMPLETE",
            "sync_epoch_id": "sync_epoch_2",
            "next_cursor": new_checkpoint,
            "success_count": 2,
        }

    monkeypatch.setattr(handlers, "run_managed_feishu_sync", fake_managed_sync)

    result = DummyHandler()._handle_knowledge_connector_action(
        PROJECT,
        CONNECTOR,
        "sync",
        {
            "deletion_policy": "RETAIN",
            "allow_raw_text_fallback": False,
        },
        tmp_path,
        ACTOR,
    )

    assert observed["project"] == PROJECT
    assert observed["connector"] == CONNECTOR
    assert result["status"] == 200
    assert result["body"]["ok"] is True
    # Checkpoint and cursor must never leak to client
    serialized = json.dumps(result["body"], ensure_ascii=False, sort_keys=True)
    assert new_checkpoint not in serialized
    assert result["body"]["data"]["next_cursor_returned_to_client"] is False


def test_non_complete_sync_returns_409_and_sanitizes_cursor(
    tmp_path,
    monkeypatch,
):
    """Handler returns 409 for failed sync and never leaks cursor."""
    import ai_test_asset_center.private_pilot_connector_handlers as handlers

    monkeypatch.setattr(
        handlers,
        "run_managed_feishu_sync",
        lambda *args, **kwargs: {
            "status": "FAILED",
            "sync_epoch_id": "sync_failed",
            "next_cursor": "must-not-commit",
            "errors": [{"code": "FEISHU_EXPORT_FAILED"}],
        },
    )

    result = DummyHandler()._handle_knowledge_connector_action(
        PROJECT,
        CONNECTOR,
        "sync",
        {},
        tmp_path,
        ACTOR,
    )

    assert result["status"] == 409
    assert result["body"]["ok"] is False
    assert "must-not-commit" not in json.dumps(result["body"])


def test_private_pilot_handler_composes_connector_router_before_canonical_router():
    from ai_test_asset_center.private_pilot_http_routing import HttpRoutingMixin
    from ai_test_asset_center.private_pilot_service import PrivatePilotHandler

    mro = list(PrivatePilotHandler.__mro__)
    assert mro.index(KnowledgeConnectorHandlersMixin) < mro.index(HttpRoutingMixin)


def test_checkpoint_registry_mismatch_blocks_before_remote_sync(tmp_path, monkeypatch):
    """validate_connector_checkpoint raises on fingerprint mismatch."""
    import ai_test_asset_center.connector_auto_sync as auto

    checkpoint = "feishu-snapshot-v1:" + "9" * 64
    monkeypatch.setattr(
        auto,
        "list_connector_instances",
        lambda project, root, include_disabled: {
            "connector_instances": [
                {
                    "connector_instance_id": CONNECTOR,
                    "last_committed_cursor_fingerprint": hashlib.sha256(
                        b"different-checkpoint"
                    ).hexdigest(),
                }
            ]
        },
    )

    with pytest.raises(
        ConnectorProfileError,
        match="checkpoint_registry_mismatch",
    ):
        validate_connector_checkpoint(
            PROJECT,
            CONNECTOR,
            checkpoint,
            root=tmp_path,
        )
