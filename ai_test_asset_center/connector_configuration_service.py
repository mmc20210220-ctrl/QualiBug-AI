"""Fenced application service for connector creation and reconfiguration.

New connector creation has no previous writer to revoke. Updating an existing connector first
issues a newer fencing token, marks any prior owner FENCED_OUT, runs the existing crash recovery
to abort a stranded epoch without advancing its checkpoint, and only then delegates to the
canonical encrypted profile/configuration authority.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .connector_checkpoint_recovery import recover_connector_checkpoint_commit
from .connector_connection_profiles import configure_feishu_connector
from .connector_sync_authority import list_connector_instances
from .connector_sync_fencing import managed_connector_sync_fence
from .enterprise_knowledge_center._common import ROOT
from .real_project_onboarding import _safe_project_id


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


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
    }
    if existing is None:
        result = configure_feishu_connector(project, **kwargs)
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
        result = configure_feishu_connector(project, **kwargs)
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


__all__ = ["configure_managed_feishu_connector"]
