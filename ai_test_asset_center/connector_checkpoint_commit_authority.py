"""Bind connector cursor checkpoints to durable remote-lifecycle commit decisions.

The generic sync authority remains unchanged for connectors that do not have a second lifecycle
phase.  Feishu uses this composition authority: its material snapshot completes with a hashed
cursor in ``PENDING_LIFECYCLE_COMMIT`` state, then the cursor is made current only after the
remote-lifecycle transaction has a durable COMMITTED decision.  A crash after that decision is
recovered forward; a lifecycle rollback never advances the checkpoint.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Callable

from . import connector_sync_authority as _sync
from .connector_lifecycle_commit_authority import (
    ConnectorLifecycleCommitError,
    commit_connector_lifecycle_transaction,
)
from .enterprise_knowledge_center._common import ROOT, _safe_project_id
from .enterprise_knowledge_center._utils import _now, _require_manage_actor
from .enterprise_knowledge_center.transaction_lock import (
    KnowledgeTransactionBusy,
    knowledge_transaction,
)

CONNECTOR_CHECKPOINT_COMMIT_SCHEMA = "qualibug.connector-checkpoint-commit.v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FINISH_OVERRIDE_LOCK = threading.RLock()


class ConnectorCheckpointCommitError(RuntimeError):
    """A deferred connector checkpoint could not be committed safely."""


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _restore_optional_fields(
    target: dict[str, Any],
    snapshot: dict[str, tuple[bool, Any]],
) -> None:
    for key, (existed, value) in snapshot.items():
        if existed:
            target[key] = value
        else:
            target.pop(key, None)


def _pending_finish(
    original_finish: Callable[..., str],
    project: str,
    connector: str,
    run: dict[str, Any],
    root: Path,
    actor: dict[str, str],
    next_cursor_hash: str,
) -> str:
    """Run the canonical finish path without committing, then mark the hash pending."""
    before_registry = _sync._load_connector_registry(project, root)
    before_instance = _sync._instance_by_id(before_registry, connector)
    if before_instance is None:
        raise ConnectorCheckpointCommitError(
            "checkpoint_pending_connector_instance_missing"
        )
    prior_success = {
        key: (key in before_instance, before_instance.get(key))
        for key in (
            "last_successful_sync_epoch_id",
            "last_successful_sync_at_utc",
        )
    }
    prior_pending_epoch = _text(
        before_instance.get("pending_lifecycle_sync_epoch_id"), 160
    )
    receipt_path = original_finish(
        project,
        connector,
        run,
        root,
        actor,
        "",
    )
    complete = run.get("status") == "COMPLETE"
    pending_hash = _text(next_cursor_hash, 128)
    if not complete or not pending_hash:
        return receipt_path
    if not _SHA256_RE.fullmatch(pending_hash):
        raise ConnectorCheckpointCommitError(
            "checkpoint_pending_cursor_fingerprint_invalid"
        )

    registry = _sync._load_connector_registry(project, root)
    instance = _sync._instance_by_id(registry, connector)
    if instance is None:
        raise ConnectorCheckpointCommitError(
            "checkpoint_pending_connector_instance_disappeared"
        )
    _restore_optional_fields(instance, prior_success)
    completed_at = _text(run.get("completed_at_utc"), 80) or _now()
    epoch = _text(run.get("sync_epoch_id"), 160)
    run.update(
        {
            "cursor_checkpoint_committed": False,
            "committed_cursor_fingerprint": "",
            "previous_cursor_checkpoint_preserved": True,
            "cursor_checkpoint_pending_lifecycle_commit": True,
            "pending_cursor_fingerprint": pending_hash,
            "checkpoint_commit_authority": CONNECTOR_CHECKPOINT_COMMIT_SCHEMA,
            "checkpoint_commit_requires_lifecycle_transaction": True,
        }
    )
    instance.update(
        {
            "pending_lifecycle_sync_epoch_id": epoch,
            "pending_cursor_fingerprint": pending_hash,
            "pending_checkpoint_since_utc": completed_at,
            "last_materialization_sync_epoch_id": epoch,
            "last_materialization_sync_at_utc": completed_at,
        }
    )
    path = _sync._write_run_receipt(project, connector, epoch, root, run)
    _sync._run_summary(registry, run, path)
    summary = next(
        (
            row
            for row in registry.get("sync_runs") or []
            if row.get("sync_epoch_id") == epoch
        ),
        None,
    )
    if summary is None:
        raise ConnectorCheckpointCommitError(
            "checkpoint_pending_sync_summary_missing"
        )
    summary.update(
        {
            "cursor_checkpoint_committed": False,
            "cursor_checkpoint_pending_lifecycle_commit": True,
            "checkpoint_commit_authority": CONNECTOR_CHECKPOINT_COMMIT_SCHEMA,
        }
    )
    registry.setdefault("governance", {}).update(
        {
            "cursor_checkpoint_requires_committed_remote_lifecycle": True,
            "raw_cursor_values_persisted": False,
            "customer_material_mutation_executed": False,
        }
    )
    registry.setdefault("audit_events", []).append(
        {
            "event": "defer_connector_cursor_checkpoint",
            "at_utc": completed_at,
            "actor": actor,
            "connector_instance_id": connector,
            "sync_epoch_id": epoch,
            "superseded_pending_lifecycle_sync_epoch_id": (
                prior_pending_epoch if prior_pending_epoch != epoch else ""
            ),
            "cursor_fingerprint_persisted": True,
            "raw_cursor_value_persisted": False,
            "customer_material_mutation_executed": False,
        }
    )
    _sync._save_connector_registry(project, root, registry)
    return path


def sync_connector_snapshot_batch_deferred(
    project_id: str,
    *,
    connector_instance_id: str,
    items: list[dict[str, Any]],
    unchanged_observations: list[dict[str, Any]] | None = None,
    coverage_observations: list[dict[str, Any]] | None = None,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    sync_mode: str = "INCREMENTAL",
    previous_cursor: str = "",
    next_cursor: str = "",
    deletion_policy: str = "RETAIN",
    snapshot_complete: bool = False,
    max_retire_count: int = 100,
    max_retire_ratio: float = 0.25,
    sync_epoch_id: str = "",
) -> dict[str, Any]:
    """Execute the canonical batch while deferring only its cursor commit."""
    owner_thread = threading.get_ident()
    with _FINISH_OVERRIDE_LOCK:
        original_finish = _sync._finish_run

        def finish_dispatcher(*args: Any, **kwargs: Any) -> str:
            if threading.get_ident() != owner_thread:
                return original_finish(*args, **kwargs)
            return _pending_finish(original_finish, *args, **kwargs)

        _sync._finish_run = finish_dispatcher
        try:
            return _sync.sync_connector_snapshot_batch(
                project_id,
                connector_instance_id=connector_instance_id,
                items=items,
                unchanged_observations=unchanged_observations,
                coverage_observations=coverage_observations,
                root=root,
                actor=actor,
                sync_mode=sync_mode,
                previous_cursor=previous_cursor,
                next_cursor=next_cursor,
                deletion_policy=deletion_policy,
                snapshot_complete=snapshot_complete,
                max_retire_count=max_retire_count,
                max_retire_ratio=max_retire_ratio,
                sync_epoch_id=sync_epoch_id,
            )
        finally:
            if _sync._finish_run is finish_dispatcher:
                _sync._finish_run = original_finish


def _finalize_connector_sync_checkpoint_unlocked(
    project: str,
    connector: str,
    sync_epoch_id: str,
    root: Path,
    actor: dict[str, str],
    lifecycle_transaction_id: str,
) -> dict[str, Any]:
    run = _sync.load_connector_sync_run(
        project,
        connector_instance_id=connector,
        sync_epoch_id=sync_epoch_id,
        root=root,
    )
    lifecycle_commit = dict(run.get("remote_lifecycle_commit") or {})
    if not (
        lifecycle_commit.get("status") == "COMMITTED"
        and lifecycle_commit.get("transaction_id") == lifecycle_transaction_id
    ):
        raise ConnectorCheckpointCommitError(
            "checkpoint_lifecycle_commit_decision_missing"
        )
    pending_hash = _text(run.get("pending_cursor_fingerprint"), 128)
    if run.get("cursor_checkpoint_committed") is True:
        if not (
            _text(run.get("committed_cursor_fingerprint"), 128) == pending_hash
            and run.get("checkpoint_committed_by_lifecycle_transaction_id")
            == lifecycle_transaction_id
        ):
            raise ConnectorCheckpointCommitError(
                "checkpoint_existing_commit_identity_mismatch"
            )
        return {
            "schema": CONNECTOR_CHECKPOINT_COMMIT_SCHEMA,
            "status": "ALREADY_COMMITTED",
            "sync_epoch_id": sync_epoch_id,
            "lifecycle_transaction_id": lifecycle_transaction_id,
            "cursor_checkpoint_committed": True,
        }
    if not (
        run.get("cursor_checkpoint_pending_lifecycle_commit") is True
        and _SHA256_RE.fullmatch(pending_hash)
    ):
        raise ConnectorCheckpointCommitError(
            "checkpoint_pending_state_missing_or_invalid"
        )

    registry = _sync._load_connector_registry(project, root)
    instance = _sync._instance_by_id(registry, connector)
    if instance is None:
        raise ConnectorCheckpointCommitError(
            "checkpoint_connector_instance_missing"
        )
    completed_at = _text(run.get("completed_at_utc"), 80) or _now()
    run.update(
        {
            "cursor_checkpoint_committed": True,
            "committed_cursor_fingerprint": pending_hash,
            "previous_cursor_checkpoint_preserved": False,
            "cursor_checkpoint_pending_lifecycle_commit": False,
            "pending_cursor_fingerprint": "",
            "checkpoint_committed_at_utc": _now(),
            "checkpoint_committed_by_lifecycle_transaction_id": (
                lifecycle_transaction_id
            ),
        }
    )
    instance.update(
        {
            "last_committed_cursor_fingerprint": pending_hash,
            "last_successful_sync_epoch_id": sync_epoch_id,
            "last_successful_sync_at_utc": completed_at,
        }
    )
    if _text(instance.get("pending_lifecycle_sync_epoch_id"), 160) == sync_epoch_id:
        instance["pending_lifecycle_sync_epoch_id"] = ""
        instance["pending_cursor_fingerprint"] = ""
        instance["pending_checkpoint_since_utc"] = ""
    path = _sync._write_run_receipt(
        project,
        connector,
        sync_epoch_id,
        root,
        run,
    )
    _sync._run_summary(registry, run, path)
    summary = next(
        (
            row
            for row in registry.get("sync_runs") or []
            if row.get("sync_epoch_id") == sync_epoch_id
        ),
        None,
    )
    if summary is None:
        raise ConnectorCheckpointCommitError(
            "checkpoint_commit_sync_summary_missing"
        )
    summary.update(
        {
            "cursor_checkpoint_committed": True,
            "cursor_checkpoint_pending_lifecycle_commit": False,
            "checkpoint_committed_by_lifecycle_transaction_id": (
                lifecycle_transaction_id
            ),
            "checkpoint_commit_authority": CONNECTOR_CHECKPOINT_COMMIT_SCHEMA,
        }
    )
    registry.setdefault("audit_events", []).append(
        {
            "event": "commit_connector_cursor_after_remote_lifecycle",
            "at_utc": _now(),
            "actor": actor,
            "connector_instance_id": connector,
            "sync_epoch_id": sync_epoch_id,
            "lifecycle_transaction_id": lifecycle_transaction_id,
            "raw_cursor_value_persisted": False,
            "customer_material_mutation_executed": False,
        }
    )
    _sync._save_connector_registry(project, root, registry)
    verified_run = _sync.load_connector_sync_run(
        project,
        connector_instance_id=connector,
        sync_epoch_id=sync_epoch_id,
        root=root,
    )
    verified_registry = _sync._load_connector_registry(project, root)
    verified_instance = _sync._instance_by_id(verified_registry, connector)
    if not (
        verified_run.get("cursor_checkpoint_committed") is True
        and verified_run.get("cursor_checkpoint_pending_lifecycle_commit") is False
        and verified_run.get("committed_cursor_fingerprint") == pending_hash
        and isinstance(verified_instance, dict)
        and verified_instance.get("last_committed_cursor_fingerprint") == pending_hash
    ):
        raise ConnectorCheckpointCommitError(
            "checkpoint_commit_verification_failed"
        )
    return {
        "schema": CONNECTOR_CHECKPOINT_COMMIT_SCHEMA,
        "status": "COMMITTED",
        "sync_epoch_id": sync_epoch_id,
        "lifecycle_transaction_id": lifecycle_transaction_id,
        "cursor_checkpoint_committed": True,
        "raw_cursor_value_persisted": False,
        "customer_material_mutation_executed": False,
    }


def recover_committed_connector_checkpoint(
    project_id: str,
    *,
    connector_instance_id: str,
    sync_epoch_id: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Finalize a pending hash only when its lifecycle run already says COMMITTED."""
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _sync._identifier(connector_instance_id, "connector_instance_id")
    epoch = _sync._identifier(sync_epoch_id, "sync_epoch_id")
    clean_actor = _require_manage_actor(actor)
    run = _sync.load_connector_sync_run(
        project,
        connector_instance_id=connector,
        sync_epoch_id=epoch,
        root=resolved_root,
    )
    commit = dict(run.get("remote_lifecycle_commit") or {})
    transaction_id = _text(commit.get("transaction_id"), 160)
    if commit.get("status") != "COMMITTED" or not transaction_id:
        return {
            "schema": CONNECTOR_CHECKPOINT_COMMIT_SCHEMA,
            "status": "WAITING_FOR_LIFECYCLE_COMMIT",
            "cursor_checkpoint_committed": False,
        }
    with _sync._sync_lock(
        project,
        connector,
        f"{epoch}.checkpoint_recovery",
        resolved_root,
    ):
        return _finalize_connector_sync_checkpoint_unlocked(
            project,
            connector,
            epoch,
            resolved_root,
            clean_actor,
            transaction_id,
        )


