"""Atomic commit authority for connector remote-lifecycle evidence.

A lifecycle observation changes several existing authorities: the enterprise knowledge
source-occurrence registry, the connector sync run receipt and summary registry, and—when a
canonical interpretation is reactivated or retired—the runtime source registry.  Per-file atomic
writes are not enough: a process failure between those writes can leave a lifecycle state that is
visible in one authority and absent from another.

This module adds one project-scoped write-ahead transaction without creating another knowledge
registry.  It snapshots only existing registry/receipt files (never source bytes), records a
prepare decision, runs the existing lifecycle algorithm, verifies both public receipts, then
records the durable commit decision.  Before that decision every failure restores the exact prior
files.  After that decision recovery completes the commit instead of rolling it back.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

from .connector_sync_authority import (
    ConnectorSyncError,
    _load_connector_registry,
    _registry_path as connector_registry_path,
    _run_path as connector_run_path,
    _run_summary,
    _save_connector_registry,
    _sync_lock,
    _write_run_receipt,
    load_connector_sync_run,
)
from .enterprise_knowledge_center._common import ROOT, _safe_project_id
from .enterprise_knowledge_center._utils import (
    _load_json,
    _now,
    _paths as knowledge_paths,
    _require_manage_actor,
    _write_json,
)
from .enterprise_knowledge_center.transaction_lock import (
    KnowledgeTransactionBusy,
    knowledge_transaction,
)
from .enterprise_source_registry import _paths as runtime_source_paths

CONNECTOR_LIFECYCLE_COMMIT_SCHEMA = "qualibug.connector-lifecycle-commit.v1"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_FINAL_STATES = {"COMMITTED", "ROLLED_BACK", "ABANDONED_BEFORE_APPLY"}
_LEGACY_TRANSACTION_DIR = "connector_lifecycle_transactions"
_COMPACT_TRANSACTION_DIR = "txn"
_WINDOWS_COMPACT_TRANSACTION_ID_LENGTH = 20
_WINDOWS_MAX_PATH_LENGTH = 260


class ConnectorLifecycleCommitError(RuntimeError):
    """The cross-authority lifecycle transaction could not be completed safely."""


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _identifier(value: Any, field: str) -> str:
    result = _text(value, 160)
    if not _IDENTIFIER_RE.fullmatch(result):
        raise ConnectorLifecycleCommitError(f"{field}_invalid")
    return result


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            # The transaction directory is already deliberately compact on
            # Windows.  Keep tempfile's random name unadorned so staging does
            # not cross MAX_PATH when the final backup path is valid.
            prefix="",
            suffix="",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        temporary = None
        _fsync_directory(path.parent)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _unlink_durable(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(path.parent)


def _within_root(path: Path, root: Path) -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve(strict=False)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ConnectorLifecycleCommitError("lifecycle_commit_path_outside_root")
    return resolved


def _transaction_base(project: str, connector: str, root: Path) -> Path:
    workspace = knowledge_paths(project, root)["workspace"]
    legacy = workspace / _LEGACY_TRANSACTION_DIR / connector
    # The transaction directory is nested below the user-selected artifact root.  On
    # Windows, a normal pytest or application temp root can leave enough room for the
    # run receipt but not for the recovery directory plus its 128-bit transaction id.
    # Keep the established path for normal roots and use a deterministic compact path
    # only when the legacy path would cross the Win32 MAX_PATH boundary.  All callers
    # resolve through this function, so recovery and commit share the same choice.
    if os.name == "nt":
        compact_dir = legacy / "pending" / (
            "x" * _WINDOWS_COMPACT_TRANSACTION_ID_LENGTH
        )
        worst_case = max(
            len(str(compact_dir / "j")),
            len(str(compact_dir / ("x" * 8))),
        )
        if worst_case >= _WINDOWS_MAX_PATH_LENGTH:
            return workspace / _COMPACT_TRANSACTION_DIR / connector
    return legacy


def _pending_root(project: str, connector: str, root: Path) -> Path:
    return _transaction_base(project, connector, root) / "pending"


def _receipt_root(project: str, connector: str, root: Path) -> Path:
    return _transaction_base(project, connector, root) / "receipts"


def _target_paths(
    project: str,
    connector: str,
    sync_epoch_id: str,
    root: Path,
) -> dict[str, Path]:
    runtime = runtime_source_paths(root, project)
    targets = {
        "knowledge_registry": knowledge_paths(project, root)["registry"],
        "runtime_source_registry": runtime["registry"],
        "runtime_source_audit": runtime["audit"],
        "sync_run_receipt": connector_run_path(project, connector, sync_epoch_id, root),
        "connector_registry": connector_registry_path(project, root),
    }
    return {key: _within_root(path, root) for key, path in targets.items()}


def _journal_path(transaction_dir: Path) -> Path:
    compact = transaction_dir / "j"
    legacy = transaction_dir / "journal.json"
    if os.name == "nt" and not compact.exists() and legacy.exists():
        return legacy
    return compact if os.name == "nt" else legacy


def _final_receipt_path(
    project: str,
    connector: str,
    transaction_id: str,
    root: Path,
) -> Path:
    return _receipt_root(project, connector, root) / f"{transaction_id}.json"


def _write_journal(transaction_dir: Path, journal: dict[str, Any]) -> None:
    journal["updated_at_utc"] = _now()
    _write_json(_journal_path(transaction_dir), journal)


def _load_journal(transaction_dir: Path) -> dict[str, Any]:
    payload = _load_json(_journal_path(transaction_dir), {})
    if not isinstance(payload, dict) or payload.get("schema") != CONNECTOR_LIFECYCLE_COMMIT_SCHEMA:
        raise ConnectorLifecycleCommitError("lifecycle_commit_journal_invalid")
    return payload


def _begin_transaction(
    project: str,
    connector: str,
    sync_epoch_id: str,
    root: Path,
    actor: dict[str, str],
) -> tuple[Path, dict[str, Any]]:
    transaction_root = _transaction_base(project, connector, root)
    if os.name == "nt":
        # A URL-safe UUID encoding retains 120 bits in 20 characters.  Windows'
        # legacy path limit makes the shorter opaque identity necessary for
        # deeply nested artifact roots; it remains collision-resistant for
        # local transaction recovery and avoids customer-specific path rules.
        transaction_id = base64.urlsafe_b64encode(uuid.uuid4().bytes).decode(
            "ascii"
        ).rstrip("=")[:_WINDOWS_COMPACT_TRANSACTION_ID_LENGTH]
    else:
        transaction_id = "lctx_" + uuid.uuid4().hex
    transaction_dir = transaction_root / "pending" / transaction_id
    transaction_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(transaction_dir, 0o700)
    targets = _target_paths(project, connector, sync_epoch_id, root)
    journal: dict[str, Any] = {
        "schema": CONNECTOR_LIFECYCLE_COMMIT_SCHEMA,
        "transaction_id": transaction_id,
        "phase": "PREPARING",
        "project_id": project,
        "connector_instance_id": connector,
        "sync_epoch_id": sync_epoch_id,
        "started_at_utc": _now(),
        "actor": actor,
        "apply_started": False,
        "snapshots": [],
        "source_content_backed_up": False,
        "customer_material_mutation_executed": False,
    }
    _write_journal(transaction_dir, journal)
    snapshots: list[dict[str, Any]] = []
    for order, (name, target) in enumerate(targets.items()):
        existed = target.is_file()
        content = target.read_bytes() if existed else b""
        backup_name = f"b{order:02d}"
        if existed:
            _write_bytes_atomic(transaction_dir / backup_name, content)
        snapshots.append(
            {
                "name": name,
                "target_relative_path": str(target.relative_to(root.resolve())).replace("\\", "/"),
                "existed": existed,
                "sha256": _sha256(content) if existed else "ABSENT",
                "backup_name": backup_name if existed else "",
                "backup_complete": True,
            }
        )
    journal["snapshots"] = snapshots
    journal["phase"] = "PREPARED"
    _write_journal(transaction_dir, journal)
    return transaction_dir, journal


def _snapshot_current_hashes(
    project: str,
    connector: str,
    sync_epoch_id: str,
    root: Path,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, path in _target_paths(project, connector, sync_epoch_id, root).items():
        result[name] = _sha256(path.read_bytes()) if path.is_file() else "ABSENT"
    return result


def _restore_snapshots(
    transaction_dir: Path,
    journal: dict[str, Any],
    root: Path,
) -> dict[str, str]:
    restored: dict[str, str] = {}
    # Connector registry is restored last because it is the public summary/index authority.
    priorities = {
        "runtime_source_audit": 0,
        "runtime_source_registry": 1,
        "knowledge_registry": 2,
        "sync_run_receipt": 3,
        "connector_registry": 4,
    }
    snapshots = sorted(
        [row for row in journal.get("snapshots") or [] if isinstance(row, dict)],
        key=lambda row: priorities.get(_text(row.get("name"), 80), 99),
    )
    for snapshot in snapshots:
        relative = _text(snapshot.get("target_relative_path"), 2000)
        target = _within_root(root / relative, root)
        if snapshot.get("existed") is True:
            backup = transaction_dir / _text(snapshot.get("backup_name"), 240)
            if not backup.is_file():
                raise ConnectorLifecycleCommitError("lifecycle_commit_backup_missing")
            content = backup.read_bytes()
            if _sha256(content) != _text(snapshot.get("sha256"), 128):
                raise ConnectorLifecycleCommitError("lifecycle_commit_backup_hash_mismatch")
            _write_bytes_atomic(target, content)
        else:
            _unlink_durable(target)
        actual = _sha256(target.read_bytes()) if target.is_file() else "ABSENT"
        expected = _text(snapshot.get("sha256"), 128)
        if actual != expected:
            raise ConnectorLifecycleCommitError("lifecycle_commit_restore_hash_mismatch")
        restored[_text(snapshot.get("name"), 80)] = actual
    return restored


def _write_final_receipt(
    project: str,
    connector: str,
    transaction_id: str,
    root: Path,
    payload: dict[str, Any],
) -> Path:
    receipt = {
        "schema": CONNECTOR_LIFECYCLE_COMMIT_SCHEMA,
        "transaction_id": transaction_id,
        "project_id": project,
        "connector_instance_id": connector,
        "source_content_backed_up": False,
        "customer_material_mutation_executed": False,
        **payload,
    }
    path = _final_receipt_path(project, connector, transaction_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, receipt)
    return path


def _rollback_transaction(
    transaction_dir: Path,
    journal: dict[str, Any],
    root: Path,
    *,
    reason_code: str,
) -> dict[str, Any]:
    journal["phase"] = "ROLLING_BACK"
    journal["rollback_reason_code"] = _identifier(reason_code, "rollback_reason_code")
    _write_journal(transaction_dir, journal)
    try:
        restored = _restore_snapshots(transaction_dir, journal, root)
        receipt_path = _write_final_receipt(
            _safe_project_id(journal.get("project_id")),
            _identifier(journal.get("connector_instance_id"), "connector_instance_id"),
            _identifier(journal.get("transaction_id"), "transaction_id"),
            root,
            {
                "status": "ROLLED_BACK",
                "sync_epoch_id": _identifier(journal.get("sync_epoch_id"), "sync_epoch_id"),
                "completed_at_utc": _now(),
                "rollback_reason_code": journal["rollback_reason_code"],
                "restored_state_hashes": restored,
                "rollback_verified": True,
            },
        )
        shutil.rmtree(transaction_dir)
        return {
            "status": "ROLLED_BACK",
            "rollback_verified": True,
            "transaction_receipt_path": str(receipt_path.relative_to(root)).replace("\\", "/"),
        }
    except Exception as exc:
        journal["phase"] = "ROLLBACK_BLOCKED"
        journal["rollback_error_type"] = type(exc).__name__
        _write_journal(transaction_dir, journal)
        raise ConnectorLifecycleCommitError("lifecycle_commit_rollback_blocked") from exc


def _projection_payload(
    lifecycle: dict[str, Any],
    *,
    transaction_id: str,
    commit_status: str,
) -> dict[str, Any]:
    projected = dict(lifecycle)
    projected.update(
        {
            "lifecycle_commit_transaction_id": transaction_id,
            "lifecycle_commit_status": commit_status,
            "cross_authority_atomic_commit": True,
            "rollback_required_on_precommit_failure": True,
            "customer_material_mutation_executed": False,
        }
    )
    return projected


def _persist_projection(
    project: str,
    connector: str,
    sync_epoch_id: str,
    root: Path,
    actor: dict[str, str],
    lifecycle: dict[str, Any],
    *,
    transaction_id: str,
    commit_status: str,
) -> dict[str, Any]:
    projected = _projection_payload(
        lifecycle,
        transaction_id=transaction_id,
        commit_status=commit_status,
    )
    run = load_connector_sync_run(
        project,
        connector_instance_id=connector,
        sync_epoch_id=sync_epoch_id,
        root=root,
    )
    run["remote_lifecycle"] = projected
    run["remote_lifecycle_status"] = projected.get("status")
    run["remote_lifecycle_commit"] = {
        "schema": CONNECTOR_LIFECYCLE_COMMIT_SCHEMA,
        "transaction_id": transaction_id,
        "status": commit_status,
        "cross_authority_atomic_commit": True,
        "customer_material_mutation_executed": False,
    }
    path = _write_run_receipt(project, connector, sync_epoch_id, root, run)
    registry = _load_connector_registry(project, root)
    _run_summary(registry, run, path)
    summary = next(
        (
            row
            for row in registry.get("sync_runs") or []
            if row.get("sync_epoch_id") == sync_epoch_id
        ),
        None,
    )
    if summary is None:
        raise ConnectorLifecycleCommitError("lifecycle_commit_sync_summary_missing")
    summary.update(
        {
            "remote_lifecycle_status": projected.get("status"),
            "remote_lifecycle_commit_transaction_id": transaction_id,
            "remote_lifecycle_commit_status": commit_status,
            "remote_lifecycle_evidence_persisted": True,
        }
    )
    registry.setdefault("governance", {}).update(
        {
            "remote_lifecycle_cross_authority_atomic_commit": True,
            "remote_lifecycle_precommit_failure_rolls_back": True,
            "remote_lifecycle_postcommit_failure_recovers_forward": True,
            "customer_material_mutation_executed": False,
        }
    )
    registry.setdefault("audit_events", []).append(
        {
            "event": "persist_connector_lifecycle_commit_projection",
            "at_utc": _now(),
            "actor": actor,
            "connector_instance_id": connector,
            "sync_epoch_id": sync_epoch_id,
            "transaction_id": transaction_id,
            "commit_status": commit_status,
            "customer_material_mutation_executed": False,
        }
    )
    _save_connector_registry(project, root, registry)
    return projected


def _verify_projection(
    project: str,
    connector: str,
    sync_epoch_id: str,
    root: Path,
    *,
    transaction_id: str,
    commit_status: str,
) -> None:
    run = load_connector_sync_run(
        project,
        connector_instance_id=connector,
        sync_epoch_id=sync_epoch_id,
        root=root,
    )
    lifecycle = dict(run.get("remote_lifecycle") or {})
    commit = dict(run.get("remote_lifecycle_commit") or {})
    registry = _load_connector_registry(project, root)
    summary = next(
        (
            row
            for row in registry.get("sync_runs") or []
            if row.get("sync_epoch_id") == sync_epoch_id
        ),
        {},
    )
    if not (
        lifecycle.get("lifecycle_commit_transaction_id") == transaction_id
        and lifecycle.get("lifecycle_commit_status") == commit_status
        and commit.get("transaction_id") == transaction_id
        and commit.get("status") == commit_status
        and summary.get("remote_lifecycle_commit_transaction_id") == transaction_id
        and summary.get("remote_lifecycle_commit_status") == commit_status
        and summary.get("remote_lifecycle_evidence_persisted") is True
    ):
        raise ConnectorLifecycleCommitError("lifecycle_commit_projection_verification_failed")


def _final_receipt(
    project: str,
    connector: str,
    transaction_id: str,
    root: Path,
) -> dict[str, Any]:
    payload = _load_json(
        _final_receipt_path(project, connector, transaction_id, root),
        {},
    )
    return payload if isinstance(payload, dict) else {}


def recover_connector_lifecycle_transactions(
    project_id: str,
    *,
    connector_instance_id: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recover abandoned lifecycle transactions while both project mutation locks are held."""
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    _require_manage_actor(actor)
    pending = _pending_root(project, connector, resolved_root)
    recovered: list[str] = []
    if not pending.exists():
        return {"status": "NOT_REQUIRED", "recovered_transaction_count": 0}
    for transaction_dir in sorted(path for path in pending.iterdir() if path.is_dir()):
        journal = _load_journal(transaction_dir)
        transaction_id = _identifier(journal.get("transaction_id"), "transaction_id")
        final = _final_receipt(project, connector, transaction_id, resolved_root)
        if final.get("status") == "COMMITTED":
            lifecycle = load_connector_sync_run(
                project,
                connector_instance_id=connector,
                sync_epoch_id=_identifier(journal.get("sync_epoch_id"), "sync_epoch_id"),
                root=resolved_root,
            ).get("remote_lifecycle") or {}
            _persist_projection(
                project,
                connector,
                _identifier(journal.get("sync_epoch_id"), "sync_epoch_id"),
                resolved_root,
                _require_manage_actor(actor),
                dict(lifecycle),
                transaction_id=transaction_id,
                commit_status="COMMITTED",
            )
            _verify_projection(
                project,
                connector,
                _identifier(journal.get("sync_epoch_id"), "sync_epoch_id"),
                resolved_root,
                transaction_id=transaction_id,
                commit_status="COMMITTED",
            )
            shutil.rmtree(transaction_dir)
            recovered.append(transaction_id)
            continue
        if final.get("status") in {"ROLLED_BACK", "ABANDONED_BEFORE_APPLY"}:
            shutil.rmtree(transaction_dir)
            recovered.append(transaction_id)
            continue
        if journal.get("apply_started") is not True:
            _write_final_receipt(
                project,
                connector,
                transaction_id,
                resolved_root,
                {
                    "status": "ABANDONED_BEFORE_APPLY",
                    "sync_epoch_id": journal.get("sync_epoch_id"),
                    "completed_at_utc": _now(),
                    "rollback_verified": True,
                },
            )
            shutil.rmtree(transaction_dir)
            recovered.append(transaction_id)
            continue
        _rollback_transaction(
            transaction_dir,
            journal,
            resolved_root,
            reason_code="RECOVER_INCOMPLETE_APPLY",
        )
        recovered.append(transaction_id)
    return {
        "status": "RECOVERED" if recovered else "NOT_REQUIRED",
        "recovered_transaction_count": len(recovered),
        "recovered_transaction_ids": recovered,
    }


