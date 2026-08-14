"""Atomic archive transport authority for enterprise knowledge ingestion.

Archives are transport containers, not business documents. Every successful leaf is delegated
back to the canonical document-ingestion transaction. Expansion is atomic per top-level archive:
a failure at any member or nesting depth rolls back every leaf from that archive while retaining
fail-visible receipts and the immutable transport artifact.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import os
import re
import selectors
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import unicodedata
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Protocol

from ._common import MAX_SOURCE_BYTES, ROOT
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
_SYSTEM_JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}


@dataclass(frozen=True)
class ArchiveLimits:
    max_archive_bytes: int = 100 * 1024 * 1024
    max_member_bytes: int = MAX_SOURCE_BYTES
    max_total_uncompressed_bytes: int = 250 * 1024 * 1024
    max_members: int = 1000
    max_depth: int = 3
    max_nested_archives: int = 64
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


@dataclass
class _ExpansionBudget:
    member_count: int = 0
    total_uncompressed_bytes: int = 0
    nested_archive_count: int = 0


class ArchiveProvider(Protocol):
    name: str
    version: str

    def available(self) -> bool:
        ...

    def supports(self, filename: str, data: bytes) -> bool:
        ...

    def members(self, filename: str, data: bytes, limits: ArchiveLimits) -> list[ArchiveMember]:
        ...


def _error(
    code: str,
    *,
    archive_filename: str,
    archive_hash: str,
    member_path: str = "",
    detail: str = "",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "code": code,
        "archive_filename": archive_filename,
        "archive_hash": archive_hash,
        "member_path": member_path,
        "detail": detail[:1000],
        "severity": "P0",
        "blocks_formal_understanding": True,
        "silent_failure_allowed": False,
        **extra,
    }


def _exception_code(exc: Exception, fallback: str = "ARCHIVE_EXPANSION_FAILED") -> str:
    if isinstance(exc, zipfile.BadZipFile):
        return "ARCHIVE_CONTAINER_CORRUPT"
    if isinstance(exc, (tarfile.TarError, gzip.BadGzipFile, EOFError)):
        return "ARCHIVE_CONTAINER_CORRUPT"
    value = str(exc).split(":", 1)[0].strip()
    return value if value.startswith("ARCHIVE_") else fallback


def _normalized_member_path(raw: str) -> str:
    value = unicodedata.normalize("NFC", str(raw or "").replace("\\", "/"))
    if not value or "\x00" in value:
        raise ValueError("ARCHIVE_MEMBER_PATH_EMPTY_OR_NUL")
    if value.startswith("/") or value.startswith("//") or _WINDOWS_DRIVE_RE.match(value):
        raise ValueError("ARCHIVE_MEMBER_PATH_ABSOLUTE")
    segments = value.split("/")
    if any(part in {"", ".", ".."} for part in segments):
        raise ValueError("ARCHIVE_MEMBER_PATH_TRAVERSAL")
    normalized = PurePosixPath(*segments).as_posix()
    if len(normalized) > 1000:
        raise ValueError("ARCHIVE_MEMBER_PATH_TOO_LONG")
    return normalized


def _is_system_junk(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts and parts[0].lower() == "__macosx") or PurePosixPath(
        path
    ).name.lower() in _SYSTEM_JUNK_NAMES


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
    if filename.lower().endswith(".gz"):
        return filename[:-3] or "decompressed.bin"
    return f"{filename}.decompressed"


def _validate_member_size(
    *,
    path: str,
    declared_size: int,
    compressed_size: int,
    limits: ArchiveLimits,
    enforce_ratio: bool = True,
) -> None:
    if declared_size < 0:
        raise ValueError(f"ARCHIVE_MEMBER_SIZE_INVALID:{path}")
    if declared_size > limits.max_member_bytes:
        raise ValueError(f"ARCHIVE_MEMBER_TOO_LARGE:{path}:{declared_size}")
    if enforce_ratio and declared_size and compressed_size <= 0:
        raise ValueError(f"ARCHIVE_MEMBER_COMPRESSION_RATIO_UNBOUNDED:{path}")
    if (
        enforce_ratio
        and compressed_size > 0
        and declared_size / compressed_size > limits.max_compression_ratio
    ):
        raise ValueError(f"ARCHIVE_MEMBER_COMPRESSION_RATIO_EXCEEDED:{path}")


class ZipArchiveProvider:
    name = "stdlib-zip-archive-provider"
    version = "2"

    def available(self) -> bool:
        return True

    def supports(self, filename: str, data: bytes) -> bool:
        return data.startswith(b"PK") or str(filename).lower().endswith(".zip")

    def members(self, filename: str, data: bytes, limits: ArchiveLimits) -> list[ArchiveMember]:
        rows: list[ArchiveMember] = []
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = [row for row in archive.infolist() if not row.is_dir()]
            if len(infos) > limits.max_members:
                raise ValueError("ARCHIVE_MEMBER_COUNT_EXCEEDED")
            declared_total = sum(max(0, int(row.file_size)) for row in infos)
            if declared_total > limits.max_total_uncompressed_bytes:
                raise ValueError("ARCHIVE_TOTAL_UNCOMPRESSED_SIZE_EXCEEDED")
            seen: set[str] = set()
            for info in infos:
                path = _normalized_member_path(info.filename)
                if path in seen:
                    raise ValueError(f"ARCHIVE_DUPLICATE_MEMBER_PATH:{path}")
                seen.add(path)
                unix_mode = (int(info.external_attr) >> 16) & 0xFFFF
                if unix_mode and stat.S_ISLNK(unix_mode):
                    raise ValueError(f"ARCHIVE_LINK_MEMBER_FORBIDDEN:{path}")
                file_type = stat.S_IFMT(unix_mode) if unix_mode else 0
                if file_type and not stat.S_ISREG(unix_mode):
                    raise ValueError(f"ARCHIVE_NON_REGULAR_MEMBER_FORBIDDEN:{path}")
                if int(info.flag_bits) & 0x1:
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
    version = "2"

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
            infos = [row for row in archive.getmembers() if not row.isdir()]
            if len(infos) > limits.max_members:
                raise ValueError("ARCHIVE_MEMBER_COUNT_EXCEEDED")
            declared_total = sum(max(0, int(row.size)) for row in infos if row.isfile())
            if declared_total > limits.max_total_uncompressed_bytes:
                raise ValueError("ARCHIVE_TOTAL_UNCOMPRESSED_SIZE_EXCEEDED")
            if data and declared_total / len(data) > limits.max_compression_ratio:
                raise ValueError("ARCHIVE_TOTAL_COMPRESSION_RATIO_EXCEEDED")
            seen: set[str] = set()
            for info in infos:
                path = _normalized_member_path(info.name)
                if path in seen:
                    raise ValueError(f"ARCHIVE_DUPLICATE_MEMBER_PATH:{path}")
                seen.add(path)
                if not info.isfile():
                    raise ValueError(f"ARCHIVE_NON_REGULAR_MEMBER_FORBIDDEN:{path}")
                _validate_member_size(
                    path=path,
                    declared_size=int(info.size),
                    compressed_size=max(1, int(info.size)),
                    limits=limits,
                    enforce_ratio=False,
                )
                stream = archive.extractfile(info)
                if stream is None:
                    raise ValueError(f"ARCHIVE_MEMBER_STREAM_UNAVAILABLE:{path}")
                with stream:
                    value = stream.read(limits.max_member_bytes + 1)
                if len(value) > limits.max_member_bytes:
                    raise ValueError(f"ARCHIVE_MEMBER_TOO_LARGE:{path}:{len(value)}")
                if len(value) != int(info.size):
                    raise ValueError(f"ARCHIVE_MEMBER_SIZE_MISMATCH:{path}")
                rows.append(ArchiveMember(path=path, data=value, declared_size=int(info.size)))
        return rows


class GzipArchiveProvider:
    name = "stdlib-gzip-single-member-provider"
    version = "2"

    def available(self) -> bool:
        return True

    def supports(self, filename: str, data: bytes) -> bool:
        low = str(filename or "").lower()
        return data.startswith(b"\x1f\x8b") and not low.endswith(
            (".tgz", ".tar.gz", ".tar.gzip")
        )

    def members(self, filename: str, data: bytes, limits: ArchiveLimits) -> list[ArchiveMember]:
        with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as stream:
            value = stream.read(limits.max_member_bytes + 1)
        if len(value) > limits.max_member_bytes:
            raise ValueError(f"ARCHIVE_MEMBER_TOO_LARGE:{filename}:{len(value)}")
        path = _normalized_member_path(_gzip_output_name(filename))
        _validate_member_size(
            path=path,
            declared_size=len(value),
            compressed_size=len(data),
            limits=limits,
        )
        return [
            ArchiveMember(
                path=path,
                data=value,
                compressed_size=len(data),
                declared_size=len(value),
            )
        ]


class BsdtarArchiveProvider:
    """RAR/7Z reader using libarchive without archive-controlled extraction paths."""

    name = "bsdtar-libarchive-provider"
    version = "2"

    @staticmethod
    def _binary() -> str:
        return shutil.which("bsdtar") or ""

    def available(self) -> bool:
        return bool(self._binary())

    def supports(self, filename: str, data: bytes) -> bool:
        low = str(filename or "").lower()
        return low.endswith((".rar", ".7z")) or data.startswith(
            (b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07")
        )

    @staticmethod
    def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )

    def _read_member(
        self,
        binary: str,
        archive_path: Path,
        member: str,
        limits: ArchiveLimits,
    ) -> bytes:
        process = subprocess.Popen(
            [binary, "-xOf", str(archive_path), "--", member],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        selector = selectors.DefaultSelector()
        stdout = process.stdout
        stderr = process.stderr
        if stdout is None or stderr is None:
            process.kill()
            raise ValueError(f"ARCHIVE_MEMBER_EXTRACTION_FAILED:{member}:missing pipes")
        selector.register(stdout, selectors.EVENT_READ, "stdout")
        selector.register(stderr, selectors.EVENT_READ, "stderr")
        chunks: list[bytes] = []
        error_chunks: list[bytes] = []
        total = 0
        deadline = time.monotonic() + limits.command_timeout_seconds
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    raise ValueError(f"ARCHIVE_PROVIDER_TIMEOUT:{member}")
                events = selector.select(timeout=min(1.0, remaining))
                if not events and process.poll() is not None:
                    for registered in list(selector.get_map().values()):
                        selector.unregister(registered.fileobj)
                    break
                for key, _mask in events:
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "stdout":
                        total += len(chunk)
                        if total > limits.max_member_bytes:
                            process.kill()
                            raise ValueError(f"ARCHIVE_MEMBER_TOO_LARGE:{member}:{total}")
                        chunks.append(chunk)
                    elif sum(len(row) for row in error_chunks) < 64 * 1024:
                        error_chunks.append(chunk)
            return_code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            process.kill()
            raise ValueError(f"ARCHIVE_PROVIDER_TIMEOUT:{member}") from exc
        finally:
            selector.close()
            if process.poll() is None:
                process.kill()
            process.wait()
        if return_code != 0:
            detail = b"".join(error_chunks).decode("utf-8", errors="replace")[:500]
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
                [binary, "-tvf", str(archive_path)],
                limits.command_timeout_seconds,
            )
            if listed.returncode != 0:
                detail = listed.stderr.decode("utf-8", errors="replace")[:500]
                raise ValueError(f"ARCHIVE_LIST_FAILED:{detail}")
            entries = [
                line
                for line in listed.stdout.decode("utf-8", errors="replace").splitlines()
                if line
            ]
            if len(entries) > limits.max_members:
                raise ValueError("ARCHIVE_MEMBER_COUNT_EXCEEDED")
            rows: list[ArchiveMember] = []
            seen: set[str] = set()
            total = 0
            for line in entries:
                parts = line.split(maxsplit=8)
                if len(parts) < 2:
                    raise ValueError("ARCHIVE_VERBOSE_LIST_UNPARSABLE")
                mode = parts[0]
                raw_name = parts[-1]
                if mode.startswith("d"):
                    continue
                path = _normalized_member_path(raw_name)
                if path in seen:
                    raise ValueError(f"ARCHIVE_DUPLICATE_MEMBER_PATH:{path}")
                seen.add(path)
                if not mode.startswith("-"):
                    raise ValueError(f"ARCHIVE_NON_REGULAR_MEMBER_FORBIDDEN:{path}")
                value = self._read_member(binary, archive_path, path, limits)
                total += len(value)
                if total > limits.max_total_uncompressed_bytes:
                    raise ValueError("ARCHIVE_TOTAL_UNCOMPRESSED_SIZE_EXCEEDED")
                rows.append(ArchiveMember(path=path, data=value, declared_size=len(value)))
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


def _display_name(chain: list[dict[str, Any]]) -> str:
    if not chain:
        return ""
    return "!/".join(
        [str(chain[0]["archive_filename"])]
        + [str(row["member_path"]) for row in chain]
    )


def _rollback_receipts(
    receipts: list[dict[str, Any]],
    *,
    receipt_start: int,
    archive_hash: str,
) -> None:
    for child in receipts[receipt_start + 1 :]:
        if child.get("status") in {"COMPLETE", "PARTIAL"}:
            child["status"] = "ROLLED_BACK"
            child["expanded_document_count"] = 0
            child["rolled_back_by_archive_hash"] = archive_hash


def _block_scope(
    *,
    expansion: ArchiveExpansion,
    receipt: dict[str, Any],
    receipt_start: int,
    document_start: int,
    error_row: dict[str, Any],
) -> None:
    del expansion.documents[document_start:]
    receipt["status"] = "BLOCKED"
    receipt["expanded_document_count"] = 0
    receipt["errors"].append(error_row)
    receipt["partial_member_activation_rolled_back"] = True
    _rollback_receipts(
        expansion.receipts,
        receipt_start=receipt_start,
        archive_hash=str(receipt.get("archive_hash") or ""),
    )


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
    budget: _ExpansionBudget,
) -> bool:
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
        "expanded_document_count": 0,
        "nested_archive_count": 0,
        "ignored_member_count": 0,
        "errors": [],
        "limits": {
            "max_archive_bytes": limits.max_archive_bytes,
            "max_member_bytes": limits.max_member_bytes,
            "max_total_uncompressed_bytes": limits.max_total_uncompressed_bytes,
            "max_members": limits.max_members,
            "max_depth": limits.max_depth,
            "max_nested_archives": limits.max_nested_archives,
            "max_compression_ratio": limits.max_compression_ratio,
        },
        "archive_is_transport_not_business_authority": True,
        "business_semantics_added": False,
        "failed_archive_activates_no_members": True,
    }
    receipt_start = len(expansion.receipts)
    document_start = len(expansion.documents)
    expansion.receipts.append(receipt)

    def fail(code: str, *, member_path: str = "", detail: str = "", **extra: Any) -> bool:
        row = _error(
            code,
            archive_filename=filename,
            archive_hash=archive_hash,
            member_path=member_path,
            detail=detail,
            **extra,
        )
        _block_scope(
            expansion=expansion,
            receipt=receipt,
            receipt_start=receipt_start,
            document_start=document_start,
            error_row=row,
        )
        expansion.errors.append(row)
        return False

    if len(data) > limits.max_archive_bytes:
        return fail("ARCHIVE_SOURCE_TOO_LARGE", byte_count=len(data))
    if depth > limits.max_depth:
        return fail("ARCHIVE_NESTING_DEPTH_EXCEEDED", depth=depth)

    try:
        provider = registry.resolve(filename, data)
        receipt["provider_name"] = provider.name
        receipt["provider_version"] = provider.version
        members = provider.members(filename, data, limits)
    except Exception as exc:
        return fail(
            _exception_code(exc),
            detail=f"{type(exc).__name__}: {exc}",
        )

    receipt["member_count"] = len(members)
    seen: set[str] = set()
    for member in members:
        try:
            path = _normalized_member_path(member.path)
            if path in seen:
                raise ValueError(f"ARCHIVE_DUPLICATE_MEMBER_PATH:{path}")
            seen.add(path)
            budget.member_count += 1
            if budget.member_count > limits.max_members:
                raise ValueError("ARCHIVE_MEMBER_COUNT_EXCEEDED")
            budget.total_uncompressed_bytes += len(member.data)
            if budget.total_uncompressed_bytes > limits.max_total_uncompressed_bytes:
                raise ValueError("ARCHIVE_TOTAL_UNCOMPRESSED_SIZE_EXCEEDED")
            if _is_system_junk(path):
                receipt["ignored_member_count"] += 1
                expansion.ignored_members.append(
                    {
                        "code": "ARCHIVE_SYSTEM_JUNK_SKIPPED",
                        "archive_filename": filename,
                        "archive_hash": archive_hash,
                        "member_path": path,
                        "severity": "P2",
                        "blocks_formal_understanding": False,
                    }
                )
                continue

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
            if _looks_like_archive(path, member.data):
                budget.nested_archive_count += 1
                receipt["nested_archive_count"] += 1
                if budget.nested_archive_count > limits.max_nested_archives:
                    raise ValueError("ARCHIVE_NESTED_COUNT_EXCEEDED")
                child_ok = _expand_one(
                    filename=path,
                    data=member.data,
                    limits=limits,
                    registry=registry,
                    expansion=expansion,
                    inherited=inherited,
                    chain=member_chain,
                    depth=depth + 1,
                    budget=budget,
                )
                if not child_ok:
                    nested_row = {
                        "code": "ARCHIVE_NESTED_MEMBER_BLOCKED",
                        "archive_filename": filename,
                        "archive_hash": archive_hash,
                        "member_path": path,
                        "severity": "P0",
                        "blocks_formal_understanding": True,
                        "silent_failure_allowed": False,
                    }
                    _block_scope(
                        expansion=expansion,
                        receipt=receipt,
                        receipt_start=receipt_start,
                        document_start=document_start,
                        error_row=nested_row,
                    )
                    return False
                continue

            tags = [
                *[str(value) for value in (inherited.get("tags") or []) if str(value).strip()],
                "archive_member",
                f"archive_depth_{depth}",
            ]
            envelope: dict[str, Any] = {
                "content_bytes": member.data,
                "filename": _display_name(member_chain),
                "tags": list(dict.fromkeys(tags))[:20],
                "external_ref": _archive_reference(member_chain),
                "archive_provenance": {
                    "schema": "qualibug.archive-member-provenance.v1",
                    "root_archive_filename": member_chain[0]["archive_filename"],
                    "root_archive_hash": member_chain[0]["archive_hash"],
                    "member_hash": member_hash,
                    "chain": member_chain,
                    "archive_is_transport_not_business_authority": True,
                },
            }
            if (
                inherited.get("source_type")
                and inherited.get("inherit_source_type_to_members") is True
            ):
                envelope["source_type"] = inherited.get("source_type")
            expansion.documents.append(envelope)
            receipt["document_member_count"] += 1
        except Exception as exc:
            return fail(
                _exception_code(exc, "ARCHIVE_MEMBER_REJECTED"),
                member_path=str(getattr(member, "path", "")),
                detail=f"{type(exc).__name__}: {exc}",
            )

    expanded_count = len(expansion.documents) - document_start
    if expanded_count <= 0:
        return fail("ARCHIVE_NO_IMPORTABLE_MEMBERS")
    receipt["expanded_document_count"] = expanded_count
    receipt["total_budget_member_count"] = budget.member_count
    receipt["total_budget_uncompressed_bytes"] = budget.total_uncompressed_bytes
    receipt["status"] = "PARTIAL" if receipt["ignored_member_count"] else "COMPLETE"
    return True


def expand_archive_documents(
    documents: list[dict[str, Any]],
    *,
    limits: ArchiveLimits | None = None,
    registry: ArchiveProviderRegistry | None = None,
) -> ArchiveExpansion:
    """Expand archives atomically per top-level envelope; pass non-archives unchanged."""

    resolved_limits = limits or ArchiveLimits()
    resolved_registry = registry or build_default_archive_provider_registry()
    expansion = ArchiveExpansion()
    for index, raw in enumerate(documents or []):
        if not isinstance(raw, dict):
            expansion.errors.append(
                {
                    "code": "DOCUMENT_ENVELOPE_INVALID",
                    "index": index,
                    "severity": "P0",
                    "blocks_formal_understanding": True,
                }
            )
            continue
        doc = dict(raw)
        path = Path(str(doc.get("file_path"))) if doc.get("file_path") else None
        content = doc.get("content_bytes")
        if isinstance(content, (bytearray, memoryview)):
            content = bytes(content)
        if isinstance(content, bytes):
            data = content
            filename = str(doc.get("filename") or doc.get("name") or "source.archive")
        elif path is not None and path.exists() and path.is_file():
            if path.stat().st_size > resolved_limits.max_archive_bytes:
                data = path.read_bytes()
            else:
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
        local = ArchiveExpansion()
        ok = _expand_one(
            filename=filename,
            data=data,
            limits=resolved_limits,
            registry=resolved_registry,
            expansion=local,
            inherited=doc,
            chain=[],
            depth=1,
            budget=_ExpansionBudget(),
        )
        expansion.receipts.extend(local.receipts)
        expansion.errors.extend(local.errors)
        expansion.ignored_members.extend(local.ignored_members)
        if ok:
            expansion.documents.extend(local.documents)
    return expansion


def _attach_archive_provenance_to_created_sources(
    *,
    project: str,
    root: Path,
    created: list[dict[str, Any]],
    provenance_by_external_ref: dict[str, dict[str, Any]],
) -> None:
    if not created or not provenance_by_external_ref:
        return
    registry = _load_registry(project, root)
    by_id = {
        str(row.get("source_id") or ""): row
        for row in registry.get("sources") or []
        if str(row.get("source_id") or "")
    }
    changed = False
    for returned in created:
        source_id = str(returned.get("source_id") or "")
        record = by_id.get(source_id)
        external_ref = str(
            (record or {}).get("external_ref") or returned.get("external_ref") or ""
        )
        provenance = provenance_by_external_ref.get(external_ref)
        if not provenance:
            continue
        returned["archive_provenance"] = dict(provenance)
        if record is not None:
            record["archive_provenance"] = dict(provenance)
            parse = dict(record.get("parse") or {})
            receipt = dict(parse.get("receipt") or {})
            receipt["archive_provenance"] = dict(provenance)
            parse["receipt"] = receipt
            record["parse"] = parse
            changed = True
    if changed:
        _save_registry(project, root, registry)


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
    """Retain transport artifacts and ingest only members of successfully expanded packages."""

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
    expansion = expand_archive_documents(envelopes, limits=limits, registry=registry)

    transport_dir = _paths(project, resolved_root)["workspace"] / "archive_transport"
    transport_dir.mkdir(parents=True, exist_ok=True)
    stored_artifacts: list[dict[str, Any]] = []
    for artifact in expansion.transport_artifacts:
        target = transport_dir / (
            f"{str(artifact['archive_hash'])[:16]}_{_safe_slug(str(artifact['filename']))}"
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
    provenance_by_external_ref = {
        str(row.get("external_ref") or ""): dict(row.get("archive_provenance") or {})
        for row in expansion.documents
        if str(row.get("external_ref") or "")
    }
    if expansion.documents:
        with tempfile.TemporaryDirectory(prefix="qualibug-archive-members-") as directory:
            temp_root = Path(directory)
            child_envelopes: list[dict[str, Any]] = []
            for index, doc in enumerate(expansion.documents):
                content = doc.get("content_bytes")
                if not isinstance(content, (bytes, bytearray, memoryview)):
                    child_envelopes.append(doc)
                    continue
                filename = str(doc.get("filename") or f"member_{index}.bin")
                suffix = Path(filename).suffix
                target = temp_root / (
                    f"member_{index}_{hashlib.sha256(bytes(content)).hexdigest()[:16]}{suffix}"
                )
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

    created = list(child_result.get("created") or [])
    _attach_archive_provenance_to_created_sources(
        project=project,
        root=resolved_root,
        created=created,
        provenance_by_external_ref=provenance_by_external_ref,
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
            "ignored_member_count": len(expansion.ignored_members),
            "created_source_ids": [row.get("source_id") for row in created],
            "failed_archives_activate_no_members": True,
        }
    )
    _save_registry(project, resolved_root, knowledge_registry)

    errors = [*expansion.errors, *list(child_result.get("errors") or [])]
    warnings = [
        *list(child_result.get("warnings") or []),
        *expansion.ignored_members,
    ]
    return {
        "schema": ARCHIVE_INGESTION_RECEIPT_SCHEMA,
        "ok": not errors and bool(child_result.get("ok", True)),
        "project_id": project,
        "archive_receipts": expansion.receipts,
        "archive_transport_artifacts": stored_artifacts,
        "archive_ignored_members": expansion.ignored_members,
        "expanded_document_count": len(expansion.documents),
        "created": created,
        "duplicates": list(child_result.get("duplicates") or []),
        "errors": errors,
        "warnings": warnings,
        "source_count": int(child_result.get("source_count") or 0),
        "archive_is_transport_not_business_authority": True,
        "members_use_canonical_document_ingestion": True,
        "failed_archives_activate_no_members": True,
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
