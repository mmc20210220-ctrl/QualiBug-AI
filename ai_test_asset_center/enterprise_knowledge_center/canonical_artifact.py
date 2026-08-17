"""Canonical Artifact contract over the existing source registries.

This module is a read-only projection. It does not create a second storage model:

* immutable versioned content comes from ``enterprise_source_registry`` (the runtime
  source asset registry: asset -> versions -> blobs -> chunk indexes), and
* content/interpretation/source-occurrence semantics come from the enterprise
  knowledge-center registry (``content_assets``, ``interpretation_assets``,
  ``source_occurrences``).

One canonical Artifact is one stable source asset lineage (``knowledge_*`` runtime
asset id). Its versions are the immutable runtime versions; its source relations
are the knowledge source occurrences that point at those versions; its content
blocks are the Document IR retrieval chunks registered per content hash.

The projection intentionally does not re-derive entity mentions (those belong to
the asset-level enterprise understanding model), does not persist raw connector
cursors (the mainline deliberately persists only cursor fingerprints), and never
infers business meaning from file names or content.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from ._utils import _load_registry, _now
from ..enterprise_source_registry import (
    SourceRegistryError,
    _safe_project,
    list_source_asset_versions,
)

CANONICAL_ARTIFACT_SCHEMA = "qualibug.canonical-artifact.v1"
CANONICAL_ARTIFACT_VERSION_SCHEMA = "qualibug.canonical-artifact-version.v1"
CANONICAL_ARTIFACT_BLOCK_SCHEMA = "qualibug.canonical-artifact-block.v1"
CANONICAL_ARTIFACT_SOURCE_RELATION_SCHEMA = (
    "qualibug.canonical-artifact-source-relation.v1"
)
CANONICAL_ARTIFACT_QUERY_SCHEMA = "qualibug.canonical-artifact-query.v1"
CANONICAL_ARTIFACT_DIFF_SCHEMA = "qualibug.canonical-artifact-diff.v1"


class CanonicalArtifactError(ValueError):
    """The canonical artifact projection cannot be resolved safely."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _project_path(root: Path, project_id: str) -> str:
    return str(
        (Path(root) / "platform_workspace" / _safe_project(project_id)).resolve()
    )


def _version_fingerprint(chunk: dict[str, Any]) -> str:
    """Stable content fingerprint for one chunk row."""
    content_hash = _text(chunk.get("content_hash"))
    if content_hash:
        return content_hash
    content = _text(chunk.get("content"))
    if content:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
    return ""


def _block_key(chunk: dict[str, Any]) -> str:
    """Block-level anchor that is stable across versions of one artifact lineage."""
    block_id = _text(chunk.get("block_id"))
    if block_id:
        return f"block:{block_id}"
    chunk_id = _text(chunk.get("chunk_id"))
    if chunk_id:
        # chunk_id embeds the version-specific knowledge source id; keep only the
        # stable tail (e.g. ``chunk:semantic-projection``) as the anchor.
        parts = chunk_id.split(":", 2)
        if len(parts) == 3 and parts[0] == "chunk":
            return f"chunk:{parts[2]}"
        return f"chunk:{chunk_id}"
    return ""


def _block_ref(chunk: dict[str, Any]) -> dict[str, Any]:
    row = {
        "schema": CANONICAL_ARTIFACT_BLOCK_SCHEMA,
        "chunk_id": _text(chunk.get("chunk_id")),
        "block_id": _text(chunk.get("block_id")),
        "block_key": _block_key(chunk),
        "chunk_type": _text(chunk.get("chunk_type")),
        "content_hash": _version_fingerprint(chunk),
        "confidence": chunk.get("confidence"),
        "extraction_method": _text(chunk.get("extraction_method")),
    }
    for field in (
        "page",
        "bbox",
        "sheet",
        "cell_ref",
        "slide",
        "shape_id",
        "parent_id",
    ):
        if chunk.get(field) not in (None, "", []):
            row[field] = chunk.get(field)
    return row


