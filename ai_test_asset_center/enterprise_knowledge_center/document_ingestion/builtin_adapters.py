"""Built-in format adapters.

DOCX and PDF wrap the existing source-preserving extractors.  Generic text is a
lower-fidelity fallback.  Unknown binary sources always produce an explicit blocked
IR rather than disappearing from enterprise understanding.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from collections import Counter
from typing import Any

from .._document_structure_ir import DOCUMENT_IR_SCHEMA, STRUCTURE_RECEIPT_SCHEMA
from .contract import (
    AdapterMatch,
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
    DocumentAdapter,
    DocumentSource,
    MODE_FALLBACK,
    MODE_PRIMARY,
)


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part or "").strip() for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _looks_like_docx(data: bytes) -> bool:
    if not data.startswith(b"PK"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            return "word/document.xml" in names and "[Content_Types].xml" in names
    except Exception:
        return False


def _decoded_text(data: bytes) -> tuple[str, float]:
    if not data:
        return "", 1.0
    decoded = data.decode("utf-8", errors="replace")
    replacement_ratio = decoded.count("\ufffd") / max(1, len(decoded))
    control_count = sum(1 for char in decoded if ord(char) < 32 and char not in "\n\r\t")
    control_ratio = control_count / max(1, len(decoded))
    confidence = max(0.0, 1.0 - replacement_ratio * 4.0 - control_ratio * 8.0)
    return decoded, round(confidence, 4)


def _enrich_pdf_table_gap_targets(document_ir: dict[str, Any]) -> dict[str, Any]:
    """Attach exact native table-region targets to the PDF gap receipt.

    The PDF extractor already emits source-preserving TABLE_REGION blocks.  Deferred visual
    recovery needs their page and identity to avoid clearing every table on a page after one
    partial success.  This function adds evidence only; it does not change table semantics.
    """
    result = dict(document_ir or {})
    tables = [
        dict(row)
        for row in (result.get("tables") or [])
        if isinstance(row, dict) and str(row.get("type") or "").strip() == "TABLE_REGION"
    ]
    pages = sorted(
        {
            int(row.get("page") or 0)
            for row in tables
            if str(row.get("page") or "").isdigit() and int(row.get("page") or 0) > 0
        }
    )
    region_ids = sorted(
        {
            str(row.get("block_id") or row.get("source_locator") or "").strip()
            for row in tables
            if str(row.get("block_id") or row.get("source_locator") or "").strip()
        }
    )
    unsupported: list[dict[str, Any]] = []
    for raw in result.get("unsupported_content") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        reason = str(row.get("reason_code") or row.get("kind") or "").strip()
        if reason == "PDF_TABLE_REGION_NOT_CELL_PARSED":
            row["pages"] = pages
            row["region_ids"] = region_ids
            row["count"] = len(region_ids) or int(row.get("count") or 0)
            row["target_evidence_complete"] = bool(region_ids)
        unsupported.append(row)
    receipt = dict(result.get("structure_receipt") or {})
    receipt["unsupported_content"] = unsupported
    receipt["unsupported_content_count"] = sum(int(row.get("count") or 0) for row in unsupported)
    result["tables"] = tables
    result["unsupported_content"] = unsupported
    result["structure_receipt"] = receipt
    return result


class DocxDocumentAdapter(DocumentAdapter):
    name = "docx-native-structure"
    parser_version = "1"
    priority = 100
    mode = MODE_PRIMARY
    capabilities = frozenset(
        {
            CAP_TEXT_EXTRACTION,
            CAP_HEADING_HIERARCHY,
            CAP_LIST_HIERARCHY,
            CAP_TABLE_STRUCTURE,
            CAP_HEADER_FOOTER,
            CAP_FONT_EVIDENCE,
        }
    )

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        signature = _looks_like_docx(source.data)
        suffix = source.suffix == ".docx"
        if not signature and not suffix:
            return None
        score = 120 if signature else 92
        reason = "docx_zip_container_signature" if signature else "docx_filename_suffix"
        return AdapterMatch(self.name, score, reason, tuple(sorted(self.capabilities)), self.mode)

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        from .._document_structure_ir_normalizer import extract_normalized_docx_document_ir

        return extract_normalized_docx_document_ir(source.data, filename=source.filename)


class PdfDocumentAdapter(DocumentAdapter):
    name = "pdf-native-layout"
    parser_version = "1"
    priority = 100
    mode = MODE_PRIMARY
    capabilities = frozenset(
        {
            CAP_TEXT_EXTRACTION,
            CAP_PAGE_LAYOUT,
            CAP_TEXT_COORDINATES,
            CAP_FONT_EVIDENCE,
            CAP_HEADING_HIERARCHY,
            CAP_LIST_HIERARCHY,
            CAP_TABLE_REGION_DETECTION,
            CAP_IMAGE_PRESENCE,
            CAP_HEADER_FOOTER,
        }
    )

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        signature = source.data.lstrip().startswith(b"%PDF-")
        suffix = source.suffix == ".pdf"
        if not signature and not suffix:
            return None
        score = 120 if signature else 90
        reason = "pdf_file_signature" if signature else "pdf_filename_suffix"
        return AdapterMatch(self.name, score, reason, tuple(sorted(self.capabilities)), self.mode)

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        from .._pdf_document_structure_ir import extract_pdf_document_ir

        return _enrich_pdf_table_gap_targets(
            extract_pdf_document_ir(source.data, filename=source.filename)
        )


class GenericTextDocumentAdapter(DocumentAdapter):
    name = "generic-text-structure"
    parser_version = "1"
    priority = 30
    mode = MODE_FALLBACK
    capabilities = frozenset(
        {
            CAP_TEXT_EXTRACTION,
            CAP_HEADING_HIERARCHY,
            CAP_LIST_HIERARCHY,
        }
    )

    _TEXT_SUFFIXES = {
        ".txt",
        ".md",
        ".markdown",
        ".rst",
        ".csv",
        ".tsv",
        ".json",
        ".yaml",
        ".yml",
        ".xml",
        ".html",
        ".htm",
        ".sql",
        ".log",
    }
    _LIST_RE = re.compile(
        r"^\s*(?:[-*•·▪◦‣]|\d{1,4}[.)、）]|[（(]\d{1,4}[）)]|"
        r"[一二三四五六七八九十百千]+[、.)）]|[（(][一二三四五六七八九十百千]+[）)]|"
        r"[①②③④⑤⑥⑦⑧⑨⑩]|[A-Za-z][.)、）])\s*\S"
    )

    _TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
    _TABLE_SEPARATOR_RE = re.compile(r"^\s*\|[\s:\-|]+\|\s*$")

    @staticmethod
    def _split_table_cells(raw: str) -> list[str]:
        text = str(raw or "").strip()
        if not text.startswith("|"):
            return []
        return [part.strip() for part in text.strip("|").split("|")]

    @staticmethod
    def _markdown_table_runs(lines: list[str]) -> list[tuple[int, int]]:
        """Return (start, end) 0-based index ranges of consecutive table lines.

        A run qualifies as a markdown table only when it contains a separator
        row (``|---|``), so pipe-delimited prose is never misclassified.
        """

        runs: list[tuple[int, int]] = []
        index = 0
        count = len(lines)
        while index < count:
            if not GenericTextDocumentAdapter._TABLE_ROW_RE.match(lines[index]):
                index += 1
                continue
            end = index
            while end < count and GenericTextDocumentAdapter._TABLE_ROW_RE.match(lines[end]):
                end += 1
            if any(
                GenericTextDocumentAdapter._TABLE_SEPARATOR_RE.match(lines[candidate])
                for candidate in range(index, end)
            ):
                runs.append((index, end))
            index = end
        return runs

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        decoded, confidence = _decoded_text(source.data)
        if source.legacy_text.strip() and not decoded.strip():
            decoded = source.legacy_text
            confidence = 0.85
        if not decoded.strip() or confidence < 0.72:
            return None
        score = 70 if source.suffix in self._TEXT_SUFFIXES else 52
        reason = "declared_text_family" if source.suffix in self._TEXT_SUFFIXES else "utf8_text_content_probe"
        return AdapterMatch(self.name, score, reason, tuple(sorted(self.capabilities)), self.mode)

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        decoded, confidence = _decoded_text(source.data)
        value = decoded if decoded.strip() else source.legacy_text
        blocks: list[dict[str, Any]] = []
        sections: list[dict[str, Any]] = []
        heading_stack: list[dict[str, Any]] = []
        table_assets: list[dict[str, Any]] = []
        offset = 0
        order = 0
        source_lines = value.splitlines()
        table_runs = self._markdown_table_runs(source_lines)
        table_cell_ids: dict[tuple[int, int, int], str] = {}
        table_run_of_line: dict[int, tuple[int, int]] = {}
        for run_index, (start, end) in enumerate(table_runs):
            for line_index in range(start, end):
                table_run_of_line[line_index] = (run_index, start, end)
        for line_number, raw in enumerate(source_lines, start=1):
            line_index = line_number - 1
            line = raw.strip()
            if not line:
                offset += len(raw) + 1
                continue
            table_run = table_run_of_line.get(line_index)
            if table_run is not None:
                run_index, run_start, run_end = table_run
                run_lines = source_lines[run_start:run_end]
                separator_index = next(
                    (
                        candidate
                        for candidate in range(len(run_lines))
                        if self._TABLE_SEPARATOR_RE.match(run_lines[candidate])
                    ),
                    -1,
                )
                header_cells = self._split_table_cells(run_lines[0])
                if separator_index < 0 or not header_cells:
                    offset += len(raw) + 1
                    continue
                if line_index == run_start + separator_index:
                    offset += len(raw) + 1
                    continue
                else:
                    prior_lines = [
                        candidate
                        for candidate in range(run_start, line_index)
                        if candidate != run_start + separator_index
                    ]
                    row_index = len(prior_lines) + 1
                table_id = _stable_id("table", source.source_id, run_index + 1, "markdown_table")
                cells = self._split_table_cells(raw)
                for column_index, cell_text in enumerate(cells, start=1):
                    order += 1
                    block_id = _stable_id(
                        "table_cell",
                        source.source_id,
                        run_index + 1,
                        row_index,
                        column_index,
                    )
                    start = offset + max(0, raw.find(cell_text)) if cell_text else offset
                    end = start + max(0, len(cell_text) - 1)
                    blocks.append(
                        {
                            "block_id": block_id,
                            "type": "TABLE_CELL",
                            "parent_id": table_id,
                            "order": order,
                            "region": "body",
                            "level": None,
                            "text": cell_text,
                            "row_index": row_index,
                            "column_index": column_index,
                            "row_span": 1,
                            "column_span": 1,
                            "start_offset": start,
                            "end_offset": end,
                            "source_locator": (
                                f"{source.filename or 'document.txt'}#line={line_number};"
                                f"table-cell={table_id}:r{row_index}c{column_index}"
                            ),
                            "structure_evidence": {
                                "method": "markdown_table_row",
                                "decode_confidence": confidence,
                            },
                        }
                    )
                    table_cell_ids[(run_index, row_index, column_index)] = block_id
                offset += len(raw) + 1
                continue
            order += 1
            heading_match = re.match(r"^\s*(#{1,6})\s+(.+?)\s*$", raw)
            level = len(heading_match.group(1)) if heading_match else None
            block_text = heading_match.group(2).strip() if heading_match else line
            if level:
                block_type = "HEADING"
                while heading_stack and int(heading_stack[-1].get("level") or 0) >= level:
                    heading_stack.pop()
                parent_id = heading_stack[-1]["block_id"] if heading_stack else ""
            elif self._LIST_RE.match(raw):
                block_type = "LIST_ITEM"
                parent_id = heading_stack[-1]["block_id"] if heading_stack else ""
            else:
                block_type = "PARAGRAPH"
                parent_id = heading_stack[-1]["block_id"] if heading_stack else ""
            start = offset + max(0, raw.find(line))
            end = start + len(line)
            block_id = _stable_id("text_block", source.source_id, line_number, block_type, block_text)
            block = {
                "block_id": block_id,
                "type": block_type,
                "parent_id": parent_id,
                "order": order,
                "region": "body",
                "level": level,
                "text": block_text,
                "start_offset": start,
                "end_offset": max(start, end - 1),
                "source_locator": f"{source.filename or 'document.txt'}#line={line_number};chars={start}-{max(start, end - 1)}",
                "structure_evidence": {
                    "method": "markdown_heading" if level else "visible_list_marker" if block_type == "LIST_ITEM" else "plain_text_line",
                    "decode_confidence": confidence,
                },
            }
            if block_type == "LIST_ITEM":
                block["numbering"] = {"numbered": True, "source": "visible_text_marker"}
            blocks.append(block)
            if level:
                heading_stack.append(block)
                sections.append(
                    {
                        "block_id": block_id,
                        "level": level,
                        "title": block_text,
                        "start_offset": start,
                        "source_locator": block["source_locator"],
                        "structure_evidence": block["structure_evidence"],
                    }
                )
            offset += len(raw) + 1
        for run_index, (start, end) in enumerate(table_runs):
            run_cell_ids = [
                table_cell_ids[key]
                for key in sorted(table_cell_ids)
                if key[0] == run_index
            ]
            if not run_cell_ids:
                continue
            run_lines = source_lines[start:end]
            header_cells = self._split_table_cells(run_lines[0])
            row_count = (
                max((key[1] for key in table_cell_ids if key[0] == run_index), default=0)
                + 1
            )
            table_id = _stable_id("table", source.source_id, run_index + 1, "markdown_table")
            table_assets.append(
                {
                    "block_id": table_id,
                    "table_id": table_id,
                    "cell_block_ids": run_cell_ids,
                    "row_count": row_count,
                    "column_count": max(len(header_cells), 1),
                    "source_locator": f"{source.filename or 'document.txt'}#table={run_index + 1}",
                    "structure_evidence": {
                        "method": "markdown_table_separator_detected",
                        "decode_confidence": confidence,
                    },
                }
            )
        block_counts = Counter(str(row.get("type") or "") for row in blocks)
        unsupported = []
        status = "COMPLETE"
        if confidence < 0.9:
            unsupported.append(
                {
                    "kind": "GENERIC_TEXT_DECODE_LOW_CONFIDENCE",
                    "count": 1,
                    "status": "TEXT_DECODE_PROJECTED",
                    "severity": "P1",
                    "blocks_formal_understanding": False,
                    "reason_code": "GENERIC_TEXT_DECODE_LOW_CONFIDENCE",
                    "included_in_plain_text_authority": True,
                    "decode_confidence": confidence,
                }
            )
            status = "PARTIAL"
        return {
            "schema": DOCUMENT_IR_SCHEMA,
            "format": source.suffix.lstrip(".") or "text",
            "filename": source.filename,
            "plain_text": value,
            "blocks": blocks,
            "sections": sections,
            "tables": table_assets,
            "unsupported_content": unsupported,
            "structure_receipt": {
                "schema": STRUCTURE_RECEIPT_SCHEMA,
                "status": status,
                "format": source.suffix.lstrip(".") or "text",
                "block_count": len(blocks),
                "source_traceability_rate": 1.0 if blocks else 0.0,
                "block_type_distribution": dict(block_counts),
                "section_count": len(sections),
                "unsupported_content_count": len(unsupported),
                "unsupported_content": unsupported,
                "document_order_is_business_flow": False,
                "filename_is_business_context": False,
                "generic_text_fallback": True,
            },
        }


class UnknownBinaryDocumentAdapter(DocumentAdapter):
    name = "unknown-binary-fallback"
    parser_version = "1"
    priority = -100
    mode = MODE_FALLBACK
    capabilities = frozenset()

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        return AdapterMatch(
            self.name,
            1,
            "no_supported_native_or_text_adapter_matched",
            (),
            self.mode,
        )

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        block_id = _stable_id("unknown_document_block", source.source_id, source.filename, source.content_hash)
        unsupported = [
            {
                "kind": "UNSUPPORTED_SOURCE_FORMAT",
                "count": 1,
                "status": "NO_RELIABLE_DOCUMENT_ADAPTER",
                "severity": "P0",
                "blocks_formal_understanding": True,
                "reason_code": "UNSUPPORTED_SOURCE_FORMAT",
                "included_in_plain_text_authority": False,
                "filename": source.filename,
                "signature_hex": source.signature_hex,
            }
        ]
        return {
            "schema": DOCUMENT_IR_SCHEMA,
            "format": "unknown",
            "filename": source.filename,
            "plain_text": "",
            "blocks": [
                {
                    "block_id": block_id,
                    "type": "UNKNOWN_BLOCK",
                    "parent_id": "",
                    "order": 1,
                    "region": "body",
                    "text": "",
                    "source_locator": f"{source.filename or 'unknown-source'}#whole-file",
                    "reason": "UNSUPPORTED_SOURCE_FORMAT",
                    "blocks_formal_understanding": True,
                }
            ],
            "sections": [],
            "tables": [],
            "unsupported_content": unsupported,
            "structure_receipt": {
                "schema": STRUCTURE_RECEIPT_SCHEMA,
                "status": "BLOCKED",
                "format": "unknown",
                "block_count": 1,
                "source_traceability_rate": 1.0,
                "block_type_distribution": {"UNKNOWN_BLOCK": 1},
                "section_count": 0,
                "unsupported_content_count": 1,
                "unsupported_content": unsupported,
                "document_order_is_business_flow": False,
                "filename_is_business_context": False,
            },
        }


__all__ = [
    "DocxDocumentAdapter",
    "PdfDocumentAdapter",
    "GenericTextDocumentAdapter",
    "UnknownBinaryDocumentAdapter",
]
