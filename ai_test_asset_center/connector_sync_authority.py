"""Connector instance and online-source synchronization authority.

Adapters fetch remote data outside this module. This authority stores no credentials, raw
cursor values, or source content; it delegates every material snapshot to the existing
source-occurrence ingestion and lifecycle authorities.
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .connector_source_ingestion import (
    ConnectorSnapshotError,
    build_connector_source_ref,
    ingest_connector_snapshots_batch,
)
from .enterprise_knowledge_center import (
    delete_enterprise_knowledge_source,
    list_enterprise_knowledge_sources,
)
from .enterprise_knowledge_center.source_occurrence_observation import (
    list_source_occurrence_observations,
    record_source_occurrence_observations_batch,
)
from .enterprise_knowledge_center._common import ROOT, _safe_project_id
from .enterprise_knowledge_center._utils import (
    _load_json,
    _now,
    _redact_text,
    _require_manage_actor,
    _short_hash,
    _write_json,
)

CONNECTOR_SYNC_REGISTRY_SCHEMA = "qualibug.connector-sync-registry.v1"
CONNECTOR_INSTANCE_SCHEMA = "qualibug.connector-instance.v1"
CONNECTOR_SYNC_RUN_SCHEMA = "qualibug.connector-sync-run.v1"

_SYNC_MODES = {"FULL", "INCREMENTAL"}
_DELETION_POLICIES = {"RETAIN", "RETIRE_MISSING"}
_INSTANCE_STATUSES = {"ACTIVE", "PAUSED", "DISABLED"}
_COVERAGE_STATES = {"UNSUPPORTED"}
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_METADATA_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,119}$")
_SECRET_KEY_RE = re.compile(
    r"(?i)(?:authorization|password|passwd|secret|token|cookie|api[_-]?key|credential)"
)
_CONNECTION_REF_PREFIXES = ("connection-profile://", "vault-ref://")


class ConnectorSyncError(RuntimeError):
    """Connector registration or synchronization could not complete safely."""


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _identifier(value: Any, field: str) -> str:
    result = _text(value, 160)
    if not _IDENTIFIER_RE.fullmatch(result):
        raise ConnectorSyncError(f"{field}_invalid")
    return result


def _cursor_hash(value: Any) -> str:
    raw = str(value or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""


def _workspace(project: str, root: Path) -> Path:
    return root / "platform_workspace" / project / "enterprise_knowledge_center"


def _registry_path(project: str, root: Path) -> Path:
    return _workspace(project, root) / "connector_sync_registry.json"


def _run_path(project: str, connector: str, epoch: str, root: Path) -> Path:
    return _workspace(project, root) / "connector_sync_runs" / connector / f"{epoch}.json"


def _lock_path(project: str, connector: str, root: Path) -> Path:
    return _workspace(project, root) / "connector_sync_locks" / f"{connector}.lock"


def _lock_epoch(project: str, connector: str, root: Path) -> str:
    path = _lock_path(project, connector, root)
    try:
        return _text(path.read_text(encoding="utf-8"), 160)
    except OSError:
        return ""


def _remove_sync_lock(project: str, connector: str, epoch: str, root: Path) -> None:
    path = _lock_path(project, connector, root)
    try:
        if path.is_file() and (not epoch or _lock_epoch(project, connector, root) == epoch):
            path.unlink()
    except OSError:
        pass


@contextmanager
def _sync_lock(project: str, connector: str, epoch: str, root: Path):
    path = _lock_path(project, connector, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ConnectorSyncError("connector_sync_lock_held") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(epoch)
        yield
    finally:
        _remove_sync_lock(project, connector, epoch, root)


def _registry_default(project: str) -> dict[str, Any]:
    return {
        "schema": CONNECTOR_SYNC_REGISTRY_SCHEMA,
        "project_id": project,
        "created_at_utc": _now(),
        "updated_at_utc": _now(),
        "connector_instances": [],
        "sync_runs": [],
        "audit_events": [],
        "governance": {
            "network_access_outside_sync_authority": True,
            "connector_credentials_persisted": False,
            "raw_cursor_values_persisted": False,
            "source_content_persisted_in_sync_registry": False,
            "source_occurrence_is_material_identity_authority": True,
            "missing_source_retirement_requires_complete_full_snapshot": True,
            "coverage_observations_create_source_occurrences": False,
        },
    }


def _load_connector_registry(project_id: str, root: Path) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    raw = _load_json(_registry_path(project, root), {})
    registry = _registry_default(project)
    if isinstance(raw, dict):
        registry.update(raw)
    for key in ("connector_instances", "sync_runs", "audit_events"):
        registry[key] = [row for row in registry.get(key) or [] if isinstance(row, dict)]
    governance = dict(registry.get("governance") or {})
    governance.update(_registry_default(project)["governance"])
    registry["governance"] = governance
    return registry


def _save_connector_registry(project_id: str, root: Path, registry: dict[str, Any]) -> None:
    project = _safe_project_id(project_id)
    path = _registry_path(project, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    registry["updated_at_utc"] = _now()
    _write_json(path, registry)


def _write_run_receipt(
    project_id: str,
    connector_instance_id: str,
    sync_epoch_id: str,
    root: Path,
    receipt: dict[str, Any],
) -> str:
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    epoch = _identifier(sync_epoch_id, "sync_epoch_id")
    path = _run_path(project, connector, epoch, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, receipt)
    return str(path.relative_to(root)).replace("\\", "/")


def load_connector_sync_run(
    project_id: str,
    *,
    connector_instance_id: str,
    sync_epoch_id: str,
    root: Path | None = None,
) -> dict[str, Any]:
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    epoch = _identifier(sync_epoch_id, "sync_epoch_id")
    run = _load_json(_run_path(project, connector, epoch, resolved_root), {})
    if not isinstance(run, dict) or not run:
        raise KeyError(f"connector sync run not found: {epoch}")
    return run


def _instance_by_id(registry: dict[str, Any], connector: str) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in registry.get("connector_instances") or []
            if _text(row.get("connector_instance_id"), 160) == connector
        ),
        None,
    )


def _sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise ConnectorSyncError("connector_instance_metadata_must_be_object")
    if len(metadata) > 40:
        raise ConnectorSyncError("connector_instance_metadata_field_limit_exceeded")
    result: dict[str, Any] = {}
    for raw_key, value in metadata.items():
        key = _text(raw_key, 120)
        if not _METADATA_KEY_RE.fullmatch(key):
            raise ConnectorSyncError(f"connector_instance_metadata_key_invalid:{key or 'empty'}")
        if _SECRET_KEY_RE.search(key):
            raise ConnectorSyncError(f"connector_instance_metadata_secret_key_rejected:{key}")
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            result[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            result[key] = value
        elif isinstance(value, str):
            bounded = value[:2000]
            if _redact_text(bounded, 2000) != bounded:
                raise ConnectorSyncError(
                    f"connector_instance_metadata_secret_value_rejected:{key}"
                )
            result[key] = bounded
        else:
            raise ConnectorSyncError(f"connector_instance_metadata_value_invalid:{key}")
    return result


def _profile_ref(value: Any) -> str:
    result = _text(value, 500)
    if not result:
        return ""
    if (
        not result.startswith(_CONNECTION_REF_PREFIXES)
        or "?" in result
        or "#" in result
        or any(ch.isspace() for ch in result)
    ):
        raise ConnectorSyncError("connector_connection_profile_ref_invalid")
    return result


def register_connector_instance(
    project_id: str,
    *,
    connector_instance_id: str,
    connector_type: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    display_name: str | None = None,
    resource_scope: str | None = None,
    connection_profile_ref: str | None = None,
    metadata: dict[str, Any] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Create or update one project-scoped connector instance."""
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    connector_kind = _identifier(connector_type, "connector_type")
    requested_status = _text(status, 32).upper()
    if requested_status and requested_status not in _INSTANCE_STATUSES:
        raise ConnectorSyncError("connector_instance_status_invalid")

    registry = _load_connector_registry(project, resolved_root)
    instance = _instance_by_id(registry, connector)
    created = instance is None
    now = _now()
    if instance is None:
        instance = {
            "schema": CONNECTOR_INSTANCE_SCHEMA,
            "connector_instance_id": connector,
            "connector_type": connector_kind,
            "created_at_utc": now,
            "created_by": clean_actor,
            "active_sync_epoch_id": "",
            "last_committed_cursor_fingerprint": "",
            "last_successful_sync_epoch_id": "",
            "last_failed_sync_epoch_id": "",
        }
        registry["connector_instances"].append(instance)
    elif _text(instance.get("connector_type"), 160) != connector_kind:
        raise ConnectorSyncError("connector_instance_type_is_immutable")

    normalized_status = requested_status or _text(instance.get("status"), 32) or "ACTIVE"
    if instance.get("active_sync_epoch_id") and normalized_status != "ACTIVE":
        raise ConnectorSyncError("connector_instance_status_change_blocked_during_sync")
    if display_name is not None:
        instance["display_name"] = _text(display_name, 240)
    else:
        instance.setdefault("display_name", "")
    if resource_scope is not None:
        instance["resource_scope"] = _text(resource_scope, 20000)
    else:
        instance.setdefault("resource_scope", "")
    if connection_profile_ref is not None:
        instance["connection_profile_ref"] = _profile_ref(connection_profile_ref)
    else:
        instance.setdefault("connection_profile_ref", "")
    if metadata is not None:
        instance["metadata"] = _sanitize_metadata(metadata)
    else:
        instance.setdefault("metadata", {})
    instance.update(
        {
            "status": normalized_status,
            "updated_at_utc": now,
            "updated_by": clean_actor,
            "credentials_persisted": False,
        }
    )
    registry["audit_events"].append(
        {
            "event": "register_connector_instance" if created else "update_connector_instance",
            "at_utc": now,
            "actor": clean_actor,
            "connector_instance_id": connector,
            "connector_type": connector_kind,
            "status": normalized_status,
            "credentials_persisted": False,
        }
    )
    _save_connector_registry(project, resolved_root, registry)
    return {
        "ok": True,
        "created": created,
        "connector_instance": dict(instance),
        "credentials_persisted": False,
    }


