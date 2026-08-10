"""Scan-result object-tree normalization: content-addressed dedup at rest.

P0-3 root cause — why a full v12 scan_result reaches ~4GB. The runtime result
tree embeds the same receipt objects over and over: an Experiment appears in
``v12.experiments.by_obligation`` AND in ``experiments`` / ``all_experiments``
AND in ``experiment_compile.*``; an Execution Result embeds the full
finding + execution receipt; the obligation-attempt ledger embeds the full
gate receipt / delivery evidence bundle per attempt; ``delivery_occurrences``
and ``findings`` embed the same findings again.  Measured on run16
(4,015,228,861 B): ``v12`` alone was 3.6GB, of which experiments 48.6%,
experiment_execution 19.4%, experiment_compile 18.4%.

This module rewrites the in-memory tree **in place** so that every subtree
larger than a floor lives in exactly ONE place — a top-level artifact registry
(``_artifact_registry``) organized as content-addressed by-id maps — and every
occurrence in the tree becomes a tiny ref marker. Large response bodies /
evidence strings are moved into a sha256-addressed blob store. Hydration
(``hydrate_refs``) restores the exact same object tree at load time, so the
storage form is the only thing that changes: identity keys (``finding_id``,
``canonical_defect_id``, …) and all consumer-visible content are untouched.

Generic rules (no benchmark data, no business terms):
  * any dict/list whose estimated serialized size >= ``dedup_threshold_bytes``
    is moved to the registry keyed by content hash (or by its own id field,
    e.g. ``finding_id`` -> ``findings_by_id``);
  * any string >= ``blob_threshold_bytes`` is moved to the sha256-addressed
    blob store;
  * sealed obligation-attempt ledgers are excluded from interior dedup: the
    ledger fingerprints (``ledger_fingerprint`` / per-attempt
    ``attempt_fingerprint``) are computed over the persisted content, so
    refs inside a ledger would break fail-closed validation on reload.
    The ledger itself is still stored as one atomic unit (see the store).

Ref marker shapes (the loader resolves them):
  * ``{"$qualibug_artifact_ref": "<map>:<id>"}`` — registry entry
  * ``{"$qualibug_blob_ref": "sha256:<hex>"}`` — blob store string

The registry section is attached by the store writer as ``_artifact_registry``
and removed again by the loader after hydration, so a fully loaded scan result
is the same tree the runtime produced.
"""
from __future__ import annotations

import base64
import hashlib
from typing import Any, Callable

# Registry section key in the scan_result index (never a product key).
REGISTRY_KEY = "_artifact_registry"
# Ref markers. A captured business payload containing these exact single-key
# dicts is not a supported input; the loader fails loudly on unresolvable refs.
REF_KEY = "$qualibug_artifact_ref"
BLOB_KEY = "$qualibug_blob_ref"

NORMALIZED_SCHEMA = "qualibug.scan-result-normalized.v1"

# Sealed ledger schema: interior is excluded from dedup (fingerprint contract).
_LEDGER_SCHEMA = "qualibug.obligation-attempt-ledger.v1"

# id-field -> registry map name. Product-schema identity fields only; the
# first matching field wins (most specific first).
_ID_FIELD_MAPS: tuple[tuple[str, str], ...] = (
    ("finding_id", "findings_by_id"),
    ("evidence_id", "evidence_by_id"),
    ("execution_id", "executions_by_id"),
    ("experiment_id", "experiments_by_id"),
    ("gate_receipt_id", "gate_receipts_by_id"),
    ("oracle_receipt_id", "oracle_receipts_by_id"),
    ("operation_id", "operations_by_id"),
    ("obligation_id", "obligations_by_id"),
    ("candidate_id", "candidates_by_id"),
    ("receipt_id", "receipts_by_id"),
    ("attempt_id", "attempts_by_id"),
)

# Defaults: 16 KiB container floor (measured on run16: the duplicated units —
# experiment receipts, execution evidence, gate bundles — are ~18 KiB each;
# a 64 KiB floor left three byte-identical 277MB experiment lists fully
# duplicated; 16 KiB collapses them), 256 KiB string blob floor (aligned with
# the redactor's own 256 KiB string truncation cap).
DEFAULT_DEDUP_THRESHOLD_BYTES = 16 * 1024
DEFAULT_BLOB_THRESHOLD_BYTES = 256 * 1024

