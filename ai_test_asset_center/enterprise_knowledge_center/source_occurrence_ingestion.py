"""Canonical public ingestion root for content, interpretation, and source occurrence identity.

The existing atomic ingestion layer still owns archive transport transactions and ``_crud``
remains the only document activation/parser authority.  This module adds the missing identity
model above those authorities:

* content asset: immutable byte identity;
* interpretation asset: content interpreted under one source type and format identity;
* source occurrence: one stable source reference pointing at that interpretation.

Identical content under the same interpretation is parsed once and may have many occurrences.
Identical bytes under a different interpretation are blocked rather than silently reusing the
wrong parse.  No source occurrence is inferred from product output or selected fuzzily.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from . import atomic_ingestion as _atomic
from . import _crud
from ._common import PHASE, ROOT, _safe_project_id
from ._utils import (
    _load_registry,
    _now,
    _require_manage_actor,
    _save_registry,
    _short_hash,
)

SOURCE_OCCURRENCE_INGESTION_SCHEMA = (
    "qualibug.source-occurrence-enterprise-material-ingestion.v1"
)
CONTENT_ASSET_SCHEMA = "qualibug.enterprise-content-asset.v1"
INTERPRETATION_ASSET_SCHEMA = "qualibug.enterprise-interpretation-asset.v1"
SOURCE_OCCURRENCE_SCHEMA = "qualibug.enterprise-source-occurrence.v1"


class SourceOccurrenceIngestionError(RuntimeError):
    """The source occurrence identity transaction could not complete safely."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _portable_ref(value: Any) -> str:
    reference = _text(value).replace("\\", "/")
    if not reference:
        return ""
    if "://" in reference:
        return reference
    while "//" in reference:
        reference = reference.replace("//", "/")
    return reference.strip("/")


def _format_identity(filename: Any) -> str:
    suffix = Path(_text(filename) or "document").suffix.lower().lstrip(".")
    return suffix or "unknown"


def _content_asset_id(content_hash: str) -> str:
    return f"content:sha256:{content_hash}"


def _interpretation_asset_id(
    content_hash: str,
    source_type: str,
    format_identity: str,
) -> str:
    return "interpretation:" + _short_hash(
        {
            "content_hash": content_hash,
            "source_type": source_type,
            "format_identity": format_identity,
        },
        32,
    )


def _source_occurrence_id(
    project: str,
    source_ref: str,
    content_hash: str,
    interpretation_asset_id: str,
) -> str:
    return "occurrence:" + _short_hash(
        {
            "project": project,
            "source_ref": source_ref,
            "content_hash": content_hash,
            "interpretation_asset_id": interpretation_asset_id,
        },
        32,
    )


def _archive_source_ref(row: dict[str, Any], envelope: dict[str, Any]) -> str:
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


def _source_ref_for_result(
    row: dict[str, Any],
    envelope: dict[str, Any],
) -> str:
    explicit = _portable_ref(row.get("external_ref") or envelope.get("external_ref"))
    if explicit and not row.get("archive_provenance"):
        return explicit
    archive_ref = _archive_source_ref(row, envelope)
    if archive_ref:
        return archive_ref
    filename = _portable_ref(
        row.get("original_name")
        or row.get("filename")
        or envelope.get("filename")
        or envelope.get("name")
        or _atomic._envelope_filename(envelope)
    )
    source_type = _text(row.get("source_type") or envelope.get("source_type"))
    logical_key = _text(row.get("logical_key"))
    fallback = logical_key or f"{source_type or 'other_document'}:{filename or 'document'}"
    return f"unbound://{fallback}"


def _registry_rows(registry: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [
        row
        for row in registry.setdefault(key, [])
        if isinstance(row, dict)
    ]


def _source_by_id(registry: dict[str, Any], source_id: str) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in registry.get("sources") or []
            if isinstance(row, dict)
            and _text(row.get("source_id")) == source_id
        ),
        None,
    )


def _canonical_row_for_result(
    registry: dict[str, Any],
    raw: dict[str, Any],
) -> dict[str, Any]:
    source_id = _text(raw.get("source_id"))
    source = _source_by_id(registry, source_id)
    if not isinstance(source, dict):
        raise SourceOccurrenceIngestionError(
            f"canonical_source_missing_for_occurrence:{source_id or 'unknown'}"
        )
    return source