def list_connector_instances(
    project_id: str,
    *,
    root: Path | None = None,
    include_disabled: bool = False,
) -> dict[str, Any]:
    registry = _load_connector_registry(project_id, root or ROOT)
    rows = [
        dict(row)
        for row in registry["connector_instances"]
        if include_disabled or row.get("status") != "DISABLED"
    ]
    return {
        "schema": CONNECTOR_SYNC_REGISTRY_SCHEMA,
        "project_id": registry["project_id"],
        "connector_instances": rows,
        "summary": {
            "connector_instance_count": len(rows),
            "active_count": sum(row.get("status") == "ACTIVE" for row in rows),
            "running_count": sum(bool(row.get("active_sync_epoch_id")) for row in rows),
        },
        "governance": dict(registry.get("governance") or {}),
    }


def list_connector_sync_runs(
    project_id: str,
    *,
    connector_instance_id: str,
    root: Path | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Return bounded, cursor-free run summaries for one connector instance."""
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ConnectorSyncError("connector_sync_run_limit_invalid")
    registry = _load_connector_registry(project, resolved_root)
    runs = [
        dict(row)
        for row in registry.get("sync_runs") or []
        if isinstance(row, dict)
        and _text(row.get("connector_instance_id"), 160) == connector
    ]
    runs.sort(
        key=lambda row: _text(
            row.get("completed_at_utc") or row.get("started_at_utc"),
            80,
        ),
        reverse=True,
    )
    public_fields = {
        "sync_epoch_id",
        "connector_instance_id",
        "sync_mode",
        "status",
        "started_at_utc",
        "completed_at_utc",
        "item_count",
        "success_count",
        "materialized_success_count",
        "unchanged_success_count",
        "coverage_observation_count",
        "knowledge_coverage_status",
        "failure_count",
        "retired_count",
        "cursor_checkpoint_committed",
    }
    safe_runs: list[dict[str, Any]] = []
    for row in runs[:limit]:
        safe = {
            key: row[key]
            for key in public_fields
            if key in row
        }
        safe["raw_cursor_returned"] = False
        safe["source_content_returned"] = False
        safe_runs.append(safe)
    return {
        "schema": "qualibug.connector-sync-run-inventory.v1",
        "project_id": project,
        "connector_instance_id": connector,
        "runs": safe_runs,
        "truncated": len(runs) > len(safe_runs),
        "raw_cursor_returned": False,
        "source_content_returned": False,
        "credential_values_returned": False,
    }


def _new_epoch(project: str, connector: str) -> str:
    return "sync_" + _short_hash(
        {
            "project": project,
            "connector_instance_id": connector,
            "started_at_utc": _now(),
            "nonce": uuid.uuid4().hex,
        },
        28,
    )


def _connector_prefix(connector: str) -> str:
    return "connector://" + quote(connector, safe="._-") + "/"


def _active_refs(project: str, connector: str, root: Path) -> set[str]:
    inventory = list_enterprise_knowledge_sources(project, root=root, include_deleted=False)
    prefix = _connector_prefix(connector)
    return {
        _text(row.get("source_ref"), 2000)
        for row in inventory.get("sources") or []
        if isinstance(row, dict) and _text(row.get("source_ref"), 2000).startswith(prefix)
    }


def connector_snapshot_observation_index(
    project_id: str,
    *,
    connector_instance_id: str,
    root: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Index active connector occurrences by remote identity without loading source bytes."""
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    payload = list_source_occurrence_observations(
        project,
        source_ref_prefix=_connector_prefix(connector),
        root=resolved_root,
    )
    result: dict[str, dict[str, Any]] = {}
    for row in payload.get("source_occurrences") or []:
        if not isinstance(row, dict):
            continue
        metadata = dict(row.get("source_metadata") or {})
        if _text(metadata.get("connector_instance_id"), 160) != connector:
            continue
        remote_id = _text(metadata.get("remote_resource_id"), 1000)
        if not remote_id:
            continue
        if remote_id in result:
            raise ConnectorSyncError(
                f"connector_remote_identity_ambiguous:{remote_id}"
            )
        result[remote_id] = {
            "source_occurrence_id": row.get("source_occurrence_id"),
            "source_ref": row.get("source_ref"),
            "content_hash": row.get("content_hash"),
            "source_metadata": metadata,
            "last_seen_at_utc": row.get("last_seen_at_utc"),
            "observation_count": row.get("observation_count"),
        }
    return result


def _normalize_unchanged_observations(
    connector: str,
    observations: list[dict[str, Any]] | None,
    *,
    changed_refs: set[str],
) -> list[dict[str, Any]]:
    if observations is None:
        return []
    if not isinstance(observations, list):
        raise ConnectorSyncError(
            "connector_sync_unchanged_observations_must_be_list"
        )
    normalized: list[dict[str, Any]] = []
    refs: set[str] = set()
    for index, raw in enumerate(observations):
        if not isinstance(raw, dict):
            raise ConnectorSyncError(
                f"connector_sync_unchanged_observation_invalid:{index}"
            )
        remote_id = _text(raw.get("remote_resource_id"), 1000)
        kind = _text(raw.get("resource_kind"), 80) or "document"
        if not remote_id:
            raise ConnectorSyncError(
                f"connector_sync_remote_resource_id_required:{index}"
            )
        source_ref = build_connector_source_ref(
            connector, remote_id, resource_kind=kind
        )
        if source_ref in refs or source_ref in changed_refs:
            raise ConnectorSyncError(
                f"connector_sync_duplicate_remote_identity:{source_ref}"
            )
        metadata = raw.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ConnectorSyncError(
                f"connector_sync_unchanged_metadata_invalid:{index}"
            )
        refs.add(source_ref)
        normalized.append(
            {
                "remote_resource_id": remote_id,
                "resource_kind": kind,
                "source_ref": source_ref,
                "metadata": dict(metadata or {}),
            }
        )
    return normalized


def _normalize_coverage_observations(
    connector: str,
    observations: list[dict[str, Any]] | None,
    *,
    blocked_refs: set[str],
) -> list[dict[str, Any]]:
    if observations is None:
        return []
    if not isinstance(observations, list):
        raise ConnectorSyncError(
            "connector_sync_coverage_observations_must_be_list"
        )
    normalized: list[dict[str, Any]] = []
    refs: set[str] = set()
    for index, raw in enumerate(observations):
        if not isinstance(raw, dict):
            raise ConnectorSyncError(
                f"connector_sync_coverage_observation_invalid:{index}"
            )
        remote_id = _text(raw.get("remote_resource_id"), 1000)
        kind = _text(raw.get("resource_kind"), 80) or "document"
        state = _text(raw.get("state"), 40).upper()
        reason_code = _text(raw.get("reason_code"), 160)
        if not remote_id:
            raise ConnectorSyncError(
                f"connector_sync_coverage_remote_resource_id_required:{index}"
            )
        if state not in _COVERAGE_STATES:
            raise ConnectorSyncError(
                f"connector_sync_coverage_state_invalid:{index}"
            )
        if not reason_code or not _IDENTIFIER_RE.fullmatch(reason_code):
            raise ConnectorSyncError(
                f"connector_sync_coverage_reason_code_invalid:{index}"
            )
        source_ref = build_connector_source_ref(
            connector, remote_id, resource_kind=kind
        )
        if source_ref in refs or source_ref in blocked_refs:
            raise ConnectorSyncError(
                f"connector_sync_duplicate_remote_identity:{source_ref}"
            )
        metadata = _sanitize_metadata(dict(raw.get("metadata") or {}))
        refs.add(source_ref)
        normalized.append(
            {
                "remote_resource_id": remote_id,
                "resource_kind": kind,
                "source_ref": source_ref,
                "state": state,
                "reason_code": reason_code,
                "remote_object_type": _text(raw.get("remote_object_type"), 80),
                "display_title": _redact_text(raw.get("display_title"), 300),
                "retry_trigger": _text(raw.get("retry_trigger"), 160),
                "capability_contract_version": _text(
                    raw.get("capability_contract_version"), 160
                ),
                "metadata": metadata,
                "content_materialized": False,
                "source_occurrence_created": False,
                "customer_source_modified": False,
            }
        )
    return normalized


def _normalize_items(connector: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        raise ConnectorSyncError("connector_sync_items_must_be_list")
    normalized: list[dict[str, Any]] = []
    refs: set[str] = set()
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise ConnectorSyncError(f"connector_sync_item_invalid:{index}")
        row = dict(raw)
        remote_id = _text(row.get("remote_resource_id"), 1000)
        source_type = _text(row.get("source_type"), 80)
        kind = _text(row.get("resource_kind"), 80) or "document"
        if not remote_id:
            raise ConnectorSyncError(f"connector_sync_remote_resource_id_required:{index}")
        if not source_type:
            raise ConnectorSyncError(f"connector_sync_source_type_required:{index}")
        source_ref = build_connector_source_ref(connector, remote_id, resource_kind=kind)
        if source_ref in refs:
            raise ConnectorSyncError(f"connector_sync_duplicate_remote_identity:{source_ref}")
        refs.add(source_ref)
        raw_metadata = row.get("metadata")
        if raw_metadata not in (None, "") and not isinstance(raw_metadata, dict):
            raise ConnectorSyncError(
                f"connector_sync_item_metadata_must_be_object:{index}"
            )
        metadata = _sanitize_metadata(dict(raw_metadata or {}) if raw_metadata else None)
        row.update(
            {
                "_remote_resource_id": remote_id,
                "_source_type": source_type,
                "_resource_kind": kind,
                "_source_ref": source_ref,
                "_metadata": metadata,
            }
        )
        normalized.append(row)
    return normalized


def _run_summary(registry: dict[str, Any], run: dict[str, Any], receipt_path: str) -> None:
    epoch = _text(run.get("sync_epoch_id"), 160)
    summary = next(
        (row for row in registry.get("sync_runs") or [] if row.get("sync_epoch_id") == epoch),
        None,
    )
    if summary is None:
        summary = {}
        registry.setdefault("sync_runs", []).append(summary)
    summary.update(
        {
            "sync_epoch_id": epoch,
            "connector_instance_id": run.get("connector_instance_id"),
            "sync_mode": run.get("sync_mode"),
            "status": run.get("status"),
            "started_at_utc": run.get("started_at_utc"),
            "completed_at_utc": run.get("completed_at_utc", ""),
            "item_count": run.get("item_count", 0),
            "success_count": run.get("success_count", 0),
            "materialized_success_count": run.get("materialized_success_count", 0),
            "unchanged_success_count": run.get("unchanged_success_count", 0),
            "coverage_observation_count": run.get("coverage_observation_count", 0),
            "knowledge_coverage_status": run.get("knowledge_coverage_status", ""),
            "failure_count": run.get("failure_count", 0),
            "retired_count": run.get("retired_count", 0),
            "cursor_checkpoint_committed": bool(run.get("cursor_checkpoint_committed")),
            "run_receipt_path": receipt_path,
        }
    )


def _start_run(
    project: str,
    connector: str,
    instance: dict[str, Any],
    registry: dict[str, Any],
    run: dict[str, Any],
    root: Path,
) -> None:
    if instance.get("active_sync_epoch_id"):
        raise ConnectorSyncError("connector_sync_already_running")
    epoch = run["sync_epoch_id"]
    if any(row.get("sync_epoch_id") == epoch for row in registry.get("sync_runs") or []):
        raise ConnectorSyncError("connector_sync_epoch_already_exists")
    receipt_path = _write_run_receipt(project, connector, epoch, root, run)
    _run_summary(registry, run, receipt_path)
    instance["active_sync_epoch_id"] = epoch
    instance["last_sync_started_at_utc"] = run["started_at_utc"]
    _save_connector_registry(project, root, registry)


def _finish_run(
    project: str,
    connector: str,
    run: dict[str, Any],
    root: Path,
    actor: dict[str, str],
    next_cursor_hash: str,
) -> str:
    registry = _load_connector_registry(project, root)
    instance = _instance_by_id(registry, connector)
    if instance is None:
        raise ConnectorSyncError("connector_instance_disappeared_during_sync")
    complete = run.get("status") == "COMPLETE"
    committed = complete and bool(next_cursor_hash)
    completed_at = _text(run.get("completed_at_utc"), 80) or _now()
    run["cursor_checkpoint_committed"] = committed
    run["committed_cursor_fingerprint"] = next_cursor_hash if committed else ""
    run["previous_cursor_checkpoint_preserved"] = not committed
    instance["active_sync_epoch_id"] = ""
    instance["last_sync_completed_at_utc"] = completed_at
    if complete:
        instance["last_successful_sync_epoch_id"] = run["sync_epoch_id"]
        instance["last_successful_sync_at_utc"] = completed_at
        if committed:
            instance["last_committed_cursor_fingerprint"] = next_cursor_hash
    else:
        instance["last_failed_sync_epoch_id"] = run["sync_epoch_id"]
        instance["last_failed_sync_at_utc"] = completed_at
    path = _write_run_receipt(project, connector, run["sync_epoch_id"], root, run)
    _run_summary(registry, run, path)
    registry["audit_events"].append(
        {
            "event": "complete_connector_sync_run",
            "at_utc": completed_at,
            "actor": actor,
            "connector_instance_id": connector,
            "sync_epoch_id": run["sync_epoch_id"],
            "status": run.get("status"),
            "success_count": run.get("success_count", 0),
            "coverage_observation_count": run.get("coverage_observation_count", 0),
            "failure_count": run.get("failure_count", 0),
            "retired_count": run.get("retired_count", 0),
            "cursor_checkpoint_committed": committed,
            "raw_cursor_values_persisted": False,
        }
    )
    _save_connector_registry(project, root, registry)
    return path


def _retirement_plan(
    before_refs: set[str],
    seen_refs: set[str],
    max_retire_count: int,
    max_retire_ratio: float,
) -> dict[str, Any]:
    missing = sorted(before_refs - seen_refs)
    ratio = len(missing) / max(len(before_refs), 1)
    allowed = len(missing) <= max_retire_count and ratio <= max_retire_ratio
    return {
        "status": "ALLOWED" if allowed else "BLOCKED",
        "previous_active_count": len(before_refs),
        "seen_count": len(seen_refs),
        "missing_count": len(missing),
        "missing_source_refs": missing,
        "retire_ratio": ratio,
        "max_retire_count": max_retire_count,
        "max_retire_ratio": max_retire_ratio,
        "guard_reason": "" if allowed else "connector_missing_source_retirement_threshold_exceeded",
    }


def _retire_missing(
    project: str,
    plan: dict[str, Any],
    root: Path,
    actor: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    retired: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for source_ref in plan.get("missing_source_refs") or []:
        try:
            receipt = delete_enterprise_knowledge_source(
                project, source_ref, root=root, actor=actor, purge_bytes=False
            )
            retired.append(
                {
                    "source_ref": source_ref,
                    "source_occurrence_id": receipt.get("source_occurrence_id"),
                    "canonical_source_id": receipt.get("canonical_source_id"),
                    "historical_source_bytes_retained": True,
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "source_ref": source_ref,
                    "code": "CONNECTOR_MISSING_SOURCE_RETIREMENT_FAILED",
                    "detail": type(exc).__name__,
                }
            )
    return retired, errors


def sync_connector_snapshot_batch(
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
    """Reconcile one already-fetched remote batch into source occurrences."""
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    mode = _text(sync_mode, 32).upper()
    policy = _text(deletion_policy, 32).upper()
    if mode not in _SYNC_MODES:
        raise ConnectorSyncError("connector_sync_mode_invalid")
    if policy not in _DELETION_POLICIES:
        raise ConnectorSyncError("connector_deletion_policy_invalid")
    if policy == "RETIRE_MISSING" and (mode != "FULL" or snapshot_complete is not True):
        raise ConnectorSyncError("connector_missing_retirement_requires_complete_full_snapshot")
    if max_retire_count < 0 or not 0.0 <= float(max_retire_ratio) <= 1.0:
        raise ConnectorSyncError("connector_retirement_threshold_invalid")

    normalized = _normalize_items(connector, items)
    changed_refs = {row["_source_ref"] for row in normalized}
    unchanged = _normalize_unchanged_observations(
        connector,
        unchanged_observations,
        changed_refs=changed_refs,
    )
    unchanged_refs = {row["source_ref"] for row in unchanged}
    coverage = _normalize_coverage_observations(
        connector,
        coverage_observations,
        blocked_refs=changed_refs | unchanged_refs,
    )
    epoch = _identifier(sync_epoch_id, "sync_epoch_id") if sync_epoch_id else _new_epoch(
        project, connector
    )
    with _sync_lock(project, connector, epoch, resolved_root):
        registry = _load_connector_registry(project, resolved_root)
        instance = _instance_by_id(registry, connector)
        if instance is None:
            raise ConnectorSyncError("connector_instance_not_registered")
        if instance.get("status") != "ACTIVE":
            raise ConnectorSyncError("connector_instance_not_active")
        previous_hash = _cursor_hash(previous_cursor)
        stored_hash = _text(instance.get("last_committed_cursor_fingerprint"), 128)
        if stored_hash and previous_hash != stored_hash:
            raise ConnectorSyncError("connector_sync_cursor_mismatch")
        next_hash = _cursor_hash(next_cursor)
        before_refs = _active_refs(project, connector, resolved_root)
        run = {
            "schema": CONNECTOR_SYNC_RUN_SCHEMA,
            "sync_epoch_id": epoch,
            "project_id": project,
            "connector_instance_id": connector,
            "connector_type": instance.get("connector_type"),
            "sync_mode": mode,
            "deletion_policy": policy,
            "status": "RUNNING",
            "started_at_utc": _now(),
            "started_by": clean_actor,
            "item_count": len(normalized) + len(unchanged) + len(coverage),
            "materialized_item_count": len(normalized),
            "unchanged_item_count": len(unchanged),
            "coverage_observation_count": len(coverage),
            "coverage_observations": coverage,
            "previous_cursor_fingerprint": previous_hash,
            "next_cursor_fingerprint": next_hash,
            "cursor_checkpoint_committed": False,
            "raw_cursor_values_persisted": False,
            "source_content_persisted_in_run_receipt": False,
            "coverage_observations_create_source_occurrences": False,
            "customer_material_mutation_executed": False,
        }
        _start_run(project, connector, instance, registry, run, resolved_root)

        successes: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        seen_refs: set[str] = set()
        unchanged_recorded_count = 0
        coverage_existing_recorded_count = 0
        retired: list[dict[str, Any]] = []
        reconciliation: dict[str, Any] = {
            "status": "NOT_REQUESTED",
            "missing_count": 0,
            "retired_count": 0,
            "errors": [],
        }
        try:
            if normalized:
                try:
                    batch = ingest_connector_snapshots_batch(
                        project,
                        [
                            {
                                "source_id": _text(
                                    row.get("source_id")
                                    or row["_remote_resource_id"],
                                    160,
                                ),
                                "source_type": row["_source_type"],
                                "content": row.get("content"),
                                "remote_resource_id": row["_remote_resource_id"],
                                "resource_kind": row["_resource_kind"],
                                "remote_revision": _text(
                                    row.get("remote_revision"), 240
                                ),
                                "remote_updated_at": _text(
                                    row.get("remote_updated_at"), 80
                                ),
                                "retrieved_at": _text(
                                    row.get("retrieved_at"), 80
                                ),
                                "canonical_url": _text(
                                    row.get("canonical_url"), 1000
                                ),
                                "parent_remote_id": _text(
                                    row.get("parent_remote_id"), 1000
                                ),
                                "export_format": _text(
                                    row.get("export_format"), 80
                                ),
                                "declared_mime": _text(
                                    row.get("declared_mime"), 160
                                ),
                                "display_title": _text(
                                    row.get("display_title"), 300
                                ),
                                "etag": _text(row.get("etag"), 1000),
                                "last_modified": _text(
                                    row.get("last_modified"), 1000
                                ),
                                "source_relationships_json": _text(
                                    row.get("source_relationships_json"), 100000
                                ),
                                "aliases_json": _text(
                                    row.get("aliases_json"), 100000
                                ),
                                "forms_present": (
                                    bool(row.get("forms_present"))
                                    if "forms_present" in row
                                    else None
                                ),
                                "robots_status": _text(
                                    row.get("robots_status"), 80
                                ),
                                "sitemap_last_modified": _text(
                                    row.get("sitemap_last_modified"), 160
                                ),
                                "remote_materialization_fingerprint": _text(
                                    row.get(
                                        "remote_materialization_fingerprint"
                                    ),
                                    128,
                                ),
                                "metadata": dict(row.get("_metadata") or {}),
                                "filename": _text(row.get("filename"), 500),
                            }
                            for row in normalized
                        ],
                        root=resolved_root,
                        connector_id=connector,
                        sync_epoch_id=epoch,
                        sync_cursor=next_cursor,
                        actor=clean_actor,
                    )
                    by_ref = {
                        _text(item.get("source_ref"), 2000): dict(item)
                        for item in batch.get("items") or []
                        if isinstance(item, dict)
                        and _text(item.get("source_ref"), 2000)
                    }
                    for row in normalized:
                        receipt = by_ref.get(row["_source_ref"])
                        if receipt is None:
                            raise ConnectorSnapshotError(
                                "connector_snapshot_batch_result_missing:"
                                + row["_source_ref"]
                            )
                        occurrence = dict(
                            receipt.get("source_occurrence") or {}
                        )
                        success = {
                            "remote_resource_id": row["_remote_resource_id"],
                            "resource_kind": row["_resource_kind"],
                            "source_ref": receipt.get("source_ref")
                            or row["_source_ref"],
                            "source_occurrence_id": receipt.get(
                                "source_occurrence_id"
                            ),
                            "canonical_source_id": receipt.get(
                                "canonical_source_id"
                            ),
                            "content_hash": receipt.get("content_hash"),
                            "occurrence_version": occurrence.get("version"),
                            "observation_count": occurrence.get(
                                "observation_count"
                            ),
                            "remote_revision": _text(
                                row.get("remote_revision"), 240
                            ),
                            "status": "INGESTED",
                        }
                        successes.append(success)
                        seen_refs.add(
                            _text(success["source_ref"], 2000)
                        )
                except Exception as exc:
                    errors.append(
                        {
                            "code": (
                                "CONNECTOR_SNAPSHOT_INGESTION_FAILED"
                                if isinstance(exc, ConnectorSnapshotError)
                                else "CONNECTOR_SYNC_ITEM_UNEXPECTED_FAILURE"
                            ),
                            "detail": (
                                str(exc)[:500]
                                if isinstance(exc, ConnectorSnapshotError)
                                else type(exc).__name__
                            ),
                            "affected_item_count": len(normalized),
                            "previous_snapshot_retained": True,
                        }
                    )

            if unchanged:
                try:
                    observation_receipt = record_source_occurrence_observations_batch(
                        project,
                        [
                            {
                                "source_ref": row["source_ref"],
                                "metadata": row["metadata"],
                            }
                            for row in unchanged
                        ],
                        root=resolved_root,
                        actor=clean_actor,
                    )
                    unchanged_recorded_count = int(
                        observation_receipt.get("recorded_count") or 0
                    )
                    if unchanged_recorded_count != len(unchanged):
                        raise ConnectorSyncError(
                            "connector_unchanged_observation_count_mismatch"
                        )
                    seen_refs.update(row["source_ref"] for row in unchanged)
                except Exception as exc:
                    errors.append(
                        {
                            "code": "CONNECTOR_UNCHANGED_OBSERVATION_FAILED",
                            "detail": (
                                str(exc)[:500]
                                if isinstance(exc, ConnectorSyncError)
                                else type(exc).__name__
                            ),
                            "previous_snapshot_retained": True,
                        }
                    )

            if coverage:
                seen_refs.update(row["source_ref"] for row in coverage)
                existing_coverage = [
                    row for row in coverage if row["source_ref"] in before_refs
                ]
                if existing_coverage:
                    try:
                        coverage_receipt = record_source_occurrence_observations_batch(
                            project,
                            [
                                {
                                    "source_ref": row["source_ref"],
                                    "metadata": row["metadata"],
                                }
                                for row in existing_coverage
                            ],
                            root=resolved_root,
                            actor=clean_actor,
                        )
                        coverage_existing_recorded_count = int(
                            coverage_receipt.get("recorded_count") or 0
                        )
                        if coverage_existing_recorded_count != len(existing_coverage):
                            raise ConnectorSyncError(
                                "connector_coverage_observation_count_mismatch"
                            )
                    except Exception as exc:
                        errors.append(
                            {
                                "code": "CONNECTOR_COVERAGE_OBSERVATION_FAILED",
                                "detail": (
                                    str(exc)[:500]
                                    if isinstance(exc, ConnectorSyncError)
                                    else type(exc).__name__
                                ),
                                "previous_snapshot_retained": True,
                            }
                        )

            if not errors and policy == "RETIRE_MISSING":
                reconciliation = _retirement_plan(
                    before_refs, seen_refs, max_retire_count, float(max_retire_ratio)
                )
                if reconciliation["status"] == "ALLOWED":
                    retired, retirement_errors = _retire_missing(
                        project, reconciliation, resolved_root, clean_actor
                    )
                    reconciliation.update(
                        {
                            "status": "PARTIAL" if retirement_errors else "COMPLETE",
                            "retired_count": len(retired),
                            "retired_source_occurrences": retired,
                            "errors": retirement_errors,
                        }
                    )
                    errors.extend(retirement_errors)
            elif errors and policy == "RETIRE_MISSING":
                reconciliation = {
                    "status": "SKIPPED_SYNC_INCOMPLETE",
                    "missing_count": 0,
                    "retired_count": 0,
                    "errors": [],
                    "previous_snapshots_retained": True,
                }

            status = (
                "PARTIAL"
                if errors and (successes or unchanged_recorded_count)
                else "FAILED"
                if errors
                else "BLOCKED"
                if reconciliation.get("status") == "BLOCKED"
                else "COMPLETE"
            )
            knowledge_coverage_status = (
                "INCOMPLETE"
                if status != "COMPLETE"
                else "PARTIAL_UNSUPPORTED"
                if coverage
                else "COMPLETE"
            )
            run.update(
                {
                    "status": status,
                    "completed_at_utc": _now(),
                    "success_count": len(successes) + unchanged_recorded_count,
                    "materialized_success_count": len(successes),
                    "unchanged_success_count": unchanged_recorded_count,
                    "coverage_observation_count": len(coverage),
                    "coverage_existing_occurrence_recorded_count": (
                        coverage_existing_recorded_count
                    ),
                    "knowledge_coverage_status": knowledge_coverage_status,
                    "knowledge_coverage_complete": status == "COMPLETE" and not coverage,
                    "failure_count": len(errors),
                    "successful_items": successes,
                    "errors": errors,
                    "seen_source_ref_count": len(seen_refs),
                    "seen_source_refs_digest": _short_hash(sorted(seen_refs), 32),
                    "previous_active_snapshot_count": len(before_refs),
                    "post_active_snapshot_count": len(
                        _active_refs(project, connector, resolved_root)
                    ),
                    "retired_count": len(retired),
                    "deletion_reconciliation": reconciliation,
                    "previous_snapshots_retained_on_item_failure": True,
                    "raw_cursor_values_persisted": False,
                    "source_content_persisted_in_run_receipt": False,
                    "coverage_observations_create_source_occurrences": False,
                    "customer_material_mutation_executed": False,
                }
            )
            receipt_path = _finish_run(
                project, connector, run, resolved_root, clean_actor, next_hash
            )
            return {**run, "run_receipt_path": receipt_path}
        except Exception as exc:
            run.update(
                {
                    "status": "FAILED",
                    "completed_at_utc": _now(),
                    "success_count": len(successes) + unchanged_recorded_count,
                    "materialized_success_count": len(successes),
                    "unchanged_success_count": unchanged_recorded_count,
                    "coverage_observation_count": len(coverage),
                    "coverage_existing_occurrence_recorded_count": (
                        coverage_existing_recorded_count
                    ),
                    "knowledge_coverage_status": "INCOMPLETE",
                    "knowledge_coverage_complete": False,
                    "failure_count": len(errors) + 1,
                    "successful_items": successes,
                    "errors": [
                        *errors,
                        {
                            "code": "CONNECTOR_SYNC_COORDINATOR_FAILED",
                            "detail": type(exc).__name__,
                            "previous_cursor_checkpoint_preserved": True,
                            "previous_snapshots_retained": True,
                        },
                    ],
                    "retired_count": len(retired),
                    "cursor_checkpoint_committed": False,
                    "previous_snapshots_retained_on_item_failure": True,
                    "raw_cursor_values_persisted": False,
                    "source_content_persisted_in_run_receipt": False,
                    "coverage_observations_create_source_occurrences": False,
                    "customer_material_mutation_executed": False,
                }
            )
            try:
                _finish_run(
                    project, connector, run, resolved_root, clean_actor, next_hash
                )
            except Exception:
                pass
            raise ConnectorSyncError(
                f"connector_sync_coordinator_failed:{type(exc).__name__}:{exc}"
            ) from exc


def abort_connector_sync_run(
    project_id: str,
    *,
    connector_instance_id: str,
    reason: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Clear a stranded RUNNING epoch without advancing its cursor."""
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    registry = _load_connector_registry(project, resolved_root)
    instance = _instance_by_id(registry, connector)
    if instance is None:
        raise ConnectorSyncError("connector_instance_not_registered")
    registry_epoch = _text(instance.get("active_sync_epoch_id"), 160)
    lock_epoch = _lock_epoch(project, connector, resolved_root)
    if registry_epoch and lock_epoch and registry_epoch != lock_epoch:
        raise ConnectorSyncError("connector_sync_lock_registry_mismatch")
    epoch = registry_epoch or lock_epoch
    if not epoch:
        raise ConnectorSyncError("connector_sync_run_not_active")
    try:
        run = load_connector_sync_run(
            project,
            connector_instance_id=connector,
            sync_epoch_id=epoch,
            root=resolved_root,
        )
    except KeyError:
        run = {}
    aborted_at = _now()
    run.update(
        {
            "schema": CONNECTOR_SYNC_RUN_SCHEMA,
            "project_id": project,
            "connector_instance_id": connector,
            "sync_epoch_id": epoch,
            "status": "ABORTED",
            "completed_at_utc": aborted_at,
            "abort_reason": _redact_text(reason, 1000),
            "aborted_by": clean_actor,
            "cursor_checkpoint_committed": False,
            "previous_cursor_checkpoint_preserved": True,
            "previous_snapshots_retained": True,
            "raw_cursor_values_persisted": False,
            "source_content_persisted_in_run_receipt": False,
        }
    )
    path = _write_run_receipt(project, connector, epoch, resolved_root, run)
    instance["active_sync_epoch_id"] = ""
    instance["last_failed_sync_epoch_id"] = epoch
    instance["last_failed_sync_at_utc"] = aborted_at
    _run_summary(registry, run, path)
    registry["audit_events"].append(
        {
            "event": "abort_connector_sync_run",
            "at_utc": aborted_at,
            "actor": clean_actor,
            "connector_instance_id": connector,
            "sync_epoch_id": epoch,
            "reason": _redact_text(reason, 1000),
            "cursor_checkpoint_committed": False,
        }
    )
    _save_connector_registry(project, resolved_root, registry)
    _remove_sync_lock(project, connector, epoch, resolved_root)
    return {**run, "run_receipt_path": path}


__all__ = [
    "connector_snapshot_observation_index",
    "CONNECTOR_INSTANCE_SCHEMA",
    "CONNECTOR_SYNC_REGISTRY_SCHEMA",
    "CONNECTOR_SYNC_RUN_SCHEMA",
    "ConnectorSyncError",
    "abort_connector_sync_run",
    "list_connector_instances",
    "list_connector_sync_runs",
    "load_connector_sync_run",
    "register_connector_instance",
    "sync_connector_snapshot_batch",
]
