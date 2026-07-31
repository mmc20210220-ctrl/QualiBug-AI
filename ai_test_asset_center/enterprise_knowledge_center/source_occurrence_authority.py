"""Single authority for content, interpretation, and source-occurrence identity.

Call graph:
public source-occurrence ingestion -> existing atomic transport -> existing ``_crud`` leaf
activation -> this registry authority.  No parser, adapter, semantic extractor, or source
activation transaction is duplicated here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from . import atomic_ingestion as _atomic
from ._common import PHASE, ROOT, _safe_project_id
from ._utils import _load_registry, _now, _require_manage_actor, _save_registry, _short_hash

SOURCE_OCCURRENCE_INGESTION_SCHEMA = (
    "qualibug.source-occurrence-enterprise-material-ingestion.v1"
)
CONTENT_ASSET_SCHEMA = "qualibug.enterprise-content-asset.v1"
INTERPRETATION_ASSET_SCHEMA = "qualibug.enterprise-interpretation-asset.v1"
SOURCE_OCCURRENCE_SCHEMA = "qualibug.enterprise-source-occurrence.v1"


class SourceOccurrenceIngestionError(RuntimeError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _portable_ref(value: Any) -> str:
    ref = _text(value).replace("\\", "/")
    if not ref:
        return ""
    if "://" not in ref:
        while "//" in ref:
            ref = ref.replace("//", "/")
        ref = ref.strip("/")
    return ref


def _format_identity(value: Any) -> str:
    return Path(_text(value) or "document").suffix.lower().lstrip(".") or "unknown"


def _rows(registry: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = [row for row in registry.get(key) or [] if isinstance(row, dict)]
    registry[key] = rows
    return rows


def _source_by_id(registry: dict[str, Any], source_id: str) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in registry.get("sources") or []
            if isinstance(row, dict) and _text(row.get("source_id")) == source_id
        ),
        None,
    )


def _content_id(content_hash: str) -> str:
    return f"content:sha256:{content_hash}"


def _interpretation_id(content_hash: str, source_type: str, fmt: str) -> str:
    return "interpretation:" + _short_hash(
        {"content_hash": content_hash, "source_type": source_type, "format": fmt}, 32
    )


def _occurrence_id(
    project: str, source_ref: str, content_hash: str, interpretation_id: str
) -> str:
    return "occurrence:" + _short_hash(
        {
            "project": project,
            "source_ref": source_ref,
            "content_hash": content_hash,
            "interpretation_id": interpretation_id,
        },
        32,
    )


def _archive_ref(row: dict[str, Any], envelope: dict[str, Any]) -> str:
    provenance = dict(row.get("archive_provenance") or {})
    member = _portable_ref(
        provenance.get("virtual_member_path")
        or provenance.get("member_path")
        or row.get("filename")
        or row.get("original_name")
    )
    if not member:
        return ""
    base = _portable_ref(
        envelope.get("external_ref")
        or provenance.get("top_level_archive_name")
        or provenance.get("root_archive_filename")
        or _atomic._envelope_filename(envelope)
    )
    return f"archive://{base}!/{member}" if base else f"archive://{member}"


def _source_ref(row: dict[str, Any], envelope: dict[str, Any]) -> str:
    if row.get("archive_provenance"):
        archived = _archive_ref(row, envelope)
        if archived:
            return archived
    explicit = _portable_ref(row.get("external_ref") or envelope.get("external_ref"))
    if explicit:
        return explicit
    filename = _portable_ref(
        row.get("original_name")
        or row.get("filename")
        or envelope.get("filename")
        or envelope.get("name")
        or _atomic._envelope_filename(envelope)
    )
    logical = _text(row.get("logical_key"))
    source_type = _text(row.get("source_type") or envelope.get("source_type"))
    return f"unbound://{logical or f'{source_type or 'other_document'}:{filename or 'document'}'}"


def _canonical_for_result(
    registry: dict[str, Any], result_row: dict[str, Any]
) -> dict[str, Any]:
    source_id = _text(result_row.get("source_id"))
    canonical = _source_by_id(registry, source_id)
    if canonical is None:
        raise SourceOccurrenceIngestionError(
            f"CANONICAL_SOURCE_MISSING:{source_id or 'unknown'}"
        )
    return canonical


def _interpretation(
    canonical: dict[str, Any], row: dict[str, Any], envelope: dict[str, Any]
) -> tuple[str, str]:
    requested_type = _text(
        row.get("source_type")
        or envelope.get("source_type")
        or canonical.get("source_type")
    )
    requested_format = _format_identity(
        row.get("original_name")
        or row.get("filename")
        or envelope.get("filename")
        or envelope.get("name")
        or _atomic._envelope_filename(envelope)
    )
    canonical_type = _text(canonical.get("source_type"))
    canonical_format = _format_identity(canonical.get("original_name"))
    if requested_type != canonical_type or requested_format != canonical_format:
        raise SourceOccurrenceIngestionError(
            "SOURCE_INTERPRETATION_CONFLICT:"
            + repr(
                {
                    "canonical_source_id": canonical.get("source_id"),
                    "canonical_source_type": canonical_type,
                    "requested_source_type": requested_type,
                    "canonical_format": canonical_format,
                    "requested_format": requested_format,
                }
            )
        )
    return requested_type, requested_format


def _add_unique(row: dict[str, Any], key: str, value: str) -> None:
    row[key] = sorted(
        {
            *[_text(item) for item in row.get(key) or [] if _text(item)],
            value,
        }
    )


def _upsert_content(
    registry: dict[str, Any], canonical: dict[str, Any], occurrence_id: str
) -> str:
    content_hash = _text(canonical.get("content_hash"))
    content_id = _content_id(content_hash)
    rows = _rows(registry, "content_assets")
    asset = next((row for row in rows if row.get("content_asset_id") == content_id), None)
    if asset is None:
        asset = {
            "schema": CONTENT_ASSET_SCHEMA,
            "content_asset_id": content_id,
            "content_hash": content_hash,
            "stored_path": _text(canonical.get("stored_path")),
            "canonical_source_ids": [],
            "source_occurrence_ids": [],
            "created_at_utc": _now(),
            "immutable_bytes": True,
        }
        rows.append(asset)
    _add_unique(asset, "canonical_source_ids", _text(canonical.get("source_id")))
    _add_unique(asset, "source_occurrence_ids", occurrence_id)
    return content_id


def _upsert_interpretation(
    registry: dict[str, Any],
    *,
    canonical: dict[str, Any],
    content_id: str,
    interpretation_id: str,
    source_type: str,
    fmt: str,
    occurrence_id: str,
    source_ref: str,
) -> None:
    rows = _rows(registry, "interpretation_assets")
    asset = next(
        (row for row in rows if row.get("interpretation_asset_id") == interpretation_id),
        None,
    )
    source_id = _text(canonical.get("source_id"))
    if asset is None:
        parse = dict(canonical.get("parse") or {})
        asset = {
            "schema": INTERPRETATION_ASSET_SCHEMA,
            "interpretation_asset_id": interpretation_id,
            "content_asset_id": content_id,
            "content_hash": _text(canonical.get("content_hash")),
            "source_type": source_type,
            "format_identity": fmt,
            "canonical_source_id": source_id,
            "parser_receipt_id": _text(dict(parse.get("receipt") or {}).get("receipt_id")),
            "parse_status": _text(parse.get("parse_status")),
            "source_occurrence_ids": [],
            "source_refs": [],
            "created_at_utc": _now(),
            "parse_reuse_authority": "CONTENT_HASH_SOURCE_TYPE_FORMAT_IDENTITY",
        }
        rows.append(asset)
    if _text(asset.get("canonical_source_id")) != source_id:
        raise SourceOccurrenceIngestionError(
            f"INTERPRETATION_CANONICAL_SOURCE_CONFLICT:{interpretation_id}"
        )
    _add_unique(asset, "source_occurrence_ids", occurrence_id)
    _add_unique(asset, "source_refs", source_ref)


def _register_one(
    registry: dict[str, Any],
    *,
    project: str,
    actor: dict[str, Any],
    canonical: dict[str, Any],
    row: dict[str, Any],
    envelope: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    source_id = _text(canonical.get("source_id"))
    content_hash = _text(canonical.get("content_hash"))
    if not source_id or not content_hash:
        raise SourceOccurrenceIngestionError(
            f"CANONICAL_SOURCE_IDENTITY_INCOMPLETE:{source_id or 'unknown'}"
        )
    source_type, fmt = _interpretation(canonical, row, envelope)
    source_ref = _source_ref(row, envelope)
    if not source_ref:
        raise SourceOccurrenceIngestionError(f"SOURCE_REF_MISSING:{source_id}")
    interpretation_id = _interpretation_id(content_hash, source_type, fmt)
    occurrence_id = _occurrence_id(project, source_ref, content_hash, interpretation_id)
    occurrences = _rows(registry, "source_occurrences")
    existing = next(
        (row for row in occurrences if row.get("source_occurrence_id") == occurrence_id),
        None,
    )
    if existing is not None:
        return existing, False

    same_ref = [
        item
        for item in occurrences
        if item.get("status") == "active" and _portable_ref(item.get("source_ref")) == source_ref
    ]
    version = max(
        [
            int(item.get("version") or 0)
            for item in occurrences
            if _portable_ref(item.get("source_ref")) == source_ref
        ],
        default=0,
    ) + 1
    for item in same_ref:
        item["status"] = "superseded"
        item["superseded_at_utc"] = _now()
        item["superseded_by_occurrence_id"] = occurrence_id

    content_id = _content_id(content_hash)
    occurrence = {
        "schema": SOURCE_OCCURRENCE_SCHEMA,
        "source_occurrence_id": occurrence_id,
        "source_ref": source_ref,
        "source_id": source_id,
        "canonical_source_id": source_id,
        "content_asset_id": content_id,
        "interpretation_asset_id": interpretation_id,
        "content_hash": content_hash,
        "source_type": source_type,
        "format_identity": fmt,
        "filename": _text(canonical.get("original_name")),
        "logical_key": _text(canonical.get("logical_key")),
        "archive_provenance": dict(row.get("archive_provenance") or {}),
        "version": version,
        "status": "active",
        "created_at_utc": _now(),
        "created_by": dict(actor),
        "parse_reused": row.get("reason") == "same_content_hash",
        "independent_evidence_identity": True,
        "absolute_workspace_path_is_identity": False,
    }
    occurrences.append(occurrence)
    content_id = _upsert_content(registry, canonical, occurrence_id)
    _upsert_interpretation(
        registry,
        canonical=canonical,
        content_id=content_id,
        interpretation_id=interpretation_id,
        source_type=source_type,
        fmt=fmt,
        occurrence_id=occurrence_id,
        source_ref=source_ref,
    )
    canonical["content_asset_id"] = content_id
    canonical["interpretation_asset_id"] = interpretation_id
    _add_unique(canonical, "source_occurrence_ids", occurrence_id)
    _add_unique(canonical, "source_refs", source_ref)
    canonical["active_source_occurrence_count"] = sum(
        item.get("status") == "active"
        and _text(item.get("canonical_source_id")) == source_id
        for item in occurrences
    )
    return occurrence, True


def _register_result(
    *,
    project: str,
    root: Path,
    actor: dict[str, Any],
    envelope: dict[str, Any],
    result: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    registry = _load_registry(project, root)
    created_occurrences: list[dict[str, Any]] = []
    duplicate_occurrences: list[dict[str, Any]] = []
    result_rows = [
        (dict(row), True)
        for row in result.get("created") or []
        if isinstance(row, dict)
    ] + [
        (dict(row), False)
        for row in result.get("duplicates") or []
        if isinstance(row, dict)
    ]
    for row, canonical_created in result_rows:
        canonical = _canonical_for_result(registry, row)
        occurrence, occurrence_created = _register_one(
            registry,
            project=project,
            actor=actor,
            canonical=canonical,
            row=row,
            envelope=envelope,
        )
        projected = {**dict(occurrence), "canonical_source_was_created": canonical_created}
        (created_occurrences if occurrence_created else duplicate_occurrences).append(projected)

    registry.setdefault("governance", {}).update(
        {
            "content_identity_separate_from_source_occurrence": True,
            "interpretation_identity_separate_from_content_identity": True,
            "same_interpretation_content_parsed_once": True,
            "different_interpretation_content_reuse_fails_closed": True,
            "source_occurrence_identity_authority": "SOURCE_OCCURRENCE_REGISTRY",
        }
    )
    registry.setdefault("audit_events", []).append(
        {
            "event": "register_source_occurrences",
            "at_utc": _now(),
            "actor": actor,
            "created_source_occurrence_ids": [
                row["source_occurrence_id"] for row in created_occurrences
            ],
            "duplicate_source_occurrence_ids": [
                row["source_occurrence_id"] for row in duplicate_occurrences
            ],
        }
    )
    _save_registry(project, root, registry)
    return created_occurrences, duplicate_occurrences


def _merge(aggregate: dict[str, Any], result: dict[str, Any]) -> None:
    for key in (
        "created",
        "duplicates",
        "errors",
        "warnings",
        "rolled_back_archives",
        "archive_reconciliations",
    ):
        aggregate.setdefault(key, []).extend(
            dict(row) for row in result.get(key) or [] if isinstance(row, dict)
        )
    source = dict(result.get("archive_expansion") or {})
    target = aggregate["archive_expansion"]
    for key in ("packages", "errors", "warnings"):
        target.setdefault(key, []).extend(
            dict(row) for row in source.get(key) or [] if isinstance(row, dict)
        )
    for key in ("document_count", "package_count", "error_count", "warning_count"):
        target[key] = int(target.get(key) or 0) + int(source.get(key) or 0)
    target["status"] = (
        "BLOCKED" if target.get("errors") else "PARTIAL" if target.get("warnings") else "COMPLETE"
    )


def ingest_enterprise_knowledge_documents(
    project_id: str,
    documents: list[dict[str, Any]],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    aggregate: dict[str, Any] = {
        "schema": SOURCE_OCCURRENCE_INGESTION_SCHEMA,
        "ok": True,
        "phase": PHASE,
        "project_id": project,
        "created": [],
        "duplicates": [],
        "errors": [],
        "warnings": [],
        "source_occurrences": [],
        "duplicate_source_occurrences": [],
        "rolled_back_archives": [],
        "archive_reconciliations": [],
        "archive_expansion": {
            "schema": "qualibug.enterprise-archive-expansion.v1",
            "status": "COMPLETE",
            "document_count": 0,
            "package_count": 0,
            "error_count": 0,
            "warning_count": 0,
            "packages": [],
            "errors": [],
            "warnings": [],
            "canonical_archive_authority": "archive_ingestion_core",
        },
    }
    for raw in documents or []:
        if not isinstance(raw, dict):
            aggregate["errors"].append(
                {"code": "DOCUMENT_ENVELOPE_INVALID", "detail": "document envelope must be object"}
            )
            continue
        envelope = dict(raw)
        child = _atomic.ingest_enterprise_knowledge_documents(
            project, [envelope], root=resolved_root, actor=clean_actor
        )
        _merge(aggregate, child)
        if child.get("errors"):
            continue
        try:
            created, duplicates = _register_result(
                project=project,
                root=resolved_root,
                actor=clean_actor,
                envelope=envelope,
                result=child,
            )
            aggregate["source_occurrences"].extend(created)
            aggregate["duplicate_source_occurrences"].extend(duplicates)
        except SourceOccurrenceIngestionError as exc:
            aggregate["errors"].append(
                {
                    "code": "SOURCE_OCCURRENCE_IDENTITY_BLOCKED",
                    "detail": str(exc)[:1000],
                    "source_ref": _portable_ref(envelope.get("external_ref")),
                    "blocks_formal_understanding": True,
                    "silent_failure_allowed": False,
                }
            )

    registry = _load_registry(project, resolved_root)
    active_sources = [
        row for row in registry.get("sources") or [] if isinstance(row, dict) and row.get("status") == "active"
    ]
    active_occurrences = [
        row
        for row in registry.get("source_occurrences") or []
        if isinstance(row, dict) and row.get("status") == "active"
    ]
    aggregate.update(
        {
            "source_count": len(active_sources),
            "source_occurrence_count": len(active_occurrences),
            "content_asset_count": len(_rows(registry, "content_assets")),
            "interpretation_asset_count": len(_rows(registry, "interpretation_assets")),
            "ok": not aggregate["errors"],
            "rebuild_recommended": bool(aggregate["created"] or aggregate["source_occurrences"]),
            "content_identity_separate_from_source_occurrence": True,
            "interpretation_identity_separate_from_content_identity": True,
            "same_interpretation_content_parsed_once": True,
            "different_interpretation_content_reuse_fails_closed": True,
            "atomic_transport_authority": "atomic_ingestion",
            "canonical_document_activation_authority": "_crud",
        }
    )
    return aggregate


def ingest_enterprise_knowledge_files(
    project_id: str,
    file_paths: Iterable[str | Path],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    source_type_hints: dict[str, str] | None = None,
) -> dict[str, Any]:
    hints = source_type_hints or {}
    return ingest_enterprise_knowledge_documents(
        project_id,
        [
            {
                "file_path": str(path),
                "source_type": hints.get(str(path)),
                "external_ref": Path(str(path)).name,
            }
            for path in file_paths
        ],
        root=root,
        actor=actor,
    )


__all__ = [
    "SOURCE_OCCURRENCE_INGESTION_SCHEMA",
    "CONTENT_ASSET_SCHEMA",
    "INTERPRETATION_ASSET_SCHEMA",
    "SOURCE_OCCURRENCE_SCHEMA",
    "SourceOccurrenceIngestionError",
    "ingest_enterprise_knowledge_documents",
    "ingest_enterprise_knowledge_files",
]