def _assert_interpretation_compatible(
    canonical: dict[str, Any],
    raw: dict[str, Any],
    envelope: dict[str, Any],
) -> tuple[str, str]:
    requested_type = _text(
        raw.get("source_type")
        or envelope.get("source_type")
        or canonical.get("source_type")
    )
    requested_format = _format_identity(
        raw.get("original_name")
        or raw.get("filename")
        or envelope.get("filename")
        or envelope.get("name")
        or _atomic._envelope_filename(envelope)
    )
    canonical_type = _text(canonical.get("source_type"))
    canonical_format = _format_identity(canonical.get("original_name"))
    if requested_type != canonical_type or requested_format != canonical_format:
        raise SourceOccurrenceIngestionError(
            "SOURCE_INTERPRETATION_CONFLICT:"
            + str(
                {
                    "canonical_source_id": _text(canonical.get("source_id")),
                    "canonical_source_type": canonical_type,
                    "requested_source_type": requested_type,
                    "canonical_format": canonical_format,
                    "requested_format": requested_format,
                }
            )
        )
    return requested_type, requested_format


def _upsert_content_asset(
    registry: dict[str, Any],
    *,
    content_hash: str,
    source_id: str,
    occurrence_id: str,
    stored_path: str,
) -> dict[str, Any]:
    rows = _registry_rows(registry, "content_assets")
    identity = _content_asset_id(content_hash)
    asset = next((row for row in rows if row.get("content_asset_id") == identity), None)
    if asset is None:
        asset = {
            "schema": CONTENT_ASSET_SCHEMA,
            "content_asset_id": identity,
            "content_hash": content_hash,
            "stored_path": stored_path,
            "canonical_source_ids": [],
            "source_occurrence_ids": [],
            "created_at_utc": _now(),
            "immutable_bytes": True,
        }
        rows.append(asset)
    asset["canonical_source_ids"] = sorted(
        {
            *[_text(value) for value in asset.get("canonical_source_ids") or [] if _text(value)],
            source_id,
        }
    )
    asset["source_occurrence_ids"] = sorted(
        {
            *[_text(value) for value in asset.get("source_occurrence_ids") or [] if _text(value)],
            occurrence_id,
        }
    )
    return asset


def _upsert_interpretation_asset(
    registry: dict[str, Any],
    *,
    interpretation_asset_id: str,
    content_asset_id: str,
    content_hash: str,
    source_type: str,
    format_identity: str,
    source_id: str,
    occurrence_id: str,
    source_ref: str,
    canonical: dict[str, Any],
) -> dict[str, Any]:
    rows = _registry_rows(registry, "interpretation_assets")
    asset = next(
        (
            row
            for row in rows
            if row.get("interpretation_asset_id") == interpretation_asset_id
        ),
        None,
    )
    if asset is None:
        parse = dict(canonical.get("parse") or {})
        asset = {
            "schema": INTERPRETATION_ASSET_SCHEMA,
            "interpretation_asset_id": interpretation_asset_id,
            "content_asset_id": content_asset_id,
            "content_hash": content_hash,
            "source_type": source_type,
            "format_identity": format_identity,
            "canonical_source_id": source_id,
            "parser_receipt_id": _text(
                dict(parse.get("receipt") or {}).get("receipt_id")
            ),
            "parse_status": _text(parse.get("parse_status")),
            "source_occurrence_ids": [],
            "source_refs": [],
            "created_at_utc": _now(),
            "parse_reuse_authority": "CONTENT_HASH_SOURCE_TYPE_FORMAT_IDENTITY",
        }
        rows.append(asset)
    elif _text(asset.get("canonical_source_id")) != source_id:
        raise SourceOccurrenceIngestionError(
            "INTERPRETATION_CANONICAL_SOURCE_CONFLICT:"
            + interpretation_asset_id
        )
    asset["source_occurrence_ids"] = sorted(
        {
            *[_text(value) for value in asset.get("source_occurrence_ids") or [] if _text(value)],
            occurrence_id,
        }
    )
    asset["source_refs"] = sorted(
        {
            *[_portable_ref(value) for value in asset.get("source_refs") or [] if _portable_ref(value)],
            source_ref,
        }
    )
    return asset


