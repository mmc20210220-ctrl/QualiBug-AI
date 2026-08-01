from __future__ import annotations

from typing import Any

from ai_test_asset_center.private_pilot_connector_handlers import (
    KnowledgeConnectorHandlersMixin,
)


PROJECT = "enterprise-project"
CONNECTOR = "connector-main-feishu"
ACTOR = {"name": "qa-owner", "role": "qa_lead"}


class DummyHandler(KnowledgeConnectorHandlersMixin):
    def _json(self, body, status=200, extra_headers=None):
        return {"status": status, "body": body, "headers": extra_headers or {}}


def _row() -> dict[str, Any]:
    return {
        "connector_instance_id": CONNECTOR,
        "connector_type": "feishu",
        "display_name": "Online materials",
        "resource_scope": "wiki-all-accessible",
        "status": "ACTIVE",
        "connection_profile": {
            "connector_instance_id": CONNECTOR,
            "auth_mode": "internal_app",
            "configured_fields": {"app_id": True, "app_secret": True},
            "credentials_configured": True,
            "checkpoint_configured": False,
            "reauthorization_required": False,
            "plaintext_returned": False,
        },
        "coverage": {
            "status": "COMPLETE",
            "complete": True,
            "discovered_count": 2,
            "covered_count": 2,
            "unsupported_count": 0,
            "coverage_ratio": 1.0,
            "unsupported_resources": [],
            "source_content_returned": False,
            "customer_material_mutation_executed": False,
        },
        "acceptance": {
            "status": "NOT_RUN",
            "acceptance_ready": False,
            "latest_report": None,
        },
    }


def test_generic_read_routes_project_safe_projections(monkeypatch, tmp_path):
    import ai_test_asset_center.private_pilot_connector_handlers as handlers

    row = _row()
    monkeypatch.setattr(
        handlers,
        "_connector_inventory",
        lambda project, root: {
            "connectors": [row],
            "summary": {},
            "governance": {"connection_profiles_masked": True},
        },
    )
    monkeypatch.setattr(
        handlers,
        "_connector_resources_projection",
        lambda project, connector, root: {
            "schema": "qualibug.knowledge-connector-resources.v1",
            "project_id": project,
            "connector_instance_id": connector,
            "resources": [],
            "source_content_returned": False,
            "raw_cursor_returned": False,
            "credential_values_returned": False,
            "remote_resource_identities_returned": False,
            "source_refs_returned": False,
        },
    )
    monkeypatch.setattr(
        handlers,
        "list_connector_sync_runs",
        lambda project, **kwargs: {
            "project_id": project,
            "connector_instance_id": kwargs["connector_instance_id"],
            "runs": [],
            "raw_cursor_returned": False,
            "source_content_returned": False,
            "credential_values_returned": False,
        },
    )

    handler = DummyHandler()
    monkeypatch.setattr(
        handlers,
        "project_connector_webhook",
        lambda project, connector, root: {
            "connector_instance_id": connector,
            "status": "ENABLED",
            "supported": True,
            "enabled": True,
        },
    )
    for tail in (
        [CONNECTOR, "resources"],
        [CONNECTOR, "coverage"],
        [CONNECTOR, "runs"],
        [CONNECTOR, "acceptance"],
        [CONNECTOR, "webhook"],
    ):
        result = handler._handle_knowledge_connector_get(PROJECT, list(tail), tmp_path)
        assert result["status"] == 200
        assert result["body"]["ok"] is True
        assert result["body"]["data"]["connector_instance_id"] == CONNECTOR


def test_webhook_post_uses_event_authority_and_returns_async_projection(monkeypatch, tmp_path):
    import ai_test_asset_center.private_pilot_connector_handlers as handlers

    captured: dict[str, Any] = {}

    def fake_receive(project, connector, **kwargs):
        captured.update({"project": project, "connector": connector, **kwargs})
        return {
            "status": "SYNC_TRIGGERED",
            "accepted": True,
            "event": {"event_record_id": "webhook_evt_1"},
            "sync": {"status": "COMPLETE", "raw_cursor_returned": False},
        }

    monkeypatch.setattr(handlers, "receive_connector_webhook", fake_receive)
    handler = DummyHandler()
    handler.headers = {"X-Webhook-Signature": "fingerprint-only"}

    result = handler._handle_connector_webhook(
        PROJECT,
        CONNECTOR,
        tmp_path,
        b'{"event":"changed"}',
    )

    assert result["status"] == 202
    assert result["body"]["ok"] is True
    assert captured["project"] == PROJECT
    assert captured["connector"] == CONNECTOR
    assert captured["body"] == b'{"event":"changed"}'
    assert captured["headers"] == handler.headers
    assert result["body"]["raw_cursor_returned"] is False


