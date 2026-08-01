"""Deterministic sync-diff and downstream semantic refresh contract.

This authority deliberately separates what a connector sync proved from what
the semantic pipeline has actually executed.  It records source/artifact
impact, suppresses unchanged-source reanalysis, and exposes an explicit
pending handoff when an incremental semantic executor is not installed.  It
never claims that facts, entities, scenarios, or regression scope changed just
because a sync completed.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

CONNECTOR_SEMANTIC_REFRESH_SCHEMA = "qualibug.connector-semantic-refresh.v1"
SOURCE_EVENT_TYPES = frozenset(
    {
        "SOURCE_CREATED",
        "SOURCE_REVISION_CHANGED",
        "SOURCE_MOVED",
        "SOURCE_RENAMED",
        "SOURCE_BECAME_UNAVAILABLE",
        "SOURCE_REAPPEARED",
        "SOURCE_PERMISSION_CHANGED",
        "SOURCE_RETIRED",
        "SOURCE_CAPABILITY_NOW_SUPPORTED",
    }
)
_MATERIALIZED_AVAILABILITY = {"", "AVAILABLE"}


class ConnectorSemanticRefreshError(RuntimeError):
    """The connector sync diff could not be represented safely."""


def _text(value: Any, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _fingerprint(value: Any) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _row_provenance(row: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for key in ("source_ref", "source_id", "source_occurrence_id", "canonical_source_id"):
        value = _text(row.get(key), 2000)
        if value:
            result.add(value)
    for key in ("source_refs", "source_ids", "source_occurrence_ids"):
        values = row.get(key) or []
        if isinstance(values, str):
            values = [values]
        if isinstance(values, list):
            result.update(_text(item, 2000) for item in values if _text(item, 2000))
    return result


def _metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("metadata")
    if isinstance(value, Mapping):
        return dict(value)
    value = row.get("source_metadata")
    return dict(value) if isinstance(value, Mapping) else {}


def _event(
    event_type: str,
    row: Mapping[str, Any],
    *,
    reason_code: str,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if event_type not in SOURCE_EVENT_TYPES:
        raise ConnectorSemanticRefreshError(f"connector_source_event_invalid:{event_type}")
    metadata = _metadata(row)
    prior_metadata = _metadata(previous or {})
    return {
        "event_id": "source-event:" + _fingerprint(
            {
                "event": event_type,
                "source_ref": _text(row.get("source_ref"), 2000),
                "source_occurrence_id": _text(row.get("source_occurrence_id"), 300),
                "previous_content_hash": _text(previous and previous.get("content_hash"), 128),
                "content_hash": _text(row.get("content_hash"), 128),
                "reason_code": reason_code,
            }
        )[:32],
        "event": event_type,
        "source_ref": _text(row.get("source_ref"), 2000),
        "source_occurrence_id": _text(row.get("source_occurrence_id"), 300),
        "reason_code": reason_code,
        "source_label": _text(
            row.get("display_title")
            or metadata.get("remote_display_title")
            or metadata.get("display_title"),
            300,
        ),
        "previous_content_hash": _text(previous and previous.get("content_hash"), 128),
        "content_hash": _text(row.get("content_hash"), 128),
        "previous_remote_revision": _text(
            (previous or {}).get("remote_revision") or prior_metadata.get("remote_revision"),
            240,
        ),
        "remote_revision": _text(
            row.get("remote_revision") or metadata.get("remote_revision"), 240
        ),
        "source_content_returned": False,
    }


def _map_lifecycle_event(raw: Mapping[str, Any]) -> str:
    declared = _text(raw.get("event") or raw.get("kind"), 120).upper()
    if declared in SOURCE_EVENT_TYPES:
        return declared
    if "RENAME" in declared:
        return "SOURCE_RENAMED"
    if "MOVE" in declared:
        return "SOURCE_MOVED"
    if "REAPPEAR" in declared:
        return "SOURCE_REAPPEARED"
    if "UNAVAILABLE" in declared or "NOT_FOUND" in declared:
        return "SOURCE_BECAME_UNAVAILABLE"
    if "CAPABILITY" in declared and "SUPPORTED" in declared:
        return "SOURCE_CAPABILITY_NOW_SUPPORTED"
    if "DELETE" in declared or "REMOVE" in declared:
        return "SOURCE_BECAME_UNAVAILABLE"
    return ""


def _current_rows(
    materialized: list[Mapping[str, Any]],
    unchanged: list[Mapping[str, Any]],
    coverage: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in [*materialized, *unchanged, *coverage]:
        if not isinstance(raw, Mapping):
            continue
        source_ref = _text(raw.get("source_ref"), 2000)
        if not source_ref:
            raise ConnectorSemanticRefreshError("connector_semantic_source_ref_required")
        rows.append(dict(raw))
    return rows


def _affected_counts(asset: Mapping[str, Any] | None, changed_refs: set[str]) -> dict[str, int]:
    counts = {"content_block": 0, "fact": 0, "entity": 0, "behavior": 0, "scenario": 0, "regression": 0}
    if not isinstance(asset, Mapping) or not changed_refs:
        return counts
    fields = {
        "content_block": ("content_blocks", "blocks"),
        "fact": ("facts", "rule_library", "semantic_candidates", "business_facts"),
        "entity": ("business_objects", "data_tables", "field_dictionary", "entities"),
        "behavior": ("state_machines", "interfaces", "permission_matrix", "relationships"),
        "scenario": ("scenarios", "jobs", "probes", "oracle_library"),
        "regression": ("regression_scope", "regression_cases"),
    }
    for bucket, keys in fields.items():
        for key in keys:
            value = asset.get(key)
            if isinstance(value, list):
                counts[bucket] += sum(
                    bool(_row_provenance(row).intersection(changed_refs))
                    for row in value
                    if isinstance(row, Mapping)
                )
    return counts


def build_connector_semantic_refresh_receipt(
    project_id: str,
    connector_instance_id: str,
    *,
    sync_epoch_id: str,
    before_observations: Mapping[str, Mapping[str, Any]],
    materialized_items: list[Mapping[str, Any]],
    unchanged_observations: list[Mapping[str, Any]],
    coverage_observations: list[Mapping[str, Any]],
    retired_source_occurrences: list[Mapping[str, Any]] | None = None,
    acl_receipt: Mapping[str, Any] | None = None,
    lifecycle_events: list[Mapping[str, Any]] | None = None,
    asset: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one source-diff receipt for the completed connector sync."""
    project = _text(project_id, 160)
    connector = _text(connector_instance_id, 160)
    epoch = _text(sync_epoch_id, 160)
    if not project or not connector or not epoch:
        raise ConnectorSemanticRefreshError("connector_semantic_refresh_identity_required")
    before_by_remote = {
        _text(remote_id, 1000): dict(row)
        for remote_id, row in before_observations.items()
        if _text(remote_id, 1000) and isinstance(row, Mapping)
    }
    current = _current_rows(materialized_items, unchanged_observations, coverage_observations)
    events: list[dict[str, Any]] = []
    unchanged_count = 0
    changed_refs: set[str] = set()
    for row in current:
        remote_id = _text(row.get("remote_resource_id"), 1000)
        previous = before_by_remote.get(remote_id)
        availability = _text(
            row.get("availability")
            or row.get("remote_availability")
            or _metadata(row).get("availability"),
            60,
        ).upper()
        if availability in {"PERMISSION_DENIED", "REMOTE_DELETED", "REMOTE_UNAVAILABLE"}:
            event_type = (
                "SOURCE_PERMISSION_CHANGED"
                if availability == "PERMISSION_DENIED"
                else "SOURCE_BECAME_UNAVAILABLE"
            )
            events.append(
                _event(
                    event_type,
                    row,
                    reason_code=(
                        "REMOTE_PERMISSION_DENIED"
                        if availability == "PERMISSION_DENIED"
                        else availability
                    ),
                    previous=previous,
                )
            )
            changed_refs.add(_text(row.get("source_ref"), 2000))
            continue
        if previous is None:
            if availability in _MATERIALIZED_AVAILABILITY and row not in coverage_observations:
                events.append(_event("SOURCE_CREATED", row, reason_code="REMOTE_RESOURCE_FIRST_SEEN"))
                changed_refs.add(_text(row.get("source_ref"), 2000))
            continue
        previous_metadata = _metadata(previous)
        metadata = _metadata(row)
        if (
            availability == "AVAILABLE"
            and _text(previous_metadata.get("acl_availability"), 60).upper()
            in {"PERMISSION_DENIED", "REMOTE_DELETED", "REMOTE_UNAVAILABLE"}
        ):
            events.append(
                _event(
                    "SOURCE_REAPPEARED",
                    row,
                    reason_code="REMOTE_ACCESS_RESTORED",
                    previous=previous,
                )
            )
            changed_refs.add(_text(row.get("source_ref"), 2000))
        content_changed = bool(
            _text(row.get("content_hash"), 128)
            and _text(previous.get("content_hash"), 128)
            and _text(row.get("content_hash"), 128)
            != _text(previous.get("content_hash"), 128)
        )
        revision_changed = bool(
            _text(row.get("remote_revision") or metadata.get("remote_revision"), 240)
            and _text(row.get("remote_revision") or metadata.get("remote_revision"), 240)
            != _text(previous.get("remote_revision") or previous_metadata.get("remote_revision"), 240)
        )
        if content_changed or revision_changed:
            events.append(
                _event(
                    "SOURCE_REVISION_CHANGED",
                    row,
                    reason_code="CONTENT_HASH_CHANGED" if content_changed else "REMOTE_REVISION_CHANGED",
                    previous=previous,
                )
            )
            changed_refs.add(_text(row.get("source_ref"), 2000))
        previous_parent = _text(previous.get("parent_remote_id") or previous_metadata.get("parent_remote_id"), 1000)
        current_parent = _text(row.get("parent_remote_id") or metadata.get("parent_remote_id"), 1000)
        if previous_parent and current_parent and previous_parent != current_parent:
            events.append(_event("SOURCE_MOVED", row, reason_code="PARENT_REMOTE_ID_CHANGED", previous=previous))
            changed_refs.add(_text(row.get("source_ref"), 2000))
        previous_title = _text(previous.get("display_title") or previous_metadata.get("remote_display_title"), 300)
        current_title = _text(row.get("display_title") or metadata.get("remote_display_title"), 300)
        if previous_title and current_title and previous_title != current_title:
            events.append(_event("SOURCE_RENAMED", row, reason_code="DISPLAY_TITLE_CHANGED", previous=previous))
            changed_refs.add(_text(row.get("source_ref"), 2000))
        if not any(event.get("source_ref") == row.get("source_ref") for event in events):
            unchanged_count += 1
    for raw in retired_source_occurrences or []:
        if not isinstance(raw, Mapping):
            continue
        source_ref = _text(raw.get("source_ref"), 2000)
        if source_ref:
            row = dict(raw)
            row.setdefault("source_ref", source_ref)
            events.append(_event("SOURCE_RETIRED", row, reason_code="SYNC_RETIREMENT_POLICY"))
            changed_refs.add(source_ref)
    acl_changed = {
        _text(row.get("source_ref"), 2000): row
        for row in (acl_receipt or {}).get("changed") or []
        if isinstance(row, Mapping) and _text(row.get("source_ref"), 2000)
    }
    for source_ref, change in acl_changed.items():
        if _text(change.get("reason_code"), 160) == "ACL_SNAPSHOT_CREATED":
            continue
        if not any(
            event.get("source_ref") == source_ref
            and event.get("event") == "SOURCE_PERMISSION_CHANGED"
            for event in events
        ):
            events.append(
                _event(
                    "SOURCE_PERMISSION_CHANGED",
                    {
                        "source_ref": source_ref,
                        "source_occurrence_id": change.get("source_occurrence_id"),
                    },
                    reason_code=_text(change.get("reason_code"), 160) or "ACL_SNAPSHOT_CHANGED",
                )
            )
        changed_refs.add(source_ref)
    for raw in lifecycle_events or []:
        if not isinstance(raw, Mapping):
            continue
        event_type = _map_lifecycle_event(raw)
        source_ref = _text(raw.get("source_ref"), 2000)
        if event_type and source_ref and not any(
            event.get("source_ref") == source_ref and event.get("event") == event_type
            for event in events
        ):
            events.append(_event(event_type, raw, reason_code="ADAPTER_LIFECYCLE_EVIDENCE"))
            changed_refs.add(source_ref)
    counts = _affected_counts(asset, changed_refs)
    changed = bool(events)
    downstream_status = "SKIPPED_NO_SOURCE_CHANGE" if not changed else "PENDING_INCREMENTAL_EXECUTOR"
    downstream = [
        {
            "stage": stage,
            "status": downstream_status,
            "executed": False,
            "source_refs_bound": len(changed_refs),
            "authority": "enterprise_knowledge_composition",
        }
        for stage in (
            "fact_reextraction",
            "entity_remerge",
            "conflict_recomputation",
            "behavior_model_impact_analysis",
            "scenario_regeneration_or_invalidation",
            "regression_scope_update",
        )
    ]
    status = "NO_CHANGE" if not changed else "PENDING_VALIDATION"
    return {
        "schema": CONNECTOR_SEMANTIC_REFRESH_SCHEMA,
        "status": status,
        "project_id": project,
        "connector_instance_id": connector,
        "sync_epoch_id": epoch,
        "source_occurrence_diff": {
            "status": "COMPLETE",
            "event_count": len(events),
            "changed_source_count": len(changed_refs),
            "unchanged_source_count": unchanged_count,
            "events": events,
        },
        "artifact_diff": {
            "status": "COMPLETE",
            "changed_source_count": len(changed_refs),
            "content_block_count": counts["content_block"],
        },
        "affected_content_blocks": counts["content_block"],
        "affected_facts": counts["fact"],
        "affected_entities": counts["entity"],
        "affected_behaviors": counts["behavior"],
        "affected_scenarios": counts["scenario"],
        "affected_regression_items": counts["regression"],
        "downstream": downstream,
        "llm_reanalysis_scheduled_count": 0,
        "unchanged_materials_reanalyzed": False,
        "full_project_recompute_requested": False,
        "incremental_executor_installed": False,
        "completion_reason": (
            "NO_SOURCE_CHANGE"
            if not changed
            else "INCREMENTAL_SEMANTIC_EXECUTOR_NOT_INSTALLED"
        ),
        "source_content_returned": False,
    }


