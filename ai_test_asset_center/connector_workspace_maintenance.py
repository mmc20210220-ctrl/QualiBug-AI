"""Bounded, fail-closed cleanup for connector workspace transaction residue.

This authority deletes only stale files that match the atomic-write temporary naming contract.
Checkpoint journals, sync locks, ownership records, run receipts, source packages, and immutable
source bytes remain owned by their existing recovery and retention authorities. Detached immutable
source bytes are counted for diagnostics but never deleted.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from .connector_sync_authority import (
    _load_connector_registry,
    _save_connector_registry,
)
from .connector_sync_ownership import inspect_connector_sync_ownership
from .enterprise_knowledge_center._common import ROOT
from .enterprise_knowledge_center._utils import (
    _load_registry,
    _now,
    _paths,
    _require_manage_actor,
)
from .enterprise_knowledge_center.transaction_lock import (
    KnowledgeTransactionBusy,
    knowledge_transaction,
)
from .real_project_onboarding import _safe_project_id

CONNECTOR_WORKSPACE_MAINTENANCE_SCHEMA = (
    "qualibug.connector-workspace-maintenance.v1"
)


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = _text(os.environ.get(name), 32)
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _retention_seconds() -> int:
    return _env_int(
        "QUALIBUG_CONNECTOR_TEMP_RETENTION_SECONDS",
        24 * 60 * 60,
        60 * 60,
        30 * 24 * 60 * 60,
    )


def _scan_limit() -> int:
    return _env_int(
        "QUALIBUG_CONNECTOR_MAINTENANCE_SCAN_LIMIT",
        10_000,
        100,
        100_000,
    )


def _project_workspace(root: Path, project: str) -> Path:
    return root.resolve() / "platform_workspace" / project


def _knowledge_workspace(root: Path, project: str) -> Path:
    return _project_workspace(root, project) / "enterprise_knowledge_center"


def _maintenance_roots(root: Path, project: str) -> tuple[Path, ...]:
    project_workspace = _project_workspace(root, project)
    return (
        project_workspace / "enterprise_knowledge_center",
        project_workspace / "source_registry",
    )


def _is_atomic_temporary_file(path: Path, *, source_dir: Path) -> bool:
    """Recognize only names created by existing atomic-write helpers.

    A customer may legitimately upload a file whose original name ends in ``.tmp``. Canonical
    source files are therefore eligible only when their generated temporary name starts with a
    dot. Outside the source directory, fixed ``*.json.tmp`` and ``*.txt.tmp`` names are also
    accepted because those are used by the existing registry/blob atomic writers.
    """
    name = path.name
    if not name.endswith(".tmp"):
        return False
    try:
        inside_source_dir = path == source_dir or source_dir in path.parents
    except RuntimeError:
        return False
    if inside_source_dir:
        return name.startswith(".")
    return name.startswith(".") or name.endswith((".json.tmp", ".txt.tmp"))


def _active_connector_state(
    root: Path,
    project: str,
    registry: dict[str, Any],
) -> dict[str, Any]:
    active_epochs = [
        _text(row.get("active_sync_epoch_id"), 160)
        for row in registry.get("connector_instances") or []
        if isinstance(row, dict) and _text(row.get("active_sync_epoch_id"), 160)
    ]
    if active_epochs:
        return {
            "safe": False,
            "reason": "ACTIVE_SYNC_EPOCH",
            "active_sync_count": len(active_epochs),
        }

    workspace = _knowledge_workspace(root, project)
    lock_dir = workspace / "connector_sync_locks"
    if lock_dir.is_dir() and any(
        path.is_file() and not path.is_symlink()
        for path in lock_dir.glob("*.lock")
    ):
        return {"safe": False, "reason": "SYNC_LOCK_PRESENT"}

    journal_dir = workspace / "connector_checkpoint_journal"
    if journal_dir.is_dir() and any(
        path.is_file() and not path.is_symlink()
        for path in journal_dir.glob("*.json")
    ):
        return {"safe": False, "reason": "CHECKPOINT_JOURNAL_PRESENT"}

    for row in registry.get("connector_instances") or []:
        if not isinstance(row, dict):
            continue
        connector = _text(row.get("connector_instance_id"), 160)
        if not connector:
            continue
        try:
            ownership = inspect_connector_sync_ownership(
                project,
                connector,
                root=root,
                stale_after_seconds=30 * 60,
            )
        except Exception:
            return {
                "safe": False,
                "reason": "OWNERSHIP_UNVERIFIED",
                "connector_instance_id": connector,
            }
        if ownership.get("owner_alive") is True:
            return {
                "safe": False,
                "reason": "SYNC_OWNER_ALIVE",
                "connector_instance_id": connector,
            }
        if (
            ownership.get("state") not in {"MISSING", "", "FENCED_OUT"}
            and ownership.get("owner_dead") is not True
        ):
            return {
                "safe": False,
                "reason": "OWNERSHIP_UNVERIFIED",
                "connector_instance_id": connector,
            }
    return {"safe": True, "reason": "NO_ACTIVE_CONNECTOR_MUTATION"}


def _detached_immutable_inventory(
    root: Path,
    project: str,
    *,
    scan_limit: int,
) -> dict[str, Any]:
    paths = _paths(project, root)
    source_dir = paths["source_dir"].resolve()
    registry = _load_registry(project, root)
    referenced = {
        _text(row.get("stored_path"), 2000).replace("\\", "/")
        for row in registry.get("sources") or []
        if isinstance(row, dict) and _text(row.get("stored_path"), 2000)
    }
    count = 0
    byte_count = 0
    scanned = 0
    truncated = False
    if not source_dir.is_dir():
        return {
            "detached_immutable_source_count": 0,
            "detached_immutable_source_bytes": 0,
            "detached_immutable_sources_deleted": False,
            "immutable_inventory_truncated": False,
        }
    for path in source_dir.rglob("*"):
        if scanned >= scan_limit:
            truncated = True
            break
        scanned += 1
        try:
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.resolve().relative_to(root.resolve()).as_posix()
            if relative in referenced or _is_atomic_temporary_file(
                path,
                source_dir=source_dir,
            ):
                continue
            stat = path.stat()
        except (OSError, ValueError):
            continue
        count += 1
        byte_count += max(0, int(stat.st_size))
    return {
        "detached_immutable_source_count": count,
        "detached_immutable_source_bytes": byte_count,
        "detached_immutable_sources_deleted": False,
        "immutable_inventory_truncated": truncated,
    }


def maintain_connector_workspace(
    project_id: str,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    trigger_connector_instance_id: str = "",
) -> dict[str, Any]:
    """Delete stale atomic-write residue without deleting business or recovery artifacts."""
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    source_dir = _paths(project, resolved_root)["source_dir"].resolve()
    retention = _retention_seconds()
    scan_limit = _scan_limit()
    now = time.time()
    removed_count = 0
    removed_bytes = 0
    scanned_count = 0
    cleanup_errors = 0
    scan_truncated = False

    try:
        with knowledge_transaction(
            resolved_root,
            project,
            operation="maintain_connector_workspace",
            actor=clean_actor,
            wait_seconds=1.0,
        ):
            registry = _load_connector_registry(project, resolved_root)
            active = _active_connector_state(
                resolved_root,
                project,
                registry,
            )
            if active.get("safe") is not True:
                return {
                    "schema": CONNECTOR_WORKSPACE_MAINTENANCE_SCHEMA,
                    "status": "SKIPPED_ACTIVE_MUTATION",
                    "project_id": project,
                    "reason": _text(active.get("reason"), 80),
                    "temporary_files_removed": 0,
                    "temporary_bytes_removed": 0,
                    "historical_source_bytes_retained": True,
                    "checkpoint_artifacts_deleted": False,
                    "run_receipts_deleted": False,
                }

            for maintenance_root in _maintenance_roots(resolved_root, project):
                if not maintenance_root.is_dir():
                    continue
                for path in maintenance_root.rglob("*"):
                    if scanned_count >= scan_limit:
                        scan_truncated = True
                        break
                    scanned_count += 1
                    try:
                        if path.is_symlink() or not path.is_file():
                            continue
                        if not _is_atomic_temporary_file(
                            path,
                            source_dir=source_dir,
                        ):
                            continue
                        stat = path.stat()
                        if now - stat.st_mtime < retention:
                            continue
                        size = max(0, int(stat.st_size))
                        path.unlink()
                        removed_count += 1
                        removed_bytes += size
                    except OSError:
                        cleanup_errors += 1
                if scan_truncated:
                    break

            detached = _detached_immutable_inventory(
                resolved_root,
                project,
                scan_limit=scan_limit,
            )
            if removed_count:
                registry = _load_connector_registry(project, resolved_root)
                registry.setdefault("governance", {}).update(
                    {
                        "connector_temporary_residue_cleanup_enabled": True,
                        "temporary_cleanup_uses_atomic_name_contract": True,
                        "runtime_source_registry_temporary_residue_in_scope": True,
                        "checkpoint_artifacts_deleted_by_maintenance": False,
                        "run_receipts_deleted_by_maintenance": False,
                        "detached_immutable_sources_deleted_by_maintenance": False,
                        "historical_source_bytes_retained": True,
                    }
                )
                registry.setdefault("audit_events", []).append(
                    {
                        "event": "maintain_connector_workspace",
                        "at_utc": _now(),
                        "actor": clean_actor,
                        "trigger_connector_instance_id": _text(
                            trigger_connector_instance_id,
                            160,
                        ),
                        "temporary_files_removed": removed_count,
                        "temporary_bytes_removed": removed_bytes,
                        "cleanup_error_count": cleanup_errors,
                        "scan_truncated": scan_truncated,
                        "detached_immutable_source_count": detached[
                            "detached_immutable_source_count"
                        ],
                        "detached_immutable_sources_deleted": False,
                        "checkpoint_artifacts_deleted": False,
                        "run_receipts_deleted": False,
                        "raw_source_names_persisted": False,
                    }
                )
                _save_connector_registry(project, resolved_root, registry)
    except KnowledgeTransactionBusy:
        return {
            "schema": CONNECTOR_WORKSPACE_MAINTENANCE_SCHEMA,
            "status": "DEFERRED_TRANSACTION_BUSY",
            "project_id": project,
            "temporary_files_removed": 0,
            "temporary_bytes_removed": 0,
            "historical_source_bytes_retained": True,
            "checkpoint_artifacts_deleted": False,
            "run_receipts_deleted": False,
        }

    return {
        "schema": CONNECTOR_WORKSPACE_MAINTENANCE_SCHEMA,
        "status": "COMPLETE_WITH_ERRORS" if cleanup_errors else "COMPLETE",
        "project_id": project,
        "temporary_files_removed": removed_count,
        "temporary_bytes_removed": removed_bytes,
        "cleanup_error_count": cleanup_errors,
        "scanned_path_count": scanned_count,
        "scan_truncated": scan_truncated,
        "retention_seconds": retention,
        **detached,
        "historical_source_bytes_retained": True,
        "checkpoint_artifacts_deleted": False,
        "sync_locks_deleted": False,
        "ownership_records_deleted": False,
        "run_receipts_deleted": False,
        "source_packages_deleted": False,
        "raw_source_names_returned": False,
        "second_maintenance_registry_created": False,
    }


__all__ = [
    "CONNECTOR_WORKSPACE_MAINTENANCE_SCHEMA",
    "maintain_connector_workspace",
]
