"""Monotonic fencing-token authority for managed connector synchronization.

The token lives on the existing connector instance registry record. Issuance is serialized by
the existing project knowledge transaction. Normal synchronization cannot displace a progressing
owner. Explicit configuration replacement may request a forced takeover: the previous writer is
revoked first, then existing recovery safely aborts its RUNNING epoch before configuration writes.
A still-current lease also performs bounded temporary-residue maintenance before it completes.
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
from .connector_workspace_maintenance import maintain_connector_workspace
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
    force_takeover: bool = False,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    clean_actor = _require_manage_actor(actor)
    takeover_attempt = "fence_" + uuid.uuid4().hex
    force = bool(force_takeover)
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
            if ownership.get("owner_alive") is True and progressing and not force:
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
                and (ownership.get("progress_stale") is True or force)
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
                    "explicit_reconfiguration_uses_fenced_takeover": True,
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
                    "forced_takeover": force,
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
        "forced_takeover": force,
        "takeover_attempt_id": takeover_attempt,
        "previous_owner_state": _text(ownership.get("state"), 80),
        "previous_owner_progress_stale": bool(ownership.get("progress_stale")),
        "issued_at_unix": time.time(),
        "token_issuance_serialized": True,
        "second_registry_created": False,
    }


def _complete_connector_sync_fence(
    project_id: str,
    connector_instance_id: str,
    fencing_token: int,
    *,
    root: Path,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Complete only the still-current lease without overwriting a newer generation."""
    resolved_root = root.resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    token = int(fencing_token)
    clean_actor = _require_manage_actor(actor)
    try:
        with knowledge_transaction(
            resolved_root,
            project,
            operation="complete_connector_sync_fence",
            actor=clean_actor,
            wait_seconds=5.0,
        ):
            registry = _load_connector_registry(project, resolved_root)
            instance = _instance_by_id(registry, connector)
            if instance is None:
                return {"ok": True, "completed": False, "reason": "INSTANCE_MISSING"}
            current = _token(instance.get("fencing_generation"))
            if current != token:
                return {
                    "ok": True,
                    "completed": False,
                    "reason": "SUPERSEDED",
                    "current_fencing_token": current,
                }
            instance["fencing_takeover_pending"] = False
            instance["last_fencing_lease_completed_at_utc"] = _now()
            registry.setdefault("audit_events", []).append(
                {
                    "event": "complete_connector_sync_fencing_lease",
                    "at_utc": _now(),
                    "actor": clean_actor,
                    "connector_instance_id": connector,
                    "fencing_token": token,
                    "newer_fencing_token_overwritten": False,
                }
            )
            _save_connector_registry(project, resolved_root, registry)
    except KnowledgeTransactionBusy as exc:
        raise ConnectorSyncFenceError(
            "connector_sync_fence_transaction_busy"
        ) from exc
    return {"ok": True, "completed": True, "fencing_token": token}


@contextmanager
def managed_connector_sync_fence(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    force_takeover: bool = False,
) -> Iterator[dict[str, Any]]:
    resolved_root = (root or ROOT).resolve()
    lease = acquire_connector_sync_fence(
        project_id,
        connector_instance_id,
        root=resolved_root,
        actor=actor,
        force_takeover=force_takeover,
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
        try:
            yield dict(lease)
        finally:
            try:
                maintain_connector_workspace(
                    project,
                    root=resolved_root,
                    actor=actor,
                    trigger_connector_instance_id=connector,
                )
            except Exception:
                # Maintenance is bounded diagnostic cleanup. A revoked token, busy
                # transaction, or disk error must never mask the business operation.
                pass
            try:
                _complete_connector_sync_fence(
                    project,
                    connector,
                    token,
                    root=resolved_root,
                    actor=actor,
                )
            except Exception:
                # Lease completion is diagnostic cleanup. A newer token or shutdown must
                # never mask the real synchronization/configuration outcome.
                pass


__all__ = [
    "ConnectorSyncFenceError",
    "_complete_connector_sync_fence",
    "acquire_connector_sync_fence",
    "assert_connector_sync_fence",
    "managed_connector_sync_fence",
]
