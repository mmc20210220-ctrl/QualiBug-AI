"""Safe package expansion before enterprise documents enter the Document IR pipeline.

Archives are transport containers, never business documents. Supported packages are read
from immutable bytes and expanded into ordinary document envelopes; every child then reuses
the existing classification, versioning, parsing, evidence and runtime-registration chain.
Archive-controlled paths are never written to the filesystem.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import re
import stat
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from ._common import MAX_SOURCE_BYTES

ARCHIVE_EXPANSION_SCHEMA = "qualibug.enterprise-archive-expansion.v1"
ARCHIVE_PACKAGE_RECEIPT_SCHEMA = "qualibug.enterprise-archive-package-receipt.v1"

_ARCHIVE_SUFFIXES = {".zip", ".tar", ".tgz", ".gz", ".7z", ".rar"}
_SUPPORTED_ARCHIVE_KINDS = {"zip", "tar", "tar_gzip", "gzip"}
_SYSTEM_JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")
_TEXT_PREVIEW_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".html", ".htm", ".yaml", ".yml",
    ".csv", ".tsv", ".sql", ".json", ".xml", ".svg", ".har", ".log",
    ".feature", ".http", ".rest", ".graphql", ".gql", ".mmd", ".bpmn",
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
        }


@dataclass
class _ExpansionBudget:
    member_count: int = 0
    total_uncompressed_bytes: int = 0
    nested_archive_count: int = 0


@dataclass
class _ArchiveResult:
    documents: list[dict[str, Any]] = field(default_factory=list)
    packages: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)


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


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_storage_name(filename: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name).strip("._")
    return (value or "archive")[:120]


def _full_suffix(filename: str) -> str:
    lower = str(filename or "").lower()
    if lower.endswith(".tar.gz"):
        return ".tar.gz"
    return Path(lower).suffix


def _archive_kind(filename: str) -> str:
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
    return ""


def is_archive_envelope(envelope: dict[str, Any]) -> bool:
    filename = str(envelope.get("filename") or envelope.get("name") or "")
    if not filename and envelope.get("file_path"):
        filename = Path(str(envelope.get("file_path"))).name
    return _full_suffix(filename) in _ARCHIVE_SUFFIXES or filename.lower().endswith(".tar.gz")


def _text_preview(blob: bytes, filename: str) -> str:
    if Path(filename).suffix.lower() not in _TEXT_PREVIEW_SUFFIXES:
        return ""
    return blob[:256_000].decode("utf-8", errors="replace")


def read_document_envelope_bytes(
    envelope: dict[str, Any],
    *,
    max_bytes: int = MAX_SOURCE_BYTES,
) -> tuple[bytes, str, str]:
    """Read paths, inline text or immutable bytes under one bounded envelope contract."""

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


def _normalize_member_path(raw_name: str) -> str:
    value = str(raw_name or "").replace("\\", "/")
    if not value or "\x00" in value:
        raise ArchiveExpansionError(
            "ARCHIVE_MEMBER_PATH_INVALID",
            "archive member path is empty or contains NUL",
            member_path=value,
        )
    if value.startswith("/") or value.startswith("//") or _DRIVE_PREFIX_RE.match(value):
        raise ArchiveExpansionError(
            "ARCHIVE_MEMBER_PATH_ABSOLUTE",
            "archive member uses an absolute or drive-qualified path",
            member_path=value,
        )
    segments = value.split("/")
    if any(part in {"", ".", ".."} for part in segments):
        raise ArchiveExpansionError(
            "ARCHIVE_MEMBER_PATH_TRAVERSAL",
            "archive member path contains traversal or ambiguous segments",
            member_path=value,
        )
    normalized = str(PurePosixPath(*segments))
    if len(normalized) > 500:
        raise ArchiveExpansionError(
            "ARCHIVE_MEMBER_PATH_TOO_LONG",
            "archive member path exceeds 500 characters",
            member_path=normalized,
        )
    return normalized


def _is_system_junk(member_path: str) -> bool:
    parts = PurePosixPath(member_path).parts
    return bool(parts and parts[0].lower() == "__macosx") or PurePosixPath(member_path).name.lower() in _SYSTEM_JUNK_NAMES


def _check_member_budget(
    *,
    member_path: str,
    uncompressed_size: int,
    compressed_size: int | None,
    policy: ArchiveExpansionPolicy,
    budget: _ExpansionBudget,
) -> None:
    budget.member_count += 1
    if budget.member_count > policy.max_member_count:
        raise ArchiveExpansionError(
            "ARCHIVE_MEMBER_COUNT_LIMIT_EXCEEDED",
            f"archive contains more than {policy.max_member_count} file members",
            member_path=member_path,
        )
    if uncompressed_size < 0 or uncompressed_size > policy.max_member_bytes:
        raise ArchiveExpansionError(
            "ARCHIVE_MEMBER_SIZE_LIMIT_EXCEEDED",
            f"archive member exceeds {policy.max_member_bytes // (1024 * 1024)}MB limit",
            member_path=member_path,
        )
    budget.total_uncompressed_bytes += int(uncompressed_size)
    if budget.total_uncompressed_bytes > policy.max_total_uncompressed_bytes:
        raise ArchiveExpansionError(
            "ARCHIVE_TOTAL_SIZE_LIMIT_EXCEEDED",
            f"expanded archive exceeds {policy.max_total_uncompressed_bytes // (1024 * 1024)}MB total limit",
            member_path=member_path,
        )
    if compressed_size is not None and uncompressed_size > 0:
        if compressed_size <= 0:
            raise ArchiveExpansionError(
                "ARCHIVE_COMPRESSION_RATIO_LIMIT_EXCEEDED",
                "non-empty member has no compressed size",
                member_path=member_path,
            )
        ratio = uncompressed_size / compressed_size
        if ratio > policy.max_compression_ratio:
            raise ArchiveExpansionError(
                "ARCHIVE_COMPRESSION_RATIO_LIMIT_EXCEEDED",
                f"member compression ratio {ratio:.1f} exceeds {policy.max_compression_ratio:.1f}",
                member_path=member_path,
            )


def _read_limited(stream: Any, expected_size: int, limit: int, member_path: str) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise ArchiveExpansionError(
            "ARCHIVE_MEMBER_SIZE_LIMIT_EXCEEDED",
            f"archive member exceeds {limit // (1024 * 1024)}MB limit while reading",
            member_path=member_path,
        )
    if expected_size >= 0 and len(data) != expected_size:
        raise ArchiveExpansionError(
            "ARCHIVE_MEMBER_SIZE_MISMATCH",
            f"archive declared {expected_size} bytes but yielded {len(data)} bytes",
            member_path=member_path,
        )
    return data


def _virtual_member_path(archive_chain: list[dict[str, Any]], member_path: str) -> str:
    entered = [
        str(row.get("entered_via_member_path") or "")
        for row in archive_chain[1:]
        if str(row.get("entered_via_member_path") or "")
    ]
    return "!/".join([*entered, member_path])


def _member_envelope(
    *,
    parent: dict[str, Any],
    member_path: str,
    data: bytes,
    archive_chain: list[dict[str, Any]],
) -> dict[str, Any]:
    virtual_path = _virtual_member_path(archive_chain, member_path)
    inherited_tags = [str(value) for value in (parent.get("tags") or []) if str(value).strip()]
    envelope: dict[str, Any] = {
        "content_bytes": data,
        "filename": virtual_path,
        "tags": inherited_tags,
        "external_ref": f"archive://{archive_chain[0]['archive_hash']}/{virtual_path}",
        "archive_provenance": {
            "schema": ARCHIVE_EXPANSION_SCHEMA,
            "top_level_archive_hash": archive_chain[0]["archive_hash"],
            "top_level_archive_name": archive_chain[0]["archive_name"],
            "member_path": member_path,
            "virtual_member_path": virtual_path,
            "member_hash": _hash_bytes(data),
            "member_size": len(data),
            "archive_depth": len(archive_chain),
            "archive_chain": [dict(row) for row in archive_chain],
            "archive_member_is_business_source": True,
            "archive_container_is_business_source": False,
        },
    }
    if parent.get("inherit_source_type_to_members") is True and parent.get("source_type"):
        envelope["source_type"] = parent.get("source_type")
    return envelope


def _package_receipt(
    *,
    filename: str,
    data: bytes,
    kind: str,
    depth: int,
    parent_archive_hash: str,
    entered_via_member_path: str,
    stored_path: str,
) -> dict[str, Any]:
    return {
        "schema": ARCHIVE_PACKAGE_RECEIPT_SCHEMA,
        "archive_name": filename,
        "archive_hash": _hash_bytes(data),
        "archive_kind": kind,
        "archive_depth": depth,
        "parent_archive_hash": parent_archive_hash,
        "entered_via_member_path": entered_via_member_path,
        "compressed_byte_count": len(data),
        "stored_path": stored_path,
        "status": "PENDING",
        "member_count": 0,
        "expanded_leaf_count": 0,
        "skipped_junk_count": 0,
        "error_count": 0,
        "warning_count": 0,
        "archive_members_are_not_executed": True,
        "archive_controlled_paths_are_never_written": True,
        "network_access_used": False,
    }


def _nested_result(
    *,
    filename: str,
    data: bytes,
    parent_envelope: dict[str, Any],
    policy: ArchiveExpansionPolicy,
    budget: _ExpansionBudget,
    depth: int,
    chain: list[dict[str, Any]],
    entered_via_member_path: str,
) -> _ArchiveResult:
    budget.nested_archive_count += 1
    if budget.nested_archive_count > policy.max_nested_archive_count:
        return _ArchiveResult(
            errors=[
                ArchiveExpansionError(
                    "ARCHIVE_NESTED_COUNT_LIMIT_EXCEEDED",
                    f"nested archive count exceeds {policy.max_nested_archive_count}",
                    member_path=entered_via_member_path,
                ).to_dict()
            ]
        )
    return _expand_archive_bytes(
        filename=filename,
        data=data,
        parent_envelope=parent_envelope,
        policy=policy,
        budget=budget,
        depth=depth,
        chain=chain,
        entered_via_member_path=entered_via_member_path,
    )


def _expand_archive_bytes(
    *,
    filename: str,
    data: bytes,
    parent_envelope: dict[str, Any],
    policy: ArchiveExpansionPolicy,
    budget: _ExpansionBudget,
    depth: int,
    chain: list[dict[str, Any]],
    entered_via_member_path: str = "",
    stored_path: str = "",
) -> _ArchiveResult:
    kind = _archive_kind(filename)
    archive_hash = _hash_bytes(data)
    parent_hash = chain[-1]["archive_hash"] if chain else ""
    receipt = _package_receipt(
        filename=filename,
        data=data,
        kind=kind or "unknown",
        depth=depth,
        parent_archive_hash=parent_hash,
        entered_via_member_path=entered_via_member_path,
        stored_path=stored_path,
    )
    result = _ArchiveResult(packages=[receipt])
    local_member_count = 0
    local_uncompressed_bytes = 0

    if depth > policy.max_recursion_depth:
        result.errors.append(
            ArchiveExpansionError(
                "ARCHIVE_RECURSION_DEPTH_EXCEEDED",
                f"nested archive depth exceeds {policy.max_recursion_depth}",
                member_path=filename,
            ).to_dict(archive_name=filename, archive_hash=archive_hash)
        )
    elif kind in {"7z", "rar"}:
        result.errors.append(
            ArchiveExpansionError(
                "ARCHIVE_RUNTIME_DEPENDENCY_UNAVAILABLE",
                f"{kind.upper()} expansion runtime is not installed; source remains fail-visible",
                member_path=filename,
            ).to_dict(archive_name=filename, archive_hash=archive_hash, archive_kind=kind)
        )
    elif kind not in _SUPPORTED_ARCHIVE_KINDS:
        result.errors.append(
            ArchiveExpansionError(
                "ARCHIVE_FORMAT_UNSUPPORTED",
                "archive format is not supported",
                member_path=filename,
            ).to_dict(archive_name=filename, archive_hash=archive_hash, archive_kind=kind)
        )
    else:
        current_chain = [
            *chain,
            {
                "archive_name": filename,
                "archive_hash": archive_hash,
                "archive_kind": kind,
                "archive_depth": depth,
                "entered_via_member_path": entered_via_member_path,
            },
        ]
        seen_paths: set[str] = set()
        try:
            if kind == "zip":
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    for info in archive.infolist():
                        member_path = _normalize_member_path(info.filename.rstrip("/") if info.is_dir() else info.filename)
                        if info.is_dir():
                            continue
                        if member_path in seen_paths:
                            raise ArchiveExpansionError(
                                "ARCHIVE_MEMBER_PATH_COLLISION",
                                "archive contains duplicate normalized member paths",
                                member_path=member_path,
                            )
                        seen_paths.add(member_path)
                        if _is_system_junk(member_path):
                            result.warnings.append(
                                {
                                    "stage": "archive_expansion",
                                    "code": "ARCHIVE_SYSTEM_JUNK_SKIPPED",
                                    "member_path": member_path,
                                    "severity": "P2",
                                    "blocks_formal_understanding": False,
                                }
                            )
                            continue
                        unix_mode = (info.external_attr >> 16) & 0xFFFF
                        if unix_mode and stat.S_ISLNK(unix_mode):
                            raise ArchiveExpansionError(
                                "ARCHIVE_LINK_MEMBER_FORBIDDEN",
                                "symbolic-link archive members are forbidden",
                                member_path=member_path,
                            )
                        if info.flag_bits & 0x1:
                            raise ArchiveExpansionError(
                                "ARCHIVE_ENCRYPTED_MEMBER_UNREADABLE",
                                "encrypted archive member cannot be inspected",
                                member_path=member_path,
                            )
                        _check_member_budget(
                            member_path=member_path,
                            uncompressed_size=int(info.file_size),
                            compressed_size=int(info.compress_size),
                            policy=policy,
                            budget=budget,
                        )
                        local_member_count += 1
                        local_uncompressed_bytes += int(info.file_size)
                        with archive.open(info, "r") as stream:
                            member_data = _read_limited(
                                stream, int(info.file_size), policy.max_member_bytes, member_path
                            )
                        if is_archive_envelope({"filename": member_path}):
                            nested = _nested_result(
                                filename=member_path,
                                data=member_data,
                                parent_envelope=parent_envelope,
                                policy=policy,
                                budget=budget,
                                depth=depth + 1,
                                chain=current_chain,
                                entered_via_member_path=member_path,
                            )
                            result.packages.extend(nested.packages)
                            result.warnings.extend(nested.warnings)
                            if nested.errors:
                                result.errors.extend(nested.errors)
                                raise ArchiveExpansionError(
                                    "ARCHIVE_NESTED_MEMBER_BLOCKED",
                                    "nested archive could not be expanded completely",
                                    member_path=member_path,
                                )
                            result.documents.extend(nested.documents)
                        else:
                            result.documents.append(
                                _member_envelope(
                                    parent=parent_envelope,
                                    member_path=member_path,
                                    data=member_data,
                                    archive_chain=current_chain,
                                )
                            )
            elif kind in {"tar", "tar_gzip"}:
                with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
                    for info in archive.getmembers():
                        member_path = _normalize_member_path(info.name.rstrip("/") if info.isdir() else info.name)
                        if info.isdir():
                            continue
                        if member_path in seen_paths:
                            raise ArchiveExpansionError(
                                "ARCHIVE_MEMBER_PATH_COLLISION",
                                "archive contains duplicate normalized member paths",
                                member_path=member_path,
                            )
                        seen_paths.add(member_path)
                        if _is_system_junk(member_path):
                            result.warnings.append(
                                {
                                    "stage": "archive_expansion",
                                    "code": "ARCHIVE_SYSTEM_JUNK_SKIPPED",
                                    "member_path": member_path,
                                    "severity": "P2",
                                    "blocks_formal_understanding": False,
                                }
                            )
                            continue
                        if info.issym() or info.islnk():
                            raise ArchiveExpansionError(
                                "ARCHIVE_LINK_MEMBER_FORBIDDEN",
                                "symbolic and hard-link archive members are forbidden",
                                member_path=member_path,
                            )
                        if not info.isfile():
                            raise ArchiveExpansionError(
                                "ARCHIVE_SPECIAL_MEMBER_FORBIDDEN",
                                "device, FIFO and other special archive members are forbidden",
                                member_path=member_path,
                            )
                        _check_member_budget(
                            member_path=member_path,
                            uncompressed_size=int(info.size),
                            compressed_size=None,
                            policy=policy,
                            budget=budget,
                        )
                        local_member_count += 1
                        local_uncompressed_bytes += int(info.size)
                        stream = archive.extractfile(info)
                        if stream is None:
                            raise ArchiveExpansionError(
                                "ARCHIVE_MEMBER_READ_FAILED",
                                "tar member stream could not be opened",
                                member_path=member_path,
                            )
                        with stream:
                            member_data = _read_limited(
                                stream, int(info.size), policy.max_member_bytes, member_path
                            )
                        if is_archive_envelope({"filename": member_path}):
                            nested = _nested_result(
                                filename=member_path,
                                data=member_data,
                                parent_envelope=parent_envelope,
                                policy=policy,
                                budget=budget,
                                depth=depth + 1,
                                chain=current_chain,
                                entered_via_member_path=member_path,
                            )
                            result.packages.extend(nested.packages)
                            result.warnings.extend(nested.warnings)
                            if nested.errors:
                                result.errors.extend(nested.errors)
                                raise ArchiveExpansionError(
                                    "ARCHIVE_NESTED_MEMBER_BLOCKED",
                                    "nested archive could not be expanded completely",
                                    member_path=member_path,
                                )
                            result.documents.extend(nested.documents)
                        else:
                            result.documents.append(
                                _member_envelope(
                                    parent=parent_envelope,
                                    member_path=member_path,
                                    data=member_data,
                                    archive_chain=current_chain,
                                )
                            )
                if kind == "tar_gzip" and local_uncompressed_bytes > 0:
                    ratio = local_uncompressed_bytes / max(1, len(data))
                    if ratio > policy.max_compression_ratio:
                        raise ArchiveExpansionError(
                            "ARCHIVE_COMPRESSION_RATIO_LIMIT_EXCEEDED",
                            f"package compression ratio {ratio:.1f} exceeds {policy.max_compression_ratio:.1f}",
                            member_path=filename,
                        )
            elif kind == "gzip":
                output_name = filename[:-3] if filename.lower().endswith(".gz") else "decompressed"
                member_path = _normalize_member_path(Path(output_name).name or "decompressed")
                with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as stream:
                    member_data = stream.read(policy.max_member_bytes + 1)
                _check_member_budget(
                    member_path=member_path,
                    uncompressed_size=len(member_data),
                    compressed_size=len(data),
                    policy=policy,
                    budget=budget,
                )
                if len(member_data) > policy.max_member_bytes:
                    raise ArchiveExpansionError(
                        "ARCHIVE_MEMBER_SIZE_LIMIT_EXCEEDED",
                        f"gzip output exceeds {policy.max_member_bytes // (1024 * 1024)}MB limit",
                        member_path=member_path,
                    )
                local_member_count = 1
                local_uncompressed_bytes = len(member_data)
                if is_archive_envelope({"filename": member_path}):
                    nested = _nested_result(
                        filename=member_path,
                        data=member_data,
                        parent_envelope=parent_envelope,
                        policy=policy,
                        budget=budget,
                        depth=depth + 1,
                        chain=current_chain,
                        entered_via_member_path=member_path,
                    )
                    result.packages.extend(nested.packages)
                    result.warnings.extend(nested.warnings)
                    if nested.errors:
                        result.errors.extend(nested.errors)
                        raise ArchiveExpansionError(
                            "ARCHIVE_NESTED_MEMBER_BLOCKED",
                            "nested archive could not be expanded completely",
                            member_path=member_path,
                        )
                    result.documents.extend(nested.documents)
                else:
                    result.documents.append(
                        _member_envelope(
                            parent=parent_envelope,
                            member_path=member_path,
                            data=member_data,
                            archive_chain=current_chain,
                        )
                    )
        except ArchiveExpansionError as exc:
            result.errors.append(exc.to_dict(archive_name=filename, archive_hash=archive_hash))
        except (zipfile.BadZipFile, tarfile.TarError, gzip.BadGzipFile, EOFError, OSError) as exc:
            result.errors.append(
                ArchiveExpansionError(
                    "ARCHIVE_CONTAINER_CORRUPT",
                    f"{type(exc).__name__}: {exc}",
                    member_path=filename,
                ).to_dict(archive_name=filename, archive_hash=archive_hash)
            )

    if not result.documents and not result.errors:
        result.errors.append(
            ArchiveExpansionError(
                "ARCHIVE_NO_IMPORTABLE_MEMBERS",
                "archive contains no importable document members",
                member_path=filename,
            ).to_dict(archive_name=filename, archive_hash=archive_hash)
        )
    if result.errors:
        result.documents = []

    receipt["member_count"] = local_member_count
    receipt["expanded_leaf_count"] = len(result.documents)
    receipt["uncompressed_byte_count"] = local_uncompressed_bytes
    receipt["skipped_junk_count"] = sum(
        1 for row in result.warnings if row.get("code") == "ARCHIVE_SYSTEM_JUNK_SKIPPED"
    )
    receipt["error_count"] = len(result.errors)
    receipt["warning_count"] = len(result.warnings)
    receipt["status"] = "BLOCKED" if result.errors else "PARTIAL" if result.warnings else "COMPLETE"
    receipt["errors"] = list(result.errors)
    receipt["warnings"] = list(result.warnings)
    return result


def expand_document_envelopes(
    documents: Iterable[dict[str, Any]],
    *,
    package_store_dir: Path | None = None,
    policy: ArchiveExpansionPolicy | None = None,
) -> ArchiveExpansionBatch:
    """Expand archive envelopes and pass ordinary envelopes through unchanged."""

    resolved_policy = policy or ArchiveExpansionPolicy()
    batch = ArchiveExpansionBatch()
    budget = _ExpansionBudget()
    if package_store_dir is not None:
        package_store_dir.mkdir(parents=True, exist_ok=True)

    for index, raw in enumerate(documents or []):
        if not isinstance(raw, dict):
            batch.errors.append(
                {
                    "stage": "archive_expansion",
                    "code": "DOCUMENT_ENVELOPE_INVALID",
                    "index": index,
                    "detail": "document envelope must be object",
                    "severity": "P0",
                    "blocks_formal_understanding": True,
                }
            )
            continue
        envelope = dict(raw)
        if not is_archive_envelope(envelope):
            batch.documents.append(envelope)
            continue
        try:
            data, filename, _raw_text = read_document_envelope_bytes(
                envelope,
                max_bytes=resolved_policy.max_archive_bytes,
            )
        except Exception as exc:
            batch.errors.append(
                {
                    "stage": "archive_expansion",
                    "code": "ARCHIVE_SOURCE_READ_FAILED",
                    "index": index,
                    "detail": f"{type(exc).__name__}: {exc}"[:500],
                    "severity": "P0",
                    "blocks_formal_understanding": True,
                }
            )
            continue
        archive_hash = _hash_bytes(data)
        stored_path = ""
        if package_store_dir is not None:
            target = package_store_dir / f"{archive_hash}_{_safe_storage_name(filename)}"
            if not target.exists():
                target.write_bytes(data)
            stored_path = str(target)
        result = _expand_archive_bytes(
            filename=filename,
            data=data,
            parent_envelope=envelope,
            policy=resolved_policy,
            budget=budget,
            depth=1,
            chain=[],
            stored_path=stored_path,
        )
        batch.documents.extend(result.documents)
        batch.packages.extend(result.packages)
        batch.errors.extend(result.errors)
        batch.warnings.extend(result.warnings)
    return batch


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
