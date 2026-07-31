"""Stable public archive-ingestion facade.

The parser and transaction implementation live in ``archive_ingestion_core``.
This facade owns transport classification and source-file admission, including
rejecting oversized archive paths before any call to ``read_bytes``.
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

_CORE_ARCHIVE_CLASSIFIER = _core._looks_like_archive


def _is_archive_transport(filename: str, data: bytes) -> bool:
    if is_declared_document_container(filename):
        return False
    if data and inspect_pk_document_container(data):
        return False
    if is_declared_archive_transport(filename):
        return True
    return bool(_CORE_ARCHIVE_CLASSIFIER(filename, data))


_core._looks_like_archive = _is_archive_transport


def _envelope_filename(row: dict[str, Any]) -> str:
    filename = str(row.get("filename") or row.get("name") or "")
    if not filename and row.get("file_path"):
        filename = Path(str(row.get("file_path"))).name
    return filename


def _envelope_bytes_for_container_probe(
    row: dict[str, Any],
    limits: ArchiveLimits,
) -> bytes:
    raw = row.get("content_bytes")
    if isinstance(raw, (bytes, bytearray, memoryview)):
        value = bytes(raw)
        return value if len(value) <= limits.max_archive_bytes else b""
    path = Path(str(row.get("file_path"))) if row.get("file_path") else None
    if path is None or not path.exists() or not path.is_file():
        return b""
    try:
        if path.stat().st_size > limits.max_archive_bytes:
            return b""
        return path.read_bytes()
    except OSError:
        return b""


def _is_document_transport(
    row: dict[str, Any],
    limits: ArchiveLimits,
) -> bool:
    filename = _envelope_filename(row)
    if is_declared_document_container(filename):
        return True
    data = _envelope_bytes_for_container_probe(row, limits)
    return bool(data and inspect_pk_document_container(data))


def _oversized_archive_receipt(
    *,
    filename: str,
    byte_count: int,
    limits: ArchiveLimits,
) -> tuple[dict[str, Any], dict[str, Any]]:
    error = {
        "code": "ARCHIVE_SOURCE_TOO_LARGE",
        "archive_filename": filename,
        "archive_hash": "",
        "member_path": "",
        "detail": (
            f"archive byte count {byte_count} exceeds "
            f"max_archive_bytes {limits.max_archive_bytes}"
        ),
        "byte_count": byte_count,
        "max_archive_bytes": limits.max_archive_bytes,
        "severity": "P0",
        "blocks_formal_understanding": True,
        "silent_failure_allowed": False,
        "source_bytes_read": False,
    }
    receipt = {
        "schema": ARCHIVE_EXPANSION_RECEIPT_SCHEMA,
        "status": "BLOCKED",
        "archive_filename": filename,
        "archive_hash": "",
        "archive_byte_count": byte_count,
        "depth": 1,
        "provider_name": "",
        "member_count": 0,
        "expanded_document_count": 0,
        "errors": [error],
        "source_bytes_read": False,
        "failed_archive_activates_no_members": True,
    }
    return receipt, error


def expand_archive_documents(
    documents: list[dict[str, Any]],
    *,
    limits: ArchiveLimits | None = None,
    registry: ArchiveProviderRegistry | None = None,
) -> ArchiveExpansion:
    """Expand archive transports while passing document containers unchanged."""

    resolved_limits = limits or ArchiveLimits()
    passthrough: list[dict[str, Any]] = []
    candidates: list[Any] = []
    blocked = ArchiveExpansion()
    for raw in documents or []:
        if not isinstance(raw, dict):
            candidates.append(raw)
            continue
        row = dict(raw)
        filename = _envelope_filename(row)
        path = Path(str(row.get("file_path"))) if row.get("file_path") else None
        declared_archive = is_declared_archive_transport(filename)
        if declared_archive and path is not None and path.exists() and path.is_file():
            try:
                byte_count = path.stat().st_size
            except OSError as exc:
                blocked.errors.append(
                    {
                        "code": "ARCHIVE_SOURCE_STAT_FAILED",
                        "archive_filename": filename,
                        "detail": f"{type(exc).__name__}: {exc}"[:1000],
                        "severity": "P0",
                        "blocks_formal_understanding": True,
                        "silent_failure_allowed": False,
                        "source_bytes_read": False,
                    }
                )
                continue
            if byte_count > resolved_limits.max_archive_bytes:
                receipt, error = _oversized_archive_receipt(
                    filename=filename,
                    byte_count=byte_count,
                    limits=resolved_limits,
                )
                blocked.receipts.append(receipt)
                blocked.errors.append(error)
                continue
        if declared_archive:
            candidates.append(row)
        elif _is_document_transport(row, resolved_limits):
            passthrough.append(row)
        else:
            candidates.append(row)

    expansion = _core.expand_archive_documents(
        candidates,
        limits=resolved_limits,
        registry=registry,
    )
    expansion.documents = [*passthrough, *list(expansion.documents)]
    expansion.receipts = [*blocked.receipts, *list(expansion.receipts)]
    expansion.errors = [*blocked.errors, *list(expansion.errors)]
    expansion.ignored_members = [
        *blocked.ignored_members,
        *list(expansion.ignored_members),
    ]
    expansion.transport_artifacts = [
        *blocked.transport_artifacts,
        *list(expansion.transport_artifacts),
    ]
    return expansion


__all__ = list(_core.__all__)
if "expand_archive_documents" not in __all__:
    __all__.append("expand_archive_documents")
