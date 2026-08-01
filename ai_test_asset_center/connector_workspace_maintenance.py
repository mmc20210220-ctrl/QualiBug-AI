"""Bounded, fail-closed cleanup for connector workspace transaction residue.

This authority deletes only stale files that match the atomic-write temporary naming contract.
Checkpoint journals, sync locks, ownership records, run receipts, source packages, and immutable
source bytes remain owned by their existing recovery and retention authorities. Detached immutable
source bytes are counted for diagnostics but never deleted.
"""
from __future__ import annotations

import logging
import os
import stat
import time
from pathlib import Path
from typing import Any, Iterator

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

logger = logging.getLogger(__name__)

CONNECTOR_WORKSPACE_MAINTENANCE_SCHEMA = (
    "qualibug.connector-workspace-maintenance.v1"
)


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read one bounded integer setting, failing fast on an invalid configured value.

    An unset variable keeps its default; a present-but-illegal value must never
    silently fall back, because an operator typo would otherwise run cleanup with
    the wrong retention or scan budget and leave no signal anywhere.
    """
    raw = _text(os.environ.get(name), 64)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None
    if not minimum <= value <= maximum:
        raise ValueError(f"{name}={value} out of range [{minimum}, {maximum}]")
    return value


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


def _iter_regular_files(root: Path) -> Iterator[Path]:
    """Yield regular files under ``root``, never following directory links.

    ``pathlib.rglob`` recursed into symlinked directories on Python <= 3.11, so a
    link cycle hung the walk and an escape link enumerated paths outside the
    maintenance boundary. ``os.walk(followlinks=False)`` behaves identically on
    every supported Python version; symlinked directories are pruned explicitly so
    a future default change cannot silently widen the walk. Every yielded path is
    resolved so all later boundary checks and the unlink itself share one
    canonical path basis.
    """
    resolved_root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(resolved_root, followlinks=False):
        current = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            candidate = current / name
            try:
                if candidate.is_symlink():
                    continue
            except OSError:
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            candidate = current / name
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                yield candidate.resolve()
            except OSError:
                continue


def _dir_has_regular_file(directory: Path) -> bool:
    """True when a directory contains at least one regular, non-link file."""
    try:
        if directory.is_symlink() or not directory.is_dir():
            return False
    except OSError:
        return False
    try:
        for _ in _iter_regular_files(directory):
            return True
    except OSError:
        return False
    return False


def _is_within(base: Path, candidate: Path) -> bool:
    try:
        resolved_base = base.resolve()
        resolved_candidate = candidate.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved_candidate == resolved_base or resolved_base in resolved_candidate.parents


def _is_atomic_temporary_file(path: Path, *, source_dir: Path) -> bool:
    """Recognize only names created by existing atomic-write helpers.

    A customer may legitimately upload a file whose original name ends in ``.tmp``. Canonical
    source files are therefore eligible only when their generated temporary name starts with a
    dot. Outside the source directory, fixed ``*.json.tmp`` and ``*.txt.tmp`` names are also
    accepted because those are used by the existing registry/blob atomic writers.

    Both ``path`` and ``source_dir`` are resolved here so the membership test uses one
    canonical path basis. The walkers in this module yield resolved paths, but any future
    caller that passes an unresolved path must not silently change which files qualify.
    """
    name = path.name
    if not name.endswith(".tmp"):
        return False
    try:
        resolved_path = path.resolve()
        resolved_source = source_dir.resolve()
    except (OSError, RuntimeError):
        return False
    inside_source_dir = (
        resolved_path == resolved_source or resolved_source in resolved_path.parents
    )
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
    if _dir_has_regular_file(workspace / "connector_sync_locks"):
        return {"safe": False, "reason": "SYNC_LOCK_PRESENT"}

    if _dir_has_regular_file(workspace / "connector_checkpoint_journal"):
        return {"safe": False, "reason": "CHECKPOINT_JOURNAL_PRESENT"}

    # Ownership records are an independent authority living in their own directory.
    # Enumerating the ownership directory -- rather than iterating connector
    # instances from the registry -- keeps cleanup blocked when a live owner exists
    # even if the registry was damaged, rolled back, or externally cleared.
    ownership_dir = workspace / "connector_sync_ownership"
    if _dir_has_regular_file(ownership_dir):
        for path in _iter_regular_files(ownership_dir):
            connector = _text(path.stem, 160)
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
    if not _is_within(root.resolve(), source_dir):
        return {
            "detached_immutable_source_count": 0,
            "detached_immutable_source_bytes": 0,
            "detached_immutable_sources_deleted": False,
            "immutable_inventory_truncated": False,
            "immutable_inventory_path_boundary_blocked": True,
        }
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
            "immutable_inventory_path_boundary_blocked": False,
        }
    for path in _iter_regular_files(source_dir):
        if scanned >= scan_limit:
            truncated = True
            break
        scanned += 1
        try:
            if not _is_within(source_dir, path):
                continue
            relative = path.resolve().relative_to(root.resolve()).as_posix()
            if relative in referenced or _is_atomic_temporary_file(
                path,
                source_dir=source_dir,
            ):
                continue
            stat_result = path.stat()
        except (OSError, ValueError):
            continue
        count += 1
        byte_count += max(0, int(stat_result.st_size))
    return {
        "detached_immutable_source_count": count,
        "detached_immutable_source_bytes": byte_count,
        "detached_immutable_sources_deleted": False,
        "immutable_inventory_truncated": truncated,
        "immutable_inventory_path_boundary_blocked": False,
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
    project_workspace = _project_workspace(resolved_root, project)
    if not _is_within(resolved_root, project_workspace):
        logger.warning(
            "connector workspace maintenance blocked for project %s: "
            "project workspace escapes the deployment root",
            project,
        )
        return {
            "schema": CONNECTOR_WORKSPACE_MAINTENANCE_SCHEMA,
            "status": "BLOCKED_PATH_BOUNDARY",
            "project_id": project,
            "temporary_files_removed": 0,
            "temporary_bytes_removed": 0,
            "path_boundary_enforced": True,
            "historical_source_bytes_retained": True,
            "checkpoint_artifacts_deleted": False,
            "run_receipts_deleted": False,
        }
    # The source directory is the only place where a customer may legitimately
    # upload a file whose name ends in ``.tmp``; if it does not live inside this
    # project workspace, the eligibility rule in _is_atomic_temporary_file would
    # be applied against a path that does not belong to the project. Fail closed.
    source_dir = _paths(project, resolved_root)["source_dir"].resolve()
    if not _is_within(project_workspace, source_dir):
        logger.warning(
            "connector workspace maintenance blocked for project %s: "
            "source directory escapes the project workspace",
            project,
        )
        return {
            "schema": CONNECTOR_WORKSPACE_MAINTENANCE_SCHEMA,
            "status": "BLOCKED_PATH_BOUNDARY",
            "project_id": project,
            "temporary_files_removed": 0,
            "temporary_bytes_removed": 0,
            "path_boundary_enforced": True,
            "historical_source_bytes_retained": True,
            "checkpoint_artifacts_deleted": False,
            "run_receipts_deleted": False,
        }

    now = time.time()
    removed_count = 0
    removed_bytes = 0
    scanned_count = 0
    cleanup_errors = 0
    scan_truncated = False
    retention = 0
    scan_limit = 0
    try:
        retention = _retention_seconds()
        scan_limit = _scan_limit()
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
                reason = _text(active.get("reason"), 80)
                logger.info(
                    "connector workspace maintenance skipped for project %s: %s",
                    project,
                    reason,
                )
                return {
                    "schema": CONNECTOR_WORKSPACE_MAINTENANCE_SCHEMA,
                    "status": "SKIPPED_ACTIVE_MUTATION",
                    "project_id": project,
                    "reason": reason,
                    "temporary_files_removed": 0,
                    "temporary_bytes_removed": 0,
                    "path_boundary_enforced": True,
                    "historical_source_bytes_retained": True,
                    "checkpoint_artifacts_deleted": False,
                    "run_receipts_deleted": False,
                }

            for maintenance_root in _maintenance_roots(resolved_root, project):
                try:
                    if maintenance_root.is_symlink() or not maintenance_root.is_dir():
                        continue
                except OSError:
                    cleanup_errors += 1
                    continue
                try:
                    resolved_maintenance_root = maintenance_root.resolve()
                except OSError:
                    cleanup_errors += 1
                    continue
                if not _is_within(project_workspace, resolved_maintenance_root):
                    cleanup_errors += 1
                    continue
                for path in _iter_regular_files(resolved_maintenance_root):
                    if scanned_count >= scan_limit:
                        scan_truncated = True
                        break
                    scanned_count += 1
                    try:
                        if not _is_within(resolved_maintenance_root, path):
                            continue
                        if not _is_atomic_temporary_file(
                            path,
                            source_dir=source_dir,
                        ):
                            continue
                        metadata = path.lstat()
                        if not stat.S_ISREG(metadata.st_mode):
                            continue
                        if now - metadata.st_mtime < retention:
                            continue
                        size = max(0, int(metadata.st_size))
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
                        "maintenance_path_boundary_enforced": True,
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
                        "path_boundary_enforced": True,
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
        logger.info(
            "connector workspace maintenance deferred for project %s: transaction busy",
            project,
        )
        return {
            "schema": CONNECTOR_WORKSPACE_MAINTENANCE_SCHEMA,
            "status": "DEFERRED_TRANSACTION_BUSY",
            "project_id": project,
            "temporary_files_removed": 0,
            "temporary_bytes_removed": 0,
            "path_boundary_enforced": True,
            "historical_source_bytes_retained": True,
            "checkpoint_artifacts_deleted": False,
            "run_receipts_deleted": False,
        }
    except Exception as exc:
        # The caller treats maintenance as diagnostic cleanup and never lets it
        # mask the business operation, so the failure must leave its own trace:
        # a full exception log here and an explicit FAILED status in the result.
        logger.exception(
            "connector workspace maintenance failed for project %s",
            project,
        )
        return {
            "schema": CONNECTOR_WORKSPACE_MAINTENANCE_SCHEMA,
            "status": "FAILED_MAINTENANCE",
            "project_id": project,
            "error_type": type(exc).__name__,
            "error_detail": _text(exc, 500),
            "temporary_files_removed": removed_count,
            "temporary_bytes_removed": removed_bytes,
            "cleanup_error_count": cleanup_errors,
            "scanned_path_count": scanned_count,
            "scan_truncated": scan_truncated,
            "path_boundary_enforced": True,
            "historical_source_bytes_retained": True,
            "checkpoint_artifacts_deleted": False,
            "run_receipts_deleted": False,
            "second_maintenance_registry_created": False,
        }

    if scan_truncated:
        logger.warning(
            "connector workspace maintenance for project %s truncated at %d scanned paths",
            project,
            scan_limit,
        )
    if cleanup_errors:
        logger.warning(
            "connector workspace maintenance for project %s completed with %d errors",
            project,
            cleanup_errors,
        )
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
        "path_boundary_enforced": True,
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