def _record_occurrence(
    registry: dict[str, Any],
    *,
    project: str,
    actor: dict[str, Any],
    canonical: dict[str, Any],
    raw: dict[str, Any],
    envelope: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    source_id = _text(canonical.get("source_id"))
    content_hash = _text(canonical.get("content_hash"))
    if not source_id or not content_hash:
        raise SourceOccurrenceIngestionError(
            f"canonical_source_identity_incomplete:{source_id or 'unknown'}"
        )
    source_type, format_identity = _assert_interpretation_compatible(
        canonical, raw, envelope
    )
    source_ref = _source_ref_for_result(raw, envelope)
    if not source_ref:
        raise SourceOccurrenceIngestionError(
            f"source_occurrence_ref_missing:{source_id}"
        )
    content_asset_id = _content_asset_id(content_hash)
    interpretation_asset_id = _interpretation_asset_id(
        content_hash,
        source_type,
        format_identity,
    )
    occurrence_id = _source_occurrence_id(
        project,
        source_ref,
        content_hash,
        interpretation_asset_id,
    )
    occurrences = _registry_rows(registry, "source_occurrences")
    existing = next(
        (
            row
            for row in occurrences
            if _text(row.get("source_occurrence_id")) == occurrence_id
        ),
        None,
    )
    if existing is not None:
        return existing, False

    previous = [
        row
        for row in occurrences
        if _portable_ref(row.get("source_ref")) == source_ref
        and row.get("status") == "active"
    ]
    version = max(
        [int(row.get("version") or 0) for row in occurrences if _portable_ref(row.get("source_ref")) == source_ref],
        default=0,
    ) + 1
    for row in previous:
        row["status"] = "superseded"
        row["superseded_at_utc"] = _now()
        row["superseded_by_occurrence_id"] = occurrence_id

    occurrence = {
        "schema": SOURCE_OCCURRENCE_SCHEMA,
        "source_occurrence_id": occurrence_id,
        "source_ref": source_ref,
        "source_id": source_id,
        "canonical_source_id": source_id,
        "content_asset_id": content_asset_id,
        "interpretation_asset_id": interpretation_asset_id,
        "content_hash": content_hash,
        "source_type": source_type,
        "format_identity": format_identity,
        "filename": _text(canonical.get("original_name")),
        "logical_key": _text(canonical.get("logical_key")),
        "archive_provenance": dict(raw.get("archive_provenance") or {}),
        "version": version,
        "status": "active",
        "created_at_utc": _now(),
        "created_by": dict(actor),
        "parse_reused": bool(raw.get("reason") == "same_content_hash"),
        "independent_evidence_identity": True,
        "absolute_workspace_path_is_identity": False,
    }
    occurrences.append(occurrence)

    _upsert_content_asset(
        registry,
        content_hash=content_hash,
        source_id=source_id,
        occurrence_id=occurrence_id,
        stored_path=_text(canonical.get("stored_path")),
    )
    _upsert_interpretation_asset(
        registry,
        interpretation_asset_id=interpretation_asset_id,
        content_asset_id=content_asset_id,
        content_hash=content_hash,
        source_type=source_type,
        format_identity=format_identity,
        source_id=source_id,
        occurrence_id=occurrence_id,
        source_ref=source_ref,
        canonical=canonical,
    )
    canonical["content_asset_id"] = content_asset_id
    canonical["interpretation_asset_id"] = interpretation_asset_id
    canonical["source_occurrence_ids"] = sorted(
        {
            *[_text(value) for value in canonical.get("source_occurrence_ids") or [] if _text(value)],
            occurrence_id,
        }
    )
    canonical["source_refs"] = sorted(
        {
            *[_portable_ref(value) for value in canonical.get("source_refs") or [] if _portable_ref(value)],
            source_ref,
        }
    )
    canonical["active_source_occurrence_count"] = sum(
        row.get("status") == "active"
        and _text(row.get("canonical_source_id")) == source_id
        for row in occurrences
    )
    return occurrence, True


def _register_result_occurrences(
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
    rows = [
        (dict(row), True)
        for row in result.get("created") or []
        if isinstance(row, dict)
    ] + [
        (dict(row), False)
        for row in result.get("duplicates") or []
        if isinstance(row, dict)
    ]
    for raw, canonical_was_created in rows:
        canonical = _canonical_row_for_result(registry, raw)
        occurrence, occurrence_was_created = _record_occurrence(
            registry,
            project=project,
            actor=actor,
            canonical=canonical,
            raw=raw,
            envelope=envelope,
        )
        projected = {
            **dict(occurrence),
            "canonical_source_was_created": canonical_was_created,
        }
        if occurrence_was_created:
            created_occurrences.append(projected)
        else:
            duplicate_occurrences.append(projected)

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
            "canonical_source_ids": sorted(
                {
                    _text(row.get("canonical_source_id"))
                    for row in [*created_occurrences, *duplicate_occurrences]
                    if _text(row.get("canonical_source_id"))
                }
            ),
        }
    )
    _save_registry(project, root, registry)
    return created_occurrences, duplicate_occurrences