def test_pause_and_resume_use_the_existing_connector_registry(monkeypatch, tmp_path):
    import ai_test_asset_center.private_pilot_connector_handlers as handlers

    observed: list[str] = []

    def fake_status(project, **kwargs):
        observed.append(kwargs["status"])
        return {"connector_instance": {**_row(), "status": kwargs["status"]}}

    monkeypatch.setattr(handlers, "set_managed_connector_status", fake_status)
    monkeypatch.setattr(handlers, "_connector_inventory_row", lambda *args: _row())
    handler = DummyHandler()

    paused = handler._handle_knowledge_connector_action(
        PROJECT, CONNECTOR, "pause", {}, tmp_path, ACTOR
    )
    resumed = handler._handle_knowledge_connector_action(
        PROJECT, CONNECTOR, "resume", {}, tmp_path, ACTOR
    )

    assert paused["status"] == 200
    assert resumed["status"] == 200
    assert observed == ["PAUSED", "ACTIVE"]


def test_patch_preserves_masked_credentials_and_updates_generic_instance(monkeypatch, tmp_path):
    import ai_test_asset_center.private_pilot_connector_handlers as handlers

    captured: dict[str, Any] = {}

    def fake_configure(project, **kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "created": False,
            "connector_instance": {**_row(), "display_name": kwargs["display_name"]},
            "connection_profile": {"credentials_configured": True, "plaintext_returned": False},
            "credential_storage": {"mode": "encrypted_at_rest"},
        }

    monkeypatch.setattr(handlers, "_connector_inventory_row", lambda *args: _row())
    monkeypatch.setattr(handlers, "configure_managed_connector", fake_configure)
    result = DummyHandler()._handle_knowledge_connector_patch(
        PROJECT,
        CONNECTOR,
        {"display_name": "Renamed connector"},
        tmp_path,
        ACTOR,
    )

    assert result["status"] == 200
    assert captured["connector_type"] == "feishu"
    assert captured["display_name"] == "Renamed connector"
    assert captured["profile"] == {
        "auth_mode": "internal_app",
        "app_id": "********",
        "app_secret": "********",
    }


def test_configure_without_nested_profile_uses_selected_manifest_fields(monkeypatch, tmp_path):
    import ai_test_asset_center.private_pilot_connector_handlers as handlers

    captured: dict[str, Any] = {}

    def fake_configure(project, **kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "created": True,
            "connector_instance": {"connector_type": kwargs["connector_type"]},
            "connection_profile": {"credentials_configured": True},
            "credential_storage": {"mode": "encrypted_at_rest"},
        }

    monkeypatch.setattr(handlers, "configure_managed_connector", fake_configure)
    result = DummyHandler()._handle_knowledge_connector_configure(
        PROJECT,
        {
            "connector_instance_id": "openapi-main",
            "connector_type": "openapi",
            "resource_scope": '{"document_urls":["https://api.example.com/openapi.json"]}',
            "auth_mode": "bearer_token",
            "token": "opaque-token",
        },
        tmp_path,
        ACTOR,
    )

    assert result["status"] == 201
    assert captured["connector_type"] == "openapi"
    assert captured["profile"] == {
        "auth_mode": "bearer_token",
        "token": "opaque-token",
    }


def test_git_configure_uses_the_shared_manifest_and_preserves_token_boundary(monkeypatch, tmp_path):
    import ai_test_asset_center.private_pilot_connector_handlers as handlers

    captured: dict[str, Any] = {}

    def fake_configure(project, **kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "created": True,
            "connector_instance": {"connector_type": kwargs["connector_type"]},
            "connection_profile": {"credentials_configured": True},
            "credential_storage": {"mode": "encrypted_at_rest"},
        }

    monkeypatch.setattr(handlers, "configure_managed_connector", fake_configure)
    result = DummyHandler()._handle_knowledge_connector_configure(
        PROJECT,
        {
            "connector_instance_id": "gitlab-main",
            "connector_type": "gitlab",
            "resource_scope": '{"repository_url":"https://gitlab.com/acme/orders","branch":"main"}',
            "auth_mode": "personal_access_token",
            "token": "opaque-token",
        },
        tmp_path,
        ACTOR,
    )

    assert result["status"] == 201
    assert captured["connector_type"] == "gitlab"
    assert captured["profile"] == {
        "auth_mode": "personal_access_token",
        "token": "opaque-token",
    }
