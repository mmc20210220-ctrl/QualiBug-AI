"""Source-preserving PDF layout structure IR.

The implementation uses the already-declared pypdf dependency.  It keeps page,
coordinate, font and reading-order evidence instead of treating a PDF as one flat
string.  Layout inference is deliberately fail-visible: scanned pages, missing text
coordinates, images, table-like regions and multi-column heuristics are reported in
the structure receipt and may prevent formal enterprise understanding from passing.
"""
from __future__ import annotations

import hashlib
import io
import math
import re
import statistics
from collections import Counter, defaultdict
from typing import Any, Iterable

from ._document_structure_ir import DOCUMENT_IR_SCHEMA, STRUCTURE_RECEIPT_SCHEMA


_REPEAT_SPACE_RE = re.compile(r"\s+")
_LIST_RE = re.compile(
    r"^\s*(?:[-*•·▪◦‣]|\d{1,4}[.)、）]|[（(]\d{1,4}[）)]|"
    r"[一二三四五六七八九十百千]+[、.)）]|[（(][一二三四五六七八九十百千]+[）)]|"
    r"[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]|[A-Za-z][.)、）]|"
    r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.)、）])\s*\S"
)
_HEADING_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"^第[一二三四五六七八九十百千0-9]+(?:篇|章|部分)\s*.*$"), 1),
    (re.compile(r"^第[一二三四五六七八九十百千0-9]+节\s*.*$"), 2),
    (re.compile(r"^第[一二三四五六七八九十百千0-9]+(?:条|款|项)\s*.*$"), 3),
    (re.compile(r"^\d+(?:\.\d+){1,5}\s*\S.*$"), 2),
    (re.compile(r"^[一二三四五六七八九十百千]+[、.]\s*\S.*$"), 1),
    (re.compile(r"^[（(][一二三四五六七八九十百千]+[）)]\s*\S.*$"), 2),
)
_PAGE_NUMBER_RE = re.compile(
    r"^(?:第\s*)?\d+\s*(?:页|/\s*\d+)?$|^page\s+\d+(?:\s+of\s+\d+)?$",
    re.I,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _object(value: Any) -> Any:
    try:
        return value.get_object()
    except Exception:
        return value


def _bbox_union(rows: Iterable[Iterable[Any]]) -> list[float]:
    boxes = [list(row) for row in rows if isinstance(row, (list, tuple)) and len(row) == 4]
    if not boxes:
        return []
    return [
        round(min(_float(row[0]) for row in boxes), 3),
        round(min(_float(row[1]) for row in boxes), 3),
        round(max(_float(row[2]) for row in boxes), 3),
        round(max(_float(row[3]) for row in boxes), 3),
    ]


def _font_name(font_dict: Any) -> str:
    if not isinstance(font_dict, dict):
        return ""
    return _text(font_dict.get("/BaseFont") or font_dict.get("BaseFont")).lstrip("/")


def _font_is_bold(font_name: str) -> bool:
    low = font_name.lower()
    return any(marker in low for marker in ("bold", "black", "heavy", "demi", "semibold", "黑体"))


def _fragment_bbox(x: float, y: float, text: str, font_size: float, page_width: float, page_height: float) -> list[float]:
    size = max(1.0, font_size)
    width = min(max(1.0, len(text) * size * 0.55), max(1.0, page_width - x))
    return [
        round(max(0.0, x), 3),
        round(max(0.0, y - size * 0.25), 3),
        round(min(page_width, x + width), 3),
        round(min(page_height, y + size), 3),
    ]


def _page_resource_counts(page: Any) -> tuple[int, int]:
    """Return direct image and form XObject counts without interpreting content."""
    images = 0
    forms = 0
    try:
        resources = _object(page.get("/Resources")) or {}
        xobjects = _object(resources.get("/XObject")) if isinstance(resources, dict) else {}
        if not isinstance(xobjects, dict):
            return 0, 0
        for raw in xobjects.values():
            item = _object(raw)
            subtype = _text(item.get("/Subtype")) if isinstance(item, dict) else ""
            if subtype == "/Image":
                images += 1
            elif subtype == "/Form":
                forms += 1
    except Exception:
        return images, forms
    return images, forms


def _extract_page_fragments(page: Any, page_number: int, filename: str) -> tuple[list[dict[str, Any]], str, bool]:
    width = _float(getattr(getattr(page, "mediabox", None), "width", 0.0))
    height = _float(getattr(getattr(page, "mediabox", None), "height", 0.0))
    fragments: list[dict[str, Any]] = []
    coordinate_missing = False

    def visitor(text_value: str, _cm: Any, tm: Any, font_dict: Any, font_size: Any) -> None:
        nonlocal coordinate_missing
        raw = str(text_value or "")
        parts = [part.strip() for part in re.split(r"[\r\n]+", raw) if part.strip()]
        if not parts:
            return
        matrix = list(tm or [])
        if len(matrix) < 6:
            coordinate_missing = True
            return
        x = _float(matrix[4])
        y = _float(matrix[5])
        size = max(1.0, _float(font_size, 10.0))
        font = _font_name(font_dict)
        for part_index, part in enumerate(parts):
            adjusted_y = y - part_index * size * 1.2
            fragments.append(
                {
                    "fragment_id": _stable_id("pdf_fragment", filename, page_number, len(fragments), part),
                    "text": part,
                    "x": round(x, 3),
                    "y": round(adjusted_y, 3),
                    "bbox": _fragment_bbox(x, adjusted_y, part, size, width, height),
                    "font_name": font,
                    "font_size_pt": round(size, 3),
                    "bold": _font_is_bold(font),
                }
            )

    extracted = ""
    try:
        extracted = str(page.extract_text(visitor_text=visitor) or "")
    except TypeError:
        # A non-conforming pypdf version can still provide text, but the absence of
        # positions is a formal layout gap and is reported by the receipt.
        extracted = str(page.extract_text() or "")
        coordinate_missing = bool(extracted.strip())
    return fragments, extracted, coordinate_missing


def _join_fragments(fragments: list[dict[str, Any]]) -> str:
    if not fragments:
        return ""
    ordered = sorted(fragments, key=lambda row: _float(row.get("x")))
    out = _text(ordered[0].get("text"))
    previous = ordered[0]
    for current in ordered[1:]:
        value = _text(current.get("text"))
        if not value:
            continue
        previous_bbox = previous.get("bbox") or []
        gap = _float((current.get("bbox") or [0])[0]) - _float(previous_bbox[2] if len(previous_bbox) == 4 else 0)
        previous_text = out[-1:] if out else ""
        needs_space = bool(
            gap > max(2.0, _float(current.get("font_size_pt"), 10.0) * 0.18)
            and previous_text
            and previous_text[-1:].isascii()
            and value[:1].isascii()
            and previous_text[-1:].isalnum()
            and value[:1].isalnum()
        )
        out += (" " if needs_space else "") + value
        previous = current
    return out.strip()


def _group_lines(fragments: list[dict[str, Any]], page_height: float) -> list[dict[str, Any]]:
    if not fragments:
        return []
    ordered = sorted(fragments, key=lambda row: (-_float(row.get("y")), _float(row.get("x"))))
    lines: list[dict[str, Any]] = []
    for fragment in ordered:
        baseline = _float(fragment.get("y"))
        size = _float(fragment.get("font_size_pt"), 10.0)
        tolerance = max(2.0, size * 0.42)
        if lines and abs(baseline - _float(lines[-1].get("baseline"))) <= tolerance:
            lines[-1]["fragments"].append(fragment)
            values = [_float(row.get("font_size_pt"), 10.0) for row in lines[-1]["fragments"]]
            lines[-1]["font_size_pt"] = round(statistics.median(values), 3)
            lines[-1]["bold"] = all(bool(row.get("bold")) for row in lines[-1]["fragments"])
            lines[-1]["bbox"] = _bbox_union(row.get("bbox") or [] for row in lines[-1]["fragments"])
            lines[-1]["text"] = _join_fragments(lines[-1]["fragments"])
            continue
        lines.append(
            {
                "baseline": baseline,
                "fragments": [fragment],
                "font_size_pt": round(size, 3),
                "bold": bool(fragment.get("bold")),
                "bbox": list(fragment.get("bbox") or []),
                "text": _text(fragment.get("text")),
            }
        )
    for line in lines:
        bbox = line.get("bbox") or [0.0, 0.0, 0.0, 0.0]
        line["top"] = round(max(0.0, page_height - _float(bbox[3])), 3)
        line["bottom"] = round(max(0.0, page_height - _float(bbox[1])), 3)
        line["fragment_count"] = len(line.get("fragments") or [])
    return sorted(lines, key=lambda row: (_float(row.get("top")), _float((row.get("bbox") or [0])[0])))


def _group_text_blocks(lines: list[dict[str, Any]], page_number: int, page_width: float, page_height: float, filename: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for line in lines:
        text_value = _text(line.get("text"))
        if not text_value:
            continue
        bbox = list(line.get("bbox") or [])
        size = _float(line.get("font_size_pt"), 10.0)
        can_merge = False
        if blocks:
            previous = blocks[-1]
            previous_bbox = previous.get("bbox") or []
            vertical_gap = _float(line.get("top")) - _float(previous.get("bottom_top"))
            same_indent = abs(_float(bbox[0] if len(bbox) == 4 else 0) - _float(previous_bbox[0] if len(previous_bbox) == 4 else 0)) <= 24.0
            same_font = abs(size - _float(previous.get("style", {}).get("font_size_pt"), size)) <= 0.75
            same_weight = bool(line.get("bold")) == bool(previous.get("style", {}).get("bold"))
            can_merge = vertical_gap <= max(5.0, size * 1.25) and same_indent and same_font and same_weight
            if _LIST_RE.match(text_value):
                can_merge = False
        if can_merge:
            previous = blocks[-1]
            previous["text"] = f"{_text(previous.get('text'))}\n{text_value}".strip()
            previous["bbox"] = _bbox_union([previous.get("bbox") or [], bbox])
            previous["line_count"] = int(previous.get("line_count") or 1) + 1
            previous["bottom_top"] = _float(line.get("bottom"))
            previous["fragment_count"] = int(previous.get("fragment_count") or 0) + int(line.get("fragment_count") or 0)
            continue
        block_id = _stable_id("pdf_text_block", filename, page_number, len(blocks), text_value)
        blocks.append(
            {
                "block_id": block_id,
                "type": "PARAGRAPH",
                "parent_id": "",
                "page": page_number,
                "region": "body",
                "text": text_value,
                "bbox": bbox,
                "top": _float(line.get("top")),
                "bottom_top": _float(line.get("bottom")),
                "line_count": 1,
                "fragment_count": int(line.get("fragment_count") or 0),
                "style": {
                    "font_size_pt": round(size, 3),
                    "bold": bool(line.get("bold")),
                    "font_names": sorted(
                        {
                            _text(fragment.get("font_name"))
                            for fragment in line.get("fragments") or []
                            if _text(fragment.get("font_name"))
                        }
                    ),
                },
                "source_locator": (
                    f"{filename or 'document.pdf'}#page={page_number};"
                    f"bbox={','.join(str(round(_float(value), 3)) for value in bbox)}"
                ),
                "layout_evidence": {
                    "coordinate_system": "PDF_BOTTOM_LEFT_POINTS",
                    "page_width_pt": round(page_width, 3),
                    "page_height_pt": round(page_height, 3),
                },
            }
        )
    return blocks


def _repeat_key(value: str) -> str:
    normalized = _REPEAT_SPACE_RE.sub("", _text(value)).lower()
    if _PAGE_NUMBER_RE.match(_text(value)):
        return "__page_number__"
    return normalized


def _mark_repeated_page_regions(page_blocks: dict[int, list[dict[str, Any]]], page_sizes: dict[int, tuple[float, float]]) -> tuple[int, int]:
    candidates: dict[tuple[str, str], set[int]] = defaultdict(set)
    for page_number, blocks in page_blocks.items():
        _width, height = page_sizes.get(page_number, (0.0, 0.0))
        for block in blocks:
            bbox = block.get("bbox") or []
            if len(bbox) != 4 or not height:
                continue
            key = _repeat_key(_text(block.get("text")))
            if not key or len(key) > 160:
                continue
            top_distance = height - _float(bbox[3])
            bottom_distance = _float(bbox[1])
            if top_distance <= height * 0.1:
                candidates[("HEADER", key)].add(page_number)
            if bottom_distance <= height * 0.1:
                candidates[("FOOTER", key)].add(page_number)
    threshold = max(2, math.ceil(max(1, len(page_blocks)) * 0.5))
    repeated = {identity for identity, pages in candidates.items() if len(pages) >= threshold}
    header_count = 0
    footer_count = 0
    for blocks in page_blocks.values():
        for block in blocks:
            key = _repeat_key(_text(block.get("text")))
            if ("HEADER", key) in repeated:
                block["type"] = "HEADER"
                block["region"] = "header"
                block["excluded_from_main_flow"] = True
                header_count += 1
            elif ("FOOTER", key) in repeated:
                block["type"] = "FOOTER"
                block["region"] = "footer"
                block["excluded_from_main_flow"] = True
                footer_count += 1
    return header_count, footer_count


def _detect_columns(blocks: list[dict[str, Any]], page_width: float) -> tuple[int, float]:
    narrow = [
        block
        for block in blocks
        if len(block.get("bbox") or []) == 4
        and (_float(block["bbox"][2]) - _float(block["bbox"][0])) < page_width * 0.72
        and _text(block.get("region")) == "body"
    ]
    left = [block for block in narrow if (_float(block["bbox"][0]) + _float(block["bbox"][2])) / 2 < page_width * 0.45]
    right = [block for block in narrow if (_float(block["bbox"][0]) + _float(block["bbox"][2])) / 2 > page_width * 0.55]
    if len(left) >= 2 and len(right) >= 2:
        return 2, 0.72
    return 1, 0.94


def _order_page_blocks(blocks: list[dict[str, Any]], page_width: float, column_count: int) -> list[dict[str, Any]]:
    body = [block for block in blocks if _text(block.get("region")) == "body"]
    furniture = [block for block in blocks if _text(block.get("region")) != "body"]
    if column_count != 2:
        ordered_body = sorted(body, key=lambda row: (_float(row.get("top")), _float((row.get("bbox") or [0])[0])))
    else:
        full = [
            block
            for block in body
            if len(block.get("bbox") or []) == 4
            and (_float(block["bbox"][2]) - _float(block["bbox"][0])) >= page_width * 0.72
        ]

        def key(block: dict[str, Any]) -> tuple[int, int, float, float]:
            top = _float(block.get("top"))
            segment = sum(1 for marker in full if _float(marker.get("top")) < top)
            bbox = block.get("bbox") or [0.0, 0.0, 0.0, 0.0]
            width = _float(bbox[2]) - _float(bbox[0])
            if width >= page_width * 0.72:
                column = -1
            else:
                center = (_float(bbox[0]) + _float(bbox[2])) / 2
                column = 0 if center < page_width * 0.5 else 1
            return segment, column, top, _float(bbox[0])

        ordered_body = sorted(body, key=key)
    ordered = [*ordered_body, *sorted(furniture, key=lambda row: (_text(row.get("region")), _float(row.get("top"))))]
    for index, block in enumerate(ordered, start=1):
        block["page_reading_order"] = index
    return ordered


def _detect_table_regions(lines: list[dict[str, Any]], page_number: int, filename: str) -> list[dict[str, Any]]:
    rows = [line for line in lines if int(line.get("fragment_count") or 0) >= 2]
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_xs: list[float] = []
    previous_bottom = 0.0
    for row in rows:
        xs = sorted(_float(fragment.get("x")) for fragment in row.get("fragments") or [])
        aligned = sum(1 for x in xs if any(abs(x - prior) <= 14.0 for prior in previous_xs))
        close = not current or _float(row.get("top")) - previous_bottom <= max(28.0, _float(row.get("font_size_pt"), 10.0) * 2.2)
        if current and (aligned < 2 or not close):
            if len(current) >= 3:
                groups.append(current)
            current = []
        current.append(row)
        previous_xs = xs
        previous_bottom = _float(row.get("bottom"))
    if len(current) >= 3:
        groups.append(current)
    regions: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        bbox = _bbox_union(row.get("bbox") or [] for row in group)
        regions.append(
            {
                "block_id": _stable_id("pdf_table_region", filename, page_number, index, bbox),
                "type": "TABLE_REGION",
                "parent_id": "",
                "page": page_number,
                "region": "body",
                "bbox": bbox,
                "text": "\n".join(_text(row.get("text")) for row in group if _text(row.get("text"))),
                "row_count_projection": len(group),
                "cell_structure_parsed": False,
                "excluded_from_main_flow": True,
                "source_locator": (
                    f"{filename or 'document.pdf'}#page={page_number};table_region={index};"
                    f"bbox={','.join(str(value) for value in bbox)}"
                ),
            }
        )
    return regions


def _heading_level(text_value: str, style: dict[str, Any], body_font: float, font_ranks: dict[float, int]) -> tuple[int | None, str]:
    value = _text(text_value)
    size = round(_float(style.get("font_size_pt"), body_font), 1)
    layout_signal = bool(size >= body_font * 1.18 or (style.get("bold") and size >= body_font * 1.04))
    if len(value) <= 160 and layout_signal:
        for pattern, level in _HEADING_PATTERNS:
            if pattern.match(value):
                return level, "pdf_text_pattern_with_font_evidence"
        return min(6, font_ranks.get(size, 1)), "pdf_font_hierarchy"
    return None, ""


def _apply_heading_hierarchy(blocks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    body_blocks = [
        block for block in blocks
        if _text(block.get("region")) == "body" and _text(block.get("type")) == "PARAGRAPH"
    ]
    font_sizes = [
        _float(block.get("style", {}).get("font_size_pt"))
        for block in body_blocks
        if _float(block.get("style", {}).get("font_size_pt")) > 0
    ]
    body_font = statistics.median(font_sizes) if font_sizes else 10.0
    candidate_sizes = sorted(
        {
            round(_float(block.get("style", {}).get("font_size_pt"), body_font), 1)
            for block in body_blocks
            if len(_text(block.get("text"))) <= 160
            and (
                _float(block.get("style", {}).get("font_size_pt"), body_font) >= body_font * 1.18
                or bool(block.get("style", {}).get("bold"))
            )
        },
        reverse=True,
    )
    font_ranks = {size: min(6, index + 1) for index, size in enumerate(candidate_sizes[:6])}
    heading_stack: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    ordered = sorted(
        blocks,
        key=lambda row: (
            int(row.get("page") or 0),
            int(row.get("page_reading_order") or 0),
            _float(row.get("top")),
        ),
    )
    for block in ordered:
        if _text(block.get("region")) != "body":
            continue
        block_type = _text(block.get("type"))
        if block_type == "PARAGRAPH":
            level, method = _heading_level(_text(block.get("text")), block.get("style") or {}, body_font, font_ranks)
            if level:
                block["type"] = "HEADING"
                block["level"] = level
                block["structure_evidence"] = {
                    "heading_method": method,
                    "body_font_size_pt": round(body_font, 3),
                    "heading_font_size_pt": block.get("style", {}).get("font_size_pt"),
                }
                while heading_stack and int(heading_stack[-1].get("level") or 0) >= level:
                    heading_stack.pop()
                block["parent_id"] = heading_stack[-1]["block_id"] if heading_stack else ""
                heading_stack.append(block)
                sections.append(
                    {
                        "block_id": block.get("block_id"),
                        "level": level,
                        "title": block.get("text"),
                        "page": block.get("page"),
                        "source_locator": block.get("source_locator"),
                        "structure_evidence": block.get("structure_evidence"),
                    }
                )
                continue
            if _LIST_RE.match(_text(block.get("text"))):
                block["type"] = "LIST_ITEM"
                block["numbering"] = {"numbered": True, "source": "pdf_visible_marker"}
        if block_type in {"TABLE_REGION", "FIGURE", "SCANNED_PAGE"} or _text(block.get("type")) in {"PARAGRAPH", "LIST_ITEM", "TABLE_REGION", "FIGURE", "SCANNED_PAGE"}:
            block["parent_id"] = heading_stack[-1]["block_id"] if heading_stack else ""
    return ordered, sections


def extract_pdf_document_ir(data: bytes, filename: str = "") -> dict[str, Any]:
    """Extract page-aware PDF text blocks, coordinates, fonts and layout gaps."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ImportError("pypdf is required for PDF structure extraction") from exc

    reader = PdfReader(io.BytesIO(data))
    if getattr(reader, "is_encrypted", False):
        try:
            unlocked = reader.decrypt("")
        except Exception as exc:
            raise ValueError("encrypted PDF cannot be opened without credentials") from exc
        if not unlocked:
            raise ValueError("encrypted PDF cannot be opened without credentials")

    page_blocks: dict[int, list[dict[str, Any]]] = {}
    page_lines: dict[int, list[dict[str, Any]]] = {}
    page_sizes: dict[int, tuple[float, float]] = {}
    pages: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    coordinate_missing_pages: list[int] = []
    scanned_pages: list[int] = []
    image_total = 0
    form_total = 0
    table_regions: list[dict[str, Any]] = []
    multi_column_pages: list[int] = []
    reading_confidences: list[float] = []

    for page_index, page in enumerate(reader.pages):
        page_number = page_index + 1
        width = _float(getattr(getattr(page, "mediabox", None), "width", 0.0))
        height = _float(getattr(getattr(page, "mediabox", None), "height", 0.0))
        rotation = int(_float(page.get("/Rotate", 0))) % 360
        page_sizes[page_number] = (width, height)
        fragments, extracted_text, coordinate_missing = _extract_page_fragments(page, page_number, filename)
        lines = _group_lines(fragments, height)
        blocks = _group_text_blocks(lines, page_number, width, height, filename)
        image_count, form_count = _page_resource_counts(page)
        image_total += image_count
        form_total += form_count
        if coordinate_missing:
            coordinate_missing_pages.append(page_number)
        text_char_count = sum(len(_text(fragment.get("text"))) for fragment in fragments)
        scanned = text_char_count < 20 and image_count > 0
        if scanned:
            scanned_pages.append(page_number)
            blocks.append(
                {
                    "block_id": _stable_id("pdf_scanned_page", filename, page_number),
                    "type": "SCANNED_PAGE",
                    "parent_id": "",
                    "page": page_number,
                    "region": "body",
                    "text": "",
                    "bbox": [0.0, 0.0, round(width, 3), round(height, 3)],
                    "excluded_from_main_flow": True,
                    "source_locator": f"{filename or 'document.pdf'}#page={page_number}",
                    "requires_ocr_or_visual_parser": True,
                }
            )
        for image_index in range(image_count):
            blocks.append(
                {
                    "block_id": _stable_id("pdf_figure", filename, page_number, image_index),
                    "type": "FIGURE",
                    "parent_id": "",
                    "page": page_number,
                    "region": "body",
                    "text": "",
                    "bbox": [],
                    "bbox_available": False,
                    "image_index": image_index,
                    "excluded_from_main_flow": True,
                    "source_locator": f"{filename or 'document.pdf'}#page={page_number};image={image_index}",
                }
            )
        detected_tables = _detect_table_regions(lines, page_number, filename)
        table_regions.extend(detected_tables)
        blocks.extend(detected_tables)
        page_blocks[page_number] = blocks
        page_lines[page_number] = lines
        pages.append(
            {
                "page": page_number,
                "width_pt": round(width, 3),
                "height_pt": round(height, 3),
                "rotation": rotation,
                "text_char_count": text_char_count,
                "fragment_count": len(fragments),
                "image_count": image_count,
                "form_xobject_count": form_count,
                "scanned_page": scanned,
                "coordinates_available": not coordinate_missing,
                "legacy_extracted_text_length": len(extracted_text),
            }
        )

    repeated_headers, repeated_footers = _mark_repeated_page_regions(page_blocks, page_sizes)
    all_blocks: list[dict[str, Any]] = []
    for page in pages:
        page_number = int(page["page"])
        width, _height = page_sizes[page_number]
        column_count, confidence = _detect_columns(page_blocks[page_number], width)
        page["column_count_projection"] = column_count
        page["reading_order_confidence"] = confidence
        reading_confidences.append(confidence)
        if column_count > 1:
            multi_column_pages.append(page_number)
        ordered = _order_page_blocks(page_blocks[page_number], width, column_count)
        all_blocks.extend(ordered)

    all_blocks, sections = _apply_heading_hierarchy(all_blocks)
    main_text_parts: list[str] = []
    offset = 0
    formal_order = 0
    for block in all_blocks:
        formal_order += 1
        block["order"] = formal_order
        if _text(block.get("region")) != "body" or block.get("excluded_from_main_flow"):
            continue
        if _text(block.get("type")) not in {"HEADING", "PARAGRAPH", "LIST_ITEM"}:
            continue
        rendered = _text(block.get("text")) + "\n"
        block["start_offset"] = offset
        main_text_parts.append(rendered)
        offset += len(rendered)
        block["end_offset"] = max(int(block["start_offset"]), offset - 1)

    if scanned_pages:
        unsupported.append(
            {
                "kind": "SCANNED_PAGE_REQUIRES_OCR",
                "count": len(scanned_pages),
                "pages": scanned_pages,
                "status": "PRESENT_REQUIRES_VISUAL_PARSER",
                "severity": "P0",
                "blocks_formal_understanding": True,
                "reason_code": "SCANNED_PAGE_REQUIRES_OCR",
                "included_in_plain_text_authority": False,
            }
        )
    if coordinate_missing_pages:
        unsupported.append(
            {
                "kind": "PDF_TEXT_COORDINATES_UNAVAILABLE",
                "count": len(coordinate_missing_pages),
                "pages": coordinate_missing_pages,
                "status": "LAYOUT_FIDELITY_INSUFFICIENT",
                "severity": "P0",
                "blocks_formal_understanding": True,
                "reason_code": "PDF_TEXT_COORDINATES_UNAVAILABLE",
                "included_in_plain_text_authority": bool(main_text_parts),
            }
        )
    non_scanned_images = max(0, image_total - len(scanned_pages))
    if non_scanned_images:
        unsupported.append(
            {
                "kind": "PDF_IMAGE_CONTENT_UNPARSED",
                "count": non_scanned_images,
                "status": "PRESENT_REQUIRES_VISUAL_PARSER",
                "severity": "P1",
                "blocks_formal_understanding": False,
                "reason_code": "PDF_IMAGE_CONTENT_UNPARSED",
                "included_in_plain_text_authority": False,
            }
        )
    if form_total:
        unsupported.append(
            {
                "kind": "PDF_FORM_XOBJECT_CONTENT_UNVERIFIED",
                "count": form_total,
                "status": "PRESENT_REQUIRES_SECONDARY_LAYOUT_VALIDATION",
                "severity": "P1",
                "blocks_formal_understanding": False,
                "reason_code": "PDF_FORM_XOBJECT_CONTENT_UNVERIFIED",
                "included_in_plain_text_authority": False,
            }
        )
    if table_regions:
        unsupported.append(
            {
                "kind": "PDF_TABLE_REGION_NOT_CELL_PARSED",
                "count": len(table_regions),
                "status": "REGION_DETECTED_CELL_STRUCTURE_UNRESOLVED",
                "severity": "P1",
                "blocks_formal_understanding": False,
                "reason_code": "PDF_TABLE_REGION_NOT_CELL_PARSED",
                "included_in_plain_text_authority": True,
            }
        )
    if multi_column_pages:
        unsupported.append(
            {
                "kind": "PDF_MULTI_COLUMN_READING_ORDER_HEURISTIC",
                "count": len(multi_column_pages),
                "pages": multi_column_pages,
                "status": "READING_ORDER_PROJECTED_NOT_AUTHORITATIVE",
                "severity": "P1",
                "blocks_formal_understanding": False,
                "reason_code": "PDF_MULTI_COLUMN_READING_ORDER_HEURISTIC",
                "included_in_plain_text_authority": True,
            }
        )

    critical = [row for row in unsupported if bool(row.get("blocks_formal_understanding"))]
    status = "BLOCKED" if critical else "PARTIAL" if unsupported else "COMPLETE"
    block_counts = Counter(_text(block.get("type")) for block in all_blocks)
    traceable = [block for block in all_blocks if _text(block.get("source_locator"))]
    receipt = {
        "schema": STRUCTURE_RECEIPT_SCHEMA,
        "status": status,
        "format": "pdf",
        "page_count": len(pages),
        "text_page_count": sum(1 for page in pages if int(page.get("text_char_count") or 0) > 0),
        "scanned_page_count": len(scanned_pages),
        "scanned_pages": scanned_pages,
        "block_count": len(all_blocks),
        "source_traceability_rate": round(len(traceable) / len(all_blocks), 4) if all_blocks else 1.0,
        "block_type_distribution": dict(block_counts),
        "section_count": len(sections),
        "image_count": image_total,
        "form_xobject_count": form_total,
        "table_region_count": len(table_regions),
        "multi_column_page_count": len(multi_column_pages),
        "repeated_header_block_count": repeated_headers,
        "repeated_footer_block_count": repeated_footers,
        "mean_reading_order_confidence": (
            round(sum(reading_confidences) / len(reading_confidences), 4) if reading_confidences else 1.0
        ),
        "unsupported_content_count": sum(int(row.get("count") or 0) for row in unsupported),
        "unsupported_content": unsupported,
        "document_order_is_business_flow": False,
        "headers_and_footers_excluded_from_main_flow": True,
        "filename_is_business_context": False,
        "coordinate_system": "PDF_BOTTOM_LEFT_POINTS",
        "reading_order_is_projection": bool(multi_column_pages),
    }
    return {
        "schema": DOCUMENT_IR_SCHEMA,
        "format": "pdf",
        "filename": filename,
        "plain_text": "".join(main_text_parts).rstrip(),
        "pages": pages,
        "blocks": all_blocks,
        "sections": sections,
        "tables": table_regions,
        "unsupported_content": unsupported,
        "structure_receipt": receipt,
    }


__all__ = ["extract_pdf_document_ir"]
