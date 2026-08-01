from __future__ import annotations

import json

import pytest

from ai_test_asset_center.connector_materialization_capability import ResourceCapability
from ai_test_asset_center.connector_registry import (
    ConnectorCredentialField,
    ConnectorManifest,
    ConnectorRegistry,
)
from ai_test_asset_center.connector_connection_profiles import (
    ConnectorProfileError,
    MASKED_SECRET,
    configure_connector_profile,
    connector_credential_expiry_status,
    mark_connector_reauthorization_required,
    resolve_connector_profile,
    rotate_connector_credentials,
)

PROJECT = "enterprise-project"
CONNECTOR = "docs-main"
ACTOR = {"name": "qa-owner", "role": "qa_lead"}


class _GenericAdapter:
    def __init__(self) -> None:
        self._manifest = ConnectorManifest(
            connector_type="generic-docs",
            display_name="Generic Docs",
            category="knowledge_base",
            version="1",
            auth_modes=("api_key",),
            supported_resource_types=("document",),
            credential_fields=(
                ConnectorCredentialField(
                    name="endpoint",
                    field_type="url",
                    required=True,
                    auth_modes=("api_key",),
                ),
                ConnectorCredentialField(
                    name="api_key",
                    field_type="token",
                    required=True,
                    secret=True,
                    auth_modes=("api_key",),
                ),
            ),
            capability_contract_version="generic-docs-v1",
        )

    def manifest(self) -> ConnectorManifest:
        return self._manifest

    def test_connection(self, context):
        return {"status": "AVAILABLE"}

    def discover(self, context, cursor=""):
        return {"descriptors": [], "complete": True}

    def classify_resource(self, descriptor):
        return ResourceCapability(
            support_status="SUPPORTED",
            resource_kind="document",
            materialization_strategy="text",
        )

    def materialize(self, context, descriptor):
        return {}

    def build_cursor(self, discovery_result):
        return ""


class _NoAuthAdapter(_GenericAdapter):
    def __init__(self) -> None:
        super().__init__()
        self._manifest = ConnectorManifest(
            connector_type="public-docs",
            display_name="Public Docs",
            category="knowledge_base",
            version="1",
            supported_resource_types=("document",),
            capability_contract_version="public-docs-v1",
        )


@pytest.fixture
def generic_registry(monkeypatch):
    import ai_test_asset_center.connector_connection_profiles as profiles

    registry = ConnectorRegistry([_GenericAdapter()])
    monkeypatch.setattr(profiles, "build_default_connector_registry", lambda: registry)


def test_generic_manifest_drives_encrypted_profile_and_expiry_state(
    tmp_path,
    generic_registry,
):
    result = configure_connector_profile(
        PROJECT,
        connector_type="generic-docs",
        connector_instance_id=CONNECTOR,
        resource_scope="docs-root",
        profile={
            "auth_mode": "api_key",
            "endpoint": "https://docs.example.test",
            "api_key": "generic-secret-token",
        },
        credential_expires_at_utc="2030-01-10T00:00:00Z",
        root=tmp_path,
        actor=ACTOR,
        display_name="Generic Docs",
    )

    persisted = (
        tmp_path
        / "platform_workspace"
        / PROJECT
        / "enterprise_knowledge_center"
        / "connector_connection_profiles.json"
    ).read_text(encoding="utf-8")
    assert "generic-secret-token" not in persisted
    assert "https://docs.example.test" not in persisted
    assert result["connection_profile"]["configured_fields"] == {
        "endpoint": True,
        "api_key": True,
    }
    assert resolve_connector_profile(
        PROJECT,
        result["connection_profile"]["profile_ref"],
        root=tmp_path,
    ) == {
        "auth_mode": "api_key",
        "endpoint": "https://docs.example.test",
        "api_key": "generic-secret-token",
    }
    assert connector_credential_expiry_status(
        PROJECT,
        CONNECTOR,
        now_utc="2030-01-01T00:00:00Z",
        root=tmp_path,
    )["status"] == "ACTIVE"
    assert connector_credential_expiry_status(
        PROJECT,
        CONNECTOR,
        now_utc="2030-01-09T12:00:00Z",
        root=tmp_path,
    )["status"] == "EXPIRING"


