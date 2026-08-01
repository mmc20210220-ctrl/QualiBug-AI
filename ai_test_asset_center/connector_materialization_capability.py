"""Connector resource materialization capability classification.

This module separates deterministic adapter capability gaps from transient or unknown
materialization failures. Adapters may isolate only resources classified as observable and
unsupported; malformed descriptors remain fatal and never become silent coverage gaps.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

CONNECTOR_MATERIALIZATION_CAPABILITY_SCHEMA = (
    "qualibug.connector-materialization-capability.v1"
)


class MaterializationCapabilityError(ValueError):
    """A remote descriptor is not trustworthy enough to classify safely."""


class ResourceDisposition(str, Enum):
    MATERIALIZABLE = "MATERIALIZABLE"
    OBSERVABLE_UNSUPPORTED = "OBSERVABLE_UNSUPPORTED"
    FATAL_INVALID_DESCRIPTOR = "FATAL_INVALID_DESCRIPTOR"


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


@dataclass(frozen=True)
class ResourceCapability:
    disposition: ResourceDisposition
    connector_type: str
    remote_object_type: str
    reason_code: str
    contract_version: str
    retry_trigger: str

    @property
    def materializable(self) -> bool:
        return self.disposition is ResourceDisposition.MATERIALIZABLE

    @property
    def observable_unsupported(self) -> bool:
        return self.disposition is ResourceDisposition.OBSERVABLE_UNSUPPORTED

    def as_receipt(self) -> dict[str, Any]:
        return {
            "schema": CONNECTOR_MATERIALIZATION_CAPABILITY_SCHEMA,
            "disposition": self.disposition.value,
            "connector_type": self.connector_type,
            "remote_object_type": self.remote_object_type,
            "reason_code": self.reason_code,
            "contract_version": self.contract_version,
            "retry_trigger": self.retry_trigger,
        }


def classify_materialization_capability(
    descriptor: Mapping[str, Any],
    *,
    connector_type: str,
    materializable_types: Iterable[str],
    contract_version: str,
) -> ResourceCapability:
    """Classify one stable remote identity before any export or download starts.

    Unknown object types are deterministic capability gaps. Missing identity or type fields are
    fatal descriptor defects because treating them as unsupported would hide discovery damage.
    """
    if not isinstance(descriptor, Mapping):
        raise MaterializationCapabilityError("remote_descriptor_not_object")

    connector = _text(connector_type, 80).lower()
    remote_id = _text(descriptor.get("remote_resource_id"), 1000)
    resource_kind = _text(descriptor.get("resource_kind"), 160)
    object_type = _text(descriptor.get("obj_type"), 80).lower()
    version = _text(contract_version, 160)
    if not connector:
        raise MaterializationCapabilityError("connector_type_missing")
    if not remote_id:
        raise MaterializationCapabilityError("remote_resource_id_missing")
    if not resource_kind:
        raise MaterializationCapabilityError("resource_kind_missing")
    if not object_type:
        raise MaterializationCapabilityError("remote_object_type_missing")
    if not version:
        raise MaterializationCapabilityError("capability_contract_version_missing")

    supported = {
        _text(value, 80).lower()
        for value in materializable_types
        if _text(value, 80)
    }
    if object_type in supported:
        return ResourceCapability(
            disposition=ResourceDisposition.MATERIALIZABLE,
            connector_type=connector,
            remote_object_type=object_type,
            reason_code="",
            contract_version=version,
            retry_trigger="REMOTE_REVISION_OR_MATERIALIZATION_CONTRACT_CHANGE",
        )

    return ResourceCapability(
        disposition=ResourceDisposition.OBSERVABLE_UNSUPPORTED,
        connector_type=connector,
        remote_object_type=object_type,
        reason_code=f"{connector.upper()}_OBJECT_TYPE_UNSUPPORTED",
        contract_version=version,
        retry_trigger="ADAPTER_CAPABILITY_CHANGE",
    )


__all__ = [
    "CONNECTOR_MATERIALIZATION_CAPABILITY_SCHEMA",
    "MaterializationCapabilityError",
    "ResourceCapability",
    "ResourceDisposition",
    "classify_materialization_capability",
]
