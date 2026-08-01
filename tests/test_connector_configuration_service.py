from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from ai_test_asset_center import connector_configuration_service as service
from ai_test_asset_center import connector_sync_fencing as fencing
from ai_test_asset_center import private_pilot_connector_handlers as handlers


PROJECT = "enterprise-project"
CONNECTOR = "feishu-main"
ACTOR = {"name": "owner", "role": "knowledge_admin"}


def _configured(created: bool) -> dict:
    return {
        "ok": True,
        "created": created,
        "connector_instance": {
            "connector_instance_id": CONNECTOR,
            "connector_type": "feishu",
            "display_name": "飞书企业资料",
            "resource_scope": "wiki-all-accessible",
            "status": "ACTIVE",
        },
        "connection_profile": {
            "connector_instance_id": CONNECTOR,
            "credentials_configured": True,
            "checkpoint_configured": False,
            "plaintext_returned": False,
        },
        "credential_storage": {
            "mode": "encrypted_at_rest",
            "plaintext_returned": False,
        },
    }


def test_first_configuration_uses_canonical_profile_authority_without_takeover(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(service, "_existing_connector", lambda *a, **k: None)
    monkeypatch.setattr(
        service,
        "managed_connector_sync_fence",
        lambda *a, **k: pytest.fail("first creation has no previous writer"),
    )
    monkeypatch.setattr(
        service,
        "recover_connector_checkpoint_commit",
        lambda *a, **k: pytest.fail("first creation has no recovery state"),
    )
    observed: dict[str, object] = {}

    def configure(project, **kwargs):
        observed["project"] = project
        observed.update(kwargs)
        return _configured(True)

    monkeypatch.setattr(service, "configure_feishu_connector", configure)
    result = service.configure_managed_feishu_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        resource_scope="wiki-all-accessible",
        profile={"auth_mode": "internal_app", "app_id": "id", "app_secret": "secret"},
        root=tmp_path,
        actor=ACTOR,
        display_name="飞书企业资料",
    )

    assert result["created"] is True
    assert result["configuration_write_fencing"] == "NOT_REQUIRED_FOR_FIRST_CREATION"
    assert result["previous_writer_revoked"] is False
    assert observed["connector_instance_id"] == CONNECTOR


def test_existing_configuration_forces_fence_recovers_then_updates(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(
        service,
        "_existing_connector",
        lambda *a, **k: {"connector_instance_id": CONNECTOR},
    )
    order: list[str] = []

    @contextmanager
    def managed_fence(*args, **kwargs):
        order.append("fence")
        assert kwargs["force_takeover"] is True
        yield {"takeover": True, "fencing_token": 8}

    def recover(*args, **kwargs):
        order.append("recover")
        assert kwargs["remote_checkpoint_resolver"] is None
        return {
            "sync_lifecycle_recovery": {
                "action": "ABORTED_STRANDED_RUNNING_SYNC",
            }
        }

    def configure(*args, **kwargs):
        order.append("configure")
        return _configured(False)

    monkeypatch.setattr(service, "managed_connector_sync_fence", managed_fence)
    monkeypatch.setattr(service, "recover_connector_checkpoint_commit", recover)
    monkeypatch.setattr(service, "configure_feishu_connector", configure)

    result = service.configure_managed_feishu_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        resource_scope="wiki-space:new-space",
        profile={"auth_mode": "internal_app", "app_id": "id", "app_secret": "new-secret"},
        root=tmp_path,
        actor=ACTOR,
        display_name="飞书企业资料",
    )

    assert order == ["fence", "recover", "configure"]
    assert result["configuration_write_fencing"] == "MONOTONIC_REGISTRY_TOKEN"
    assert result["previous_writer_revoked"] is True
    assert result["previous_sync_recovery_action"] == "ABORTED_STRANDED_RUNNING_SYNC"
    assert result["checkpoint_advanced_by_configuration"] is False
    assert result["previous_snapshots_retained"] is True


def test_force_takeover_can_revoke_a_progressing_owner_for_reconfiguration(
    monkeypatch,
    tmp_path: Path,
):
    instance = {"connector_instance_id": CONNECTOR, "fencing_generation": 4}
    registry = {"connector_instances": [instance], "audit_events": [], "governance": {}}
    fenced: list[dict] = []

    @contextmanager
    def transaction(*args, **kwargs):
        yield {}

    monkeypatch.setattr(fencing, "knowledge_transaction", transaction)
    monkeypatch.setattr(
        fencing,
        "inspect_connector_sync_ownership",
        lambda *a, **k: {
            "state": "ACTIVE",
            "owner_alive": True,
            "owner_dead": False,
            "heartbeat_stale": False,
            "progress_stale": False,
        },
    )
    monkeypatch.setattr(fencing, "_load_connector_registry", lambda *a, **k: registry)
    monkeypatch.setattr(
        fencing,
        "_instance_by_id",
        lambda value, connector: value["connector_instances"][0],
    )
    monkeypatch.setattr(fencing, "_save_connector_registry", lambda *a, **k: None)
    monkeypatch.setattr(
        fencing,
        "fence_out_connector_sync_ownership",
        lambda *a, **k: fenced.append(dict(k)) or {"fenced_out": True},
    )

    lease = fencing.acquire_connector_sync_fence(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
        force_takeover=True,
    )

    assert lease["forced_takeover"] is True
    assert lease["takeover"] is True
    assert lease["fencing_token"] == 5
    assert fenced[0]["fencing_token"] == 5


