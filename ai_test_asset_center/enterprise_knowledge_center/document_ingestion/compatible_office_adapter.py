"""Compatible Office/WPS container normalization through existing native adapters.

This module is intentionally a transport/format bridge, not another document parser.
Legacy Office, ODF and WPS containers are converted to OOXML with LibreOffice and then
passed to the existing DOCX/XLSX/PPTX adapters.  The resulting IR is rebound to the
immutable original source identity so conversion artifacts never become evidence roots.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .contract import (
    AdapterMatch,
    CAP_COMMENT_EXTRACTION,
    CAP_FONT_EVIDENCE,
    CAP_FORMULA_EXTRACTION,
    CAP_HEADER_FOOTER,
    CAP_HEADING_HIERARCHY,
    CAP_IMAGE_PRESENCE,
    CAP_LIST_HIERARCHY,
    CAP_STYLE_SEMANTICS,
    CAP_TABLE_STRUCTURE,
    CAP_TEXT_EXTRACTION,
    DocumentAdapter,
    DocumentSource,
    MODE_PRIMARY,
    text,
)

OFFICE_NORMALIZATION_RECEIPT_SCHEMA = "qualibug.office-container-normalization-receipt.v1"
_MAX_NORMALIZED_BYTES = 100 * 1024 * 1024

_WORD_SUFFIXES = {".doc", ".dot", ".rtf", ".odt", ".wps", ".wpt"}
_SPREADSHEET_SUFFIXES = {".xls", ".xlt", ".xlsb", ".ods", ".et", ".ett"}
_PRESENTATION_SUFFIXES = {".ppt", ".pot", ".pps", ".odp", ".dps", ".dpt"}
_COMPATIBLE_SUFFIXES = _WORD_SUFFIXES | _SPREADSHEET_SUFFIXES | _PRESENTATION_SUFFIXES


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _replace_locator_filename(locator: Any, derived_filename: str, original_filename: str) -> str:
    value = text(locator)
    if not value:
        return value
    if value == derived_filename:
        return original_filename
    if value.startswith(derived_filename + "#"):
        return original_filename + value[len(derived_filename) :]
    return value


def _status_from_gaps(gaps: list[dict[str, Any]]) -> str:
    if any(bool(row.get("blocks_formal_understanding")) for row in gaps):
        return "BLOCKED"
    return "PARTIAL" if gaps else "COMPLETE"


@dataclass(frozen=True)
class NormalizedOfficeContainer:
    data: bytes
    filename: str
    target_suffix: str
    receipt: dict[str, Any]


class OfficeContainerNormalizer(Protocol):
    name: str
    version: str

    def available(self) -> bool:
        ...

    def normalize(self, source: DocumentSource, target_suffix: str) -> NormalizedOfficeContainer:
        ...


class LibreOfficeContainerNormalizer:
    """Headless local conversion with an isolated LibreOffice user profile."""

    name = "libreoffice-office-container-normalizer"
    version = "1"

    def __init__(self, timeout_seconds: int = 120) -> None:
        self.timeout_seconds = max(10, min(600, int(timeout_seconds)))

    @staticmethod
    def _binary() -> str:
        return shutil.which("libreoffice") or shutil.which("soffice") or ""

    def available(self) -> bool:
        return bool(self._binary())

    def normalize(self, source: DocumentSource, target_suffix: str) -> NormalizedOfficeContainer:
        binary = self._binary()
        if not binary:
            raise RuntimeError("LibreOffice executable is unavailable")
        normalized_suffix = "." + target_suffix.lstrip(".").lower()
        safe_name = Path(source.filename or f"source{source.suffix or '.bin'}").name
        with tempfile.TemporaryDirectory(prefix="qualibug-office-normalize-") as directory:
            root = Path(directory)
            input_path = root / safe_name
            output_dir = root / "output"
            profile_dir = root / "profile"
            output_dir.mkdir(parents=True, exist_ok=True)
            profile_dir.mkdir(parents=True, exist_ok=True)
            input_path.write_bytes(source.data)
            command = [
                binary,
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
                "--convert-to",
                normalized_suffix.lstrip("."),
                "--outdir",
                str(output_dir),
                str(input_path),
            ]
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
            )
            candidates = sorted(output_dir.glob(f"{input_path.stem}.*"))
            output_path = next(
                (row for row in candidates if row.suffix.lower() == normalized_suffix),
                None,
            )
            stdout = (completed.stdout or b"").decode("utf-8", errors="replace")
            stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
            if completed.returncode != 0 or output_path is None or not output_path.is_file():
                detail = (stderr or stdout or "LibreOffice produced no normalized file")[:1000]
                raise RuntimeError(f"LibreOffice normalization failed: {detail}")
            normalized_data = output_path.read_bytes()
            if not normalized_data:
                raise RuntimeError("LibreOffice normalization produced an empty file")
            if len(normalized_data) > _MAX_NORMALIZED_BYTES:
                raise RuntimeError(
                    f"normalized Office container exceeds {_MAX_NORMALIZED_BYTES // (1024 * 1024)}MB limit"
                )
            derived_filename = f"{input_path.stem}{normalized_suffix}"
            receipt = {
                "schema": OFFICE_NORMALIZATION_RECEIPT_SCHEMA,
                "status": "COMPLETE",
                "normalizer_name": self.name,
                "normalizer_version": self.version,
                "source_filename": source.filename,
                "source_format": source.suffix.lstrip(".") or "unknown",
                "source_hash": source.content_hash,
                "target_format": normalized_suffix.lstrip("."),
                "derived_filename": derived_filename,
                "derived_hash": hashlib.sha256(normalized_data).hexdigest(),
                "source_byte_count": len(source.data),
                "derived_byte_count": len(normalized_data),
                "command_exit_code": int(completed.returncode),
                "stdout_excerpt": stdout[:500],
                "stderr_excerpt": stderr[:500],
                "network_access_used": False,
                "isolated_user_profile_used": True,
                "derived_container_is_not_evidence_root": True,
                "business_semantics_added": False,
            }
            return NormalizedOfficeContainer(
                data=normalized_data,
                filename=derived_filename,
                target_suffix=normalized_suffix,
                receipt=receipt,
            )


def _target_suffix(source_suffix: str) -> str:
    if source_suffix in _WORD_SUFFIXES:
        return ".docx"
    if source_suffix in _SPREADSHEET_SUFFIXES:
        return ".xlsx"
    if source_suffix in _PRESENTATION_SUFFIXES:
        return ".pptx"
    return ""


def _delegate_for(target_suffix: str) -> DocumentAdapter:
    if target_suffix == ".docx":
        from .builtin_adapters import DocxDocumentAdapter

        return DocxDocumentAdapter()
    if target_suffix == ".xlsx":
        from .office_adapters import SpreadsheetDocumentAdapter

        return SpreadsheetDocumentAdapter()
    if target_suffix == ".pptx":
        from .office_adapters import PresentationDocumentAdapter

        return PresentationDocumentAdapter()
    raise ValueError(f"unsupported normalized Office target: {target_suffix}")


def rebase_normalized_document_ir(
    document_ir: dict[str, Any],
    *,
    original_source: DocumentSource,
    normalized: NormalizedOfficeContainer,
) -> dict[str, Any]:
    """Rebind a derived OOXML IR to the original immutable source and addresses."""

    result = dict(document_ir or {})
    original_filename = original_source.filename
    derived_filename = normalized.filename
    id_map: dict[str, str] = {}
    table_id_map: dict[str, str] = {}
    blocks: list[dict[str, Any]] = []

    for index, raw in enumerate(result.get("blocks") or [], start=1):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        old_id = text(row.get("block_id"))
        locator = _replace_locator_filename(
            row.get("source_locator"), derived_filename, original_filename
        )
        new_id = _stable_id(
            "normalized_office_block",
            original_source.source_id,
            original_source.content_hash,
            locator,
            row.get("type"),
            row.get("order") or index,
            row.get("text"),
        )
        if old_id:
            id_map[old_id] = new_id
        row["block_id"] = new_id
        row["source_locator"] = locator
        evidence = dict(row.get("structure_evidence") or {})
        evidence["container_normalization"] = {
            "normalizer_name": normalized.receipt.get("normalizer_name"),
            "source_format": normalized.receipt.get("source_format"),
            "target_format": normalized.receipt.get("target_format"),
            "derived_hash": normalized.receipt.get("derived_hash"),
            "derived_container_is_not_evidence_root": True,
        }
        row["structure_evidence"] = evidence
        blocks.append(row)

    for row in blocks:
        parent_id = text(row.get("parent_id"))
        if parent_id in id_map:
            row["parent_id"] = id_map[parent_id]

    sections: list[dict[str, Any]] = []
    for raw in result.get("sections") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        old_id = text(row.get("block_id"))
        row["block_id"] = id_map.get(old_id, old_id)
        row["source_locator"] = _replace_locator_filename(
            row.get("source_locator"), derived_filename, original_filename
        )
        sections.append(row)

    tables: list[dict[str, Any]] = []
    for index, raw in enumerate(result.get("tables") or [], start=1):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        old_id = text(row.get("block_id"))
        locator = _replace_locator_filename(
            row.get("source_locator"), derived_filename, original_filename
        )
        new_id = id_map.get(old_id) or _stable_id(
            "normalized_office_table",
            original_source.source_id,
            original_source.content_hash,
            locator,
            index,
        )
        if old_id:
            table_id_map[old_id] = new_id
        row["block_id"] = new_id
        row["source_locator"] = locator
        row["cell_block_ids"] = [
            id_map.get(text(value), text(value))
            for value in (row.get("cell_block_ids") or [])
            if text(value)
        ]
        tables.append(row)

    for row in blocks:
        parent_id = text(row.get("parent_id"))
        if parent_id in table_id_map:
            row["parent_id"] = table_id_map[parent_id]

    pages: list[dict[str, Any]] = []
    for raw in result.get("pages") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row["source_locator"] = _replace_locator_filename(
            row.get("source_locator"), derived_filename, original_filename
        )
        pages.append(row)

    unsupported: list[dict[str, Any]] = []
    for raw in result.get("unsupported_content") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row["source_locator"] = _replace_locator_filename(
            row.get("source_locator"), derived_filename, original_filename
        )
        unsupported.append(row)
    unsupported.append(
        {
            "kind": "OFFICE_COMPATIBILITY_CONTAINER_NORMALIZED",
            "reason_code": "OFFICE_COMPATIBILITY_CONTAINER_NORMALIZED",
            "count": 1,
            "status": "SOURCE_PARSED_THROUGH_AUDITED_OOXML_NORMALIZATION",
            "severity": "P1",
            "blocks_formal_understanding": False,
            "included_in_plain_text_authority": False,
            "source_locator": f"{original_filename}#whole-file",
            "source_format": original_source.suffix.lstrip(".") or "unknown",
            "normalized_format": normalized.target_suffix.lstrip("."),
            "derived_container_hash": normalized.receipt.get("derived_hash"),
            "proprietary_objects_and_embedded_automation_independently_verified": False,
        }
    )

    status = _status_from_gaps(unsupported)
    structure_receipt = dict(result.get("structure_receipt") or {})
    structure_receipt.update(
        {
            "status": status,
            "format": original_source.suffix.lstrip(".") or "unknown",
            "source_container_format": original_source.suffix.lstrip(".") or "unknown",
            "normalized_container_format": normalized.target_suffix.lstrip("."),
            "normalization_receipt": dict(normalized.receipt),
            "unsupported_content": unsupported,
            "unsupported_content_count": sum(int(row.get("count") or 0) for row in unsupported),
            "critical_unsupported_content_count": sum(
                int(row.get("count") or 0)
                for row in unsupported
                if bool(row.get("blocks_formal_understanding"))
            ),
            "original_source_hash_is_evidence_root": True,
            "derived_container_is_not_evidence_root": True,
        }
    )
    result.update(
        {
            "format": original_source.suffix.lstrip(".") or "unknown",
            "filename": original_filename,
            "blocks": blocks,
            "sections": sections,
            "tables": tables,
            "pages": pages,
            "unsupported_content": unsupported,
            "structure_receipt": structure_receipt,
            "office_normalization_receipt": dict(normalized.receipt),
        }
    )
    return result


class CompatibleOfficeDocumentAdapter(DocumentAdapter):
    """Normalize compatible Office/WPS containers and reuse native OOXML adapters."""

    name = "compatible-office-normalization"
    parser_version = "1"
    priority = 108
    mode = MODE_PRIMARY
    capabilities = frozenset(
        {
            CAP_TEXT_EXTRACTION,
            CAP_HEADING_HIERARCHY,
            CAP_LIST_HIERARCHY,
            CAP_TABLE_STRUCTURE,
            CAP_HEADER_FOOTER,
            CAP_FONT_EVIDENCE,
            CAP_FORMULA_EXTRACTION,
            CAP_COMMENT_EXTRACTION,
            CAP_IMAGE_PRESENCE,
            CAP_STYLE_SEMANTICS,
        }
    )

    def __init__(self, normalizer: OfficeContainerNormalizer | None = None) -> None:
        self.normalizer = normalizer or LibreOfficeContainerNormalizer()

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        target = _target_suffix(source.suffix)
        if not target:
            return None
        availability = "available" if self.normalizer.available() else "unavailable_at_runtime"
        return AdapterMatch(
            self.name,
            112,
            f"compatible_office_suffix:{source.suffix}->{target};normalizer={availability}",
            tuple(sorted(self.capabilities)),
            self.mode,
        )

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        target = _target_suffix(source.suffix)
        if not target:
            raise ValueError(f"unsupported compatible Office source: {source.suffix}")
        normalized = self.normalizer.normalize(source, target)
        derived_source = DocumentSource(
            source_id=source.source_id,
            filename=normalized.filename,
            data=normalized.data,
            declared_mime="",
            legacy_text="",
        )
        delegate = _delegate_for(target)
        derived_ir = delegate.extract(derived_source)
        return rebase_normalized_document_ir(
            derived_ir,
            original_source=source,
            normalized=normalized,
        )


__all__ = [
    "OFFICE_NORMALIZATION_RECEIPT_SCHEMA",
    "NormalizedOfficeContainer",
    "OfficeContainerNormalizer",
    "LibreOfficeContainerNormalizer",
    "CompatibleOfficeDocumentAdapter",
    "rebase_normalized_document_ir",
]
