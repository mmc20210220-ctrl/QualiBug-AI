"""Capability-aware Feishu snapshot orchestration.

The transport adapter remains responsible for read-only discovery and materialization. This
application service classifies every discovered resource before export, isolates deterministic
unsupported object types, preserves any previously materialized occurrence, and still fails
closed for transport, permission, export, parsing, or unknown runtime failures.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .connector_materialization_capability import (
    MaterializationCapabilityError,
    ResourceCapability,
    classify_materialization_capability,
)
from .connector_sync_authority import (
    ConnectorSyncError,
    connector_snapshot_observation_index,
    sync_connector_snapshot_batch,
)
from .enterprise_knowledge_center._common import ROOT
from .feishu_connector_adapter import (
    FEISHU_ADAPTER_SCHEMA,
    FEISHU_CONNECTOR_TYPE,
    ConnectionProfileResolver,
    FeishuConnectorError,
    FeishuTransport,
    _DIRECT_FILE_TYPES,
    _EXPORT_FORMATS,
    _DEFAULT_MAX_EXPORT_POLLS,
    _DEFAULT_MAX_NODES,
    _connector_instance,
    _default_transport,
    _materialization_fingerprint,
    _materialize_changed_resources,
    _resolve_access_token,
    _snapshot_cursor,
    _unchanged_observation,
    discover_feishu_wiki_resources,
)

FEISHU_MATERIALIZATION_CAPABILITY_VERSION = (
    "feishu-materialization-capability-v1"
)


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def classify_feishu_resource(
    descriptor: Mapping[str, Any],
) -> ResourceCapability:
    """Classify one fully identified Wiki resource before network materialization."""
    try:
        return classify_materialization_capability(
            descriptor,
            connector_type=FEISHU_CONNECTOR_TYPE,
            materializable_types={*_EXPORT_FORMATS, *_DIRECT_FILE_TYPES},
            contract_version=FEISHU_MATERIALIZATION_CAPABILITY_VERSION,
        )
    except MaterializationCapabilityError as exc:
        raise FeishuConnectorError(
            f"feishu_materialization_capability_invalid:{exc}"
        ) from exc


def _unsupported_fingerprint(
    descriptor: Mapping[str, Any],
    capability: ResourceCapability,
) -> str:
    basis = {
        "capability_contract_version": capability.contract_version,
        "disposition": capability.disposition.value,
        "remote_resource_id": _text(descriptor.get("remote_resource_id"), 1000),
        "obj_type": capability.remote_object_type,
        "remote_revision": _text(descriptor.get("remote_revision"), 240),
        "reason_code": capability.reason_code,
    }
    return "unsupported:" + hashlib.sha256(
        json.dumps(
            basis,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _unsupported_receipt(
    descriptor: Mapping[str, Any],
    capability: ResourceCapability,
    existing: Mapping[str, Any],
) -> dict[str, Any]:
    existing_metadata = dict(existing.get("source_metadata") or {})
    return {
        **capability.as_receipt(),
        "remote_resource_id": _text(
            descriptor.get("remote_resource_id"), 1000
        ),
        "resource_kind": _text(descriptor.get("resource_kind"), 160),
        "display_title": _text(descriptor.get("title"), 300),
        "remote_revision": _text(descriptor.get("remote_revision"), 240),
        "parent_remote_id": _text(
            descriptor.get("parent_node_token"), 160
        ),
        "state": "UNSUPPORTED",
        "content_materialized": False,
        "source_occurrence_created": False,
        "customer_source_modified": False,
        "historical_content_retained": bool(existing),
        "freshness": "STALE_UNSUPPORTED" if existing else "UNAVAILABLE_UNSUPPORTED",
        "last_materialized_source_occurrence_id": _text(
            existing.get("source_occurrence_id"), 240
        ),
        "last_materialized_revision": _text(
            existing_metadata.get("remote_revision"), 240
        ),
    }


def _unsupported_coverage_observation(
    descriptor: Mapping[str, Any],
    capability: ResourceCapability,
    existing: Mapping[str, Any],
) -> dict[str, Any]:
    """Represent a known gap without creating content or a synthetic source occurrence."""
    existing_metadata = dict(existing.get("source_metadata") or {})
    remote_id = _text(descriptor.get("remote_resource_id"), 1000)
    previous_kind = _text(existing_metadata.get("resource_kind"), 160)
    current_kind = _text(descriptor.get("resource_kind"), 160)
    effective_kind = previous_kind or current_kind
    freshness = "STALE_UNSUPPORTED" if existing else "UNAVAILABLE_UNSUPPORTED"
    return {
        "remote_resource_id": remote_id,
        # Source refs include resource kind. Reuse the previous kind for an existing
        # occurrence so RETIRE_MISSING and freshness observations bind to its real identity.
        "resource_kind": effective_kind,
        "state": "UNSUPPORTED",
        "reason_code": capability.reason_code,
        "remote_object_type": capability.remote_object_type,
        "display_title": _text(descriptor.get("title"), 300),
        "retry_trigger": capability.retry_trigger,
        "capability_contract_version": capability.contract_version,
        "metadata": {
            "source_origin": "connector_snapshot",
            "requested_source_id": remote_id,
            "remote_resource_id": remote_id,
            "resource_kind": effective_kind,
            "current_remote_resource_kind": current_kind,
            "remote_revision": _text(descriptor.get("remote_revision"), 240),
            "remote_updated_at": _text(
                descriptor.get("remote_updated_at"), 80
            ),
            "parent_remote_id": _text(
                descriptor.get("parent_node_token"), 1000
            ),
            "remote_materialization_fingerprint": _unsupported_fingerprint(
                descriptor, capability
            ),
            "remote_coverage_state": freshness,
            "materialization_disposition": capability.disposition.value,
            "materialization_reason_code": capability.reason_code,
            "materialization_capability_contract": capability.contract_version,
            "customer_source_modified": False,
        },
    }


def sync_feishu_connector(
    project_id: str,
    *,
    connector_instance_id: str,
    resolve_connection_profile: ConnectionProfileResolver,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    previous_cursor: str = "",
    deletion_policy: str = "RETAIN",
    max_retire_count: int = 100,
    max_retire_ratio: float = 0.25,
    max_nodes: int = _DEFAULT_MAX_NODES,
    max_export_polls: int = _DEFAULT_MAX_EXPORT_POLLS,
    export_poll_interval: float = 0.5,
    allow_raw_text_fallback: bool = False,
    transport: FeishuTransport | None = None,
    timeout: float = 15.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Synchronize all supported resources while truthfully isolating known capability gaps."""
    resolved_root = root or ROOT
    instance = _connector_instance(project_id, connector_instance_id, resolved_root)
    stored_cursor_hash = _text(
        instance.get("last_committed_cursor_fingerprint"), 128
    )
    if stored_cursor_hash and not previous_cursor:
        raise FeishuConnectorError("feishu_previous_cursor_required")
    profile_ref = _text(instance.get("connection_profile_ref"), 500)
    try:
        profile = resolve_connection_profile(profile_ref)
    except Exception as exc:
        raise FeishuConnectorError(
            f"feishu_connection_profile_resolution_failed:{type(exc).__name__}"
        ) from exc

    client = transport or _default_transport
    access_token, auth_mode = _resolve_access_token(
        profile,
        transport=client,
        timeout=timeout,
        sleeper=sleeper,
    )
    descriptors = discover_feishu_wiki_resources(
        access_token,
        _text(instance.get("resource_scope"), 1000),
        transport=client,
        timeout=timeout,
        max_nodes=max_nodes,
        sleeper=sleeper,
    )
    next_cursor = _snapshot_cursor(descriptors)
    observation_index = connector_snapshot_observation_index(
        project_id,
        connector_instance_id=connector_instance_id,
        root=resolved_root,
    )

    supported_unchanged_observations: list[dict[str, Any]] = []
    coverage_observations: list[dict[str, Any]] = []
    unsupported_resources: list[dict[str, Any]] = []
    pending_materializations: list[tuple[dict[str, Any], str]] = []

    for descriptor in descriptors:
        capability = classify_feishu_resource(descriptor)
        remote_id = _text(descriptor.get("remote_resource_id"), 1000)
        existing = dict(observation_index.get(remote_id) or {})
        if capability.observable_unsupported:
            unsupported_resources.append(
                _unsupported_receipt(descriptor, capability, existing)
            )
            coverage_observations.append(
                _unsupported_coverage_observation(
                    descriptor, capability, existing
                )
            )
            continue

        fingerprint = _materialization_fingerprint(
            descriptor,
            allow_raw_text_fallback=allow_raw_text_fallback,
        )
        existing_metadata = dict(existing.get("source_metadata") or {})
        if (
            existing
            and _text(
                existing_metadata.get("remote_materialization_fingerprint"),
                128,
            )
            == fingerprint
        ):
            supported_unchanged_observations.append(
                _unchanged_observation(descriptor, fingerprint)
            )
            continue
        pending_materializations.append((dict(descriptor), fingerprint))

    items, degraded_count, materialization_worker_count = (
        _materialize_changed_resources(
            pending_materializations,
            access_token,
            transport=client,
            timeout=timeout,
            max_export_polls=max_export_polls,
            export_poll_interval=export_poll_interval,
            allow_raw_text_fallback=allow_raw_text_fallback,
            sleeper=sleeper,
        )
    )

    discovered_count = len(descriptors)
    materialized_count = len(items)
    unchanged_supported_count = len(supported_unchanged_observations)
    unsupported_count = len(unsupported_resources)
    covered_count = materialized_count + unchanged_supported_count
    known_count = covered_count + unsupported_count
    unknown_gap_count = discovered_count - known_count
    if unknown_gap_count != 0:
        raise FeishuConnectorError(
            "feishu_resource_accounting_mismatch:"
            f"discovered={discovered_count}:known={known_count}"
        )

    try:
        run = sync_connector_snapshot_batch(
            project_id,
            connector_instance_id=connector_instance_id,
            items=items,
            unchanged_observations=supported_unchanged_observations,
            coverage_observations=coverage_observations,
            root=resolved_root,
            actor=actor,
            sync_mode="FULL",
            previous_cursor=previous_cursor,
            next_cursor=next_cursor,
            deletion_policy=deletion_policy,
            snapshot_complete=True,
            max_retire_count=max_retire_count,
            max_retire_ratio=max_retire_ratio,
        )
    except ConnectorSyncError as exc:
        raise FeishuConnectorError(f"feishu_sync_rejected:{exc}") from exc

    run_complete = run.get("status") == "COMPLETE"
    coverage_ratio = covered_count / discovered_count if discovered_count else 1.0
    coverage_status = (
        "INCOMPLETE"
        if not run_complete
        else "PARTIAL_UNSUPPORTED"
        if unsupported_count
        else "COMPLETE"
    )
    preserved_count = sum(
        int(bool(row.get("historical_content_retained")))
        for row in unsupported_resources
    )
    return {
        **run,
        "adapter_schema": FEISHU_ADAPTER_SCHEMA,
        "adapter": "feishu",
        "connector_type": FEISHU_CONNECTOR_TYPE,
        "auth_mode": auth_mode,
        "resource_scope": _text(instance.get("resource_scope"), 1000),
        "materialization_capability_contract": (
            FEISHU_MATERIALIZATION_CAPABILITY_VERSION
        ),
        "discovered_resource_count": discovered_count,
        "materialized_resource_count": materialized_count,
        "unchanged_resource_count": unchanged_supported_count,
        "unsupported_resource_count": unsupported_count,
        "preserved_unsupported_occurrence_count": preserved_count,
        "known_resource_count": known_count,
        "unknown_gap_count": unknown_gap_count,
        "covered_resource_count": covered_count,
        "knowledge_coverage_ratio": coverage_ratio,
        "knowledge_coverage_status": coverage_status,
        "knowledge_coverage_complete": run_complete and unsupported_count == 0,
        "remote_discovery_complete": True,
        "supported_materialization_complete": run_complete,
        "unsupported_resources": unsupported_resources,
        "export_avoided_count": unchanged_supported_count,
        "materialization_worker_count": materialization_worker_count,
        "parallel_materialization_used": materialization_worker_count > 1,
        "degraded_resource_count": degraded_count,
        "snapshot_complete": True,
        "next_cursor": next_cursor,
        "next_cursor_persisted_by_adapter": False,
        "connection_profile_ref": profile_ref,
        "credentials_persisted": False,
        "access_token_persisted": False,
        "source_content_persisted_in_adapter_receipt": False,
        "connector_parser_implemented": False,
        "customer_material_access": "NON_MUTATING_READ_ONLY",
        "customer_material_mutation_executed": False,
    }


__all__ = [
    "FEISHU_MATERIALIZATION_CAPABILITY_VERSION",
    "classify_feishu_resource",
    "sync_feishu_connector",
]