# dict/list structural overhead estimate used for the floor filter only.
_DICT_OVERHEAD = 64
_LIST_OVERHEAD = 32

_HASH_PREFIX_LEN = 32  # content-hash keys are truncated for readability


def _sha256_hex(payload: bytes, *, truncate: int = 64) -> str:
    return hashlib.sha256(payload).hexdigest()[:truncate]


def encode_id(identifier: str) -> str:
    """Encode a semantic id into a filesystem/dotted-path-safe token."""
    return base64.urlsafe_b64encode(identifier.encode("utf-8")).decode("ascii")


def decode_id(token: str) -> str:
    try:
        return base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - generic identity fallback
        raise ValueError(f"invalid artifact registry id token: {token!r}") from exc


def ref_key(map_name: str, identifier: str) -> str:
    """Registry entry reference key (map:encoded-id)."""
    return f"{map_name}:{encode_id(identifier)}"


def ref_to_dotted(ref: str) -> str:
    """Registry entry reference key -> index dotted path (for shard loading)."""
    map_name, _, token = ref.partition(":")
    if map_name == "content":
        # content-hash fallback keys live in the content_by_hash map.
        map_name = "content_by_hash"
    return f"{REGISTRY_KEY}.entries.{map_name}.{token}"


def blob_to_dotted(ref: str) -> str:
    """Blob reference key (sha256:<hex>) -> index dotted path."""
    _prefix, _, hexdigest = ref.partition(":")
    return f"{REGISTRY_KEY}.blobs.{hexdigest}"


def _resolve_dotted(ref: str) -> str:
    if ref.startswith("sha256:"):
        return blob_to_dotted(ref)
    return ref_to_dotted(ref)


class ArtifactRegistry:
    """In-place content-addressed registry shared by the normalize walk."""

    def __init__(
        self,
        *,
        dedup_threshold_bytes: int = DEFAULT_DEDUP_THRESHOLD_BYTES,
        blob_threshold_bytes: int = DEFAULT_BLOB_THRESHOLD_BYTES,
    ) -> None:
        self.dedup_threshold_bytes = max(1, int(dedup_threshold_bytes))
        self.blob_threshold_bytes = max(1, int(blob_threshold_bytes))
        self.entries: dict[str, dict[str, Any]] = {
            name: {} for _, name in _ID_FIELD_MAPS
        }
        self.entries["content_by_hash"] = {}
        self.blobs: dict[str, str] = {}
        # content hash -> key of the FIRST registered copy
        self._hash_to_key: dict[str, str] = {}
        # key -> content hash (semantic-key collision detection)
        self._key_to_hash: dict[str, str] = {}
        self.stats: dict[str, int] = {
            "registered": 0,
            "duplicates": 0,
            "saved_bytes_estimate": 0,
            "blobs": 0,
            "blob_bytes": 0,
        }

    def is_empty(self) -> bool:
        return not self.entries.get("content_by_hash") and not any(
            self.entries[name] for _, name in _ID_FIELD_MAPS
        ) and not self.blobs

    def register(self, node: Any, content_hash: str, size: int) -> str:
        existing = self._hash_to_key.get(content_hash)
        if existing is not None:
            self.stats["duplicates"] += 1
            self.stats["saved_bytes_estimate"] += size
            return existing
        key = self._semantic_key(node, content_hash)
        self._hash_to_key[content_hash] = key
        self._key_to_hash[key] = content_hash
        if key.startswith("content:"):
            map_name, identifier = "content_by_hash", key.split(":", 1)[1]
        else:
            map_name, _, identifier = key.partition(":")
        self.entries[map_name][identifier] = node
        self.stats["registered"] += 1
        return key

    def _semantic_key(self, node: Any, content_hash: str) -> str:
        if isinstance(node, dict):
            for field, map_name in _ID_FIELD_MAPS:
                id_value = node.get(field)
                if isinstance(id_value, str) and id_value:
                    candidate = ref_key(map_name, id_value)
                    # Same id but different content -> not a safe key (fallback).
                    if self._key_to_hash.get(candidate, content_hash) == content_hash:
                        return candidate
        return f"content:{content_hash[: _HASH_PREFIX_LEN]}"

    def register_blob(self, text: str) -> str:
        digest = _sha256_hex(text.encode("utf-8"))
        if digest not in self.blobs:
            self.blobs[digest] = text
            self.stats["blobs"] += 1
            self.stats["blob_bytes"] += len(text)
        return f"sha256:{digest}"

    def as_payload(self) -> dict[str, Any]:
        return {
            "schema_version": NORMALIZED_SCHEMA,
            "entries": self.entries,
            "blobs": self.blobs,
        }

    def resolver(self) -> Callable[[str], Any]:
        """In-memory ref resolver: ref key -> registry entry / blob string.

        For on-disk stores the store's loader builds a shard-aware resolver
        instead; this one is for in-memory hydration (tests / tooling).
        """

        def _resolve(key: str) -> Any:
            if key.startswith("sha256:"):
                return self.blobs.get(key.split(":", 1)[1])
            map_name, _, identifier = key.partition(":")
            if map_name == "content":
                map_name = "content_by_hash"
            return self.entries.get(map_name, {}).get(identifier)

        return _resolve


