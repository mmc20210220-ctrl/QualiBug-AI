"""Capability-driven parsing planner.

The planner does not infer business meaning. It fingerprints the source, resolves
registered adapters, and records which structural capabilities are available or still
missing. Supplemental adapters are planned after primary structural gaps are known.
"""
from __future__ import annotations

import mimetypes
from typing import Any, Iterable

from .contract import (
    CAP_FONT_EVIDENCE,
    CAP_FORMULA_EXTRACTION,
    CAP_HEADER_FOOTER,
    CAP_HEADING_HIERARCHY,
    CAP_IMAGE_PRESENCE,
    CAP_LIST_HIERARCHY,
    CAP_OCR,
    CAP_PAGE_LAYOUT,
    CAP_PAGE_RENDERING,
    CAP_STYLE_SEMANTICS,
    CAP_TABLE_REGION_DETECTION,
    CAP_TABLE_STRUCTURE,
    CAP_TEXT_COORDINATES,
    CAP_TEXT_EXTRACTION,
    DOCUMENT_PARSING_PLAN_SCHEMA,
    MODE_FALLBACK,
    MODE_PRIMARY,
    MODE_SUPPLEMENTAL,
    DocumentSource,
    SupplementalContext,
    text,
    unique_text,
)
from .registry import DocumentAdapterRegistry

_IMAGE_SUFFIXES = {"png", "jpg", "jpeg", "tif", "tiff", "bmp", "gif", "webp"}
_VISUAL_OFFICE_FAMILIES = {"doc", "rtf", "odt", "ppt", "pptx", "odp"}
_TABLE_OFFICE_FAMILIES = {"xls", "xlsx", "ods"}
_DEFERRED_CAPABILITIES_BY_GAP: dict[str, tuple[str, ...]] = {
    "SCANNED_PAGE_REQUIRES_OCR": (CAP_PAGE_RENDERING, CAP_OCR),
    "PDF_TABLE_REGION_NOT_CELL_PARSED": (CAP_TABLE_STRUCTURE,),
    "PDF_IMAGE_CONTENT_UNPARSED": ("DIAGRAM_STRUCTURE", CAP_PAGE_RENDERING),
}


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
    signature = source.data[:16]
    if (
        signature.startswith(
            (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"II*\x00", b"MM\x00*", b"BM")
        )
        or (signature.startswith(b"RIFF") and b"WEBP" in signature)
    ):
        return "image", "raster_image_signature"
    if stripped.startswith((b"{", b"[")):
        return "structured_text", "json_like_prefix"
    mime, _encoding = mimetypes.guess_type(source.filename)
    if mime and mime.startswith("image/"):
        return "image", "mime_guess"
    if mime and mime.startswith("text/"):
        return "text", "mime_guess"
    suffix = source.suffix.lstrip(".")
    if suffix in _IMAGE_SUFFIXES:
        return "image", "filename_suffix"
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
    if family == "image":
        return unique_text(
            [
                CAP_PAGE_RENDERING,
                CAP_OCR,
                CAP_TEXT_EXTRACTION,
                CAP_TEXT_COORDINATES,
                CAP_PAGE_LAYOUT,
            ]
        )
    if family in _VISUAL_OFFICE_FAMILIES:
        return unique_text(
            [
                CAP_PAGE_RENDERING,
                CAP_OCR,
                CAP_TEXT_EXTRACTION,
                CAP_TEXT_COORDINATES,
                CAP_PAGE_LAYOUT,
            ]
        )
    if family in _TABLE_OFFICE_FAMILIES:
        return unique_text([CAP_TABLE_STRUCTURE, CAP_FORMULA_EXTRACTION, CAP_STYLE_SEMANTICS])
    if family in {
        "text",
        "structured_text",
        "txt",
        "md",
        "markdown",
        "rst",
        "json",
        "yaml",
        "yml",
        "xml",
        "html",
        "htm",
        "sql",
        "log",
        "csv",
        "tsv",
    }:
        return unique_text([CAP_TEXT_EXTRACTION, CAP_HEADING_HIERARCHY, CAP_LIST_HIERARCHY])
    return []


def _adapter_row(adapter: Any, match: Any) -> dict[str, Any]:
    return {
        **match.to_dict(),
        "parser_version": str(getattr(adapter, "parser_version", "")),
        "priority": int(getattr(adapter, "priority", 0)),
        "standalone": bool(getattr(adapter, "standalone", False)),
    }


