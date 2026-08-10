# -*- coding: utf-8 -*-
"""Content-addressed runtime artifact store (SPEC P0-4, Phase 1).

The core architecture shift is *Run references Artifact* instead of *Run owns
files*: a Run holds only a lightweight manifest; every payload lives in a
content-addressed store, so identical bytes are physically stored once and
shared by any number of runs (SPEC §5/§8/§52).

Responsibilities of this module:

- ``ArtifactRef`` — frozen identity record (SPEC §6.1): ``sha256:<hash>`` ids
  guarantee *same content -> same artifact_id*.
- ``ArtifactStore`` — backend interface (SPEC §7/§30). Business modules must
  never ``open(path, "wb")`` a formal runtime artifact; they call ``put`` /
  ``put_file`` here. The interface deliberately leaves room for
  S3/OSS/MinIO backends (``LocalArtifactStore`` is the Phase-1 implementation).
- Canonical JSON serialization for structured artifacts (SPEC §9):
  ``sort_keys`` + compact separators + ``ensure_ascii=False``, so key-order
  differences do not create duplicate artifacts. Semantic bytes (HTTP raw
  bodies, screenshots, DOM captures) are never rewritten — raw bytes pass
  through verbatim.
- Transparent zstd compression (SPEC §10), default ``zstd``,
  ``QUALIBUG_ARTIFACT_COMPRESSION=none`` disables it; ``get`` auto-decompresses.
- Atomic writes (SPEC §11/§39): temp file -> fsync -> ``os.replace``. When two
  workers race on identical content, the loser discards its temp and reuses
  the winner's object — never an error.
- Streaming ``put_file`` for large artifacts (SPEC §44/§45): incremental
  sha256 + incremental compression, never ``huge_file.read()``.
- Sidecar metadata (SPEC §28): artifact_id / artifact_type / content_hash /
  original_size / stored_size / compression / created_at — simple, reliable,
  self-healing (a payload without a sidecar rebuilds its sidecar on read).
- Process-local lifecycle diagnostics (SPEC §37/§38/§44): new/reused counts,
  logical/physical bytes, dedup/compression savings, hash/compression/put
  timings. These are mechanism observables, never quality claims.

Log redaction (SPEC §46): this module never logs content, headers, tokens or
credentials — only ids, types and byte sizes.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

try:
    import zstandard
except Exception:  # pragma: no cover - environment probe
    zstandard = None  # type: ignore[assignment]

_STREAM_CHUNK_BYTES = 1024 * 1024  # 1 MiB streaming chunk
_ARTIFACT_ID_PREFIX = "sha256:"

# Per-root default-store cache: the evidence write path and the run-manifest
# post hook must share one store instance so the per-run lifecycle delta
# (SPEC §37) covers the run's whole artifact activity. Diagnostics only —
# LocalArtifactStore instances created directly are never cached.
_DEFAULT_STORES: dict[str, "LocalArtifactStore"] = {}

# --------------------------------------------------------------------------
# Artifact types (SPEC §13 granularity). Open set — new types are plain
# strings; these constants exist so callers share one vocabulary.
# --------------------------------------------------------------------------
HTTP_REQUEST = "HTTP_REQUEST"
HTTP_RESPONSE = "HTTP_RESPONSE"
SCREENSHOT = "SCREENSHOT"
DOM_SNAPSHOT = "DOM_SNAPSHOT"
LOG_SEGMENT = "LOG_SEGMENT"
DB_SNAPSHOT = "DB_SNAPSHOT"
UI_STATE = "UI_STATE"
EXECUTION_OUTPUT = "EXECUTION_OUTPUT"
HAR_CAPTURE = "HAR_CAPTURE"
METADATA = "METADATA"
EVIDENCE_BUNDLE_MANIFEST = "EVIDENCE_BUNDLE_MANIFEST"
RUN_MANIFEST = "RUN_MANIFEST"
TRACE_LEDGER = "TRACE_LEDGER"
TRACE_EVENT = "TRACE_EVENT"
DELIVERY_PACKAGE = "DELIVERY_PACKAGE"
SCAN_RESULT = "SCAN_RESULT"
INTELLIGENCE_REPORT = "INTELLIGENCE_REPORT"
ERROR_SUMMARY = "ERROR_SUMMARY"
DEBUG_TRACE = "DEBUG_TRACE"


class ArtifactStoreError(RuntimeError):
    """Artifact store contract violation (missing/corrupt/invalid id)."""


@dataclass(frozen=True)
class ArtifactRef:
    """Identity of one content-addressed artifact (SPEC §6.1)."""

    artifact_id: str
    artifact_type: str
    content_hash: str
    size_bytes: int
    compressed_size_bytes: int | None = None
    encoding: str | None = None


@dataclass(frozen=True)
class ArtifactMetadata:
    """Full sidecar metadata (SPEC §28)."""

    artifact_id: str
    artifact_type: str
    content_hash: str
    original_size: int
    stored_size: int
    compression: str
    created_at: str
    encoding: str | None = None


def artifact_id_from_hash(hex_digest: str) -> str:
    """Build the ``sha256:<hash>`` identity for a content digest."""
    return f"{_ARTIFACT_ID_PREFIX}{str(hex_digest or '').lower()}"


def parse_artifact_id(artifact_id: str) -> str:
    """Validate an artifact id and return its hex digest.

    Raises ``ArtifactStoreError`` for anything that is not a well-formed
    ``sha256:<64-hex>`` id (path traversal is structurally impossible).
    """
    text = str(artifact_id or "").strip()
    if not text.startswith(_ARTIFACT_ID_PREFIX):
        raise ArtifactStoreError(f"invalid_artifact_id:{artifact_id!r}")
    digest = text[len(_ARTIFACT_ID_PREFIX):]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ArtifactStoreError(f"invalid_artifact_id:{artifact_id!r}")
    return digest


def canonical_json_bytes(value: Any) -> bytes:
    """Deterministic JSON serialization for structured artifacts (SPEC §9)."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def resolve_compression() -> str:
    """Resolve ``QUALIBUG_ARTIFACT_COMPRESSION`` (default ``zstd``, SPEC §10/§31)."""
    raw = str(os.getenv("QUALIBUG_ARTIFACT_COMPRESSION", "") or "").strip().lower()
    if not raw:
        return "zstd"
    if raw not in ("zstd", "none"):
        raise ArtifactStoreError(
            f"QUALIBUG_ARTIFACT_COMPRESSION must be 'zstd' or 'none', got {raw!r}"
        )
    return raw


