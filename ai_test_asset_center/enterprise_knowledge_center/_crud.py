"""Canonical enterprise knowledge CRUD and atomic ingestion transaction."""
from __future__ import annotations

import copy
import os
import tempfile
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from ._common import PHASE, ROOT, SOURCE_TYPES, _safe_project_id
from ._parsing import _classify_source
from ._utils import (
    _hash_bytes,
    _load_registry,
    _now,
    _parser_receipt,
    _paths,
    _read_source_bytes,
    _require_manage_actor,
    _safe_slug,
    _save_registry,
    _short_hash,
)
from .archive_expansion import (
    expand_document_envelopes,
    read_document_envelope_bytes,
)
from .source_ingestion import (
    build_document_ir_retrieval_chunks,
    parse_enterprise_source,
)
from ..enterprise_source_registry import register_source_asset
from ..enterprise_source_registry_lifecycle import (
    deactivate_source_asset,
    rollback_source_asset_activation,
)

__all__ = [
    "_logical_key",
    "_record_parse",
    "delete_enterprise_knowledge_source",
    "ingest_enterprise_knowledge_documents",
    "ingest_enterprise_knowledge_files",
    "list_enterprise_knowledge_sources",
    "operate_enterprise_knowledge_center",
    "update_enterprise_knowledge_source",
]


def _empty_parse_result(
    *,
    source_id: str,
    filename: str,
    source_type: str,
    error: dict[str, Any],
) -> dict[str, Any]:
    return {
        "text": "",
        "payload": None,
        "openapi": {},
        "operations": [],
        "tables": [],
        "field_dictionary": [],
        "ui_specs": [],
        "message_chain_contracts": [],
        "permissions": [],
        "tickets": [],
        "har_errors": [],
        "log_errors": [],
        "rules": [],
        "roles": [],
        "state_machines": [],
        "parse_status": "failed",
        "parser": "none",
        "text_hash": "",
        "text_length": 0,
        "parse_errors": [error],
        "document_ir_status": "BLOCKED",
        "document_ir": {},
        "document_structure": {},
        "parser_receipt": _parser_receipt(
            source_id=source_id,
            filename=filename,
            source_type=source_type,
            parser="none",
            detected_format=Path(filename).suffix.lstrip(".") or "unknown",
            text_hash="",
            text_length=0,
            outputs={},
            errors=[error],
            parse_status="failed",
            started_at_utc=_now(),
        ),
    }


def _record_parse(record: dict[str, Any], root: Path) -> dict[str, Any]:
    stored = (root / str(record.get("stored_path") or "")).resolve()
    root_resolved = root.resolve()
    if root_resolved != stored and root_resolved not in stored.parents:
        return _empty_parse_result(
            source_id=str(record.get("source_id") or ""),
            filename=str(record.get("original_name") or "document"),
            source_type=str(record.get("source_type") or "other_document"),
            error={
                "stage": "parse",
                "code": "SOURCE_PATH_OUTSIDE_ROOT",
                "retryability": "after_registry_repair",
                "operator_action": "repair the canonical source registry path",
            },
        )
    source_id = str(record.get("source_id") or "")
    filename = str(record.get("original_name") or stored.name)
    source_type = str(record.get("source_type") or "other_document")
    if not stored.exists() or not stored.is_file():
        return _empty_parse_result(
            source_id=source_id,
            filename=filename,
            source_type=source_type,
            error={
                "stage": "parse",
                "code": "SOURCE_BYTES_MISSING",
                "identity": source_id,
                "retryability": "after_source_restore",
                "operator_action": (
                    "restore the immutable source blob or register a new source version"
                ),
            },
        )
    return parse_enterprise_source(
        stored.read_bytes(), filename, source_type, source_id
    )


def _logical_key(name: str, source_type: str) -> str:
    return f"{source_type}:{_safe_slug(Path(name).stem, 72).lower()}"


def _logical_key_for_envelope(
    name: str,
    source_type: str,
    envelope: dict[str, Any],
) -> str:
    provenance = envelope.get("archive_provenance")
    if not isinstance(provenance, dict):
        return _logical_key(name, source_type)
    package_name = str(provenance.get("top_level_archive_name") or "archive")
    virtual_path = str(provenance.get("virtual_member_path") or name)
    package_key = _safe_slug(Path(package_name).stem, 48).lower()
    member_key = _short_hash({"virtual_member_path": virtual_path}, 24)
    return f"{source_type}:archive:{package_key}:{member_key}"


