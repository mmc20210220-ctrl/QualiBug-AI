"""Capability-driven parsing planner.

The planner does not infer business meaning.  It fingerprints the source, resolves
registered adapters, and records which structural capabilities are available or still
missing.  Supplemental adapters may be added later without changing the business
understanding mainline.
"""
from __future__ import annotations

import mimetypes
from typing import Any

from .contract import (
    CAP_FONT_EVIDENCE,
    CAP_HEADER_FOOTER,
    CAP_HEADING_HIERARCHY,
    CAP_IMAGE_PRESENCE,
    CAP_LIST_HIERARCHY,
    CAP_PAGE_LAYOUT,
    CAP_TABLE_REGION_DETECTION,
    CAP_TABLE_STRUCTURE,
    CAP_TEXT_COORDINATES,
    CAP_TEXT_EXTRACTION,
    DOCUMENT_PARSING_PLAN_SCHEMA,
    MODE_FALLBACK,
    MODE_PRIMARY,
    MODE_SUPPLEMENTAL,
    DocumentSource,
    unique_text,
)
from .registry import DocumentAdapterRegistry


def _detected_family(source: DocumentSource) -> tuple[str, str]:
    stripped = source.data.lstrip()
    if stripped.startswith(b"%PDF-"):
        return "pdf", "pdf_file_signature"
    if source.data.startswith(b"PK"):
        try:
            import io
            import zipfile

            with zipfile.ZipFile(io.BytesIO(source.data)) as archive:
                names = set(archive.namelist())
                if "word/document.xml" in names:
                    return "docx", "office_open_xml_word_container"
                if "xl/workbook.xml" in names:
                    return "xlsx", "office_open_xml_excel_container"
                if "ppt/presentation.xml" in names:
                    return "pptx", "office_open_xml_presentation_container"
            return "zip", "zip_container_signature"
        except Exception:
            return "zip_or_corrupt_container", "pk_signature_without_readable_zip_directory"
    if stripped.startswith((b"{", b"[")):
        return "structured_text", "json_like_prefix"
    mime, _encoding = mimetypes.guess_type(source.filename)
    if mime and mime.startswith("text/"):
        return "text", "mime_guess"
    suffix = source.suffix.lstrip(".")
    return suffix or "unknown", "filename_suffix_or_unknown"


def _required_capabilities(family: str) -> list[str]:
    if family == "docx":
        return unique_text(
            [
                CAP_TEXT_EXTRACTION,
                CAP_HEADING_HIERARCHY,
                CAP_LIST_HIERARCHY,
                CAP_TABLE_STRUCTURE,
                CAP_HEADER_FOOTER,
                CAP_FONT_EVIDENCE,
            ]
        )
    if family == "pdf":
        return unique_text(
            [
                CAP_TEXT_EXTRACTION,
                CAP_PAGE_LAYOUT,
                CAP_TEXT_COORDINATES,
                CAP_FONT_EVIDENCE,
                CAP_HEADING_HIERARCHY,
                CAP_LIST_HIERARCHY,
                CAP_TABLE_REGION_DETECTION,
                CAP_IMAGE_PRESENCE,
                CAP_HEADER_FOOTER,
            ]
        )
    if family in {"text", "structured_text", "txt", "md", "markdown", "rst", "json", "yaml", "yml", "xml", "html", "htm", "sql", "log", "csv", "tsv"}:
        return unique_text([CAP_TEXT_EXTRACTION, CAP_HEADING_HIERARCHY, CAP_LIST_HIERARCHY])
    return []


def plan_document_parsing(
    source: DocumentSource,
    registry: DocumentAdapterRegistry,
) -> dict[str, Any]:
    family, detection_method = _detected_family(source)
    matches = registry.matches(source)
    primary_rows = [row for row in matches if row[1].mode == MODE_PRIMARY]
    supplemental_rows = [row for row in matches if row[1].mode == MODE_SUPPLEMENTAL]
    fallback_rows = [row for row in matches if row[1].mode == MODE_FALLBACK]

    selected: list[tuple[Any, Any]] = []
    if primary_rows:
        selected.append(primary_rows[0])
        provided = set(primary_rows[0][1].capabilities)
        for row in supplemental_rows:
            contribution = set(row[1].capabilities) - provided
            if not contribution:
                continue
            selected.append(row)
            provided.update(row[1].capabilities)
    elif fallback_rows:
        # Generic text outranks the fail-visible unknown adapter by match score.
        selected.append(fallback_rows[0])

    provided_capabilities = unique_text(
        capability
        for _adapter, match in selected
        for capability in match.capabilities
    )
    required_capabilities = _required_capabilities(family)
    missing_capabilities = sorted(set(required_capabilities) - set(provided_capabilities))
    alternatives = [
        match.to_dict()
        for adapter, match in matches
        if all(adapter.name != chosen.name for chosen, _chosen_match in selected)
    ]
    selected_rows = [
        {
            **match.to_dict(),
            "parser_version": str(getattr(adapter, "parser_version", "")),
            "priority": int(getattr(adapter, "priority", 0)),
        }
        for adapter, match in selected
    ]
    status = "READY"
    if not selected_rows:
        status = "BLOCKED_NO_ADAPTER"
    elif selected_rows[0]["adapter_name"] == "unknown-binary-fallback":
        status = "BLOCKED_UNSUPPORTED_SOURCE"
    elif missing_capabilities:
        status = "PARTIAL_CAPABILITY_COVERAGE"

    return {
        "schema": DOCUMENT_PARSING_PLAN_SCHEMA,
        "status": status,
        "source_id": source.source_id,
        "filename": source.filename,
        "source_hash": source.content_hash,
        "declared_mime": source.declared_mime,
        "detected_family": family,
        "detection_method": detection_method,
        "signature_hex": source.signature_hex,
        "selected_adapters": selected_rows,
        "alternative_adapters": alternatives,
        "required_capabilities": required_capabilities,
        "provided_capabilities": provided_capabilities,
        "missing_capabilities": missing_capabilities,
        "deferred_capability_triggers": {
            "SCANNED_PAGE_REQUIRES_OCR": "OCR",
            "PDF_TABLE_REGION_NOT_CELL_PARSED": "TABLE_STRUCTURE",
            "PDF_IMAGE_CONTENT_UNPARSED": "DIAGRAM_STRUCTURE_OR_VISUAL_PARSER",
        },
        "business_semantics_added": False,
        "document_order_is_business_flow": False,
        "filename_is_business_context": False,
    }


__all__ = ["plan_document_parsing"]
