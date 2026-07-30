"""Policy wrappers for native OOXML adapters.

The underlying DOCX/XLSX/PPTX adapters remain the only structural parsers. These wrappers
add source-container policy that must be consistent across families: preserve the original
OOXML subtype and fail visibly when embedded VBA code exists but is not interpreted.
"""
from __future__ import annotations

import io
import zipfile
from typing import Any

from .builtin_adapters import DocxDocumentAdapter
from .contract import AdapterMatch, DocumentSource
from .office_adapters import PresentationDocumentAdapter, SpreadsheetDocumentAdapter

_WORD_SUFFIXES = {".docx", ".docm", ".dotx", ".dotm"}
_WORD_MACRO_SUFFIXES = {".docm", ".dotm"}
_SPREADSHEET_MACRO_SUFFIXES = {".xlsm", ".xltm"}
_PRESENTATION_MACRO_SUFFIXES = {".pptm", ".potm", ".ppsm"}


def _zip_member_exists(data: bytes, member: str) -> bool:
    if not data.startswith(b"PK"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return member in set(archive.namelist())
    except Exception:
        return False


def _recompute_structure_status(result: dict[str, Any]) -> dict[str, Any]:
    unsupported = [
        dict(row)
        for row in (result.get("unsupported_content") or [])
        if isinstance(row, dict)
    ]
    critical = sum(
        int(row.get("count") or 0)
        for row in unsupported
        if bool(row.get("blocks_formal_understanding"))
    )
    status = "BLOCKED" if critical else "PARTIAL" if unsupported else "COMPLETE"
    receipt = dict(result.get("structure_receipt") or {})
    receipt.update(
        {
            "status": status,
            "unsupported_content": unsupported,
            "unsupported_content_count": sum(int(row.get("count") or 0) for row in unsupported),
            "critical_unsupported_content_count": critical,
        }
    )
    result["unsupported_content"] = unsupported
    result["structure_receipt"] = receipt
    return result


def apply_native_office_container_policy(
    document_ir: dict[str, Any],
    source: DocumentSource,
    *,
    macro_member: str,
    macro_suffixes: set[str],
    macro_reason_code: str,
) -> dict[str, Any]:
    """Preserve subtype and expose unparsed VBA without changing structural output."""

    result = dict(document_ir or {})
    source_format = source.suffix.lstrip(".") or str(result.get("format") or "unknown")
    macro_member_present = _zip_member_exists(source.data, macro_member)
    macro_suffix_declared = source.suffix in macro_suffixes
    unsupported = [
        dict(row)
        for row in (result.get("unsupported_content") or [])
        if isinstance(row, dict)
        and str(row.get("reason_code") or row.get("kind") or "") != macro_reason_code
    ]
    if macro_member_present or macro_suffix_declared:
        unsupported.append(
            {
                "kind": macro_reason_code,
                "reason_code": macro_reason_code,
                "count": 1,
                "status": "MACRO_CONTAINER_PRESENT_CODE_NOT_INTERPRETED",
                "severity": "P0",
                "blocks_formal_understanding": True,
                "included_in_plain_text_authority": False,
                "source_locator": f"{source.filename}#vba-project",
                "macro_member": macro_member,
                "macro_member_present": macro_member_present,
                "macro_suffix_declared": macro_suffix_declared,
            }
        )
    result["format"] = source_format
    result["filename"] = source.filename
    result["unsupported_content"] = unsupported
    receipt = dict(result.get("structure_receipt") or {})
    receipt.update(
        {
            "format": source_format,
            "ooxml_container_subtype": source_format,
            "vba_member_checked": macro_member,
            "vba_member_present": macro_member_present,
            "macro_suffix_declared": macro_suffix_declared,
            "macro_code_semantics_interpreted": False,
        }
    )
    result["structure_receipt"] = receipt
    return _recompute_structure_status(result)


class MacroAwareDocxDocumentAdapter(DocxDocumentAdapter):
    parser_version = "2"

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        match = super().probe(source)
        if match is not None:
            return match
        if source.suffix not in _WORD_SUFFIXES:
            return None
        return AdapterMatch(
            self.name,
            92,
            "ooxml_word_filename_suffix",
            tuple(sorted(self.capabilities)),
            self.mode,
        )

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        return apply_native_office_container_policy(
            super().extract(source),
            source,
            macro_member="word/vbaProject.bin",
            macro_suffixes=_WORD_MACRO_SUFFIXES,
            macro_reason_code="WORD_MACRO_CODE_NOT_PARSED",
        )


class MacroAwareSpreadsheetDocumentAdapter(SpreadsheetDocumentAdapter):
    parser_version = "2"

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        return apply_native_office_container_policy(
            super().extract(source),
            source,
            macro_member="xl/vbaProject.bin",
            macro_suffixes=_SPREADSHEET_MACRO_SUFFIXES,
            macro_reason_code="SPREADSHEET_MACRO_CODE_NOT_PARSED",
        )


class MacroAwarePresentationDocumentAdapter(PresentationDocumentAdapter):
    parser_version = "2"

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        return apply_native_office_container_policy(
            super().extract(source),
            source,
            macro_member="ppt/vbaProject.bin",
            macro_suffixes=_PRESENTATION_MACRO_SUFFIXES,
            macro_reason_code="PRESENTATION_MACRO_CODE_NOT_PARSED",
        )


__all__ = [
    "apply_native_office_container_policy",
    "MacroAwareDocxDocumentAdapter",
    "MacroAwareSpreadsheetDocumentAdapter",
    "MacroAwarePresentationDocumentAdapter",
]
