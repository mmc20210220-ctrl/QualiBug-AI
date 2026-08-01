"""Supervisor authority for pending connector lifecycle checkpoints.

This module recovers the boundary between a committed material snapshot and its remote-lifecycle
and cursor decisions. It reuses the connector registry, sync run receipt, lifecycle transaction
journal and content-free recovery intent. It never guesses a missing remote snapshot, never
advances a cursor without a durable lifecycle COMMITTED decision, and persists only bounded
operator status—not raw exceptions or customer content.
"""
from __future__ import annotations

import calendar
import os
import time
import uuid
from pathlib import Path
from typing import Any

from . import connector_sync_authority as _sync
from .connector_checkpoint_commit_authority import (
    recover_committed_connector_checkpoint,
    reconcile_connector_remote_lifecycle_with_checkpoint,
)
from .connector_lifecycle_commit_authority import (
    recover_connector_lifecycle_transactions,
)
from .connector_lifecycle_recovery_intent import (
    clear_connector_lifecycle_recovery_intent,
    load_connector_lifecycle_recovery_intent,
    update_connector_lifecycle_recovery_intent_state,
)
from .enterprise_knowledge_center._common import ROOT, _safe_project_id
from .enterprise_knowledge_center._utils import _now, _require_manage_actor
from .enterprise_knowledge_center.transaction_lock import (
    KnowledgeTransactionBusy,
    knowledge_transaction,
)

CONNECTOR_LIFECYCLE_RECOVERY_SUPERVISOR_SCHEMA = (
    "qualibug.connector-lifecycle-recovery-supervisor.v1"
)


class ConnectorLifecycleRecoverySupervisorError(RuntimeError):
    """A pending lifecycle checkpoint could not be recovered without guessing."""


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = _text(os.environ.get(name), 32)
    try:
        parsed = int(raw) if raw else default
    except ValueError:
        parsed = default
    return max(minimum, min(parsed, maximum))


def lifecycle_pending_stale_seconds() -> int:
    return _env_int(
        "QUALIBUG_CONNECTOR_LIFECYCLE_PENDING_STALE_SECONDS",
        15 * 60,
        60,
        7 * 24 * 60 * 60,
    )


