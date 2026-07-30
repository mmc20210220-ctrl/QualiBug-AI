"""Safe archive transport expansion for enterprise knowledge ingestion.

Archives are transport containers, never business documents. Safe members are expanded
into ordinary document envelopes and then passed to the existing knowledge ingestion
transaction. The archive itself is retained as an immutable transport artifact and every
member carries a source chain back to that artifact.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Protocol

from ._common import ROOT, MAX_SOURCE_BYTES
from ._utils import (
    _hash_bytes,
    _load_registry,
    _now,
    _paths,
    _require_manage_actor,
    _safe_project_id,
    _safe_slug,
    _save_registry,
)

ARCHIVE_EXPANSION_RECEIPT_SCHEMA = "qualibug.archive-expansion-receipt.v1"
ARCHIVE_INGESTION_RECEIPT_SCHEMA = "qualibug.archive-ingestion-receipt.v1"
_ARCHIVE_SUFFIXES = {".zip", ".tar", ".tgz", ".gz", ".7z", ".rar"}
_COMPOUND_TAR_SUFFIXES = (".tar.gz", ".tar.gzip")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class ArchiveLimits:
    max_archive_bytes: int = 100 * 1024 * 1024
    max_member_bytes: int = MAX_SOURCE_BYTES
    max_total_uncompressed_bytes: int = 250 * 1024 * 1024
    max_members: int = 1000
    max_depth: int = 3
    max_compression_ratio: float = 200.0
    command_timeout_seconds: int = 60


@dataclass(frozen=True)
class ArchiveMember:
    path: str
    data: bytes
    compressed_size: int = 0
    declared_size: int = 0
    encrypted: bool = False
    source_kind: str = "regular_file"


@dataclass
class ArchiveExpansion:
    documents: list[dict[str, Any]] = field(default_factory=list)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    ignored_members: list[dict[str, Any]] = field(default_factory=list)
    transport_artifacts: list[dict[str, Any]] = field(default_factory=list)


class ArchiveProvider(Protocol):
    name: str
    version: str

    def available(self) -> bool:
        ...

    def supports(self, filename: str, data: bytes) -> bool:
        ...

    def members(self, filename: str, data: bytes, limits: ArchiveLimits) -> list[ArchiveMember]:
        ...


def _normalized_member_path(raw: str) -> str:
    value = str(raw or "").replace("\\", "/").strip()
    if not value or "\x00" in value:
        raise ValueError("ARCHIVE_MEMBER_PATH_EMPTY_OR_NUL")
    if value.startswith("/") or _WINDOWS_DRIVE_RE.match(value):
        raise ValueError("ARCHIVE_MEMBER_PATH_ABSOLUTE")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("ARCHIVE_MEMBER_PATH_TRAVERSAL")
    normalized = path.as_posix()
    if len(normalized) > 1000:
        raise ValueError("ARCHIVE_MEMBER_PATH_TOO_LONG")
    return normalized


def _looks_like_archive(filename: str, data: bytes) -> bool:
    low = str(filename or "").lower()
    suffix = Path(low).suffix
    if suffix in _ARCHIVE_SUFFIXES or low.endswith(_COMPOUND_TAR_SUFFIXES):
        return True
    if data.startswith((b"PK\x03\x04", b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07")):
        return True
    if data.startswith(b"\x1f\x8b"):
        return True
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*"):
            return True
    except Exception:
        return False


def _gzip_output_name(filename: str) -> str:
    low = filename.lower()
    if low.endswith(".gz"):
        value = filename[:-3]
        return value or "decompressed.bin"
    return f"{filename}.decompressed"


def _validate_member_size(
    *,
    path: str,
    declared_size: int,
    compressed_size: int,
    limits: ArchiveLimits,
) -> None:
    if declared_size < 0:
        raise ValueError(f"ARCHIVE_MEMBER_SIZE_INVALID:{path}")
    if declared_size > limits.max_member_bytes:
        raise ValueError(f"ARCHIVE_MEMBER_TOO_LARGE:{path}:{declared_size}")
    if declared_size and compressed_size == 0:
        raise ValueError(f"ARCHIVE_MEMBER_COMPRESSION_RATIO_UNBOUNDED:{path}")
    if compressed_size > 0 and declared_size / compressed_size > limits.max_compression_ratio:
        raise ValueError(f"ARCHIVE_MEMBER_COMPRESSION_RATIO_EXCEEDED:{path}")


class ZipArchiveProvider:
    name = "stdlib-zip-archive-provider"
    version = "1"

    def available(self) -> bool:
        return True

    def supports(self, filename: str, data: bytes) -> bool:
        return data.startswith(b"PK") or str(filename).lower().endswith(".zip")

    def members(self, filename: str, data: bytes, limits: ArchiveLimits) -> list[ArchiveMember]:
        rows: list[ArchiveMember] = []
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_members:
                raise ValueError("ARCHIVE_MEMBER_COUNT_EXCEEDED")
            for info in infos:
                if info.is_dir():
                    continue
                path = _normalized_member_path(info.filename)
                unix_mode = (int(info.external_attr) >> 16) & 0xFFFF
                if unix_mode and stat.S_ISLNK(unix_mode):
                    raise ValueError(f"ARCHIVE_LINK_MEMBER_FORBIDDEN:{path}")
                encrypted = bool(int(info.flag_bits) & 0x1)
                if encrypted:
                    raise ValueError(f"ARCHIVE_ENCRYPTED_MEMBER_UNSUPPORTED:{path}")
                _validate_member_size(
                    path=path,
                    declared_size=int(info.file_size),
                    compressed_size=int(info.compress_size),
                    limits=limits,
                )
                value = archive.read(info)
                if len(value) != int(info.file_size):
                    raise ValueError(f"ARCHIVE_MEMBER_SIZE_MISMATCH:{path}")
                rows.append(
                    ArchiveMember(
                        path=path,
                        data=value,
                        compressed_size=int(info.compress_size),
                        declared_size=int(info.file_size),
                    )
                )
        return rows


class TarArchiveProvider:
    name = "stdlib-tar-archive-provider"
    version = "1"

    def available(self) -> bool:
        return True

    def supports(self, filename: str, data: bytes) -> bool:
        low = str(filename or "").lower()
        if low.endswith((".tar", ".tgz", ".tar.gz", ".tar.gzip")):
            return True
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*"):
                return True
        except Exception:
            return False

    def members(self, filename: str, data: bytes, limits: ArchiveLimits) -> list[ArchiveMember]:
        rows: list[ArchiveMember] = []
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            infos = archive.getmembers()
            if len(infos) > limits.max_members:
                raise ValueError("ARCHIVE_MEMBER_COUNT_EXCEEDED")
            for info in infos:
                if info.isdir():
                    continue
                path = _normalized_member_path(info.name)
                if not info.isfile():
                    raise ValueError(f"ARCHIVE_NON_REGULAR_MEMBER_FORBIDDEN:{path}")
                _validate_member_size(
                    path=path,
                    declared_size=int(info.size),
                    compressed_size=max(1, min(len(data), int(info.size) or 1)),
                    limits=limits,
                )
                stream = archive.extractfile(info)
                if stream is None:
                    raise ValueError(f"ARCHIVE_MEMBER_STREAM_UNAVAILABLE:{path}")
                value = stream.read(limits.max_member_bytes + 1)
                if len(value) > limits.max_member_bytes:
                    raise ValueError(f"ARCHIVE_MEMBER_TOO_LARGE:{path}:{len(value)}")
                if len(value) != int(info.size):
                    raise ValueError(f"ARCHIVE_MEMBER_SIZE_MISMATCH:{path}")
                rows.append(
                    ArchiveMember(
                        path=path,
                        data=value,
                        declared_size=int(info.size),
                    )
                )
        return rows


class GzipArchiveProvider:
    name = "stdlib-gzip-single-member-provider"
    version = "1"

    def available(self) -> bool:
        return True

    def supports(self, filename: str, data: bytes) -> bool:
        low = str(filename or "").lower()
        return data.startswith(b"\x1f\x8b") and not low.endswith((".tgz", ".tar.gz", ".tar.gzip"))

    def members(self, filename: str, data: bytes, limits: ArchiveLimits) -> list[ArchiveMember]:
        with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as stream:
            value = stream.read(limits.max_member_bytes + 1)
        if len(value) > limits.max_member_bytes:
            raise ValueError(f"ARCHIVE_MEMBER_TOO_LARGE:{filename}:{len(value)}")
        _validate_member_size(
            path=_gzip_output_name(filename),
            declared_size=len(value),
            compressed_size=len(data),
            limits=limits,
        )
        return [
            ArchiveMember(
                path=_normalized_member_path(_gzip_output_name(filename)),
                data=value,
                compressed_size=len(data),
                declared_size=len(value),
            )
        ]


class BsdtarArchiveProvider:
    """RAR/7Z reader using libarchive's bsdtar without filesystem extraction."""

    name = "bsdtar-libarchive-provider"
    version = "1"

    def _binary(self) -> str:
        return shutil.which("bsdtar") or ""

    def available(self) -> bool:
        return bool(self._binary())

    def supports(self, filename: str, data: bytes) -> bool:
        low = str(filename or "").lower()
        return low.endswith((".rar", ".7z")) or data.startswith(
            (b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07")
        )

    def _run(self, args: list[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )

    def _read_member(
        self, binary: str, archive_path: Path, member: str, limits: ArchiveLimits
    ) -> bytes:
        process = subprocess.Popen(
            [binary, "-xOf", str(archive_path), "--", member],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        chunks: list[bytes] = []
        total = 0
        try:
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limits.max_member_bytes:
                    process.kill()
                    raise ValueError(f"ARCHIVE_MEMBER_TOO_LARGE:{member}:{total}")
                chunks.append(chunk)
            stderr = process.stderr.read() if process.stderr is not None else b""
            return_code = process.wait(timeout=limits.command_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            raise ValueError(f"ARCHIVE_PROVIDER_TIMEOUT:{member}") from exc
        if return_code != 0:
            detail = stderr.decode("utf-8", errors="replace")[:500]
            raise ValueError(f"ARCHIVE_MEMBER_EXTRACTION_FAILED:{member}:{detail}")
        return b"".join(chunks)

    def members(self, filename: str, data: bytes, limits: ArchiveLimits) -> list[ArchiveMember]:
        binary = self._binary()
        if not binary:
            raise RuntimeError("ARCHIVE_RUNTIME_DEPENDENCY_UNAVAILABLE:bsdtar")
        with tempfile.TemporaryDirectory(prefix="qualibug-archive-provider-") as directory:
            archive_path = Path(directory) / (Path(filename).name or "source.archive")
            archive_path.write_bytes(data)
            listed = self._run(
                [binary, "-tvf", str(archive_path)], limits.command_timeout_seconds
            )
            if listed.returncode != 0:
                detail = listed.stderr.decode("utf-8", errors="replace")[:500]
                raise ValueError(f"ARCHIVE_LIST_FAILED:{detail}")
            entries = [line for line in listed.stdout.decode("utf-8", errors="replace").splitlines() if line]
            if len(entries) > limits.max_members:
                raise ValueError("ARCHIVE_MEMBER_COUNT_EXCEEDED")
            rows: list[ArchiveMember] = []
            for line in entries:
                parts = line.split(maxsplit=8)
                if len(parts) < 2:
                    raise ValueError("ARCHIVE_VERBOSE_LIST_UNPARSABLE")
                mode = parts[0]
                raw_name = parts[-1]
                if mode.startswith("d"):
                    continue
                path = _normalized_member_path(raw_name)
                if not mode.startswith("-"):
                    raise ValueError(f"ARCHIVE_NON_REGULAR_MEMBER_FORBIDDEN:{path}")
                value = self._read_member(binary, archive_path, path, limits)
                rows.append(
                    ArchiveMember(path=path, data=value, declared_size=len(value))
                )
            return rows


class ArchiveProviderRegistry:
    def __init__(self, providers: Iterable[ArchiveProvider] = ()) -> None:
        self._providers = list(providers)

    def matching(self, filename: str, data: bytes) -> list[ArchiveProvider]:
        return [provider for provider in self._providers if provider.supports(filename, data)]

    def resolve(self, filename: str, data: bytes) -> ArchiveProvider:
        matches = self.matching(filename, data)
        if not matches:
            raise ValueError("ARCHIVE_FORMAT_UNSUPPORTED")
        for provider in matches:
            if provider.available():
                return provider
        names = ",".join(provider.name for provider in matches)
        raise RuntimeError(f"ARCHIVE_RUNTIME_DEPENDENCY_UNAVAILABLE:{names}")


def build_default_archive_provider_registry() -> ArchiveProviderRegistry:
    return ArchiveProviderRegistry(
        [
            ZipArchiveProvider(),
            TarArchiveProvider(),
            GzipArchiveProvider(),
            BsdtarArchiveProvider(),
        ]
    )


def _archive_reference(chain: list[dict[str, Any]]) -> str:
    if not chain:
        return ""
    root = chain[0]
    value = f"archive://{root['archive_hash']}/{urllib.parse.quote(root['archive_filename'])}"
    for row in chain:
        value += "!/" + urllib.parse.quote(str(row["member_path"]))
    return value


def _expand_one(
    *,
    filename: str,
    data: bytes,
    limits: ArchiveLimits,
    registry: ArchiveProviderRegistry,
    expansion: ArchiveExpansion,
    inherited: dict[str, Any],
    chain: list[dict[str, Any]],
    depth: int,
) -> None:
    archive_hash = _hash_bytes(data)
    receipt: dict[str, Any] = {
        "schema": ARCHIVE_EXPANSION_RECEIPT_SCHEMA,
        "status": "PENDING",
        "archive_filename": filename,
        "archive_hash": archive_hash,
        "archive_byte_count": len(data),
        "depth": depth,
        "provider_name": "",
        "member_count": 0,
        "document_member_count": 0,
        "nested_archive_count": 0,
        "errors": [],
        "limits": {
            "max_archive_bytes": limits.max_archive_bytes,
            "max_member_bytes": limits.max_member_bytes,
            "max_total_uncompressed_bytes": limits.max_total_uncompressed_bytes,
            "max_members": limits.max_members,
            "max_depth": limits.max_depth,
            "max_compression_ratio": limits.max_compression_ratio,
        },
        "archive_is_transport_not_business_authority": True,
        "business_semantics_added": False,
    }
    expansion.receipts.append(receipt)
    if len(data) > limits.max_archive_bytes:
        error = {
            "code": "ARCHIVE_SOURCE_TOO_LARGE",
            "archive_filename": filename,
            "archive_hash": archive_hash,
            "byte_count": len(data),
        }
        receipt["errors"].append(error)
        receipt["status"] = "BLOCKED"
        expansion.errors.append(error)
        return
    if depth > limits.max_depth:
        error = {
            "code": "ARCHIVE_NESTING_DEPTH_EXCEEDED",
            "archive_filename": filename,
            "archive_hash": archive_hash,
            "depth": depth,
        }
        receipt["errors"].append(error)
        receipt["status"] = "BLOCKED"
        expansion.errors.append(error)
        return
    try:
        provider = registry.resolve(filename, data)
        receipt["provider_name"] = provider.name
        members = provider.members(filename, data, limits)
    except Exception as exc:
        error = {
            "code": str(exc).split(":", 1)[0] or "ARCHIVE_EXPANSION_FAILED",
            "archive_filename": filename,
            "archive_hash": archive_hash,
            "detail": f"{type(exc).__name__}: {exc}"[:1000],
        }
        receipt["errors"].append(error)
        receipt["status"] = "BLOCKED"
        expansion.errors.append(error)
        return

    seen: set[str] = set()
    total = 0
    receipt["member_count"] = len(members)
    for member in members:
        try:
            path = _normalized_member_path(member.path)
            if path in seen:
                raise ValueError(f"ARCHIVE_DUPLICATE_MEMBER_PATH:{path}")
            seen.add(path)
            total += len(member.data)
            if total > limits.max_total_uncompressed_bytes:
                raise ValueError("ARCHIVE_TOTAL_UNCOMPRESSED_SIZE_EXCEEDED")
            member_hash = _hash_bytes(member.data)
            member_chain = [
                *chain,
                {
                    "archive_filename": filename,
                    "archive_hash": archive_hash,
                    "member_path": path,
                    "member_hash": member_hash,
                    "depth": depth,
                },
            ]
            display_name = "!/".join(
                [str(row["archive_filename"]) for row in member_chain[:1]]
                + [str(row["member_path"]) for row in member_chain]
            )
            if _looks_like_archive(path, member.data):
                receipt["nested_archive_count"] += 1
                _expand_one(
                    filename=path,
                    data=member.data,
                    limits=limits,
                    registry=registry,
                    expansion=expansion,
                    inherited=inherited,
                    chain=member_chain,
                    depth=depth + 1,
                )
                continue
            tags = [
                *[str(value) for value in (inherited.get("tags") or []) if str(value).strip()],
                "archive_member",
                f"archive_depth_{depth}",
            ]
            expansion.documents.append(
                {
                    "content_bytes": member.data,
                    "filename": display_name,
                    "source_type": inherited.get("source_type"),
                    "tags": list(dict.fromkeys(tags))[:20],
                    "external_ref": _archive_reference(member_chain),
                    "archive_provenance": {
                        "schema": "qualibug.archive-member-provenance.v1",
                        "root_archive_filename": member_chain[0]["archive_filename"],
                        "root_archive_hash": member_chain[0]["archive_hash"],
                        "member_hash": member_hash,
                        "chain": member_chain,
                    },
                }
            )
            receipt["document_member_count"] += 1
        except Exception as exc:
            error = {
                "code": str(exc).split(":", 1)[0] or "ARCHIVE_MEMBER_REJECTED",
                "archive_filename": filename,
                "archive_hash": archive_hash,
                "member_path": str(getattr(member, "path", "")),
                "detail": f"{type(exc).__name__}: {exc}"[:1000],
            }
            receipt["errors"].append(error)
            expansion.errors.append(error)
    receipt["total_uncompressed_bytes"] = total
    receipt["status"] = "PARTIAL" if receipt["errors"] else "COMPLETE"


def expand_archive_documents(
    documents: list[dict[str, Any]],
    *,
    limits: ArchiveLimits | None = None,
    registry: ArchiveProviderRegistry | None = None,
) -> ArchiveExpansion:
    """Expand archive envelopes; non-archive envelopes pass through unchanged."""

    resolved_limits = limits or ArchiveLimits()
    resolved_registry = registry or build_default_archive_provider_registry()
    expansion = ArchiveExpansion()
    for index, raw in enumerate(documents or []):
        if not isinstance(raw, dict):
            expansion.errors.append(
                {"code": "DOCUMENT_ENVELOPE_INVALID", "index": index}
            )
            continue
        doc = dict(raw)
        path = Path(str(doc.get("file_path"))) if doc.get("file_path") else None
        content = doc.get("content_bytes")
        if isinstance(content, bytearray):
            content = bytes(content)
        if isinstance(content, bytes):
            data = content
            filename = str(doc.get("filename") or doc.get("name") or "source.archive")
        elif path is not None and path.exists() and path.is_file():
            data = path.read_bytes()
            filename = str(doc.get("filename") or doc.get("name") or path.name)
        else:
            expansion.documents.append(doc)
            continue
        if not _looks_like_archive(filename, data):
            expansion.documents.append(doc)
            continue
        expansion.transport_artifacts.append(
            {
                "filename": filename,
                "archive_hash": _hash_bytes(data),
                "data": data,
                "byte_count": len(data),
            }
        )
        _expand_one(
            filename=filename,
            data=data,
            limits=resolved_limits,
            registry=resolved_registry,
            expansion=expansion,
            inherited=doc,
            chain=[],
            depth=1,
        )
    return expansion


def ingest_enterprise_knowledge_archives(
    project_id: str,
    archive_paths: Iterable[str | Path],
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    source_type_hints: dict[str, str] | None = None,
    limits: ArchiveLimits | None = None,
    registry: ArchiveProviderRegistry | None = None,
    ingest_documents: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Safely expand archives and ingest each member through the canonical document path."""

    from ._crud import ingest_enterprise_knowledge_documents

    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    hints = source_type_hints or {}
    envelopes = [
        {
            "file_path": str(path),
            "filename": Path(path).name,
            "source_type": hints.get(str(path)),
        }
        for path in archive_paths
    ]
    expansion = expand_archive_documents(
        envelopes,
        limits=limits,
        registry=registry,
    )
    transport_dir = _paths(project, resolved_root)["workspace"] / "archive_transport"
    transport_dir.mkdir(parents=True, exist_ok=True)
    stored_artifacts: list[dict[str, Any]] = []
    for artifact in expansion.transport_artifacts:
        target = transport_dir / (
            f"{artifact['archive_hash']}_{_safe_slug(str(artifact['filename']))}"
        )
        if not target.exists():
            target.write_bytes(bytes(artifact["data"]))
        stored_artifacts.append(
            {
                "filename": artifact["filename"],
                "archive_hash": artifact["archive_hash"],
                "byte_count": artifact["byte_count"],
                "stored_path": str(target.relative_to(resolved_root)).replace("\\", "/"),
            }
        )

    child_result: dict[str, Any] = {
        "ok": True,
        "created": [],
        "duplicates": [],
        "errors": [],
        "warnings": [],
        "source_count": 0,
    }
    if expansion.documents:
        with tempfile.TemporaryDirectory(prefix="qualibug-archive-members-") as directory:
            temp_root = Path(directory)
            child_envelopes: list[dict[str, Any]] = []
            for index, doc in enumerate(expansion.documents):
                content = doc.get("content_bytes")
                if not isinstance(content, (bytes, bytearray)):
                    child_envelopes.append(doc)
                    continue
                filename = str(doc.get("filename") or f"member_{index}.bin")
                suffix = Path(filename).suffix
                target = temp_root / f"member_{index}_{hashlib.sha256(bytes(content)).hexdigest()[:16]}{suffix}"
                target.write_bytes(bytes(content))
                envelope = dict(doc)
                envelope.pop("content_bytes", None)
                envelope["file_path"] = str(target)
                child_envelopes.append(envelope)
            authority = ingest_documents or ingest_enterprise_knowledge_documents
            child_result = authority(
                project,
                child_envelopes,
                root=resolved_root,
                actor=clean_actor,
            )

    knowledge_registry = _load_registry(project, resolved_root)
    knowledge_registry["audit_events"].append(
        {
            "event": "archive_ingest",
            "at_utc": _now(),
            "actor": clean_actor,
            "archive_count": len(expansion.transport_artifacts),
            "archive_hashes": [row["archive_hash"] for row in stored_artifacts],
            "expanded_document_count": len(expansion.documents),
            "archive_error_count": len(expansion.errors),
            "created_source_ids": [
                row.get("source_id") for row in child_result.get("created") or []
            ],
        }
    )
    _save_registry(project, resolved_root, knowledge_registry)
    errors = [*expansion.errors, *list(child_result.get("errors") or [])]
    return {
        "schema": ARCHIVE_INGESTION_RECEIPT_SCHEMA,
        "ok": not errors and bool(child_result.get("ok", True)),
        "project_id": project,
        "archive_receipts": expansion.receipts,
        "archive_transport_artifacts": stored_artifacts,
        "expanded_document_count": len(expansion.documents),
        "created": list(child_result.get("created") or []),
        "duplicates": list(child_result.get("duplicates") or []),
        "errors": errors,
        "warnings": list(child_result.get("warnings") or []),
        "source_count": int(child_result.get("source_count") or 0),
        "archive_is_transport_not_business_authority": True,
        "members_use_canonical_document_ingestion": True,
    }


__all__ = [
    "ARCHIVE_EXPANSION_RECEIPT_SCHEMA",
    "ARCHIVE_INGESTION_RECEIPT_SCHEMA",
    "ArchiveLimits",
    "ArchiveMember",
    "ArchiveExpansion",
    "ArchiveProvider",
    "ArchiveProviderRegistry",
    "ZipArchiveProvider",
    "TarArchiveProvider",
    "GzipArchiveProvider",
    "BsdtarArchiveProvider",
    "build_default_archive_provider_registry",
    "expand_archive_documents",
    "ingest_enterprise_knowledge_archives",
]