def test_reauthorization_and_rotation_preserve_generic_source_binding(
    tmp_path,
    generic_registry,
):
    configured = configure_connector_profile(
        PROJECT,
        connector_type="generic-docs",
        connector_instance_id=CONNECTOR,
        resource_scope="docs-root",
        profile={
            "auth_mode": "api_key",
            "endpoint": "https://docs.example.test",
            "api_key": "generic-secret-token",
        },
        root=tmp_path,
        actor=ACTOR,
        display_name="Generic Docs",
    )

    marked = mark_connector_reauthorization_required(
        PROJECT,
        CONNECTOR,
        reason="provider revoked the credential",
        root=tmp_path,
        actor=ACTOR,
    )
    assert marked["connection_profile"]["credential_status"] == (
        "REAUTHORIZATION_REQUIRED"
    )
    assert connector_credential_expiry_status(
        PROJECT,
        CONNECTOR,
        now_utc="2030-01-01T00:00:00Z",
        root=tmp_path,
    )["status"] == "REAUTHORIZATION_REQUIRED"

    rotated = rotate_connector_credentials(
        PROJECT,
        connector_instance_id=CONNECTOR,
        profile={
            "auth_mode": "api_key",
            "endpoint": MASKED_SECRET,
            "api_key": "rotated-secret-token",
        },
        root=tmp_path,
        actor=ACTOR,
    )
    assert rotated["connector_instance"]["resource_scope"] == "docs-root"
    assert resolve_connector_profile(
        PROJECT,
        configured["connection_profile"]["profile_ref"],
        root=tmp_path,
    )["api_key"] == "rotated-secret-token"
    assert rotated["connection_profile"]["reauthorization_required"] is False
    assert rotated["connection_profile"]["credential_status"] == "ACTIVE"


def test_no_auth_manifest_does_not_require_a_synthetic_auth_mode(
    tmp_path,
    monkeypatch,
):
    import ai_test_asset_center.connector_connection_profiles as profiles

    registry = ConnectorRegistry([_NoAuthAdapter()])
    monkeypatch.setattr(profiles, "build_default_connector_registry", lambda: registry)

    result = configure_connector_profile(
        PROJECT,
        connector_type="public-docs",
        connector_instance_id="public-docs-main",
        resource_scope="docs-root",
        profile={},
        root=tmp_path,
        actor=ACTOR,
        display_name="Public Docs",
    )

    assert result["connection_profile"]["auth_mode"] == ""
    assert result["connection_profile"]["credentials_configured"] is True
    assert resolve_connector_profile(
        PROJECT,
        result["connection_profile"]["profile_ref"],
        root=tmp_path,
    ) == {"auth_mode": ""}


def test_generic_profile_rejects_undeclared_credential_fields(
    tmp_path,
    generic_registry,
):
    with pytest.raises(
        ConnectorProfileError,
        match="connector_profile_field_not_declared:unknown",
    ):
        configure_connector_profile(
            PROJECT,
            connector_type="generic-docs",
            connector_instance_id=CONNECTOR,
            resource_scope="docs-root",
            profile={
                "auth_mode": "api_key",
                "endpoint": "https://docs.example.test",
                "api_key": "generic-secret-token",
                "unknown": "must-not-persist",
            },
            root=tmp_path,
            actor=ACTOR,
        )

    store_path = (
        tmp_path
        / "platform_workspace"
        / PROJECT
        / "enterprise_knowledge_center"
        / "connector_connection_profiles.json"
    )
    if store_path.exists():
        assert "must-not-persist" not in json.dumps(
            json.loads(store_path.read_text(encoding="utf-8"))
        )