def _merge_child_result(aggregate: dict[str, Any], result: dict[str, Any]) -> None:
    for key in (
        "created",
        "duplicates",
        "errors",
        "warnings",
        "rolled_back_archives",
        "archive_reconciliations",
    ):
        aggregate.setdefault(key, []).extend(
            dict(row)
            for row in result.get(key) or []
            if isinstance(row, dict)
        )
    expansion = dict(result.get("archive_expansion") or {})
    target = aggregate.setdefault("archive_expansion", {})
    for key in ("packages", "errors", "warnings"):
        target.setdefault(key, []).extend(
            dict(row)
            for row in expansion.get(key) or []
            if isinstance(row, dict)
        )
    for key in ("document_count", "package_count", "error_count", "warning_count"):
        target[key] = int(target.get(key) or 0) + int(expansion.get(key) or 0)
    target["status"] = (
        "BLOCKED"
        if target.get("errors")
        else "PARTIAL"
        if target.get("warnings")
        else "COMPLETE"
    )


def ingest_enterprise_knowledge_documents(
    project_id: str,
    documents: list[dict[str, Any]],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ingest each transport transaction and register every source occurrence exactly once."""
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
                {
                    "code": "DOCUMENT_ENVELOPE_INVALID",
                    "detail": "document envelope must be object",
                }
            )
            continue
        envelope = dict(raw)
        result = _atomic.ingest_enterprise_knowledge_documents(
            project,
            [envelope],
            root=resolved_root,
            actor=clean_actor,
        )
        _merge_child_result(aggregate, result)
        if result.get("errors"):
            continue
        try:
            created, duplicates = _register_result_occurrences(
                project=project,
                root=resolved_root,
                actor=clean_actor,
                envelope=envelope,
                result=result,
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
    active_occurrences = [
        row
        for row in registry.get("source_occurrences") or []
        if isinstance(row, dict) and row.get("status") == "active"
    ]
    active_sources = [
        row
        for row in registry.get("sources") or []
        if isinstance(row, dict) and row.get("status") == "active"
    ]
    aggregate["source_count"] = len(active_sources)
    aggregate["source_occurrence_count"] = len(active_occurrences)
    aggregate["content_asset_count"] = len(
        [row for row in registry.get("content_assets") or [] if isinstance(row, dict)]
    )
    aggregate["interpretation_asset_count"] = len(
        [
            row
            for row in registry.get("interpretation_assets") or []
            if isinstance(row, dict)
        ]
    )
    aggregate["ok"] = not aggregate["errors"]
    aggregate["rebuild_recommended"] = bool(
        aggregate["created"] or aggregate["source_occurrences"]
    )
    aggregate["content_identity_separate_from_source_occurrence"] = True
    aggregate["interpretation_identity_separate_from_content_identity"] = True
    aggregate["same_interpretation_content_parsed_once"] = True
    aggregate["different_interpretation_content_reuse_fails_closed"] = True
    aggregate["atomic_transport_authority"] = "atomic_ingestion"
    aggregate["canonical_document_activation_authority"] = "_crud"
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