def artifact_store_enabled() -> bool:
    """Resolve ``QUALIBUG_ARTIFACT_STORE_ENABLED`` (default true, SPEC §31)."""
    raw = str(os.getenv("QUALIBUG_ARTIFACT_STORE_ENABLED", "") or "").strip().lower()
    if not raw:
        return True
    return raw not in ("0", "false", "no", "off", "disabled")


def resolve_artifact_store_root(workspace: Path | str) -> Path:
    """Store root: ``QUALIBUG_ARTIFACT_ROOT`` override or ``<workspace>/.qualibug``."""
    override = str(os.getenv("QUALIBUG_ARTIFACT_ROOT", "") or "").strip()
    if override:
        return Path(override)
    return Path(workspace) / ".qualibug"


def default_artifact_store(workspace: Path | str) -> "LocalArtifactStore":
    """Convenience factory honoring the SPEC §31 environment contract.

    Per-root instances are cached so the evidence write path and the
    run-manifest post hook share one store (per-run lifecycle deltas then
    cover the whole run). Explicitly-constructed ``LocalArtifactStore``
    instances are never cached.
    """
    root = resolve_artifact_store_root(workspace)
    key = os.path.normcase(str(root))
    cached = _DEFAULT_STORES.get(key)
    if cached is not None:
        return cached
    store = LocalArtifactStore(root, compression=resolve_compression())
    _DEFAULT_STORES[key] = store
    return store


