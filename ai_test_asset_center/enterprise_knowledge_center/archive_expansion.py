"""Compatibility bridge from knowledge CRUD to the canonical archive authority.

All archive parsing and security policy lives in :mod:`archive_ingestion_core`; transport
classification lives in the stable :mod:`archive_ingestion` facade. This module preserves the
older ``expand_document_envelopes`` contract used by ``_crud`` and contains no archive parser.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ._common import MAX_SOURCE_BYTES
from .archive_ingestion import (
    ArchiveLimits,
    expand_archive_documents,
)

ARCHIVE_EXPANSION_SCHEMA = "qualibug.enterprise-archive-expansion.v1"
ARCHIVE_PACKAGE_RECEIPT_SCHEMA = "qualibug.enterprise-archive-package-receipt.v1"
_ARCHIVE_SUFFIXES = {".zip", ".tar", ".tgz", ".gz", ".7z", ".rar"}
_TEXT_PREVIEW_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".html",
    ".htm",
    ".yaml",
    ".yml",
    ".csv",
    ".tsv",
    ".sql",
    ".json",
    ".jsonl",
    ".ndjson",
    ".xml",
    ".svg",
    ".har",
    ".log",
    ".feature",
    ".http",
    ".rest",
    ".graphql",
    ".gql",
    ".mmd",
    ".bpmn",
    ".drawio",
}


@dataclass(frozen=True)
class ArchiveExpansionPolicy:
    max_archive_bytes: int = 100 * 1024 * 1024
    max_member_bytes: int = MAX_SOURCE_BYTES
    max_total_uncompressed_bytes: int = 200 * 1024 * 1024
    max_member_count: int = 512
    max_compression_ratio: float = 200.0
    max_recursion_depth: int = 3
    max_nested_archive_count: int = 32
    command_timeout_seconds: int = 60


@dataclass
class ArchiveExpansionBatch:
    documents: list[dict[str, Any]] = field(default_factory=list)
    packages: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": ARCHIVE_EXPANSION_SCHEMA,
            "status": "BLOCKED" if self.errors else "PARTIAL" if self.warnings else "COMPLETE",
            "document_count": len(self.documents),
            "package_count": len(self.packages),
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "packages": self.packages,
            "errors": self.errors,
            "warnings": self.warnings,
            "archive_members_are_not_executed": True,
            "archive_controlled_paths_are_never_written": True,
            "canonical_archive_authority": "archive_ingestion_core",
            "transport_classification_authority": "archive_ingestion",
            "duplicate_archive_parser_present": False,
        }


class ArchiveExpansionError(ValueError):
    def __init__(self, code: str, detail: str, *, member_path: str = "") -> None:
        super().__init__(detail)
        self.code = str(code)
        self.detail = str(detail)
        self.member_path = str(member_path)

    def to_dict(self, **extra: Any) -> dict[str, Any]:
        return {
            "stage": "archive_expansion",
            "code": self.code,
            "detail": self.detail[:500],
            "member_path": self.member_path,
            "severity": "P0",
            "blocks_formal_understanding": True,
            "silent_failure_allowed": False,
            **extra,
        }


def _full_suffix(filename: str) -> str:
    lower = str(filename or "").lower()
    if lower.endswith((".tar.gz", ".tar.gzip")):
        return ".tar.gz"
    return Path(lower).suffix


def _archive_signature(prefix: bytes) -> bool:
    return prefix.startswith(
        (
            b"PK\x03\x04",
            b"7z\xbc\xaf\x27\x1c",
            b"Rar!\x1a\x07",
            b"\x1f\x8b",
        )
    )


def is_archive_envelope(envelope: dict[str, Any]) -> bool:
    """Compatibility predicate; final routing is still decided by archive_ingestion."""

    filename = str(envelope.get("filename") or envelope.get("name") or "")
    if not filename and envelope.get("file_path"):
        filename = Path(str(envelope.get("file_path"))).name
    if _full_suffix(filename) in _ARCHIVE_SUFFIXES or filename.lower().endswith(".tar.gz"):
        return True
    raw = envelope.get("content_bytes")
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return _archive_signature(bytes(raw)[:8])
    path = Path(str(envelope.get("file_path"))) if envelope.get("file_path") else None
    if path is not None and path.exists() and path.is_file():
        try:
            with path.open("rb") as stream:
                return _archive_signature(stream.read(8))
        except OSError:
            return False
    return False


def _text_preview(blob: bytes, filename: str) -> str:
    if Path(filename).suffix.lower() not in _TEXT_PREVIEW_SUFFIXES:
        return ""
    return blob[:256_000].decode("utf-8", errors="replace")


def read_document_envelope_bytes(
    envelope: dict[str, Any],
    *,
    max_bytes: int = MAX_SOURCE_BYTES,
) -> tuple[bytes, str, str]:
    """Read paths, inline text or immutable archive-member bytes under one bound."""

    raw_bytes = envelope.get("content_bytes")
    if raw_bytes is not None:
        if not isinstance(raw_bytes, (bytes, bytearray, memoryview)):
            raise TypeError("content_bytes must be bytes-like")
        blob = bytes(raw_bytes)
        filename = str(envelope.get("filename") or envelope.get("name") or "document.bin")
        if len(blob) > max_bytes:
            raise ValueError(f"source file exceeds {max_bytes // (1024 * 1024)}MB limit")
        return blob, filename, _text_preview(blob, filename)

    if envelope.get("text") is not None:
        value = str(envelope.get("text"))
        blob = value.encode("utf-8")
        if len(blob) > max_bytes:
            raise ValueError(f"source file exceeds {max_bytes // (1024 * 1024)}MB limit")
        filename = str(envelope.get("filename") or envelope.get("name") or "inline_document.txt")
        return blob, filename, value

    file_path = Path(str(envelope.get("file_path"))) if envelope.get("file_path") else None
    if file_path is None or not file_path.exists() or not file_path.is_file():
        raise FileNotFoundError(f"source file not found: {file_path}")
    size = file_path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"source file exceeds {max_bytes // (1024 * 1024)}MB limit")
    blob = file_path.read_bytes()
    filename = str(envelope.get("filename") or envelope.get("name") or file_path.name)
    return blob, filename, _text_preview(blob, filename)


def _safe_storage_name(filename: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name).strip("._")
    return (value or "archive")[:120]


def _limits(policy: ArchiveExpansionPolicy) -> ArchiveLimits:
    return ArchiveLimits(
        max_archive_bytes=policy.max_archive_bytes,
        max_member_bytes=policy.max_member_bytes,
        max_total_uncompressed_bytes=policy.max_total_uncompressed_bytes,
        max_members=policy.max_member_count,
        max_depth=policy.max_recursion_depth,
        max_nested_archives=policy.max_nested_archive_count,
        max_compression_ratio=policy.max_compression_ratio,
        command_timeout_seconds=policy.command_timeout_seconds,
    )


def _source_policies(
    documents: list[Any],
    *,
    max_archive_bytes: int,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in documents:
        if not isinstance(raw, dict) or not is_archive_envelope(raw):
            continue
        try:
            data, _filename, _preview = read_document_envelope_bytes(
                raw,
                max_bytes=max_archive_bytes,
            )
        except Exception:
            continue
        result[hashlib.sha256(data).hexdigest()] = {
            "tags": [str(value) for value in (raw.get("tags") or []) if str(value).strip()][:20],
            "source_type": str(raw.get("source_type") or ""),
            "inherit_source_type": raw.get("inherit_source_type_to_members") is True,
        }
    return result


def _provenance_compat(
    document: dict[str, Any],
    policies: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    row = dict(document)
    provenance = dict(row.get("archive_provenance") or {})
    if not provenance:
        return row
    chain = [dict(item) for item in provenance.get("chain") or [] if isinstance(item, dict)]
    virtual_path = "!/".join(
        str(item.get("member_path") or "") for item in chain if str(item.get("member_path") or "")
    )
    root_hash = str(
        provenance.get("root_archive_hash")
        or (chain[0].get("archive_hash") if chain else "")
    )
    policy = policies.get(root_hash, {})
    row["filename"] = virtual_path or str(row.get("filename") or "")
    row["tags"] = list(policy.get("tags") or [])
    if policy.get("inherit_source_type") and policy.get("source_type"):
        row["source_type"] = str(policy["source_type"])
    elif row.get("source_type"):
        # Explicit per-document source_type hint from the ingest caller
        # (structured formats the classifier cannot infer, e.g. a UI/UX
        # requirements JSON) survives for non-archive documents.
        pass
    else:
        row.pop("source_type", None)
    provenance.update(
        {
            "top_level_archive_name": str(
                provenance.get("root_archive_filename")
                or (chain[0].get("archive_filename") if chain else "")
            ),
            "top_level_archive_hash": root_hash,
            "virtual_member_path": row["filename"],
            "member_path": str(chain[-1].get("member_path") if chain else row["filename"]),
            "member_size": len(bytes(row.get("content_bytes") or b"")),
            "archive_depth": len(chain),
            "archive_chain": chain,
            "archive_member_is_business_source": True,
            "archive_container_is_business_source": False,
        }
    )
    row["archive_provenance"] = provenance
    return row


def _archive_kind(filename: str, provider_name: str) -> str:
    suffix = _full_suffix(filename)
    if suffix == ".zip":
        return "zip"
    if suffix in {".tar.gz", ".tgz"}:
        return "tar_gzip"
    if suffix == ".tar":
        return "tar"
    if suffix == ".gz":
        return "gzip"
    if suffix == ".7z":
        return "7z"
    if suffix == ".rar":
        return "rar"
    return provider_name or "unknown"


def _package_rows(
    receipts: list[dict[str, Any]],
    stored_paths: dict[str, str],
    ignored_members: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in receipts:
        if not isinstance(raw, dict):
            continue
        receipt = dict(raw)
        archive_hash = str(receipt.get("archive_hash") or "")
        warnings = [
            dict(item)
            for item in ignored_members
            if isinstance(item, dict) and str(item.get("archive_hash") or "") == archive_hash
        ]
        rows.append(
            {
                "schema": ARCHIVE_PACKAGE_RECEIPT_SCHEMA,
                "archive_name": str(receipt.get("archive_filename") or ""),
                "archive_hash": archive_hash,
                "archive_kind": _archive_kind(
                    str(receipt.get("archive_filename") or ""),
                    str(receipt.get("provider_name") or ""),
                ),
                "archive_depth": int(receipt.get("depth") or 0),
                "parent_archive_hash": "",
                "compressed_byte_count": int(receipt.get("archive_byte_count") or 0),
                "stored_path": stored_paths.get(archive_hash, ""),
                "status": str(receipt.get("status") or "BLOCKED"),
                "member_count": int(receipt.get("member_count") or 0),
                "expanded_leaf_count": int(receipt.get("expanded_document_count") or 0),
                "skipped_junk_count": int(receipt.get("ignored_member_count") or 0),
                "error_count": len(receipt.get("errors") or []),
                "warning_count": len(warnings),
                "errors": list(receipt.get("errors") or []),
                "warnings": warnings,
                "canonical_receipt": receipt,
                "canonical_archive_authority": "archive_ingestion_core",
            }
        )
    return rows


def expand_document_envelopes(
    documents: Iterable[dict[str, Any]],
    *,
    package_store_dir: Path | None = None,
    policy: ArchiveExpansionPolicy | None = None,
) -> ArchiveExpansionBatch:
    """Delegate all package parsing to the canonical atomic archive authority."""

    resolved_policy = policy or ArchiveExpansionPolicy()
    source_documents = [dict(row) if isinstance(row, dict) else row for row in documents or []]
    policies = _source_policies(
        source_documents,
        max_archive_bytes=resolved_policy.max_archive_bytes,
    )
    expansion = expand_archive_documents(
        source_documents,
        limits=_limits(resolved_policy),
    )
    stored_paths: dict[str, str] = {}
    if package_store_dir is not None:
        package_store_dir.mkdir(parents=True, exist_ok=True)
        for artifact in expansion.transport_artifacts:
            if not isinstance(artifact, dict):
                continue
            archive_hash = str(artifact.get("archive_hash") or "")
            filename = str(artifact.get("filename") or "archive")
            data = artifact.get("data")
            if not archive_hash or not isinstance(data, (bytes, bytearray, memoryview)):
                continue
            target = package_store_dir / f"{archive_hash}_{_safe_storage_name(filename)}"
            if not target.exists():
                target.write_bytes(bytes(data))
            stored_paths[archive_hash] = str(target)
    return ArchiveExpansionBatch(
        documents=[
            _provenance_compat(row, policies)
            for row in expansion.documents
            if isinstance(row, dict)
        ],
        packages=_package_rows(
            list(expansion.receipts),
            stored_paths,
            list(expansion.ignored_members),
        ),
        errors=[dict(row) for row in expansion.errors if isinstance(row, dict)],
        warnings=[
            dict(row) for row in expansion.ignored_members if isinstance(row, dict)
        ],
    )


__all__ = [
    "ARCHIVE_EXPANSION_SCHEMA",
    "ARCHIVE_PACKAGE_RECEIPT_SCHEMA",
    "ArchiveExpansionPolicy",
    "ArchiveExpansionBatch",
    "ArchiveExpansionError",
    "is_archive_envelope",
    "read_document_envelope_bytes",
    "expand_document_envelopes",
]
