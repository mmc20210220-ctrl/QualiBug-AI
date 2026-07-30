"""Stable public facade for the canonical atomic archive-ingestion authority.

The archive parser and transaction implementation live in :mod:`archive_ingestion_core`.
This facade owns the transport boundary: ZIP-based Office/ODF/WPS documents are ordinary
documents, not archive transports, even though their bytes begin with the PK signature.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..enterprise_material_formats import (
    inspect_pk_document_container,
    is_declared_archive_transport,
    is_declared_document_container,
)
from . import archive_ingestion_core as _core
from .archive_ingestion_core import *  # noqa: F401,F403


def _envelope_filename(row: dict[str, Any]) -> str:
    filename = str(row.get("filename") or row.get("name") or "")
    if not filename and row.get("file_path"):
        filename = Path(str(row.get("file_path"))).name
    return filename


def _envelope_bytes_for_container_probe(row: dict[str, Any]) -> bytes:
    raw = row.get("content_bytes")
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return bytes(raw)
    path = Path(str(row.get("file_path"))) if row.get("file_path") else None
    if path is None or not path.exists() or not path.is_file():
        return b""
    try:
        if path.stat().st_size > _core.ArchiveLimits().max_archive_bytes:
            return b""
        return path.read_bytes()
    except OSError:
        return b""


def _is_document_transport(row: dict[str, Any]) -> bool:
    filename = _envelope_filename(row)
    if is_declared_document_container(filename):
        return True
    data = _envelope_bytes_for_container_probe(row)
    return bool(data and inspect_pk_document_container(data))


def expand_archive_documents(
    documents: list[dict[str, Any]],
    *,
    limits: ArchiveLimits | None = None,
    registry: ArchiveProviderRegistry | None = None,
) -> ArchiveExpansion:
    """Expand archive transports while passing document containers through unchanged."""

    passthrough: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for raw in documents or []:
        if not isinstance(raw, dict):
            candidates.append(raw)
            continue
        row = dict(raw)
        filename = _envelope_filename(row)
        if is_declared_archive_transport(filename):
            candidates.append(row)
        elif _is_document_transport(row):
            passthrough.append(row)
        else:
            candidates.append(row)

    expansion = _core.expand_archive_documents(
        candidates,
        limits=limits,
        registry=registry,
    )
    expansion.documents = [*passthrough, *list(expansion.documents)]
    return expansion


# All other symbols remain delegated to the one canonical implementation. The wrapper is
# deliberately exported under the same public name so callers cannot bypass transport policy.
__all__ = list(_core.__all__)
if "expand_archive_documents" not in __all__:
    __all__.append("expand_archive_documents")
