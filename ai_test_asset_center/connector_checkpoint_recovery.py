"""Crash-safe connector checkpoint commit journal and recovery authority.

The source sync registry and encrypted connection profile are separate durable files.
This module closes the crash window between them without introducing a second connector
registry or source pipeline. It records only one per-connector commit intent, encrypts raw
checkpoint values, and either promotes, discards, or compensates the existing registry.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any, Callable

from .connector_connection_profiles import (
    ConnectorProfileError,
    commit_connector_sync_checkpoint,
    load_connector_sync_checkpoint,
)
from .connector_sync_authority import (
    ConnectorSyncError,
    _instance_by_id,
    _load_connector_registry,
    _save_connector_registry,
    _sync_lock,
)
from .credential_crypto import decrypt, encrypt, is_encrypted
from .enterprise_knowledge_center._common import ROOT
from .enterprise_knowledge_center._utils import _now, _require_manage_actor
from .enterprise_knowledge_center.transaction_lock import (
    KnowledgeTransactionBusy,
    knowledge_transaction,
)
from .private_pilot_json_io import _read_json_object, _write_json_object_atomic
from .real_project_onboarding import _safe_project_id

CHECKPOINT_JOURNAL_SCHEMA = "qualibug.connector-checkpoint-commit-journal.v1"


class ConnectorCheckpointRecoveryError(RuntimeError):
    """A checkpoint commit could not be recovered without guessing."""


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _fingerprint(checkpoint: str) -> str:
    value = str(checkpoint or "")
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def _journal_path(root: Path, project: str, connector: str) -> Path:
    return (
        root.resolve()
        / "platform_workspace"
        / project
        / "enterprise_knowledge_center"
        / "connector_checkpoint_journal"
        / f"{connector}.json"
    )


def _read_journal(root: Path, project: str, connector: str) -> dict[str, Any]:
    return _read_json_object(_journal_path(root, project, connector))


def _delete_journal(root: Path, project: str, connector: str) -> None:
    path = _journal_path(root, project, connector)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise ConnectorCheckpointRecoveryError(
            "connector_checkpoint_journal_delete_failed"
        ) from exc


def begin_connector_checkpoint_commit(
    project_id: str,
    connector_instance_id: str,
    previous_checkpoint: str,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a durable intent before remote synchronization starts."""

    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    if not connector:
        raise ConnectorCheckpointRecoveryError("connector_instance_id_required")
    clean_actor = _require_manage_actor(actor)
    attempt_id = "checkpoint_" + uuid.uuid4().hex
    payload = {
        "schema": CHECKPOINT_JOURNAL_SCHEMA,
        "project_id": project,
        "connector_instance_id": connector,
        "attempt_id": attempt_id,
        "state": "PREPARED",
        "previous_checkpoint_fingerprint": _fingerprint(previous_checkpoint),
        "started_at_utc": _now(),
        "updated_at_utc": _now(),
        "checkpoint_ciphertext": "",
        "checkpoint_fingerprint": "",
        "sync_epoch_id": "",
        "plaintext_checkpoint_persisted": False,
    }
    try:
        with knowledge_transaction(
            resolved_root,
            project,
            operation="begin_connector_checkpoint_commit",
            actor=clean_actor,
            wait_seconds=5.0,
        ):
            existing = _read_journal(resolved_root, project, connector)
            if existing:
                raise ConnectorCheckpointRecoveryError(
                    "connector_checkpoint_journal_already_exists"
                )
            path = _journal_path(resolved_root, project, connector)
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_json_object_atomic(path, payload)
    except KnowledgeTransactionBusy as exc:
        raise ConnectorCheckpointRecoveryError(
            "connector_checkpoint_recovery_transaction_busy"
        ) from exc
    return {
        "ok": True,
        "attempt_id": attempt_id,
        "state": "PREPARED",
        "plaintext_checkpoint_persisted": False,
    }


