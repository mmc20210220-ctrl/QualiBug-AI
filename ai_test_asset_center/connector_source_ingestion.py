"""Connector-to-source-registry bridge for enterprise document snapshots.

A connector adapter fetches data outside this module and supplies a snapshot.
This bridge validates the snapshot metadata, rejects obvious credential payloads,
and registers an immutable source version. It does not make network calls or
pretend that an external system was contacted.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .enterprise_source_registry import register_source_asset


class ConnectorSnapshotError(ValueError):
    """A connector snapshot is not suitable for immutable source registration."""


_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:authorization|x-api-key)\s*[:=]\s*(?:bearer\s+)?[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(?:client_secret|api_key|access_token|refresh_token)\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{12,}"),
)


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _safe_connector_id(value: Any) -> str:
    identifier = _text(value, 160)
    if not identifier:
        raise ConnectorSnapshotError("connector_id_required")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", identifier):
        raise ConnectorSnapshotError("connector_id_invalid")
    return identifier


def _reject_embedded_credentials(content: str) -> None:
    for pattern in _SECRET_PATTERNS:
        if pattern.search(content):
            raise ConnectorSnapshotError("connector_snapshot_contains_credential")


def ingest_connector_snapshot(
    project_id: str,
    *,
    root: Path,
    connector_id: str,
    source_id: str,
    source_type: str,
    content: str | dict[str, Any] | list[Any],
    external_ref: str,
    sync_cursor: str = "",
    actor: dict[str, Any] | None = None,
    filename: str = "",
) -> dict[str, Any]:
    """Register one externally fetched document snapshot as a source version."""
    connector = _safe_connector_id(connector_id)
    if not _text(project_id, 160) or not _text(source_id, 160) or not _text(source_type, 80):
        raise ConnectorSnapshotError("connector_snapshot_identity_missing")
    if content in (None, ""):
        raise ConnectorSnapshotError("connector_snapshot_content_missing")
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
    _reject_embedded_credentials(text)
    manifest = register_source_asset(
        project_id,
        source_id,
        text,
        source_type=source_type,
        root=root,
        actor=actor or {"name": f"connector:{connector}", "role": "connector"},
        origin="connector_snapshot",
        filename=filename,
        external_ref=external_ref,
        metadata={"connector_id": connector, "sync_cursor": _text(sync_cursor, 240)},
    )
    return {
        **manifest,
        "source_origin": "connector_snapshot",
        "connector_id": connector,
        "external_ref": _text(external_ref, 500),
        "sync_cursor": _text(sync_cursor, 240),
    }
