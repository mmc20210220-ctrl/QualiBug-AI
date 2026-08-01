"""Bridge externally fetched enterprise material snapshots into the canonical ingestion authority.

Connector adapters perform network access outside this module. This bridge validates one fetched
snapshot, derives a stable connector-owned source reference, and delegates immutable bytes,
parsing, content deduplication, and source-occurrence lifecycle to the enterprise knowledge
center. It never implements a second parser or source registry.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from .enterprise_knowledge_center.source_occurrence_authority import (
    ingest_enterprise_knowledge_documents,
)
from .enterprise_knowledge_center.source_occurrence_core import (
    ingest_enterprise_knowledge_document_batch,
)
from .enterprise_knowledge_center.source_occurrence_observation import (
    SourceOccurrenceObservationError,
    record_source_occurrence_observation,
    record_source_occurrence_observations_batch,
)


class ConnectorSnapshotError(ValueError):
    """A connector snapshot is not suitable for governed enterprise-material ingestion."""


_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(?:authorization|x-api-key)\s*[:=]\s*"
        r"(?:bearer\s+)?[A-Za-z0-9._~+/=-]{12,}"
    ),
    re.compile(
        r"(?i)\b(?:client_secret|api_key|access_token|refresh_token)\s*[:=]\s*"
        r"[\"']?[A-Za-z0-9._~+/=-]{12,}"
    ),
)
_ALLOWED_KNOWLEDGE_ROLES = {"knowledge_admin", "project_owner", "qa_lead", "admin"}
_CONNECTOR_ROLES = {"connector", "connector_service"}
_JSON_SOURCE_TYPES = {"openapi", "postman", "json", "api_contract"}


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _safe_connector_id(value: Any) -> str:
    identifier = _text(value, 160)
    if not identifier:
        raise ConnectorSnapshotError("connector_id_required")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", identifier):
        raise ConnectorSnapshotError("connector_id_invalid")
    return identifier


def _safe_resource_kind(value: Any) -> str:
    kind = _text(value, 80) or "document"
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", kind):
        raise ConnectorSnapshotError("connector_resource_kind_invalid")
    return kind


def _safe_filename(value: Any) -> str:
    filename = Path(_text(value, 500)).name
    if filename in {"", ".", ".."}:
        return ""
    return filename


def _default_filename(source_id: str, source_type: str, content: Any) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", _text(source_id, 120)).strip("._")
    stem = stem or "connector_document"
    suffix = (
        ".json"
        if isinstance(content, (dict, list)) or source_type in _JSON_SOURCE_TYPES
        else ".txt"
    )
    return f"{stem}{suffix}"


def _sanitized_url(value: Any) -> str:
    raw = _text(value, 1000)
    if not raw:
        return ""
    parts = urlsplit(raw)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.username
        or parts.password
    ):
        raise ConnectorSnapshotError("connector_canonical_url_invalid")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))[:1000]


def _reject_embedded_credentials(content: str) -> None:
    for pattern in _SECRET_PATTERNS:
        if pattern.search(content):
            raise ConnectorSnapshotError("connector_snapshot_contains_credential")


def _snapshot_payload(
    content: str | dict[str, Any] | list[Any] | bytes | bytearray | memoryview,
) -> tuple[dict[str, Any], str]:
    if isinstance(content, str):
        if not content:
            raise ConnectorSnapshotError("connector_snapshot_content_missing")
        return {"text": content}, content
    if isinstance(content, (dict, list)):
        text = json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return {"text": text}, text
    if isinstance(content, (bytes, bytearray, memoryview)):
        blob = bytes(content)
        if not blob:
            raise ConnectorSnapshotError("connector_snapshot_content_missing")
        credential_scan_text = blob[: 4 * 1024 * 1024].decode(
            "utf-8", errors="ignore"
        )
        return {"content_bytes": blob}, credential_scan_text
    raise ConnectorSnapshotError("connector_snapshot_content_type_unsupported")


def build_connector_source_ref(
    connector_id: str,
    remote_resource_id: str,
    *,
    resource_kind: str = "document",
) -> str:
    """Build the stable source-occurrence identity for one remote enterprise resource."""
    connector = _safe_connector_id(connector_id)
    remote_id = _text(remote_resource_id, 1000)
    if not remote_id:
        raise ConnectorSnapshotError("connector_remote_resource_id_required")
    kind = _safe_resource_kind(resource_kind)
    return (
        "connector://"
        + quote(connector, safe="._-")
        + "/"
        + quote(kind, safe="._-")
        + "/"
        + quote(remote_id, safe="")
    )


def _effective_actor(
    actor: dict[str, Any] | None,
    *,
    connector_id: str,
) -> tuple[dict[str, str], str]:
    raw = actor if isinstance(actor, dict) else {}
    name = (
        _text(raw.get("name") or raw.get("actor"), 120)
        or f"connector:{connector_id}"
    )
    requested_role = _text(raw.get("role"), 64) or "connector_service"
    if requested_role in _ALLOWED_KNOWLEDGE_ROLES:
        return {"name": name, "role": requested_role}, "DIRECT_PRIVILEGED_ACTOR"
    if requested_role in _CONNECTOR_ROLES:
        # This bridge is the trusted mutation boundary. The knowledge center currently accepts
        # operator management roles only, so connector principals delegate through that authority
        # without weakening the shared authorization helper.
        return (
            {"name": name, "role": "knowledge_admin"},
            "CONNECTOR_SERVICE_DELEGATION",
        )
    raise ConnectorSnapshotError("connector_snapshot_actor_not_authorized")


def _current_occurrence(result: dict[str, Any], source_ref: str) -> dict[str, Any]:
    rows = [
        *[
            dict(row)
            for row in result.get("source_occurrences") or []
            if isinstance(row, dict)
        ],
        *[
            dict(row)
            for row in result.get("duplicate_source_occurrences") or []
            if isinstance(row, dict)
        ],
    ]
    return next(
        (
            row
            for row in rows
            if _text(row.get("source_ref"), 2000) == source_ref
        ),
        {},
    )


def _canonical_result(
    result: dict[str, Any], canonical_source_id: str
) -> dict[str, Any]:
    rows = [
        *[dict(row) for row in result.get("created") or [] if isinstance(row, dict)],
        *[
            dict(row)
            for row in result.get("duplicates") or []
            if isinstance(row, dict)
        ],
    ]
    return next(
        (
            row
            for row in rows
            if _text(row.get("source_id"), 200) == canonical_source_id
        ),
        {},
    )


def _observation_metadata(
    *,
    connector: str,
    requested_source_id: str,
    remote_id: str,
    resource_kind: str,
    remote_revision: str,
    remote_updated_at: str,
    retrieved_at: str,
    canonical_url: str,
    parent_remote_id: str,
    sync_epoch_id: str,
    sync_cursor_fingerprint: str,
    export_format: str,
    declared_mime: str,
    remote_materialization_fingerprint: str,
    display_title: str = "",
    etag: str = "",
    last_modified: str = "",
    source_relationships_json: str = "",
    aliases_json: str = "",
    forms_present: bool | None = None,
    robots_status: str = "",
    sitemap_last_modified: str = "",
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source_origin": "connector_snapshot",
        "connector_id": connector,
        "connector_instance_id": connector,
        "requested_source_id": requested_source_id,
        "remote_resource_id": remote_id,
        "resource_kind": _safe_resource_kind(resource_kind),
        "remote_revision": _text(remote_revision, 240),
        "remote_updated_at": _text(remote_updated_at, 80),
        "retrieved_at": _text(retrieved_at, 80),
        "canonical_url": canonical_url,
        "parent_remote_id": _text(parent_remote_id, 1000),
        "sync_epoch_id": _text(sync_epoch_id, 240),
        "sync_cursor_fingerprint": sync_cursor_fingerprint,
        "export_format": _text(export_format, 80),
        "declared_mime": _text(declared_mime, 160),
        "remote_materialization_fingerprint": _text(
            remote_materialization_fingerprint, 128
        ),
    }
    for key, value, limit in (
        ("etag", etag, 1000),
        ("display_title", display_title, 300),
        ("last_modified", last_modified, 1000),
        ("source_relationships_json", source_relationships_json, 100000),
        ("aliases_json", aliases_json, 100000),
        ("robots_status", robots_status, 80),
        ("sitemap_last_modified", sitemap_last_modified, 160),
    ):
        bounded = _text(value, limit)
        if bounded:
            metadata[key] = bounded
    if forms_present is not None:
        metadata["forms_present"] = bool(forms_present)
    return metadata


def ingest_connector_snapshots_batch(
    project_id: str,
    snapshots: list[dict[str, Any]],
    *,
    root: Path,
    connector_id: str,
    sync_epoch_id: str = "",
    sync_cursor: str = "",
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ingest changed connector snapshots through one canonical batch transaction."""
    if not isinstance(snapshots, list) or not snapshots:
        raise ConnectorSnapshotError("connector_snapshot_batch_required")
    connector = _safe_connector_id(connector_id)
    effective_actor, actor_authority = _effective_actor(
        actor, connector_id=connector
    )
    cursor_fingerprint = (
        hashlib.sha256(str(sync_cursor).encode("utf-8")).hexdigest()
        if str(sync_cursor)
        else ""
    )
    envelopes: list[dict[str, Any]] = []
    prepared: list[dict[str, Any]] = []
    source_refs: set[str] = set()
    for index, raw in enumerate(snapshots):
        if not isinstance(raw, dict):
            raise ConnectorSnapshotError(
                f"connector_snapshot_batch_item_invalid:{index}"
            )
        row = dict(raw)
        requested_source_id = _text(
            row.get("source_id") or row.get("remote_resource_id"), 160
        )
        source_kind = _text(row.get("source_type"), 80)
        if not requested_source_id or not source_kind:
            raise ConnectorSnapshotError(
                f"connector_snapshot_identity_missing:{index}"
            )
        content = row.get("content")
        payload, credential_scan_text = _snapshot_payload(content)
        _reject_embedded_credentials(credential_scan_text)
        resolved_filename = _safe_filename(row.get("filename"))
        if isinstance(content, (bytes, bytearray, memoryview)) and not resolved_filename:
            raise ConnectorSnapshotError(
                f"connector_snapshot_filename_required_for_binary:{index}"
            )
        if not resolved_filename:
            resolved_filename = _default_filename(
                requested_source_id, source_kind, content
            )
        explicit_ref = _text(row.get("external_ref"), 1000)
        remote_id = _text(row.get("remote_resource_id"), 1000)
        resource_kind = _safe_resource_kind(row.get("resource_kind"))
        if explicit_ref.startswith("connector://") and not remote_id:
            source_ref = explicit_ref
            remote_id = requested_source_id
        else:
            remote_id = remote_id or explicit_ref or requested_source_id
            source_ref = build_connector_source_ref(
                connector, remote_id, resource_kind=resource_kind
            )
        if source_ref in source_refs:
            raise ConnectorSnapshotError(
                f"connector_snapshot_remote_identity_duplicate:{source_ref}"
            )
        source_refs.add(source_ref)
        safe_canonical_url = _sanitized_url(row.get("canonical_url"))
        metadata = _observation_metadata(
            connector=connector,
            requested_source_id=requested_source_id,
            remote_id=remote_id,
            resource_kind=resource_kind,
            remote_revision=_text(row.get("remote_revision"), 240),
            remote_updated_at=_text(row.get("remote_updated_at"), 80),
            retrieved_at=_text(row.get("retrieved_at"), 80),
            canonical_url=safe_canonical_url,
            parent_remote_id=_text(row.get("parent_remote_id"), 1000),
            sync_epoch_id=_text(sync_epoch_id, 240),
            sync_cursor_fingerprint=cursor_fingerprint,
            export_format=_text(row.get("export_format"), 80),
            declared_mime=_text(row.get("declared_mime"), 160),
            remote_materialization_fingerprint=_text(
                row.get("remote_materialization_fingerprint"), 128
            ),
            display_title=_text(row.get("display_title"), 300),
            etag=_text(row.get("etag"), 1000),
            last_modified=_text(row.get("last_modified"), 1000),
            source_relationships_json=_text(
                row.get("source_relationships_json"), 100000
            ),
            aliases_json=_text(row.get("aliases_json"), 100000),
            forms_present=(
                bool(row.get("forms_present"))
                if "forms_present" in row
                else None
            ),
            robots_status=_text(row.get("robots_status"), 80),
            sitemap_last_modified=_text(
                row.get("sitemap_last_modified"), 160
            ),
        )
        envelopes.append(
            {
                **payload,
                "filename": resolved_filename,
                "source_type": source_kind,
                "external_ref": source_ref,
            }
        )
        prepared.append(
            {
                "requested_source_id": requested_source_id,
                "remote_resource_id": remote_id,
                "resource_kind": resource_kind,
                "source_ref": source_ref,
                "metadata": metadata,
                "canonical_url": safe_canonical_url,
                "remote_revision": _text(row.get("remote_revision"), 240),
                "remote_updated_at": _text(row.get("remote_updated_at"), 80),
                "retrieved_at": _text(row.get("retrieved_at"), 80),
                "parent_remote_id": _text(row.get("parent_remote_id"), 1000),
                "export_format": _text(row.get("export_format"), 80),
                "declared_mime": _text(row.get("declared_mime"), 160),
                "remote_materialization_fingerprint": _text(
                    row.get("remote_materialization_fingerprint"), 128
                ),
            }
        )

    result = ingest_enterprise_knowledge_document_batch(
        project_id,
        envelopes,
        root=root,
        actor=effective_actor,
    )
    if result.get("errors"):
        first = next(
            (row for row in result.get("errors") or [] if isinstance(row, dict)),
            {},
        )
        code = _text(first.get("code") or first.get("error"), 200) or "unknown"
        raise ConnectorSnapshotError(
            f"connector_snapshot_batch_ingestion_failed:{code}"
        )
    occurrences = {
        _text(row.get("source_ref"), 2000): dict(row)
        for row in [
            *list(result.get("source_occurrences") or []),
            *list(result.get("duplicate_source_occurrences") or []),
        ]
        if isinstance(row, dict) and _text(row.get("source_ref"), 2000)
    }
    missing = [row["source_ref"] for row in prepared if row["source_ref"] not in occurrences]
    if missing:
        raise ConnectorSnapshotError(
            "connector_snapshot_batch_occurrence_missing:" + ",".join(missing[:5])
        )
    try:
        observation_receipt = record_source_occurrence_observations_batch(
            project_id,
            [
                {
                    "source_ref": row["source_ref"],
                    "metadata": row["metadata"],
                }
                for row in prepared
            ],
            root=root,
            actor=effective_actor,
        )
    except SourceOccurrenceObservationError as exc:
        raise ConnectorSnapshotError(
            f"connector_snapshot_batch_observation_failed:{exc}"
        ) from exc
    refreshed = {
        _text(row.get("source_ref"), 2000): dict(row)
        for row in observation_receipt.get("source_occurrences") or []
        if isinstance(row, dict)
    }
    items: list[dict[str, Any]] = []
    for row in prepared:
        occurrence = dict(occurrences[row["source_ref"]])
        occurrence.update(refreshed.get(row["source_ref"]) or {})
        canonical_source_id = _text(occurrence.get("canonical_source_id"), 200)
        items.append(
            {
                "source_origin": "connector_snapshot",
                "connector_id": connector,
                "connector_instance_id": connector,
                "requested_source_id": row["requested_source_id"],
                "remote_resource_id": row["remote_resource_id"],
                "resource_kind": row["resource_kind"],
                "source_ref": row["source_ref"],
                "external_ref": row["source_ref"],
                "source_occurrence": occurrence,
                "source_occurrence_id": _text(
                    occurrence.get("source_occurrence_id"), 200
                ),
                "canonical_source_id": canonical_source_id,
                "knowledge_source_id": canonical_source_id,
                "content_hash": _text(occurrence.get("content_hash"), 128),
                "remote_revision": row["remote_revision"],
                "remote_updated_at": row["remote_updated_at"],
                "retrieved_at": row["retrieved_at"],
                "canonical_url": row["canonical_url"],
                "parent_remote_id": row["parent_remote_id"],
                "sync_epoch_id": _text(sync_epoch_id, 240),
                "sync_cursor_fingerprint": cursor_fingerprint,
                "export_format": row["export_format"],
                "declared_mime": row["declared_mime"],
                "remote_materialization_fingerprint": row[
                    "remote_materialization_fingerprint"
                ],
            }
        )
    return {
        "status": "INGESTED",
        "item_count": len(items),
        "items": items,
        "source_occurrence_batch": result,
        "source_occurrence_observation_batch": observation_receipt,
        "actor_authority": actor_authority,
        "canonical_ingestion_authority": "SOURCE_OCCURRENCE_REGISTRY",
        "connector_parser_implemented": False,
        "raw_sync_cursor_persisted": False,
    }