def test_current_lease_completion_clears_pending_but_never_overwrites_newer_token(
    monkeypatch,
    tmp_path: Path,
):
    instance = {
        "connector_instance_id": CONNECTOR,
        "fencing_generation": 7,
        "fencing_takeover_pending": True,
    }
    registry = {"connector_instances": [instance], "audit_events": []}
    saved: list[dict] = []

    @contextmanager
    def transaction(*args, **kwargs):
        yield {}

    monkeypatch.setattr(fencing, "knowledge_transaction", transaction)
    monkeypatch.setattr(fencing, "_load_connector_registry", lambda *a, **k: registry)
    monkeypatch.setattr(
        fencing,
        "_instance_by_id",
        lambda value, connector: value["connector_instances"][0],
    )
    monkeypatch.setattr(
        fencing,
        "_save_connector_registry",
        lambda *a, **k: saved.append(dict(instance)),
    )

    completed = fencing._complete_connector_sync_fence(
        PROJECT,
        CONNECTOR,
        7,
        root=tmp_path,
        actor=ACTOR,
    )
    assert completed["completed"] is True
    assert instance["fencing_takeover_pending"] is False
    assert saved[-1]["fencing_generation"] == 7

    instance["fencing_generation"] = 8
    instance["fencing_takeover_pending"] = True
    save_count = len(saved)
    superseded = fencing._complete_connector_sync_fence(
        PROJECT,
        CONNECTOR,
        7,
        root=tmp_path,
        actor=ACTOR,
    )
    assert superseded["completed"] is False
    assert superseded["reason"] == "SUPERSEDED"
    assert instance["fencing_generation"] == 8
    assert instance["fencing_takeover_pending"] is True
    assert len(saved) == save_count


class DummyHandler(handlers.KnowledgeConnectorHandlersMixin):
    def _json(self, body, status=200, extra_headers=None):
        return {"status": status, "body": body, "headers": extra_headers or {}}


def test_handler_uses_managed_configuration_and_hides_takeover_details(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(
        handlers,
        "configure_managed_feishu_connector",
        lambda *a, **k: {
            **_configured(False),
            "configuration_write_fencing": "MONOTONIC_REGISTRY_TOKEN",
            "previous_writer_revoked": True,
            "previous_sync_recovery_action": "ABORTED_STRANDED_RUNNING_SYNC",
        },
    )
    response = DummyHandler()._handle_knowledge_connector_configure(
        PROJECT,
        {
            "connector_instance_id": CONNECTOR,
            "display_name": "飞书企业资料",
            "resource_scope": "wiki-all-accessible",
            "connection_profile": {
                "auth_mode": "internal_app",
                "app_id": "id",
                "app_secret": "secret",
            },
        },
        tmp_path,
        ACTOR,
    )
    data = response["body"]["data"]
    assert response["status"] == 200
    assert "configuration_write_fencing" not in data
    assert "previous_writer_revoked" not in data
    assert "previous_sync_recovery_action" not in data
    assert data["connector_instance"]["fencing_token_returned_to_client"] is False


def test_generic_connector_type_uses_manifest_driven_configuration_service(
    monkeypatch,
    tmp_path: Path,
):
    observed: dict[str, object] = {}

    def configure(project, **kwargs):
        observed["project"] = project
        observed.update(kwargs)
        return {
            **_configured(True),
            "connector_instance": {
                **_configured(True)["connector_instance"],
                "connector_type": "generic-docs",
            },
        }

    monkeypatch.setattr(handlers, "configure_managed_connector", configure)
    response = DummyHandler()._handle_knowledge_connector_configure(
        PROJECT,
        {
            "connector_type": "generic-docs",
            "connector_instance_id": CONNECTOR,
            "display_name": "Generic Docs",
            "resource_scope": "docs-root",
            "connection_profile": {
                "auth_mode": "api_key",
                "endpoint": "https://docs.example.test",
                "api_key": "secret",
            },
        },
        tmp_path,
        ACTOR,
    )

    assert response["status"] == 201
    assert observed["project"] == PROJECT
    assert observed["connector_type"] == "generic-docs"
    assert observed["profile"]["auth_mode"] == "api_key"


def test_handler_imports_managed_configuration_not_low_level_authority():
    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center"
        / "private_pilot_connector_handlers.py"
    ).read_text(encoding="utf-8")
    assert "configure_managed_feishu_connector" in source
    assert "configure_feishu_connector" not in source
