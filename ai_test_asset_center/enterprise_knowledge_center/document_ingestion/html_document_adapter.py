"""Source-preserving HTML document adapter.

HTML sources previously fell through to the generic plain-text fallback, which
split raw lines into text blocks — markup, attributes and inline SVG included.
Those markup "statements" then flowed into the structure-first fact compiler
and polluted the business fact ledger.

This adapter parses HTML with the standard library parser and emits Document IR
whose block text contains only visible text. It never infers business meaning;
headings, list items, table cells and paragraphs stay source-preserving with
character-offset locators into the original bytes.
"""
from __future__ import annotations

import re
from collections import Counter
from html.parser import HTMLParser
from typing import Any

from .._document_structure_ir import DOCUMENT_IR_SCHEMA, STRUCTURE_RECEIPT_SCHEMA
from .contract import (
    AdapterMatch,
    CAP_HEADING_HIERARCHY,
    CAP_LIST_HIERARCHY,
    CAP_TABLE_STRUCTURE,
    CAP_TEXT_EXTRACTION,
    DocumentAdapter,
    DocumentSource,
    MODE_PRIMARY,
)

_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
# Tags whose content must never become document text. All of them carry
# explicit closing tags in practice; void elements are handled separately so
# an omitted end tag can never wedge the skip state open.
_SKIP_TAGS = {"script", "style", "template", "noscript", "svg", "iframe", "object", "embed", "canvas", "title"}
_VOID_TAGS = {"meta", "link", "br", "img", "hr", "input", "source", "track", "wbr", "area", "base", "col", "param"}
_BLOCK_BREAK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "header",
    "footer",
    "aside",
    "main",
    "nav",
    "li",
    "tr",
    "br",
    "blockquote",
    "figure",
    "figcaption",
    "pre",
    "summary",
    "details",
}

_DOCTYPE_RE = re.compile(r"<!doctype\s+html", re.I)
_HTML_ROOT_RE = re.compile(r"<html[\s>]", re.I)
_MARKUP_DENSITY_TAGS_RE = re.compile(r"<(?:div|span|section|article|table|thead|tbody|ul|ol|li|p|h[1-6])\b", re.I)


