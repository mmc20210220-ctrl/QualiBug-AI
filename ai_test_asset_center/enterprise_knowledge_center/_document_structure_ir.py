"""Source-preserving document structure IR.

The first implementation is deliberately strongest for DOCX because Word files are
common enterprise source material and the former decoder flattened paragraphs,
styles, numbering and tables into one string.  This module keeps those structures
as auditable blocks.  It performs format interpretation only; it does not create
business facts or infer business flow from document order.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from collections import Counter
from typing import Any, Iterable

DOCUMENT_IR_SCHEMA = "qualibug.document-structure-ir.v1"
STRUCTURE_RECEIPT_SCHEMA = "qualibug.document-structure-receipt.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _enum_value(value: Any) -> str:
    if value is None:
        return ""
    name = getattr(value, "name", None)
    return _text(name or value)


def _length_points(value: Any) -> float | None:
    if value is None:
        return None
    points = getattr(value, "pt", None)
    try:
        return round(float(points if points is not None else value), 2)
    except (TypeError, ValueError):
        return None


def _xml_val(parent: Any, child_name: str) -> str:
    if parent is None:
        return ""
    try:
        from docx.oxml.ns import qn

        child = parent.find(qn(child_name))
        if child is None:
            return ""
        return _text(child.get(qn("w:val")))
    except Exception:
        return ""


def _paragraph_numbering(paragraph: Any) -> dict[str, Any]:
    p_pr = getattr(getattr(paragraph, "_p", None), "pPr", None)
    num_pr = getattr(p_pr, "numPr", None)
    if num_pr is None:
        return {}
    num_id = _xml_val(num_pr, "w:numId")
    level = _xml_val(num_pr, "w:ilvl")
    result: dict[str, Any] = {"numbered": True}
    if num_id:
        result["num_id"] = num_id
    if level:
        try:
            result["level"] = int(level)
        except ValueError:
            result["level"] = level
    return result


def _outline_level(paragraph: Any) -> int | None:
    p_pr = getattr(getattr(paragraph, "_p", None), "pPr", None)
    raw = _xml_val(p_pr, "w:outlineLvl")
    if not raw:
        style = getattr(paragraph, "style", None)
        style_p_pr = getattr(getattr(style, "_element", None), "pPr", None)
        raw = _xml_val(style_p_pr, "w:outlineLvl")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value + 1 if 0 <= value <= 8 else None


_STYLE_HEADING_RE = re.compile(
    r"^(?:heading|标题|標題|überschrift|titre|titolo|encabezado)\s*([1-9])$",
    re.I,
)
_TEXT_HEADING_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"^第[一二三四五六七八九十百千0-9]+(?:篇|章|部分)\s*.*$"), 1),
    (re.compile(r"^第[一二三四五六七八九十百千0-9]+节\s*.*$"), 2),
    (re.compile(r"^第[一二三四五六七八九十百千0-9]+(?:条|款|项)\s*.*$"), 3),
    (re.compile(r"^\d+(?:\.\d+){1,5}\s+\S.*$"), 2),
    (re.compile(r"^[一二三四五六七八九十百千]+[、.]\s*\S.*$"), 1),
    (re.compile(r"^[（(][一二三四五六七八九十百千]+[）)]\s*\S.*$"), 2),
)


def _heading_level(paragraph: Any, text: str, style_summary: dict[str, Any]) -> tuple[int | None, str]:
    outline = _outline_level(paragraph)
    if outline:
        return outline, "docx_outline_level"
    style_name = _text(style_summary.get("style_name"))
    match = _STYLE_HEADING_RE.match(style_name)
    if match:
        return int(match.group(1)), "docx_paragraph_style"
    if style_name.lower() in {"title", "标题", "文档标题", "document title"}:
        return 1, "docx_title_style"
    # Text patterns are accepted only when the paragraph is short and carries at
    # least one layout signal.  This avoids turning ordinary numbered sentences
    # into headings merely because they start with "1." or "（一）".
    has_layout_signal = bool(
        style_summary.get("bold")
        or style_summary.get("keep_with_next")
        or (style_summary.get("space_before_pt") or 0) > 0
        or style_summary.get("alignment") in {"CENTER", "center", "1"}
    )
    if len(text) <= 120 and has_layout_signal:
        for pattern, level in _TEXT_HEADING_PATTERNS:
            if pattern.match(text):
                return level, "text_pattern_with_docx_layout_evidence"
    return None, ""


def _run_summary(paragraph: Any) -> dict[str, Any]:
    runs = [run for run in getattr(paragraph, "runs", []) if _text(getattr(run, "text", ""))]
    if not runs:
        return {
            "bold": False,
            "italic": False,
            "underline": False,
            "font_sizes_pt": [],
            "font_names": [],
        }
    visible_count = len(runs)
    bold_count = sum(1 for run in runs if bool(getattr(run, "bold", False)))
    italic_count = sum(1 for run in runs if bool(getattr(run, "italic", False)))
    underline_count = sum(1 for run in runs if bool(getattr(run, "underline", False)))
    sizes = sorted(
        {
            size
            for run in runs
            if (size := _length_points(getattr(getattr(run, "font", None), "size", None))) is not None
        }
    )
    fonts = sorted(
        {
            _text(getattr(getattr(run, "font", None), "name", ""))
            for run in runs
            if _text(getattr(getattr(run, "font", None), "name", ""))
        }
    )
    return {
        "bold": bold_count == visible_count,
        "bold_run_ratio": round(bold_count / visible_count, 4),
        "italic": italic_count == visible_count,
        "italic_run_ratio": round(italic_count / visible_count, 4),
        "underline": underline_count == visible_count,
        "underline_run_ratio": round(underline_count / visible_count, 4),
        "font_sizes_pt": sizes,
        "max_font_size_pt": max(sizes) if sizes else None,
        "font_names": fonts,
    }


def _paragraph_style(paragraph: Any) -> dict[str, Any]:
    paragraph_format = getattr(paragraph, "paragraph_format", None)
    style = getattr(paragraph, "style", None)
    result = {
        "style_name": _text(getattr(style, "name", "")),
        "alignment": _enum_value(getattr(paragraph, "alignment", None)),
        "left_indent_pt": _length_points(getattr(paragraph_format, "left_indent", None)),
        "right_indent_pt": _length_points(getattr(paragraph_format, "right_indent", None)),
        "first_line_indent_pt": _length_points(getattr(paragraph_format, "first_line_indent", None)),
        "space_before_pt": _length_points(getattr(paragraph_format, "space_before", None)),
        "space_after_pt": _length_points(getattr(paragraph_format, "space_after", None)),
        "line_spacing": _text(getattr(paragraph_format, "line_spacing", "")),
        "keep_with_next": bool(getattr(paragraph_format, "keep_with_next", False)),
        "keep_together": bool(getattr(paragraph_format, "keep_together", False)),
        "page_break_before": bool(getattr(paragraph_format, "page_break_before", False)),
        "widow_control": bool(getattr(paragraph_format, "widow_control", False)),
    }
    result.update(_run_summary(paragraph))
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _contains_page_break(paragraph: Any) -> bool:
    try:
        from docx.oxml.ns import qn

        for br in paragraph._p.iter(qn("w:br")):
            if _text(br.get(qn("w:type"))).lower() in {"page", "column"}:
                return True
    except Exception:
        return False
    return False


def _table_cell_metadata(cell: Any) -> dict[str, Any]:
    tc_pr = getattr(getattr(cell, "_tc", None), "tcPr", None)
    grid_span = _xml_val(tc_pr, "w:gridSpan")
    v_merge = _xml_val(tc_pr, "w:vMerge")
    result: dict[str, Any] = {}
    if grid_span:
        try:
            result["grid_span"] = int(grid_span)
        except ValueError:
            result["grid_span"] = grid_span
    if v_merge or (tc_pr is not None and _xml_val(tc_pr, "w:vMerge") == ""):
        result["vertical_merge"] = v_merge or "continue"
    return result


def _zip_feature_counts(data: bytes) -> dict[str, int]:
    counts = {
        "comments": 0,
        "footnotes": 0,
        "endnotes": 0,
        "textboxes": 0,
        "tracked_insertions": 0,
        "tracked_deletions": 0,
    }
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            for name, key in (
                ("word/comments.xml", "comments"),
                ("word/footnotes.xml", "footnotes"),
                ("word/endnotes.xml", "endnotes"),
            ):
                if name in names:
                    xml = archive.read(name)
                    counts[key] = max(1, xml.count(b"<w:" + key[:-1].encode("ascii")))
            document_xml = archive.read("word/document.xml") if "word/document.xml" in names else b""
            counts["textboxes"] = document_xml.count(b"<w:txbxContent")
            counts["tracked_insertions"] = document_xml.count(b"<w:ins")
            counts["tracked_deletions"] = document_xml.count(b"<w:del")
    except Exception:
        return counts
    return counts


def _unsupported_content(feature_counts: dict[str, int], inline_shape_count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    features = {
        "INLINE_IMAGE_OR_SHAPE": inline_shape_count,
        "COMMENT": feature_counts.get("comments", 0),
        "FOOTNOTE": feature_counts.get("footnotes", 0),
        "ENDNOTE": feature_counts.get("endnotes", 0),
        "TEXTBOX": feature_counts.get("textboxes", 0),
        "TRACKED_INSERTION": feature_counts.get("tracked_insertions", 0),
        "TRACKED_DELETION": feature_counts.get("tracked_deletions", 0),
    }
    for kind, count in features.items():
        if not count:
            continue
        rows.append(
            {
                "kind": kind,
                "count": int(count),
                "status": "PRESENT_REQUIRES_SECONDARY_PARSER",
                "included_in_plain_text_authority": False,
            }
        )
    return rows


def extract_docx_document_ir(data: bytes, filename: str = "") -> dict[str, Any]:
    """Extract DOCX paragraphs, numbering, styles and tables in source order."""
    try:
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        from docx.oxml.ns import qn
    except ImportError as exc:
        raise ImportError("python-docx is required for DOCX structure extraction") from exc

    document = docx.Document(io.BytesIO(data))
    body_parent = document._body
    blocks: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    main_text_parts: list[str] = []
    heading_stack: list[dict[str, Any]] = []
    offset = 0
    order = 0
    table_index = 0

    def append_main_text(value: str) -> tuple[int, int]:
        nonlocal offset
        value = str(value or "")
        start = offset
        main_text_parts.append(value)
        offset += len(value)
        return start, offset

    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, body_parent)
            paragraph_text = str(paragraph.text or "").strip()
            if not paragraph_text and not _contains_page_break(paragraph):
                continue
            order += 1
            style = _paragraph_style(paragraph)
            numbering = _paragraph_numbering(paragraph)
            heading_level, heading_method = _heading_level(paragraph, paragraph_text, style)
            if heading_level:
                block_type = "HEADING"
                while heading_stack and int(heading_stack[-1]["level"]) >= heading_level:
                    heading_stack.pop()
                parent_id = heading_stack[-1]["block_id"] if heading_stack else ""
            elif numbering:
                block_type = "LIST_ITEM"
                parent_id = heading_stack[-1]["block_id"] if heading_stack else ""
            else:
                block_type = "PARAGRAPH"
                parent_id = heading_stack[-1]["block_id"] if heading_stack else ""
            rendered = paragraph_text + "\n"
            start, end = append_main_text(rendered)
            block_id = _stable_id("document_block", filename, order, block_type, paragraph_text)
            block = {
                "block_id": block_id,
                "type": block_type,
                "parent_id": parent_id,
                "order": order,
                "region": "body",
                "level": heading_level,
                "text": paragraph_text,
                "start_offset": start,
                "end_offset": max(start, end - 1),
                "source_locator": f"{filename or 'document.docx'}#block={order};chars={start}-{max(start, end - 1)}",
                "style": style,
                "numbering": numbering,
                "contains_page_or_column_break": _contains_page_break(paragraph),
                "structure_evidence": {
                    "heading_method": heading_method,
                    "docx_style_name": style.get("style_name", ""),
                    "outline_level": _outline_level(paragraph),
                },
            }
            blocks.append(block)
            if heading_level:
                heading_stack.append(block)
                sections.append(
                    {
                        "block_id": block_id,
                        "level": heading_level,
                        "title": paragraph_text,
                        "start_offset": start,
                        "source_locator": block["source_locator"],
                        "structure_evidence": block["structure_evidence"],
                    }
                )
            continue

        if child.tag != qn("w:tbl"):
            continue
        table_index += 1
        order += 1
        table = Table(child, body_parent)
        parent_id = heading_stack[-1]["block_id"] if heading_stack else ""
        table_id = _stable_id("document_table", filename, table_index, order)
        normalized_rows: list[dict[str, str]] = []
        headers: list[str] = []
        table_lines: list[str] = []
        cell_blocks: list[dict[str, Any]] = []
        seen_cell_xml: dict[int, str] = {}
        for row_index, row in enumerate(table.rows):
            values: list[str] = []
            for column_index, cell in enumerate(row.cells):
                value = "\n".join(
                    part.strip() for part in str(cell.text or "").splitlines() if part.strip()
                )
                values.append(value)
                xml_identity = id(cell._tc)
                merged_with = seen_cell_xml.get(xml_identity, "")
                cell_id = _stable_id(
                    "document_cell", filename, table_index, row_index, column_index, xml_identity
                )
                seen_cell_xml.setdefault(xml_identity, cell_id)
                cell_blocks.append(
                    {
                        "block_id": cell_id,
                        "type": "TABLE_CELL",
                        "parent_id": table_id,
                        "order": order,
                        "region": "body",
                        "table_index": table_index,
                        "row_index": row_index,
                        "column_index": column_index,
                        "text": value,
                        "merged_with_cell_id": merged_with,
                        "merge": _table_cell_metadata(cell),
                        "source_locator": (
                            f"{filename or 'document.docx'}#table={table_index};"
                            f"row={row_index};cell={column_index}"
                        ),
                    }
                )
            table_lines.append("\t".join(values))
            if row_index == 0:
                headers = values
            elif headers:
                padded = values[: len(headers)] + [""] * max(0, len(headers) - len(values))
                normalized_rows.append({headers[index]: padded[index] for index in range(len(headers))})
        rendered_table = "\n".join(table_lines) + "\n"
        start, end = append_main_text(rendered_table)
        table_block = {
            "block_id": table_id,
            "type": "TABLE",
            "parent_id": parent_id,
            "order": order,
            "region": "body",
            "table_index": table_index,
            "row_count": len(table.rows),
            "column_count": max((len(row.cells) for row in table.rows), default=0),
            "start_offset": start,
            "end_offset": max(start, end - 1),
            "source_locator": f"{filename or 'document.docx'}#table={table_index};chars={start}-{max(start, end - 1)}",
            "text": rendered_table.rstrip("\n"),
            "style": {"style_name": _text(getattr(getattr(table, "style", None), "name", ""))},
            "cell_block_ids": [row["block_id"] for row in cell_blocks],
        }
        blocks.append(table_block)
        blocks.extend(cell_blocks)
        tables.append(
            {
                "headers": headers,
                "rows": normalized_rows,
                "source_locator": table_block["source_locator"],
                "format": "docx",
                "table_block_id": table_id,
                "row_count": len(table.rows),
                "column_count": table_block["column_count"],
            }
        )

    # Headers and footers are preserved as separate regions.  They are not appended
    # to main-flow text because repeated page furniture must not become business facts.
    seen_regions: set[tuple[str, str]] = set()
    for section_index, section in enumerate(document.sections):
        for region_name, container in (("header", section.header), ("footer", section.footer)):
            for paragraph_index, paragraph in enumerate(container.paragraphs):
                value = _text(paragraph.text)
                if not value or (region_name, value) in seen_regions:
                    continue
                seen_regions.add((region_name, value))
                order += 1
                blocks.append(
                    {
                        "block_id": _stable_id(
                            "document_region_block", filename, section_index, region_name, paragraph_index, value
                        ),
                        "type": region_name.upper(),
                        "parent_id": "",
                        "order": order,
                        "region": region_name,
                        "section_index": section_index,
                        "text": value,
                        "excluded_from_main_flow": True,
                        "source_locator": (
                            f"{filename or 'document.docx'}#section={section_index};"
                            f"{region_name}={paragraph_index}"
                        ),
                        "style": _paragraph_style(paragraph),
                    }
                )

    feature_counts = _zip_feature_counts(data)
    unsupported = _unsupported_content(feature_counts, len(document.inline_shapes))
    block_counts = Counter(_text(block.get("type")) for block in blocks)
    main_blocks = [block for block in blocks if _text(block.get("region")) == "body"]
    traceable_main_blocks = [block for block in main_blocks if _text(block.get("source_locator"))]
    status = "PARTIAL" if unsupported else "COMPLETE"
    receipt = {
        "schema": STRUCTURE_RECEIPT_SCHEMA,
        "status": status,
        "format": "docx",
        "block_count": len(blocks),
        "main_flow_block_count": len(main_blocks),
        "traceable_main_flow_block_count": len(traceable_main_blocks),
        "source_traceability_rate": (
            round(len(traceable_main_blocks) / len(main_blocks), 4) if main_blocks else 1.0
        ),
        "block_type_distribution": dict(block_counts),
        "section_count": len(sections),
        "table_count": len(tables),
        "unsupported_content_count": sum(int(row.get("count") or 0) for row in unsupported),
        "unsupported_content": unsupported,
        "document_order_is_business_flow": False,
        "headers_and_footers_excluded_from_main_flow": True,
        "filename_is_business_context": False,
    }
    return {
        "schema": DOCUMENT_IR_SCHEMA,
        "format": "docx",
        "filename": filename,
        "plain_text": "".join(main_text_parts).rstrip(),
        "blocks": blocks,
        "sections": sections,
        "tables": tables,
        "unsupported_content": unsupported,
        "structure_receipt": receipt,
    }


__all__ = [
    "DOCUMENT_IR_SCHEMA",
    "STRUCTURE_RECEIPT_SCHEMA",
    "extract_docx_document_ir",
]
