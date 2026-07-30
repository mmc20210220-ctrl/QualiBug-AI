"""Canonical upload dispatch for the private-pilot knowledge endpoint.

The HTTP handler owns request validation and response scheduling only. Ordinary documents
and archive transports both enter ``ingest_enterprise_knowledge_documents``; archive
expansion is an internal stage of that transaction. This module never registers a second
source asset and never calls a parallel archive parser.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .private_pilot_project_assets import (
    KNOWLEDGE_INGEST_ARCHIVE_EXTENSIONS,
    KNOWLEDGE_INGEST_TEXT_EXTENSIONS,
    resolve_knowledge_source_type,
)

UPLOAD_INGEST_AUTHORITY_SCHEMA = "qualibug.upload-ingest-authority-result.v1"


def _validate_ingest_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise TypeError("knowledge ingest result must be an object")
    if not isinstance(result.get("ok"), bool):
        raise ValueError("knowledge ingest result ok must be a boolean")
    for field in ("created", "duplicates", "errors"):
        if field in result and not isinstance(result.get(field), list):
            raise ValueError(f"knowledge ingest result {field} must be a list")
    return result


def _source_ids(result: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("created", "duplicates"):
        for row in result.get(key) or []:
            if not isinstance(row, dict):
                continue
            value = str(row.get("source_id") or "").strip()
            if value and value not in values:
                values.append(value)
    return values


def ingest_uploaded_enterprise_material(
    *,
    project: str,
    root: Path,
    actor: dict[str, str],
    out_path: Path,
    filename: str,
    raw: bytes,
    explicit_type: str = "",
) -> dict[str, Any]:
    """Ingest one uploaded transport artifact through the canonical knowledge authority."""

    from .enterprise_knowledge_center import ingest_enterprise_knowledge_documents

    suffix = Path(filename).suffix.lower()
    is_archive = suffix in KNOWLEDGE_INGEST_ARCHIVE_EXTENSIONS
    extracted_text = ""
    doc_info: dict[str, Any] = {}
    envelope: dict[str, Any] = {
        "file_path": str(out_path),
        "filename": filename,
    }
    if is_archive:
        type_resolution = "member_automatic"
        if explicit_type:
            source_hint, _resolution = resolve_knowledge_source_type(
                filename,
                "",
                explicit_type,
            )
            envelope["source_type"] = source_hint
            envelope["inherit_source_type_to_members"] = True
            type_resolution = "explicit_member_override"
        doc_type = "archive_package"
    else:
        from .document_change_watcher import ingest_document

        observed = ingest_document(str(out_path))
        if not isinstance(observed, dict) or observed.get("ok") is not True:
            detail = (
                str(observed.get("error") or "document intelligence returned an invalid result")
                if isinstance(observed, dict)
                else "document intelligence result must be an object"
            )
            raise ValueError(f"DOCUMENT_INGEST_FAILED:{detail}")
        doc_info = dict(observed)
        extracted_text = str(doc_info.get("text") or "")
        if not extracted_text and suffix in KNOWLEDGE_INGEST_TEXT_EXTENSIONS:
            extracted_text = raw.decode("utf-8", errors="replace")
        doc_type, type_resolution = resolve_knowledge_source_type(
            filename,
            extracted_text,
            explicit_type or None,
        )
        envelope["source_type"] = doc_type

    ingest_result = ingest_enterprise_knowledge_documents(
        project,
        [envelope],
        root=root,
        actor=actor,
    )
    ingest_result = _validate_ingest_result(ingest_result)
    if is_archive:
        archive_expansion = dict(ingest_result.get("archive_expansion") or {})
        doc_info = {
            "ok": bool(ingest_result.get("ok")),
            "transport": "archive",
            "archive_receipts": list(archive_expansion.get("packages") or []),
            "expanded_document_count": int(
                archive_expansion.get("document_count") or 0
            ),
            "archive_error_count": int(archive_expansion.get("error_count") or 0),
            "archive_warning_count": int(
                archive_expansion.get("warning_count") or 0
            ),
            "canonical_archive_authority": archive_expansion.get(
                "canonical_archive_authority"
            ),
            "archive_member_type_mode": type_resolution,
        }

    source_ids = _source_ids(ingest_result)
    if ingest_result["ok"] is False:
        return {
            "schema": UPLOAD_INGEST_AUTHORITY_SCHEMA,
            "ok": False,
            "transport": "archive" if is_archive else "document",
            "filename": filename,
            "doc_type": doc_type,
            "type_resolution": type_resolution,
            "source_ids": source_ids,
            "source_id": source_ids[0] if source_ids else "",
            "source_manifest": {},
            "extracted_text": extracted_text,
            "doc_info": doc_info,
            "ingest_result": ingest_result,
            "second_source_registration_performed": False,
            "parallel_archive_parser_called": False,
        }

    from .enterprise_source_registry import compose_project_source_manifest

    source_manifest = compose_project_source_manifest(
        project,
        root=root,
        actor=actor,
    )
    if not isinstance(source_manifest, dict):
        raise TypeError("composed source manifest must be an object")
    manifest_source_id = str(source_manifest.get("source_id") or "").strip()
    manifest_source_hash = str(source_manifest.get("source_hash") or "").strip()
    if not manifest_source_id or not manifest_source_hash:
        raise ValueError("canonical runtime corpus manifest is unavailable after ingest")
    return {
        "schema": UPLOAD_INGEST_AUTHORITY_SCHEMA,
        "ok": True,
        "transport": "archive" if is_archive else "document",
        "filename": filename,
        "doc_type": doc_type,
        "type_resolution": type_resolution,
        "source_ids": source_ids,
        "source_id": source_ids[0] if source_ids else manifest_source_id,
        "source_manifest": source_manifest,
        "extracted_text": extracted_text,
        "doc_info": doc_info,
        "ingest_result": ingest_result,
        "second_source_registration_performed": False,
        "parallel_archive_parser_called": False,
        "canonical_runtime_corpus_used": True,
    }


__all__ = [
    "UPLOAD_INGEST_AUTHORITY_SCHEMA",
    "ingest_uploaded_enterprise_material",
]
