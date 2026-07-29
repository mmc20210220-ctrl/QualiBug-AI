"""Choose formal visual-table cells over overlapping page-level OCR text.

Both OCR and table adapters remain in Document IR for evidence.  Only the merged text
projection changes: when a formal visual table covers an OCR paragraph, the paragraph is
excluded from fact extraction and the source-preserving TABLE_CELL blocks become authority.
"""
from __future__ import annotations

import re
from typing import Any

from .contract import text


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bbox(value: Any) -> list[float]:
    rows = list(value or [])
    if len(rows) != 4:
        return []
    try:
        return [float(row) for row in rows]
    except (TypeError, ValueError):
        return []


def _center_inside(inner: list[float], outer: list[float]) -> bool:
    if len(inner) != 4 or len(outer) != 4:
        return False
    center_x = (inner[0] + inner[2]) / 2.0
    center_y = (inner[1] + inner[3]) / 2.0
    return outer[0] <= center_x <= outer[2] and outer[1] <= center_y <= outer[3]


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", "", text(value))


def _compose(blocks: list[dict[str, Any]], fallback: str) -> str:
    allowed = {
        "HEADING",
        "PARAGRAPH",
        "LIST_ITEM",
        "TABLE_CELL",
        "KEY_VALUE",
        "NOTE",
        "CAPTION",
        "FORMULA",
    }
    values: list[str] = []
    seen: set[tuple[str, str]] = set()
    for block in sorted(
        blocks,
        key=lambda row: (
            int(row.get("page") or 0),
            int(row.get("order") or row.get("page_reading_order") or 0),
            text(row.get("source_locator")),
        ),
    ):
        if text(block.get("region")) not in {"", "body"}:
            continue
        if block.get("excluded_from_main_flow") or block.get("excluded_from_plain_text_projection"):
            continue
        if text(block.get("type")) not in allowed:
            continue
        value = text(block.get("text"))
        if not value:
            continue
        identity = (text(block.get("source_locator")), _normalized(value))
        if identity in seen:
            continue
        seen.add(identity)
        values.append(value)
    return "\n".join(values).strip() or text(fallback)


def apply_visual_table_projection_authority(document_ir: dict[str, Any]) -> dict[str, Any]:
    result = dict(document_ir or {})
    blocks = [dict(row) for row in _list(result.get("blocks")) if isinstance(row, dict)]
    visual_tables = [
        row
        for row in blocks
        if text(row.get("type")) == "TABLE"
        and (
            "formal_table_structure" in row
            or text(_dict(row.get("structure_evidence")).get("provider"))
            in {"ruled-grid-visual-table-provider", "region-table-test-provider"}
        )
    ]
    visual_table_ids = {text(row.get("block_id")) for row in visual_tables if text(row.get("block_id"))}
    visual_cells = [
        row
        for row in blocks
        if text(row.get("type")) == "TABLE_CELL"
        and text(row.get("table_block_id")) in visual_table_ids
    ]
    formal_tables = [
        row
        for row in visual_tables
        if bool(row.get("formal_table_structure")) and len(_bbox(row.get("bbox"))) == 4
    ]

    superseded: list[dict[str, Any]] = []
    if formal_tables:
        for block in blocks:
            if text(block.get("type")) not in {"PARAGRAPH", "LIST_ITEM"}:
                continue
            evidence = _dict(block.get("structure_evidence"))
            if text(evidence.get("coordinate_system")) != "IMAGE_PIXELS_LOCAL_TO_RENDERED_PAGE":
                continue
            block_bbox = _bbox(block.get("bbox"))
            if not block_bbox:
                continue
            try:
                page = int(block.get("page") or 0)
            except (TypeError, ValueError):
                continue
            for table in formal_tables:
                try:
                    table_page = int(table.get("page") or 0)
                except (TypeError, ValueError):
                    continue
                if table_page != page:
                    continue
                if not _center_inside(block_bbox, _bbox(table.get("bbox"))):
                    continue
                block["excluded_from_plain_text_projection"] = True
                block["superseded_by_table_block_id"] = table.get("block_id")
                superseded.append(
                    {
                        "block_id": block.get("block_id"),
                        "table_block_id": table.get("block_id"),
                        "page": page,
                        "reason": "FORMAL_TABLE_CELL_TEXT_HAS_HIGHER_STRUCTURE_AUTHORITY",
                    }
                )
                break

    previous_text = text(result.get("plain_text"))
    result["blocks"] = blocks
    result["plain_text"] = _compose(blocks, previous_text)
    unresolved_region_count = sum(
        int(row.get("count") or 0)
        for row in _list(result.get("unsupported_content"))
        if isinstance(row, dict)
        and text(row.get("reason_code") or row.get("kind"))
        in {
            "PDF_TABLE_REGION_NOT_CELL_PARSED",
            "VISUAL_TABLE_STRUCTURE_NOT_RECOVERED",
        }
    )
    receipt = {
        "schema": "qualibug.visual-table-text-authority-receipt.v1",
        "visual_table_count": len(visual_tables),
        "formal_table_count": len(formal_tables),
        "visual_table_cell_count": len(visual_cells),
        "unresolved_visual_table_region_count": unresolved_region_count,
        "superseded_visual_text_block_count": len(superseded),
        "superseded_visual_text_blocks": superseded,
        "table_cells_are_projection_authority": bool(formal_tables),
        "evidence_blocks_deleted": False,
        "business_semantics_added": False,
    }
    result["visual_table_text_authority_receipt"] = receipt
    structure_receipt = dict(result.get("structure_receipt") or {})
    structure_receipt["visual_table_count"] = len(visual_tables)
    structure_receipt["formal_visual_table_count"] = len(formal_tables)
    structure_receipt["visual_table_cell_count"] = len(visual_cells)
    structure_receipt["unresolved_visual_table_region_count"] = unresolved_region_count
    structure_receipt["visual_table_text_authority"] = receipt
    result["structure_receipt"] = structure_receipt
    return result


__all__ = ["apply_visual_table_projection_authority"]