def plan_document_parsing(
    source: DocumentSource,
    registry: DocumentAdapterRegistry,
) -> dict[str, Any]:
    family, detection_method = _detected_family(source)
    matches = registry.matches(source)
    primary_rows = [row for row in matches if row[1].mode == MODE_PRIMARY]
    supplemental_rows = [row for row in matches if row[1].mode == MODE_SUPPLEMENTAL]
    fallback_rows = [row for row in matches if row[1].mode == MODE_FALLBACK]
    required_capabilities = _required_capabilities(family)

    selected: list[tuple[Any, Any]] = []
    if primary_rows:
        # Supplemental capabilities are never run eagerly beside a native primary.
        # They are selected only after the primary exposes a concrete structural gap.
        selected.append(primary_rows[0])
    else:
        standalone_rows = [
            row
            for row in supplemental_rows
            if bool(getattr(row[0], "standalone", False))
            and set(row[1].capabilities) & set(required_capabilities)
        ]
        if standalone_rows:
            selected.append(standalone_rows[0])
        elif fallback_rows:
            # Generic text outranks the fail-visible unknown adapter by match score.
            selected.append(fallback_rows[0])

    provided_capabilities = unique_text(
        capability
        for _adapter, match in selected
        for capability in match.capabilities
    )
    missing_capabilities = sorted(set(required_capabilities) - set(provided_capabilities))
    alternatives = [
        match.to_dict()
        for adapter, match in matches
        if all(adapter.name != chosen.name for chosen, _chosen_match in selected)
    ]
    selected_rows = [_adapter_row(adapter, match) for adapter, match in selected]
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
            key: list(values) for key, values in _DEFERRED_CAPABILITIES_BY_GAP.items()
        },
        "business_semantics_added": False,
        "document_order_is_business_flow": False,
        "filename_is_business_context": False,
    }


def plan_deferred_supplementals(
    source: DocumentSource,
    primary_document_ir: dict[str, Any],
    registry: DocumentAdapterRegistry,
    *,
    excluded_names: Iterable[str] = (),
) -> dict[str, Any]:
    """Plan supplemental adapters from fail-visible primary structure gaps."""
    trigger_gaps = [
        dict(row)
        for row in (primary_document_ir.get("unsupported_content") or [])
        if isinstance(row, dict)
        and text(row.get("reason_code") or row.get("kind")) in _DEFERRED_CAPABILITIES_BY_GAP
        and int(row.get("count") or 0) > 0
    ]
    requested = unique_text(
        capability
        for row in trigger_gaps
        for capability in _DEFERRED_CAPABILITIES_BY_GAP[
            text(row.get("reason_code") or row.get("kind"))
        ]
    )
    context = SupplementalContext(
        primary_document_ir=dict(primary_document_ir or {}),
        trigger_gaps=tuple(trigger_gaps),
        requested_capabilities=tuple(requested),
    )
    matches = registry.supplemental_matches(
        source,
        context,
        excluded_names=excluded_names,
    )
    selected: list[tuple[Any, Any]] = []
    provided: set[str] = set()
    for adapter, match in matches:
        contribution = set(match.capabilities) & set(requested)
        if not contribution:
            continue
        selected.append((adapter, match))
        provided.update(contribution)
    missing = sorted(set(requested) - provided)
    status = "NOT_REQUIRED"
    if trigger_gaps and selected and not missing:
        status = "READY"
    elif trigger_gaps and selected and missing:
        status = "PARTIAL_REQUIRED_SUPPLEMENTAL_CAPABILITY"
    elif trigger_gaps and not selected:
        status = "BLOCKED_REQUIRED_SUPPLEMENTAL_ADAPTER_UNAVAILABLE"
    return {
        "schema": "qualibug.deferred-document-parsing-plan.v1",
        "status": status,
        "trigger_gaps": trigger_gaps,
        "requested_capabilities": requested,
        "provided_capabilities": sorted(provided),
        "missing_capabilities": missing,
        "selected_adapters": [_adapter_row(adapter, match) for adapter, match in selected],
        "business_semantics_added": False,
        "document_order_is_business_flow": False,
    }


__all__ = ["plan_document_parsing", "plan_deferred_supplementals"]