class ArtifactStore(ABC):
    """Backend-neutral artifact store contract (SPEC §7/§30).

    Business modules depend on this interface only; the local filesystem
    implementation may later be swapped for S3/OSS/MinIO without touching
    call sites.
    """

    @abstractmethod
    def put(
        self,
        content: bytes | dict[str, Any] | list[Any] | str,
        artifact_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Store content and return its content-addressed ref.

        ``dict``/``list`` content is canonical-JSON serialized before hashing
        (SPEC §9); ``bytes``/``str`` content is stored verbatim — semantic
        payloads (HTTP raw bodies, screenshots, DOM) are never rewritten.
        Identical content returns the same ``artifact_id`` and physically
        stores once (SPEC §8/§39).
        """

    @abstractmethod
    def put_file(
        self,
        path: Path | str,
        artifact_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Store a file with streaming hash + streaming compression (SPEC §45)."""

    @abstractmethod
    def get(self, artifact_id: str) -> bytes:
        """Return the artifact payload, transparently decompressed (SPEC §10)."""

    @abstractmethod
    def exists(self, artifact_id: str) -> bool:
        """True when the artifact payload is physically present."""

    @abstractmethod
    def stat(self, artifact_id: str) -> ArtifactRef:
        """Return the artifact identity record (SPEC §7)."""

    @abstractmethod
    def delete(self, artifact_id: str) -> None:
        """Remove the artifact (payload + metadata). Idempotent."""

    @abstractmethod
    def list_all(self) -> list[str]:
        """Enumerate every stored artifact id (used by reference GC)."""

    @abstractmethod
    def metadata(self, artifact_id: str) -> ArtifactMetadata:
        """Full sidecar metadata (created_at, compression, sizes)."""

    @abstractmethod
    def open(self, artifact_id: str) -> Any:
        """Context manager streaming the decompressed payload."""

    @abstractmethod
    def resolve(self, artifact_id: str) -> Path:
        """Resolve an artifact id to its local storage path (SPEC §29).

        Business objects must persist ``artifact_id`` — never machine paths —
        and resolve at runtime through the store.
        """

    @abstractmethod
    def snapshot_stats(self) -> dict[str, float]:
        """Process-local lifecycle diagnostics (SPEC §37/§38)."""


class LocalArtifactStore(ArtifactStore):
    """Filesystem content-addressed store.

    Layout (SPEC §5)::

        <root>/artifacts/sha256/<ab>/<hash>.<zst|bin>   # payload
        <root>/artifacts/sha256/<ab>/<hash>.json        # sidecar metadata
    """

    def __init__(
        self,
        root: Path | str,
        *,
        compression: str | None = None,
    ) -> None:
        self._root = Path(root)
        compression = (compression or resolve_compression()).strip().lower()
        if compression not in ("zstd", "none"):
            raise ArtifactStoreError(
                f"unsupported compression {compression!r} (zstd|none)"
            )
        if compression == "zstd" and zstandard is None:
            raise ArtifactStoreError(
                "zstd compression requested but the 'zstandard' package is not "
                "installed; set QUALIBUG_ARTIFACT_COMPRESSION=none or install "
                "zstandard"
            )
        self._compression = compression
        self._stats_lock = threading.Lock()
        self._stats: dict[str, float] = {
            "artifact_new_count": 0.0,
            "artifact_reused_count": 0.0,
            "artifact_logical_bytes": 0.0,
            "artifact_physical_bytes": 0.0,
            "artifact_dedup_saved_bytes": 0.0,
            "artifact_compression_saved_bytes": 0.0,
            "artifact_hash_time_ms": 0.0,
            "artifact_compression_time_ms": 0.0,
            "artifact_put_time_ms": 0.0,
        }
        # NOTE: zstd compressor/decompressor instances are created per call
        # (concurrent native compress() on a shared instance crashes with an
        # access violation on Windows). No shared zstd state is kept here.

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #
    @property
    def root(self) -> Path:
        return self._root

    @property
    def compression(self) -> str:
        return self._compression

    def put(
        self,
        content: bytes | dict[str, Any] | list[Any] | str,
        artifact_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        if isinstance(content, (dict, list)):
            raw = canonical_json_bytes(content)
            encoding = "utf-8"
        elif isinstance(content, bytes):
            raw = content
            encoding = None
        elif isinstance(content, str):
            raw = content.encode("utf-8")
            encoding = "utf-8"
        else:
            raise ArtifactStoreError(
                f"unsupported content type {type(content).__name__}"
            )
        return self._put_raw(raw, artifact_type, encoding=encoding)

    def put_json(
        self,
        obj: dict[str, Any] | list[Any],
        artifact_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Store a structured object with canonical JSON serialization."""
        return self.put(obj, artifact_type, metadata=metadata)

    def put_file(
        self,
        path: Path | str,
        artifact_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Streaming store of a large file (SPEC §44/§45).

        Two bounded passes over the source — hash-only, then
        hash+compress+write — each reading 1 MiB chunks; the whole file is
        never loaded into memory.
        """
        source = Path(path)
        if not source.is_file():
            raise ArtifactStoreError(f"put_file_source_missing:{source}")
        started = time.perf_counter()
        digest = _stream_sha256(source)
        target_dir = self._payload_dir(digest)
        target_dir.mkdir(parents=True, exist_ok=True)
        payload_path = target_dir / f"{digest}{self._suffix()}"
        sidecar_path = target_dir / f"{digest}.json"
        if payload_path.is_file() and sidecar_path.is_file():
            self._note_reuse(source.stat().st_size)
            return self.stat(artifact_id_from_hash(digest))

        compress_started = time.perf_counter()
        temporary = target_dir / f".{digest}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
        written = 0
        try:
            compressor = self._new_compressor()
            with temporary.open("wb") as out:
                with source.open("rb") as handle:
                    while True:
                        chunk = handle.read(_STREAM_CHUNK_BYTES)
                        if not chunk:
                            break
                        out.write(compressor.compress(chunk))
                out.write(compressor.flush())
                out.flush()
                os.fsync(out.fileno())
            written = temporary.stat().st_size
            won_payload = _atomic_replace(temporary, payload_path)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass
        compress_ms = (time.perf_counter() - compress_started) * 1000.0
        original_size = source.stat().st_size
        created = False
        if won_payload:
            if not sidecar_path.is_file():
                self._write_sidecar(
                    digest,
                    artifact_type,
                    encoding=None,
                    original_size=original_size,
                    stored_size=written,
                )
                created = True
        else:
            if not sidecar_path.is_file():
                try:
                    self._write_sidecar(
                        digest,
                        artifact_type,
                        encoding=None,
                        original_size=original_size,
                        stored_size=written,
                    )
                except OSError:
                    pass
        if created:
            self._note_new(
                original=original_size,
                stored=written,
                hash_ms=0.0,
                compress_ms=compress_ms,
                put_ms=(time.perf_counter() - started) * 1000.0,
            )
        else:
            self._note_reuse(original_size)
        return self.stat(artifact_id_from_hash(digest))

    def get(self, artifact_id: str) -> bytes:
        digest = parse_artifact_id(artifact_id)
        payload_path = self._payload_path(digest)
        if not payload_path.is_file():
            raise ArtifactStoreError(f"artifact_missing:{artifact_id}")
        if payload_path.suffix == ".zst":
            # Fresh decompressor per call (per-call context; the shared
            # instance is not safe under concurrent native calls).
            decompressor = zstandard.ZstdDecompressor().decompressobj()
            chunks: list[bytes] = []
            with _open_read_retry(payload_path) as handle:
                while True:
                    block = handle.read(_STREAM_CHUNK_BYTES)
                    if not block:
                        break
                    chunks.append(decompressor.decompress(block))
            chunks.append(decompressor.flush())
            return b"".join(chunks)
        return _read_bytes_retry(payload_path)

    def get_json(self, artifact_id: str) -> Any:
        """Decode a canonical-JSON artifact (raises on invalid JSON)."""
        raw = self.get(artifact_id)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactStoreError(f"artifact_json_invalid:{artifact_id}") from exc

    def exists(self, artifact_id: str) -> bool:
        try:
            digest = parse_artifact_id(artifact_id)
        except ArtifactStoreError:
            return False
        return self._payload_path(digest).is_file()

    def stat(self, artifact_id: str) -> ArtifactRef:
        digest = parse_artifact_id(artifact_id)
        meta = self._sidecar_or_heal(digest)
        return ArtifactRef(
            artifact_id=artifact_id_from_hash(digest),
            artifact_type=meta.artifact_type,
            content_hash=meta.content_hash,
            size_bytes=meta.original_size,
            compressed_size_bytes=meta.stored_size,
            encoding=meta.encoding,
        )

    def metadata(self, artifact_id: str) -> ArtifactMetadata:
        return self._sidecar_or_heal(parse_artifact_id(artifact_id))

    def delete(self, artifact_id: str) -> None:
        digest = parse_artifact_id(artifact_id)
        for path in (self._payload_path(digest), self._sidecar_path(digest)):
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                pass

    def list_all(self) -> list[str]:
        digest_dir = self._root / "artifacts" / "sha256"
        ids: list[str] = []
        if not digest_dir.is_dir():
            return ids
        for prefix_dir in sorted(digest_dir.iterdir()):
            if not prefix_dir.is_dir() or len(prefix_dir.name) != 2:
                continue
            for child in sorted(prefix_dir.iterdir()):
                if child.is_file() and child.suffix in (".zst", ".bin"):
                    ids.append(artifact_id_from_hash(child.stem))
        return ids

    def open(self, artifact_id: str) -> Any:
        """Context manager streaming the decompressed payload."""
        digest = parse_artifact_id(artifact_id)
        payload_path = self._payload_path(digest)
        if not payload_path.is_file():
            raise ArtifactStoreError(f"artifact_missing:{artifact_id}")
        if payload_path.suffix == ".zst":
            return zstandard.ZstdDecompressor().stream_reader(
                payload_path.open("rb")
            )
        return payload_path.open("rb")

    def resolve(self, artifact_id: str) -> Path:
        digest = parse_artifact_id(artifact_id)
        payload_path = self._payload_path(digest)
        if not payload_path.is_file():
            raise ArtifactStoreError(f"artifact_missing:{artifact_id}")
        return payload_path

    def snapshot_stats(self) -> dict[str, float]:
        with self._stats_lock:
            return dict(self._stats)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _put_raw(
        self,
        raw: bytes,
        artifact_type: str,
        *,
        encoding: str | None,
    ) -> ArtifactRef:
        started = time.perf_counter()
        hash_started = time.perf_counter()
        digest = hashlib.sha256(raw).hexdigest()
        hash_ms = (time.perf_counter() - hash_started) * 1000.0
        target_dir = self._payload_dir(digest)
        target_dir.mkdir(parents=True, exist_ok=True)
        payload_path = target_dir / f"{digest}{self._suffix()}"
        sidecar_path = target_dir / f"{digest}.json"
        if payload_path.is_file() and sidecar_path.is_file():
            self._note_reuse(len(raw))
            return self.stat(artifact_id_from_hash(digest))

        compress_started = time.perf_counter()
        if self._compression == "zstd":
            # A fresh compressor per call: the zstd context is not safe for
            # concurrent compress() from multiple workers (native access
            # violation observed on Windows); construction is cheap.
            stored = zstandard.ZstdCompressor().compress(raw)
        else:
            stored = raw
        compress_ms = (time.perf_counter() - compress_started) * 1000.0

        temporary = target_dir / f".{digest}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
        with temporary.open("wb") as out:
            out.write(stored)
            out.flush()
            os.fsync(out.fileno())
        won_payload = _atomic_replace(temporary, payload_path)
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
        created = False
        if won_payload:
            if not sidecar_path.is_file():
                self._write_sidecar(
                    digest,
                    artifact_type,
                    encoding=encoding,
                    original_size=len(raw),
                    stored_size=len(stored),
                )
                created = True
        else:
            # A concurrent worker created the identical object — reuse it
            # (SPEC §39: never an error); heal a missing sidecar if needed.
            if not sidecar_path.is_file():
                try:
                    self._write_sidecar(
                        digest,
                        artifact_type,
                        encoding=encoding,
                        original_size=len(raw),
                        stored_size=len(stored),
                    )
                except OSError:
                    pass
        if created:
            self._note_new(
                original=len(raw),
                stored=len(stored),
                hash_ms=hash_ms,
                compress_ms=compress_ms,
                put_ms=(time.perf_counter() - started) * 1000.0,
            )
        else:
            # A concurrent worker created the identical object — reuse it.
            self._note_reuse(len(raw))
        return self.stat(artifact_id_from_hash(digest))

    def _new_compressor(self) -> Any:
        if self._compression == "zstd":
            return zstandard.ZstdCompressor().compressobj()
        return _IdentityCompressor()

    def _suffix(self) -> str:
        return ".zst" if self._compression == "zstd" else ".bin"

    def _payload_dir(self, digest: str) -> Path:
        return self._root / "artifacts" / "sha256" / digest[:2]

    def _payload_path(self, digest: str) -> Path:
        return self._payload_dir(digest) / f"{digest}{self._suffix()}"

    def _sidecar_path(self, digest: str) -> Path:
        return self._payload_dir(digest) / f"{digest}.json"

    def _write_sidecar(
        self,
        digest: str,
        artifact_type: str,
        *,
        encoding: str | None,
        original_size: int,
        stored_size: int,
    ) -> None:
        payload = {
            "schema_version": "qualibug.artifact-sidecar.v1",
            "artifact_id": artifact_id_from_hash(digest),
            "artifact_type": str(artifact_type or "UNKNOWN"),
            "content_hash": digest,
            "original_size": int(original_size),
            "stored_size": int(stored_size),
            "compression": self._compression,
            "encoding": encoding,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        sidecar_path = self._sidecar_path(digest)
        temporary = sidecar_path.with_name(
            f".{sidecar_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
        )
        with temporary.open("wb") as out:
            out.write(canonical_json_bytes(payload))
            out.flush()
            os.fsync(out.fileno())
        _atomic_replace(temporary, sidecar_path)
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass

    def _sidecar_or_heal(self, digest: str) -> ArtifactMetadata:
        """Read sidecar metadata, rebuilding it when the payload exists but the
        sidecar was lost (crash between the two atomic renames)."""
        sidecar_path = self._sidecar_path(digest)
        payload_path = self._payload_path(digest)
        if sidecar_path.is_file():
            try:
                raw_text = _read_bytes_retry(sidecar_path).decode("utf-8")
                raw = json.loads(raw_text or "null")
            except (OSError, json.JSONDecodeError) as exc:
                raise ArtifactStoreError(
                    f"artifact_sidecar_corrupt:{artifact_id_from_hash(digest)}"
                ) from exc
            if not isinstance(raw, dict):
                raise ArtifactStoreError(
                    f"artifact_sidecar_invalid:{artifact_id_from_hash(digest)}"
                )
            return ArtifactMetadata(
                artifact_id=str(
                    raw.get("artifact_id") or artifact_id_from_hash(digest)
                ),
                artifact_type=str(raw.get("artifact_type") or "UNKNOWN"),
                content_hash=str(raw.get("content_hash") or digest),
                original_size=int(raw.get("original_size") or 0),
                stored_size=int(raw.get("stored_size") or 0),
                compression=str(raw.get("compression") or "none"),
                created_at=str(raw.get("created_at") or ""),
                encoding=raw.get("encoding"),
            )
        if not payload_path.is_file():
            raise ArtifactStoreError(
                f"artifact_missing:{artifact_id_from_hash(digest)}"
            )
        # Self-heal: rebuild the sidecar from the physical payload.
        stored_size = payload_path.stat().st_size
        compression = "zstd" if payload_path.suffix == ".zst" else "none"
        original_size = stored_size
        if compression == "zstd" and zstandard is not None:
            try:
                original_size = len(self.get(artifact_id_from_hash(digest)))
            except Exception:
                original_size = stored_size
        self._write_sidecar(
            digest,
            "UNKNOWN",
            encoding=None,
            original_size=original_size,
            stored_size=stored_size,
        )
        return self._sidecar_or_heal(digest)

    def _note_new(
        self,
        *,
        original: int,
        stored: int,
        hash_ms: float,
        compress_ms: float,
        put_ms: float,
    ) -> None:
        with self._stats_lock:
            self._stats["artifact_new_count"] += 1.0
            self._stats["artifact_logical_bytes"] += float(original)
            self._stats["artifact_physical_bytes"] += float(stored)
            self._stats["artifact_compression_saved_bytes"] += float(
                max(0, original - stored)
            )
            self._stats["artifact_hash_time_ms"] += float(hash_ms)
            self._stats["artifact_compression_time_ms"] += float(compress_ms)
            self._stats["artifact_put_time_ms"] += float(put_ms)

    def _note_reuse(self, logical_bytes: int) -> None:
        with self._stats_lock:
            self._stats["artifact_reused_count"] += 1.0
            self._stats["artifact_dedup_saved_bytes"] += float(logical_bytes)


class _IdentityCompressor:
    """Pass-through compressor used when compression is disabled."""

    def compress(self, data: bytes) -> bytes:
        return data

    def flush(self) -> bytes:
        return b""


def _atomic_replace(temporary: Path, target: Path) -> bool:
    """Atomically move ``temporary`` onto ``target`` (SPEC §11/§39).

    Concurrent identical puts race the same target; on Windows a transient
    sharing violation can surface as PermissionError. When the target already
    exists the temp is discarded and ``False`` is returned — the caller then
    reuses the winner's object instead of erroring (SPEC §39).
    """
    for _attempt in range(100):
        try:
            os.replace(temporary, target)
            return True
        except PermissionError:
            if target.is_file():
                return False
            time.sleep(0.005)
    try:
        os.replace(temporary, target)
        return True
    except OSError:
        return target.is_file()


def _open_read_retry(path: Path) -> Any:
    """Open a file for reading, retrying transient Windows sharing violations
    (a concurrent writer may be mid-``os.replace``)."""
    for _attempt in range(100):
        try:
            return path.open("rb")
        except PermissionError:
            time.sleep(0.005)
    return path.open("rb")


def _read_bytes_retry(path: Path) -> bytes:
    """Read a whole file, retrying transient Windows sharing violations."""
    for _attempt in range(100):
        try:
            return path.read_bytes()
        except PermissionError:
            time.sleep(0.005)
    return path.read_bytes()


def snapshot_lifecycle_delta(
    before: dict[str, float], after: dict[str, float]
) -> dict[str, Any]:
    """Per-run lifecycle summary (SPEC §37/§38) over a stats delta.

    Keys: new/reused counts, logical/physical bytes, dedup/compression
    savings, put time and the dedup ratio ``1 - physical/logical``.
    """
    keys = (
        "artifact_new_count",
        "artifact_reused_count",
        "artifact_logical_bytes",
        "artifact_physical_bytes",
        "artifact_dedup_saved_bytes",
        "artifact_compression_saved_bytes",
        "artifact_hash_time_ms",
        "artifact_compression_time_ms",
        "artifact_put_time_ms",
    )
    delta = {
        key: round(float(after.get(key, 0.0)) - float(before.get(key, 0.0)), 3)
        for key in keys
    }
    logical = delta["artifact_logical_bytes"]
    physical = delta["artifact_physical_bytes"]
    delta["artifact_dedup_ratio"] = (
        round(1.0 - physical / logical, 4) if logical > 0 else 0.0
    )
    return delta


def merge_lifecycle_deltas(*deltas: dict[str, Any] | None) -> dict[str, Any]:
    """Sum numeric lifecycle deltas (e.g. evidence persist + post hook)."""
    merged: dict[str, float] = {}
    for delta in deltas:
        if not isinstance(delta, dict):
            continue
        for key, value in delta.items():
            if key == "artifact_dedup_ratio" or not isinstance(value, (int, float)):
                continue
            merged[key] = merged.get(key, 0.0) + float(value)
    if merged.get("artifact_logical_bytes", 0.0) > 0:
        merged["artifact_dedup_ratio"] = round(
            1.0
            - merged.get("artifact_physical_bytes", 0.0)
            / merged["artifact_logical_bytes"],
            4,
        )
    return merged


def _stream_sha256(source: Path) -> str:
    """Single bounded-pass sha256 over a file (never loads it fully)."""
    hasher = hashlib.sha256()
    with source.open("rb") as handle:
        while True:
            chunk = handle.read(_STREAM_CHUNK_BYTES)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()
