"""Capability-driven parsing planner.

The planner fingerprints sources and chooses structural adapters. Supplemental adapters
are selected only after a native primary exposes a concrete, fail-visible gap.
"""
from __future__ import annotations

import mimetypes
from typing import Any, Iterable

from ...enterprise_material_formats import inspect_pk_document_container
from .contract import (
    CAP_COMMENT_EXTRACTION,
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
from .image_decoding import sniff_image_source
from .registry import DocumentAdapterRegistry

# Exact Office subtypes are kept in detected_family for backward compatibility with the
# merger, which uses that field as the final source format. capability_family is the new
# normalized field used solely for capability selection.
_NATIVE_WORD_FORMATS = {"docx", "docm", "dotx", "dotm"}
_NATIVE_SPREADSHEET_FORMATS = {"xlsx", "xlsm", "xltx", "xltm"}
_NATIVE_PRESENTATION_FORMATS = {"pptx", "pptm", "potx", "potm", "ppsx", "ppsm"}
_COMPATIBLE_WORD_FORMATS = {"doc", "dot", "rtf", "odt", "wps", "wpt"}
_COMPATIBLE_SPREADSHEET_FORMATS = {"xls", "xlt", "xlsb", "ods", "et", "ett"}
_COMPATIBLE_PRESENTATION_FORMATS = {"ppt", "pot", "pps", "odp", "dps", "dpt"}
_ALL_WORD_FORMATS = _NATIVE_WORD_FORMATS | _COMPATIBLE_WORD_FORMATS
_ALL_SPREADSHEET_FORMATS = _NATIVE_SPREADSHEET_FORMATS | _COMPATIBLE_SPREADSHEET_FORMATS
_ALL_PRESENTATION_FORMATS = _NATIVE_PRESENTATION_FORMATS | _COMPATIBLE_PRESENTATION_FORMATS
_DATABASE_MODEL_FORMATS = {"pdm", "mwb", "sqlite", "sqlite3", "db"}
_TEXT_FIRST_FAMILIES = {"text", "structured_text"}
_DEFERRED_CAPABILITIES_BY_GAP: dict[str, tuple[str, ...]] = {
    "SCANNED_PAGE_REQUIRES_OCR": (
        CAP_PAGE_RENDERING,
        CAP_OCR,
        CAP_TABLE_REGION_DETECTION,
        CAP_TABLE_STRUCTURE,
    ),
    "PDF_TABLE_REGION_NOT_CELL_PARSED": (
        CAP_PAGE_RENDERING,
        CAP_TABLE_STRUCTURE,
    ),
    "PDF_IMAGE_CONTENT_UNPARSED": ("DIAGRAM_STRUCTURE", CAP_PAGE_RENDERING),
    "PRESENTATION_IMAGE_CONTENT_UNPARSED": (
        CAP_PAGE_RENDERING,
        CAP_OCR,
        CAP_TEXT_EXTRACTION,
    ),
    "SPREADSHEET_EMBEDDED_IMAGE_NOT_SEMANTICALLY_PARSED": (
        CAP_OCR,
        CAP_TEXT_EXTRACTION,
    ),
}


def _looks_like_svg_markup(source: DocumentSource) -> bool:
    if source.suffix != ".svg":
        return False
    stripped = source.data.lstrip()[:4096].lower()
    if stripped.startswith(b"<svg"):
        return True
    return stripped.startswith(b"<?xml") and b"<svg" in stripped


def _declared_format(source: DocumentSource) -> str:
    return source.suffix.lstrip(".").lower()


def _office_format_or_default(source: DocumentSource, allowed: set[str], default: str) -> str:
    declared = _declared_format(source)
    return declared if declared in allowed else default


def _detected_family(source: DocumentSource) -> tuple[str, str]:
    """Return a source format identifier plus the detection method.

    Exact Office and database-model subtypes are retained so the final Document IR never
    labels a MySQL Workbench model as ZIP or a SQLite database as an unknown binary.
    """

    stripped = source.data.lstrip()
    declared = _declared_format(source)
    if stripped.startswith(b"%PDF-"):
        return "pdf", "pdf_file_signature"
    if source.data.startswith(b"SQLite format 3\x00"):
        return (
            declared if declared in {"sqlite", "sqlite3", "db"} else "sqlite",
            "sqlite_file_signature",
        )
    if source.data.startswith(b"PK"):
        structural_family = inspect_pk_document_container(source.data)
        if structural_family == "mysql_workbench_model":
            return "mwb", "mysql_workbench_document_xml_container"
        try:
            import io
            import zipfile

            with zipfile.ZipFile(io.BytesIO(source.data)) as archive:
                names = set(archive.namelist())
                if "word/document.xml" in names:
                    return (
                        _office_format_or_default(source, _NATIVE_WORD_FORMATS, "docx"),
                        "office_open_xml_word_container",
                    )
                if "xl/workbook.xml" in names:
                    return (
                        _office_format_or_default(
                            source, _NATIVE_SPREADSHEET_FORMATS, "xlsx"
                        ),
                        "office_open_xml_excel_container",
                    )
                if "xl/workbook.bin" in names:
                    return "xlsb", "office_open_xml_binary_excel_container"
                if "ppt/presentation.xml" in names:
                    return (
                        _office_format_or_default(
                            source, _NATIVE_PRESENTATION_FORMATS, "pptx"
                        ),
                        "office_open_xml_presentation_container",
                    )
                if declared in (
                    _COMPATIBLE_WORD_FORMATS
                    | _COMPATIBLE_SPREADSHEET_FORMATS
                    | _COMPATIBLE_PRESENTATION_FORMATS
                ):
                    return declared, "compatible_office_zip_container"
            return "zip", "zip_container_signature"
        except Exception:
            if declared in (
                _COMPATIBLE_WORD_FORMATS
                | _COMPATIBLE_SPREADSHEET_FORMATS
                | _COMPATIBLE_PRESENTATION_FORMATS
            ):
                return declared, "compatible_office_pk_container_unreadable"
            return "zip_or_corrupt_container", "pk_signature_without_readable_zip_directory"
    # SVG is both an image and XML. Preserve its native tags, labels and identifiers before
    # any rendered/OCR supplemental path; otherwise the semantic layer receives pixels only.
    if _looks_like_svg_markup(source):
        return "structured_text", "svg_xml_markup_signature"
    image_family, image_reason = sniff_image_source(source)
    if image_family:
        return "image", image_reason
    if stripped.startswith((b"{", b"[")):
        return "structured_text", "json_like_prefix"
    mime, _encoding = mimetypes.guess_type(source.filename)
    if mime and mime.startswith("text/"):
        return "text", "mime_guess"
    return declared or "unknown", "filename_suffix_or_unknown"


def _capability_family(source_format: str) -> str:
    if source_format in _ALL_WORD_FORMATS:
        return "word"
    if source_format in _ALL_SPREADSHEET_FORMATS:
        return "spreadsheet"
    if source_format in _ALL_PRESENTATION_FORMATS:
        return "presentation"
    if source_format in _DATABASE_MODEL_FORMATS:
        return "database_model"
    if source_format in {"text", "structured_text"}:
        return source_format
    return source_format


def _word_capabilities() -> list[str]:
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


def _spreadsheet_capabilities() -> list[str]:
    return unique_text(
        [
            CAP_TEXT_EXTRACTION,
            CAP_TABLE_STRUCTURE,
            CAP_FORMULA_EXTRACTION,
            CAP_COMMENT_EXTRACTION,
            CAP_STYLE_SEMANTICS,
        ]
    )


def _presentation_capabilities() -> list[str]:
    return unique_text(
        [
            CAP_TEXT_EXTRACTION,
            CAP_HEADING_HIERARCHY,
            CAP_TABLE_STRUCTURE,
            CAP_IMAGE_PRESENCE,
            CAP_COMMENT_EXTRACTION,
            CAP_STYLE_SEMANTICS,
        ]
    )


def _required_capabilities(family: str) -> list[str]:
    if family == "word":
        return _word_capabilities()
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
    if family == "spreadsheet":
        return _spreadsheet_capabilities()
    if family == "presentation":
        return _presentation_capabilities()
    if family == "database_model":
        return unique_text([CAP_TEXT_EXTRACTION, CAP_TABLE_STRUCTURE])
    if family == "image":
        return unique_text(
            [
                CAP_PAGE_RENDERING,
                CAP_OCR,
                CAP_TEXT_EXTRACTION,
                CAP_TEXT_COORDINATES,
                CAP_PAGE_LAYOUT,
                CAP_TABLE_REGION_DETECTION,
                CAP_TABLE_STRUCTURE,
            ]
        )
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
        "svg",
    }:
        return unique_text(
            [CAP_TEXT_EXTRACTION, CAP_HEADING_HIERARCHY, CAP_LIST_HIERARCHY]
        )
    return []


