"""Knowledge center CRUD: ingest, list, update, delete, operate."""
from __future__ import annotations

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
    stored = root / str(record.get("stored_path") or "")
    source_id = str(record.get("source_id") or "")
    filename = str(record.get("original_name") or stored.name)
    source_type = str(record.get("source_type") or "other_document")
    if not stored.exists():
        error = {
            "stage": "parse",
            "code": "SOURCE_BYTES_MISSING",
            "identity": source_id,
            "retryability": "after_source_restore",
            "operator_action": "restore the immutable source blob or register a new source version",
        }
        return _empty_parse_result(
            source_id=source_id,
            filename=filename,
            source_type=source_type,
            error=error,
        )
    return parse_enterprise_source(stored.read_bytes(), filename, source_type, source_id)


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
                row["stored_path"] = str(
                    Path(stored_path).resolve().relative_to(root.resolve())
                ).replace("\\", "/")
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
        "document_ir_block_count": len((parsed.get("document_ir") or {}).get("blocks") or []),
        "document_ir_table_count": len((parsed.get("document_ir") or {}).get("tables") or []),
        "semantic_projection_table_count": int(projection.get("projected_table_count") or 0),
        "evidence_exact_address_rate": evidence.get("exact_address_rate"),
        "chunk_count": int(chunk_receipt.get("chunk_count") or 0),
        "chunk_exact_address_rate": chunk_receipt.get("exact_address_rate"),
        "fidelity": str(receipt.get("fidelity") or "unknown"),
        "errors": list(parsed.get("parse_errors") or []),
        "receipt": receipt,
        "chunk_index": dict(chunk_receipt or {}),
        "runtime_source_manifest": dict(runtime_manifest or {}),
    }


def _chunk_index_file(root: Path, chunk_receipt: dict[str, Any]) -> Path | None:
    relative = str(chunk_receipt.get("chunk_index_path") or "").strip()
    if not relative:
        return None
    candidate = root / relative
    try:
        candidate.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def _remove_record_chunk_index(root: Path, record: dict[str, Any]) -> list[str]:
    receipt = dict(((record.get("parse") or {}).get("chunk_index") or {}))
    target = _chunk_index_file(root, receipt)
    if target is None:
        return []
    removed: list[str] = []
    try:
        if target.exists() and target.is_file():
            target.unlink()
            removed.append(str(target.relative_to(root)).replace("\\", "/"))
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
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    projection = str(parsed.get("text") or "")
    receipt = dict(parsed.get("parser_receipt") or {})
    evidence = dict(receipt.get("evidence_closure_receipt") or {})
    try:
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
                "document_ir_format": str(parsed.get("document_ir", {}).get("format") or ""),
                "document_ir_status": str(parsed.get("document_ir_status") or "UNKNOWN"),
                "parser_receipt_id": str(receipt.get("receipt_id") or ""),
                "evidence_exact_address_rate": evidence.get("exact_address_rate"),
                "projection_schema": str(
                    (parsed.get("semantic_projection_receipt") or {}).get("schema") or ""
                ),
                "archive_provenance": dict(archive_provenance or {}),
            },
        )
        return {
            **manifest,
            "status": "REGISTERED",
            "runtime_asset_id": runtime_asset_id,
            "original_content_hash": content_hash,
            "knowledge_source_id": source_id,
        }, None
    except Exception as exc:
        error = {
            "stage": "runtime_source_registry",
            "code": "SOURCE_RUNTIME_REGISTRATION_FAILED",
            "identity": source_id,
            "retryability": "after_source_registry_or_projection_fix",
            "operator_action": "inspect the canonical enterprise source registry",
            "detail": f"{type(exc).__name__}: {exc}"[:500],
            "severity": "P0",
            "blocks_formal_understanding": True,
        }
        return {
            "status": "FAILED",
            "runtime_asset_id": runtime_asset_id,
            "original_content_hash": content_hash,
            "knowledge_source_id": source_id,
            "error": error["detail"],
        }, error


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
            "operator_action": "inspect Document IR blocks and semantic projection receipt",
            "severity": "P1",
            "blocks_formal_understanding": False,
        }
        return {**receipt, "status": "EMPTY"}, warning
    try:
        from ..enterprise_source_registry import register_source_chunks

        persisted = register_source_chunks(
            project,
            source_id,
            content_hash,
            chunks,
            root=root,
        )
        return {**receipt, **persisted, "status": "REGISTERED"}, None
    except Exception as exc:
        warning = {
            "stage": "retrieval_index",
            "code": "DOCUMENT_IR_RETRIEVAL_INDEX_FAILED",
            "identity": source_id,
            "retryability": "after_registry_fix",
            "operator_action": "inspect the source chunk registry",
            "detail": f"{type(exc).__name__}: {exc}"[:500],
            "severity": "P1",
            "blocks_formal_understanding": False,
        }
        return {**receipt, "status": "FAILED", "error": warning["detail"]}, warning


