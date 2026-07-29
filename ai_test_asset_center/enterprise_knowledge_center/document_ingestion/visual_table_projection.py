"""Choose formal visual-table cells over overlapping page-level text projections.

Page-level OCR and native PDF text remain in Document IR as evidence.  When a formally
recovered visual table covers those blocks, the source-preserving TABLE_CELL blocks become
the merged business-text authority.  No evidence block is deleted.
"""
from __future__ import annotations

import re
from typing import Any

from .contract import text
from .visual_table_continuation import (
    TABLE_CONTINUATION_SCHEMA,
    apply_visual_table_continuations,
)
from .visual_table_semantic_candidates import apply_visual_table_semantic_candidates


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


def _native_table_regions(blocks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        text(row.get("block_id")): row
        for row in blocks
        if text(row.get("type")) == "TABLE_REGION"
        and text(row.get("block_id"))
        and len(_bbox(row.get("bbox"))) == 4
    }


def _authority_region(
    table: dict[str, Any],
    native_regions: dict[str, dict[str, Any]],
) -> tuple[str, list[float], str]:
    evidence = _dict(table.get("structure_evidence"))
    target_region_id = text(evidence.get("target_region_id") or table.get("target_region_id"))
    native_region = native_regions.get(target_region_id)
    if native_region is not None:
        return (
            "PDF_BOTTOM_LEFT_POINTS",
            _bbox(native_region.get("bbox")),
            target_region_id,
        )
    return (
        "IMAGE_PIXELS_LOCAL_TO_RENDERED_PAGE",
        _bbox(table.get("bbox")),
        target_region_id,
    )


def _table_method(table: dict[str, Any]) -> str:
    return text(_dict(table.get("structure_evidence")).get("detection_method"))


def _sync_table_summaries(result: dict[str, Any], blocks: list[dict[str, Any]]) -> None:
    table_blocks = {
        text(row.get("block_id")): row
        for row in blocks
        if text(row.get("type")) == "TABLE" and text(row.get("block_id"))
    }
    table_summary_fields = {
        "logical_table_id",
        "table_fragment_index",
        "table_fragment_count",
        "continued_from_table_id",
        "continued_to_table_id",
        "cross_page_relation_status",
        "header_row_count",
        "header_source_table_id",
        "repeated_header_row_count",
        "document_order_is_business_flow",
        "semantic_candidate_header_row_count",
        "header_node_ids",
        "column_role_candidate_ids",
        "legend_candidate_ids",
        "decision_matrix_candidate",
        "decision_matrix_candidate_id",
        "candidate_only",
        "business_semantics_added",
    }
    summaries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in _list(result.get("tables")):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        block_id = text(row.get("block_id"))
        block = table_blocks.get(block_id)
        if block:
            for field in table_summary_fields:
                if field in block:
                    row[field] = block.get(field)
        summaries.append(row)
        if block_id:
            seen.add(block_id)
    for block_id, block in table_blocks.items():
        if block_id in seen or not (
            text(block.get("logical_table_id")) or bool(block.get("decision_matrix_candidate"))
        ):
            continue
        summaries.append(
            {
                "block_id": block_id,
                "type": "TABLE",
                "page": block.get("page"),
                "bbox": block.get("bbox"),
                "formal_table_structure": block.get("formal_table_structure"),
                "source_locator": block.get("source_locator"),
                **{field: block.get(field) for field in table_summary_fields if field in block},
            }
        )
    result["tables"] = summaries


