"""Core authority for content, interpretation, and source-occurrence identity.

This module does not implement a parser. It orchestrates the existing atomic transport and CRUD
activation authorities, then records three distinct identities in the existing project registry:
immutable content, one governed interpretation, and each source occurrence.

Canonical interpretation records are deliberately provenance-neutral. Source paths, archive
membership and source versions live only on occurrence records, so updating or removing one
occurrence cannot supersede a canonical interpretation still used elsewhere.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from . import _crud
from . import atomic_ingestion as _atomic
from ._common import PHASE, ROOT, _safe_project_id
from ._utils import _load_registry, _now, _require_manage_actor, _save_registry, _short_hash
from ..enterprise_source_registry_lifecycle import deactivate_source_asset

SOURCE_OCCURRENCE_INGESTION_SCHEMA = "qualibug.source-occurrence-enterprise-material-ingestion.v2"
CONTENT_ASSET_SCHEMA = "qualibug.enterprise-content-asset.v1"
INTERPRETATION_ASSET_SCHEMA = "qualibug.enterprise-interpretation-asset.v1"
SOURCE_OCCURRENCE_SCHEMA = "qualibug.enterprise-source-occurrence.v1"


class SourceOccurrenceIngestionError(RuntimeError):
    """The occurrence identity transaction could not be completed safely."""


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


def _registry_rows(registry: dict[str, Any], key: str) -> list[dict[str, Any]]:
    raw = registry.get(key)
    if isinstance(raw, list) and all(isinstance(row, dict) for row in raw):
        # Keep the existing list object stable across nested authority calls. Replacing a list
        # here while a caller still holds it loses newly appended source occurrences when the
        # same registry is normalized again by _supersede_active_source_ref_peers or cleanup.
        return raw
    rows = [row for row in raw or [] if isinstance(row, dict)] if isinstance(raw, list) else []
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


def _content_asset_id(content_hash: str) -> str:
    return f"content:sha256:{content_hash}"


def _interpretation_asset_id(content_hash: str, source_type: str, fmt: str) -> str:
    return "interpretation:" + _short_hash(
        {"content_hash": content_hash, "source_type": source_type, "format_identity": fmt},
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


def _archive_base_ref(envelope: dict[str, Any]) -> str:
    return _portable_ref(
        envelope.get("external_ref")
        or _atomic._envelope_filename(envelope)
        or "archive"
    )


def _archive_prefix(envelope: dict[str, Any]) -> str:
    return f"archive://{_archive_base_ref(envelope)}!/"


def _archive_source_ref(row: dict[str, Any], envelope: dict[str, Any]) -> str:
    provenance = dict(row.get("archive_provenance") or {})
    member = _portable_ref(
        provenance.get("virtual_member_path")
        or provenance.get("member_path")
        or row.get("filename")
        or row.get("original_name")
    )
    return f"{_archive_prefix(envelope)}{member}" if member else ""


def _source_ref(row: dict[str, Any], envelope: dict[str, Any]) -> str:
    if row.get("archive_provenance"):
        archived = _archive_source_ref(row, envelope)
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
    source_type = _text(row.get("source_type") or envelope.get("source_type")) or "other_document"
    fallback = _text(row.get("logical_key")) or f"{source_type}:{filename or 'document'}"
    return f"unbound://{fallback}"


def _canonical_for_result(registry: dict[str, Any], result_row: dict[str, Any]) -> dict[str, Any]:
    source_id = _text(result_row.get("source_id"))
    canonical = _source_by_id(registry, source_id)
    if canonical is None:
        raise SourceOccurrenceIngestionError(f"CANONICAL_SOURCE_MISSING:{source_id or 'unknown'}")
    return canonical


def _interpretation(
    canonical: dict[str, Any], result_row: dict[str, Any], envelope: dict[str, Any]
) -> tuple[str, str]:
    requested_type = _text(
        result_row.get("source_type")
        or envelope.get("source_type")
        or canonical.get("source_type")
    )
    requested_format = _format_identity(
        result_row.get("original_name")
        or result_row.get("filename")
        or envelope.get("filename")
        or envelope.get("name")
        or _atomic._envelope_filename(envelope)
    )
    canonical_type = _text(canonical.get("source_type"))
    canonical_format = _format_identity(canonical.get("original_name"))
    generic_types = {"collaboration_document", "other_document", "other"}
    effective_type = (
        canonical_type
        if requested_type in generic_types and canonical_type not in generic_types
        else requested_type
    )
    if effective_type != canonical_type or requested_format != canonical_format:
        raise SourceOccurrenceIngestionError(
            "SOURCE_INTERPRETATION_CONFLICT:"
            + repr(
                {
                    "canonical_source_id": canonical.get("source_id"),
                    "canonical_source_type": canonical_type,
                    "requested_source_type": requested_type,
                    "effective_source_type": effective_type,
                    "canonical_format": canonical_format,
                    "requested_format": requested_format,
                }
            )
        )
    return effective_type, requested_format


def _add_unique(row: dict[str, Any], key: str, value: str) -> None:
    row[key] = sorted(
        {
            *[_text(item) for item in row.get(key) or [] if _text(item)],
            value,
        }
    )


def _neutralize_canonical(
    canonical: dict[str, Any], interpretation_asset_id: str
) -> bool:
    changed = False
    canonical_key = f"interpretation:{interpretation_asset_id}"
    if _text(canonical.get("logical_key")) != canonical_key:
        canonical.setdefault("source_occurrence_logical_key", canonical.get("logical_key"))
        canonical["logical_key"] = canonical_key
        changed = True
    if canonical.get("archive_provenance"):
        canonical.setdefault(
            "source_occurrence_archive_provenance",
            dict(canonical.get("archive_provenance") or {}),
        )
        canonical["archive_provenance"] = {}
        changed = True
    if _text(canonical.get("external_ref")):
        canonical.setdefault("source_occurrence_external_ref", canonical.get("external_ref"))
        canonical["external_ref"] = ""
        changed = True
    canonical["identity_role"] = "CANONICAL_INTERPRETATION"
    canonical["interpretation_asset_id"] = interpretation_asset_id
    canonical["source_path_controls_canonical_lifecycle"] = False
    return changed


def _prepare_existing_canonical_identities(registry: dict[str, Any]) -> bool:
    changed = False
    source_by_id = {
        _text(row.get("source_id")): row
        for row in registry.get("sources") or []
        if isinstance(row, dict) and _text(row.get("source_id"))
    }
    interpretation_by_id = {
        _text(row.get("canonical_source_id")): _text(row.get("interpretation_asset_id"))
        for row in registry.get("interpretation_assets") or []
        if isinstance(row, dict)
        and _text(row.get("canonical_source_id"))
        and _text(row.get("interpretation_asset_id"))
    }
    for source_id, interpretation_id in interpretation_by_id.items():
        canonical = source_by_id.get(source_id)
        if canonical is not None:
            changed = _neutralize_canonical(canonical, interpretation_id) or changed
    return changed


def _upsert_content_asset(
    registry: dict[str, Any], canonical: dict[str, Any], occurrence_id: str
) -> str:
    content_hash = _text(canonical.get("content_hash"))
    identity = _content_asset_id(content_hash)
    rows = _registry_rows(registry, "content_assets")
    asset = next((row for row in rows if row.get("content_asset_id") == identity), None)
    if asset is None:
        asset = {
            "schema": CONTENT_ASSET_SCHEMA,
            "content_asset_id": identity,
            "content_hash": content_hash,
            "stored_path": _text(canonical.get("stored_path")),
            "canonical_source_ids": [],
            "source_occurrence_ids": [],
            "created_at_utc": _now(),
            "immutable_bytes": True,
            "historical_bytes_retained_without_active_occurrence": True,
        }
        rows.append(asset)
    _add_unique(asset, "canonical_source_ids", _text(canonical.get("source_id")))
    _add_unique(asset, "source_occurrence_ids", occurrence_id)
    asset["status"] = "ACTIVE"
    return identity


def _upsert_interpretation_asset(
    registry: dict[str, Any],
    *,
    canonical: dict[str, Any],
    content_asset_id: str,
    interpretation_asset_id: str,
    source_type: str,
    fmt: str,
    occurrence_id: str,
    source_ref: str,
) -> None:
    rows = _registry_rows(registry, "interpretation_assets")
    asset = next(
        (row for row in rows if row.get("interpretation_asset_id") == interpretation_asset_id),
        None,
    )
    source_id = _text(canonical.get("source_id"))
    if asset is None:
        parse = dict(canonical.get("parse") or {})
        asset = {
            "schema": INTERPRETATION_ASSET_SCHEMA,
            "interpretation_asset_id": interpretation_asset_id,
            "content_asset_id": content_asset_id,
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
            f"INTERPRETATION_CANONICAL_SOURCE_CONFLICT:{interpretation_asset_id}"
        )
    _add_unique(asset, "source_occurrence_ids", occurrence_id)
    _add_unique(asset, "source_refs", source_ref)
    asset["status"] = "ACTIVE"


def _unlink_occurrence(registry: dict[str, Any], occurrence: dict[str, Any]) -> None:
    occurrence_id = _text(occurrence.get("source_occurrence_id"))
    source_ref = _text(occurrence.get("source_ref"))
    for key in ("content_assets", "interpretation_assets"):
        for row in registry.get(key) or []:
            if not isinstance(row, dict):
                continue
            row["source_occurrence_ids"] = [
                value
                for value in row.get("source_occurrence_ids") or []
                if _text(value) != occurrence_id
            ]
            if key == "interpretation_assets":
                row["source_refs"] = [
                    value
                    for value in row.get("source_refs") or []
                    if _text(value) != source_ref
                ]
    canonical = _source_by_id(registry, _text(occurrence.get("canonical_source_id")))
    if canonical is not None:
        canonical["source_occurrence_ids"] = [
            value
            for value in canonical.get("source_occurrence_ids") or []
            if _text(value) != occurrence_id
        ]
        canonical["source_refs"] = [
            value
            for value in canonical.get("source_refs") or []
            if _text(value) != source_ref
        ]


def _reactivate_canonical_if_needed(
    *,
    project: str,
    root: Path,
    actor: dict[str, Any],
    canonical: dict[str, Any],
) -> None:
    if canonical.get("status") == "active":
        return
    parsed = _crud._record_parse(canonical, root)
    if str(parsed.get("parse_status") or "") == "failed":
        raise SourceOccurrenceIngestionError(
            f"CANONICAL_INTERPRETATION_REACTIVATION_PARSE_FAILED:{canonical.get('source_id')}"
        )
    runtime_manifest, runtime_error = _crud._register_runtime_source(
        project=project,
        root=root,
        runtime_asset_id=_text(canonical.get("runtime_asset_id"))
        or _crud._runtime_asset_id(_text(canonical.get("logical_key"))),
        filename=_text(canonical.get("original_name")) or "document",
        source_type=_text(canonical.get("source_type")) or "other_document",
        source_id=_text(canonical.get("source_id")),
        content_hash=_text(canonical.get("content_hash")),
        version=int(canonical.get("version") or 1),
        external_ref="",
        parsed=parsed,
        actor=actor,
        archive_provenance={},
    )
    if runtime_error:
        raise SourceOccurrenceIngestionError(
            "CANONICAL_INTERPRETATION_RUNTIME_REACTIVATION_FAILED:"
            + _text(runtime_error.get("detail") or runtime_error.get("code"))
        )
    chunk_receipt, chunk_warning = _crud._register_chunks(
        project=project,
        root=root,
        source_id=_text(canonical.get("source_id")),
        content_hash=_text(canonical.get("content_hash")),
        version=int(canonical.get("version") or 1),
        parsed=parsed,
        runtime_manifest=runtime_manifest,
    )
    if chunk_warning and str(chunk_receipt.get("status") or "") == "FAILED":
        raise SourceOccurrenceIngestionError(
            "CANONICAL_INTERPRETATION_CHUNK_REACTIVATION_FAILED:"
            + _text(chunk_warning.get("detail") or chunk_warning.get("code"))
        )
    canonical["runtime_source_manifest"] = runtime_manifest
    canonical["parse"] = _crud._parse_summary(parsed, chunk_receipt, runtime_manifest)
    canonical["status"] = "active"
    canonical["reactivated_at_utc"] = _now()


def deactivate_unreferenced_canonical_sources(
    project_id: str,
    canonical_source_ids: Iterable[str],
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    reason: str = "no_active_source_occurrences",
) -> dict[str, Any]:
    """Remove orphan interpretations from active understanding while retaining source bytes."""
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    registry = _load_registry(project, resolved_root)
    occurrences = _registry_rows(registry, "source_occurrences")
    deactivated: list[str] = []
    errors: list[dict[str, Any]] = []
    for source_id in sorted({_text(value) for value in canonical_source_ids if _text(value)}):
        if any(
            row.get("status") == "active"
            and _text(row.get("canonical_source_id")) == source_id
            for row in occurrences
        ):
            continue
        canonical = _source_by_id(registry, source_id)
        if canonical is None or canonical.get("status") != "active":
            continue
        runtime_asset_id = _text(canonical.get("runtime_asset_id"))
        try:
            runtime_receipt = deactivate_source_asset(
                project,
                runtime_asset_id,
                root=resolved_root,
                actor=clean_actor,
                reason=reason,
            )
            if runtime_receipt.get("deactivated") is not True:
                raise RuntimeError(_text(runtime_receipt.get("reason")) or "runtime source not deactivated")
            canonical["status"] = "superseded"
            canonical["superseded_reason"] = reason
            canonical["superseded_at_utc"] = _now()
            canonical["historical_source_bytes_retained"] = True
            canonical["chunk_index_retained_for_reactivation"] = True
            deactivated.append(source_id)
        except Exception as exc:
            errors.append(
                {
                    "code": "ORPHAN_CANONICAL_INTERPRETATION_DEACTIVATION_FAILED",
                    "canonical_source_id": source_id,
                    "detail": f"{type(exc).__name__}: {exc}"[:500],
                    "blocks_formal_understanding": True,
                }
            )
    registry.setdefault("audit_events", []).append(
        {
            "event": "deactivate_unreferenced_canonical_interpretations",
            "at_utc": _now(),
            "actor": clean_actor,
            "canonical_source_ids": deactivated,
            "reason": reason,
            "historical_source_bytes_retained": True,
            "error_count": len(errors),
        }
    )
    _save_registry(project, resolved_root, registry)
    return {
        "status": "BLOCKED" if errors else "PASS",
        "deactivated_canonical_source_ids": deactivated,
        "historical_source_bytes_retained": True,
        "errors": errors,
    }


def _reactivate_existing_occurrence(
    registry: dict[str, Any],
    *,
    canonical: dict[str, Any],
    occurrence: dict[str, Any],
    actor: dict[str, Any],
) -> None:
    previous_status = _text(occurrence.get("status")) or "unknown"
    occurrence_id = _text(occurrence.get("source_occurrence_id"))
    source_ref = _text(occurrence.get("source_ref"))
    content_id = _upsert_content_asset(registry, canonical, occurrence_id)
    _upsert_interpretation_asset(
        registry,
        canonical=canonical,
        content_asset_id=content_id,
        interpretation_asset_id=_text(occurrence.get("interpretation_asset_id")),
        source_type=_text(occurrence.get("source_type")),
        fmt=_text(occurrence.get("format_identity")),
        occurrence_id=occurrence_id,
        source_ref=source_ref,
    )
    _add_unique(canonical, "source_occurrence_ids", occurrence_id)
    _add_unique(canonical, "source_refs", source_ref)
    history = [
        dict(row)
        for row in occurrence.get("lifecycle_history") or []
        if isinstance(row, dict)
    ]
    history.append(
        {
            "status": previous_status,
            "ended_at_utc": _now(),
            "reason": _text(
                occurrence.get("retired_reason")
                or occurrence.get("deleted_reason")
                or occurrence.get("superseded_reason")
            ),
        }
    )
    occurrence["lifecycle_history"] = history[-50:]
    occurrence["status"] = "active"
    occurrence["reactivated_at_utc"] = _now()
    occurrence["reactivated_by"] = dict(actor)
    occurrence["reactivated_from_status"] = previous_status
    for key in (
        "retired_at_utc",
        "retired_by",
        "retired_reason",
        "retirement_evidence",
        "deleted_at_utc",
        "deleted_by",
        "deleted_reason",
        "superseded_at_utc",
        "superseded_by_occurrence_id",
        "superseded_reason",
    ):
        occurrence.pop(key, None)
    canonical["content_asset_id"] = content_id
    canonical["active_source_occurrence_count"] = sum(
        item.get("status") == "active"
        and _text(item.get("canonical_source_id")) == _text(canonical.get("source_id"))
        for item in _registry_rows(registry, "source_occurrences")
    )


def _supersede_active_source_ref_peers(
    registry: dict[str, Any],
    *,
    source_ref: str,
    winning_occurrence_id: str,
    reason: str,
) -> set[str]:
    orphan_candidates: set[str] = set()
    for item in _registry_rows(registry, "source_occurrences"):
        if (
            item.get("status") != "active"
            or _portable_ref(item.get("source_ref")) != source_ref
            or _text(item.get("source_occurrence_id")) == winning_occurrence_id
        ):
            continue
        item["status"] = "superseded"
        item["superseded_at_utc"] = _now()
        item["superseded_by_occurrence_id"] = winning_occurrence_id
        item["superseded_reason"] = reason
        orphan_candidates.add(_text(item.get("canonical_source_id")))
        _unlink_occurrence(registry, item)
    return orphan_candidates


def _register_occurrence(
    registry: dict[str, Any],
    *,
    project: str,
    root: Path,
    actor: dict[str, Any],
    canonical: dict[str, Any],
    result_row: dict[str, Any],
    envelope: dict[str, Any],
) -> tuple[dict[str, Any], bool, set[str]]:
    source_id = _text(canonical.get("source_id"))
    content_hash = _text(canonical.get("content_hash"))
    if not source_id or not content_hash:
        raise SourceOccurrenceIngestionError(
            f"CANONICAL_SOURCE_IDENTITY_INCOMPLETE:{source_id or 'unknown'}"
        )
    source_type, fmt = _interpretation(canonical, result_row, envelope)
    source_ref = _source_ref(result_row, envelope)
    if not source_ref:
        raise SourceOccurrenceIngestionError(f"SOURCE_REF_MISSING:{source_id}")
    interpretation_id = _interpretation_asset_id(content_hash, source_type, fmt)
    _neutralize_canonical(canonical, interpretation_id)
    _reactivate_canonical_if_needed(
        project=project,
        root=root,
        actor=actor,
        canonical=canonical,
    )
    occurrence_id = _source_occurrence_id(project, source_ref, content_hash, interpretation_id)
    occurrences = _registry_rows(registry, "source_occurrences")
    existing = next(
        (row for row in occurrences if row.get("source_occurrence_id") == occurrence_id),
        None,
    )
    if existing is not None:
        orphan_candidates = _supersede_active_source_ref_peers(
            registry,
            source_ref=source_ref,
            winning_occurrence_id=occurrence_id,
            reason="source_content_reverted_to_prior_occurrence",
        )
        if existing.get("status") != "active":
            _reactivate_existing_occurrence(
                registry,
                canonical=canonical,
                occurrence=existing,
                actor=actor,
            )
        existing["content_reversion_reconciled"] = bool(orphan_candidates)
        existing["single_active_source_ref_invariant"] = True
        orphan_candidates.discard(source_id)
        return existing, False, orphan_candidates

    orphan_candidates = _supersede_active_source_ref_peers(
        registry,
        source_ref=source_ref,
        winning_occurrence_id=occurrence_id,
        reason="source_occurrence_superseded",
    )
    version = max(
        [
            int(item.get("version") or 0)
            for item in occurrences
            if _portable_ref(item.get("source_ref")) == source_ref
        ],
        default=0,
    ) + 1
    content_id = _content_asset_id(content_hash)
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
        "source_occurrence_logical_key": _text(canonical.get("source_occurrence_logical_key")),
        "archive_provenance": dict(result_row.get("archive_provenance") or {}),
        "version": version,
        "status": "active",
        "created_at_utc": _now(),
        "created_by": dict(actor),
        "parse_reused": result_row.get("reason") == "same_content_hash",
        "independent_evidence_identity": True,
        "absolute_workspace_path_is_identity": False,
        "single_active_source_ref_invariant": True,
    }
    occurrences.append(occurrence)
    content_id = _upsert_content_asset(registry, canonical, occurrence_id)
    _upsert_interpretation_asset(
        registry,
        canonical=canonical,
        content_asset_id=content_id,
        interpretation_asset_id=interpretation_id,
        source_type=source_type,
        fmt=fmt,
        occurrence_id=occurrence_id,
        source_ref=source_ref,
    )
    canonical["content_asset_id"] = content_id
    _add_unique(canonical, "source_occurrence_ids", occurrence_id)
    _add_unique(canonical, "source_refs", source_ref)
    canonical["active_source_occurrence_count"] = sum(
        item.get("status") == "active"
        and _text(item.get("canonical_source_id")) == source_id
        for item in occurrences
    )
    orphan_candidates.discard(source_id)
    return occurrence, True, orphan_candidates


def _register_result_rows(
    *,
    project: str,
    root: Path,
    actor: dict[str, Any],
    result_rows: list[tuple[dict[str, Any], bool, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    registry = _load_registry(project, root)
    created_occurrences: list[dict[str, Any]] = []
    duplicate_occurrences: list[dict[str, Any]] = []
    orphan_candidates: set[str] = set()
    for result_row, canonical_created, envelope in result_rows:
        canonical = _canonical_for_result(registry, result_row)
        occurrence, occurrence_created, newly_orphaned = _register_occurrence(
            registry,
            project=project,
            root=root,
            actor=actor,
            canonical=canonical,
            result_row=result_row,
            envelope=envelope,
        )
        orphan_candidates.update(newly_orphaned)
        projected = {**dict(occurrence), "canonical_source_was_created": canonical_created}
        (created_occurrences if occurrence_created else duplicate_occurrences).append(projected)

    registry.setdefault("governance", {}).update(
        {
            "content_identity_separate_from_source_occurrence": True,
            "interpretation_identity_separate_from_content_identity": True,
            "same_interpretation_content_parsed_once": True,
            "different_interpretation_content_reuse_fails_closed": True,
            "source_occurrence_identity_authority": "SOURCE_OCCURRENCE_REGISTRY",
            "canonical_interpretations_are_provenance_neutral": True,
            "source_paths_control_only_occurrence_lifecycle": True,
            "source_occurrence_batch_registration_authority": (
                "source_occurrence_core"
            ),
            "retired_occurrence_reactivation_preserves_identity": True,
            "single_active_occurrence_per_source_ref": True,
            "content_reversion_reuses_prior_occurrence_identity": True,
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
            "reactivated_source_occurrence_ids": [
                row["source_occurrence_id"]
                for row in duplicate_occurrences
                if row.get("reactivated_at_utc")
            ],
            "content_reversion_occurrence_ids": [
                row["source_occurrence_id"]
                for row in duplicate_occurrences
                if row.get("content_reversion_reconciled")
            ],
            "batch_registration": len(result_rows) > 1,
        }
    )
    _save_registry(project, root, registry)
    cleanup = deactivate_unreferenced_canonical_sources(
        project,
        orphan_candidates,
        root=root,
        actor=actor,
        reason="source_occurrence_superseded",
    )
    return created_occurrences, duplicate_occurrences, list(cleanup.get("errors") or [])


def _register_child_result(
    *,
    project: str,
    root: Path,
    actor: dict[str, Any],
    envelope: dict[str, Any],
    child: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    result_rows = [
        (dict(row), True, envelope)
        for row in child.get("created") or []
        if isinstance(row, dict)
    ] + [
        (dict(row), False, envelope)
        for row in child.get("duplicates") or []
        if isinstance(row, dict)
    ]
    return _register_result_rows(
        project=project,
        root=root,
        actor=actor,
        result_rows=result_rows,
    )


def _reconcile_archive_occurrences(
    *,
    project: str,
    root: Path,
    actor: dict[str, Any],
    envelope: dict[str, Any],
    previous_refs: set[str],
    current_refs: set[str],
) -> dict[str, Any]:
    stale_refs = sorted(previous_refs - current_refs)
    if not stale_refs:
        return {
            "status": "PASS",
            "archive_ref": _archive_base_ref(envelope),
            "retired_source_occurrence_ids": [],
            "historical_source_bytes_retained": True,
            "errors": [],
        }
    registry = _load_registry(project, root)
    orphan_candidates: set[str] = set()
    retired: list[str] = []
    for occurrence in _registry_rows(registry, "source_occurrences"):
        if occurrence.get("status") != "active" or _text(occurrence.get("source_ref")) not in stale_refs:
            continue
        occurrence["status"] = "retired_archive_member"
        occurrence["retired_at_utc"] = _now()
        occurrence["retired_reason"] = "member_absent_from_new_archive_version"
        retired.append(_text(occurrence.get("source_occurrence_id")))
        orphan_candidates.add(_text(occurrence.get("canonical_source_id")))
        _unlink_occurrence(registry, occurrence)
    registry.setdefault("audit_events", []).append(
        {
            "event": "reconcile_archive_source_occurrences",
            "at_utc": _now(),
            "actor": actor,
            "archive_ref": _archive_base_ref(envelope),
            "stale_source_refs": stale_refs,
            "retired_source_occurrence_ids": retired,
            "historical_source_bytes_retained": True,
        }
    )
    _save_registry(project, root, registry)
    cleanup = deactivate_unreferenced_canonical_sources(
        project,
        orphan_candidates,
        root=root,
        actor=actor,
        reason="archive_source_occurrence_removed",
    )
    return {
        "status": "BLOCKED" if cleanup.get("errors") else "PASS",
        "archive_ref": _archive_base_ref(envelope),
        "retired_source_occurrence_ids": retired,
        "historical_source_bytes_retained": True,
        "errors": list(cleanup.get("errors") or []),
    }


def _merge_child(aggregate: dict[str, Any], child: dict[str, Any]) -> None:
    for key in (
        "created",
        "duplicates",
        "errors",
        "warnings",
        "rolled_back_archives",
        "archive_reconciliations",
    ):
        aggregate.setdefault(key, []).extend(
            dict(row) for row in child.get(key) or [] if isinstance(row, dict)
        )
    source = dict(child.get("archive_expansion") or {})
    target = aggregate["archive_expansion"]
    for key in ("packages", "errors", "warnings"):
        target.setdefault(key, []).extend(
            dict(row) for row in source.get(key) or [] if isinstance(row, dict)
        )
    for key in ("document_count", "package_count", "error_count", "warning_count"):
        target[key] = int(target.get(key) or 0) + int(source.get(key) or 0)
    target["status"] = (
        "BLOCKED"
        if target.get("errors")
        else "PARTIAL"
        if target.get("warnings")
        else "COMPLETE"
    )


def ingest_enterprise_knowledge_document_batch(
    project_id: str,
    documents: list[dict[str, Any]],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ingest one ordinary-document batch through the canonical atomic authority.

    The batch requires a unique stable ``external_ref`` per document and rejects archive
    transports. It exists for connector snapshots, where thousands of ordinary remote
    documents must share one CRUD transaction and one occurrence-registry commit.
    """
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    if not isinstance(documents, list) or not documents:
        raise SourceOccurrenceIngestionError(
            "SOURCE_OCCURRENCE_BATCH_DOCUMENTS_REQUIRED"
        )
    envelopes: list[dict[str, Any]] = []
    by_ref: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(documents):
        if not isinstance(raw, dict):
            raise SourceOccurrenceIngestionError(
                f"SOURCE_OCCURRENCE_BATCH_ENVELOPE_INVALID:{index}"
            )
        envelope = dict(raw)
        if _atomic._is_archive_transport(envelope):
            raise SourceOccurrenceIngestionError(
                f"SOURCE_OCCURRENCE_BATCH_ARCHIVE_UNSUPPORTED:{index}"
            )
        source_ref = _portable_ref(envelope.get("external_ref"))
        if not source_ref:
            raise SourceOccurrenceIngestionError(
                f"SOURCE_OCCURRENCE_BATCH_SOURCE_REF_REQUIRED:{index}"
            )
        if source_ref in by_ref:
            raise SourceOccurrenceIngestionError(
                f"SOURCE_OCCURRENCE_BATCH_SOURCE_REF_DUPLICATE:{source_ref}"
            )
        by_ref[source_ref] = envelope
        envelopes.append(envelope)

    registry_before = _load_registry(project, resolved_root)
    if _prepare_existing_canonical_identities(registry_before):
        _save_registry(project, resolved_root, registry_before)

    child = _atomic.ingest_enterprise_knowledge_documents(
        project,
        envelopes,
        root=resolved_root,
        actor=clean_actor,
    )
    aggregate: dict[str, Any] = {
        "schema": SOURCE_OCCURRENCE_INGESTION_SCHEMA,
        "ok": not child.get("errors"),
        "phase": PHASE,
        "project_id": project,
        "created": [dict(row) for row in child.get("created") or [] if isinstance(row, dict)],
        "duplicates": [dict(row) for row in child.get("duplicates") or [] if isinstance(row, dict)],
        "errors": [dict(row) for row in child.get("errors") or [] if isinstance(row, dict)],
        "warnings": [dict(row) for row in child.get("warnings") or [] if isinstance(row, dict)],
        "source_occurrences": [],
        "duplicate_source_occurrences": [],
        "source_occurrence_reconciliations": [],
        "rolled_back_archives": [],
        "archive_reconciliations": [],
        "archive_expansion": dict(child.get("archive_expansion") or {}),
    }
    if not aggregate["errors"]:
        rows: list[tuple[dict[str, Any], bool, dict[str, Any]]] = []
        for key, canonical_created in (("created", True), ("duplicates", False)):
            for raw in child.get(key) or []:
                if not isinstance(raw, dict):
                    continue
                row = dict(raw)
                source_ref = _portable_ref(row.get("external_ref"))
                envelope = by_ref.get(source_ref)
                if envelope is None:
                    raise SourceOccurrenceIngestionError(
                        f"SOURCE_OCCURRENCE_BATCH_RESULT_REF_UNMAPPED:{source_ref or 'unknown'}"
                    )
                rows.append((row, canonical_created, envelope))
        created, duplicates, registration_errors = _register_result_rows(
            project=project,
            root=resolved_root,
            actor=clean_actor,
            result_rows=rows,
        )
        aggregate["source_occurrences"].extend(created)
        aggregate["duplicate_source_occurrences"].extend(duplicates)
        aggregate["errors"].extend(registration_errors)

    registry = _load_registry(project, resolved_root)
    active_sources = [
        row
        for row in registry.get("sources") or []
        if isinstance(row, dict) and row.get("status") == "active"
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
            "content_asset_count": len(_registry_rows(registry, "content_assets")),
            "interpretation_asset_count": len(
                _registry_rows(registry, "interpretation_assets")
            ),
            "ok": not aggregate["errors"],
            "rebuild_recommended": bool(
                aggregate["created"] or aggregate["source_occurrences"]
            ),
            "content_identity_separate_from_source_occurrence": True,
            "interpretation_identity_separate_from_content_identity": True,
            "same_interpretation_content_parsed_once": True,
            "different_interpretation_content_reuse_fails_closed": True,
            "canonical_interpretations_are_provenance_neutral": True,
            "historical_source_bytes_retained": True,
            "atomic_transport_authority": "atomic_ingestion",
            "canonical_document_activation_authority": "_crud",
            "batch_transport_transaction": True,
        }
    )
    return aggregate