def _parse_utc(value: Any) -> float:
    text = _text(value, 80)
    if not text:
        return 0.0
    try:
        return float(calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return 0.0


def _instance(project: str, connector: str, root: Path) -> dict[str, Any]:
    registry = _sync._load_connector_registry(project, root)
    instance = _sync._instance_by_id(registry, connector)
    if instance is None:
        raise ConnectorLifecycleRecoverySupervisorError(
            "connector_lifecycle_recovery_instance_missing"
        )
    return dict(instance)


def _pending_age(
    instance: dict[str, Any],
    intent: dict[str, Any],
    *,
    now: float,
) -> float:
    started = _parse_utc(instance.get("pending_checkpoint_since_utc"))
    if not started and intent:
        started = _parse_utc(intent.get("created_at_utc"))
    return max(0.0, now - started) if started else 0.0


def inspect_pending_connector_lifecycle_checkpoint(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _sync._identifier(connector_instance_id, "connector_instance_id")
    timestamp = time.time() if now is None else float(now)
    instance = _instance(project, connector, resolved_root)
    intent = load_connector_lifecycle_recovery_intent(
        project,
        connector,
        root=resolved_root,
    )
    pending_epoch = _text(instance.get("pending_lifecycle_sync_epoch_id"), 160)
    intent_epoch = _text(intent.get("sync_epoch_id"), 160)
    age = _pending_age(instance, intent, now=timestamp)
    stale = bool(age >= lifecycle_pending_stale_seconds())
    if not pending_epoch:
        return {
            "schema": CONNECTOR_LIFECYCLE_RECOVERY_SUPERVISOR_SCHEMA,
            "status": "ORPHAN_INTENT" if intent else "NOT_REQUIRED",
            "project_id": project,
            "connector_instance_id": connector,
            "pending_sync_epoch_id": "",
            "intent_sync_epoch_id": intent_epoch,
            "pending_age_seconds": age,
            "stale": stale,
            "attention_required": bool(intent and stale),
            "source_content_inspected": False,
        }
    try:
        run = _sync.load_connector_sync_run(
            project,
            connector_instance_id=connector,
            sync_epoch_id=pending_epoch,
            root=resolved_root,
        )
    except KeyError:
        run = {}
    lifecycle_commit = dict(run.get("remote_lifecycle_commit") or {})
    pending_fingerprint = _text(
        run.get("pending_cursor_fingerprint")
        or instance.get("pending_cursor_fingerprint"),
        128,
    )
    intent_fingerprint = _text(intent.get("next_cursor_fingerprint"), 128)
    intent_matches = bool(
        intent
        and intent_epoch == pending_epoch
        and intent_fingerprint == pending_fingerprint
    )
    if not run:
        status = "BLOCKED_RUN_RECEIPT_MISSING"
    elif run.get("status") != "COMPLETE":
        status = "INCOMPLETE_SNAPSHOT"
    elif lifecycle_commit.get("status") == "COMMITTED":
        status = "COMMITTED_PENDING_CHECKPOINT"
    elif intent_matches:
        status = "READY_TO_REPLAY_LIFECYCLE"
    elif not intent:
        status = "BLOCKED_RECOVERY_INTENT_MISSING"
    else:
        status = "BLOCKED_RECOVERY_INTENT_MISMATCH"
    blocked = status.startswith("BLOCKED_")
    return {
        "schema": CONNECTOR_LIFECYCLE_RECOVERY_SUPERVISOR_SCHEMA,
        "status": status,
        "project_id": project,
        "connector_instance_id": connector,
        "pending_sync_epoch_id": pending_epoch,
        "intent_sync_epoch_id": intent_epoch,
        "run_status": _text(run.get("status"), 40),
        "lifecycle_commit_status": _text(lifecycle_commit.get("status"), 40),
        "lifecycle_transaction_id": _text(
            lifecycle_commit.get("transaction_id"), 160
        ),
        "intent_matches_pending_run": intent_matches,
        "pending_age_seconds": age,
        "stale": stale,
        "attention_required": blocked and stale,
        "source_content_inspected": False,
        "raw_cursor_inspected": False,
    }


def _record_state(
    project: str,
    connector: str,
    root: Path,
    actor: dict[str, str],
    *,
    state: str,
    sync_epoch_id: str,
    pending_age_seconds: float,
    attention_required: bool,
    error_category: str = "",
    increment_failure: bool = False,
) -> None:
    lock_epoch = "lifecycle_recovery_state_" + uuid.uuid4().hex
    try:
        with _sync._sync_lock(project, connector, lock_epoch, root):
            registry = _sync._load_connector_registry(project, root)
            instance = _sync._instance_by_id(registry, connector)
            if instance is None:
                return
            previous_failures = int(
                instance.get("lifecycle_recovery_failure_count") or 0
            )
            instance.update(
                {
                    "lifecycle_recovery_state": _text(state, 80),
                    "lifecycle_recovery_sync_epoch_id": _text(
                        sync_epoch_id, 160
                    ),
                    "lifecycle_recovery_last_attempt_at_utc": _now(),
                    "lifecycle_recovery_pending_age_seconds": int(
                        max(0.0, pending_age_seconds)
                    ),
                    "lifecycle_recovery_attention_required": bool(
                        attention_required
                    ),
                    "lifecycle_recovery_last_error_category": _text(
                        error_category, 120
                    ),
                    "lifecycle_recovery_failure_count": (
                        previous_failures + 1 if increment_failure else 0
                    ),
                    "lifecycle_recovery_raw_error_persisted": False,
                }
            )
            registry.setdefault("audit_events", []).append(
                {
                    "event": "observe_connector_lifecycle_recovery",
                    "at_utc": _now(),
                    "actor": actor,
                    "connector_instance_id": connector,
                    "sync_epoch_id": _text(sync_epoch_id, 160),
                    "state": _text(state, 80),
                    "attention_required": bool(attention_required),
                    "error_category": _text(error_category, 120),
                    "raw_error_persisted": False,
                    "customer_material_mutation_executed": False,
                }
            )
            _sync._save_connector_registry(project, root, registry)
    except Exception:
        return


def _recover_transaction_journals(
    project: str,
    connector: str,
    root: Path,
    actor: dict[str, str],
    sync_epoch_id: str,
) -> dict[str, Any]:
    lock_epoch = f"{sync_epoch_id}.lifecycle_journal_recovery"
    try:
        with _sync._sync_lock(project, connector, lock_epoch, root):
            with knowledge_transaction(
                root,
                project,
                operation="recover_connector_lifecycle_transactions",
                actor=actor,
                wait_seconds=5.0,
            ):
                return recover_connector_lifecycle_transactions(
                    project,
                    connector_instance_id=connector,
                    root=root,
                    actor=actor,
                )
    except KnowledgeTransactionBusy as exc:
        raise ConnectorLifecycleRecoverySupervisorError(
            "connector_lifecycle_recovery_transaction_busy"
        ) from exc
    except _sync.ConnectorSyncError as exc:
        raise ConnectorLifecycleRecoverySupervisorError(
            "connector_lifecycle_recovery_sync_lock_held"
        ) from exc


def _abandon_incomplete_pending_checkpoint(
    project: str,
    connector: str,
    sync_epoch_id: str,
    root: Path,
    actor: dict[str, str],
) -> dict[str, Any]:
    lock_epoch = f"{sync_epoch_id}.abandon_incomplete_lifecycle_checkpoint"
    try:
        with _sync._sync_lock(project, connector, lock_epoch, root):
            with knowledge_transaction(
                root,
                project,
                operation="abandon_incomplete_lifecycle_checkpoint",
                actor=actor,
                wait_seconds=5.0,
            ):
                run = _sync.load_connector_sync_run(
                    project,
                    connector_instance_id=connector,
                    sync_epoch_id=sync_epoch_id,
                    root=root,
                )
                registry = _sync._load_connector_registry(project, root)
                instance = _sync._instance_by_id(registry, connector)
                if instance is None:
                    raise ConnectorLifecycleRecoverySupervisorError(
                        "connector_lifecycle_recovery_instance_missing"
                    )
                run.update(
                    {
                        "cursor_checkpoint_committed": False,
                        "cursor_checkpoint_pending_lifecycle_commit": False,
                        "pending_cursor_fingerprint": "",
                        "previous_cursor_checkpoint_preserved": True,
                        "checkpoint_abandoned": True,
                        "checkpoint_abandon_reason": (
                            "MATERIAL_SNAPSHOT_NOT_COMPLETE"
                        ),
                        "remote_lifecycle_status": (
                            "SKIPPED_INCOMPLETE_MATERIAL_SNAPSHOT"
                        ),
                    }
                )
                if _text(
                    instance.get("pending_lifecycle_sync_epoch_id"), 160
                ) == sync_epoch_id:
                    instance["pending_lifecycle_sync_epoch_id"] = ""
                    instance["pending_cursor_fingerprint"] = ""
                    instance["pending_checkpoint_since_utc"] = ""
                instance["last_failed_sync_epoch_id"] = sync_epoch_id
                instance["last_failed_sync_at_utc"] = _now()
                path = _sync._write_run_receipt(
                    project,
                    connector,
                    sync_epoch_id,
                    root,
                    run,
                )
                _sync._run_summary(registry, run, path)
                registry.setdefault("audit_events", []).append(
                    {
                        "event": "abandon_incomplete_lifecycle_checkpoint",
                        "at_utc": _now(),
                        "actor": actor,
                        "connector_instance_id": connector,
                        "sync_epoch_id": sync_epoch_id,
                        "cursor_checkpoint_advanced": False,
                        "previous_cursor_checkpoint_preserved": True,
                        "customer_material_mutation_executed": False,
                    }
                )
                _sync._save_connector_registry(project, root, registry)
    except KnowledgeTransactionBusy as exc:
        raise ConnectorLifecycleRecoverySupervisorError(
            "connector_lifecycle_recovery_transaction_busy"
        ) from exc
    except _sync.ConnectorSyncError as exc:
        raise ConnectorLifecycleRecoverySupervisorError(
            "connector_lifecycle_recovery_sync_lock_held"
        ) from exc
    return {
        "status": "ABANDONED_INCOMPLETE_SNAPSHOT",
        "sync_epoch_id": sync_epoch_id,
        "cursor_checkpoint_committed": False,
        "previous_cursor_checkpoint_preserved": True,
    }


def recover_pending_connector_lifecycle_checkpoint(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _sync._identifier(connector_instance_id, "connector_instance_id")
    clean_actor = _require_manage_actor(actor)
    timestamp = time.time() if now is None else float(now)
    inspection = inspect_pending_connector_lifecycle_checkpoint(
        project,
        connector,
        root=resolved_root,
        now=timestamp,
    )
    status = inspection["status"]
    epoch = _text(inspection.get("pending_sync_epoch_id"), 160)
    age = float(inspection.get("pending_age_seconds") or 0.0)

    if status == "NOT_REQUIRED":
        return {**inspection, "recovery_action": "NOT_REQUIRED"}
    if status == "ORPHAN_INTENT":
        if inspection.get("stale") is not True:
            return {**inspection, "recovery_action": "WAITING_FOR_SNAPSHOT_BIND"}
        intent = load_connector_lifecycle_recovery_intent(
            project,
            connector,
            root=resolved_root,
        )
        clear_connector_lifecycle_recovery_intent(
            project,
            connector,
            root=resolved_root,
            actor=clean_actor,
            expected_sync_epoch_id=_text(intent.get("sync_epoch_id"), 160),
        )
        _record_state(
            project,
            connector,
            resolved_root,
            clean_actor,
            state="CLEARED_STALE_ORPHAN_INTENT",
            sync_epoch_id=_text(intent.get("sync_epoch_id"), 160),
            pending_age_seconds=age,
            attention_required=False,
        )
        return {
            **inspection,
            "status": "CLEARED_STALE_ORPHAN_INTENT",
            "recovery_action": "CLEARED_STALE_ORPHAN_INTENT",
        }

    failure_recorded = False
    failure_category = ""
    try:
        _recover_transaction_journals(
            project,
            connector,
            resolved_root,
            clean_actor,
            epoch,
        )
        inspection = inspect_pending_connector_lifecycle_checkpoint(
            project,
            connector,
            root=resolved_root,
            now=timestamp,
        )
        status = inspection["status"]
        age = float(inspection.get("pending_age_seconds") or age)

        if status == "COMMITTED_PENDING_CHECKPOINT":
            checkpoint = recover_committed_connector_checkpoint(
                project,
                connector_instance_id=connector,
                sync_epoch_id=epoch,
                root=resolved_root,
                actor=clean_actor,
            )
            intent = load_connector_lifecycle_recovery_intent(
                project,
                connector,
                root=resolved_root,
            )
            if intent and intent.get("sync_epoch_id") == epoch:
                clear_connector_lifecycle_recovery_intent(
                    project,
                    connector,
                    root=resolved_root,
                    actor=clean_actor,
                    expected_sync_epoch_id=epoch,
                )
            _record_state(
                project,
                connector,
                resolved_root,
                clean_actor,
                state="RECOVERED_COMMITTED_CHECKPOINT",
                sync_epoch_id=epoch,
                pending_age_seconds=age,
                attention_required=False,
            )
            return {
                **inspection,
                "status": "COMPLETE",
                "recovery_action": "RECOVERED_COMMITTED_CHECKPOINT",
                "checkpoint_commit": checkpoint,
                "cursor_checkpoint_committed": True,
                "recovery_only": True,
            }

        if status == "READY_TO_REPLAY_LIFECYCLE":
            intent = load_connector_lifecycle_recovery_intent(
                project,
                connector,
                root=resolved_root,
            )
            update_connector_lifecycle_recovery_intent_state(
                project,
                connector,
                state="LIFECYCLE_RECOVERY_RUNNING",
                root=resolved_root,
                actor=clean_actor,
                expected_sync_epoch_id=epoch,
            )
            lifecycle = reconcile_connector_remote_lifecycle_with_checkpoint(
                project,
                connector_instance_id=connector,
                present_resources=list(intent["present_resources"]),
                sync_epoch_id=epoch,
                root=resolved_root,
                actor=clean_actor,
                deletion_policy=_text(intent.get("deletion_policy"), 40),
                authoritative_snapshot_complete=True,
                retire_after_complete_snapshots=int(
                    intent.get("retire_after_complete_snapshots") or 2
                ),
                max_retire_count=int(intent.get("max_retire_count") or 0),
                max_retire_ratio=float(intent.get("max_retire_ratio") or 0.0),
            )
            if lifecycle.get("cursor_checkpoint_committed") is not True:
                raise ConnectorLifecycleRecoverySupervisorError(
                    "connector_lifecycle_recovery_checkpoint_not_committed"
                )
            clear_connector_lifecycle_recovery_intent(
                project,
                connector,
                root=resolved_root,
                actor=clean_actor,
                expected_sync_epoch_id=epoch,
            )
            _record_state(
                project,
                connector,
                resolved_root,
                clean_actor,
                state="REPLAYED_LIFECYCLE_AND_COMMITTED_CHECKPOINT",
                sync_epoch_id=epoch,
                pending_age_seconds=age,
                attention_required=False,
            )
            return {
                **inspection,
                **lifecycle,
                "status": "COMPLETE",
                "recovery_action": (
                    "REPLAYED_LIFECYCLE_AND_COMMITTED_CHECKPOINT"
                ),
                "recovery_only": True,
            }

        if status == "INCOMPLETE_SNAPSHOT":
            result = _abandon_incomplete_pending_checkpoint(
                project,
                connector,
                epoch,
                resolved_root,
                clean_actor,
            )
            intent = load_connector_lifecycle_recovery_intent(
                project,
                connector,
                root=resolved_root,
            )
            if intent and intent.get("sync_epoch_id") == epoch:
                clear_connector_lifecycle_recovery_intent(
                    project,
                    connector,
                    root=resolved_root,
                    actor=clean_actor,
                    expected_sync_epoch_id=epoch,
                )
            _record_state(
                project,
                connector,
                resolved_root,
                clean_actor,
                state="ABANDONED_INCOMPLETE_SNAPSHOT",
                sync_epoch_id=epoch,
                pending_age_seconds=age,
                attention_required=False,
            )
            return {
                **inspection,
                **result,
                "recovery_action": "ABANDONED_INCOMPLETE_SNAPSHOT",
            }

        failure_category = status or "UNKNOWN"
        attention = bool(
            inspection.get("stale")
            and failure_category.startswith("BLOCKED_")
        )
        _record_state(
            project,
            connector,
            resolved_root,
            clean_actor,
            state=failure_category,
            sync_epoch_id=epoch,
            pending_age_seconds=age,
            attention_required=attention,
            error_category=failure_category,
            increment_failure=True,
        )
        failure_recorded = True
        raise ConnectorLifecycleRecoverySupervisorError(
            "connector_lifecycle_recovery_blocked:" + failure_category
        )
    except Exception as exc:
        if not failure_recorded:
            failure_category = type(exc).__name__
            _record_state(
                project,
                connector,
                resolved_root,
                clean_actor,
                state="RECOVERY_RETRYING",
                sync_epoch_id=epoch,
                pending_age_seconds=age,
                attention_required=bool(
                    age >= lifecycle_pending_stale_seconds()
                ),
                error_category=failure_category,
                increment_failure=True,
            )
        prefix = (
            "connector_lifecycle_recovery_blocked:"
            if failure_recorded
            else "connector_lifecycle_recovery_retryable:"
        )
        raise ConnectorLifecycleRecoverySupervisorError(
            prefix + failure_category
        ) from exc


__all__ = [
    "CONNECTOR_LIFECYCLE_RECOVERY_SUPERVISOR_SCHEMA",
    "ConnectorLifecycleRecoverySupervisorError",
    "inspect_pending_connector_lifecycle_checkpoint",
    "lifecycle_pending_stale_seconds",
    "recover_pending_connector_lifecycle_checkpoint",
]