def apply_visual_table_projection_authority(document_ir: dict[str, Any]) -> dict[str, Any]:
    # Cross-page linking runs before semantic candidates.  Candidate projection sees the full
    # logical table and exact inherited headers, then repeated headers and superseded page text
    # are removed only from the merged business-text authority.
    prior_continuation = _dict(document_ir.get("visual_table_continuation_receipt"))
    continued = (
        dict(document_ir or {})
        if text(prior_continuation.get("schema")) == TABLE_CONTINUATION_SCHEMA
        else apply_visual_table_continuations(document_ir)
    )
    result = apply_visual_table_semantic_candidates(continued)
    blocks = [dict(row) for row in _list(result.get("blocks")) if isinstance(row, dict)]
    visual_tables = [
        row
        for row in blocks
        if text(row.get("type")) == "TABLE" and "formal_table_structure" in row
    ]
    visual_table_ids = {
        text(row.get("block_id")) for row in visual_tables if text(row.get("block_id"))
    }
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
    native_regions = _native_table_regions(blocks)

    borderless_tables = [
        row for row in visual_tables if "borderless" in _table_method(row)
    ]
    ruled_tables = [
        row for row in visual_tables if "ruled_grid" in _table_method(row)
    ]
    merged_cells = [
        row
        for row in visual_cells
        if int(row.get("row_span") or 1) > 1 or int(row.get("column_span") or 1) > 1
    ]
    merged_table_ids = {
        text(row.get("table_block_id")) for row in merged_cells if text(row.get("table_block_id"))
    }

    superseded: list[dict[str, Any]] = []
    if formal_tables:
        for block in blocks:
            if text(block.get("type")) not in {"PARAGRAPH", "LIST_ITEM"}:
                continue
            block_bbox = _bbox(block.get("bbox"))
            if not block_bbox:
                continue
            try:
                page = int(block.get("page") or 0)
            except (TypeError, ValueError):
                continue
            evidence = _dict(block.get("structure_evidence"))
            block_coordinate_system = text(evidence.get("coordinate_system"))
            block_is_rendered_text = (
                block_coordinate_system == "IMAGE_PIXELS_LOCAL_TO_RENDERED_PAGE"
                or bool(block.get("rendered_page_source_locator"))
            )
            for table in formal_tables:
                try:
                    table_page = int(table.get("page") or 0)
                except (TypeError, ValueError):
                    continue
                if table_page != page:
                    continue
                authority_system, authority_bbox, target_region_id = _authority_region(
                    table, native_regions
                )
                if not authority_bbox:
                    continue
                if authority_system == "PDF_BOTTOM_LEFT_POINTS" and block_is_rendered_text:
                    continue
                if (
                    authority_system == "IMAGE_PIXELS_LOCAL_TO_RENDERED_PAGE"
                    and not block_is_rendered_text
                ):
                    continue
                if not _center_inside(block_bbox, authority_bbox):
                    continue
                block["excluded_from_plain_text_projection"] = True
                block["superseded_by_table_block_id"] = table.get("block_id")
                block["superseded_by_table_region_id"] = target_region_id
                superseded.append(
                    {
                        "block_id": block.get("block_id"),
                        "table_block_id": table.get("block_id"),
                        "table_region_id": target_region_id,
                        "page": page,
                        "coordinate_system": authority_system,
                        "reason": "FORMAL_TABLE_CELL_TEXT_HAS_HIGHER_STRUCTURE_AUTHORITY",
                    }
                )
                break

    previous_text = text(result.get("plain_text"))
    result["blocks"] = blocks
    result["plain_text"] = _compose(blocks, previous_text)
    _sync_table_summaries(result, blocks)
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
    unresolved_merged_structure_count = sum(
        int(row.get("count") or 0)
        for row in _list(result.get("unsupported_content"))
        if isinstance(row, dict)
        and text(row.get("reason_code") or row.get("kind"))
        == "VISUAL_TABLE_MERGED_CELL_OR_BORDER_UNRESOLVED"
    )
    receipt = {
        "schema": "qualibug.visual-table-text-authority-receipt.v1",
        "visual_table_count": len(visual_tables),
        "formal_table_count": len(formal_tables),
        "ruled_visual_table_count": len(ruled_tables),
        "borderless_visual_table_count": len(borderless_tables),
        "visual_table_cell_count": len(visual_cells),
        "merged_visual_table_count": len(merged_table_ids),
        "merged_visual_table_cell_count": len(merged_cells),
        "unresolved_merged_structure_count": unresolved_merged_structure_count,
        "native_table_region_count": len(native_regions),
        "unresolved_visual_table_region_count": unresolved_region_count,
        "superseded_visual_text_block_count": len(superseded),
        "superseded_visual_text_blocks": superseded,
        "table_cells_are_projection_authority": bool(formal_tables),
        "native_pdf_table_text_can_be_superseded": True,
        "rendered_ocr_table_text_can_be_superseded": True,
        "evidence_blocks_deleted": False,
        "business_semantics_added": False,
    }
    result["visual_table_text_authority_receipt"] = receipt
    structure_receipt = dict(result.get("structure_receipt") or {})
    structure_receipt["visual_table_count"] = len(visual_tables)
    structure_receipt["formal_visual_table_count"] = len(formal_tables)
    structure_receipt["ruled_visual_table_count"] = len(ruled_tables)
    structure_receipt["borderless_visual_table_count"] = len(borderless_tables)
    structure_receipt["visual_table_cell_count"] = len(visual_cells)
    structure_receipt["merged_visual_table_count"] = len(merged_table_ids)
    structure_receipt["merged_visual_table_cell_count"] = len(merged_cells)
    structure_receipt["unresolved_merged_structure_count"] = unresolved_merged_structure_count
    structure_receipt["unresolved_visual_table_region_count"] = unresolved_region_count
    structure_receipt["visual_table_text_authority"] = receipt
    result["structure_receipt"] = structure_receipt
    return result


__all__ = ["apply_visual_table_projection_authority"]
