"""Occurrence-aware source inventory and lifecycle authority.

The user-visible source is a source occurrence, not a canonical interpretation record. Removing
one occurrence never deletes shared bytes, chunks, or runtime source state. Removing the final
occurrence deactivates the canonical interpretation while retaining historical bytes by default;
physical deletion requires an explicit ``purge_bytes`` request. Connector scope retirement is a
separate internal lifecycle state and must never be represented as customer-source deletion.
"""
from __future__ import annotations

import copy
from collections import Counter
from pathlib import Path
from typing import Any

from . import _crud
from . import source_occurrence_core as _occurrence_core
from ._common import PHASE, ROOT, _safe_project_id
from ._utils import _load_registry, _now, _require_manage_actor, _save_registry
from .source_occurrence_authority import ingest_enterprise_knowledge_documents

SOURCE_OCCURRENCE_LIFECYCLE_SCHEMA = "qualibug.enterprise-source-occurrence-lifecycle.v2"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical_by_id(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("source_id")): row
        for row in registry.get("sources") or []
        if isinstance(row, dict) and _text(row.get("source_id"))
    }


def _occurrences(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        row
        for row in registry.get("source_occurrences") or []
        if isinstance(row, dict)
    ]
    registry["source_occurrences"] = rows
    return rows


def _active_occurrences(registry: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in _occurrences(registry) if row.get("status") == "active"]


