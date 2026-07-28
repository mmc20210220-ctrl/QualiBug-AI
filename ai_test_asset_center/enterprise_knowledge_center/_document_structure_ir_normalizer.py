"""Normalize raw document structure blocks without adding business semantics."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

_LIST_STYLE_RE = re.compile(
    r"(?:^|\b)(?:list|bullet|number|numbering)(?:\b|$)|列表|项目符号|编号|正文缩进",
    re.I,
)
_LIST_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*[-*•·▪◦‣]\s+\S"), "BULLET"),
    (re.compile(r"^\s*\d{1,4}[.)、）]\s*\S"), "ARABIC"),
    (re.compile(r"^\s*[（(]\d{1,4}[）)]\s*\S"), "ARABIC_PAREN"),
    (re.compile(r"^\s*[一二三四五六七八九十百千]+[、.)）]\s*\S"), "CHINESE"),
    (re.compile(r"^\s*[（(][一二三四五六七八九十百千]+[）)]\s*\S"), "CHINESE_PAREN"),
    (re.compile(r"^\s*[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]\s*\S"), "CIRCLED"),
    (re.compile(r"^\s*[A-Za-z][.)、）]\s+\S"), "LATIN"),
    (re.compile(r"^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.)、）]\s*\S"), "ROMAN"),
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _visible_list_marker(text: str) -> str:
    for pattern, marker_type in _LIST_TEXT_PATTERNS:
        if pattern.match(text):
            return marker_type
    return ""


def normalize_document_structure_ir(document_ir: dict[str, Any]) -> dict[str, Any]:
    """Promote source-visible list signals and sanitize merge metadata."""
    result = dict(document_ir or {})
    blocks = [dict(row) for row in _list(result.get("blocks")) if isinstance(row, dict)]
    list_promotions = 0
    merge_metadata_sanitized = 0
    for block in blocks:
        if _text(block.get("type")) == "PARAGRAPH":
            style = _dict(block.get("style"))
            style_name = _text(style.get("style_name"))
            marker_type = _visible_list_marker(_text(block.get("text")))
            if _LIST_STYLE_RE.search(style_name) or marker_type:
                block["type"] = "LIST_ITEM"
                numbering = _dict(block.get("numbering"))
                numbering.update(
                    {
                        "numbered": True,
                        "source": (
                            "docx_list_style"
                            if _LIST_STYLE_RE.search(style_name)
                            else "visible_list_marker"
                        ),
                    }
                )
                if marker_type:
                    numbering["marker_type"] = marker_type
                block["numbering"] = numbering
                list_promotions += 1
        if _text(block.get("type")) == "TABLE_CELL":
            merge = _dict(block.get("merge"))
            # The raw XML helper intentionally stays conservative.  A vMerge value
            # of "continue" is retained only when python-docx exposes the same XML
            # cell in another grid position; otherwise the empty value could simply
            # mean that no vMerge element existed.
            if (
                merge.get("vertical_merge") == "continue"
                and not _text(block.get("merged_with_cell_id"))
            ):
                merge.pop("vertical_merge", None)
                merge_metadata_sanitized += 1
            block["merge"] = merge
    result["blocks"] = blocks
    receipt = dict(_dict(result.get("structure_receipt")))
    receipt["block_type_distribution"] = dict(
        Counter(_text(block.get("type")) for block in blocks)
    )
    receipt["list_item_promotion_count"] = list_promotions
    receipt["merge_metadata_sanitized_count"] = merge_metadata_sanitized
    receipt["normalization_contract"] = {
        "business_semantics_added": False,
        "document_order_is_business_flow": False,
        "list_promotion_requires_source_style_or_visible_marker": True,
    }
    result["structure_receipt"] = receipt
    return result


def extract_normalized_docx_document_ir(data: bytes, filename: str = "") -> dict[str, Any]:
    from ._document_structure_ir import extract_docx_document_ir

    return normalize_document_structure_ir(
        extract_docx_document_ir(data, filename=filename)
    )


__all__ = [
    "extract_normalized_docx_document_ir",
    "normalize_document_structure_ir",
]
