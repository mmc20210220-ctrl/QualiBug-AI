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
from .source_ingestion import (
    build_document_ir_retrieval_chunks,
    parse_enterprise_source,
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


def _parse_summary(parsed: dict[str, Any], chunk_receipt: dict[str, Any]) -> dict[str, Any]:
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


def _register_chunks(
    *,
    project: str,
    root: Path,
    source_id: str,
    content_hash: str,
    version: int,
    parsed: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    chunks, receipt = build_document_ir_retrieval_chunks(
        parsed,
        source_id=source_id,
        source_hash=content_hash,
        source_version=version,
    )
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
    """Ingest path or text envelopes into project-scoped versioned source storage.

    Accepted envelope fields: file_path, text, filename/name, source_type,
    tags and external_ref. external_ref is metadata only; this function never
    fetches remote Feishu/Confluence content by URL.
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

    for index, doc in enumerate(documents or []):
        if not isinstance(doc, dict):
            errors.append({"index": index, "error": "document envelope must be object"})
            continue
        stored: Path | None = None
        try:
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
                    }
                )
                continue

            logical_key = _logical_key(filename, source_type)
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
            chunk_receipt: dict[str, Any] = {
                "schema": "qualibug.document-ir-chunk-index-receipt.v1",
                "source_id": source_id,
                "source_hash": content_hash,
                "chunk_count": 0,
                "status": "NOT_REGISTERED_PARSE_FAILED" if parse_failed else "PENDING",
                "raw_binary_utf8_decode_used": False,
                "silent_failure_allowed": False,
            }
            chunk_warning: dict[str, Any] | None = None
            if not parse_failed:
                chunk_receipt, chunk_warning = _register_chunks(
                    project=project,
                    root=root,
                    source_id=source_id,
                    content_hash=content_hash,
                    version=version,
                    parsed=parsed,
                )
                if chunk_warning:
                    warnings.append({"index": index, "filename": filename, **chunk_warning})
                    parsed.setdefault("parse_errors", []).append(chunk_warning)
                    parser_receipt = dict(parsed.get("parser_receipt") or {})
                    parser_receipt["errors"] = list(parsed.get("parse_errors") or [])
                    parser_receipt["parser_status"] = "degraded"
                    parsed["parser_receipt"] = parser_receipt

            status = "failed" if parse_failed else "active"
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
                "stored_path": str(stored.relative_to(root)).replace("\\", "/"),
                "created_at_utc": _now(),
                "created_by": clean_actor,
                "parse": _parse_summary(parsed, chunk_receipt),
            }

            superseded: list[dict[str, Any]] = []
            if status == "active":
                superseded = [row for row in active if row.get("logical_key") == logical_key]
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
                        "code": "SOURCE_FORMAL_PARSE_BLOCKED",
                        "formal_status": formal_status,
                        "error": error_detail[:500],
                    }
                )

            registry["sources"].append(record)
            created.append(record)
            for previous in superseded:
                _remove_record_chunk_index(root, previous)
        except Exception as exc:
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
                [row for row in registry["sources"] if row.get("status") == "superseded"]
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
        record["logical_key"] = _logical_key(
            str(record.get("original_name") or "document"),
            source_type,
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
                removed_paths.append(str(candidate.relative_to(root)).replace("\\", "/"))
        except Exception:
            continue
    removed_paths.extend(_remove_record_chunk_index(root, record))
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
