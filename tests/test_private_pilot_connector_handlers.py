from __future__ import annotations

import json

from ai_test_asset_center.private_pilot_connector_handlers import (
    KnowledgeConnectorHandlersMixin,
    _connector_route,
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


def test_sync_action_uses_server_checkpoint_and_commits_new_checkpoint(
    tmp_path,
    monkeypatch,
):
    import ai_test_asset_center.private_pilot_connector_handlers as handlers

    observed = {}
    old_checkpoint = "feishu-snapshot-v1:" + "1" * 64
    new_checkpoint = "feishu-snapshot-v1:" + "2" * 64

    monkeypatch.setattr(
        handlers,
        "load_connector_sync_checkpoint",
        lambda project, connector, root: old_checkpoint,
    )

    def fake_sync(project, **kwargs):
        observed["project"] = project
        observed.update(kwargs)
        return {
            "status": "COMPLETE",
            "sync_epoch_id": "sync_epoch_2",
            "next_cursor": new_checkpoint,
            "success_count": 2,
        }

    monkeypatch.setattr(handlers, "sync_feishu_connector", fake_sync)

    def fake_commit(project, connector, checkpoint, **kwargs):
        observed["committed"] = {
            "project": project,
            "connector": connector,
            "checkpoint": checkpoint,
            **kwargs,
        }
        return {"ok": True}

    monkeypatch.setattr(
        handlers,
        "commit_connector_sync_checkpoint",
        fake_commit,
    )

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
    assert observed["previous_cursor"] == old_checkpoint
    assert observed["committed"]["checkpoint"] == new_checkpoint
    assert observed["committed"]["sync_epoch_id"] == "sync_epoch_2"
    assert result["status"] == 200
    assert result["body"]["ok"] is True
    serialized = json.dumps(result["body"], ensure_ascii=False, sort_keys=True)
    assert old_checkpoint not in serialized
    assert new_checkpoint not in serialized


def test_non_complete_sync_does_not_advance_encrypted_checkpoint(
    tmp_path,
    monkeypatch,
):
    import ai_test_asset_center.private_pilot_connector_handlers as handlers

    monkeypatch.setattr(
        handlers,
        "load_connector_sync_checkpoint",
        lambda project, connector, root: "",
    )
    monkeypatch.setattr(
        handlers,
        "sync_feishu_connector",
        lambda *args, **kwargs: {
            "status": "FAILED",
            "sync_epoch_id": "sync_failed",
            "next_cursor": "must-not-commit",
            "errors": [{"code": "FEISHU_EXPORT_FAILED"}],
        },
    )
    committed = False

    def fail_if_committed(*args, **kwargs):
        nonlocal committed
        committed = True

    monkeypatch.setattr(
        handlers,
        "commit_connector_sync_checkpoint",
        fail_if_committed,
    )

    result = DummyHandler()._handle_knowledge_connector_action(
        PROJECT,
        CONNECTOR,
        "sync",
        {},
        tmp_path,
        ACTOR,
    )

    assert committed is False
    assert result["status"] == 409
    assert result["body"]["ok"] is False
    assert "must-not-commit" not in json.dumps(result["body"])


def test_private_pilot_handler_composes_connector_router_before_canonical_router():
    from ai_test_asset_center.private_pilot_http_routing import HttpRoutingMixin
    from ai_test_asset_center.private_pilot_service import PrivatePilotHandler

    mro = list(PrivatePilotHandler.__mro__)
    assert mro.index(KnowledgeConnectorHandlersMixin) < mro.index(HttpRoutingMixin)