def _version_view(
    version: dict[str, Any],
    *,
    block_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    row = {
        "schema": CANONICAL_ARTIFACT_VERSION_SCHEMA,
        "version_id": _text(version.get("version_id")),
        "source_hash": _text(version.get("source_hash")),
        "byte_count": version.get("byte_count"),
        "source_type": _text(version.get("source_type")),
        "source_origin": _text(version.get("source_origin")),
        "filename": _text(version.get("filename")),
        "external_ref": _text(version.get("external_ref")),
        "registered_at_utc": _text(version.get("registered_at_utc")),
        "blob_ref": _text(version.get("blob_ref")),
        "metadata": dict(version.get("metadata") or {}),
        "knowledge_source_id": _text(
            _dict(version.get("metadata") or {}).get("knowledge_source_id")
        ),
        "knowledge_source_version": _dict(version.get("metadata") or {}).get(
            "knowledge_source_version"
        ),
    }
    if block_refs is not None:
        row["block_count"] = len(block_refs)
    return row


def _knowledge_instance(
    source: dict[str, Any],
    *,
    content_asset: dict[str, Any] | None,
    interpretation_asset: dict[str, Any] | None,
) -> dict[str, Any]:
    parse = _dict(source.get("parse"))
    return {
        "knowledge_source_id": _text(source.get("source_id")),
        "content_hash": _text(source.get("content_hash")),
        "version": source.get("version"),
        "status": _text(source.get("status")),
        "source_type": _text(source.get("source_type")),
        "format_identity": _text(source.get("format_identity")),
        "original_name": _text(source.get("original_name")),
        "content_asset_id": _text(
            source.get("content_asset_id")
            or (content_asset or {}).get("content_asset_id")
        ),
        "interpretation_asset_id": _text(
            source.get("interpretation_asset_id")
            or (interpretation_asset or {}).get("interpretation_asset_id")
        ),
        "parse_status": _text(parse.get("parse_status")),
        "parse_reused": bool(parse.get("parse_reused")),
        "runtime_source_manifest": dict(source.get("runtime_source_manifest") or {}),
    }


def _source_relation(occurrence: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": CANONICAL_ARTIFACT_SOURCE_RELATION_SCHEMA,
        "source_occurrence_id": _text(occurrence.get("source_occurrence_id")),
        "source_ref": _text(occurrence.get("source_ref")),
        "external_ref": _text(
            occurrence.get("external_ref") or occurrence.get("source_ref")
        ),
        "content_hash": _text(occurrence.get("content_hash")),
        "canonical_source_id": _text(occurrence.get("canonical_source_id")),
        "version": occurrence.get("version"),
        "status": _text(occurrence.get("status")),
        "tags": list(occurrence.get("tags") or []),
        "updated_at_utc": _text(occurrence.get("updated_at_utc")),
    }


def _load_chunk_refs(
    project_id: str, source_hash: str, root: Path
) -> list[dict[str, Any]]:
    from ..enterprise_source_registry import load_source_chunks

    if not _text(source_hash):
        return []
    try:
        chunks = load_source_chunks(project_id, source_hash, root=root)
    except SourceRegistryError:
        return []
    return [_block_ref(chunk) for chunk in chunks if isinstance(chunk, dict)]


def _chunk_hash(version: dict[str, Any]) -> str:
    """Chunk indexes are keyed by the knowledge content hash, not the projection hash."""
    metadata = _dict(version.get("metadata"))
    original = _text(metadata.get("original_content_hash"))
    return original or _text(version.get("source_hash"))


def _artifact_view(
    *,
    runtime_asset: dict[str, Any],
    knowledge_sources: list[dict[str, Any]],
    occurrences: list[dict[str, Any]],
    content_assets: dict[str, dict[str, Any]],
    interpretation_assets: dict[str, dict[str, Any]],
    project_id: str,
    root: Path,
    include_deleted: bool,
) -> dict[str, Any]:
    versions = [
        dict(row) for row in _list(runtime_asset.get("versions")) if isinstance(row, dict)
    ]
    latest_hash = _text(runtime_asset.get("latest_source_hash"))
    instance_rows: list[dict[str, Any]] = []
    for source in knowledge_sources:
        manifest = _dict(source.get("runtime_source_manifest"))
        if _text(manifest.get("source_id")) != _text(runtime_asset.get("source_id")):
            continue
        status = _text(source.get("status"))
        if not include_deleted and status not in {"active", "superseded"}:
            continue
        content_asset = content_assets.get(_text(source.get("content_asset_id")))
        interpretation_asset = interpretation_assets.get(
            _text(source.get("interpretation_asset_id"))
        )
        instance_rows.append(
            _knowledge_instance(
                source,
                content_asset=content_asset,
                interpretation_asset=interpretation_asset,
            )
        )

    instance_ids = {row["knowledge_source_id"] for row in instance_rows}
    relations: list[dict[str, Any]] = []
    for occurrence in occurrences:
        if _text(occurrence.get("canonical_source_id")) not in instance_ids:
            continue
        status = _text(occurrence.get("status"))
        if not include_deleted and status != "active":
            continue
        relations.append(_source_relation(occurrence))

    version_views: list[dict[str, Any]] = []
    for version in versions:
        hash_value = _chunk_hash(version)
        block_refs = (
            _load_chunk_refs(project_id, hash_value, root) if hash_value else []
        )
        version_views.append(_version_view(version, block_refs=block_refs))
    head_version = versions[-1] if versions else {}
    latest_chunk_hash = _chunk_hash(head_version) or latest_hash
    latest_blocks = (
        _load_chunk_refs(project_id, latest_chunk_hash, root)
        if latest_chunk_hash
        else []
    )

    return {
        "schema": CANONICAL_ARTIFACT_SCHEMA,
        "artifact_id": _text(runtime_asset.get("source_id")),
        "artifact_kind": "RUNTIME_SOURCE_ASSET",
        "source_type": _text(runtime_asset.get("source_type")),
        "latest_source_hash": latest_hash,
        "latest_version_id": _text(runtime_asset.get("latest_version_id")),
        "updated_at_utc": _text(runtime_asset.get("updated_at_utc")),
        "versions": version_views,
        "version_count": len(version_views),
        "content_blocks": latest_blocks,
        "content_block_count": len(latest_blocks),
        "knowledge_instances": instance_rows,
        "knowledge_instance_count": len(instance_rows),
        "source_relations": relations,
        "source_relation_count": len(relations),
        "access_scope": {
            "project_id": _safe_project(project_id),
            "scope": "PROJECT_SCOPED",
            "storage_scope": _project_path(root, project_id),
        },
        "entity_mentions": {
            "status": "NOT_PROJECTED",
            "scope": "ASSET_LEVEL_ENTERPRISE_UNDERSTANDING",
            "note": "Entity mentions are derived by the asset-level understanding model, not by the artifact registry projection.",
        },
        "sync_cursor": {
            "persisted": False,
            "note": "Raw connector cursors are never persisted in product registries; only cursor fingerprints are observed.",
        },
        "traceability": {
            "runtime_registry": "platform_workspace/<project>/source_registry/registry.json",
            "knowledge_registry": "platform_workspace/<project>/enterprise_knowledge_center/source_registry.json",
            "chunk_index": "platform_workspace/<project>/source_registry/chunks/<content_hash>/chunks.json",
            "projected_at_utc": _now(),
            "read_only_projection": True,
        },
    }


def _asset_maps(
    project_id: str, root: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    registry = _load_registry(_safe_project(project_id), root)
    content_assets = {
        _text(row.get("content_asset_id")): row
        for row in _list(registry.get("content_assets"))
        if isinstance(row, dict) and _text(row.get("content_asset_id"))
    }
    interpretation_assets = {
        _text(row.get("interpretation_asset_id")): row
        for row in _list(registry.get("interpretation_assets"))
        if isinstance(row, dict)
        and _text(row.get("interpretation_asset_id"))
    }
    return content_assets, interpretation_assets


def project_canonical_artifacts(
    project_id: str,
    root: Path | None = None,
    *,
    include_deleted: bool = False,
) -> list[dict[str, Any]]:
    """Project one canonical Artifact per stable source-asset lineage (read-only)."""
    resolved_root = Path(root) if root is not None else Path.cwd()
    project = _safe_project(project_id)
    runtime_assets = _runtime_assets(project, resolved_root)
    registry = _load_registry(project, resolved_root)
    content_assets, interpretation_assets = _asset_maps(project, resolved_root)
    knowledge_sources = [
        dict(row)
        for row in _list(registry.get("sources"))
        if isinstance(row, dict) and _text(row.get("source_id"))
    ]
    occurrences = [
        dict(row)
        for row in _list(registry.get("source_occurrences"))
        if isinstance(row, dict)
    ]
    artifacts: list[dict[str, Any]] = []
    for asset_id in sorted(runtime_assets):
        artifact = _artifact_view(
            runtime_asset=runtime_assets[asset_id],
            knowledge_sources=knowledge_sources,
            occurrences=occurrences,
            content_assets=content_assets,
            interpretation_assets=interpretation_assets,
            project_id=project,
            root=resolved_root,
            include_deleted=include_deleted,
        )
        artifacts.append(artifact)
    return artifacts


def _runtime_assets(
    project_id: str, root: Path
) -> dict[str, dict[str, Any]]:
    rows = list_source_asset_versions(project_id, root=root)
    assets: dict[str, dict[str, Any]] = {}
    for raw in rows:
        asset_id = _text(raw.get("source_id"))
        if not asset_id:
            continue
        asset = assets.get(asset_id)
        if asset is None:
            asset = {
                "source_id": asset_id,
                "source_type": _text(raw.get("source_type")),
                "versions": [],
                "latest_source_hash": "",
                "latest_version_id": "",
                "updated_at_utc": "",
            }
            assets[asset_id] = asset
        asset["versions"].append(raw)
        registered_at = _text(raw.get("registered_at_utc"))
        if registered_at > _text(asset.get("updated_at_utc")):
            asset["updated_at_utc"] = registered_at
    for asset in assets.values():
        versions = asset["versions"]
        if versions:
            asset["latest_source_hash"] = _text(versions[-1].get("source_hash"))
            asset["latest_version_id"] = _text(versions[-1].get("version_id"))
    return assets


def query_canonical_artifacts(
    project_id: str,
    root: Path | None = None,
    *,
    artifact_id: str | None = None,
    source_ref: str | None = None,
    content_hash: str | None = None,
    knowledge_source_id: str | None = None,
    include_deleted: bool = False,
) -> dict[str, Any]:
    """Query the canonical artifact view by stable identity, source ref, or content hash."""
    resolved_root = Path(root) if root is not None else Path.cwd()
    project = _safe_project(project_id)
    artifacts = project_canonical_artifacts(
        project, resolved_root, include_deleted=include_deleted
    )
    wanted_artifact = _text(artifact_id)
    wanted_ref = _text(source_ref)
    wanted_hash = _text(content_hash).lower()
    wanted_knowledge = _text(knowledge_source_id)
    matches: list[dict[str, Any]] = []
    for artifact in artifacts:
        if wanted_artifact and _text(artifact.get("artifact_id")) != wanted_artifact:
            continue
        if wanted_ref:
            relations = _list(artifact.get("source_relations"))
            if not any(
                _text(row.get("source_ref")) == wanted_ref
                or _text(row.get("source_occurrence_id")) == wanted_ref
                for row in relations
            ):
                continue
        if wanted_hash:
            hashes = {
                _text(version.get("source_hash"))
                for version in _list(artifact.get("versions"))
                if _text(version.get("source_hash"))
            }
            hashes.update(
                {
                    _text(row.get("content_hash"))
                    for row in _list(artifact.get("knowledge_instances"))
                    if _text(row.get("content_hash"))
                }
            )
            if wanted_hash not in hashes:
                continue
        if wanted_knowledge:
            instances = _list(artifact.get("knowledge_instances"))
            if not any(
                _text(row.get("knowledge_source_id")) == wanted_knowledge
                for row in instances
            ):
                continue
        matches.append(artifact)
    return {
        "schema": CANONICAL_ARTIFACT_QUERY_SCHEMA,
        "project_id": project,
        "filters": {
            "artifact_id": wanted_artifact,
            "source_ref": wanted_ref,
            "content_hash": wanted_hash,
            "knowledge_source_id": wanted_knowledge,
            "include_deleted": bool(include_deleted),
        },
        "match_count": len(matches),
        "artifacts": matches,
        "status": "MATCHED" if matches else "NO_MATCH",
    }


def list_artifact_versions(
    project_id: str,
    artifact_id: str,
    root: Path | None = None,
) -> dict[str, Any]:
    """List immutable versions for one canonical artifact."""
    resolved_root = Path(root) if root is not None else Path.cwd()
    project = _safe_project(project_id)
    query = query_canonical_artifacts(
        project, resolved_root, artifact_id=artifact_id, include_deleted=True
    )
    artifacts = _list(query.get("artifacts"))
    if len(artifacts) != 1:
        raise CanonicalArtifactError(
            f"artifact identity is missing or ambiguous: {artifact_id or 'unknown'}"
        )
    artifact = artifacts[0]
    return {
        "schema": CANONICAL_ARTIFACT_QUERY_SCHEMA,
        "project_id": project,
        "artifact_id": _text(artifact.get("artifact_id")),
        "version_count": int(artifact.get("version_count") or 0),
        "versions": _list(artifact.get("versions")),
    }


def _resolve_version_manifests(
    project_id: str,
    artifact_id: str,
    root: Path,
    *,
    base_version_id: str | None,
    head_version_id: str | None,
    base_hash: str | None,
    head_hash: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime_assets = _runtime_assets(project_id, root)
    asset = runtime_assets.get(artifact_id)
    if asset is None:
        raise CanonicalArtifactError(
            f"artifact not found in runtime source registry: {artifact_id}"
        )
    versions = [
        dict(row) for row in _list(asset.get("versions")) if isinstance(row, dict)
    ]
    if len(versions) < 2:
        raise CanonicalArtifactError(
            f"artifact requires at least two immutable versions for diff: {artifact_id}"
        )

    def pick(
        version_id: str | None,
        source_hash: str | None,
        *,
        fallback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if version_id:
            for row in versions:
                if _text(row.get("version_id")) == _text(version_id):
                    return row
            raise CanonicalArtifactError(
                f"version not found: {version_id} (artifact {artifact_id})"
            )
        if source_hash:
            for row in versions:
                if _text(row.get("source_hash")) == _text(source_hash).lower():
                    return row
            raise CanonicalArtifactError(
                f"source hash not found among artifact versions: {source_hash}"
            )
        if fallback is None:
            raise CanonicalArtifactError(
                f"cannot resolve version for artifact {artifact_id}: "
                "provide base/head version ids or hashes"
            )
        return fallback

    base = pick(base_version_id, base_hash, fallback=versions[0])
    head = pick(head_version_id, head_hash, fallback=versions[-1])
    return base, head


def _chunk_index(chunks: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        key = _block_key(chunk)
        if not key:
            continue
        if key in index:
            continue
        index[key] = chunk
    return index


def diff_artifact_versions(
    project_id: str,
    artifact_id: str,
    root: Path | None = None,
    *,
    base_version_id: str | None = None,
    head_version_id: str | None = None,
    base_hash: str | None = None,
    head_hash: str | None = None,
) -> dict[str, Any]:
    """Block-level diff between two immutable versions of one canonical artifact.

    The diff compares Document IR retrieval chunks anchored by stable block
    identity. When either side has no chunk index, it emits an explicit
    ``CHANGE_REVIEW_GAP`` instead of inventing a change summary.
    """
    resolved_root = Path(root) if root is not None else Path.cwd()
    project = _safe_project(project_id)
    from ..enterprise_source_registry import load_source_chunks

    base, head = _resolve_version_manifests(
        project,
        _text(artifact_id),
        resolved_root,
        base_version_id=base_version_id,
        head_version_id=head_version_id,
        base_hash=base_hash,
        head_hash=head_hash,
    )
    base_hash_value = _text(base.get("source_hash"))
    head_hash_value = _text(head.get("source_hash"))
    base_chunk_hash = _chunk_hash(base)
    head_chunk_hash = _chunk_hash(head)
    common: dict[str, Any] = {
        "schema": CANONICAL_ARTIFACT_DIFF_SCHEMA,
        "project_id": project,
        "artifact_id": _text(artifact_id),
        "base": {
            "version_id": _text(base.get("version_id")),
            "source_hash": base_hash_value,
        },
        "head": {
            "version_id": _text(head.get("version_id")),
            "source_hash": head_hash_value,
        },
        "content_hash_changed": base_hash_value != head_hash_value,
        "summary": {},
        "added_blocks": [],
        "removed_blocks": [],
        "changed_blocks": [],
        "unchanged_blocks": [],
        "change_review_gaps": [],
    }
    if base_hash_value == head_hash_value:
        common["verdict"] = "UNCHANGED"
        common["summary"] = {
            "added_block_count": 0,
            "removed_block_count": 0,
            "changed_block_count": 0,
            "unchanged_block_count": 0,
            "review_required": False,
        }
        return common

    try:
        base_chunks = [
            dict(row)
            for row in load_source_chunks(
                project, base_chunk_hash, root=resolved_root
            )
            if isinstance(row, dict)
        ]
        head_chunks = [
            dict(row)
            for row in load_source_chunks(
                project, head_chunk_hash, root=resolved_root
            )
            if isinstance(row, dict)
        ]
    except SourceRegistryError as exc:
        raise CanonicalArtifactError(
            f"artifact chunk index unavailable: {exc}"
        ) from exc

    if not base_chunks or not head_chunks:
        common["verdict"] = "CHANGED"
        common["change_review_gaps"].append(
            {
                "kind": "CHANGE_REVIEW_GAP",
                "code": "ARTIFACT_CHUNK_INDEX_UNAVAILABLE",
                "detail": (
                    "block-level comparison needs Document IR chunk indexes for both "
                    "versions; missing indexes are reported instead of a fabricated "
                    "change list"
                ),
                "base_chunk_count": len(base_chunks),
                "head_chunk_count": len(head_chunks),
            }
        )
        common["summary"] = {
            "added_block_count": 0,
            "removed_block_count": 0,
            "changed_block_count": 0,
            "unchanged_block_count": 0,
            "review_required": True,
        }
        return common

    base_index = _chunk_index(base_chunks)
    head_index = _chunk_index(head_chunks)
    keys = sorted(set(base_index) | set(head_index))
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    identity_mode = "BLOCK_ID"
    for key in keys:
        base_chunk = base_index.get(key)
        head_chunk = head_index.get(key)
        if base_chunk is None:
            added.append(_block_ref(head_chunk))
        elif head_chunk is None:
            removed.append(_block_ref(base_chunk))
        elif _version_fingerprint(base_chunk) != _version_fingerprint(head_chunk):
            changed.append(
                {
                    "block_key": key,
                    "base": _block_ref(base_chunk),
                    "head": _block_ref(head_chunk),
                }
            )
        else:
            unchanged.append(_block_ref(base_chunk))
    if not keys:
        identity_mode = "FINGERPRINT_ONLY"
        base_fps = {
            _version_fingerprint(chunk)
            for chunk in base_chunks
            if _version_fingerprint(chunk)
        }
        head_fps = {
            _version_fingerprint(chunk)
            for chunk in head_chunks
            if _version_fingerprint(chunk)
        }
        for fingerprint in sorted(base_fps - head_fps):
            removed.append({"block_key": "", "content_hash": fingerprint})
        for fingerprint in sorted(head_fps - base_fps):
            added.append({"block_key": "", "content_hash": fingerprint})
    common.update(
        {
            "verdict": "CHANGED",
            "block_identity_mode": identity_mode,
            "added_blocks": added,
            "removed_blocks": removed,
            "changed_blocks": changed,
            "unchanged_blocks": unchanged,
            "summary": {
                "added_block_count": len(added),
                "removed_block_count": len(removed),
                "changed_block_count": len(changed),
                "unchanged_block_count": len(unchanged),
                "review_required": bool(added or removed or changed),
            },
        }
    )
    return common


__all__ = [
    "CANONICAL_ARTIFACT_SCHEMA",
    "CANONICAL_ARTIFACT_VERSION_SCHEMA",
    "CANONICAL_ARTIFACT_BLOCK_SCHEMA",
    "CANONICAL_ARTIFACT_SOURCE_RELATION_SCHEMA",
    "CANONICAL_ARTIFACT_QUERY_SCHEMA",
    "CANONICAL_ARTIFACT_DIFF_SCHEMA",
    "CanonicalArtifactError",
    "project_canonical_artifacts",
    "query_canonical_artifacts",
    "list_artifact_versions",
    "diff_artifact_versions",
]