def reconcile_connector_remote_lifecycle_with_checkpoint(
    project_id: str,
    *,
    connector_instance_id: str,
    present_resources: list[dict[str, Any]],
    sync_epoch_id: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    deletion_policy: str = "RETAIN",
    authoritative_snapshot_complete: bool = False,
    retire_after_complete_snapshots: int = 2,
    max_retire_count: int = 100,
    max_retire_ratio: float = 0.25,
    transaction_wait_seconds: float = 30.0,
) -> dict[str, Any]:
    """Commit lifecycle evidence, then its cursor hash, under the same lock ownership."""
    from . import connector_remote_lifecycle as lifecycle

    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _sync._identifier(connector_instance_id, "connector_instance_id")
    epoch = _sync._identifier(sync_epoch_id, "sync_epoch_id")
    clean_actor = _require_manage_actor(actor)
    try:
        with _sync._sync_lock(
            project,
            connector,
            f"{epoch}.remote_lifecycle_checkpoint",
            resolved_root,
        ):
            with knowledge_transaction(
                resolved_root,
                project,
                operation="commit_connector_lifecycle_and_checkpoint",
                actor=clean_actor,
                wait_seconds=float(transaction_wait_seconds),
            ):
                committed = commit_connector_lifecycle_transaction(
                    project,
                    connector_instance_id=connector,
                    sync_epoch_id=epoch,
                    root=resolved_root,
                    actor=clean_actor,
                    apply_lifecycle=lambda: lifecycle._reconcile_connector_remote_lifecycle_unlocked(
                        project,
                        connector_instance_id=connector,
                        present_resources=present_resources,
                        sync_epoch_id=epoch,
                        root=resolved_root,
                        actor=clean_actor,
                        deletion_policy=deletion_policy,
                        authoritative_snapshot_complete=authoritative_snapshot_complete,
                        retire_after_complete_snapshots=retire_after_complete_snapshots,
                        max_retire_count=max_retire_count,
                        max_retire_ratio=max_retire_ratio,
                    ),
                )
                transaction_id = _text(
                    committed.get("lifecycle_commit_transaction_id"), 160
                )
                if not transaction_id:
                    raise ConnectorCheckpointCommitError(
                        "checkpoint_lifecycle_transaction_id_missing"
                    )
                checkpoint = _finalize_connector_sync_checkpoint_unlocked(
                    project,
                    connector,
                    epoch,
                    resolved_root,
                    clean_actor,
                    transaction_id,
                )
                return {
                    **committed,
                    "cursor_checkpoint_committed": True,
                    "cursor_checkpoint_pending_lifecycle_commit": False,
                    "checkpoint_commit": checkpoint,
                    "checkpoint_committed_after_lifecycle_decision": True,
                }
    except KnowledgeTransactionBusy as exc:
        raise ConnectorCheckpointCommitError(
            "checkpoint_lifecycle_knowledge_transaction_busy"
        ) from exc
    except _sync.ConnectorSyncError as exc:
        raise ConnectorCheckpointCommitError(
            "checkpoint_lifecycle_connector_sync_lock_held"
        ) from exc
    except ConnectorLifecycleCommitError:
        raise
    except ConnectorCheckpointCommitError:
        raise
    except Exception as exc:
        raise ConnectorCheckpointCommitError(
            "checkpoint_finalization_pending_recovery"
        ) from exc


__all__ = [
    "CONNECTOR_CHECKPOINT_COMMIT_SCHEMA",
    "ConnectorCheckpointCommitError",
    "recover_committed_connector_checkpoint",
    "reconcile_connector_remote_lifecycle_with_checkpoint",
    "sync_connector_snapshot_batch_deferred",
]
