"""Durable, content-free recovery intent for connector remote lifecycle.

Remote enumeration and material ingestion precede lifecycle reconciliation.  A process crash in
that boundary must not force the supervisor to guess which resources were present.  This journal
stores only the bounded lifecycle descriptor set, policy and cursor fingerprint.  It never stores
source bytes, credentials, access tokens or the raw cursor, and it is removed after the lifecycle
and cursor checkpoint commit completes.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .connector_remote_lifecycle import _normalize_present_resources
from .enterprise_knowledge_center._common import ROOT, _safe_project_id
from .enterprise_knowledge_center._utils import (
    _load_json,
    _now,
    _require_manage_actor,
    _write_json,
)
from .enterprise_knowledge_center.transaction_lock import (
    KnowledgeTransactionBusy,
    knowledge_transaction,
)

CONNECTOR_LIFECYCLE_RECOVERY_INTENT_SCHEMA = (
    "qualibug.connector-lifecycle-recovery-intent.v1"
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_POLICIES = {"RETAIN", "RETIRE_MISSING"}
_STATES = {
    "STAGED",
    "SNAPSHOT_COMMITTING",
    "SNAPSHOT_COMMITTED_PENDING_LIFECYCLE",
    "LIFECYCLE_RECOVERY_RUNNING",
    "LIFECYCLE_COMMITTED_PENDING_CHECKPOINT",
    "RECOVERY_BLOCKED",
}
_MAX_RESOURCES = 10_000


class ConnectorLifecycleRecoveryIntentError(RuntimeError):
    """A lifecycle recovery intent could not be persisted or trusted."""


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _identifier(value: Any, field: str) -> str:
    result = _text(value, 160)
    if not _IDENTIFIER_RE.fullmatch(result):
        raise ConnectorLifecycleRecoveryIntentError(f"{field}_invalid")
    return result


def _workspace(project: str, root: Path) -> Path:
    return (
        root.resolve()
        / "platform_workspace"
        / project
        / "enterprise_knowledge_center"
    )


def lifecycle_recovery_intent_path(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
) -> Path:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    return (
        _workspace(project, resolved_root)
        / "connector_lifecycle_recovery_intents"
        / f"{connector}.json"
    )


def _digest(resources: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(
            resources,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != CONNECTOR_LIFECYCLE_RECOVERY_INTENT_SCHEMA:
        raise ConnectorLifecycleRecoveryIntentError(
            "connector_lifecycle_recovery_intent_schema_invalid"
        )
    _identifier(payload.get("connector_instance_id"), "connector_instance_id")
    _identifier(payload.get("sync_epoch_id"), "sync_epoch_id")
    resources = payload.get("present_resources")
    if not isinstance(resources, list) or len(resources) > _MAX_RESOURCES:
        raise ConnectorLifecycleRecoveryIntentError(
            "connector_lifecycle_recovery_intent_resources_invalid"
        )
    normalized = list(_normalize_present_resources(resources).values())
    normalized.sort(key=lambda row: row["remote_resource_id"])
    if _digest(normalized) != _text(payload.get("resource_digest"), 128):
        raise ConnectorLifecycleRecoveryIntentError(
            "connector_lifecycle_recovery_intent_digest_mismatch"
        )
    cursor_fingerprint = _text(payload.get("next_cursor_fingerprint"), 128)
    if cursor_fingerprint and not _SHA256_RE.fullmatch(cursor_fingerprint):
        raise ConnectorLifecycleRecoveryIntentError(
            "connector_lifecycle_recovery_intent_cursor_fingerprint_invalid"
        )
    if payload.get("source_content_persisted") is not False:
        raise ConnectorLifecycleRecoveryIntentError(
            "connector_lifecycle_recovery_intent_content_governance_invalid"
        )
    return {**payload, "present_resources": normalized}


def load_connector_lifecycle_recovery_intent(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    path = lifecycle_recovery_intent_path(
        project_id,
        connector_instance_id,
        root=root,
    )
    payload = _load_json(path, {})
    if not payload:
        return {}
    if not isinstance(payload, dict):
        raise ConnectorLifecycleRecoveryIntentError(
            "connector_lifecycle_recovery_intent_unreadable"
        )
    return _validate_payload(payload)


def stage_connector_lifecycle_recovery_intent(
    project_id: str,
    connector_instance_id: str,
    *,
    present_resources: list[dict[str, Any]],
    next_cursor_fingerprint: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    deletion_policy: str = "RETAIN",
    retire_after_complete_snapshots: int = 2,
    max_retire_count: int = 100,
    max_retire_ratio: float = 0.25,
    sync_epoch_id: str = "",
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    clean_actor = _require_manage_actor(actor)
    epoch = _identifier(
        sync_epoch_id or f"feishu_{uuid.uuid4().hex}",
        "sync_epoch_id",
    )
    policy = _text(deletion_policy, 40).upper() or "RETAIN"
    if policy not in _POLICIES:
        raise ConnectorLifecycleRecoveryIntentError(
            "connector_lifecycle_recovery_intent_policy_invalid"
        )
    grace = int(retire_after_complete_snapshots)
    if not 2 <= grace <= 100:
        raise ConnectorLifecycleRecoveryIntentError(
            "connector_lifecycle_recovery_intent_grace_invalid"
        )
    if max_retire_count < 0 or not 0.0 <= float(max_retire_ratio) <= 1.0:
        raise ConnectorLifecycleRecoveryIntentError(
            "connector_lifecycle_recovery_intent_threshold_invalid"
        )
    if len(present_resources) > _MAX_RESOURCES:
        raise ConnectorLifecycleRecoveryIntentError(
            "connector_lifecycle_recovery_intent_resource_limit_exceeded"
        )
    normalized = list(_normalize_present_resources(present_resources).values())
    normalized.sort(key=lambda row: row["remote_resource_id"])
    cursor_fingerprint = _text(next_cursor_fingerprint, 128)
    if not _SHA256_RE.fullmatch(cursor_fingerprint):
        raise ConnectorLifecycleRecoveryIntentError(
            "connector_lifecycle_recovery_intent_cursor_fingerprint_invalid"
        )
    payload = {
        "schema": CONNECTOR_LIFECYCLE_RECOVERY_INTENT_SCHEMA,
        "project_id": project,
        "connector_instance_id": connector,
        "sync_epoch_id": epoch,
        "state": "STAGED",
        "created_at_utc": _now(),
        "updated_at_utc": _now(),
        "created_by": clean_actor,
        "present_resource_count": len(normalized),
        "present_resources": normalized,
        "resource_digest": _digest(normalized),
        "next_cursor_fingerprint": cursor_fingerprint,
        "deletion_policy": policy,
        "authoritative_snapshot_complete": True,
        "retire_after_complete_snapshots": grace,
        "max_retire_count": int(max_retire_count),
        "max_retire_ratio": float(max_retire_ratio),
        "source_content_persisted": False,
        "raw_cursor_persisted": False,
        "credentials_persisted": False,
        "customer_material_mutation_executed": False,
    }
    path = lifecycle_recovery_intent_path(project, connector, root=resolved_root)
    try:
        with knowledge_transaction(
            resolved_root,
            project,
            operation="stage_connector_lifecycle_recovery_intent",
            actor=clean_actor,
            wait_seconds=5.0,
        ):
            existing = _load_json(path, {})
            if existing:
                trusted = _validate_payload(existing) if isinstance(existing, dict) else {}
                if (
                    trusted.get("sync_epoch_id") == epoch
                    and trusted.get("resource_digest") == payload["resource_digest"]
                    and trusted.get("next_cursor_fingerprint") == cursor_fingerprint
                ):
                    return trusted
                raise ConnectorLifecycleRecoveryIntentError(
                    "connector_lifecycle_recovery_intent_already_exists"
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_json(path, payload)
    except KnowledgeTransactionBusy as exc:
        raise ConnectorLifecycleRecoveryIntentError(
            "connector_lifecycle_recovery_intent_transaction_busy"
        ) from exc
    return payload


def update_connector_lifecycle_recovery_intent_state(
    project_id: str,
    connector_instance_id: str,
    *,
    state: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    expected_sync_epoch_id: str = "",
    reason_code: str = "",
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    clean_actor = _require_manage_actor(actor)
    normalized_state = _text(state, 80).upper()
    if normalized_state not in _STATES:
        raise ConnectorLifecycleRecoveryIntentError(
            "connector_lifecycle_recovery_intent_state_invalid"
        )
    expected = (
        _identifier(expected_sync_epoch_id, "sync_epoch_id")
        if expected_sync_epoch_id
        else ""
    )
    path = lifecycle_recovery_intent_path(project, connector, root=resolved_root)
    try:
        with knowledge_transaction(
            resolved_root,
            project,
            operation="update_connector_lifecycle_recovery_intent",
            actor=clean_actor,
            wait_seconds=5.0,
        ):
            payload = load_connector_lifecycle_recovery_intent(
                project,
                connector,
                root=resolved_root,
            )
            if not payload:
                raise ConnectorLifecycleRecoveryIntentError(
                    "connector_lifecycle_recovery_intent_missing"
                )
            if expected and payload.get("sync_epoch_id") != expected:
                raise ConnectorLifecycleRecoveryIntentError(
                    "connector_lifecycle_recovery_intent_epoch_mismatch"
                )
            payload["state"] = normalized_state
            payload["updated_at_utc"] = _now()
            payload["last_updated_by"] = clean_actor
            payload["reason_code"] = _text(reason_code, 160)
            _write_json(path, payload)
    except KnowledgeTransactionBusy as exc:
        raise ConnectorLifecycleRecoveryIntentError(
            "connector_lifecycle_recovery_intent_transaction_busy"
        ) from exc
    return payload


def clear_connector_lifecycle_recovery_intent(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    expected_sync_epoch_id: str = "",
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    clean_actor = _require_manage_actor(actor)
    expected = (
        _identifier(expected_sync_epoch_id, "sync_epoch_id")
        if expected_sync_epoch_id
        else ""
    )
    path = lifecycle_recovery_intent_path(project, connector, root=resolved_root)
    removed_epoch = ""
    try:
        with knowledge_transaction(
            resolved_root,
            project,
            operation="clear_connector_lifecycle_recovery_intent",
            actor=clean_actor,
            wait_seconds=5.0,
        ):
            payload = load_connector_lifecycle_recovery_intent(
                project,
                connector,
                root=resolved_root,
            )
            if payload:
                removed_epoch = _text(payload.get("sync_epoch_id"), 160)
                if expected and removed_epoch != expected:
                    raise ConnectorLifecycleRecoveryIntentError(
                        "connector_lifecycle_recovery_intent_epoch_mismatch"
                    )
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise ConnectorLifecycleRecoveryIntentError(
                    "connector_lifecycle_recovery_intent_delete_failed"
                ) from exc
    except KnowledgeTransactionBusy as exc:
        raise ConnectorLifecycleRecoveryIntentError(
            "connector_lifecycle_recovery_intent_transaction_busy"
        ) from exc
    return {
        "cleared": True,
        "sync_epoch_id": removed_epoch,
        "source_content_deleted": False,
        "customer_material_mutation_executed": False,
    }


def lifecycle_recovery_intent_age_seconds(payload: dict[str, Any]) -> float:
    value = _text(payload.get("created_at_utc"), 80)
    if not value:
        return 0.0
    try:
        started = time.mktime(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return 0.0
    return max(0.0, time.time() - started)


__all__ = [
    "CONNECTOR_LIFECYCLE_RECOVERY_INTENT_SCHEMA",
    "ConnectorLifecycleRecoveryIntentError",
    "clear_connector_lifecycle_recovery_intent",
    "lifecycle_recovery_intent_age_seconds",
    "lifecycle_recovery_intent_path",
    "load_connector_lifecycle_recovery_intent",
    "stage_connector_lifecycle_recovery_intent",
    "update_connector_lifecycle_recovery_intent_state",
]
