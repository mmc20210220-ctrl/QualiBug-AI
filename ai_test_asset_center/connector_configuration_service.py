"""Fenced application service for connector creation and reconfiguration.

New connector creation has no previous writer to revoke. Updating an existing connector first
issues a newer fencing token, marks any prior owner FENCED_OUT, runs the existing crash recovery
to abort a stranded epoch without advancing its checkpoint, and only then delegates to the
canonical encrypted profile/configuration authority.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .connector_checkpoint_recovery import recover_connector_checkpoint_commit
from .connector_connection_profiles import (
    configure_connector_profile,
    configure_feishu_connector,
)
from .connector_sync_authority import (
    list_connector_instances,
    register_connector_instance,
)
from .connector_sync_fencing import managed_connector_sync_fence
from .enterprise_knowledge_center._common import ROOT
from .real_project_onboarding import _safe_project_id


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


_SYNC_POLICY_KEYS = {
    "sync_interval_seconds",
    "sync_retry_base_seconds",
    "sync_retry_max_seconds",
    "sync_rate_limit_per_minute",
    "sync_max_resources",
    "sync_max_export_polls",
    "sync_timeout_seconds",
}


def _sync_policy_metadata(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("connector_sync_policy_must_be_object")
    unknown = sorted(set(value) - _SYNC_POLICY_KEYS)
    if unknown:
        raise ValueError(f"connector_sync_policy_field_not_supported:{unknown[0]}")
    return {str(key): item for key, item in value.items()}


def _existing_connector(
    project: str,
    connector: str,
    root: Path,
) -> dict[str, Any] | None:
    rows = list_connector_instances(
        project,
        root=root,
        include_disabled=True,
    ).get("connector_instances") or []
    return next(
        (
            dict(row)
            for row in rows
            if isinstance(row, dict)
            and _text(row.get("connector_instance_id"), 160) == connector
        ),
        None,
    )


def _configure_managed_connector(
    project_id: str,
    *,
    connector_type: str | None,
    configuration_writer,
    connector_instance_id: str,
    resource_scope: str,
    profile: dict[str, Any],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    display_name: str = "",
    status: str = "ACTIVE",
    credential_expires_at_utc: Any = "",
    sync_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create normally or replace an existing configuration under a newer fence."""
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    existing = _existing_connector(project, connector, resolved_root)
    kwargs = {
        "connector_instance_id": connector,
        "resource_scope": resource_scope,
        "profile": profile,
        "root": resolved_root,
        "actor": actor,
        "display_name": display_name,
        "status": status,
        "credential_expires_at_utc": credential_expires_at_utc,
        "instance_metadata": _sync_policy_metadata(sync_policy),
    }
    if connector_type is not None:
        kwargs["connector_type"] = connector_type
    if existing is None:
        result = configuration_writer(project, **kwargs)
        return {
            **result,
            "configuration_write_fencing": "NOT_REQUIRED_FOR_FIRST_CREATION",
            "previous_writer_revoked": False,
        }

    with managed_connector_sync_fence(
        project,
        connector,
        root=resolved_root,
        actor=actor,
        force_takeover=True,
    ) as fence:
        recovery = recover_connector_checkpoint_commit(
            project,
            connector,
            root=resolved_root,
            actor=actor,
            remote_checkpoint_resolver=None,
        )
        result = configuration_writer(project, **kwargs)
        return {
            **result,
            "configuration_write_fencing": "MONOTONIC_REGISTRY_TOKEN",
            "previous_writer_revoked": bool(fence.get("takeover")),
            "previous_sync_recovery_action": _text(
                dict(recovery.get("sync_lifecycle_recovery") or {}).get("action"),
                80,
            ),
            "checkpoint_advanced_by_configuration": False,
            "previous_snapshots_retained": True,
        }


def configure_managed_connector(
    project_id: str,
    *,
    connector_type: str,
    connector_instance_id: str,
    resource_scope: str,
    profile: dict[str, Any],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    display_name: str = "",
    status: str = "ACTIVE",
    credential_expires_at_utc: Any = "",
    sync_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _configure_managed_connector(
        project_id,
        connector_type=connector_type,
        configuration_writer=configure_connector_profile,
        connector_instance_id=connector_instance_id,
        resource_scope=resource_scope,
        profile=profile,
        root=root,
        actor=actor,
        display_name=display_name,
        status=status,
        credential_expires_at_utc=credential_expires_at_utc,
        sync_policy=sync_policy,
    )


def configure_managed_feishu_connector(
    project_id: str,
    *,
    connector_instance_id: str,
    resource_scope: str,
    profile: dict[str, Any],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    display_name: str = "",
    status: str = "ACTIVE",
    credential_expires_at_utc: Any = "",
    sync_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility facade for the generic managed configuration service."""
    return _configure_managed_connector(
        project_id,
        connector_type=None,
        configuration_writer=configure_feishu_connector,
        connector_instance_id=connector_instance_id,
        resource_scope=resource_scope,
        profile=profile,
        root=root,
        actor=actor,
        display_name=display_name,
        status=status,
        credential_expires_at_utc=credential_expires_at_utc,
        sync_policy=sync_policy,
    )


def set_managed_connector_status(
    project_id: str,
    *,
    connector_instance_id: str,
    status: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Change lifecycle state through the existing connector registry authority."""
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    existing = _existing_connector(project, connector, resolved_root)
    if existing is None:
        raise ValueError("connector_instance_not_registered")
    return register_connector_instance(
        project,
        root=resolved_root,
        actor=actor,
        connector_instance_id=connector,
        connector_type=_text(existing.get("connector_type"), 160),
        display_name=_text(existing.get("display_name"), 240),
        resource_scope=_text(existing.get("resource_scope"), 20000),
        connection_profile_ref=_text(
            existing.get("connection_profile_ref"),
            500,
        ),
        metadata=dict(existing.get("metadata") or {}),
        status=_text(status, 32).upper(),
    )


__all__ = [
    "configure_managed_connector",
    "configure_managed_feishu_connector",
    "set_managed_connector_status",
]