def ingest_connector_snapshot(
    project_id: str,
    *,
    root: Path,
    connector_id: str,
    source_id: str,
    source_type: str,
    content: str | dict[str, Any] | list[Any] | bytes | bytearray | memoryview,
    external_ref: str = "",
    remote_resource_id: str = "",
    resource_kind: str = "document",
    remote_revision: str = "",
    remote_updated_at: str = "",
    retrieved_at: str = "",
    canonical_url: str = "",
    parent_remote_id: str = "",
    sync_epoch_id: str = "",
    sync_cursor: str = "",
    export_format: str = "",
    declared_mime: str = "",
    remote_materialization_fingerprint: str = "",
    display_title: str = "",
    etag: str = "",
    last_modified: str = "",
    source_relationships_json: str = "",
    aliases_json: str = "",
    forms_present: bool | None = None,
    robots_status: str = "",
    sitemap_last_modified: str = "",
    actor: dict[str, Any] | None = None,
    filename: str = "",
) -> dict[str, Any]:
    """Ingest one externally fetched snapshot through the occurrence authority.

    ``source_id`` remains a compatibility input and is returned unchanged. Remote identity is
    owned by ``remote_resource_id`` (preferred), then ``external_ref``, then ``source_id``. Binary
    snapshots require a filename so existing document adapters can interpret the official export
    without a connector-specific parser.
    """
    connector = _safe_connector_id(connector_id)
    requested_source_id = _text(source_id, 160)
    source_kind = _text(source_type, 80)
    if not _text(project_id, 160) or not requested_source_id or not source_kind:
        raise ConnectorSnapshotError("connector_snapshot_identity_missing")

    payload, credential_scan_text = _snapshot_payload(content)
    _reject_embedded_credentials(credential_scan_text)

    resolved_filename = _safe_filename(filename)
    if isinstance(content, (bytes, bytearray, memoryview)) and not resolved_filename:
        raise ConnectorSnapshotError("connector_snapshot_filename_required_for_binary")
    if not resolved_filename:
        resolved_filename = _default_filename(
            requested_source_id, source_kind, content
        )

    explicit_ref = _text(external_ref, 1000)
    remote_id = _text(remote_resource_id, 1000)
    if explicit_ref.startswith("connector://") and not remote_id:
        source_ref = explicit_ref
        remote_id = requested_source_id
    else:
        remote_id = remote_id or explicit_ref or requested_source_id
        source_ref = build_connector_source_ref(
            connector,
            remote_id,
            resource_kind=resource_kind,
        )

    effective_actor, actor_authority = _effective_actor(
        actor, connector_id=connector
    )
    safe_canonical_url = _sanitized_url(canonical_url)
    cursor_fingerprint = (
        hashlib.sha256(str(sync_cursor).encode("utf-8")).hexdigest()
        if str(sync_cursor)
        else ""
    )
    result = ingest_enterprise_knowledge_documents(
        project_id,
        [
            {
                **payload,
                "filename": resolved_filename,
                "source_type": source_kind,
                "external_ref": source_ref,
            }
        ],
        root=root,
        actor=effective_actor,
    )
    if result.get("errors"):
        first = next(
            (
                row
                for row in result.get("errors") or []
                if isinstance(row, dict)
            ),
            {},
        )
        code = _text(first.get("code") or first.get("error"), 200) or "unknown"
        raise ConnectorSnapshotError(
            f"connector_snapshot_ingestion_failed:{code}"
        )

    occurrence = _current_occurrence(result, source_ref)
    occurrence_identity = _text(occurrence.get("source_occurrence_id"), 300) or source_ref
    try:
        observation = record_source_occurrence_observation(
            project_id,
            occurrence_identity,
            metadata=_observation_metadata(
                connector=connector,
                requested_source_id=requested_source_id,
                remote_id=remote_id,
                resource_kind=resource_kind,
                remote_revision=remote_revision,
                remote_updated_at=remote_updated_at,
                retrieved_at=retrieved_at,
                canonical_url=safe_canonical_url,
                parent_remote_id=parent_remote_id,
                sync_epoch_id=sync_epoch_id,
                sync_cursor_fingerprint=cursor_fingerprint,
                export_format=export_format,
                declared_mime=declared_mime,
                remote_materialization_fingerprint=(
                    remote_materialization_fingerprint
                ),
                display_title=display_title,
                etag=etag,
                last_modified=last_modified,
                source_relationships_json=source_relationships_json,
                aliases_json=aliases_json,
                forms_present=forms_present,
                robots_status=robots_status,
                sitemap_last_modified=sitemap_last_modified,
            ),
            root=root,
            actor=effective_actor,
        )
    except SourceOccurrenceObservationError as exc:
        raise ConnectorSnapshotError(
            f"connector_snapshot_observation_failed:{exc}"
        ) from exc
    occurrence = dict(observation.get("source_occurrence") or occurrence)

    canonical_source_id = _text(occurrence.get("canonical_source_id"), 200)
    canonical = _canonical_result(result, canonical_source_id)
    runtime_manifest = dict(canonical.get("runtime_source_manifest") or {})
    runtime_source_id = _text(runtime_manifest.get("source_id"), 200)
    runtime_source_hash = _text(runtime_manifest.get("source_hash"), 128)

    return {
        **result,
        **runtime_manifest,
        "source_origin": "connector_snapshot",
        "connector_id": connector,
        "connector_instance_id": connector,
        "requested_source_id": requested_source_id,
        "remote_resource_id": remote_id,
        "resource_kind": _safe_resource_kind(resource_kind),
        "remote_revision": _text(remote_revision, 240),
        "remote_updated_at": _text(remote_updated_at, 80),
        "retrieved_at": _text(retrieved_at, 80),
        "canonical_url": safe_canonical_url,
        "parent_remote_id": _text(parent_remote_id, 1000),
        "sync_epoch_id": _text(sync_epoch_id, 240),
        "sync_cursor_fingerprint": cursor_fingerprint,
        "export_format": _text(export_format, 80),
        "declared_mime": _text(declared_mime, 160),
        "remote_materialization_fingerprint": _text(
            remote_materialization_fingerprint, 128
        ),
        "external_ref": source_ref,
        "source_ref": source_ref,
        "source_occurrence": occurrence,
        "source_occurrence_observation": observation,
        "source_occurrence_id": _text(
            occurrence.get("source_occurrence_id"), 200
        ),
        "source_id": requested_source_id,
        "canonical_source_id": canonical_source_id,
        "knowledge_source_id": canonical_source_id,
        "runtime_source_id": runtime_source_id,
        "content_hash": _text(occurrence.get("content_hash"), 128),
        "source_hash": runtime_source_hash
        or _text(occurrence.get("content_hash"), 128),
        "runtime_source_hash": runtime_source_hash,
        "source_version_id": _text(
            runtime_manifest.get("source_version_id")
            or runtime_manifest.get("version_id"),
            200,
        ),
        "actor_authority": actor_authority,
        "canonical_ingestion_authority": "SOURCE_OCCURRENCE_REGISTRY",
        "connector_parser_implemented": False,
        "raw_sync_cursor_persisted": False,
    }


__all__ = [
    "ConnectorSnapshotError",
    "build_connector_source_ref",
    "ingest_connector_snapshot",
    "ingest_connector_snapshots_batch",
]
