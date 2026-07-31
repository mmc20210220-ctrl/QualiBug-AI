"""Govern mutable observations about an immutable enterprise source occurrence.

Content bytes, interpretation identity, and occurrence version remain owned by
``source_occurrence_core``. This authority only records bounded transport observations such as a
remote revision, sync epoch, or last-seen timestamp. It deliberately stores no credentials and
never reparses or re-registers source content.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ._common import ROOT, _safe_project_id
from ._utils import _load_registry, _now, _require_manage_actor, _save_registry

SOURCE_OCCURRENCE_OBSERVATION_SCHEMA = (
    "qualibug.enterprise-source-occurrence-observation.v1"
)
_MAX_METADATA_FIELDS = 40
_MAX_METADATA_VALUE_LENGTH = 2000
_METADATA_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,119}$")
_SECRET_KEY_RE = re.compile(
    r"(?i)(?:authorization|password|passwd|secret|token|cookie|api[_-]?key|credential)"
)


class SourceOccurrenceObservationError(RuntimeError):
    """Source occurrence observation metadata could not be recorded safely."""


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise SourceOccurrenceObservationError("source_occurrence_metadata_must_be_object")
    if len(metadata) > _MAX_METADATA_FIELDS:
        raise SourceOccurrenceObservationError("source_occurrence_metadata_field_limit_exceeded")

    sanitized: dict[str, Any] = {}
    for raw_key, value in metadata.items():
        key = _text(raw_key, 120)
        if not _METADATA_KEY_RE.fullmatch(key):
            raise SourceOccurrenceObservationError(
                f"source_occurrence_metadata_key_invalid:{key or 'empty'}"
            )
        if _SECRET_KEY_RE.search(key):
            raise SourceOccurrenceObservationError(
                f"source_occurrence_metadata_secret_key_rejected:{key}"
            )
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            sanitized[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            sanitized[key] = value
        elif isinstance(value, str):
            sanitized[key] = value[:_MAX_METADATA_VALUE_LENGTH]
        else:
            raise SourceOccurrenceObservationError(
                f"source_occurrence_metadata_value_invalid:{key}"
            )
    return sanitized


def _resolve_active_occurrence(
    registry: dict[str, Any], identity: str
) -> dict[str, Any]:
    matches = [
        row
        for row in registry.get("source_occurrences") or []
        if isinstance(row, dict)
        and row.get("status") == "active"
        and (
            _text(row.get("source_occurrence_id"), 300) == identity
            or _text(row.get("source_ref"), 2000) == identity
        )
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SourceOccurrenceObservationError(
            f"source_occurrence_observation_identity_ambiguous:{identity}"
        )
    raise SourceOccurrenceObservationError(
        f"active_source_occurrence_not_found:{identity}"
    )


def record_source_occurrence_observation(
    project_id: str,
    occurrence_identity: str,
    *,
    metadata: dict[str, Any] | None = None,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge one bounded observation into an active source occurrence.

    Re-observing identical content updates ``last_seen_at_utc`` and metadata without creating a
    new content, interpretation, or occurrence identity.
    """
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    identity = _text(occurrence_identity, 2000)
    if not identity:
        raise SourceOccurrenceObservationError(
            "source_occurrence_observation_identity_required"
        )

    registry = _load_registry(project, resolved_root)
    occurrence = _resolve_active_occurrence(registry, identity)
    observed_at = _now()
    sanitized = _sanitize_metadata(metadata)
    merged = dict(occurrence.get("source_metadata") or {})
    merged.update(sanitized)
    occurrence["source_metadata"] = merged
    occurrence["last_seen_at_utc"] = observed_at
    occurrence["observation_count"] = int(occurrence.get("observation_count") or 0) + 1
    occurrence["last_observed_by"] = clean_actor

    registry.setdefault("governance", {}).update(
        {
            "source_occurrence_transport_metadata_is_provenance_only": True,
            "source_occurrence_transport_metadata_cannot_change_interpretation": True,
            "source_occurrence_credentials_forbidden": True,
        }
    )
    registry.setdefault("audit_events", []).append(
        {
            "event": "record_source_occurrence_observation",
            "at_utc": observed_at,
            "actor": clean_actor,
            "source_occurrence_id": occurrence.get("source_occurrence_id"),
            "source_ref": occurrence.get("source_ref"),
            "metadata_keys": sorted(sanitized),
            "credential_values_recorded": False,
        }
    )
    _save_registry(project, resolved_root, registry)
    return {
        "schema": SOURCE_OCCURRENCE_OBSERVATION_SCHEMA,
        "status": "RECORDED",
        "project_id": project,
        "source_occurrence_id": occurrence.get("source_occurrence_id"),
        "source_ref": occurrence.get("source_ref"),
        "last_seen_at_utc": observed_at,
        "observation_count": occurrence.get("observation_count"),
        "source_occurrence": dict(occurrence),
        "credential_values_recorded": False,
        "content_identity_changed": False,
        "interpretation_identity_changed": False,
    }


__all__ = [
    "SOURCE_OCCURRENCE_OBSERVATION_SCHEMA",
    "SourceOccurrenceObservationError",
    "record_source_occurrence_observation",
]
