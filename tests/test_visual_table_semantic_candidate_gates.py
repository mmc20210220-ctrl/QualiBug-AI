from __future__ import annotations

from typing import Any

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.visual_table_projection import (
    apply_visual_table_projection_authority,
)


def _table(*, header_row_count: int) -> dict[str, Any]:
    return {
        "block_id": "table-1",
        "type": "TABLE",
        "page": 1,
        "order": 1,
        "region": "body",
        "bbox": [20, 20, 420, 260],
        "formal_table_structure": True,
        "header_row_count": header_row_count,
        "excluded_from_main_flow": True,
        "source_locator": "matrix.pdf#page=1;table=1",
        "structure_evidence": {
            "detection_method": "ruled_grid_pixel_line_intersections",
            "coordinate_system": "IMAGE_PIXELS_LOCAL_TO_RENDERED_PAGE",
            "page_rendering": {"page": 1, "width_px": 500, "height_px": 700},
        },
    }


def _cell(row: int, column: int, value: str) -> dict[str, Any]:
    left = 20 + column * 190
    top = 20 + row * 55
    return {
        "block_id": f"cell:{row}:{column}",
        "type": "TABLE_CELL",
        "table_block_id": "table-1",
        "parent_id": f"row:{row}",
        "page": 1,
        "order": 10 + row * 10 + column,
        "region": "body",
        "row_index": row,
        "column_index": column,
        "row_span": 1,
        "column_span": 1,
        "bbox": [left, top, left + 180, top + 50],
        "text": value,
        "source_locator": f"matrix.pdf#page=1;table=1;row={row};column={column}",
        "structure_evidence": {
            "coordinate_system": "IMAGE_PIXELS_LOCAL_TO_RENDERED_PAGE"
        },
    }


def _document(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "qualibug.document-structure-ir.v1",
        "format": "pdf",
        "filename": "matrix.pdf",
        "plain_text": "\n".join(str(row.get("text") or "") for row in blocks if row.get("text")),
        "blocks": blocks,
        "tables": [{"block_id": "table-1", "type": "TABLE", "page": 1}],
        "pages": [{"page": 1, "page_rendering": [{"width_px": 500, "height_px": 700}]}],
        "unsupported_content": [],
        "structure_receipt": {"status": "COMPLETE"},
    }


def test_overlapping_condition_and_result_column_is_not_decision_matrix() -> None:
    projected = apply_visual_table_projection_authority(
        _document(
            [
                _table(header_row_count=1),
                _cell(0, 0, "条件结果"),
                _cell(0, 1, "备注"),
                _cell(1, 0, "A"),
                _cell(1, 1, "B"),
                _cell(2, 0, "C"),
                _cell(2, 1, "D"),
            ]
        )
    )
    assert projected["decision_matrix_candidates"] == []
    assert projected["structure_receipt"]["rejected_overlapping_decision_matrix_candidate_count"] == 1
    assert any(
        row.get("reason_code") == "DECISION_COLUMN_ROLE_AMBIGUOUS"
        for row in projected["unsupported_content"]
    )
    table = next(row for row in projected["blocks"] if row.get("type") == "TABLE")
    assert table["decision_matrix_candidate"] is False


def test_leftmost_column_is_not_row_header_without_header_boundary() -> None:
    projected = apply_visual_table_projection_authority(
        _document(
            [
                _table(header_row_count=0),
                _cell(0, 0, "A"),
                _cell(0, 1, "第一项"),
                _cell(1, 0, "B"),
                _cell(1, 1, "第二项"),
                _cell(2, 0, "C"),
                _cell(2, 1, "第三项"),
            ]
        )
    )
    assert projected["table_row_header_candidates"] == []
    assert projected["structure_receipt"]["rejected_row_header_candidate_count"] == 3
    for cell in [row for row in projected["blocks"] if row.get("type") == "TABLE_CELL"]:
        assert not any(
            role.get("role") == "ROW_HEADER_CANDIDATE"
            for role in cell.get("structural_role_candidates") or []
        )
