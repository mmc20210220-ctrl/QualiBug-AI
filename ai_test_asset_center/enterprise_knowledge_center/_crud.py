"""Knowledge center CRUD: ingest, list, update, delete, operate."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable
from ._parsing import _classify_source, _parse_source  # noqa: F401
from ._utils import _hash_bytes, _load_registry, _now, _parser_receipt, _paths, _read_source_bytes, _require_manage_actor, _safe_slug, _save_registry, _short_hash  # noqa: F401

logger = logging.getLogger(__name__)

try:
    import docx2txt
except ImportError:
    docx2txt = None

from ._common import *  # noqa: F401,F403
from ._common import _safe_project_id  # explicit: underscore names not exported by *
from ._utils import *  # noqa: F401,F403
from ._parsing import *  # noqa: F401,F403

__all__ = [
    "_logical_key", "_record_parse",
    "delete_enterprise_knowledge_source", "ingest_enterprise_knowledge_documents",
    "ingest_enterprise_knowledge_files", "list_enterprise_knowledge_sources",
    "operate_enterprise_knowledge_center", "update_enterprise_knowledge_source",
]


def _record_parse(record: dict[str, Any], root: Path) -> dict[str, Any]:
    stored = root / str(record.get("stored_path") or "")
    if not stored.exists():
        source_id = str(record.get("source_id") or "")
        error = {
            "stage": "parse",
            "code": "SOURCE_BYTES_MISSING",
            "identity": source_id,
            "retryability": "after_source_restore",
            "operator_action": "restore the immutable source blob or register a new source version",
        }
        return {
            "text": "", "payload": None, "openapi": {}, "operations": [], "tables": [],
            "field_dictionary": [], "ui_specs": [], "permissions": [], "tickets": [],
            "har_errors": [], "log_errors": [], "rules": [], "roles": [], "state_machines": [],
            "parse_status": "failed", "parser": "none", "text_hash": "", "text_length": 0,
            "parse_errors": [error],
            "parser_receipt": _parser_receipt(
                source_id=source_id,
                filename=str(record.get("original_name") or stored.name),
                source_type=str(record.get("source_type") or "other_document"),
                parser="none",
                detected_format=Path(str(record.get("original_name") or stored.name)).suffix.lstrip(".") or "unknown",
                text_hash="",
                text_length=0,
                outputs={},
                errors=[error],
                parse_status="failed",
                started_at_utc=_now(),
            ),
        }
    return _parse_source(stored.read_bytes(), str(record.get("original_name") or stored.name), str(record.get("source_type") or "other_document"), str(record.get("source_id") or ""))


def _logical_key(name: str, source_type: str) -> str:
    return f"{source_type}:{_safe_slug(Path(name).stem, 72).lower()}"


def ingest_enterprise_knowledge_documents(
    project_id: str,
    documents: list[dict[str, Any]],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ingest path or text envelopes into project-scoped versioned source storage.

    Accepted envelope fields: file_path, text, filename/name, source_type,
    tags and external_ref.  external_ref is metadata only; this function never
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
    for index, doc in enumerate(documents or []):
        if not isinstance(doc, dict):
            errors.append({"index": index, "error": "document envelope must be object"})
            continue
        try:
            file_path = Path(str(doc.get("file_path"))) if doc.get("file_path") else None
            inline_text = str(doc.get("text")) if doc.get("text") is not None else None
            blob, detected_name, raw_text = _read_source_bytes(file_path, inline_text)
            filename = str(doc.get("filename") or doc.get("name") or detected_name)
            source_type = _classify_source(filename, raw_text, str(doc.get("source_type") or ""))
            content_hash = _hash_bytes(blob)
            duplicate = next((row for row in registry["sources"] if row.get("status") != "deleted" and row.get("content_hash") == content_hash), None)
            if duplicate:
                duplicates.append({"filename": filename, "source_id": duplicate.get("source_id"), "reason": "same_content_hash", "source_type": source_type})
                continue
            logical_key = _logical_key(filename, source_type)
            versions = [int(row.get("version") or 0) for row in registry["sources"] if row.get("logical_key") == logical_key]
            version = max(versions, default=0) + 1
            source_id = f"src_{_short_hash({'project': project, 'hash': content_hash, 'logical_key': logical_key, 'version': version})}"
            for previous in [row for row in active if row.get("logical_key") == logical_key]:
                previous["status"] = "superseded"
                previous["superseded_at_utc"] = _now()
                previous["superseded_by"] = source_id
            active = [row for row in active if row.get("status") == "active"]
            storage_name = f"{source_id}_v{version}_{_safe_slug(filename)}"
            stored = paths["source_dir"] / storage_name
            stored.write_bytes(blob)
            try:
                parsed = _parse_source(blob, filename, source_type, source_id)
            except Exception as exc:
                parse_error = {
                    "stage": "parse",
                    "code": "SOURCE_PARSE_FAILED",
                    "identity": source_id,
                    "retryability": "after_source_or_parser_fix",
                    "operator_action": "inspect the parser receipt and register a corrected source version",
                    "detail": f"{type(exc).__name__}: {exc}"[:500],
                }
                errors.append({
                    "index": index,
                    "filename": filename,
                    "source_id": source_id,
                    "code": "SOURCE_PARSE_FAILED",
                    "error": parse_error["detail"],
                })
                parsed = {
                    "text": "", "payload": None, "openapi": {}, "operations": [], "tables": [],
                    "field_dictionary": [], "ui_specs": [], "permissions": [], "tickets": [],
                    "har_errors": [], "log_errors": [], "rules": [], "roles": [], "state_machines": [],
                    "parse_status": "failed", "parser": "none", "text_hash": "", "text_length": 0,
                    "parse_errors": [parse_error],
                    "parser_receipt": _parser_receipt(
                        source_id=source_id,
                        filename=filename,
                        source_type=source_type,
                        parser="none",
                        detected_format=Path(filename).suffix.lstrip(".") or "unknown",
                        text_hash="",
                        text_length=0,
                        outputs={},
                        errors=[parse_error],
                        parse_status="failed",
                        started_at_utc=_now(),
                    ),
                }
            record = {
                "source_id": source_id,
                "logical_key": logical_key,
                "original_name": filename,
                "source_type": source_type,
                "version": version,
                "content_hash": content_hash,
                "status": "active",
                "tags": [str(x)[:80] for x in (doc.get("tags") or []) if str(x).strip()][:20],
                "external_ref": str(doc.get("external_ref") or "")[:500],
                "stored_path": str(stored.relative_to(root)).replace("\\", "/"),
                "created_at_utc": _now(),
                "created_by": clean_actor,
                "parse": {
                    "parser": parsed["parser"],
                    "parse_status": parsed["parse_status"],
                    "text_hash": parsed["text_hash"],
                    "text_length": parsed["text_length"],
                    "operation_count": len(parsed["operations"]),
                    "table_count": len(parsed["tables"]),
                    "field_count": len(parsed["field_dictionary"]),
                    "ui_spec_count": len(parsed["ui_specs"]),
                    "permission_count": len(parsed["permissions"]),
                    "rule_count": len(parsed["rules"]),
                    "ticket_count": len(parsed["tickets"]),
                    "fidelity": str((parsed.get("parser_receipt") or {}).get("fidelity") or "unknown"),
                    "errors": list(parsed.get("parse_errors") or []),
                    "receipt": dict(parsed.get("parser_receipt") or {}),
                },
            }
            registry["sources"].append(record)
            active.append(record)
            created.append(record)
            # ── Chunk registration for GraphRAG retrieval ──
            # Parse document into typed chunks with evidence metadata and
            # persist them so search_chunks_by_entity can provide precise
            # context to LLM reasoning engines.
            try:
                from .document_intelligence import parse_document as _parse_doc_chunks
                from .enterprise_source_registry import register_source_chunks
                _text_for_chunks = blob.decode("utf-8", errors="replace")
                _chunk_result = _parse_doc_chunks(
                    _text_for_chunks, filename=filename, text=_text_for_chunks, source_id=source_id,
                )
                _chunks = _chunk_result.get("chunks") or []
                if _chunks:
                    register_source_chunks(
                        project, source_id, content_hash, _chunks, root=root,
                    )
            except Exception:
                pass  # Chunk registration is best-effort; must not block ingest
        except Exception as exc:
            errors.append({"index": index, "filename": str(doc.get("filename") or doc.get("name") or ""), "error": str(exc)[:500]})
    registry["audit_events"].append({"event": "ingest", "at_utc": _now(), "actor": clean_actor, "created_source_ids": [x["source_id"] for x in created], "duplicate_count": len(duplicates), "error_count": len(errors)})
    _save_registry(project, root, registry)
    return {"ok": not errors, "phase": PHASE, "project_id": project, "created": created, "duplicates": duplicates, "errors": errors, "source_count": len([x for x in registry["sources"] if x.get("status") == "active"]), "rebuild_recommended": bool(created)}


def ingest_enterprise_knowledge_files(project_id: str, file_paths: Iterable[str | Path], root: Path | None = None, actor: dict[str, Any] | None = None, source_type_hints: dict[str, str] | None = None) -> dict[str, Any]:
    source_type_hints = source_type_hints or {}
    documents = [{"file_path": str(path), "source_type": source_type_hints.get(str(path))} for path in file_paths]
    return ingest_enterprise_knowledge_documents(project_id, documents, root=root, actor=actor)


def list_enterprise_knowledge_sources(project_id: str, root: Path | None = None, include_deleted: bool = False) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    registry = _load_registry(project, root)
    sources = registry["sources"] if include_deleted else [x for x in registry["sources"] if x.get("status") == "active"]
    return {"phase": PHASE, "project_id": project, "sources": sources, "summary": {"active_source_count": len([x for x in registry["sources"] if x.get("status") == "active"]), "superseded_source_count": len([x for x in registry["sources"] if x.get("status") == "superseded"]), "deleted_source_count": len([x for x in registry["sources"] if x.get("status") == "deleted"]), "source_type_distribution": dict(Counter(str(x.get("source_type") or "unknown") for x in sources))}, "governance": registry.get("governance") or {}}


def update_enterprise_knowledge_source(project_id: str, source_id: str, patch: dict[str, Any], root: Path | None = None, actor: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    registry = _load_registry(project, root)
    record = next((row for row in registry["sources"] if row.get("source_id") == source_id and row.get("status") == "active"), None)
    if not record:
        raise KeyError(f"active source not found: {source_id}")
    if "tags" in patch:
        record["tags"] = [str(x)[:80] for x in (patch.get("tags") or []) if str(x).strip()][:20]
    if "source_type" in patch:
        source_type = str(patch.get("source_type") or "").lower()
        if source_type not in SOURCE_TYPES:
            raise ValueError("unsupported source_type")
        record["source_type"] = source_type
        record["logical_key"] = _logical_key(str(record.get("original_name") or "document"), source_type)
    if "external_ref" in patch:
        record["external_ref"] = str(patch.get("external_ref") or "")[:500]
    record["updated_at_utc"] = _now()
    record["updated_by"] = clean_actor
    registry["audit_events"].append({"event": "update_metadata", "at_utc": _now(), "actor": clean_actor, "source_id": source_id, "fields": sorted(set(patch).intersection({"tags", "source_type", "external_ref"}))})
    _save_registry(project, root, registry)
    return {"ok": True, "source": record, "rebuild_recommended": True}


def delete_enterprise_knowledge_source(project_id: str, source_id: str, root: Path | None = None, actor: dict[str, Any] | None = None, purge_bytes: bool = False) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    registry = _load_registry(project, root)
    record_index = next((index for index, row in enumerate(registry["sources"]) if row.get("source_id") == source_id and row.get("status") == "active"), None)
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
        candidate_paths.append(root / "platform_workspace" / project / "input" / original_name)
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
    registry["sources"].pop(record_index)
    registry["audit_events"].append({
        "event": "delete",
        "at_utc": _now(),
        "actor": clean_actor,
        "source_id": source_id,
        "original_name": original_name,
        "removed_paths": removed_paths,
        "physical_delete": True,
    })
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
    """Small controller facade for the local knowledge-center page/API.

    It intentionally delegates to the existing ingestion and governance functions
    instead of introducing a second web service or persistence model.
    """
    payload = payload if isinstance(payload, dict) else {}
    root = root or ROOT
    project = _safe_project_id(project_id)
    action = str(action or "view").strip().lower()
    if action in {"view", "list"}:
        from ._api import load_enterprise_business_knowledge_asset  # lazy: avoid circular import
        asset = load_enterprise_business_knowledge_asset(project, root)
        return {
            "ok": True,
            "action": "view",
            "inventory": list_enterprise_knowledge_sources(project, root, include_deleted=bool(payload.get("include_deleted"))),
            "asset": asset or {},
        }
    if action in {"upload", "ingest"}:
        docs = payload.get("documents") if isinstance(payload.get("documents"), list) else []
        result = ingest_enterprise_knowledge_documents(project, docs, root=root, actor=actor)
        return {"ok": bool(result.get("ok")), "action": "upload", "result": result}
    if action in {"edit", "update"}:
        source_id = str(payload.get("source_id") or "")
        return {"ok": True, "action": "edit", "result": update_enterprise_knowledge_source(project, source_id, payload.get("patch") or {}, root=root, actor=actor)}
    if action in {"delete", "remove"}:
        source_id = str(payload.get("source_id") or "")
        return {"ok": True, "action": "delete", "result": delete_enterprise_knowledge_source(project, source_id, root=root, actor=actor, purge_bytes=bool(payload.get("purge_bytes")))}
    if action in {"rebuild", "build"}:
        from ._api import build_enterprise_business_knowledge_asset  # lazy: avoid circular import
        asset = build_enterprise_business_knowledge_asset(project, root, options=payload.get("options") if isinstance(payload.get("options"), dict) else None)
        return {"ok": True, "action": "rebuild", "asset": asset}
    raise ValueError("unsupported knowledge center action; use view, upload, edit, delete or rebuild")


