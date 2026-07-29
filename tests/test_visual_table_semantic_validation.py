from __future__ import annotations

from typing import Any

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.visual_table_projection import (
    apply_visual_table_projection_authority,
)


def _table() -> dict[str, Any]:
    return {
        "block_id": "table-1",
        "type": "TABLE",
        "page": 1,
        "order": 1,
        "region": "body",
        "bbox": [20, 20, 420, 260],
        "formal_table_structure": True,
        "header_row_count": 1,
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


def _note(block_id: str, value: str, *, order: int) -> dict[str, Any]:
    return {
        "block_id": block_id,
        "type": "NOTE",
        "page": 1,
        "order": order,
        "region": "body",
        "bbox": [20, 280 + order, 300, 305 + order],
        "text": value,
        "source_locator": f"matrix.pdf#page=1;note={block_id}",
    }


def _document(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "qualibug.document-structure-ir.v1",
        "format": "pdf",
        "filename": "matrix.pdf",
        "plain_text": "\n".join(
            str(row.get("text") or "") for row in blocks if row.get("text")
        ),
        "blocks": blocks,
        "tables": [{"block_id": "table-1", "type": "TABLE", "page": 1}],
        "pages": [{"page": 1, "page_rendering": [{"width_px": 500, "height_px": 700}]}],
        "unsupported_content": [],
        "structure_receipt": {"status": "COMPLETE"},
    }


def test_short_ascii_if_is_not_matched_inside_unrelated_header() -> None:
    blocks = [
        _table(),
        _cell(0, 0, "gift"),
        _cell(0, 1, "result"),
        _cell(1, 0, "A"),
        _cell(1, 1, "B"),
        _cell(2, 0, "C"),
        _cell(2, 1, "D"),
    ]
    projected = apply_visual_table_projection_authority(_document(blocks))
    assert projected["decision_matrix_candidates"] == []
    assert {
        row["role"] for row in projected["table_column_role_candidates"]
    } == {"RESULT_COLUMN_CANDIDATE"}
    assert projected["structure_receipt"]["rejected_unsafe_column_role_candidate_count"] == 1
    table = next(row for row in projected["blocks"] if row.get("type") == "TABLE")
    assert table["decision_matrix_candidate"] is False


def test_symbol_without_unique_explicit_legend_remains_unresolved() -> None:
    blocks = [
        _table(),
        _cell(0, 0, "条件"),
        _cell(0, 1, "结果"),
        _cell(1, 0, "资料完整"),
        _cell(1, 1, "√"),
        _cell(2, 0, "资料缺失"),
        _cell(2, 1, "×"),
    ]
    projected = apply_visual_table_projection_authority(_document(blocks))
    gap = next(
        row
        for row in projected["unsupported_content"]
        if row.get("reason_code") == "TABLE_SYMBOL_LEGEND_MISSING"
    )
    assert gap["count"] == 2
    assert gap["blocks_formal_understanding"] is False
    assert projected["structure_receipt"]["status"] == "PARTIAL"


def test_same_symbol_with_multiple_meanings_is_ambiguous() -> None:
    blocks = [
        _table(),
        _cell(0, 0, "条件"),
        _cell(0, 1, "结果"),
        _cell(1, 0, "资料完整"),
        _cell(1, 1, "√"),
        _cell(2, 0, "资料缺失"),
        _cell(2, 1, "×"),
        _note("legend-1", "√=允许", order=1),
        _note("legend-2", "√=推荐", order=2),
        _note("legend-3", "×=禁止", order=3),
    ]
    projected = apply_visual_table_projection_authority(_document(blocks))
    ambiguity = next(
        row
        for row in projected["unsupported_content"]
        if row.get("reason_code") == "TABLE_LEGEND_TOKEN_AMBIGUOUS"
    )
    assert ambiguity["token"] == "√"
    assert sorted(ambiguity["meaning_candidates"]) == ["允许", "推荐"]
    assert projected["structure_receipt"]["table_legend_token_ambiguity_count"] == 1


def test_color_legend_text_is_candidate_until_visual_sample_is_bound() -> None:
    blocks = [
        _table(),
        _cell(0, 0, "条件"),
        _cell(0, 1, "结果"),
        _cell(1, 0, "资料完整"),
        _cell(1, 1, "通过"),
        _cell(2, 0, "资料缺失"),
        _cell(2, 1, "异常"),
        _note("legend-color", "红色表示异常", order=1),
    ]
    projected = apply_visual_table_projection_authority(_document(blocks))
    color = next(
        row
        for row in projected["table_legend_candidates"]
        if row.get("kind") == "COLOR_LEGEND_CANDIDATE"
    )
    assert color["visual_sample_verified"] is False
    gap = next(
        row
        for row in projected["unsupported_content"]
        if row.get("reason_code") == "TABLE_COLOR_LEGEND_VISUAL_SAMPLE_UNVERIFIED"
    )
    assert gap["blocks_formal_understanding"] is False
    assert projected["structure_receipt"]["table_color_legend_unverified_count"] == 1