def stage_connector_checkpoint_result(
    project_id: str,
    connector_instance_id: str,
    attempt_id: str,
    checkpoint: str,
    *,
    sync_epoch_id: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Encrypt the coordinator-committed checkpoint before profile promotion."""

    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    attempt = _text(attempt_id, 160)
    value = str(checkpoint or "").strip()
    epoch = _text(sync_epoch_id, 160)
    if not connector or not attempt or not value or not epoch:
        raise ConnectorCheckpointRecoveryError(
            "connector_checkpoint_stage_fields_required"
        )
    clean_actor = _require_manage_actor(actor)
    ciphertext = encrypt(value)
    if not is_encrypted(ciphertext):
        raise ConnectorCheckpointRecoveryError(
            "connector_checkpoint_journal_plaintext_refused"
        )
    try:
        with knowledge_transaction(
            resolved_root,
            project,
            operation="stage_connector_checkpoint_result",
            actor=clean_actor,
            wait_seconds=5.0,
        ):
            journal = _read_journal(resolved_root, project, connector)
            if not journal or _text(journal.get("attempt_id"), 160) != attempt:
                raise ConnectorCheckpointRecoveryError(
                    "connector_checkpoint_journal_attempt_mismatch"
                )
            journal.update(
                {
                    "state": "REGISTRY_COMMITTED_PENDING_PROFILE",
                    "checkpoint_ciphertext": ciphertext,
                    "checkpoint_fingerprint": _fingerprint(value),
                    "sync_epoch_id": epoch,
                    "updated_at_utc": _now(),
                    "plaintext_checkpoint_persisted": False,
                }
            )
            _write_json_object_atomic(
                _journal_path(resolved_root, project, connector),
                journal,
            )
    except KnowledgeTransactionBusy as exc:
        raise ConnectorCheckpointRecoveryError(
            "connector_checkpoint_recovery_transaction_busy"
        ) from exc
    return {
        "ok": True,
        "attempt_id": attempt,
        "state": "REGISTRY_COMMITTED_PENDING_PROFILE",
        "checkpoint_fingerprint": _fingerprint(value),
        "plaintext_checkpoint_persisted": False,
    }


def clear_connector_checkpoint_journal(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    expected_attempt_id: str = "",
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    clean_actor = _require_manage_actor(actor)
    try:
        with knowledge_transaction(
            resolved_root,
            project,
            operation="clear_connector_checkpoint_journal",
            actor=clean_actor,
            wait_seconds=5.0,
        ):
            journal = _read_journal(resolved_root, project, connector)
            if expected_attempt_id and journal:
                if _text(journal.get("attempt_id"), 160) != _text(
                    expected_attempt_id, 160
                ):
                    raise ConnectorCheckpointRecoveryError(
                        "connector_checkpoint_journal_attempt_mismatch"
                    )
            _delete_journal(resolved_root, project, connector)
    except KnowledgeTransactionBusy as exc:
        raise ConnectorCheckpointRecoveryError(
            "connector_checkpoint_recovery_transaction_busy"
        ) from exc
    return {"ok": True, "cleared": True}


def _decrypt_journal_checkpoint(journal: dict[str, Any]) -> str:
    ciphertext = str(journal.get("checkpoint_ciphertext") or "")
    expected = _text(journal.get("checkpoint_fingerprint"), 128)
    if not ciphertext or not is_encrypted(ciphertext) or not expected:
        return ""
    try:
        checkpoint = decrypt(ciphertext)
    except Exception as exc:
        raise ConnectorCheckpointRecoveryError(
            "connector_checkpoint_journal_decryption_failed"
        ) from exc
    if not checkpoint or _fingerprint(checkpoint) != expected:
        raise ConnectorCheckpointRecoveryError(
            "connector_checkpoint_journal_integrity_failed"
        )
    return checkpoint


def _registry_fingerprint(
    root: Path,
    project: str,
    connector: str,
) -> tuple[str, dict[str, Any]]:
    registry = _load_connector_registry(project, root)
    instance = _instance_by_id(registry, connector)
    if instance is None:
        raise ConnectorSyncError("connector_instance_not_registered")
    return _text(instance.get("last_committed_cursor_fingerprint"), 128), instance


def _repair_registry_to_profile(
    root: Path,
    project: str,
    connector: str,
    active_fingerprint: str,
    *,
    actor: dict[str, Any],
    reason: str,
) -> None:
    recovery_epoch = "recovery_" + uuid.uuid4().hex[:24]
    try:
        with _sync_lock(project, connector, recovery_epoch, root):
            with knowledge_transaction(
                root,
                project,
                operation="repair_connector_checkpoint_registry",
                actor=actor,
                wait_seconds=5.0,
            ):
                registry = _load_connector_registry(project, root)
                instance = _instance_by_id(registry, connector)
                if instance is None:
                    raise ConnectorSyncError("connector_instance_not_registered")
                if instance.get("active_sync_epoch_id"):
                    raise ConnectorCheckpointRecoveryError(
                        "connector_checkpoint_recovery_blocked_by_active_sync"
                    )
                previous = _text(
                    instance.get("last_committed_cursor_fingerprint"), 128
                )
                instance["last_committed_cursor_fingerprint"] = active_fingerprint
                instance["checkpoint_recovery_at_utc"] = _now()
                instance["checkpoint_recovery_mode"] = "ROLLBACK_AND_REPLAY"
                registry.setdefault("audit_events", []).append(
                    {
                        "event": "repair_connector_checkpoint_registry",
                        "at_utc": _now(),
                        "actor": actor,
                        "connector_instance_id": connector,
                        "previous_cursor_fingerprint": previous,
                        "restored_cursor_fingerprint": active_fingerprint,
                        "reason": reason,
                        "source_snapshots_retained": True,
                        "full_replay_required": True,
                    }
                )
                _save_connector_registry(project, root, registry)
    except KnowledgeTransactionBusy as exc:
        raise ConnectorCheckpointRecoveryError(
            "connector_checkpoint_recovery_transaction_busy"
        ) from exc
    except ConnectorSyncError as exc:
        raise ConnectorCheckpointRecoveryError(
            "connector_checkpoint_recovery_sync_lock_busy"
        ) from exc


def recover_connector_checkpoint_commit(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    remote_checkpoint_resolver: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Recover all crash windows without asking the user to repair internals."""

    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    clean_actor = _require_manage_actor(actor)
    active_checkpoint = load_connector_sync_checkpoint(
        project, connector, root=resolved_root
    )
    active_fingerprint = _fingerprint(active_checkpoint)
    registry_fingerprint, _ = _registry_fingerprint(
        resolved_root, project, connector
    )
    journal = _read_journal(resolved_root, project, connector)

    if registry_fingerprint == active_fingerprint and not journal:
        return {"ok": True, "action": "CONSISTENT", "replay_required": False}

    if journal:
        previous = _text(
            journal.get("previous_checkpoint_fingerprint"), 128
        )
        staged_checkpoint = _decrypt_journal_checkpoint(journal)
        staged_fingerprint = _fingerprint(staged_checkpoint)
        staged_epoch = _text(journal.get("sync_epoch_id"), 160)

        if staged_checkpoint and registry_fingerprint == staged_fingerprint:
            if active_fingerprint != staged_fingerprint:
                commit_connector_sync_checkpoint(
                    project,
                    connector,
                    staged_checkpoint,
                    sync_epoch_id=staged_epoch,
                    root=resolved_root,
                    actor=clean_actor,
                )
            clear_connector_checkpoint_journal(
                project,
                connector,
                root=resolved_root,
                actor=clean_actor,
                expected_attempt_id=_text(journal.get("attempt_id"), 160),
            )
            return {
                "ok": True,
                "action": "PROMOTED_STAGED_CHECKPOINT",
                "replay_required": False,
            }

        if registry_fingerprint in {active_fingerprint, previous}:
            clear_connector_checkpoint_journal(
                project,
                connector,
                root=resolved_root,
                actor=clean_actor,
                expected_attempt_id=_text(journal.get("attempt_id"), 160),
            )
            return {
                "ok": True,
                "action": "DISCARDED_UNCOMMITTED_INTENT",
                "replay_required": False,
            }

    if remote_checkpoint_resolver is not None:
        try:
            remote_checkpoint = str(remote_checkpoint_resolver() or "").strip()
        except Exception:
            remote_checkpoint = ""
        if remote_checkpoint and _fingerprint(remote_checkpoint) == registry_fingerprint:
            epoch = _text(
                (journal or {}).get("sync_epoch_id"), 160
            ) or "sync_recovered_" + uuid.uuid4().hex[:24]
            commit_connector_sync_checkpoint(
                project,
                connector,
                remote_checkpoint,
                sync_epoch_id=epoch,
                root=resolved_root,
                actor=clean_actor,
            )
            clear_connector_checkpoint_journal(
                project,
                connector,
                root=resolved_root,
                actor=clean_actor,
            )
            return {
                "ok": True,
                "action": "RECONSTRUCTED_REMOTE_CHECKPOINT",
                "replay_required": False,
            }

    _repair_registry_to_profile(
        resolved_root,
        project,
        connector,
        active_fingerprint,
        actor=clean_actor,
        reason="profile checkpoint missing after registry commit",
    )
    clear_connector_checkpoint_journal(
        project,
        connector,
        root=resolved_root,
        actor=clean_actor,
    )
    return {
        "ok": True,
        "action": "ROLLED_BACK_REGISTRY_FOR_SAFE_REPLAY",
        "replay_required": True,
        "source_snapshots_retained": True,
    }


__all__ = [
    "CHECKPOINT_JOURNAL_SCHEMA",
    "ConnectorCheckpointRecoveryError",
    "begin_connector_checkpoint_commit",
    "clear_connector_checkpoint_journal",
    "recover_connector_checkpoint_commit",
    "stage_connector_checkpoint_result",
]
