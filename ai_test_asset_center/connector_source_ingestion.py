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
    actor: dict[str, Any] | None = None,
    filename: str = "",
) -> dict[str, Any]:
    """Ingest one externally fetched snapshot through the occurrence authority.

    ``source_id`` remains a compatibility input and is recorded in the receipt. Remote identity is
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
    canonical_source_id = _text(occurrence.get("canonical_source_id"), 200)
    canonical = _canonical_result(result, canonical_source_id)
    runtime_manifest = dict(canonical.get("runtime_source_manifest") or {})

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
        "external_ref": source_ref,
        "source_ref": source_ref,
        "source_occurrence": occurrence,
        "source_occurrence_id": _text(
            occurrence.get("source_occurrence_id"), 200
        ),
        "canonical_source_id": canonical_source_id,
        "source_id": _text(runtime_manifest.get("source_id"), 200)
        or canonical_source_id,
        "content_hash": _text(occurrence.get("content_hash"), 128),
        "source_hash": _text(runtime_manifest.get("source_hash"), 128)
        or _text(occurrence.get("content_hash"), 128),
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
]