def _estimate_size(value: Any) -> int:
    """Light serialized-size estimate (floor filter only, never persisted)."""
    if isinstance(value, dict):
        total = _DICT_OVERHEAD
        for key, child in value.items():
            total += len(str(key)) + 2 + _estimate_size(child)
        return total
    if isinstance(value, list):
        total = _LIST_OVERHEAD
        for child in value:
            total += _estimate_size(child)
        return total
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if value is None:
        return 4
    return len(repr(value))


def _scalar_hash(value: Any) -> str:
    if value is None:
        return "v:null"
    if isinstance(value, bool):
        return "v:true" if value else "v:false"
    if isinstance(value, str):
        return "s" + _sha256_hex(value.encode("utf-8"), truncate=40)
    if isinstance(value, (bytes, bytearray)):
        return "b" + _sha256_hex(bytes(value), truncate=40)
    return "v:" + repr(value)


def _normalize_walk(
    node: Any,
    registry: ArtifactRegistry,
    *,
    is_root: bool = False,
) -> tuple[Any, int, str]:
    """In-place bottom-up walk. Returns (replacement, size, content-hash).

    Child hashes come from the child's own walk (never recomputed), so the
    whole pass is O(total content), not O(n^2). ``is_root`` keeps the
    top-level dict in the tree (the sharded index needs a dict root; the root
    is never registered as a value).
    """
    if isinstance(node, dict):
        if node.get("schema_version") == _LEDGER_SCHEMA:
            # Sealed ledger: interior excluded from dedup (fingerprint
            # contract). The ledger's own fingerprint is content-stable, so it
            # is used as this node's content hash.
            return (
                node,
                _estimate_size(node),
                "ledger:" + str(node.get("ledger_fingerprint") or "unsealed"),
            )
        size = _DICT_OVERHEAD
        digest = hashlib.sha256()
        for key, child in list(node.items()):
            replacement, child_size, child_hash = _normalize_walk(child, registry)
            node[key] = replacement
            size += len(str(key)) + 2 + child_size
            digest.update(str(key).encode("utf-8"))
            digest.update(b":")
            digest.update(child_hash.encode("ascii"))
            digest.update(b"|")
        content_hash = digest.hexdigest()
        if not is_root and size >= registry.dedup_threshold_bytes:
            key = registry.register(node, content_hash, size)
            return {REF_KEY: key}, 48, "ref:" + key
        return node, size, content_hash
    if isinstance(node, list):
        size = _LIST_OVERHEAD
        digest = hashlib.sha256()
        for index, child in enumerate(node):
            replacement, child_size, child_hash = _normalize_walk(child, registry)
            node[index] = replacement
            size += child_size
            digest.update(child_hash.encode("ascii"))
            digest.update(b"|")
        content_hash = digest.hexdigest()
        if not is_root and size >= registry.dedup_threshold_bytes:
            key = registry.register(node, content_hash, size)
            return {REF_KEY: key}, 48, "ref:" + key
        return node, size, content_hash
    if isinstance(node, str):
        if len(node) >= registry.blob_threshold_bytes:
            key = registry.register_blob(node)
            return {BLOB_KEY: key}, 48, "blob:" + key
        return node, len(node), _scalar_hash(node)
    if isinstance(node, (bytes, bytearray)):
        text = bytes(node)
        if len(text) >= registry.blob_threshold_bytes:
            key = registry.register_blob(text.decode("utf-8", errors="replace"))
            return {BLOB_KEY: key}, 48, "blob:" + key
        return node, len(text), _scalar_hash(node)
    return node, _estimate_size(node), _scalar_hash(node)