def _relative_archive_receipt_paths(
    root: Path,
    archive_expansion: dict[str, Any],
) -> dict[str, Any]:
    result = dict(archive_expansion or {})
    packages: list[dict[str, Any]] = []
    for raw in result.get("packages") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        stored_path = str(row.get("stored_path") or "")
        if stored_path:
            try:
                row["stored_path"] = Path(stored_path).resolve().relative_to(
                    root.resolve()
                ).as_posix()
            except (OSError, ValueError):
                row["stored_path"] = ""
                row["stored_path_outside_project_root"] = True
        packages.append(row)
    result["packages"] = packages
    return result


def _parse_summary(
    parsed: dict[str, Any],
    chunk_receipt: dict[str, Any],
    runtime_manifest: dict[str, Any],
) -> dict[str, Any]:
    receipt = dict(parsed.get("parser_receipt") or {})
    evidence = dict(receipt.get("evidence_closure_receipt") or {})
    projection = dict(parsed.get("semantic_projection_receipt") or {})
    document_ir = (
        parsed.get("document_ir")
        if isinstance(parsed.get("document_ir"), dict)
        else {}
    )
    return {
        "parser": str(parsed.get("parser") or receipt.get("parser") or "none"),
        "parse_status": str(parsed.get("parse_status") or "failed"),
        "formal_status": str(parsed.get("document_ir_status") or "UNKNOWN"),
        "text_hash": str(parsed.get("text_hash") or ""),
        "text_length": int(parsed.get("text_length") or 0),
        "operation_count": len(parsed.get("operations") or []),
        "table_count": len(parsed.get("tables") or []),
        "field_count": len(parsed.get("field_dictionary") or []),
        "ui_spec_count": len(parsed.get("ui_specs") or []),
        "permission_count": len(parsed.get("permissions") or []),
        "rule_count": len(parsed.get("rules") or []),
        "ticket_count": len(parsed.get("tickets") or []),
        "document_ir_block_count": len(document_ir.get("blocks") or []),
        "document_ir_table_count": len(document_ir.get("tables") or []),
        "semantic_projection_table_count": int(
            projection.get("projected_table_count") or 0
        ),
        "evidence_exact_address_rate": evidence.get("exact_address_rate"),
        "chunk_count": int(chunk_receipt.get("chunk_count") or 0),
        "chunk_exact_address_rate": chunk_receipt.get("exact_address_rate"),
        "fidelity": str(receipt.get("fidelity") or "unknown"),
        "errors": list(parsed.get("parse_errors") or []),
        "receipt": receipt,
        "chunk_index": dict(chunk_receipt or {}),
        "runtime_source_manifest": dict(runtime_manifest or {}),
    }


def _chunk_index_file(
    root: Path,
    chunk_receipt: dict[str, Any],
) -> Path | None:
    relative = str(chunk_receipt.get("chunk_index_path") or "").strip()
    if not relative:
        return None
    candidate = (root / relative).resolve()
    root_resolved = root.resolve()
    if root_resolved != candidate and root_resolved not in candidate.parents:
        return None
    return candidate


def _remove_record_chunk_index(
    root: Path,
    record: dict[str, Any],
) -> list[str]:
    receipt = dict(((record.get("parse") or {}).get("chunk_index") or {}))
    target = _chunk_index_file(root, receipt)
    if target is None:
        return []
    removed: list[str] = []
    try:
        if target.exists() and target.is_file():
            target.unlink()
            removed.append(target.relative_to(root.resolve()).as_posix())
        parent = target.parent
        if parent.exists() and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        return removed
    return removed


def _runtime_asset_id(logical_key: str) -> str:
    return "knowledge_" + _short_hash({"logical_key": logical_key}, 24)


