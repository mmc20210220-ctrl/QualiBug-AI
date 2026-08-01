"""Generic connector protocol, manifest validation, and adapter registry.

This module is deliberately limited to connector capability metadata and dispatch lookup. It
does not perform network access, persist credentials, ingest source content, or create a second
source registry. Runtime work remains owned by the existing connector sync and Source Occurrence
authorities.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, Sequence, TypedDict, runtime_checkable

from .connector_materialization_capability import ResourceCapability

CONNECTOR_MANIFEST_SCHEMA = "qualibug.connector-manifest.v1"
CONNECTOR_TYPE_CATALOG_SCHEMA = "qualibug.connector-type-catalog.v1"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_ALLOWED_CATEGORIES = {
    "knowledge_base",
    "project_management",
    "api_contract",
    "source_code",
    "website",
    "database_metadata",
    "communication",
}
_ALLOWED_SYNC_MODES = {"FULL", "INCREMENTAL"}
_ALLOWED_CREDENTIAL_FIELD_TYPES = {
    "text",
    "secret",
    "token",
    "url",
    "username",
    "password",
    "oauth_authorization_code",
    "personal_access_token",
    "ssh_key_reference",
    "client_certificate_reference",
    "cookie_session_reference",
}
_SECRET_FIELD_TYPES = {
    "secret",
    "token",
    "password",
    "oauth_authorization_code",
    "personal_access_token",
    "ssh_key_reference",
    "client_certificate_reference",
    "cookie_session_reference",
}
_WEBHOOK_SECRET_FIELD_NAME = "webhook_secret"
_DEFAULT_WEBHOOK_POLICY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Configuration-driven HMAC webhook verification and sync triggering.",
    "properties": {
        "enabled": {"type": "boolean", "default": False},
        "signature_header": {"type": "string", "default": "X-Webhook-Signature"},
        "event_id_header": {"type": "string", "default": "X-Webhook-Event-Id"},
        "timestamp_header": {"type": "string", "default": "X-Webhook-Timestamp"},
        "sequence_header": {"type": "string", "default": "X-Webhook-Sequence"},
        "algorithm": {"type": "string", "enum": ["hmac-sha256"]},
        "encoding": {"type": "string", "enum": ["hex", "base64"]},
        "signed_payload": {
            "type": "string",
            "enum": ["body", "timestamp.body", "event_id.timestamp.body"],
        },
        "signature_prefix": {"type": "string"},
        "max_age_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
        "future_skew_seconds": {"type": "integer", "minimum": 0, "maximum": 600},
        "event_retention_count": {"type": "integer", "minimum": 100, "maximum": 2000},
    },
}
_REQUIRED_ADAPTER_METHODS = (
    "manifest",
    "test_connection",
    "discover",
    "classify_resource",
    "materialize",
    "build_cursor",
)


class ConnectorRegistryError(ValueError):
    """A connector manifest or adapter registry operation is invalid."""


class ConnectionTestResult(TypedDict, total=False):
    schema: str
    status: str
    connector_instance_id: str
    connector_type: str
    auth_mode: str
    network_side_effect: str


class RemoteResourceDescriptor(TypedDict, total=False):
    remote_resource_id: str
    resource_kind: str
    remote_object_type: str
    display_title: str
    canonical_url: str
    parent_remote_id: str
    remote_revision: str
    remote_updated_at: str
    declared_mime: str
    acl_fingerprint: str
    acl: dict[str, Any]
    acl_version: str
    principals: list[Any]
    visibility: str
    inherited_from: str
    captured_at: str
    complete: bool
    availability: str
    metadata: dict[str, Any]


class DiscoveryResult(TypedDict, total=False):
    schema: str
    descriptors: list[RemoteResourceDescriptor]
    complete: bool
    next_cursor: str
    coverage: dict[str, Any]
    lifecycle: list[dict[str, Any]]


class MaterializedSnapshot(TypedDict, total=False):
    remote_resource_id: str
    source_type: str
    filename: str
    content: str | bytes
    export_format: str
    declared_mime: str
    remote_revision: str
    retrieved_at: str
    materialization_fingerprint: str
    acl: dict[str, Any]
    acl_version: str
    principals: list[Any]
    visibility: str
    inherited_from: str
    captured_at: str
    complete: bool
    availability: str


SyncCursor = str
RemoteLifecycleEvidence = Mapping[str, Any]
ConnectorContext = Mapping[str, Any]


def _identifier(value: Any, field_name: str) -> str:
    result = str(value or "").strip()
    if not _IDENTIFIER_RE.fullmatch(result):
        raise ConnectorRegistryError(f"{field_name}_invalid")
    return result


def _unique_strings(values: Iterable[Any], field_name: str, *, upper: bool = False) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if upper:
            item = item.upper()
        if not item:
            raise ConnectorRegistryError(f"{field_name}_contains_empty_value")
        if item in seen:
            raise ConnectorRegistryError(f"{field_name}_contains_duplicate:{item}")
        seen.add(item)
        result.append(item)
    return tuple(result)


@dataclass(frozen=True)
class ConnectorCredentialField:
    """A non-secret declaration of one connector configuration field."""

    name: str
    field_type: str
    required: bool = False
    secret: bool = False
    description: str = ""
    auth_modes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identifier(self.name, "credential_field_name"))
        field_type = str(self.field_type or "").strip().lower()
        if field_type not in _ALLOWED_CREDENTIAL_FIELD_TYPES:
            raise ConnectorRegistryError("credential_field_type_invalid")
        object.__setattr__(self, "field_type", field_type)
        if not isinstance(self.required, bool) or not isinstance(self.secret, bool):
            raise ConnectorRegistryError("credential_field_flags_invalid")
        if field_type in _SECRET_FIELD_TYPES and not self.secret:
            raise ConnectorRegistryError("secret_credential_field_must_be_secret")
        object.__setattr__(self, "description", str(self.description or "").strip()[:300])
        object.__setattr__(
            self,
            "auth_modes",
            _unique_strings(self.auth_modes, "credential_field_auth_modes"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "field_type": self.field_type,
            "required": self.required,
            "secret": self.secret,
            "description": self.description,
            "auth_modes": list(self.auth_modes),
        }


@dataclass(frozen=True)
class ConnectorManifest:
    """Validated, non-secret capability and configuration metadata for one connector."""

    connector_type: str
    display_name: str
    category: str
    version: str
    auth_modes: tuple[str, ...] = ()
    scope_schema: Mapping[str, Any] = field(default_factory=dict)
    supported_resource_types: tuple[str, ...] = ()
    sync_modes: tuple[str, ...] = ("FULL",)
    webhook_supported: bool = False
    local_runner_supported: bool = False
    local_runner_required: bool = False
    read_only: bool = True
    credential_fields: tuple[ConnectorCredentialField, ...] = ()
    capability_contract_version: str = ""
    schema: str = CONNECTOR_MANIFEST_SCHEMA
    webhook_policy_schema: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "connector_type", _identifier(self.connector_type, "connector_type"))
        display_name = str(self.display_name or "").strip()[:240]
        if not display_name:
            raise ConnectorRegistryError("display_name_missing")
        object.__setattr__(self, "display_name", display_name)
        category = str(self.category or "").strip().lower()
        if category not in _ALLOWED_CATEGORIES:
            raise ConnectorRegistryError("connector_category_invalid")
        object.__setattr__(self, "category", category)
        version = str(self.version or "").strip()[:80]
        if not version:
            raise ConnectorRegistryError("manifest_version_missing")
        object.__setattr__(self, "version", version)
        contract = str(self.capability_contract_version or "").strip()[:160]
        if not contract:
            raise ConnectorRegistryError("capability_contract_version_missing")
        object.__setattr__(self, "capability_contract_version", contract)

        object.__setattr__(self, "auth_modes", _unique_strings(self.auth_modes, "auth_modes"))
        if not isinstance(self.scope_schema, Mapping):
            raise ConnectorRegistryError("scope_schema_must_be_object")
        object.__setattr__(self, "scope_schema", dict(self.scope_schema))
        object.__setattr__(
            self,
            "supported_resource_types",
            _unique_strings(self.supported_resource_types, "supported_resource_types"),
        )
        sync_modes = _unique_strings(self.sync_modes, "sync_modes", upper=True)
        if any(mode not in _ALLOWED_SYNC_MODES for mode in sync_modes):
            raise ConnectorRegistryError("sync_mode_invalid")
        object.__setattr__(self, "sync_modes", sync_modes)
        for name in ("webhook_supported", "local_runner_supported", "local_runner_required", "read_only"):
            if not isinstance(getattr(self, name), bool):
                raise ConnectorRegistryError(f"{name}_invalid")
        if self.local_runner_required and not self.local_runner_supported:
            raise ConnectorRegistryError("local_runner_required_without_support")

        fields = tuple(self.credential_fields)
        if self.webhook_supported and not any(
            item.name == _WEBHOOK_SECRET_FIELD_NAME for item in fields
        ):
            fields += (
                ConnectorCredentialField(
                    name=_WEBHOOK_SECRET_FIELD_NAME,
                    field_type="secret",
                    required=False,
                    secret=True,
                    description="Encrypted HMAC secret used only to verify configured webhook events.",
                ),
            )
        if any(not isinstance(item, ConnectorCredentialField) for item in fields):
            raise ConnectorRegistryError("credential_field_invalid")
        names = [field.name for field in fields]
        if len(names) != len(set(names)):
            raise ConnectorRegistryError("credential_field_name_duplicate")
        supported_auth_modes = set(self.auth_modes)
        if any(
            field.auth_modes and not set(field.auth_modes) <= supported_auth_modes
            for field in fields
        ):
            raise ConnectorRegistryError("credential_field_auth_mode_not_supported")
        object.__setattr__(self, "credential_fields", fields)
        policy_schema = dict(self.webhook_policy_schema)
        if self.webhook_supported and not policy_schema:
            policy_schema = dict(_DEFAULT_WEBHOOK_POLICY_SCHEMA)
        if not self.webhook_supported and policy_schema:
            raise ConnectorRegistryError("webhook_policy_schema_without_support")
        object.__setattr__(self, "webhook_policy_schema", policy_schema)
        if self.schema != CONNECTOR_MANIFEST_SCHEMA:
            raise ConnectorRegistryError("manifest_schema_invalid")

    def credential_fields_for_auth_mode(
        self,
        auth_mode: str,
    ) -> tuple[ConnectorCredentialField, ...]:
        mode = str(auth_mode or "").strip()
        if mode not in self.auth_modes:
            raise ConnectorRegistryError("auth_mode_not_supported")
        return tuple(
            field
            for field in self.credential_fields
            if not field.auth_modes or mode in field.auth_modes
        )

    def as_dict(self) -> dict[str, Any]:
        """Return only public capability metadata; no configured credential value exists here."""
        return {
            "schema": self.schema,
            "connector_type": self.connector_type,
            "display_name": self.display_name,
            "category": self.category,
            "version": self.version,
            "auth_modes": list(self.auth_modes),
            "scope_schema": dict(self.scope_schema),
            "supported_resource_types": list(self.supported_resource_types),
            "sync_modes": list(self.sync_modes),
            "webhook_supported": self.webhook_supported,
            "local_runner_supported": self.local_runner_supported,
            "local_runner_required": self.local_runner_required,
            "read_only": self.read_only,
            "credential_fields": [field.as_dict() for field in self.credential_fields],
            "capability_contract_version": self.capability_contract_version,
            "webhook_policy_schema": dict(self.webhook_policy_schema),
        }


@runtime_checkable
class ConnectorAdapter(Protocol):
    """The only adapter seam allowed to cross into connector runtime services."""

    def manifest(self) -> ConnectorManifest: ...

    def test_connection(self, context: ConnectorContext) -> ConnectionTestResult: ...

    def discover(self, context: ConnectorContext, cursor: SyncCursor = "") -> DiscoveryResult: ...

    def classify_resource(self, descriptor: Mapping[str, Any]) -> ResourceCapability: ...

    def materialize(
        self,
        context: ConnectorContext,
        descriptor: Mapping[str, Any],
    ) -> MaterializedSnapshot: ...

    def build_cursor(
        self,
        discovery_result: DiscoveryResult | Sequence[Mapping[str, Any]],
    ) -> SyncCursor: ...


class ConnectorRegistry:
    """Deterministic process-local adapter registry with no network side effects."""

    def __init__(self, adapters: Iterable[ConnectorAdapter] = ()) -> None:
        self._adapters: dict[str, ConnectorAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ConnectorAdapter) -> None:
        if not isinstance(adapter, ConnectorAdapter):
            missing = [name for name in _REQUIRED_ADAPTER_METHODS if not callable(getattr(adapter, name, None))]
            raise ConnectorRegistryError(
                "connector_adapter_invalid:" + ",".join(missing or ["protocol"])
            )
        manifest = adapter.manifest()
        if not isinstance(manifest, ConnectorManifest):
            raise ConnectorRegistryError("connector_adapter_manifest_invalid")
        connector_type = manifest.connector_type
        if connector_type in self._adapters:
            raise ConnectorRegistryError(f"connector_adapter_already_registered:{connector_type}")
        self._adapters[connector_type] = adapter

    def get(self, connector_type: str) -> ConnectorAdapter:
        key = _identifier(connector_type, "connector_type")
        try:
            return self._adapters[key]
        except KeyError as exc:
            raise ConnectorRegistryError(f"connector_adapter_not_registered:{key}") from exc

    def manifest(self, connector_type: str) -> ConnectorManifest:
        return self.get(connector_type).manifest()

    def all(self) -> list[ConnectorAdapter]:
        return sorted(
            self._adapters.values(),
            key=lambda adapter: adapter.manifest().connector_type,
        )

    def manifests(self) -> list[ConnectorManifest]:
        return [adapter.manifest() for adapter in self.all()]

    def catalog(self) -> dict[str, Any]:
        return {
            "schema": CONNECTOR_TYPE_CATALOG_SCHEMA,
            "connector_types": [manifest.as_dict() for manifest in self.manifests()],
            "governance": {
                "network_access_performed": False,
                "credentials_returned": False,
                "source_content_returned": False,
                "raw_cursor_returned": False,
                "single_adapter_registry": True,
            },
        }


def build_default_connector_registry() -> ConnectorRegistry:
    """Build the installed product registry without importing adapters at module import time."""
    from .feishu_connector_adapter import FeishuConnectorAdapter
    from .git_connector_adapter import GitRepositoryConnectorAdapter
    from .openapi_connector_adapter import OpenApiConnectorAdapter
    from .website_connector_adapter import WebsiteConnectorAdapter

    return ConnectorRegistry(
        (
            FeishuConnectorAdapter(),
            GitRepositoryConnectorAdapter("gitee"),
            GitRepositoryConnectorAdapter("gitlab"),
            GitRepositoryConnectorAdapter("github"),
            GitRepositoryConnectorAdapter("git"),
            OpenApiConnectorAdapter(),
            OpenApiConnectorAdapter("apifox"),
            OpenApiConnectorAdapter("yapi"),
            WebsiteConnectorAdapter(),
        )
    )


__all__ = [
    "CONNECTOR_MANIFEST_SCHEMA",
    "CONNECTOR_TYPE_CATALOG_SCHEMA",
    "ConnectionTestResult",
    "ConnectorAdapter",
    "ConnectorContext",
    "ConnectorCredentialField",
    "ConnectorManifest",
    "ConnectorRegistry",
    "ConnectorRegistryError",
    "DiscoveryResult",
    "MaterializedSnapshot",
    "RemoteLifecycleEvidence",
    "RemoteResourceDescriptor",
    "SyncCursor",
    "build_default_connector_registry",
]
