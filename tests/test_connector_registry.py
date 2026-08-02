from __future__ import annotations

from typing import Any, Mapping

import pytest

from ai_test_asset_center.connector_materialization_capability import ResourceCapability
from ai_test_asset_center.connector_registry import (
    ConnectorCredentialField,
    ConnectorManifest,
    ConnectorRegistry,
    ConnectorRegistryError,
    build_default_connector_registry,
)


class _Adapter:
    def __init__(self, connector_type: str) -> None:
        self._manifest = ConnectorManifest(
            connector_type=connector_type,
            display_name=connector_type,
            category="knowledge_base",
            version="1",
            supported_resource_types=("document",),
            capability_contract_version="test-v1",
        )

    def manifest(self) -> ConnectorManifest:
        return self._manifest

    def test_connection(self, context: Mapping[str, Any]) -> dict[str, Any]:
        return {"status": "AVAILABLE"}

    def discover(self, context: Mapping[str, Any], cursor: str = "") -> dict[str, Any]:
        return {"descriptors": [], "complete": True}

    def classify_resource(self, descriptor: Mapping[str, Any]) -> ResourceCapability:
        raise NotImplementedError

    def materialize(
        self,
        context: Mapping[str, Any],
        descriptor: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {}

    def build_cursor(self, discovery_result: Mapping[str, Any] | list[Mapping[str, Any]]) -> str:
        return ""


def test_manifest_rejects_secret_field_declared_as_non_secret() -> None:
    with pytest.raises(ConnectorRegistryError, match="must_be_secret"):
        ConnectorCredentialField(
            name="access_token",
            field_type="token",
            required=True,
            secret=False,
        )


def test_credential_field_display_name_is_public_capability_metadata() -> None:
    field = ConnectorCredentialField(
        name="token",
        field_type="token",
        required=True,
        secret=True,
        display_name="访问令牌",
    )

    assert field.as_dict()["display_name"] == "访问令牌"


def test_manifest_rejects_quick_connect_scope_without_required_url_field() -> None:
    with pytest.raises(ConnectorRegistryError, match="scope_field_not_required"):
        ConnectorManifest(
            connector_type="quick",
            display_name="quick",
            category="website",
            version="1",
            scope_schema={
                "type": "object",
                "required": ["other"],
                "properties": {
                    "url": {"type": "string"},
                    "other": {"type": "string"},
                },
            },
            quick_connect_schema={
                "input_type": "url",
                "scope_field": "url",
            },
            capability_contract_version="test-v1",
        )


def test_registry_rejects_duplicate_connector_types_and_sorts_catalog() -> None:
    registry = ConnectorRegistry([_Adapter("zeta"), _Adapter("alpha")])

    assert [manifest.connector_type for manifest in registry.manifests()] == [
        "alpha",
        "zeta",
    ]
    with pytest.raises(ConnectorRegistryError, match="already_registered:alpha"):
        registry.register(_Adapter("alpha"))


def test_registry_catalog_is_metadata_only() -> None:
    registry = ConnectorRegistry([_Adapter("alpha")])

    catalog = registry.catalog()
    assert catalog["schema"] == "qualibug.connector-type-catalog.v1"
    assert catalog["connector_types"][0]["connector_type"] == "alpha"
    assert catalog["governance"] == {
        "network_access_performed": False,
        "credentials_returned": False,
        "source_content_returned": False,
        "raw_cursor_returned": False,
        "single_adapter_registry": True,
    }


def test_default_registry_exposes_existing_feishu_adapter_without_network() -> None:
    registry = build_default_connector_registry()

    manifest = registry.manifest("feishu")
    assert manifest.category == "knowledge_base"
    assert manifest.read_only is True
    assert "FULL" in manifest.sync_modes
    assert manifest.webhook_supported is True
    assert manifest.webhook_policy_schema["properties"]["signature_header"]["type"] == "string"
    assert {field.name for field in manifest.credential_fields} == {
        "app_id",
        "app_secret",
        "tenant_access_token",
        "user_access_token",
        "webhook_secret",
    }
    assert {
        field.name
        for field in manifest.credential_fields_for_auth_mode("internal_app")
    } == {"app_id", "app_secret", "webhook_secret"}
    assert {
        field.name
        for field in manifest.credential_fields_for_auth_mode(
            "tenant_access_token"
        )
    } == {"tenant_access_token", "webhook_secret"}
    assert registry.catalog()["governance"]["network_access_performed"] is False


def test_default_registry_exposes_manifest_driven_website_adapter_without_network() -> None:
    registry = build_default_connector_registry()

    manifest = registry.manifest("website")
    assert manifest.category == "website"
    assert manifest.read_only is True
    assert manifest.auth_modes == ("anonymous", "cookie_session")
    assert manifest.scope_schema["required"] == ["seed_urls"]
    assert manifest.quick_connect_schema == {
        "input_type": "url",
        "scope_field": "seed_urls",
        "priority": 10,
    }
    assert manifest.credential_fields_for_auth_mode("cookie_session")[0].display_name == "登录会话 Cookie"
    assert {
        field.name
        for field in manifest.credential_fields_for_auth_mode("cookie_session")
    } == {"session_cookie"}
    assert {row["connector_type"] for row in registry.catalog()["connector_types"]} == {
        "feishu",
        "git",
        "gitee",
        "github",
        "gitlab",
        "apifox",
        "openapi",
        "yapi",
        "website",
    }
    assert registry.catalog()["governance"]["network_access_performed"] is False


def test_default_registry_exposes_manifest_driven_openapi_adapter_without_network() -> None:
    registry = build_default_connector_registry()

    manifest = registry.manifest("openapi")
    assert manifest.category == "api_contract"
    assert manifest.read_only is True
    assert manifest.scope_schema["required"] == ["document_urls"]
    assert manifest.quick_connect_schema == {
        "input_type": "url",
        "scope_field": "document_urls",
        "priority": 20,
    }
    assert set(manifest.auth_modes) == {
        "anonymous",
        "bearer_token",
        "api_key",
        "cookie_session",
    }
    assert registry.catalog()["governance"]["network_access_performed"] is False


def test_generic_git_quick_connect_uses_url_scope_without_provider_guessing_in_ui() -> None:
    registry = build_default_connector_registry()

    assert registry.manifest("git").quick_connect_schema == {
        "input_type": "url",
        "scope_field": "repository_url",
        "priority": 30,
    }
    assert registry.manifest("github").quick_connect_schema == {}


def test_openapi_export_connectors_reuse_the_same_manifest_driven_adapter() -> None:
    registry = build_default_connector_registry()

    apifox = registry.get("apifox")
    yapi = registry.get("yapi")

    assert type(apifox) is type(yapi)
    assert apifox.manifest().connector_type == "apifox"
    assert yapi.manifest().connector_type == "yapi"
    assert apifox.manifest().quick_connect_schema == {}
    assert yapi.manifest().quick_connect_schema == {}
    assert apifox.manifest().scope_schema == yapi.manifest().scope_schema
    assert apifox.manifest().supported_resource_types == yapi.manifest().supported_resource_types


def test_default_feishu_adapter_builds_cursor_from_discovery_descriptors() -> None:
    adapter = build_default_connector_registry().get("feishu")

    cursor = adapter.build_cursor(
        [
            {
                "remote_resource_id": "wiki:space:node",
                "obj_token": "obj-1",
                "obj_type": "docx",
                "remote_revision": "7",
                "parent_node_token": "",
            }
        ]
    )

    assert cursor.startswith("feishu-snapshot-v1:")
