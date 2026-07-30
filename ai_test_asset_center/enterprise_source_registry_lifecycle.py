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


def rollback_source_asset_activation(
    project_id: str,
    source_id: str,
    *,
    root: Path,
    actor: dict[str, Any] | None = None,
    restore_source_hash: str = "",
    restore_version_id: str = "",
    reason: str = "activation_transaction_rolled_back",
) -> dict[str, Any]:
    """Restore the previous active version, or deactivate a first-version asset.

    Registration is immutable, so a failed outer transaction never deletes the newly
    written version or blob. It only restores which version participates in corpus
    composition. This prevents a failed update from removing the prior valid source.
    """

    project = _safe_project(project_id)
    asset_id = _safe_asset_id(source_id)
    root_path = Path(root)
    registry = _read_registry(root_path, project)
    assets = registry.get("assets")
    if not isinstance(assets, dict):
        return {
            "source_id": asset_id,
            "rolled_back": False,
            "reason": "source_registry_assets_invalid",
        }
    asset = assets.get(asset_id)
    if not isinstance(asset, dict):
        return {
            "source_id": asset_id,
            "rolled_back": False,
            "reason": "source_asset_not_active",
        }

    restore_hash = str(restore_source_hash or "").strip().lower()
    restore_version = str(restore_version_id or "").strip()
    versions = [row for row in asset.get("versions") or [] if isinstance(row, dict)]
    candidate = None
    if restore_hash or restore_version:
        candidate = next(
            (
                row
                for row in reversed(versions)
                if (not restore_hash or str(row.get("source_hash") or "") == restore_hash)
                and (not restore_version or str(row.get("version_id") or "") == restore_version)
            ),
            None,
        )
    previous_latest_hash = str(asset.get("latest_source_hash") or "")
    if candidate is None:
        assets.pop(asset_id, None)
        outcome = "deactivated_no_previous_version"
        active_hash = ""
        active_version = ""
    else:
        active_hash = str(candidate.get("source_hash") or "")
        active_version = str(candidate.get("version_id") or "")
        asset["latest_source_hash"] = active_hash
        asset["latest_version_id"] = active_version
        asset["source_type"] = str(candidate.get("source_type") or asset.get("source_type") or "")
        asset["updated_at_utc"] = _now()
        assets[asset_id] = asset
        outcome = "previous_version_restored"

    registry["updated_at_utc"] = _now()
    _atomic_json(_paths(root_path, project)["registry"], registry)
    _append_audit(
        root_path,
        project,
        f"source_asset_activation_rolled_back:{str(reason or 'transaction_rolled_back')[:80]}",
        asset_id,
        active_hash or previous_latest_hash,
        actor,
    )
    return {
        "source_id": asset_id,
        "rolled_back": True,
        "outcome": outcome,
        "restored_source_hash": active_hash,
        "restored_version_id": active_version,
        "immutable_versions_retained": True,
        "audit_retained": True,
    }


__all__ = ["deactivate_source_asset", "rollback_source_asset_activation"]
