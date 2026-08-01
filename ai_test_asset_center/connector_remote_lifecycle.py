"""Remote lifecycle reconciliation for connector-owned source occurrences.

A complete connector enumeration proves only what is visible inside the configured scope. It does
not by itself prove that a customer deleted a remote document: the resource may have moved outside
the scope or become invisible after a permission change. This coordinator therefore records
bounded scope-presence evidence, preserves the last-known-good occurrence, and only performs an
internal retirement after consecutive complete snapshots and explicit RETIRE_MISSING policy.

The module creates no second source registry and never touches the customer system. Content,
interpretation, occurrence identity, runtime activation and historical bytes remain owned by the
existing enterprise knowledge authorities.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from .connector_sync_authority import (
    _load_connector_registry,
    _run_summary,
    _save_connector_registry,
    _write_run_receipt,
    load_connector_sync_run,
)
from .enterprise_knowledge_center import source_occurrence_core as _occurrence_core
from .enterprise_knowledge_center._common import ROOT, _safe_project_id
from .enterprise_knowledge_center._utils import (
    _load_registry,
    _now,
    _require_manage_actor,
    _save_registry,
)
from .enterprise_knowledge_center.source_occurrence_lifecycle import (
    delete_enterprise_knowledge_source,
)

CONNECTOR_REMOTE_LIFECYCLE_SCHEMA = "qualibug.connector-remote-lifecycle.v1"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_POLICIES = {"RETAIN", "RETIRE_MISSING"}


class ConnectorRemoteLifecycleError(RuntimeError):
    """Remote lifecycle evidence could not be reconciled safely."""


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _connector_id(value: Any) -> str:
    connector = _text(value, 160)
    if not _IDENTIFIER_RE.fullmatch(connector):
        raise ConnectorRemoteLifecycleError("connector_remote_lifecycle_id_invalid")
    return connector


def _connector_prefix(connector: str) -> str:
    return "connector://" + quote(connector, safe="._-") + "/"


def _connector_occurrences(
    registry: dict[str, Any], connector: str
) -> list[dict[str, Any]]:
    prefix = _connector_prefix(connector)
    rows: list[dict[str, Any]] = []
    for row in registry.get("source_occurrences") or []:
        if not isinstance(row, dict):
            continue
        metadata = dict(row.get("source_metadata") or {})
        if (
            _text(metadata.get("connector_instance_id"), 160) == connector
            or _text(row.get("source_ref"), 2000).startswith(prefix)
        ):
            rows.append(row)
    return rows


def _normalize_present_resources(
    resources: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not isinstance(resources, list):
        raise ConnectorRemoteLifecycleError(
            "connector_remote_lifecycle_resources_must_be_list"
        )
    normalized: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(resources):
        if not isinstance(raw, dict):
            raise ConnectorRemoteLifecycleError(
                f"connector_remote_lifecycle_resource_invalid:{index}"
            )
        remote_id = _text(raw.get("remote_resource_id"), 1000)
        if not remote_id:
            raise ConnectorRemoteLifecycleError(
                f"connector_remote_lifecycle_remote_id_required:{index}"
            )
        if remote_id in normalized:
            raise ConnectorRemoteLifecycleError(
                f"connector_remote_lifecycle_remote_id_duplicate:{remote_id}"
            )
        normalized[remote_id] = {
            "remote_resource_id": remote_id,
            "resource_kind": _text(raw.get("resource_kind"), 160),
            "display_title": _text(raw.get("display_title"), 300),
            "parent_remote_id": _text(raw.get("parent_remote_id"), 1000),
            "remote_space_id": _text(raw.get("remote_space_id"), 160),
            "remote_revision": _text(raw.get("remote_revision"), 240),
            "materialization_state": _text(
                raw.get("materialization_state"), 80
            ),
        }
    return normalized


def _latest_occurrence(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(
        rows,
        key=lambda row: (
            int(row.get("version") or 0),
            _text(row.get("created_at_utc"), 80),
        ),
    )


def _reactivate_retired_occurrence(
    project: str,
    root: Path,
    registry: dict[str, Any],
    occurrence: dict[str, Any],
    actor: dict[str, str],
) -> bool:
    if occurrence.get("status") != "retired_remote_scope":
        return False
    canonical_id = _text(occurrence.get("canonical_source_id"), 200)
    canonical = next(
        (
            row
            for row in registry.get("sources") or []
            if isinstance(row, dict)
            and _text(row.get("source_id"), 200) == canonical_id
        ),
        None,
    )
    if canonical is None:
        raise ConnectorRemoteLifecycleError(
            "connector_remote_lifecycle_canonical_missing"
        )
    _occurrence_core._reactivate_canonical_if_needed(
        project=project,
        root=root,
        actor=actor,
        canonical=canonical,
    )
    _occurrence_core._reactivate_existing_occurrence(
        registry,
        canonical=canonical,
        occurrence=occurrence,
        actor=actor,
    )
    return True


def _present_state(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    reappeared: bool,
) -> tuple[str, bool, bool]:
    old_title = _text(previous.get("remote_display_title"), 300)
    new_title = _text(current.get("display_title"), 300)
    old_parent = _text(previous.get("parent_remote_id"), 1000)
    new_parent = _text(current.get("parent_remote_id"), 1000)
    renamed = bool(old_title and new_title and old_title != new_title)
    moved = bool(old_parent and old_parent != new_parent)
    if reappeared:
        return "REAPPEARED", renamed, moved
    if renamed and moved:
        return "PRESENT_RENAMED_AND_MOVED_WITHIN_SCOPE", True, True
    if renamed:
        return "PRESENT_RENAMED", True, False
    if moved:
        return "PRESENT_MOVED_WITHIN_SCOPE", False, True
    return "PRESENT", False, False


def _attach_to_sync_receipt(
    project: str,
    connector: str,
    sync_epoch_id: str,
    root: Path,
    lifecycle: dict[str, Any],
) -> bool:
    if not sync_epoch_id:
        return False
    try:
        run = load_connector_sync_run(
            project,
            connector_instance_id=connector,
            sync_epoch_id=sync_epoch_id,
            root=root,
        )
        run["requested_deletion_policy"] = lifecycle[
            "requested_deletion_policy"
        ]
        run["effective_deletion_policy"] = lifecycle[
            "effective_deletion_policy"
        ]
        run["remote_lifecycle"] = lifecycle
        run["retired_count"] = int(lifecycle.get("retired_count") or 0)
        path = _write_run_receipt(
            project,
            connector,
            sync_epoch_id,
            root,
            run,
        )
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
        if summary is not None:
            summary.update(
                {
                    "remote_lifecycle_status": lifecycle.get("status"),
                    "remote_absent_count": lifecycle.get("absent_count", 0),
                    "remote_unconfirmed_missing_count": lifecycle.get(
                        "unconfirmed_missing_count", 0
                    ),
                    "remote_retirement_eligible_count": lifecycle.get(
                        "retirement_eligible_count", 0
                    ),
                    "renamed_resource_count": lifecycle.get(
                        "renamed_resource_count", 0
                    ),
                    "moved_resource_count": lifecycle.get(
                        "moved_resource_count", 0
                    ),
                    "reappeared_resource_count": lifecycle.get(
                        "reappeared_resource_count", 0
                    ),
                }
            )
        _save_connector_registry(project, root, registry)
        return True
    except Exception:
        return False


def reconcile_connector_remote_lifecycle(
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
) -> dict[str, Any]:
    """Record scope-presence evidence and optionally retire confirmed absences.

    Missing resources are labelled as absent from the configured scope, never as remotely deleted.
    A permission or traversal error must prevent this function from being called with an
    authoritative snapshot; therefore it cannot advance an absence counter.
    """
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _connector_id(connector_instance_id)
    clean_actor = _require_manage_actor(actor)
    policy = _text(deletion_policy, 40).upper() or "RETAIN"
    if policy not in _POLICIES:
        raise ConnectorRemoteLifecycleError(
            "connector_remote_lifecycle_policy_invalid"
        )
    grace = int(retire_after_complete_snapshots)
    if not 2 <= grace <= 100:
        raise ConnectorRemoteLifecycleError(
            "connector_remote_lifecycle_grace_out_of_range"
        )
    if max_retire_count < 0 or not 0.0 <= float(max_retire_ratio) <= 1.0:
        raise ConnectorRemoteLifecycleError(
            "connector_remote_lifecycle_threshold_invalid"
        )
    present = _normalize_present_resources(present_resources)
    observed_at = _now()
    registry = _load_registry(project, resolved_root)
    before_rows = _connector_occurrences(registry, connector)
    active_before = [row for row in before_rows if row.get("status") == "active"]
    by_remote: dict[str, list[dict[str, Any]]] = {}
    for row in before_rows:
        metadata = dict(row.get("source_metadata") or {})
        remote_id = _text(metadata.get("remote_resource_id"), 1000)
        if remote_id:
            by_remote.setdefault(remote_id, []).append(row)

    renamed_count = 0
    moved_count = 0
    reappeared_count = 0
    present_existing_count = 0
    for remote_id, resource in present.items():
        candidates = by_remote.get(remote_id, [])
        occurrence = next(
            (row for row in candidates if row.get("status") == "active"),
            None,
        )
        reappeared = False
        if occurrence is None:
            retired = _latest_occurrence(
                [
                    row
                    for row in candidates
                    if row.get("status") == "retired_remote_scope"
                ]
            )
            if retired is not None:
                reappeared = _reactivate_retired_occurrence(
                    project,
                    resolved_root,
                    registry,
                    retired,
                    clean_actor,
                )
                occurrence = retired
        if occurrence is None:
            continue
        present_existing_count += 1
        previous = dict(occurrence.get("source_metadata") or {})
        lifecycle_state, renamed, moved = _present_state(
            previous,
            resource,
            reappeared=reappeared
            or _text(previous.get("remote_lifecycle_state"), 100).startswith(
                "ABSENT_FROM_CONFIGURED_SCOPE"
            ),
        )
        renamed_count += int(renamed)
        moved_count += int(moved)
        reappeared_count += int(lifecycle_state == "REAPPEARED")
        metadata = dict(previous)
        metadata.update(
            {
                "connector_instance_id": connector,
                "remote_resource_id": remote_id,
                "resource_kind": resource["resource_kind"]
                or _text(metadata.get("resource_kind"), 160),
                "remote_display_title": resource["display_title"],
                "parent_remote_id": resource["parent_remote_id"],
                "remote_space_id": resource["remote_space_id"],
                "remote_revision": resource["remote_revision"],
                "remote_materialization_state": resource[
                    "materialization_state"
                ],
                "remote_lifecycle_state": lifecycle_state,
                "remote_scope_presence": "PRESENT",
                "remote_missing_complete_snapshot_count": 0,
                "remote_missing_first_observed_at_utc": "",
                "remote_missing_last_observed_at_utc": "",
                "remote_last_present_at_utc": observed_at,
                "remote_last_present_sync_epoch_id": _text(
                    sync_epoch_id, 160
                ),
                "remote_deletion_inferred": False,
                "permission_loss_inferred": False,
                "customer_source_modified": False,
            }
        )
        occurrence["source_metadata"] = metadata
        occurrence["last_seen_at_utc"] = observed_at

    absent_rows: list[dict[str, Any]] = []
    unconfirmed_rows: list[dict[str, Any]] = []
    eligible_rows: list[dict[str, Any]] = []
    if authoritative_snapshot_complete:
        present_ids = set(present)
        for occurrence in _connector_occurrences(registry, connector):
            if occurrence.get("status") != "active":
                continue
            metadata = dict(occurrence.get("source_metadata") or {})
            remote_id = _text(metadata.get("remote_resource_id"), 1000)
            if not remote_id or remote_id in present_ids:
                continue
            count = int(
                metadata.get("remote_missing_complete_snapshot_count") or 0
            ) + 1
            first_at = _text(
                metadata.get("remote_missing_first_observed_at_utc"), 80
            ) or observed_at
            confirmed = count >= grace
            metadata.update(
                {
                    "remote_lifecycle_state": (
                        "ABSENT_FROM_CONFIGURED_SCOPE_CONFIRMED"
                        if confirmed
                        else "ABSENT_FROM_CONFIGURED_SCOPE_UNCONFIRMED"
                    ),
                    "remote_scope_presence": "ABSENT",
                    "remote_missing_complete_snapshot_count": count,
                    "remote_missing_first_observed_at_utc": first_at,
                    "remote_missing_last_observed_at_utc": observed_at,
                    "remote_missing_last_sync_epoch_id": _text(
                        sync_epoch_id, 160
                    ),
                    "remote_absence_evidence": (
                        "CONSECUTIVE_COMPLETE_CONFIGURED_SCOPE_SNAPSHOTS"
                    ),
                    "remote_absence_is_remote_deletion_proof": False,
                    "remote_deletion_inferred": False,
                    "permission_loss_inferred": False,
                    "customer_source_modified": False,
                }
            )
            occurrence["source_metadata"] = metadata
            absent_rows.append(occurrence)
            (eligible_rows if confirmed else unconfirmed_rows).append(occurrence)

    registry.setdefault("governance", {}).update(
        {
            "remote_absence_does_not_prove_customer_deletion": True,
            "permission_errors_do_not_advance_absence_counters": True,
            "remote_scope_retirement_requires_consecutive_complete_snapshots": True,
            "remote_scope_retirement_is_internal_only": True,
            "customer_material_mutation_executed": False,
        }
    )
    registry.setdefault("audit_events", []).append(
        {
            "event": "reconcile_connector_remote_lifecycle_evidence",
            "at_utc": observed_at,
            "actor": clean_actor,
            "connector_instance_id": connector,
            "sync_epoch_id": _text(sync_epoch_id, 160),
            "authoritative_snapshot_complete": bool(
                authoritative_snapshot_complete
            ),
            "present_count": len(present),
            "absent_count": len(absent_rows),
            "unconfirmed_missing_count": len(unconfirmed_rows),
            "retirement_eligible_count": len(eligible_rows),
            "renamed_resource_count": renamed_count,
            "moved_resource_count": moved_count,
            "reappeared_resource_count": reappeared_count,
            "remote_deletion_inferred": False,
            "permission_loss_inferred": False,
            "customer_source_modified": False,
        }
    )
    _save_registry(project, resolved_root, registry)

    retire_ratio = len(eligible_rows) / max(len(active_before), 1)
    threshold_allowed = (
        len(eligible_rows) <= max_retire_count
        and retire_ratio <= float(max_retire_ratio)
    )
    retired: list[dict[str, Any]] = []
    retirement_errors: list[dict[str, Any]] = []
    if policy == "RETIRE_MISSING" and authoritative_snapshot_complete:
        if threshold_allowed:
            for occurrence in eligible_rows:
                source_ref = _text(occurrence.get("source_ref"), 2000)
                metadata = dict(occurrence.get("source_metadata") or {})
                try:
                    retirement = delete_enterprise_knowledge_source(
                        project,
                        source_ref,
                        root=resolved_root,
                        actor=clean_actor,
                        purge_bytes=False,
                        retirement_reason=(
                            "absent_from_configured_scope_after_"
                            "consecutive_complete_snapshots"
                        ),
                        retirement_evidence={
                            "sync_epoch_id": _text(sync_epoch_id, 160),
                            "remote_resource_id": _text(
                                metadata.get("remote_resource_id"), 500
                            ),
                            "complete_snapshot_count": int(
                                metadata.get(
                                    "remote_missing_complete_snapshot_count"
                                )
                                or 0
                            ),
                            "required_complete_snapshot_count": grace,
                            "absence_is_remote_deletion_proof": False,
                            "customer_source_modified": False,
                        },
                    )
                    retired.append(
                        {
                            "source_ref": source_ref,
                            "source_occurrence_id": retirement.get(
                                "source_occurrence_id"
                            ),
                            "remote_resource_id": _text(
                                metadata.get("remote_resource_id"), 1000
                            ),
                            "lifecycle_status": retirement.get(
                                "lifecycle_status"
                            ),
                            "historical_source_bytes_retained": True,
                            "customer_source_modified": False,
                        }
                    )
                except Exception as exc:
                    retirement_errors.append(
                        {
                            "source_ref": source_ref,
                            "code": "CONNECTOR_REMOTE_SCOPE_RETIREMENT_FAILED",
                            "detail": type(exc).__name__,
                            "previous_occurrence_retained": True,
                        }
                    )
    status = (
        "PARTIAL"
        if retirement_errors
        else "BLOCKED_THRESHOLD"
        if policy == "RETIRE_MISSING"
        and authoritative_snapshot_complete
        and eligible_rows
        and not threshold_allowed
        else "COMPLETE"
    )
    lifecycle = {
        "schema": CONNECTOR_REMOTE_LIFECYCLE_SCHEMA,
        "status": status,
        "project_id": project,
        "connector_instance_id": connector,
        "sync_epoch_id": _text(sync_epoch_id, 160),
        "requested_deletion_policy": policy,
        "effective_deletion_policy": (
            "GUARDED_REMOTE_SCOPE_RETIREMENT"
            if policy == "RETIRE_MISSING"
            else "RETAIN"
        ),
        "authoritative_snapshot_complete": bool(
            authoritative_snapshot_complete
        ),
        "retire_after_complete_snapshots": grace,
        "present_count": len(present),
        "present_existing_occurrence_count": present_existing_count,
        "absent_count": len(absent_rows),
        "unconfirmed_missing_count": len(unconfirmed_rows),
        "retirement_eligible_count": len(eligible_rows),
        "retired_count": len(retired),
        "retired_source_occurrences": retired,
        "retire_ratio": retire_ratio,
        "max_retire_count": max_retire_count,
        "max_retire_ratio": float(max_retire_ratio),
        "retirement_threshold_allowed": threshold_allowed,
        "renamed_resource_count": renamed_count,
        "moved_resource_count": moved_count,
        "reappeared_resource_count": reappeared_count,
        "errors": retirement_errors,
        "absence_interpretation": (
            "ABSENT_FROM_CONFIGURED_SCOPE_NOT_REMOTE_DELETE_PROOF"
        ),
        "remote_deletion_inferred": False,
        "permission_loss_inferred": False,
        "historical_source_bytes_retained": True,
        "customer_material_access": "NON_MUTATING_READ_ONLY",
        "customer_material_mutation_executed": False,
        "second_source_registry_created": False,
    }
    lifecycle["sync_receipt_persisted"] = _attach_to_sync_receipt(
        project,
        connector,
        _text(sync_epoch_id, 160),
        resolved_root,
        lifecycle,
    )
    return lifecycle


__all__ = [
    "CONNECTOR_REMOTE_LIFECYCLE_SCHEMA",
    "ConnectorRemoteLifecycleError",
    "reconcile_connector_remote_lifecycle",
]