def ingest_enterprise_knowledge_documents(
    project_id: str,
    documents: list[dict[str, Any]],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one atomic transport per envelope and register all resulting occurrences."""
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
        "source_occurrence_reconciliations": [],
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
        registry_before = _load_registry(project, resolved_root)
        if _prepare_existing_canonical_identities(registry_before):
            _save_registry(project, resolved_root, registry_before)
        archive_transport = _atomic._is_archive_transport(envelope)
        prefix = _archive_prefix(envelope) if archive_transport else ""
        previous_archive_refs = {
            _text(row.get("source_ref"))
            for row in registry_before.get("source_occurrences") or []
            if isinstance(row, dict)
            and row.get("status") == "active"
            and prefix
            and _text(row.get("source_ref")).startswith(prefix)
        }

        child = _atomic.ingest_enterprise_knowledge_documents(
            project, [envelope], root=resolved_root, actor=clean_actor
        )
        _merge_child(aggregate, child)
        if child.get("errors"):
            continue
        try:
            created, duplicates, registration_errors = _register_child_result(
                project=project,
                root=resolved_root,
                actor=clean_actor,
                envelope=envelope,
                child=child,
            )
            aggregate["source_occurrences"].extend(created)
            aggregate["duplicate_source_occurrences"].extend(duplicates)
            aggregate["errors"].extend(registration_errors)
            if archive_transport:
                current_refs = {
                    _text(row.get("source_ref"))
                    for row in [*created, *duplicates]
                    if _text(row.get("source_ref"))
                }
                reconciliation = _reconcile_archive_occurrences(
                    project=project,
                    root=resolved_root,
                    actor=clean_actor,
                    envelope=envelope,
                    previous_refs=previous_archive_refs,
                    current_refs=current_refs,
                )
                aggregate["source_occurrence_reconciliations"].append(reconciliation)
                aggregate["errors"].extend(reconciliation.get("errors") or [])
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
        row
        for row in registry.get("sources") or []
        if isinstance(row, dict) and row.get("status") == "active"
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
            "content_asset_count": len(_registry_rows(registry, "content_assets")),
            "interpretation_asset_count": len(_registry_rows(registry, "interpretation_assets")),
            "ok": not aggregate["errors"],
            "rebuild_recommended": bool(
                aggregate["created"]
                or aggregate["source_occurrences"]
                or aggregate["source_occurrence_reconciliations"]
            ),
            "content_identity_separate_from_source_occurrence": True,
            "interpretation_identity_separate_from_content_identity": True,
            "same_interpretation_content_parsed_once": True,
            "different_interpretation_content_reuse_fails_closed": True,
            "canonical_interpretations_are_provenance_neutral": True,
            "historical_source_bytes_retained": True,
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
    "deactivate_unreferenced_canonical_sources",
    "ingest_enterprise_knowledge_document_batch",
    "ingest_enterprise_knowledge_documents",
    "ingest_enterprise_knowledge_files",
]
