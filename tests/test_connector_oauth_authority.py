from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlparse

import pytest

from ai_test_asset_center.connector_connection_profiles import (
    commit_connector_sync_checkpoint,
    configure_connector_profile,
    load_connector_sync_checkpoint,
    resolve_connector_profile,
)
from ai_test_asset_center.connector_materialization_capability import ResourceCapability
from ai_test_asset_center.connector_oauth_authority import (
    ConnectorOAuthError,
    handle_connector_oauth_callback,
    project_connector_oauth,
    refresh_connector_oauth,
    start_connector_oauth,
)
from ai_test_asset_center.connector_registry import (
    ConnectorCredentialField,
    ConnectorManifest,
    ConnectorRegistry,
)
from ai_test_asset_center.connector_sync_authority import list_connector_instances

PROJECT = "oauth-project"
CONNECTOR = "oauth-docs"
ACTOR = {"name": "qa-owner", "role": "qa_lead"}


class _OAuthAdapter:
    def __init__(self) -> None:
        self._manifest = ConnectorManifest(
            connector_type="oauth-docs",
            display_name="OAuth Docs",
            category="knowledge_base",
            version="1",
            auth_modes=("oauth2",),
            supported_resource_types=("document",),
            credential_fields=(
                ConnectorCredentialField(
                    name="oauth_client_secret",
                    field_type="secret",
                    required=True,
                    secret=True,
                    auth_modes=("oauth2",),
                ),
                ConnectorCredentialField(
                    name="oauth_access_token",
                    field_type="token",
                    secret=True,
                    auth_modes=("oauth2",),
                ),
                ConnectorCredentialField(
                    name="oauth_refresh_token",
                    field_type="token",
                    secret=True,
                    auth_modes=("oauth2",),
                ),
                ConnectorCredentialField(
                    name="oauth_granted_scope",
                    field_type="text",
                    auth_modes=("oauth2",),
                ),
                ConnectorCredentialField(
                    name="oauth_token_type",
                    field_type="text",
                    auth_modes=("oauth2",),
                ),
            ),
            oauth_schema={
                "type": "oauth2_authorization_code",
                "authorization_endpoint": "https://provider.example.test/authorize",
                "token_endpoint": "https://provider.example.test/token",
                "client_id": "public-client",
                "redirect_uri": "https://app.example.test/oauth/callback",
                "auth_mode": "oauth2",
                "minimum_scopes": ["docs:read"],
                "optional_scopes": ["docs:metadata"],
                "client_auth_method": "client_secret_basic",
                "client_secret_field": "oauth_client_secret",
                "access_token_field": "oauth_access_token",
                "refresh_token_field": "oauth_refresh_token",
                "scope_field": "oauth_granted_scope",
                "token_type_field": "oauth_token_type",
            },
            capability_contract_version="oauth-docs-v1",
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


@pytest.fixture
def oauth_registry(monkeypatch):
    import ai_test_asset_center.connector_connection_profiles as profiles
    import ai_test_asset_center.connector_oauth_authority as oauth

    registry = ConnectorRegistry([_OAuthAdapter()])
    monkeypatch.setattr(profiles, "build_default_connector_registry", lambda: registry)
    monkeypatch.setattr(oauth, "build_default_connector_registry", lambda: registry)
    return registry


def _configure(tmp_path, oauth_registry, connector: str = CONNECTOR) -> dict:
    return configure_connector_profile(
        PROJECT,
        connector_type="oauth-docs",
        connector_instance_id=connector,
        resource_scope="docs-root",
        profile={
            "auth_mode": "oauth2",
            "oauth_client_secret": "client-secret-value",
        },
        root=tmp_path,
        actor=ACTOR,
        display_name="OAuth Docs",
    )


def _start(tmp_path, oauth_registry, connector: str = CONNECTOR) -> dict:
    _configure(tmp_path, oauth_registry, connector)
    return start_connector_oauth(
        PROJECT,
        connector,
        root=tmp_path,
        actor=ACTOR,
        additional_scopes=["docs:metadata"],
    )


def test_start_uses_manifest_scopes_state_and_pkce_without_persisting_raw_state(
    tmp_path,
    oauth_registry,
):
    started = _start(tmp_path, oauth_registry)
    query = parse_qs(urlparse(started["authorization_url"]).query)
    assert query["client_id"] == ["public-client"]
    assert query["scope"] == ["docs:read docs:metadata"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"][0]
    assert query["state"][0]
    assert started["state_returned_only_inside_authorization_url"] is True

    ledger_path = (
        tmp_path
        / "platform_workspace"
        / PROJECT
        / "enterprise_knowledge_center"
        / "connector_oauth"
        / f"{CONNECTOR}.json"
    )
    persisted = ledger_path.read_text(encoding="utf-8")
    assert query["state"][0] not in persisted
    assert "authorization-code-value" not in persisted
    assert "client-secret-value" not in persisted
    ledger = json.loads(persisted)
    assert ledger["transactions"][0]["code_verifier_ciphertext"].startswith(
        "enc$v1$"
    )


def test_callback_encrypts_tokens_and_preserves_source_scope_and_checkpoint(
    tmp_path,
    oauth_registry,
):
    configured = _configure(tmp_path, oauth_registry)
    commit_connector_sync_checkpoint(
        PROJECT,
        CONNECTOR,
        "cursor-before-oauth",
        sync_epoch_id="sync-before-oauth",
        root=tmp_path,
        actor=ACTOR,
    )
    started = start_connector_oauth(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
    )
    query = parse_qs(urlparse(started["authorization_url"]).query)
    observed: dict[str, object] = {}

    def requester(endpoint, body, headers, timeout):
        observed.update({"endpoint": endpoint, "body": dict(body), "headers": dict(headers)})
        assert body["grant_type"] == "authorization_code"
        assert body["code"] == "authorization-code-value"
        assert "client_id" not in body
        assert headers["Authorization"].startswith("Basic ")
        decoded = base64.b64decode(headers["Authorization"].split(" ", 1)[1]).decode()
        assert decoded == "public-client:client-secret-value"
        return {
            "access_token": "access-token-value",
            "refresh_token": "refresh-token-value",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "docs:read",
        }

    result = handle_connector_oauth_callback(
        PROJECT,
        CONNECTOR,
        {"state": query["state"][0], "code": "authorization-code-value"},
        root=tmp_path,
        actor=ACTOR,
        token_requester=requester,
    )
    assert result["authorization_status"] == "AUTHORIZED"
    assert result["permission_status"] == "OBSERVED"
    assert result["source_identity_preserved"] is True
    assert result["checkpoint_preserved"] is True
    assert result["credential_values_returned"] is False
    assert observed["endpoint"] == "https://provider.example.test/token"

    resolved = resolve_connector_profile(
        PROJECT,
        configured["connection_profile"]["profile_ref"],
        root=tmp_path,
    )
    assert resolved["oauth_access_token"] == "access-token-value"
    assert resolved["oauth_refresh_token"] == "refresh-token-value"
    instance = list_connector_instances(PROJECT, root=tmp_path)["connector_instances"][0]
    assert instance["resource_scope"] == "docs-root"
    assert load_connector_sync_checkpoint(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    ) == "cursor-before-oauth"
    assert project_connector_oauth(PROJECT, CONNECTOR, root=tmp_path)["status"] == (
        "EXPIRING"
    )
    persisted_profile = (
        tmp_path
        / "platform_workspace"
        / PROJECT
        / "enterprise_knowledge_center"
        / "connector_connection_profiles.json"
    ).read_text(encoding="utf-8")
    assert "access-token-value" not in persisted_profile
    assert "refresh-token-value" not in persisted_profile
    assert "authorization-code-value" not in persisted_profile

    with pytest.raises(ConnectorOAuthError, match="oauth_state_replayed"):
        handle_connector_oauth_callback(
            PROJECT,
            CONNECTOR,
            {"state": query["state"][0], "code": "authorization-code-value"},
            root=tmp_path,
            actor=ACTOR,
            token_requester=requester,
        )


def test_callback_exposes_insufficient_permission_and_requires_reauthorization(
    tmp_path,
    oauth_registry,
):
    started = _start(tmp_path, oauth_registry, "oauth-insufficient")
    query = parse_qs(urlparse(started["authorization_url"]).query)

    def requester(endpoint, body, headers, timeout):
        return {
            "access_token": "insufficient-access-token",
            "refresh_token": "insufficient-refresh-token",
            "expires_in": 3600,
            "scope": "docs:metadata",
        }

    with pytest.raises(ConnectorOAuthError, match="oauth_permission_insufficient"):
        handle_connector_oauth_callback(
            PROJECT,
            "oauth-insufficient",
            {"state": query["state"][0], "code": "authorization-code-value"},
            root=tmp_path,
            actor=ACTOR,
            token_requester=requester,
        )
    projection = project_connector_oauth(
        PROJECT,
        "oauth-insufficient",
        root=tmp_path,
    )
    assert projection["status"] == "PERMISSION_INSUFFICIENT"
    assert projection["permission_status"] == "PERMISSION_INSUFFICIENT"
    assert projection["last_failure"]["required_scopes"] == ["docs:read"]
    resolved = resolve_connector_profile(
        PROJECT,
        "vault-ref://connectors/oauth-insufficient",
        root=tmp_path,
    )
    assert "oauth_access_token" not in resolved
    assert "insufficient-access-token" not in json.dumps(projection)

    retried = start_connector_oauth(
        PROJECT,
        "oauth-insufficient",
        root=tmp_path,
        actor=ACTOR,
    )
    retried_query = parse_qs(urlparse(retried["authorization_url"]).query)
    handle_connector_oauth_callback(
        PROJECT,
        "oauth-insufficient",
        {"state": retried_query["state"][0], "code": "authorization-code-value"},
        root=tmp_path,
        actor=ACTOR,
        token_requester=lambda *args: {
            "access_token": "recovered-access-token",
            "refresh_token": "recovered-refresh-token",
            "expires_in": 3600,
            "scope": "docs:read",
        },
    )
    recovered = project_connector_oauth(
        PROJECT,
        "oauth-insufficient",
        root=tmp_path,
    )
    assert recovered["status"] == "EXPIRING"
    assert recovered["permission_status"] == "OBSERVED"


def test_callback_rejects_redirect_mismatch_and_optional_scope_invention(
    tmp_path,
    oauth_registry,
):
    _configure(tmp_path, oauth_registry, "oauth-redirect")
    with pytest.raises(ConnectorOAuthError, match="oauth_scope_not_declared_optional"):
        start_connector_oauth(
            PROJECT,
            "oauth-redirect",
            root=tmp_path,
            actor=ACTOR,
            additional_scopes=["admin:write"],
        )
    started = start_connector_oauth(
        PROJECT,
        "oauth-redirect",
        root=tmp_path,
        actor=ACTOR,
    )
    query = parse_qs(urlparse(started["authorization_url"]).query)
    with pytest.raises(ConnectorOAuthError, match="oauth_redirect_uri_mismatch"):
        handle_connector_oauth_callback(
            PROJECT,
            "oauth-redirect",
            {
                "state": query["state"][0],
                "code": "authorization-code-value",
                "redirect_uri": "https://evil.example.test/callback",
            },
            root=tmp_path,
            actor=ACTOR,
            token_requester=lambda *args: {
                "access_token": "unused",
                "refresh_token": "unused",
            },
        )


def _authorize_for_refresh(tmp_path, oauth_registry, connector: str, expires_in: int) -> None:
    _configure(tmp_path, oauth_registry, connector)
    started = start_connector_oauth(
        PROJECT,
        connector,
        root=tmp_path,
        actor=ACTOR,
    )
    query = parse_qs(urlparse(started["authorization_url"]).query)
    handle_connector_oauth_callback(
        PROJECT,
        connector,
        {"state": query["state"][0], "code": "authorization-code-value"},
        root=tmp_path,
        actor=ACTOR,
        token_requester=lambda *args: {
            "access_token": "initial-access-token",
            "refresh_token": "initial-refresh-token",
            "expires_in": expires_in,
            "scope": "docs:read",
        },
    )


def test_refresh_rotates_access_token_and_preserves_refresh_token_source_and_checkpoint(
    tmp_path,
    oauth_registry,
):
    connector = "oauth-refresh"
    configured = _configure(tmp_path, oauth_registry, connector)
    commit_connector_sync_checkpoint(
        PROJECT,
        connector,
        "cursor-before-refresh",
        sync_epoch_id="sync-before-refresh",
        root=tmp_path,
        actor=ACTOR,
    )
    _authorize_for_refresh(tmp_path, oauth_registry, connector, expires_in=1)
    observed: dict[str, object] = {}

    def requester(endpoint, body, headers, timeout):
        observed.update({"endpoint": endpoint, "body": dict(body)})
        assert body["grant_type"] == "refresh_token"
        assert body["refresh_token"] == "initial-refresh-token"
        return {
            "access_token": "refreshed-access-token",
            "expires_in": 3600,
            "scope": "docs:read",
        }

    result = refresh_connector_oauth(
        PROJECT,
        connector,
        root=tmp_path,
        actor=ACTOR,
        token_requester=requester,
    )
    assert result["refresh_status"] == "SUCCEEDED"
    assert result["refreshed"] is True
    assert observed["endpoint"] == "https://provider.example.test/token"
    resolved = resolve_connector_profile(
        PROJECT,
        configured["connection_profile"]["profile_ref"],
        root=tmp_path,
    )
    assert resolved["oauth_access_token"] == "refreshed-access-token"
    assert resolved["oauth_refresh_token"] == "initial-refresh-token"
    assert load_connector_sync_checkpoint(PROJECT, connector, root=tmp_path) == (
        "cursor-before-refresh"
    )
    projection = project_connector_oauth(PROJECT, connector, root=tmp_path)
    assert projection["automatic_refresh_status"] == "SUCCEEDED"
    assert projection["last_refresh_success"]["status"] == "SUCCEEDED"
    persisted_profile = (
        tmp_path
        / "platform_workspace"
        / PROJECT
        / "enterprise_knowledge_center"
        / "connector_connection_profiles.json"
    ).read_text(encoding="utf-8")
    assert "initial-refresh-token" not in persisted_profile
    assert "refreshed-access-token" not in persisted_profile


def test_refresh_rejection_requires_reauthorization_without_mutating_source_or_token(
    tmp_path,
    oauth_registry,
):
    connector = "oauth-refresh-rejected"
    _authorize_for_refresh(tmp_path, oauth_registry, connector, expires_in=1)
    with pytest.raises(ConnectorOAuthError, match="oauth_refresh_token_rejected"):
        refresh_connector_oauth(
            PROJECT,
            connector,
            root=tmp_path,
            actor=ACTOR,
            token_requester=lambda *args: {"error": "invalid_grant"},
        )
    resolved = resolve_connector_profile(
        PROJECT,
        f"vault-ref://connectors/{connector}",
        root=tmp_path,
    )
    assert resolved["oauth_access_token"] == "initial-access-token"
    projection = project_connector_oauth(PROJECT, connector, root=tmp_path)
    assert projection["status"] == "REAUTHORIZATION_REQUIRED"
    assert projection["automatic_refresh_status"] == "FAILED"
    assert projection["last_refresh_failure"]["failure_reason"] == (
        "oauth_refresh_token_rejected"
    )


def test_refresh_skips_active_token_without_transport(
    tmp_path,
    oauth_registry,
):
    connector = "oauth-refresh-not-due"
    _authorize_for_refresh(tmp_path, oauth_registry, connector, expires_in=3600)
    result = refresh_connector_oauth(
        PROJECT,
        connector,
        root=tmp_path,
        actor=ACTOR,
        expiring_within_seconds=0,
        token_requester=lambda *args: pytest.fail("active token must not refresh"),
    )
    assert result["attempted"] is False
    assert result["refresh_status"] == "NOT_DUE"
