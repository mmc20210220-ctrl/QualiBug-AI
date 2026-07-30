"""Lifecycle operations for the canonical enterprise source registry.

This module does not introduce another registry. It performs status-changing operations
against ``enterprise_source_registry`` using that module's existing persistence, identity
and audit authorities. Immutable blobs and historical audit entries are intentionally kept.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .enterprise_source_registry import (
    _append_audit,
    _atomic_json,
    _now,
    _paths,
    _read_registry,
    _safe_asset_id,
    _safe_project,
)


def deactivate_source_asset(
    project_id: str,
    source_id: str,
    *,
    root: Path,
    actor: dict[str, Any] | None = None,
    reason: str = "source_removed",
) -> dict[str, Any]:
    """Remove an asset from active corpus composition without deleting immutable blobs."""

    project = _safe_project(project_id)
    asset_id = _safe_asset_id(source_id)
    root_path = Path(root)
    registry = _read_registry(root_path, project)
    assets = registry.get("assets")
    if not isinstance(assets, dict):
        return {
            "source_id": asset_id,
            "deactivated": False,
            "reason": "source_registry_assets_invalid",
        }
    removed = assets.pop(asset_id, None)
    if not isinstance(removed, dict):
        return {
            "source_id": asset_id,
            "deactivated": False,
            "reason": "source_asset_not_active",
        }
    source_hash = str(removed.get("latest_source_hash") or "")
    registry["updated_at_utc"] = _now()
    _atomic_json(_paths(root_path, project)["registry"], registry)
    _append_audit(
        root_path,
        project,
        f"source_asset_deactivated:{str(reason or 'source_removed')[:80]}",
        asset_id,
        source_hash,
        actor,
    )
    return {
        "source_id": asset_id,
        "source_hash": source_hash,
        "deactivated": True,
        "immutable_blobs_retained": True,
        "audit_retained": True,
    }


__all__ = ["deactivate_source_asset"]