def commit_connector_lifecycle_transaction(
    project_id: str,
    *,
    connector_instance_id: str,
    sync_epoch_id: str,
    apply_lifecycle: Callable[[], dict[str, Any]],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the existing lifecycle algorithm under one recoverable cross-file commit."""
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    epoch = _identifier(sync_epoch_id, "sync_epoch_id")
    clean_actor = _require_manage_actor(actor)
    # The lifecycle is meaningful only for an already completed connector run.
    load_connector_sync_run(
        project,
        connector_instance_id=connector,
        sync_epoch_id=epoch,
        root=resolved_root,
    )
    recover_connector_lifecycle_transactions(
        project,
        connector_instance_id=connector,
        root=resolved_root,
        actor=clean_actor,
    )
    transaction_dir, journal = _begin_transaction(
        project,
        connector,
        epoch,
        resolved_root,
        clean_actor,
    )
    transaction_id = journal["transaction_id"]
    journal["phase"] = "APPLYING"
    journal["apply_started"] = True
    _write_journal(transaction_dir, journal)
    commit_decided = False
    try:
        lifecycle = apply_lifecycle()
        if not isinstance(lifecycle, dict):
            raise ConnectorLifecycleCommitError("lifecycle_commit_result_invalid")
        if not (
            lifecycle.get("sync_receipt_persisted") is True
            and lifecycle.get("evidence_persistence_status") == "COMPLETE"
        ):
            raise ConnectorLifecycleCommitError("lifecycle_commit_receipt_not_persisted")
        journal["phase"] = "VERIFYING_PREPARED_PROJECTION"
        _write_journal(transaction_dir, journal)
        prepared = _persist_projection(
            project,
            connector,
            epoch,
            resolved_root,
            clean_actor,
            lifecycle,
            transaction_id=transaction_id,
            commit_status="PREPARED",
        )
        _verify_projection(
            project,
            connector,
            epoch,
            resolved_root,
            transaction_id=transaction_id,
            commit_status="PREPARED",
        )
        journal["phase"] = "COMMIT_DECISION_PENDING"
        _write_journal(transaction_dir, journal)
        _write_final_receipt(
            project,
            connector,
            transaction_id,
            resolved_root,
            {
                "status": "COMMITTED",
                "sync_epoch_id": epoch,
                "committed_at_utc": _now(),
                "before_state_hashes": {
                    row["name"]: row["sha256"]
                    for row in journal.get("snapshots") or []
                    if isinstance(row, dict)
                },
                "prepared_state_hashes": _snapshot_current_hashes(
                    project, connector, epoch, resolved_root
                ),
                "rollback_performed": False,
                "commit_recovery_required": False,
            },
        )
        commit_decided = True
        journal["phase"] = "COMMIT_DECIDED"
        _write_journal(transaction_dir, journal)
        committed = _persist_projection(
            project,
            connector,
            epoch,
            resolved_root,
            clean_actor,
            prepared,
            transaction_id=transaction_id,
            commit_status="COMMITTED",
        )
        _verify_projection(
            project,
            connector,
            epoch,
            resolved_root,
            transaction_id=transaction_id,
            commit_status="COMMITTED",
        )
        shutil.rmtree(transaction_dir)
        return committed
    except Exception as exc:
        if commit_decided or _final_receipt(
            project, connector, transaction_id, resolved_root
        ).get("status") == "COMMITTED":
            journal["phase"] = "COMMIT_FINALIZATION_PENDING"
            journal["commit_finalization_error_type"] = type(exc).__name__
            _write_journal(transaction_dir, journal)
            raise ConnectorLifecycleCommitError(
                "lifecycle_commit_finalization_pending_recovery"
            ) from exc
        _rollback_transaction(
            transaction_dir,
            journal,
            resolved_root,
            reason_code="PRECOMMIT_APPLY_OR_RECEIPT_FAILURE",
        )
        raise ConnectorLifecycleCommitError("lifecycle_commit_rolled_back") from exc


def reconcile_connector_remote_lifecycle_atomic(
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
    """Acquire connector then knowledge locks and atomically commit lifecycle evidence."""
    from . import connector_remote_lifecycle as lifecycle

    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    epoch = _identifier(sync_epoch_id, "sync_epoch_id")
    clean_actor = _require_manage_actor(actor)
    lock_epoch = f"{epoch}.remote_lifecycle"
    try:
        with _sync_lock(project, connector, lock_epoch, resolved_root):
            with knowledge_transaction(
                resolved_root,
                project,
                operation="commit_connector_remote_lifecycle",
                actor=clean_actor,
                wait_seconds=float(transaction_wait_seconds),
            ):
                return commit_connector_lifecycle_transaction(
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
    except KnowledgeTransactionBusy as exc:
        raise ConnectorLifecycleCommitError(
            "lifecycle_commit_knowledge_transaction_busy"
        ) from exc
    except ConnectorSyncError as exc:
        raise ConnectorLifecycleCommitError(
            "lifecycle_commit_connector_sync_lock_held"
        ) from exc


__all__ = [
    "CONNECTOR_LIFECYCLE_COMMIT_SCHEMA",
    "ConnectorLifecycleCommitError",
    "commit_connector_lifecycle_transaction",
    "recover_connector_lifecycle_transactions",
    "reconcile_connector_remote_lifecycle_atomic",
]
