from __future__ import annotations

import hashlib
import hmac
from typing import Any, Mapping

import pytest

from ai_test_asset_center.connector_configuration_service import (
    configure_managed_connector,
)
from ai_test_asset_center.connector_materialization_capability import ResourceCapability
from ai_test_asset_center.connector_registry import (
    ConnectorManifest,
    ConnectorRegistry,
)
from ai_test_asset_center.connector_webhook_events import (
    ConnectorWebhookError,
    project_connector_webhook,
    receive_connector_webhook,
)

PROJECT = "enterprise-project"
CONNECTOR = "webhook-docs"
ACTOR = {"name": "qa-owner", "role": "qa_lead"}
TIMESTAMP = "1700000000"


class _WebhookAdapter:
    def __init__(self) -> None:
        self._manifest = ConnectorManifest(
            connector_type="webhook-docs",
            display_name="Webhook Docs",
            category="knowledge_base",
            version="1",
            auth_modes=("anonymous",),
            supported_resource_types=("document",),
            webhook_supported=True,
            capability_contract_version="webhook-docs-v1",
        )

    def manifest(self) -> ConnectorManifest:
        return self._manifest

    def test_connection(self, context: Mapping[str, Any]) -> dict[str, Any]:
        return {"status": "AVAILABLE"}

    def discover(self, context: Mapping[str, Any], cursor: str = "") -> dict[str, Any]:
        return {"descriptors": [], "complete": True}

    def classify_resource(self, descriptor: Mapping[str, Any]) -> ResourceCapability:
        return ResourceCapability(
            support_status="SUPPORTED",
            resource_kind="document",
            materialization_strategy="text",
        )

    def materialize(
        self,
        context: Mapping[str, Any],
        descriptor: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {}

    def build_cursor(self, discovery_result: Any) -> str:
        return ""


@pytest.fixture
def webhook_registry(monkeypatch):
    import ai_test_asset_center.connector_connection_profiles as profiles
    import ai_test_asset_center.connector_webhook_events as events

    registry = ConnectorRegistry([_WebhookAdapter()])
    monkeypatch.setattr(profiles, "build_default_connector_registry", lambda: registry)
    monkeypatch.setattr(events, "build_default_connector_registry", lambda: registry)
    return registry


def _configure(tmp_path, webhook_registry) -> None:
    configure_managed_connector(
        PROJECT,
        connector_type="webhook-docs",
        connector_instance_id=CONNECTOR,
        resource_scope="docs-root",
        profile={
            "auth_mode": "anonymous",
            "webhook_secret": "webhook-secret-value",
        },
        webhook_policy={
            "enabled": True,
            "sequence_header": "X-Webhook-Sequence",
        },
        root=tmp_path,
        actor=ACTOR,
        display_name="Webhook Docs",
    )


def _headers(body: bytes, *, sequence: int, signature: str | None = None) -> dict[str, str]:
    payload = TIMESTAMP.encode("utf-8") + b"." + body
    value = signature or hmac.new(
        b"webhook-secret-value",
        payload,
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Webhook-Event-Id": f"event-{sequence}",
        "X-Webhook-Timestamp": TIMESTAMP,
        "X-Webhook-Sequence": str(sequence),
        "X-Webhook-Signature": value,
    }


def test_webhook_is_idempotent_and_reuses_managed_sync_without_returning_cursor(
    tmp_path,
    webhook_registry,
):
    _configure(tmp_path, webhook_registry)
    body = b'{"changed":"document-1"}'
    calls: list[dict[str, Any]] = []

    def sync_runner(project, connector, **kwargs):
        calls.append({"project": project, "connector": connector, **kwargs})
        return {
            "status": "COMPLETE",
            "sync_epoch_id": "sync-webhook-1",
            "next_cursor": "cursor-must-not-leak",
            "snapshot_complete": True,
            "materialized_resource_count": 1,
            "unchanged_resource_count": 0,
            "failure_count": 0,
        }

    first = receive_connector_webhook(
        PROJECT,
        CONNECTOR,
        headers=_headers(body, sequence=1),
        body=body,
        root=tmp_path,
        now_utc=TIMESTAMP,
        sync_runner=sync_runner,
    )
    second = receive_connector_webhook(
        PROJECT,
        CONNECTOR,
        headers=_headers(body, sequence=1),
        body=body,
        root=tmp_path,
        now_utc=TIMESTAMP,
        sync_runner=sync_runner,
    )

    assert first["status"] == "SYNC_TRIGGERED"
    assert second["status"] == "DUPLICATE"
    assert len(calls) == 1
    assert calls[0]["deletion_policy"] == "RETAIN"
    assert "next_cursor" not in repr(first)
    projection = project_connector_webhook(PROJECT, CONNECTOR, root=tmp_path)
    assert projection["state"]["last_success_event"]["sync_epoch_id"] == "sync-webhook-1"
    assert projection["governance"]["raw_event_body_persisted"] is False
    assert len(projection["events"]) == 1


def test_sequence_gap_requests_calibration_and_old_sequence_does_not_trigger_sync(
    tmp_path,
    webhook_registry,
):
    _configure(tmp_path, webhook_registry)
    calls: list[int] = []

    def sync_runner(project, connector, **kwargs):
        calls.append(len(calls) + 1)
        return {
            "status": "COMPLETE",
            "sync_epoch_id": f"sync-webhook-{len(calls)}",
            "snapshot_complete": True,
        }

    first = receive_connector_webhook(
        PROJECT,
        CONNECTOR,
        headers=_headers(b"one", sequence=1),
        body=b"one",
        root=tmp_path,
        now_utc=TIMESTAMP,
        sync_runner=sync_runner,
    )
    gap = receive_connector_webhook(
        PROJECT,
        CONNECTOR,
        headers=_headers(b"three", sequence=3),
        body=b"three",
        root=tmp_path,
        now_utc=TIMESTAMP,
        sync_runner=sync_runner,
    )
    old = receive_connector_webhook(
        PROJECT,
        CONNECTOR,
        headers=_headers(b"two", sequence=2),
        body=b"two",
        root=tmp_path,
        now_utc=TIMESTAMP,
        sync_runner=sync_runner,
    )

    assert first["status"] == "SYNC_TRIGGERED"
    assert gap["status"] == "CALIBRATION_SYNC_COMPLETE"
    assert gap["event"]["ordering_status"] == "GAP_DETECTED"
    assert gap["event"]["calibration_status"] == "COMPLETED"
    assert old["status"] == "OUT_OF_ORDER"
    assert old["event"]["sync_status"] == "NOT_TRIGGERED"
    assert calls == [1, 2]
    assert project_connector_webhook(PROJECT, CONNECTOR, root=tmp_path)["state"][
        "calibration_required"
    ] is False


def test_invalid_signature_and_replay_are_rejected_before_sync(tmp_path, webhook_registry):
    _configure(tmp_path, webhook_registry)

    with pytest.raises(ConnectorWebhookError, match="signature_invalid"):
        receive_connector_webhook(
            PROJECT,
            CONNECTOR,
            headers=_headers(b"bad", sequence=1, signature="0" * 64),
            body=b"bad",
            root=tmp_path,
            now_utc=TIMESTAMP,
            sync_runner=lambda *args, **kwargs: pytest.fail("sync must not run"),
        )

    with pytest.raises(ConnectorWebhookError, match="replay_window_exceeded"):
        receive_connector_webhook(
            PROJECT,
            CONNECTOR,
            headers=_headers(b"old", sequence=1),
            body=b"old",
            root=tmp_path,
            now_utc="1700001000",
            sync_runner=lambda *args, **kwargs: pytest.fail("sync must not run"),
        )


def test_sync_failure_is_retained_as_visible_last_failure(tmp_path, webhook_registry):
    _configure(tmp_path, webhook_registry)

    result = receive_connector_webhook(
        PROJECT,
        CONNECTOR,
        headers=_headers(b"failure", sequence=1),
        body=b"failure",
        root=tmp_path,
        now_utc=TIMESTAMP,
        sync_runner=lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("remote sync unavailable")
        ),
    )

    assert result["status"] == "SYNC_FAILED"
    assert result["event"]["status"] == "FAILED"
    projection = project_connector_webhook(PROJECT, CONNECTOR, root=tmp_path)
    assert projection["state"]["last_failure_event"]["error_code"] == "RuntimeError"
    assert projection["state"]["last_success_event"] is None