def _safe_retirement_evidence(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("source occurrence retirement evidence must be an object")
    if len(value) > 30:
        raise ValueError("source occurrence retirement evidence field limit exceeded")
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = _text(raw_key)[:120]
        if not key or not all(ch.isalnum() or ch in "_.:-" for ch in key):
            raise ValueError("source occurrence retirement evidence key invalid")
        if raw_value is None or raw_value == "":
            continue
        if isinstance(raw_value, bool):
            result[key] = raw_value
        elif isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            result[key] = raw_value
        elif isinstance(raw_value, str):
            result[key] = raw_value[:500]
        else:
            raise ValueError(
                f"source occurrence retirement evidence value invalid: {key}"
            )
    return result


def _project_occurrence(
    occurrence: dict[str, Any], canonical: dict[str, Any] | None
) -> dict[str, Any]:
    occurrence_id = _text(occurrence.get("source_occurrence_id"))
    canonical_source_id = _text(occurrence.get("canonical_source_id"))
    metadata = copy.deepcopy(occurrence.get("source_metadata") or {})
    remote_title = _text(metadata.get("remote_display_title"))
    source_ref = _text(occurrence.get("source_ref"))
    permission_scope: dict[str, Any] = {
        "visibility": _text(metadata.get("acl_visibility"))[:40].upper()
        or "NOT_DECLARED",
        "availability": _text(metadata.get("acl_availability"))[:60].upper()
        or "NOT_DECLARED",
        "evidence_status": _text(metadata.get("acl_evidence_status"))[:40].upper()
        or "NOT_DECLARED",
        "acl_version": _text(metadata.get("acl_version"))[:240],
        "raw_remote_principals_returned": False,
    }
    if "acl_complete" in metadata:
        permission_scope["complete"] = metadata.get("acl_complete") is True
    if "acl_propagation_allowed" in metadata:
        permission_scope["propagation_allowed"] = (
            metadata.get("acl_propagation_allowed") is True
        )
    observed_at = _text(occurrence.get("last_seen_at_utc"))[:80]
    updated_at = (
        observed_at
        or _text(occurrence.get("updated_at_utc"))[:80]
        or _text(occurrence.get("created_at_utc"))[:80]
        or _text((canonical or {}).get("updated_at_utc"))[:80]
    )
    base = copy.deepcopy(canonical or {})
    base.update(
        {
            "source_id": occurrence_id,
            "source_occurrence_id": occurrence_id,
            "canonical_source_id": canonical_source_id,
            "source_ref": source_ref,
            "external_ref": source_ref,
            "source_origin_ref": source_ref,
            "content_asset_id": occurrence.get("content_asset_id"),
            "interpretation_asset_id": occurrence.get("interpretation_asset_id"),
            "content_hash": occurrence.get("content_hash")
            or (canonical or {}).get("content_hash"),
            "source_type": occurrence.get("source_type")
            or (canonical or {}).get("source_type"),
            "original_name": remote_title
            or occurrence.get("filename")
            or (canonical or {}).get("original_name"),
            "version": occurrence.get("version"),
            "occurrence_version": occurrence.get("version"),
            "created_at_utc": _text(
                occurrence.get("created_at_utc")
                or (canonical or {}).get("created_at_utc"),
            )[:80],
            "updated_at_utc": updated_at,
            "last_seen_at_utc": observed_at,
            "source_updated_at": _text(
                metadata.get("remote_updated_at")
                or metadata.get("last_modified")
            ),
            "source_origin": (
                "ONLINE_CONNECTOR"
                if source_ref.startswith("connector://")
                else "DOCUMENT_REFERENCE"
            ),
            "permission_scope": permission_scope,
            "status": occurrence.get("status"),
            "tags": list(occurrence.get("tags") or []),
            "source_metadata": metadata,
            "remote_lifecycle_state": _text(
                metadata.get("remote_lifecycle_state")
            ),
            "remote_missing_complete_snapshot_count": int(
                metadata.get("remote_missing_complete_snapshot_count") or 0
            ),
            "retired_reason": _text(occurrence.get("retired_reason")),
            "retirement_evidence": copy.deepcopy(
                occurrence.get("retirement_evidence") or {}
            ),
            "inventory_role": "SOURCE_OCCURRENCE",
            "parse_reused": bool(occurrence.get("parse_reused")),
            "independent_evidence_identity": True,
            "canonical_parse_shared": True,
        }
    )
    return base


def list_enterprise_knowledge_sources(
    project_id: str,
    root: Path | None = None,
    include_deleted: bool = False,
) -> dict[str, Any]:
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    registry = _load_registry(project, resolved_root)
    occurrence_rows = _occurrences(registry)
    if not occurrence_rows:
        return _crud.list_enterprise_knowledge_sources(
            project,
            root=resolved_root,
            include_deleted=include_deleted,
        )
    selected = (
        occurrence_rows
        if include_deleted
        else [row for row in occurrence_rows if row.get("status") == "active"]
    )
    canonical = _canonical_by_id(registry)
    sources = [
        _project_occurrence(row, canonical.get(_text(row.get("canonical_source_id"))))
        for row in selected
    ]
    status_counts = Counter(_text(row.get("status")) or "unknown" for row in occurrence_rows)
    return {
        "schema": SOURCE_OCCURRENCE_LIFECYCLE_SCHEMA,
        "phase": PHASE,
        "project_id": project,
        "sources": sources,
        "summary": {
            "active_source_count": status_counts.get("active", 0),
            "superseded_source_count": status_counts.get("superseded", 0),
            "failed_source_count": status_counts.get("failed", 0),
            "deleted_source_count": status_counts.get("deleted", 0),
            "retired_remote_scope_count": status_counts.get(
                "retired_remote_scope", 0
            ),
            "retired_archive_member_count": status_counts.get(
                "retired_archive_member", 0
            ),
            "canonical_source_count": len(canonical),
            "content_asset_count": len(
                [row for row in registry.get("content_assets") or [] if isinstance(row, dict)]
            ),
            "interpretation_asset_count": len(
                [
                    row
                    for row in registry.get("interpretation_assets") or []
                    if isinstance(row, dict)
                ]
            ),
            "source_type_distribution": dict(
                Counter(_text(row.get("source_type")) or "unknown" for row in sources)
            ),
        },
        "governance": {
            **dict(registry.get("governance") or {}),
            "public_source_inventory_identity": "SOURCE_OCCURRENCE",
            "canonical_interpretations_hidden_from_source_count": True,
            "historical_source_bytes_retained_by_default": True,
            "remote_scope_retirement_is_not_customer_source_deletion": True,
        },
    }


def _resolve_active_occurrence(
    registry: dict[str, Any], identity: str
) -> dict[str, Any]:
    active = _active_occurrences(registry)
    direct = [
        row
        for row in active
        if _text(row.get("source_occurrence_id")) == identity
        or _text(row.get("source_ref")) == identity
    ]
    if len(direct) == 1:
        return direct[0]
    if len(direct) > 1:
        raise ValueError(f"source occurrence identity is ambiguous: {identity}")
    canonical = [
        row for row in active if _text(row.get("canonical_source_id")) == identity
    ]
    if len(canonical) == 1:
        return canonical[0]
    if len(canonical) > 1:
        raise ValueError(
            "canonical source has multiple active occurrences; select one occurrence_id or source_ref"
        )
    raise KeyError(f"active source occurrence not found: {identity}")


def update_enterprise_knowledge_source(
    project_id: str,
    source_id: str,
    patch: dict[str, Any],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    registry = _load_registry(project, resolved_root)
    if not registry.get("source_occurrences"):
        return _crud.update_enterprise_knowledge_source(
            project,
            source_id,
            patch,
            root=resolved_root,
            actor=clean_actor,
        )
    occurrence = _resolve_active_occurrence(registry, _text(source_id))
    requested = set(patch or {})
    forbidden = requested.intersection(
        {"source_type", "external_ref", "source_ref", "content_hash", "filename"}
    )
    if forbidden:
        raise ValueError(
            "source occurrence identity or interpretation is immutable; re-ingest a new occurrence: "
            + ",".join(sorted(forbidden))
        )
    unknown = requested - {"tags"}
    if unknown:
        raise ValueError(
            "unsupported source occurrence metadata fields: "
            + ",".join(sorted(unknown))
        )
    if "tags" in patch:
        occurrence["tags"] = [
            str(value)[:80]
            for value in patch.get("tags") or []
            if str(value).strip()
        ][:20]
    occurrence["updated_at_utc"] = _now()
    occurrence["updated_by"] = clean_actor
    registry.setdefault("audit_events", []).append(
        {
            "event": "update_source_occurrence_metadata",
            "at_utc": _now(),
            "actor": clean_actor,
            "source_occurrence_id": occurrence.get("source_occurrence_id"),
            "source_ref": occurrence.get("source_ref"),
            "fields": sorted(requested),
        }
    )
    _save_registry(project, resolved_root, registry)
    canonical = _canonical_by_id(registry).get(
        _text(occurrence.get("canonical_source_id"))
    )
    return {
        "ok": True,
        "source": _project_occurrence(occurrence, canonical),
        "rebuild_recommended": True,
    }


def delete_enterprise_knowledge_source(
    project_id: str,
    source_id: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    purge_bytes: bool = False,
    retirement_reason: str = "",
    retirement_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    reason = _text(retirement_reason)[:240]
    retiring = bool(reason)
    evidence = _safe_retirement_evidence(retirement_evidence)
    if retiring and purge_bytes:
        raise ValueError("remote-scope retirement cannot purge source bytes")
    registry = _load_registry(project, resolved_root)
    if not registry.get("source_occurrences"):
        if retiring:
            raise ValueError(
                "remote-scope retirement requires source occurrence registry"
            )
        return _crud.delete_enterprise_knowledge_source(
            project,
            source_id,
            root=resolved_root,
            actor=clean_actor,
            purge_bytes=purge_bytes,
        )
    before = copy.deepcopy(registry)
    occurrence = _resolve_active_occurrence(registry, _text(source_id))
    occurrence_id = _text(occurrence.get("source_occurrence_id"))
    canonical_source_id = _text(occurrence.get("canonical_source_id"))
    changed_at = _now()
    if retiring:
        occurrence["status"] = "retired_remote_scope"
        occurrence["retired_at_utc"] = changed_at
        occurrence["retired_by"] = clean_actor
        occurrence["retired_reason"] = reason
        occurrence["retirement_evidence"] = evidence
    else:
        occurrence["status"] = "deleted"
        occurrence["deleted_at_utc"] = changed_at
        occurrence["deleted_by"] = clean_actor
    _occurrence_core._unlink_occurrence(registry, occurrence)
    remaining = [
        row
        for row in _active_occurrences(registry)
        if _text(row.get("canonical_source_id")) == canonical_source_id
    ]
    registry.setdefault("governance", {}).update(
        {
            "remote_scope_retirement_is_not_customer_source_deletion": True,
            "remote_scope_retirement_never_purges_bytes": True,
        }
    )
    registry.setdefault("audit_events", []).append(
        {
            "event": (
                "retire_remote_scope_source_occurrence"
                if retiring
                else "delete_source_occurrence"
            ),
            "at_utc": changed_at,
            "actor": clean_actor,
            "source_occurrence_id": occurrence_id,
            "source_ref": occurrence.get("source_ref"),
            "canonical_source_id": canonical_source_id,
            "remaining_active_occurrence_count": len(remaining),
            "retirement_reason": reason if retiring else "",
            "retirement_evidence": evidence if retiring else {},
            "purge_bytes_requested": bool(purge_bytes),
            "customer_source_modified": False,
        }
    )
    _save_registry(project, resolved_root, registry)

    canonical_delete_result: dict[str, Any] | None = None
    canonical_deactivation_result: dict[str, Any] | None = None
    if not remaining:
        try:
            if purge_bytes:
                canonical_delete_result = _crud.delete_enterprise_knowledge_source(
                    project,
                    canonical_source_id,
                    root=resolved_root,
                    actor=clean_actor,
                    purge_bytes=True,
                )
            else:
                canonical_deactivation_result = (
                    _occurrence_core.deactivate_unreferenced_canonical_sources(
                        project,
                        [canonical_source_id],
                        root=resolved_root,
                        actor=clean_actor,
                        reason=(
                            "final_source_occurrence_retired_remote_scope"
                            if retiring
                            else "final_source_occurrence_deleted"
                        ),
                    )
                )
                if canonical_deactivation_result.get("errors"):
                    raise RuntimeError(
                        str(canonical_deactivation_result.get("errors"))[:1000]
                    )
        except Exception:
            _save_registry(project, resolved_root, before)
            raise

    historical_bytes_retained = not purge_bytes
    return {
        "ok": True,
        "schema": SOURCE_OCCURRENCE_LIFECYCLE_SCHEMA,
        "source_id": occurrence_id,
        "source_occurrence_id": occurrence_id,
        "source_ref": occurrence.get("source_ref"),
        "canonical_source_id": canonical_source_id,
        "lifecycle_status": occurrence.get("status"),
        "retired_remote_scope": retiring,
        "retirement_reason": reason if retiring else "",
        "retirement_evidence": evidence if retiring else {},
        "remaining_active_occurrence_count": len(remaining),
        "canonical_source_deleted": canonical_delete_result is not None,
        "canonical_source_deactivated": canonical_deactivation_result is not None,
        "shared_bytes_retained": bool(remaining) or historical_bytes_retained,
        "shared_chunks_retained": bool(remaining) or historical_bytes_retained,
        "shared_runtime_source_retained": bool(remaining),
        "historical_source_bytes_retained": historical_bytes_retained,
        "purge_bytes_executed": bool(canonical_delete_result),
        "canonical_delete_result": canonical_delete_result or {},
        "canonical_deactivation_result": canonical_deactivation_result or {},
        "customer_source_modified": False,
        "rebuild_recommended": True,
    }


def operate_enterprise_knowledge_center(
    project_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    action = _text(action or "view").lower()
    if action in {"view", "list"}:
        from .composition import load_enterprise_business_knowledge_asset

        return {
            "ok": True,
            "action": "view",
            "inventory": list_enterprise_knowledge_sources(
                project,
                resolved_root,
                include_deleted=bool(payload.get("include_deleted")),
            ),
            "asset": load_enterprise_business_knowledge_asset(project, resolved_root)
            or {},
        }
    if action in {"upload", "ingest"}:
        documents = (
            payload.get("documents")
            if isinstance(payload.get("documents"), list)
            else []
        )
        result = ingest_enterprise_knowledge_documents(
            project,
            documents,
            root=resolved_root,
            actor=actor,
        )
        return {"ok": bool(result.get("ok")), "action": "upload", "result": result}
    if action in {"edit", "update"}:
        result = update_enterprise_knowledge_source(
            project,
            _text(payload.get("source_id") or payload.get("source_ref")),
            payload.get("patch") or {},
            root=resolved_root,
            actor=actor,
        )
        return {"ok": True, "action": "edit", "result": result}
    if action in {"delete", "remove"}:
        result = delete_enterprise_knowledge_source(
            project,
            _text(payload.get("source_id") or payload.get("source_ref")),
            root=resolved_root,
            actor=actor,
            purge_bytes=bool(payload.get("purge_bytes")),
        )
        return {"ok": True, "action": "delete", "result": result}
    if action in {"rebuild", "build"}:
        from .composition import build_enterprise_business_knowledge_asset

        asset = build_enterprise_business_knowledge_asset(
            project,
            resolved_root,
            options=payload.get("options")
            if isinstance(payload.get("options"), dict)
            else None,
        )
        return {"ok": True, "action": "rebuild", "asset": asset}
    if action in {"artifacts", "artifact-view"}:
        from .canonical_artifact import query_canonical_artifacts

        result = query_canonical_artifacts(
            project,
            resolved_root,
            artifact_id=payload.get("artifact_id"),
            source_ref=payload.get("source_ref"),
            content_hash=payload.get("content_hash"),
            knowledge_source_id=payload.get("knowledge_source_id"),
            include_deleted=bool(payload.get("include_deleted")),
        )
        return {"ok": True, "action": "artifacts", "result": result}
    if action in {"artifact-versions"}:
        from .canonical_artifact import list_artifact_versions

        result = list_artifact_versions(
            project,
            _text(payload.get("artifact_id")),
            resolved_root,
        )
        return {"ok": True, "action": "artifact-versions", "result": result}
    if action in {"artifact-diff"}:
        from .canonical_artifact import diff_artifact_versions

        result = diff_artifact_versions(
            project,
            _text(payload.get("artifact_id")),
            resolved_root,
            base_version_id=payload.get("base_version_id"),
            head_version_id=payload.get("head_version_id"),
            base_hash=payload.get("base_hash"),
            head_hash=payload.get("head_hash"),
        )
        return {"ok": True, "action": "artifact-diff", "result": result}
    raise ValueError(
        "unsupported knowledge center action; use view, upload, edit, delete, rebuild, "
        "artifacts, artifact-versions or artifact-diff"
    )


__all__ = [
    "SOURCE_OCCURRENCE_LIFECYCLE_SCHEMA",
    "list_enterprise_knowledge_sources",
    "update_enterprise_knowledge_source",
    "delete_enterprise_knowledge_source",
    "operate_enterprise_knowledge_center",
]
