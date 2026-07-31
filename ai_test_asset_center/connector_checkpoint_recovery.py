"""Crash-safe checkpoint and stranded RUNNING recovery for online connectors."""
from __future__ import annotations

import calendar
import hashlib
import os
import time
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
    _lock_epoch,
    _remove_sync_lock,
    _save_connector_registry,
    _sync_lock,
    abort_connector_sync_run,
    load_connector_sync_run,
)
from .connector_sync_ownership import (
    ConnectorSyncOwnershipError,
    begin_connector_sync_ownership,
    inspect_connector_sync_ownership,
    stop_connector_sync_ownership,
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


class ConnectorCheckpointRecoveryError(ConnectorProfileError):
    """A connector transaction could not be recovered without guessing."""

    def __init__(self, message: str) -> None:
        normalized = str(message or "connector_checkpoint_recovery_failed")
        normalized = normalized.replace(
            "connector_sync_owner_active",
            "connector_sync_already_running_owner_active",
        )
        normalized = normalized.replace(
            "connector_sync_owner_unverified",
            "connector_sync_lock_held_owner_unverified",
        )
        super().__init__(normalized)


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _fingerprint(value: str) -> str:
    raw = str(value or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""


def _path(root: Path, project: str, connector: str) -> Path:
    return (
        root.resolve()
        / "platform_workspace"
        / project
        / "enterprise_knowledge_center"
        / "connector_checkpoint_journal"
        / f"{connector}.json"
    )


def _read(root: Path, project: str, connector: str) -> dict[str, Any]:
    return _read_json_object(_path(root, project, connector))


def _remove(root: Path, project: str, connector: str) -> None:
    try:
        _path(root, project, connector).unlink(missing_ok=True)
    except OSError as exc:
        raise ConnectorCheckpointRecoveryError(
            "connector_checkpoint_journal_delete_failed"
        ) from exc


def _knowledge_tx(
    root: Path,
    project: str,
    operation: str,
    actor: dict[str, Any],
):
    return knowledge_transaction(
        root,
        project,
        operation=operation,
        actor=actor,
        wait_seconds=5.0,
    )


def _active_epoch(
    root: Path,
    project: str,
    connector: str,
) -> str:
    registry = _load_connector_registry(project, root)
    instance = _instance_by_id(registry, connector)
    return _text(
        instance.get("active_sync_epoch_id") if isinstance(instance, dict) else "",
        160,
    )


def begin_connector_checkpoint_commit(
    project_id: str,
    connector_instance_id: str,
    previous_checkpoint: str,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
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
        "sync_owner_recorded": True,
    }
    ownership_started = False
    try:
        with _knowledge_tx(
            resolved_root,
            project,
            "begin_connector_checkpoint_commit",
            clean_actor,
        ):
            if _read(resolved_root, project, connector):
                raise ConnectorCheckpointRecoveryError(
                    "connector_checkpoint_journal_already_exists"
                )
            path = _path(resolved_root, project, connector)
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_json_object_atomic(path, payload)
            begin_connector_sync_ownership(
                project,
                connector,
                attempt_id,
                root=resolved_root,
                epoch_provider=lambda: _active_epoch(
                    resolved_root,
                    project,
                    connector,
                ),
            )
            ownership_started = True
    except KnowledgeTransactionBusy as exc:
        raise ConnectorCheckpointRecoveryError(
            "connector_checkpoint_recovery_transaction_busy"
        ) from exc
    except ConnectorSyncOwnershipError as exc:
        try:
            _remove(resolved_root, project, connector)
        except Exception:
            pass
        raise ConnectorCheckpointRecoveryError(str(exc)) from exc
    except Exception:
        if ownership_started:
            try:
                stop_connector_sync_ownership(
                    project,
                    connector,
                    root=resolved_root,
                    expected_attempt_id=attempt_id,
                )
            except Exception:
                pass
        raise
    return {
        "ok": True,
        "attempt_id": attempt_id,
        "state": "PREPARED",
        "sync_owner_recorded": True,
        "heartbeat_started": True,
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
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    attempt = _text(attempt_id, 160)
    value = str(checkpoint or "").strip()
    epoch = _text(sync_epoch_id, 160)
    clean_actor = _require_manage_actor(actor)
    if not connector or not attempt or not value or not epoch:
        raise ConnectorCheckpointRecoveryError(
            "connector_checkpoint_stage_fields_required"
        )
    ciphertext = encrypt(value)
    if not is_encrypted(ciphertext):
        raise ConnectorCheckpointRecoveryError(
            "connector_checkpoint_journal_plaintext_refused"
        )
    try:
        with _knowledge_tx(
            resolved_root,
            project,
            "stage_connector_checkpoint_result",
            clean_actor,
        ):
            journal = _read(resolved_root, project, connector)
            if _text(journal.get("attempt_id"), 160) != attempt:
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
                _path(resolved_root, project, connector),
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
    attempt = _text(expected_attempt_id, 160)
    try:
        with _knowledge_tx(
            resolved_root,
            project,
            "clear_connector_checkpoint_journal",
            clean_actor,
        ):
            journal = _read(resolved_root, project, connector)
            if attempt and journal:
                if _text(journal.get("attempt_id"), 160) != attempt:
                    raise ConnectorCheckpointRecoveryError(
                        "connector_checkpoint_journal_attempt_mismatch"
                    )
            if not attempt and journal:
                attempt = _text(journal.get("attempt_id"), 160)
            _remove(resolved_root, project, connector)
    except KnowledgeTransactionBusy as exc:
        raise ConnectorCheckpointRecoveryError(
            "connector_checkpoint_recovery_transaction_busy"
        ) from exc
    try:
        stop_connector_sync_ownership(
            project,
            connector,
            root=resolved_root,
            expected_attempt_id=attempt,
        )
    except ConnectorSyncOwnershipError as exc:
        raise ConnectorCheckpointRecoveryError(str(exc)) from exc
    return {
        "ok": True,
        "cleared": True,
        "heartbeat_stopped": True,
    }


def _decrypt_staged(journal: dict[str, Any]) -> str:
    ciphertext = str(journal.get("checkpoint_ciphertext") or "")
    expected = _text(journal.get("checkpoint_fingerprint"), 128)
    if not ciphertext or not expected:
        return ""
    if not is_encrypted(ciphertext):
        raise ConnectorCheckpointRecoveryError(
            "connector_checkpoint_journal_ciphertext_invalid"
        )
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


def _parse_utc(value: Any) -> float:
    text = _text(value, 80)
    if not text:
        return 0.0
    try:
        return float(calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return 0.0


def _legacy_stale_seconds() -> int:
    raw = _text(os.environ.get("QUALIBUG_CONNECTOR_SYNC_STALE_SECONDS"), 32)
    try:
        value = int(raw) if raw else 30 * 60
    except ValueError:
        value = 30 * 60
    return max(5 * 60, min(value, 7 * 24 * 60 * 60))


def _active_age_seconds(
    root: Path,
    project: str,
    connector: str,
    epoch: str,
    instance: dict[str, Any],
) -> float:
    started = 0.0
    if epoch:
        try:
            run = load_connector_sync_run(
                project,
                connector_instance_id=connector,
                sync_epoch_id=epoch,
                root=root,
            )
        except KeyError:
            run = {}
        started = _parse_utc(run.get("started_at_utc"))
    if not started:
        started = _parse_utc(instance.get("last_sync_started_at_utc"))
    if not started:
        lock_path = (
            root
            / "platform_workspace"
            / project
            / "enterprise_knowledge_center"
            / "connector_sync_locks"
            / f"{connector}.lock"
        )
        try:
            started = lock_path.stat().st_mtime
        except OSError:
            started = 0.0
    return max(0.0, time.time() - started) if started else 0.0


def _recover_stale_sync_lifecycle(
    root: Path,
    project: str,
    connector: str,
    *,
    actor: dict[str, Any],
) -> dict[str, Any]:
    registry = _load_connector_registry(project, root)
    instance = _instance_by_id(registry, connector)
    if instance is None:
        return {
            "action": "NO_REGISTERED_INSTANCE",
            "replay_required": False,
        }
    registry_epoch = _text(instance.get("active_sync_epoch_id"), 160)
    lock_epoch = _lock_epoch(project, connector, root)
    ownership = inspect_connector_sync_ownership(
        project,
        connector,
        root=root,
        stale_after_seconds=max(120, _legacy_stale_seconds() // 4),
    )

    if not registry_epoch and not lock_epoch:
        if ownership.get("owner_alive") is True:
            raise ConnectorCheckpointRecoveryError(
                "connector_sync_owner_active"
            )
        if ownership.get("owner_dead") is True:
            stop_connector_sync_ownership(
                project,
                connector,
                root=root,
                expected_attempt_id=_text(ownership.get("attempt_id"), 160),
            )
            return {
                "action": "REMOVED_DEAD_OWNER_RECORD",
                "replay_required": False,
            }
        if ownership.get("state") not in {"MISSING", ""}:
            raise ConnectorCheckpointRecoveryError(
                "connector_sync_owner_unverified:"
                + (_text(ownership.get("state"), 80) or "UNKNOWN")
            )
        return {"action": "NO_ACTIVE_SYNC", "replay_required": False}

    epoch = registry_epoch or lock_epoch
    age = _active_age_seconds(root, project, connector, epoch, instance)
    owner_dead = ownership.get("owner_dead") is True
    missing_legacy_owner = (
        ownership.get("state") == "MISSING"
        and age >= _legacy_stale_seconds()
    )
    if not owner_dead and not missing_legacy_owner:
        state = _text(ownership.get("state"), 80)
        raise ConnectorCheckpointRecoveryError(
            "connector_sync_owner_active"
            if ownership.get("owner_alive") is True
            else f"connector_sync_owner_unverified:{state or 'UNKNOWN'}"
        )

    if lock_epoch and registry_epoch and lock_epoch != registry_epoch:
        _remove_sync_lock(project, connector, lock_epoch, root)
        lock_epoch = ""

    if registry_epoch:
        abort_connector_sync_run(
            project,
            connector_instance_id=connector,
            reason="automatic recovery: synchronization owner is no longer running",
            root=root,
            actor=actor,
        )
        action = "ABORTED_STRANDED_RUNNING_SYNC"
    elif lock_epoch:
        _remove_sync_lock(project, connector, lock_epoch, root)
        action = "REMOVED_ORPHAN_SYNC_LOCK"
    else:
        action = "NO_ACTIVE_SYNC"

    try:
        stop_connector_sync_ownership(
            project,
            connector,
            root=root,
            expected_attempt_id=_text(ownership.get("attempt_id"), 160),
        )
    except ConnectorSyncOwnershipError:
        stop_connector_sync_ownership(
            project,
            connector,
            root=root,
        )
    return {
        "action": action,
        "replay_required": action in {
            "ABORTED_STRANDED_RUNNING_SYNC",
            "REMOVED_ORPHAN_SYNC_LOCK",
        },
        "owner_state": _text(ownership.get("state"), 80),
        "active_sync_epoch_id": registry_epoch,
        "lock_epoch_id": lock_epoch,
        "previous_snapshots_retained": True,
        "checkpoint_advanced": False,
    }


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
            with _knowledge_tx(
                root,
                project,
                "repair_connector_checkpoint_registry",
                actor,
            ):
                registry = _load_connector_registry(project, root)
                instance = _instance_by_id(registry, connector)
                if instance is None:
                    raise ConnectorSyncError(
                        "connector_instance_not_registered"
                    )
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


def _merge_recovery(
    result: dict[str, Any],
    lifecycle: dict[str, Any],
) -> dict[str, Any]:
    replay = bool(result.get("replay_required")) or bool(
        lifecycle.get("replay_required")
    )
    return {
        **result,
        "replay_required": replay,
        "sync_lifecycle_recovery": lifecycle,
    }


def recover_connector_checkpoint_commit(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    remote_checkpoint_resolver: Callable[[], str] | None = None,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    clean_actor = _require_manage_actor(actor)

    lifecycle = _recover_stale_sync_lifecycle(
        resolved_root,
        project,
        connector,
        actor=clean_actor,
    )
    active = load_connector_sync_checkpoint(
        project,
        connector,
        root=resolved_root,
    )
    active_fingerprint = _fingerprint(active)
    registry_fingerprint, _ = _registry_fingerprint(
        resolved_root,
        project,
        connector,
    )
    journal = _read(resolved_root, project, connector)

    if registry_fingerprint == active_fingerprint and not journal:
        action = (
            lifecycle.get("action")
            if lifecycle.get("replay_required")
            else "CONSISTENT"
        )
        return _merge_recovery(
            {"ok": True, "action": action, "replay_required": False},
            lifecycle,
        )

    if journal:
        previous = _text(
            journal.get("previous_checkpoint_fingerprint"), 128
        )
        staged = _decrypt_staged(journal)
        staged_fingerprint = _fingerprint(staged)
        if staged and registry_fingerprint == staged_fingerprint:
            if active_fingerprint != staged_fingerprint:
                commit_connector_sync_checkpoint(
                    project,
                    connector,
                    staged,
                    sync_epoch_id=_text(journal.get("sync_epoch_id"), 160),
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
            return _merge_recovery(
                {
                    "ok": True,
                    "action": "PROMOTED_STAGED_CHECKPOINT",
                    "replay_required": False,
                },
                lifecycle,
            )
        if registry_fingerprint in {active_fingerprint, previous}:
            clear_connector_checkpoint_journal(
                project,
                connector,
                root=resolved_root,
                actor=clean_actor,
                expected_attempt_id=_text(journal.get("attempt_id"), 160),
            )
            return _merge_recovery(
                {
                    "ok": True,
                    "action": "DISCARDED_UNCOMMITTED_INTENT",
                    "replay_required": False,
                },
                lifecycle,
            )

    remote = ""
    if remote_checkpoint_resolver is not None:
        try:
            remote = str(remote_checkpoint_resolver() or "").strip()
        except Exception:
            remote = ""
    if remote and _fingerprint(remote) == registry_fingerprint:
        epoch = _text(
            (journal or {}).get("sync_epoch_id"), 160
        ) or "sync_recovered_" + uuid.uuid4().hex[:24]
        commit_connector_sync_checkpoint(
            project,
            connector,
            remote,
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
        return _merge_recovery(
            {
                "ok": True,
                "action": "RECONSTRUCTED_REMOTE_CHECKPOINT",
                "replay_required": False,
            },
            lifecycle,
        )

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
    return _merge_recovery(
        {
            "ok": True,
            "action": "ROLLED_BACK_REGISTRY_FOR_SAFE_REPLAY",
            "replay_required": True,
            "source_snapshots_retained": True,
        },
        lifecycle,
    )


__all__ = [
    "CHECKPOINT_JOURNAL_SCHEMA",
    "ConnectorCheckpointRecoveryError",
    "begin_connector_checkpoint_commit",
    "clear_connector_checkpoint_journal",
    "recover_connector_checkpoint_commit",
    "stage_connector_checkpoint_result",
]