def ingest_enterprise_knowledge_documents(
    project_id: str,
    documents: list[dict[str, Any]],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ingest local documents and archive members into one canonical source transaction.

    Accepted envelope fields include file_path, text, content_bytes, filename/name,
    source_type, tags and external_ref. Archive packages are retained as audit containers;
    only their safely expanded members become business sources. No remote URL is fetched.
    """

    root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    paths = _paths(project, root)
    paths["source_dir"].mkdir(parents=True, exist_ok=True)
    registry = _load_registry(project, root)
    active = [row for row in registry["sources"] if row.get("status") == "active"]
    created: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    archive_batch = expand_document_envelopes(
        documents or [],
        package_store_dir=paths["workspace"] / "packages",
    )
    archive_expansion = _relative_archive_receipt_paths(root, archive_batch.to_dict())
    errors.extend(dict(row) for row in archive_batch.errors if isinstance(row, dict))
    warnings.extend(dict(row) for row in archive_batch.warnings if isinstance(row, dict))
    expanded_documents = archive_batch.documents
    if archive_expansion.get("package_count"):
        governance = registry.setdefault("governance", {})
        governance["original_archive_packages_retained"] = True
        governance["archive_members_expanded_without_archive_controlled_path_writes"] = True
        governance["archive_security_failures_are_visible"] = True

    for index, doc in enumerate(expanded_documents):
        if not isinstance(doc, dict):
            errors.append({"index": index, "error": "document envelope must be object"})
            continue
        stored: Path | None = None
        runtime_manifest: dict[str, Any] = {}
        previous_runtime_manifest: dict[str, Any] = {}
        try:
            if doc.get("content_bytes") is not None:
                blob, detected_name, raw_text = read_document_envelope_bytes(doc)
            else:
                file_path = Path(str(doc.get("file_path"))) if doc.get("file_path") else None
                inline_text = str(doc.get("text")) if doc.get("text") is not None else None
                blob, detected_name, raw_text = _read_source_bytes(file_path, inline_text)
            filename = str(doc.get("filename") or doc.get("name") or detected_name)
            explicit_source_type = str(doc.get("source_type") or "")
            source_type = _classify_source(filename, raw_text, explicit_source_type)
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
            if duplicate:
                duplicates.append(
                    {
                        "filename": filename,
                        "source_id": duplicate.get("source_id"),
                        "reason": "same_content_hash",
                        "source_type": source_type,
                        "archive_provenance": dict(doc.get("archive_provenance") or {}),
                    }
                )
                continue

            logical_key = _logical_key_for_envelope(filename, source_type, doc)
            versions = [
                int(row.get("version") or 0)
                for row in registry["sources"]
                if row.get("logical_key") == logical_key
            ]
            version = max(versions, default=0) + 1
            source_id = (
                "src_"
                + _short_hash(
                    {
                        "project": project,
                        "hash": content_hash,
                        "logical_key": logical_key,
                        "version": version,
                    }
                )
            )
            storage_name = f"{source_id}_v{version}_{_safe_slug(filename)}"
            stored = paths["source_dir"] / storage_name
            stored.write_bytes(blob)

            try:
                parsed = parse_enterprise_source(blob, filename, source_type, source_id)
            except Exception as exc:
                parse_error = {
                    "stage": "parse",
                    "code": "SOURCE_PARSE_FAILED",
                    "identity": source_id,
                    "retryability": "after_source_or_parser_fix",
                    "operator_action": "inspect the parser receipt and register a corrected source version",
                    "detail": f"{type(exc).__name__}: {exc}"[:500],
                }
                parsed = _empty_parse_result(
                    source_id=source_id,
                    filename=filename,
                    source_type=source_type,
                    error=parse_error,
                )

            formal_status = str(parsed.get("document_ir_status") or "UNKNOWN")
            parse_failed = str(parsed.get("parse_status") or "") == "failed"
            previous_active = next(
                (row for row in active if row.get("logical_key") == logical_key),
                None,
            )
            previous_runtime_manifest = dict(
                (previous_active or {}).get("runtime_source_manifest") or {}
            )
            runtime_asset_id = _runtime_asset_id(logical_key)
            runtime_manifest = {
                "status": "NOT_REGISTERED_PARSE_FAILED" if parse_failed else "PENDING",
                "runtime_asset_id": runtime_asset_id,
                "original_content_hash": content_hash,
                "knowledge_source_id": source_id,
            }
            runtime_error: dict[str, Any] | None = None
            if not parse_failed:
                runtime_manifest, runtime_error = _register_runtime_source(
                    project=project,
                    root=root,
                    runtime_asset_id=runtime_asset_id,
                    filename=filename,
                    source_type=source_type,
                    source_id=source_id,
                    content_hash=content_hash,
                    version=version,
                    external_ref=str(doc.get("external_ref") or "")[:500],
                    parsed=parsed,
                    actor=clean_actor,
                    archive_provenance=dict(doc.get("archive_provenance") or {}),
                )
                if runtime_error:
                    parsed.setdefault("parse_errors", []).append(runtime_error)
                    parser_receipt = dict(parsed.get("parser_receipt") or {})
                    parser_receipt["errors"] = list(parsed.get("parse_errors") or [])
                    parser_receipt["parser_status"] = "failed"
                    parser_receipt["runtime_source_manifest"] = runtime_manifest
                    parsed["parser_receipt"] = parser_receipt
                else:
                    parser_receipt = dict(parsed.get("parser_receipt") or {})
                    parser_receipt["runtime_source_manifest"] = runtime_manifest
                    parsed["parser_receipt"] = parser_receipt

            activation_failed = parse_failed or runtime_error is not None
            chunk_receipt: dict[str, Any] = {
                "schema": "qualibug.document-ir-chunk-index-receipt.v1",
                "source_id": source_id,
                "source_hash": content_hash,
                "chunk_count": 0,
                "status": "NOT_REGISTERED_SOURCE_INACTIVE" if activation_failed else "PENDING",
                "raw_binary_utf8_decode_used": False,
                "silent_failure_allowed": False,
                "runtime_source_id": str(runtime_manifest.get("source_id") or ""),
                "runtime_source_hash": str(runtime_manifest.get("source_hash") or ""),
            }
            chunk_warning: dict[str, Any] | None = None
            if not activation_failed:
                chunk_receipt, chunk_warning = _register_chunks(
                    project=project,
                    root=root,
                    source_id=source_id,
                    content_hash=content_hash,
                    version=version,
                    parsed=parsed,
                    runtime_manifest=runtime_manifest,
                )
                if chunk_warning:
                    warnings.append({"index": index, "filename": filename, **chunk_warning})
                    parsed.setdefault("parse_errors", []).append(chunk_warning)
                    parser_receipt = dict(parsed.get("parser_receipt") or {})
                    parser_receipt["errors"] = list(parsed.get("parse_errors") or [])
                    parser_receipt["parser_status"] = "degraded"
                    parsed["parser_receipt"] = parser_receipt

            status = "failed" if activation_failed else "active"
            record = {
                "source_id": source_id,
                "logical_key": logical_key,
                "original_name": filename,
                "source_type": source_type,
                "version": version,
                "content_hash": content_hash,
                "status": status,
                "tags": [
                    str(value)[:80]
                    for value in (doc.get("tags") or [])
                    if str(value).strip()
                ][:20],
                "external_ref": str(doc.get("external_ref") or "")[:500],
                "archive_provenance": dict(doc.get("archive_provenance") or {}),
                "stored_path": str(stored.relative_to(root)).replace("\\", "/"),
                "created_at_utc": _now(),
                "created_by": clean_actor,
                "runtime_asset_id": runtime_asset_id,
                "runtime_source_manifest": runtime_manifest,
                "parse": _parse_summary(parsed, chunk_receipt, runtime_manifest),
            }

            superseded: list[dict[str, Any]] = []
            if status == "active":
                superseded = [
                    row for row in active if row.get("logical_key") == logical_key
                ]
                for previous in superseded:
                    previous["status"] = "superseded"
                    previous["superseded_at_utc"] = _now()
                    previous["superseded_by"] = source_id
                active = [
                    row
                    for row in active
                    if row.get("logical_key") != logical_key
                    and row.get("status") == "active"
                ]
                active.append(record)
            else:
                error_detail = next(
                    (
                        str(row.get("detail") or row.get("code") or "")
                        for row in parsed.get("parse_errors") or []
                        if isinstance(row, dict)
                    ),
                    "formal document parsing was blocked",
                )
                errors.append(
                    {
                        "index": index,
                        "filename": filename,
                        "source_id": source_id,
                        "code": (
                            "SOURCE_RUNTIME_REGISTRATION_FAILED"
                            if runtime_error
                            else "SOURCE_FORMAL_PARSE_BLOCKED"
                        ),
                        "formal_status": formal_status,
                        "error": (
                            str(runtime_error.get("detail") or "")[:500]
                            if runtime_error
                            else error_detail[:500]
                        ),
                    }
                )

            registry["sources"].append(record)
            created.append(record)
            for previous in superseded:
                _remove_record_chunk_index(root, previous)
        except Exception as exc:
            if runtime_manifest.get("status") == "REGISTERED":
                try:
                    rollback_source_asset_activation(
                        project,
                        str(runtime_manifest.get("source_id") or ""),
                        root=root,
                        actor=clean_actor,
                        restore_source_hash=str(
                            previous_runtime_manifest.get("source_hash") or ""
                        ),
                        restore_version_id=str(
                            previous_runtime_manifest.get("source_version_id") or ""
                        ),
                        reason="knowledge_ingest_transaction_rolled_back",
                    )
                except Exception:
                    pass
            if stored is not None:
                try:
                    if stored.exists():
                        stored.unlink()
                except OSError:
                    pass
            errors.append(
                {
                    "index": index,
                    "filename": str(doc.get("filename") or doc.get("name") or ""),
                    "error": str(exc)[:500],
                }
            )

    registry["audit_events"].append(
        {
            "event": "ingest",
            "at_utc": _now(),
            "actor": clean_actor,
            "created_source_ids": [row["source_id"] for row in created],
            "duplicate_count": len(duplicates),
            "error_count": len(errors),
            "warning_count": len(warnings),
            "archive_package_count": int(archive_expansion.get("package_count") or 0),
            "archive_expanded_document_count": int(
                archive_expansion.get("document_count") or 0
            ),
            "archive_error_count": int(archive_expansion.get("error_count") or 0),
            "archive_hashes": [
                str(row.get("archive_hash") or "")
                for row in archive_expansion.get("packages") or []
                if isinstance(row, dict) and str(row.get("archive_hash") or "")
            ],
        }
    )
    _save_registry(project, root, registry)
    return {
        "ok": not errors,
        "phase": PHASE,
        "project_id": project,
        "created": created,
        "duplicates": duplicates,
        "errors": errors,
        "warnings": warnings,
        "archive_expansion": archive_expansion,
        "source_count": len(
            [row for row in registry["sources"] if row.get("status") == "active"]
        ),
        "rebuild_recommended": bool(
            [row for row in created if row.get("status") == "active"]
        ),
    }


def ingest_enterprise_knowledge_files(
    project_id: str,
    file_paths: Iterable[str | Path],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    source_type_hints: dict[str, str] | None = None,
) -> dict[str, Any]:
    source_type_hints = source_type_hints or {}
    documents = [
        {"file_path": str(path), "source_type": source_type_hints.get(str(path))}
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
    root = root or ROOT
    project = _safe_project_id(project_id)
    registry = _load_registry(project, root)
    sources = (
        registry["sources"]
        if include_deleted
        else [row for row in registry["sources"] if row.get("status") == "active"]
    )
    return {
        "phase": PHASE,
        "project_id": project,
        "sources": sources,
        "summary": {
            "active_source_count": len(
                [row for row in registry["sources"] if row.get("status") == "active"]
            ),
            "superseded_source_count": len(
                [
                    row
                    for row in registry["sources"]
                    if row.get("status") == "superseded"
                ]
            ),
            "failed_source_count": len(
                [row for row in registry["sources"] if row.get("status") == "failed"]
            ),
            "deleted_source_count": len(
                [row for row in registry["sources"] if row.get("status") == "deleted"]
            ),
            "source_type_distribution": dict(
                Counter(str(row.get("source_type") or "unknown") for row in sources)
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
    root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    registry = _load_registry(project, root)
    record = next(
        (
            row
            for row in registry["sources"]
            if row.get("source_id") == source_id and row.get("status") == "active"
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
                set(patch).intersection({"tags", "source_type", "external_ref"})
            ),
        }
    )
    _save_registry(project, root, registry)
    return {"ok": True, "source": record, "rebuild_recommended": True}


def delete_enterprise_knowledge_source(
    project_id: str,
    source_id: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    purge_bytes: bool = False,
) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    registry = _load_registry(project, root)
    record_index = next(
        (
            index
            for index, row in enumerate(registry["sources"])
            if row.get("source_id") == source_id and row.get("status") == "active"
        ),
        None,
    )
    record = registry["sources"][record_index] if record_index is not None else None
    if not record:
        raise KeyError(f"active source not found: {source_id}")
    removed_paths: list[str] = []
    original_name = str(record.get("original_name") or "")
    candidate_paths: list[Path] = []
    stored_path = str(record.get("stored_path") or "")
    if stored_path:
        candidate_paths.append(root / stored_path)
    if original_name:
        candidate_paths.append(
            root / "platform_workspace" / project / "input" / original_name
        )
    seen_paths: set[str] = set()
    for candidate in candidate_paths:
        resolved = str(candidate.resolve())
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        try:
            if candidate.exists() and candidate.is_file():
                candidate.unlink()
                removed_paths.append(
                    str(candidate.relative_to(root)).replace("\\", "/")
                )
        except Exception:
            continue
    removed_paths.extend(_remove_record_chunk_index(root, record))
    runtime_asset_id = str(record.get("runtime_asset_id") or "") or _runtime_asset_id(
        str(record.get("logical_key") or "")
    )
    try:
        runtime_deactivation = deactivate_source_asset(
            project,
            runtime_asset_id,
            root=root,
            actor=clean_actor,
            reason="knowledge_source_deleted",
        )
    except Exception as exc:
        runtime_deactivation = {
            "source_id": runtime_asset_id,
            "deactivated": False,
            "reason": f"{type(exc).__name__}: {exc}"[:500],
        }
    registry["sources"].pop(record_index)
    registry["audit_events"].append(
        {
            "event": "delete",
            "at_utc": _now(),
            "actor": clean_actor,
            "source_id": source_id,
            "original_name": original_name,
            "removed_paths": removed_paths,
            "physical_delete": True,
        }
    )
    _save_registry(project, root, registry)
    return {
        "ok": True,
        "source_id": source_id,
        "original_name": original_name,
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
    """Controller facade that delegates to existing storage and build authorities."""

    payload = payload if isinstance(payload, dict) else {}
    root = root or ROOT
    project = _safe_project_id(project_id)
    action = str(action or "view").strip().lower()
    if action in {"view", "list"}:
        from ._api import load_enterprise_business_knowledge_asset

        asset = load_enterprise_business_knowledge_asset(project, root)
        return {
            "ok": True,
            "action": "view",
            "inventory": list_enterprise_knowledge_sources(
                project,
                root,
                include_deleted=bool(payload.get("include_deleted")),
            ),
            "asset": asset or {},
        }
    if action in {"upload", "ingest"}:
        docs = (
            payload.get("documents")
            if isinstance(payload.get("documents"), list)
            else []
        )
        result = ingest_enterprise_knowledge_documents(
            project,
            docs,
            root=root,
            actor=actor,
        )
        return {"ok": bool(result.get("ok")), "action": "upload", "result": result}
    if action in {"edit", "update"}:
        source_id = str(payload.get("source_id") or "")
        return {
            "ok": True,
            "action": "edit",
            "result": update_enterprise_knowledge_source(
                project,
                source_id,
                payload.get("patch") or {},
                root=root,
                actor=actor,
            ),
        }
    if action in {"delete", "remove"}:
        source_id = str(payload.get("source_id") or "")
        return {
            "ok": True,
            "action": "delete",
            "result": delete_enterprise_knowledge_source(
                project,
                source_id,
                root=root,
                actor=actor,
                purge_bytes=bool(payload.get("purge_bytes")),
            ),
        }
    if action in {"rebuild", "build"}:
        from ._api import build_enterprise_business_knowledge_asset

        asset = build_enterprise_business_knowledge_asset(
            project,
            root,
            options=payload.get("options")
            if isinstance(payload.get("options"), dict)
            else None,
        )
        return {"ok": True, "action": "rebuild", "asset": asset}
    raise ValueError(
        "unsupported knowledge center action; use view, upload, edit, delete or rebuild"
    )