def _stable_id(prefix: str, *parts: Any) -> str:
    import hashlib

    material = "\x1f".join(str(part or "").strip() for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _decode_html(data: bytes) -> tuple[str, float]:
    decoded = data.decode("utf-8", errors="replace")
    replacement_ratio = decoded.count("\ufffd") / max(1, len(decoded))
    confidence = max(0.0, 1.0 - replacement_ratio * 4.0)
    return decoded, round(confidence, 4)


def _looks_like_html(decoded: str) -> bool:
    head = decoded[:4096]
    if _DOCTYPE_RE.search(head) or _HTML_ROOT_RE.search(head):
        return True
    sample = decoded[:16384]
    if not sample.strip():
        return False
    tag_count = len(_MARKUP_DENSITY_TAGS_RE.findall(sample))
    return tag_count >= 8


class _HtmlStructureParser(HTMLParser):
    """Collect visible-text blocks with stable source offsets.

    The parser walks tags in document order and projects visible text into
    heading, list-item, table-cell and paragraph units. Markup never appears
    in emitted text; entities are converted by the standard library.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict[str, Any]] = []
        self.sections: list[dict[str, Any]] = []
        self.tables: list[dict[str, Any]] = []
        self.plain_lines: list[str] = []
        self._skip_depth = 0
        self._in_head = False
        self._heading_stack: list[dict[str, Any]] = []
        self._current_heading: dict[str, Any] | None = None
        self._current_list_item: dict[str, Any] | None = None
        self._paragraph_parts: list[str] = []
        self._paragraph_started = False
        self._table_stack: list[dict[str, Any]] = []
        self._current_cell: dict[str, Any] | None = None
        self._order = 0

    # -- helpers ---------------------------------------------------------
    def _parent_id(self) -> str:
        return self._heading_stack[-1]["block_id"] if self._heading_stack else ""

    def _flush_paragraph(self) -> None:
        text_value = re.sub(r"\s+", " ", "".join(self._paragraph_parts)).strip()
        self._paragraph_parts = []
        started = self._paragraph_started
        self._paragraph_started = False
        if not text_value or not started:
            return
        self._order += 1
        self.blocks.append(
            {
                "type": "PARAGRAPH",
                "parent_id": self._parent_id(),
                "order": self._order,
                "text": text_value,
            }
        )

    # -- parser hooks ----------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _VOID_TAGS:
            return
        if tag == "head":
            self._in_head = True
            return
        if tag == "body":
            self._in_head = False
            return
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth or self._in_head:
            return
        if tag in _HEADING_TAGS:
            self._flush_paragraph()
            if self._current_heading is not None:
                self._finish_heading()
            self._current_heading = {
                "level": _HEADING_TAGS[tag],
                "parts": [],
            }
            return
        if tag == "li":
            self._flush_paragraph()
            if self._current_list_item is not None:
                self._finish_list_item()
            self._current_list_item = {"parts": []}
            return
        if tag == "table":
            self._flush_paragraph()
            self._table_stack.append({"rows": [], "cells": [], "row_count": 0})
            return
        if tag == "tr" and self._table_stack:
            self._table_stack[-1]["row_count"] += 1
            return
        if tag in {"td", "th"} and self._table_stack:
            if self._current_cell is not None:
                self._finish_cell()
            self._current_cell = {"parts": [], "header": tag == "th"}
            return
        if tag in _BLOCK_BREAK_TAGS:
            self._flush_paragraph()
            return
        self._paragraph_started = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _VOID_TAGS:
            return
        if tag == "head":
            self._in_head = False
            return
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth or self._in_head:
            return
        if tag in _HEADING_TAGS and self._current_heading is not None:
            self._finish_heading()
            return
        if tag == "li" and self._current_list_item is not None:
            self._finish_list_item()
            return
        if tag in {"td", "th"} and self._current_cell is not None:
            self._finish_cell()
            return
        if tag == "table" and self._table_stack:
            self._flush_paragraph()
            table = self._table_stack.pop()
            if table["cells"]:
                self.tables.append(table)
            return
        if tag in _BLOCK_BREAK_TAGS:
            self._flush_paragraph()

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._in_head or not data.strip():
            return
        self._paragraph_started = True
        if self._current_heading is not None:
            self._current_heading["parts"].append(data)
        elif self._current_list_item is not None:
            self._current_list_item["parts"].append(data)
        elif self._current_cell is not None:
            self._current_cell["parts"].append(data)
        else:
            self._paragraph_parts.append(data)

    def close(self) -> None:  # type: ignore[override]
        super().close()
        if self._current_heading is not None:
            self._finish_heading()
        if self._current_list_item is not None:
            self._finish_list_item()
        if self._current_cell is not None:
            self._finish_cell()
        self._flush_paragraph()
        while self._table_stack:
            table = self._table_stack.pop()
            if table["cells"]:
                self.tables.append(table)

    # -- finishers ---------------------------------------------------------
    def _finish_heading(self) -> None:
        heading = self._current_heading
        self._current_heading = None
        if heading is None:
            return
        text_value = re.sub(r"\s+", " ", "".join(heading["parts"])).strip()
        if not text_value:
            return
        level = int(heading["level"])
        while self._heading_stack and self._heading_stack[-1]["level"] >= level:
            self._heading_stack.pop()
        self._order += 1
        block = {
            "type": "HEADING",
            "parent_id": self._parent_id(),
            "order": self._order,
            "level": level,
            "text": text_value,
        }
        self._heading_stack.append({"block_id": "", "level": level, "title": text_value, "block": block})
        self.blocks.append(block)
        self.sections.append({"level": level, "title": text_value})

    def _finish_list_item(self) -> None:
        item = self._current_list_item
        self._current_list_item = None
        if item is None:
            return
        text_value = re.sub(r"\s+", " ", "".join(item["parts"])).strip()
        if not text_value:
            return
        self._order += 1
        self.blocks.append(
            {
                "type": "LIST_ITEM",
                "parent_id": self._parent_id(),
                "order": self._order,
                "text": text_value,
            }
        )

    def _finish_cell(self) -> None:
        cell = self._current_cell
        self._current_cell = None
        if cell is None or not self._table_stack:
            return
        text_value = re.sub(r"\s+", " ", "".join(cell["parts"])).strip()
        if not text_value:
            return
        table = self._table_stack[-1]
        row_index = max(1, table["row_count"])
        column_index = sum(1 for row in table["cells"] if row["row_index"] == row_index) + 1
        table["cells"].append(
            {
                "row_index": row_index,
                "column_index": column_index,
                "header": bool(cell["header"]),
                "text": text_value,
            }
        )


class HtmlDocumentAdapter(DocumentAdapter):
    name = "html-native-structure"
    parser_version = "1"
    priority = 90
    mode = MODE_PRIMARY
    capabilities = frozenset(
        {
            CAP_TEXT_EXTRACTION,
            CAP_HEADING_HIERARCHY,
            CAP_LIST_HIERARCHY,
            CAP_TABLE_STRUCTURE,
        }
    )

    _HTML_SUFFIXES = {".html", ".htm", ".xhtml"}

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        decoded, confidence = _decode_html(source.data)
        if not decoded.strip() or confidence < 0.72:
            return None
        suffix_match = source.suffix in self._HTML_SUFFIXES
        signature_match = _looks_like_html(decoded)
        if not suffix_match and not signature_match:
            return None
        score = 118 if signature_match and suffix_match else 110 if signature_match else 96
        reason = (
            "html_signature_and_suffix"
            if signature_match and suffix_match
            else "html_content_signature"
            if signature_match
            else "html_filename_suffix"
        )
        return AdapterMatch(self.name, score, reason, tuple(sorted(self.capabilities)), self.mode)

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        decoded, confidence = _decode_html(source.data)
        parser = _HtmlStructureParser()
        parser.feed(decoded)
        parser.close()

        filename = source.filename or "document.html"
        blocks: list[dict[str, Any]] = []
        sections: list[dict[str, Any]] = []
        search_from = 0
        for raw_block in parser.blocks:
            text_value = str(raw_block.get("text") or "")
            start = decoded.find(text_value, search_from)
            if start < 0:
                start = max(0, decoded.find(text_value))
            search_from = max(search_from, start + len(text_value))
            end = start + max(1, len(text_value)) - 1
            line_number = decoded.count("\n", 0, start) + 1 if start >= 0 else 1
            block_id = _stable_id(
                "text_block", source.source_id, line_number, raw_block["type"], text_value
            )
            block = {
                "block_id": block_id,
                "type": raw_block["type"],
                "parent_id": raw_block.get("parent_id") or "",
                "order": int(raw_block.get("order") or 0),
                "region": "body",
                "level": raw_block.get("level"),
                "text": text_value,
                "start_offset": start if start >= 0 else 0,
                "end_offset": end,
                "source_locator": f"{filename}#line={line_number};chars={max(0, start)}-{end}",
                "structure_evidence": {
                    "method": "html_native_structure",
                    "decode_confidence": confidence,
                },
            }
            blocks.append(block)

        # Assign heading hierarchy and parent non-heading blocks under the
        # nearest preceding heading, now that every block id exists.
        heading_index: dict[str, str] = {}
        stack: list[tuple[int, str]] = []
        current_heading_id = ""
        for raw_block, block in zip(parser.blocks, blocks):
            if raw_block["type"] == "HEADING":
                level = int(raw_block.get("level") or 1)
                while stack and stack[-1][0] >= level:
                    stack.pop()
                block["parent_id"] = stack[-1][1] if stack else ""
                stack.append((level, block["block_id"]))
                heading_index[raw_block.get("text") or ""] = block["block_id"]
                current_heading_id = block["block_id"]
            else:
                block["parent_id"] = current_heading_id
        for raw_section in parser.sections:
            sections.append(
                {
                    "block_id": heading_index.get(raw_section.get("title") or "", ""),
                    "level": int(raw_section.get("level") or 1),
                    "title": raw_section.get("title") or "",
                    "structure_evidence": {
                        "method": "html_heading_tag",
                        "decode_confidence": confidence,
                    },
                }
            )

        table_assets: list[dict[str, Any]] = []
        for table_index, table in enumerate(parser.tables):
            table_id = _stable_id("table", source.source_id, table_index + 1, "html_table")
            cell_block_ids: list[str] = []
            max_column = 0
            for cell in table["cells"]:
                row_index = int(cell.get("row_index") or 1)
                column_index = int(cell.get("column_index") or 1)
                max_column = max(max_column, column_index)
                cell_text = str(cell.get("text") or "")
                cell_id = _stable_id(
                    "table_cell", source.source_id, table_index + 1, row_index, column_index
                )
                start = decoded.find(cell_text)
                end = start + max(1, len(cell_text)) - 1 if start >= 0 else 0
                line_number = decoded.count("\n", 0, start) + 1 if start >= 0 else 1
                cell_block_ids.append(cell_id)
                blocks.append(
                    {
                        "block_id": cell_id,
                        "type": "TABLE_CELL",
                        "parent_id": table_id,
                        "order": len(blocks) + 1,
                        "region": "body",
                        "level": None,
                        "text": cell_text,
                        "row_index": row_index,
                        "column_index": column_index,
                        "row_span": 1,
                        "column_span": 1,
                        "start_offset": max(0, start),
                        "end_offset": end,
                        "source_locator": (
                            f"{filename}#line={line_number};table-cell={table_id}:"
                            f"r{row_index}c{column_index}"
                        ),
                        "structure_evidence": {
                            "method": "html_table_cell",
                            "decode_confidence": confidence,
                        },
                    }
                )
            table_assets.append(
                {
                    "block_id": table_id,
                    "table_id": table_id,
                    "cell_block_ids": cell_block_ids,
                    "row_count": int(table.get("row_count") or 0),
                    "column_count": max(max_column, 1),
                    "source_locator": f"{filename}#table={table_index + 1}",
                    "structure_evidence": {
                        "method": "html_table_structure",
                        "decode_confidence": confidence,
                    },
                }
            )

        plain_lines = [str(block.get("text") or "") for block in blocks if block.get("text")]
        block_counts = Counter(str(row.get("type") or "") for row in blocks)
        unsupported: list[dict[str, Any]] = []
        status = "COMPLETE"
        if confidence < 0.9:
            status = "PARTIAL"
            unsupported.append(
                {
                    "kind": "HTML_DECODE_LOW_CONFIDENCE",
                    "count": 1,
                    "status": "HTML_DECODE_PROJECTED",
                    "severity": "P1",
                    "blocks_formal_understanding": False,
                    "reason_code": "HTML_DECODE_LOW_CONFIDENCE",
                    "included_in_plain_text_authority": True,
                    "decode_confidence": confidence,
                }
            )
        if not blocks:
            status = "PARTIAL"
            unsupported.append(
                {
                    "kind": "HTML_NO_VISIBLE_TEXT",
                    "count": 1,
                    "status": "VISIBLE_TEXT_EMPTY",
                    "severity": "P1",
                    "blocks_formal_understanding": False,
                    "reason_code": "HTML_NO_VISIBLE_TEXT",
                    "included_in_plain_text_authority": False,
                }
            )
        return {
            "schema": DOCUMENT_IR_SCHEMA,
            "format": "html",
            "filename": source.filename,
            "plain_text": "\n".join(plain_lines),
            "blocks": blocks,
            "sections": sections,
            "tables": table_assets,
            "unsupported_content": unsupported,
            "structure_receipt": {
                "schema": STRUCTURE_RECEIPT_SCHEMA,
                "status": status,
                "format": "html",
                "block_count": len(blocks),
                "source_traceability_rate": 1.0 if blocks else 0.0,
                "block_type_distribution": dict(block_counts),
                "section_count": len(sections),
                "unsupported_content_count": len(unsupported),
                "unsupported_content": unsupported,
                "document_order_is_business_flow": False,
                "filename_is_business_context": False,
                "generic_text_fallback": False,
                "markup_excluded_from_block_text": True,
            },
        }


__all__ = ["HtmlDocumentAdapter"]
