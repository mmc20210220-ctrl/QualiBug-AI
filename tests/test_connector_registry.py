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
    assert {field.name for field in manifest.credential_fields} == {
        "app_id",
        "app_secret",
        "tenant_access_token",
        "user_access_token",
    }
    assert registry.catalog()["governance"]["network_access_performed"] is False


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