def normalize_scan_result(
    result: dict[str, Any],
    registry: ArtifactRegistry | None = None,
    *,
    dedup_threshold_bytes: int = DEFAULT_DEDUP_THRESHOLD_BYTES,
    blob_threshold_bytes: int = DEFAULT_BLOB_THRESHOLD_BYTES,
) -> ArtifactRegistry:
    """Normalize the tree in place; returns the populated registry.

    The caller attaches ``registry.as_payload()`` under :data:`REGISTRY_KEY`
    when it is non-empty. Every subtree >= the dedup floor is replaced with a
    ref marker (the FIRST copy moves into the registry; duplicates are pure
    refs), so the surviving in-memory tree holds each unique subtree exactly
    once.
    """
    reg = registry or ArtifactRegistry(
        dedup_threshold_bytes=dedup_threshold_bytes,
        blob_threshold_bytes=blob_threshold_bytes,
    )
    replacement, _size, _hash = _normalize_walk(result, reg, is_root=True)
    if replacement is not result:
        raise ValueError("normalize_scan_result root must remain the result dict")
    return reg


def is_ref_marker(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and len(value) == 1
        and isinstance(value.get(REF_KEY), str)
    )


def is_blob_marker(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and len(value) == 1
        and isinstance(value.get(BLOB_KEY), str)
    )


def hydrate_refs(
    value: Any,
    resolve: Callable[[str], Any],
    *,
    skip_registry: bool = True,
    _seen: set[int] | None = None,
) -> Any:
    """Replace ref markers with resolved registry content, in place.

    ``resolve`` maps a ref key (``<map>:<id>`` / ``sha256:<hex>``) to the
    loaded registry entry; it may return a new object (loaded from a shard)
    or an already-hydrated one. The walk recurses into resolved entries so
    nested refs (e.g. a finding referencing its evidence) are hydrated too.
    The ``_artifact_registry`` section itself is skipped: it is the source of
    the entries, never a target of hydration.
    """
    if _seen is None:
        _seen = set()
    if isinstance(value, dict):
        if is_ref_marker(value):
            target = resolve(value[REF_KEY])
            if target is None:
                raise ValueError(
                    f"scan_result artifact ref unresolvable: {value[REF_KEY]}"
                )
            return hydrate_refs(
                target, resolve, skip_registry=skip_registry, _seen=_seen
            )
        if is_blob_marker(value):
            target = resolve(value[BLOB_KEY])
            if target is None:
                raise ValueError(
                    f"scan_result blob ref unresolvable: {value[BLOB_KEY]}"
                )
            return target
        marker = id(value)
        if marker in _seen:
            return value
        _seen.add(marker)
        try:
            for key, child in list(value.items()):
                if skip_registry and key == REGISTRY_KEY:
                    continue
                value[key] = hydrate_refs(
                    child, resolve, skip_registry=skip_registry, _seen=_seen
                )
        finally:
            _seen.discard(marker)
        return value
    if isinstance(value, list):
        marker = id(value)
        if marker in _seen:
            return value
        _seen.add(marker)
        try:
            for index, child in enumerate(value):
                value[index] = hydrate_refs(
                    child, resolve, skip_registry=skip_registry, _seen=_seen
                )
        finally:
            _seen.discard(marker)
        return value
    return value


__all__ = [
    "BLOB_KEY",
    "DEFAULT_BLOB_THRESHOLD_BYTES",
    "DEFAULT_DEDUP_THRESHOLD_BYTES",
    "NORMALIZED_SCHEMA",
    "REF_KEY",
    "REGISTRY_KEY",
    "ArtifactRegistry",
    "blob_to_dotted",
    "decode_id",
    "encode_id",
    "hydrate_refs",
    "is_blob_marker",
    "is_ref_marker",
    "normalize_scan_result",
    "ref_key",
    "ref_to_dotted",
]
