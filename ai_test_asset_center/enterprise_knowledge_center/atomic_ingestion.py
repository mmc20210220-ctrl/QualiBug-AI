"""Public atomic composition root for enterprise material ingestion.

The canonical CRUD transaction remains the only authority for one document. This module adds
one missing transport invariant: members of a top-level archive activate as a group. If any
member fails formal parsing or runtime registration, every new member from that package is
rolled back and the previously active corpus is restored, including retrieval chunks.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Iterable

from ..enterprise_material_formats import (
    inspect_pk_document_container,
    is_declared_archive_transport,
    is_declared_document_container,
)
from ..enterprise_source_registry_lifecycle import rollback_source_asset_activation
from . import _crud
from ._common import PHASE, ROOT, _safe_project_id
from ._utils import (
    _load_registry,
    _now,
    _paths,
    _require_manage_actor,
    _save_registry,
)

ATOMIC_INGESTION_SCHEMA = "qualibug.atomic-enterprise-material-ingestion.v1"
_ARCHIVE_SIGNATURES = (
    b"PK\x03\x04",
    b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07",
    b"\x1f\x8b",
)


def _envelope_filename(row: dict[str, Any]) -> str:
    filename = str(row.get("filename") or row.get("name") or "")
    if not filename and row.get("file_path"):
        filename = Path(str(row.get("file_path"))).name
    return filename


def _bounded_envelope_bytes(row: dict[str, Any], limit: int = 100 * 1024 * 1024) -> bytes:
    raw = row.get("content_bytes")
    if isinstance(raw, (bytes, bytearray, memoryview)):
        value = bytes(raw)
        return value if len(value) <= limit else b""
    path = Path(str(row.get("file_path"))) if row.get("file_path") else None
    if path is None or not path.exists() or not path.is_file():
        return b""
    try:
        if path.stat().st_size > limit:
            return b""
        return path.read_bytes()
    except OSError:
        return b""


def _is_archive_transport(row: dict[str, Any]) -> bool:
    filename = _envelope_filename(row)
    if is_declared_archive_transport(filename):
        return True
    if is_declared_document_container(filename):
        return False
    data = _bounded_envelope_bytes(row)
    if not data or inspect_pk_document_container(data):
        return False
    return data.startswith(_ARCHIVE_SIGNATURES)


def _remove_new_record_artifacts(root: Path, record: dict[str, Any]) -> list[str]:
    removed = _crud._remove_record_chunk_index(root, record)
    stored_path = str(record.get("stored_path") or "")
    if stored_path:
        target = root / stored_path
        try:
            target.resolve().relative_to(root.resolve())
            if target.exists() and target.is_file():
                target.unlink()
                removed.append(str(target.relative_to(root)).replace("\\", "/"))
        except (OSError, ValueError):
            pass
    return removed


def _restore_previous_chunks(
    *,
    project: str,
    root: Path,
    previous_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for record in previous_records:
        try:
            parsed = _crud._record_parse(record, root)
            if str(parsed.get("parse_status") or "") == "failed":
                raise RuntimeError("previous source could not be reparsed")
            manifest = dict(record.get("runtime_source_manifest") or {})
            receipt, warning = _crud._register_chunks(
                project=project,
                root=root,
                source_id=str(record.get("source_id") or ""),
                content_hash=str(record.get("content_hash") or ""),
                version=int(record.get("version") or 0),
                parsed=parsed,
                runtime_manifest=manifest,
            )
            if warning or str(receipt.get("status") or "") not in {"REGISTERED", "EMPTY"}:
                raise RuntimeError(str((warning or {}).get("detail") or receipt.get("status") or "chunk restore failed"))
        except Exception as exc:
            errors.append(
                {
                    "stage": "archive_activation_rollback",
                    "code": "ARCHIVE_PREVIOUS_CHUNK_INDEX_RESTORE_FAILED",
                    "source_id": str(record.get("source_id") or ""),
                    "detail": f"{type(exc).__name__}: {exc}"[:500],
                    "severity": "P0",
                    "blocks_formal_understanding": True,
                    "silent_failure_allowed": False,
                }
            )
    return errors


def _rollback_archive_activation(
    *,
    project: str,
    root: Path,
    actor: dict[str, Any],
    before_registry: dict[str, Any],
    failed_result: dict[str, Any],
    archive_filename: str,
) -> dict[str, Any]:
    created = [dict(row) for row in failed_result.get("created") or [] if isinstance(row, dict)]
    previous_active = {
        str(row.get("logical_key") or ""): dict(row)
        for row in before_registry.get("sources") or []
        if isinstance(row, dict)
        and row.get("status") == "active"
        and str(row.get("logical_key") or "")
    }
    affected_previous: list[dict[str, Any]] = []
    rollback_errors: list[dict[str, Any]] = []
    removed_paths: list[str] = []

    for record in created:
        logical_key = str(record.get("logical_key") or "")
        previous = previous_active.get(logical_key)
        if previous is not None and all(
            str(row.get("source_id") or "") != str(previous.get("source_id") or "")
            for row in affected_previous
        ):
            affected_previous.append(previous)
        manifest = dict(record.get("runtime_source_manifest") or {})
        if str(manifest.get("status") or "") == "REGISTERED":
            previous_manifest = dict((previous or {}).get("runtime_source_manifest") or {})
            try:
                rollback_source_asset_activation(
                    project,
                    str(manifest.get("source_id") or record.get("runtime_asset_id") or ""),
                    root=root,
                    actor=actor,
                    restore_source_hash=str(previous_manifest.get("source_hash") or ""),
                    restore_version_id=str(
                        previous_manifest.get("source_version_id")
                        or previous_manifest.get("version_id")
                        or ""
                    ),
                    reason="archive_member_activation_transaction_rolled_back",
                )
            except Exception as exc:
                rollback_errors.append(
                    {
                        "stage": "archive_activation_rollback",
                        "code": "ARCHIVE_RUNTIME_SOURCE_ROLLBACK_FAILED",
                        "source_id": str(record.get("source_id") or ""),
                        "detail": f"{type(exc).__name__}: {exc}"[:500],
                        "severity": "P0",
                        "blocks_formal_understanding": True,
                        "silent_failure_allowed": False,
                    }
                )
        removed_paths.extend(_remove_new_record_artifacts(root, record))

    rollback_errors.extend(
        _restore_previous_chunks(
            project=project,
            root=root,
            previous_records=affected_previous,
        )
    )

    restored = copy.deepcopy(before_registry)
    restored.setdefault("audit_events", []).append(
        {
            "event": "archive_activation_rollback",
            "at_utc": _now(),
            "actor": actor,
            "archive_filename": archive_filename,
            "rolled_back_source_ids": [
                str(row.get("source_id") or "") for row in created
            ],
            "restored_source_ids": [
                str(row.get("source_id") or "") for row in affected_previous
            ],
            "removed_paths": sorted(set(removed_paths)),
            "rollback_error_count": len(rollback_errors),
            "immutable_runtime_versions_retained": True,
        }
    )
    _save_registry(project, root, restored)

    return {
        "schema": "qualibug.archive-activation-rollback.v1",
        "status": "PARTIAL" if rollback_errors else "COMPLETE",
        "archive_filename": archive_filename,
        "rolled_back_source_ids": [str(row.get("source_id") or "") for row in created],
        "restored_source_ids": [str(row.get("source_id") or "") for row in affected_previous],
        "removed_paths": sorted(set(removed_paths)),
        "errors": rollback_errors,
        "archive_members_active_after_rollback": False,
        "immutable_runtime_versions_retained": True,
    }


def _merge_archive_expansion(target: dict[str, Any], value: dict[str, Any]) -> None:
    for key in ("packages", "errors", "warnings"):
        target.setdefault(key, []).extend(
            dict(row) for row in value.get(key) or [] if isinstance(row, dict)
        )
    for key in ("document_count", "package_count", "error_count", "warning_count"):
        target[key] = int(target.get(key) or 0) + int(value.get(key) or 0)
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
    """Ingest ordinary documents normally and each top-level archive atomically."""

    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    ordinary: list[dict[str, Any]] = []
    archives: list[dict[str, Any]] = []
    invalid: list[Any] = []
    for raw in documents or []:
        if not isinstance(raw, dict):
            invalid.append(raw)
        elif _is_archive_transport(raw):
            archives.append(dict(raw))
        else:
            ordinary.append(dict(raw))

    aggregate: dict[str, Any] = {
        "schema": ATOMIC_INGESTION_SCHEMA,
        "ok": True,
        "phase": PHASE,
        "project_id": project,
        "created": [],
        "duplicates": [],
        "errors": [],
        "warnings": [],
        "rolled_back_archives": [],
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
    for _row in invalid:
        aggregate["errors"].append(
            {
                "code": "DOCUMENT_ENVELOPE_INVALID",
                "detail": "document envelope must be object",
            }
        )

    if ordinary:
        result = _crud.ingest_enterprise_knowledge_documents(
            project,
            ordinary,
            root=resolved_root,
            actor=clean_actor,
        )
        for key in ("created", "duplicates", "errors", "warnings"):
            aggregate[key].extend(result.get(key) or [])
        _merge_archive_expansion(
            aggregate["archive_expansion"],
            dict(result.get("archive_expansion") or {}),
        )

    for archive in archives:
        before = copy.deepcopy(_load_registry(project, resolved_root))
        result = _crud.ingest_enterprise_knowledge_documents(
            project,
            [archive],
            root=resolved_root,
            actor=clean_actor,
        )
        _merge_archive_expansion(
            aggregate["archive_expansion"],
            dict(result.get("archive_expansion") or {}),
        )
        archive_filename = _envelope_filename(archive)
        if result.get("errors"):
            rollback = _rollback_archive_activation(
                project=project,
                root=resolved_root,
                actor=clean_actor,
                before_registry=before,
                failed_result=result,
                archive_filename=archive_filename,
            )
            aggregate["rolled_back_archives"].append(rollback)
            aggregate["errors"].extend(result.get("errors") or [])
            aggregate["errors"].extend(rollback.get("errors") or [])
            aggregate["warnings"].extend(result.get("warnings") or [])
            continue
        aggregate["created"].extend(result.get("created") or [])
        aggregate["duplicates"].extend(result.get("duplicates") or [])
        aggregate["warnings"].extend(result.get("warnings") or [])

    final_inventory = _crud.list_enterprise_knowledge_sources(
        project,
        root=resolved_root,
        include_deleted=False,
    )
    aggregate["source_count"] = int(
        (final_inventory.get("summary") or {}).get("active_source_count") or 0
    )
    aggregate["ok"] = not aggregate["errors"]
    aggregate["rebuild_recommended"] = bool(aggregate["created"])
    aggregate["archive_activation_atomic"] = True
    aggregate["ordinary_document_transaction_unchanged"] = True
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
            {"file_path": str(path), "source_type": hints.get(str(path))}
            for path in file_paths
        ],
        root=root,
        actor=actor,
    )


__all__ = [
    "ATOMIC_INGESTION_SCHEMA",
    "ingest_enterprise_knowledge_documents",
    "ingest_enterprise_knowledge_files",
]