def project_connector_semantic_refresh_receipt(
    receipt: Mapping[str, Any], *, include_events: bool = True
) -> dict[str, Any]:
    """Return an ordinary-frontend-safe impact projection."""
    source_diff = receipt.get("source_occurrence_diff") or {}
    events = []
    if include_events:
        for raw in source_diff.get("events") or []:
            if not isinstance(raw, Mapping):
                continue
            source_ref = _text(raw.get("source_ref"), 2000)
            events.append(
                {
                    "event": _text(raw.get("event"), 80),
                    "reason_code": _text(raw.get("reason_code"), 160),
                    "source_label": _text(raw.get("source_label"), 300),
                    "source_identity_fingerprint": hashlib.sha256(
                        source_ref.encode("utf-8")
                    ).hexdigest()[:32]
                    if source_ref
                    else "",
                }
            )
    return {
        "schema": CONNECTOR_SEMANTIC_REFRESH_SCHEMA,
        "status": _text(receipt.get("status"), 80),
        "sync_epoch_id": _text(receipt.get("sync_epoch_id"), 160),
        "event_count": int(source_diff.get("event_count") or 0),
        "changed_source_count": int(source_diff.get("changed_source_count") or 0),
        "unchanged_source_count": int(source_diff.get("unchanged_source_count") or 0),
        "affected_content_blocks": int(receipt.get("affected_content_blocks") or 0),
        "affected_facts": int(receipt.get("affected_facts") or 0),
        "affected_entities": int(receipt.get("affected_entities") or 0),
        "affected_behaviors": int(receipt.get("affected_behaviors") or 0),
        "affected_scenarios": int(receipt.get("affected_scenarios") or 0),
        "affected_regression_items": int(receipt.get("affected_regression_items") or 0),
        "downstream": [
            {
                "stage": _text(row.get("stage"), 120),
                "status": _text(row.get("status"), 120),
                "executed": bool(row.get("executed")),
            }
            for row in receipt.get("downstream") or []
            if isinstance(row, Mapping)
        ],
        "events": events,
        "unchanged_materials_reanalyzed": receipt.get("unchanged_materials_reanalyzed") is True,
        "full_project_recompute_requested": receipt.get("full_project_recompute_requested") is True,
        "incremental_executor_installed": receipt.get("incremental_executor_installed") is True,
        "completion_reason": _text(receipt.get("completion_reason"), 160),
        "source_content_returned": False,
        "remote_resource_identities_returned": False,
    }


__all__ = [
    "CONNECTOR_SEMANTIC_REFRESH_SCHEMA",
    "ConnectorSemanticRefreshError",
    "SOURCE_EVENT_TYPES",
    "build_connector_semantic_refresh_receipt",
    "project_connector_semantic_refresh_receipt",
]