def _adapter_row(adapter: Any, match: Any) -> dict[str, Any]:
    row = {
        **match.to_dict(),
        "parser_version": str(getattr(adapter, "parser_version", "")),
        "priority": int(getattr(adapter, "priority", 0)),
        "standalone": bool(getattr(adapter, "standalone", False)),
    }
    # Compatibility for adapters created before runtime readiness became part of the
    # contract. Their reason field already exposed this state; normalize it centrally.
    if "unavailable_at_runtime" in text(row.get("reason")):
        row["runtime_ready"] = False
        row["runtime_reason"] = text(row.get("runtime_reason")) or "RUNTIME_DEPENDENCY_UNAVAILABLE"
    return row


def plan_document_parsing(
    source: DocumentSource,
    registry: DocumentAdapterRegistry,
) -> dict[str, Any]:
    source_format, detection_method = _detected_family(source)
    capability_family = _capability_family(source_format)
    matches = registry.matches(source)
    primary_rows = [row for row in matches if row[1].mode == MODE_PRIMARY]
    supplemental_rows = [row for row in matches if row[1].mode == MODE_SUPPLEMENTAL]
    fallback_rows = [row for row in matches if row[1].mode == MODE_FALLBACK]
    required_capabilities = _required_capabilities(capability_family)

    selected: list[tuple[Any, Any]] = []
    if primary_rows:
        selected.append(primary_rows[0])
    elif capability_family in _TEXT_FIRST_FAMILIES and fallback_rows:
        # A source that is natively decodable text must not be rasterized first merely
        # because a visual renderer is available. Rendering can supplement a structural
        # parser later, but cannot replace exact tags, identifiers or source lines.
        selected.append(fallback_rows[0])
    else:
        provided: set[str] = set()
        for row in supplemental_rows:
            adapter, match = row
            if not bool(getattr(adapter, "standalone", False)):
                continue
            contribution = (set(match.capabilities) & set(required_capabilities)) - provided
            if not contribution:
                continue
            selected.append(row)
            provided.update(set(match.capabilities))
        if not selected and fallback_rows:
            selected.append(fallback_rows[0])

    provided_capabilities = unique_text(
        capability for _adapter, match in selected for capability in match.capabilities
    )
    missing_capabilities = sorted(
        set(required_capabilities) - set(provided_capabilities)
    )
    selected_rows = [_adapter_row(adapter, match) for adapter, match in selected]
    alternatives = [
        _adapter_row(adapter, match)
        for adapter, match in matches
        if all(adapter.name != chosen.name for chosen, _chosen_match in selected)
    ]
    runtime_blockers = [
        {
            "adapter_name": row.get("adapter_name"),
            "runtime_reason": row.get("runtime_reason") or "RUNTIME_DEPENDENCY_UNAVAILABLE",
        }
        for row in selected_rows
        if row.get("runtime_ready") is False
    ]
    status = "READY"
    if not selected_rows:
        status = "BLOCKED_NO_ADAPTER"
    elif selected_rows[0]["adapter_name"] == "unknown-binary-fallback":
        status = "BLOCKED_UNSUPPORTED_SOURCE"
    elif runtime_blockers:
        status = "BLOCKED_RUNTIME_DEPENDENCY_UNAVAILABLE"
    elif missing_capabilities:
        status = "PARTIAL_CAPABILITY_COVERAGE"
    return {
        "schema": DOCUMENT_PARSING_PLAN_SCHEMA,
        "status": status,
        "source_id": source.source_id,
        "filename": source.filename,
        "source_hash": source.content_hash,
        "declared_mime": source.declared_mime,
        # Legacy field retained because downstream mergers currently use it as source format.
        "detected_family": source_format,
        "detected_format": source_format,
        "capability_family": capability_family,
        "detection_method": detection_method,
        "signature_hex": source.signature_hex,
        "selected_adapters": selected_rows,
        "alternative_adapters": alternatives,
        "runtime_blockers": runtime_blockers,
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
    trigger_gaps = [
        dict(row)
        for row in (primary_document_ir.get("unsupported_content") or [])
        if isinstance(row, dict)
        and text(row.get("reason_code") or row.get("kind"))
        in _DEFERRED_CAPABILITIES_BY_GAP
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
        contribution = (set(match.capabilities) & set(requested)) - provided
        if not contribution:
            continue
        selected.append((adapter, match))
        provided.update(set(match.capabilities) & set(requested))
    selected_rows = [_adapter_row(adapter, match) for adapter, match in selected]
    runtime_blockers = [
        {
            "adapter_name": row.get("adapter_name"),
            "runtime_reason": row.get("runtime_reason") or "RUNTIME_DEPENDENCY_UNAVAILABLE",
        }
        for row in selected_rows
        if row.get("runtime_ready") is False
    ]
    missing = sorted(set(requested) - provided)
    status = "NOT_REQUIRED"
    if trigger_gaps and runtime_blockers:
        status = "BLOCKED_RUNTIME_DEPENDENCY_UNAVAILABLE"
    elif trigger_gaps and selected:
        status = "READY"
    elif trigger_gaps and not selected:
        status = "BLOCKED_REQUIRED_SUPPLEMENTAL_ADAPTER_UNAVAILABLE"
    return {
        "schema": "qualibug.deferred-document-parsing-plan.v1",
        "status": status,
        "trigger_gaps": trigger_gaps,
        "requested_capabilities": requested,
        "provided_capabilities": sorted(provided),
        "missing_capabilities": missing,
        "selected_adapters": selected_rows,
        "runtime_blockers": runtime_blockers,
        "business_semantics_added": False,
        "document_order_is_business_flow": False,
    }


__all__ = ["plan_document_parsing", "plan_deferred_supplementals"]
