"""Monotonic fencing-token authority for managed connector synchronization.

The token lives on the existing connector instance registry record. Issuance is serialized by
the existing project knowledge transaction. A healthy progressing owner cannot be displaced; an
owner whose call stack has stopped making progress beyond the takeover threshold is assigned a
newer token and marked FENCED_OUT before recovery continues.
"""
from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .connector_sync_authority import (
    ConnectorSyncError,
    _instance_by_id,
    _load_connector_registry,
    _save_connector_registry,
)
from .connector_sync_ownership import (
    ConnectorSyncOwnershipError,
    fence_out_connector_sync_ownership,
    inspect_connector_sync_ownership,
)
from .connector_write_fence import (
    ConnectorWriteFenceRevoked,
    connector_write_fence,
)
from .enterprise_knowledge_center._common import ROOT
from .enterprise_knowledge_center._utils import _now, _require_manage_actor
from .enterprise_knowledge_center.transaction_lock import (
    KnowledgeTransactionBusy,
    knowledge_transaction,
)
from .real_project_onboarding import _safe_project_id


class ConnectorSyncFenceError(ConnectorSyncError):
    """A connector fencing token could not be issued or validated safely."""


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _takeover_seconds() -> int:
    raw = _text(os.environ.get("QUALIBUG_CONNECTOR_SYNC_TAKEOVER_SECONDS"), 32)
    try:
        value = int(raw) if raw else 30 * 60
    except ValueError:
        value = 30 * 60
    return max(2 * 60, min(value, 24 * 60 * 60))


def _token(value: Any) -> int:
    try:
        result = int(value or 0)
    except (TypeError, ValueError):
        result = 0
    return max(0, result)


def assert_connector_sync_fence(
    project_id: str,
    connector_instance_id: str,
    fencing_token: int,
    *,
    root: Path | None = None,
) -> None:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    expected = int(fencing_token)
    registry = _load_connector_registry(project, resolved_root)
    instance = _instance_by_id(registry, connector)
    if instance is None:
        raise ConnectorWriteFenceRevoked("connector_sync_fence_instance_missing")
    current = _token(instance.get("fencing_generation"))
    if expected <= 0 or current != expected:
        raise ConnectorWriteFenceRevoked(
            f"connector_sync_fence_revoked:expected_{expected}:current_{current}"
        )


def acquire_connector_sync_fence(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    clean_actor = _require_manage_actor(actor)
    takeover_attempt = "fence_" + uuid.uuid4().hex
    try:
        with knowledge_transaction(
            resolved_root,
            project,
            operation="acquire_connector_sync_fence",
            actor=clean_actor,
            wait_seconds=5.0,
        ):
            ownership = inspect_connector_sync_ownership(
                project,
                connector,
                root=resolved_root,
                stale_after_seconds=_takeover_seconds(),
            )
            progressing = ownership.get("progress_stale") is not True
            if ownership.get("owner_alive") is True and progressing:
                raise ConnectorSyncFenceError(
                    "connector_sync_already_running_owner_active"
                )
            if ownership.get("owner_alive") is None and ownership.get("state") not in {
                "MISSING",
                "",
            }:
                raise ConnectorSyncFenceError(
                    "connector_sync_lock_held_owner_unverified:"
                    + (_text(ownership.get("state"), 80) or "UNKNOWN")
                )

            registry = _load_connector_registry(project, resolved_root)
            instance = _instance_by_id(registry, connector)
            if instance is None:
                raise ConnectorSyncFenceError("connector_instance_not_registered")
            current = _token(instance.get("fencing_generation"))
            token = current + 1
            taken_over = bool(
                ownership.get("owner_alive") is True
                and ownership.get("progress_stale") is True
            )
            instance["fencing_generation"] = token
            instance["last_fencing_token_issued_at_utc"] = _now()
            instance["last_fencing_token_issued_by"] = clean_actor
            instance["fencing_takeover_pending"] = taken_over
            registry.setdefault("governance", {}).update(
                {
                    "connector_sync_fencing_enabled": True,
                    "connector_sync_fencing_token_authority": "CONNECTOR_INSTANCE_REGISTRY",
                    "stale_connector_threads_cannot_commit_writes": True,
                    "fencing_token_issuance_project_transaction_serialized": True,
                    "second_fencing_registry_created": False,
                }
            )
            registry.setdefault("audit_events", []).append(
                {
                    "event": "issue_connector_sync_fencing_token",
                    "at_utc": _now(),
                    "actor": clean_actor,
                    "connector_instance_id": connector,
                    "previous_fencing_token": current,
                    "fencing_token": token,
                    "takeover": taken_over,
                    "previous_owner_state": _text(ownership.get("state"), 80),
                    "previous_owner_progress_stale": bool(
                        ownership.get("progress_stale")
                    ),
                    "raw_credentials_persisted": False,
                    "source_content_persisted": False,
                }
            )
            _save_connector_registry(project, resolved_root, registry)

            if ownership.get("state") not in {"MISSING", ""}:
                try:
                    fence_out_connector_sync_ownership(
                        project,
                        connector,
                        root=resolved_root,
                        takeover_attempt_id=takeover_attempt,
                        fencing_token=token,
                    )
                except ConnectorSyncOwnershipError as exc:
                    raise ConnectorSyncFenceError(
                        f"connector_sync_fence_owner_invalidation_failed:{exc}"
                    ) from exc
    except KnowledgeTransactionBusy as exc:
        raise ConnectorSyncFenceError(
            "connector_sync_fence_transaction_busy"
        ) from exc

    return {
        "ok": True,
        "project_id": project,
        "connector_instance_id": connector,
        "fencing_token": token,
        "previous_fencing_token": current,
        "takeover": taken_over,
        "takeover_attempt_id": takeover_attempt,
        "previous_owner_state": _text(ownership.get("state"), 80),
        "previous_owner_progress_stale": bool(ownership.get("progress_stale")),
        "issued_at_unix": time.time(),
        "token_issuance_serialized": True,
        "second_registry_created": False,
    }


@contextmanager
def managed_connector_sync_fence(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    resolved_root = (root or ROOT).resolve()
    lease = acquire_connector_sync_fence(
        project_id,
        connector_instance_id,
        root=resolved_root,
        actor=actor,
    )
    project = lease["project_id"]
    connector = lease["connector_instance_id"]
    token = int(lease["fencing_token"])

    def validate(value_project: str, value_connector: str, value_token: int) -> None:
        if value_project != project or value_connector != connector or value_token != token:
            raise ConnectorWriteFenceRevoked("connector_sync_fence_context_mismatch")
        assert_connector_sync_fence(
            project,
            connector,
            token,
            root=resolved_root,
        )

    with connector_write_fence(
        project,
        connector,
        token,
        validator=validate,
    ):
        yield dict(lease)


__all__ = [
    "ConnectorSyncFenceError",
    "acquire_connector_sync_fence",
    "assert_connector_sync_fence",
    "managed_connector_sync_fence",
]