def _register_runtime_source(
    *,
    project: str,
    root: Path,
    runtime_asset_id: str,
    filename: str,
    source_type: str,
    source_id: str,
    content_hash: str,
    version: int,
    external_ref: str,
    parsed: dict[str, Any],
    actor: dict[str, Any],
    archive_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    projection = str(parsed.get("text") or "")
    receipt = dict(parsed.get("parser_receipt") or {})
    evidence = dict(receipt.get("evidence_closure_receipt") or {})
    manifest = register_source_asset(
        project,
        runtime_asset_id,
        projection,
        source_type=source_type,
        root=root,
        actor=actor,
        origin="enterprise_knowledge_center_document_ir",
        filename=filename,
        external_ref=external_ref,
        metadata={
            "knowledge_source_id": source_id,
            "knowledge_source_version": version,
            "original_content_hash": content_hash,
            "document_ir_format": str(
                (parsed.get("document_ir") or {}).get("format") or ""
            ),
            "document_ir_status": str(
                parsed.get("document_ir_status") or "UNKNOWN"
            ),
            "parser_receipt_id": str(receipt.get("receipt_id") or ""),
            "evidence_exact_address_rate": evidence.get("exact_address_rate"),
            "projection_schema": str(
                (parsed.get("semantic_projection_receipt") or {}).get("schema")
                or ""
            ),
            "archive_provenance": dict(archive_provenance or {}),
        },
    )
    if not isinstance(manifest, dict):
        raise TypeError("runtime source registration must return an object")
    runtime_source_id = str(manifest.get("source_id") or "")
    runtime_source_hash = str(manifest.get("source_hash") or "")
    if not runtime_source_id or not runtime_source_hash:
        raise ValueError("runtime source registration returned incomplete identity")
    return {
        **manifest,
        "status": "REGISTERED",
        "runtime_asset_id": runtime_asset_id,
        "original_content_hash": content_hash,
        "knowledge_source_id": source_id,
    }


def _register_chunks(
    *,
    project: str,
    root: Path,
    source_id: str,
    content_hash: str,
    version: int,
    parsed: dict[str, Any],
    runtime_manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    chunks, receipt = build_document_ir_retrieval_chunks(
        parsed,
        source_id=source_id,
        source_hash=content_hash,
        source_version=version,
    )
    runtime_source_id = str(runtime_manifest.get("source_id") or "")
    runtime_source_hash = str(runtime_manifest.get("source_hash") or "")
    for chunk in chunks:
        chunk["runtime_source_id"] = runtime_source_id
        chunk["runtime_source_hash"] = runtime_source_hash
    receipt["runtime_source_id"] = runtime_source_id
    receipt["runtime_source_hash"] = runtime_source_hash
    if not chunks:
        warning = {
            "stage": "retrieval_index",
            "code": "DOCUMENT_IR_RETRIEVAL_CHUNKS_EMPTY",
            "identity": source_id,
            "retryability": "after_source_or_projection_fix",
            "operator_action": (
                "inspect Document IR blocks and semantic projection receipt"
            ),
            "severity": "P1",
            "blocks_formal_understanding": False,
        }
        return {**receipt, "status": "EMPTY"}, warning
    from ..enterprise_source_registry import register_source_chunks

    persisted = register_source_chunks(
        project,
        source_id,
        content_hash,
        chunks,
        root=root,
    )
    if not isinstance(persisted, dict):
        raise TypeError("source chunk registration must return an object")
    return {**receipt, **persisted, "status": "REGISTERED"}, None


def _write_blob_atomic(target: Path, blob: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(blob)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        if target.exists():
            raise FileExistsError(f"immutable source target already exists: {target}")
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _ingest_error(
    *,
    index: int,
    filename: str,
    code: str,
    detail: str,
) -> dict[str, Any]:
    return {
        "index": index,
        "filename": filename,
        "code": code,
        "error": detail[:500],
        "severity": "P0",
        "blocks_formal_understanding": True,
        "silent_failure_allowed": False,
    }


def _blocked_result(
    *,
    project: str,
    registry: dict[str, Any],
    duplicates: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    archive_expansion: dict[str, Any],
    transaction_id: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "phase": PHASE,
        "project_id": project,
        "transaction_id": transaction_id,
        "transaction_status": "BLOCKED",
        "created": [],
        "duplicates": duplicates,
        "errors": errors,
        "warnings": warnings,
        "archive_expansion": archive_expansion,
        "source_count": len(
            [row for row in registry["sources"] if row.get("status") == "active"]
        ),
        "rebuild_recommended": False,
        "partial_activation_allowed": False,
        "activated_source_count": 0,
    }


def ingest_enterprise_knowledge_documents(
    project_id: str,
    documents: list[dict[str, Any]],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically validate and activate one document/archive batch.

    Phase 1 performs archive expansion, byte reads, source classification and
    formal parsing for the entire batch without activating any source. Phase 2
    activates every prepared source. Any activation or registry commit failure
    rolls back this batch's runtime registrations, chunk indexes and immutable
    source files in reverse order; prior active versions remain authoritative.
    """

    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    paths = _paths(project, resolved_root)
    paths["source_dir"].mkdir(parents=True, exist_ok=True)
    registry = _load_registry(project, resolved_root)
    original_registry = copy.deepcopy(registry)
    active = [
        row for row in registry["sources"] if row.get("status") == "active"
    ]
    transaction_id = "kitx_" + uuid.uuid4().hex
    duplicates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    archive_batch = expand_document_envelopes(
        documents or [],
        package_store_dir=paths["workspace"] / "packages",
    )
    archive_expansion = _relative_archive_receipt_paths(
        resolved_root,
        archive_batch.to_dict(),
    )
    errors.extend(
        dict(row) for row in archive_batch.errors if isinstance(row, dict)
    )
    warnings.extend(
        dict(row) for row in archive_batch.warnings if isinstance(row, dict)
    )
    if errors:
        registry["audit_events"].append(
            {
                "event": "ingest_blocked",
                "at_utc": _now(),
                "actor": clean_actor,
                "transaction_id": transaction_id,
                "reason": "archive_expansion_failed",
                "error_count": len(errors),
                "partial_activation_allowed": False,
            }
        )
        _save_registry(project, resolved_root, registry)
        return _blocked_result(
            project=project,
            registry=registry,
            duplicates=duplicates,
            errors=errors,
            warnings=warnings,
            archive_expansion=archive_expansion,
            transaction_id=transaction_id,
        )

    prepared: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    seen_logical_keys: set[str] = set()
    for index, raw_document in enumerate(archive_batch.documents):
        if not isinstance(raw_document, dict):
            errors.append(
                _ingest_error(
                    index=index,
                    filename="",
                    code="DOCUMENT_ENVELOPE_INVALID",
                    detail="document envelope must be an object",
                )
            )
            break
        document = dict(raw_document)
        filename = str(
            document.get("filename")
            or document.get("name")
            or "enterprise_source.bin"
        )
        try:
            if document.get("content_bytes") is not None:
                blob, detected_name, raw_text = read_document_envelope_bytes(document)
            else:
                file_path = (
                    Path(str(document.get("file_path")))
                    if document.get("file_path")
                    else None
                )
                inline_text = (
                    str(document.get("text"))
                    if document.get("text") is not None
                    else None
                )
                blob, detected_name, raw_text = _read_source_bytes(
                    file_path,
                    inline_text,
                )
            filename = str(
                document.get("filename")
                or document.get("name")
                or detected_name
            )
            source_type = _classify_source(
                filename,
                raw_text,
                str(document.get("source_type") or ""),
            )
            content_hash = _hash_bytes(blob)
            duplicate = next(
                (
                    row
                    for row in registry["sources"]
                    if row.get("status") in {"active", "superseded"}
                    and row.get("content_hash") == content_hash
                ),
                None,
            )
            batch_duplicate = next(
                (
                    row
                    for row in prepared
                    if row.get("content_hash") == content_hash
                ),
                None,
            )
            canonical_duplicate = duplicate or batch_duplicate
            if canonical_duplicate is not None:
                duplicates.append(
                    {
                        "filename": filename,
                        "source_id": canonical_duplicate.get("source_id"),
                        "content_hash": content_hash,
                        "reason": "same_content_hash",
                        "source_type": source_type,
                        "external_ref": str(document.get("external_ref") or ""),
                        "archive_provenance": dict(
                            document.get("archive_provenance") or {}
                        ),
                    }
                )
                continue
            logical_key = _logical_key_for_envelope(
                filename,
                source_type,
                document,
            )
            if logical_key in seen_logical_keys:
                raise ValueError(
                    f"BATCH_LOGICAL_KEY_COLLISION:{logical_key}"
                )
            versions = [
                int(row.get("version") or 0)
                for row in registry["sources"]
                if row.get("logical_key") == logical_key
            ]
            version = max(versions, default=0) + 1
            source_id = "src_" + _short_hash(
                {
                    "project": project,
                    "hash": content_hash,
                    "logical_key": logical_key,
                    "version": version,
                }
            )
            parsed = parse_enterprise_source(
                blob,
                filename,
                source_type,
                source_id,
            )
            if str(parsed.get("parse_status") or "") == "failed":
                detail = next(
                    (
                        str(row.get("detail") or row.get("code") or "")
                        for row in parsed.get("parse_errors") or []
                        if isinstance(row, dict)
                    ),
                    "formal document parsing was blocked",
                )
                raise ValueError(f"SOURCE_FORMAL_PARSE_BLOCKED:{detail}")
            formal_status = str(
                parsed.get("document_ir_status") or "UNKNOWN"
            ).upper()
            if formal_status in {"BLOCKED", "FAILED"}:
                raise ValueError(
                    f"SOURCE_FORMAL_PARSE_BLOCKED:{formal_status}"
                )
            storage_name = (
                f"{source_id}_v{version}_{_safe_slug(filename)}"
            )
            final_path = (paths["source_dir"] / storage_name).resolve()
            source_root = paths["source_dir"].resolve()
            if source_root != final_path and source_root not in final_path.parents:
                raise ValueError("SOURCE_STORAGE_PATH_OUTSIDE_SOURCE_ROOT")
            previous_active = next(
                (row for row in active if row.get("logical_key") == logical_key),
                None,
            )
            prepared.append(
                {
                    "index": index,
                    "document": document,
                    "blob": blob,
                    "filename": filename,
                    "source_type": source_type,
                    "content_hash": content_hash,
                    "logical_key": logical_key,
                    "version": version,
                    "source_id": source_id,
                    "parsed": parsed,
                    "runtime_asset_id": _runtime_asset_id(logical_key),
                    "final_path": final_path,
                    "previous_active": previous_active,
                    "previous_runtime_manifest": dict(
                        (previous_active or {}).get("runtime_source_manifest")
                        or {}
                    ),
                }
            )
            seen_hashes.add(content_hash)
            seen_logical_keys.add(logical_key)
        except Exception as exc:
            errors.append(
                _ingest_error(
                    index=index,
                    filename=filename,
                    code=str(exc).split(":", 1)[0]
                    if str(exc).split(":", 1)[0].isupper()
                    else "SOURCE_PREPARATION_FAILED",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            break

    if errors:
        registry["audit_events"].append(
            {
                "event": "ingest_blocked",
                "at_utc": _now(),
                "actor": clean_actor,
                "transaction_id": transaction_id,
                "reason": "batch_preparation_failed",
                "error_count": len(errors),
                "prepared_source_count": len(prepared),
                "partial_activation_allowed": False,
            }
        )
        _save_registry(project, resolved_root, registry)
        return _blocked_result(
            project=project,
            registry=registry,
            duplicates=duplicates,
            errors=errors,
            warnings=warnings,
            archive_expansion=archive_expansion,
            transaction_id=transaction_id,
        )

    activated: list[dict[str, Any]] = []
    created: list[dict[str, Any]] = []
    superseded_after_commit: list[dict[str, Any]] = []
    rollback_errors: list[dict[str, Any]] = []
    try:
        for item in prepared:
            document = item["document"]
            runtime_manifest = _register_runtime_source(
                project=project,
                root=resolved_root,
                runtime_asset_id=item["runtime_asset_id"],
                filename=item["filename"],
                source_type=item["source_type"],
                source_id=item["source_id"],
                content_hash=item["content_hash"],
                version=item["version"],
                external_ref=str(document.get("external_ref") or "")[:500],
                parsed=item["parsed"],
                actor=clean_actor,
                archive_provenance=dict(
                    document.get("archive_provenance") or {}
                ),
            )
            item["runtime_manifest"] = runtime_manifest
            item["record"] = {
                "source_id": item["source_id"],
                "logical_key": item["logical_key"],
                "original_name": item["filename"],
                "source_type": item["source_type"],
                "version": item["version"],
                "content_hash": item["content_hash"],
                "status": "activating",
                "tags": [
                    str(value)[:80]
                    for value in (document.get("tags") or [])
                    if str(value).strip()
                ][:20],
                "external_ref": str(document.get("external_ref") or "")[:500],
                "archive_provenance": dict(
                    document.get("archive_provenance") or {}
                ),
                "stored_path": item["final_path"].relative_to(
                    resolved_root
                ).as_posix(),
                "created_at_utc": _now(),
                "created_by": clean_actor,
                "runtime_asset_id": item["runtime_asset_id"],
                "runtime_source_manifest": runtime_manifest,
            }
            activated.append(item)

            chunk_receipt, chunk_warning = _register_chunks(
                project=project,
                root=resolved_root,
                source_id=item["source_id"],
                content_hash=item["content_hash"],
                version=item["version"],
                parsed=item["parsed"],
                runtime_manifest=runtime_manifest,
            )
            if chunk_warning:
                warnings.append(
                    {
                        "index": item["index"],
                        "filename": item["filename"],
                        **chunk_warning,
                    }
                )
                item["parsed"].setdefault("parse_errors", []).append(
                    chunk_warning
                )
            item["record"]["parse"] = _parse_summary(
                item["parsed"],
                chunk_receipt,
                runtime_manifest,
            )
            _write_blob_atomic(item["final_path"], item["blob"])
            item["record"]["status"] = "active"

        for item in activated:
            previous = item.get("previous_active")
            if isinstance(previous, dict):
                previous["status"] = "superseded"
                previous["superseded_at_utc"] = _now()
                previous["superseded_by"] = item["source_id"]
                superseded_after_commit.append(previous)
            registry["sources"].append(item["record"])
            created.append(item["record"])

        governance = registry.setdefault("governance", {})
        if archive_expansion.get("package_count"):
            governance["original_archive_packages_retained"] = True
            governance[
                "archive_members_expanded_without_archive_controlled_path_writes"
            ] = True
            governance["archive_security_failures_are_visible"] = True
        governance["knowledge_ingest_atomic_batch"] = True
        registry["audit_events"].append(
            {
                "event": "ingest_committed",
                "at_utc": _now(),
                "actor": clean_actor,
                "transaction_id": transaction_id,
                "created_source_ids": [row["source_id"] for row in created],
                "duplicate_count": len(duplicates),
                "warning_count": len(warnings),
                "archive_package_count": int(
                    archive_expansion.get("package_count") or 0
                ),
                "archive_hashes": [
                    str(row.get("archive_hash") or "")
                    for row in archive_expansion.get("packages") or []
                    if isinstance(row, dict)
                    and str(row.get("archive_hash") or "")
                ],
                "partial_activation_allowed": False,
            }
        )
        _save_registry(project, resolved_root, registry)
    except Exception as exc:
        for item in reversed(activated):
            record = item.get("record") if isinstance(item.get("record"), dict) else {}
            _remove_record_chunk_index(resolved_root, record)
            final_path = item.get("final_path")
            if isinstance(final_path, Path):
                final_path.unlink(missing_ok=True)
            runtime_manifest = item.get("runtime_manifest")
            if not isinstance(runtime_manifest, dict):
                continue
            try:
                rollback_source_asset_activation(
                    project,
                    str(runtime_manifest.get("source_id") or ""),
                    root=resolved_root,
                    actor=clean_actor,
                    restore_source_hash=str(
                        item.get("previous_runtime_manifest", {}).get(
                            "source_hash"
                        )
                        or ""
                    ),
                    restore_version_id=str(
                        item.get("previous_runtime_manifest", {}).get(
                            "source_version_id"
                        )
                        or ""
                    ),
                    reason="knowledge_ingest_batch_rolled_back",
                )
            except Exception as rollback_exc:
                rollback_errors.append(
                    {
                        "source_id": item.get("source_id"),
                        "code": "SOURCE_RUNTIME_ROLLBACK_FAILED",
                        "detail": (
                            f"{type(rollback_exc).__name__}: {rollback_exc}"
                        )[:500],
                        "severity": "P0",
                        "silent_failure_allowed": False,
                    }
                )
        errors.append(
            _ingest_error(
                index=int(activated[-1].get("index") or 0)
                if activated
                else 0,
                filename=str(activated[-1].get("filename") or "")
                if activated
                else "",
                code="INGEST_BATCH_ACTIVATION_FAILED",
                detail=f"{type(exc).__name__}: {exc}",
            )
        )
        errors.extend(rollback_errors)
        failed_registry = original_registry
        failed_registry["audit_events"].append(
            {
                "event": "ingest_rolled_back",
                "at_utc": _now(),
                "actor": clean_actor,
                "transaction_id": transaction_id,
                "activated_before_failure": len(activated),
                "rollback_error_count": len(rollback_errors),
                "partial_activation_allowed": False,
            }
        )
        _save_registry(project, resolved_root, failed_registry)
        return {
            **_blocked_result(
                project=project,
                registry=failed_registry,
                duplicates=duplicates,
                errors=errors,
                warnings=warnings,
                archive_expansion=archive_expansion,
                transaction_id=transaction_id,
            ),
            "transaction_status": "ROLLED_BACK",
            "rollback_complete": not rollback_errors,
        }

    for previous in superseded_after_commit:
        removed = _remove_record_chunk_index(resolved_root, previous)
        if not removed:
            continue
    return {
        "ok": True,
        "phase": PHASE,
        "project_id": project,
        "transaction_id": transaction_id,
        "transaction_status": "COMMITTED",
        "created": created,
        "duplicates": duplicates,
        "errors": [],
        "warnings": warnings,
        "archive_expansion": archive_expansion,
        "source_count": len(
            [row for row in registry["sources"] if row.get("status") == "active"]
        ),
        "rebuild_recommended": bool(created),
        "partial_activation_allowed": False,
        "activated_source_count": len(created),
    }


def ingest_enterprise_knowledge_files(
    project_id: str,
    file_paths: Iterable[str | Path],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    source_type_hints: dict[str, str] | None = None,
) -> dict[str, Any]:
    hints = source_type_hints or {}
    documents = [
        {
            "file_path": str(path),
            "source_type": hints.get(str(path)),
        }
        for path in file_paths
    ]
    return ingest_enterprise_knowledge_documents(
        project_id,
        documents,
        root=root,
        actor=actor,
    )


def list_enterprise_knowledge_sources(
    project_id: str,
    root: Path | None = None,
    include_deleted: bool = False,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    registry = _load_registry(project, resolved_root)
    sources = (
        registry["sources"]
        if include_deleted
        else [
            row for row in registry["sources"] if row.get("status") == "active"
        ]
    )
    return {
        "phase": PHASE,
        "project_id": project,
        "sources": sources,
        "summary": {
            "active_source_count": len(
                [
                    row
                    for row in registry["sources"]
                    if row.get("status") == "active"
                ]
            ),
            "superseded_source_count": len(
                [
                    row
                    for row in registry["sources"]
                    if row.get("status") == "superseded"
                ]
            ),
            "failed_source_count": len(
                [
                    row
                    for row in registry["sources"]
                    if row.get("status") == "failed"
                ]
            ),
            "deleted_source_count": len(
                [
                    row
                    for row in registry["sources"]
                    if row.get("status") == "deleted"
                ]
            ),
            "source_type_distribution": dict(
                Counter(
                    str(row.get("source_type") or "unknown")
                    for row in sources
                )
            ),
        },
        "governance": registry.get("governance") or {},
    }


def update_enterprise_knowledge_source(
    project_id: str,
    source_id: str,
    patch: dict[str, Any],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    registry = _load_registry(project, resolved_root)
    record = next(
        (
            row
            for row in registry["sources"]
            if row.get("source_id") == source_id
            and row.get("status") == "active"
        ),
        None,
    )
    if not record:
        raise KeyError(f"active source not found: {source_id}")
    if "tags" in patch:
        record["tags"] = [
            str(value)[:80]
            for value in (patch.get("tags") or [])
            if str(value).strip()
        ][:20]
    if "source_type" in patch:
        source_type = str(patch.get("source_type") or "").lower()
        if source_type not in SOURCE_TYPES:
            raise ValueError("unsupported source_type")
        record["source_type"] = source_type
        record["logical_key"] = _logical_key_for_envelope(
            str(record.get("original_name") or "document"),
            source_type,
            {"archive_provenance": record.get("archive_provenance") or {}},
        )
    if "external_ref" in patch:
        record["external_ref"] = str(patch.get("external_ref") or "")[:500]
    record["updated_at_utc"] = _now()
    record["updated_by"] = clean_actor
    registry["audit_events"].append(
        {
            "event": "update_metadata",
            "at_utc": _now(),
            "actor": clean_actor,
            "source_id": source_id,
            "fields": sorted(
                set(patch).intersection(
                    {"tags", "source_type", "external_ref"}
                )
            ),
        }
    )
    _save_registry(project, resolved_root, registry)
    return {"ok": True, "source": record, "rebuild_recommended": True}


def delete_enterprise_knowledge_source(
    project_id: str,
    source_id: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    purge_bytes: bool = False,
) -> dict[str, Any]:
    del purge_bytes
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    registry = _load_registry(project, resolved_root)
    record_index = next(
        (
            index
            for index, row in enumerate(registry["sources"])
            if row.get("source_id") == source_id
            and row.get("status") == "active"
        ),
        None,
    )
    record = (
        registry["sources"][record_index]
        if record_index is not None
        else None
    )
    if not record:
        raise KeyError(f"active source not found: {source_id}")

    runtime_asset_id = str(record.get("runtime_asset_id") or "") or _runtime_asset_id(
        str(record.get("logical_key") or "")
    )
    runtime_deactivation = deactivate_source_asset(
        project,
        runtime_asset_id,
        root=resolved_root,
        actor=clean_actor,
        reason="knowledge_source_deleted",
    )
    if not isinstance(runtime_deactivation, dict) or runtime_deactivation.get(
        "deactivated"
    ) is False:
        raise RuntimeError("runtime source deactivation failed")

    removed_paths: list[str] = []
    stored_path = str(record.get("stored_path") or "")
    if stored_path:
        candidate = (resolved_root / stored_path).resolve()
        if (
            resolved_root != candidate
            and resolved_root not in candidate.parents
        ):
            raise ValueError("stored source path escaped project root")
        if candidate.exists() and candidate.is_file():
            candidate.unlink()
            removed_paths.append(candidate.relative_to(resolved_root).as_posix())
    removed_paths.extend(_remove_record_chunk_index(resolved_root, record))
    registry["sources"].pop(record_index)
    registry["audit_events"].append(
        {
            "event": "delete",
            "at_utc": _now(),
            "actor": clean_actor,
            "source_id": source_id,
            "original_name": str(record.get("original_name") or ""),
            "removed_paths": removed_paths,
            "physical_delete": True,
        }
    )
    _save_registry(project, resolved_root, registry)
    return {
        "ok": True,
        "source_id": source_id,
        "original_name": str(record.get("original_name") or ""),
        "purged_bytes": bool(removed_paths),
        "removed_paths": removed_paths,
        "runtime_source_deactivation": runtime_deactivation,
        "rebuild_recommended": True,
    }


def operate_enterprise_knowledge_center(
    project_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request = payload if isinstance(payload, dict) else {}
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    operation = str(action or "view").strip().lower()
    if operation in {"view", "list"}:
        from ._api import load_enterprise_business_knowledge_asset

        return {
            "ok": True,
            "action": "view",
            "inventory": list_enterprise_knowledge_sources(
                project,
                resolved_root,
                include_deleted=bool(request.get("include_deleted")),
            ),
            "asset": load_enterprise_business_knowledge_asset(
                project, resolved_root
            )
            or {},
        }
    if operation in {"upload", "ingest"}:
        documents = (
            request.get("documents")
            if isinstance(request.get("documents"), list)
            else []
        )
        result = ingest_enterprise_knowledge_documents(
            project,
            documents,
            root=resolved_root,
            actor=actor,
        )
        return {
            "ok": bool(result.get("ok")),
            "action": "upload",
            "result": result,
        }
    if operation in {"edit", "update"}:
        result = update_enterprise_knowledge_source(
            project,
            str(request.get("source_id") or ""),
            request.get("patch") or {},
            root=resolved_root,
            actor=actor,
        )
        return {"ok": True, "action": "edit", "result": result}
    if operation in {"delete", "remove"}:
        result = delete_enterprise_knowledge_source(
            project,
            str(request.get("source_id") or ""),
            root=resolved_root,
            actor=actor,
            purge_bytes=bool(request.get("purge_bytes")),
        )
        return {"ok": True, "action": "delete", "result": result}
    if operation in {"rebuild", "build"}:
        from ._api import build_enterprise_business_knowledge_asset

        asset = build_enterprise_business_knowledge_asset(
            project,
            resolved_root,
            options=request.get("options")
            if isinstance(request.get("options"), dict)
            else None,
        )
        return {"ok": True, "action": "rebuild", "asset": asset}
    raise ValueError(
        "unsupported knowledge center action; use view, upload, edit, delete or rebuild"
    )
