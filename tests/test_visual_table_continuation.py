from __future__ import annotations

from typing import Any

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.visual_table_continuation import (
    apply_visual_table_continuations,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.visual_table_projection import (
    apply_visual_table_projection_authority,
)


def _table(table_id: str, page: int, bbox: list[int]) -> dict[str, Any]:
    return {
        "block_id": table_id,
        "type": "TABLE",
        "page": page,
        "order": page * 100,
        "region": "body",
        "bbox": bbox,
        "formal_table_structure": True,
        "excluded_from_main_flow": True,
        "source_locator": f"report.pdf#page={page};table={table_id}",
        "structure_evidence": {
            "provider": "continuation-test-provider",
            "detection_method": "ruled_grid_pixel_line_intersections",
            "coordinate_system": "IMAGE_PIXELS_LOCAL_TO_RENDERED_PAGE",
            "page_rendering": {
                "page": page,
                "width_px": 600,
                "height_px": 800,
                "renderer_name": "test",
            },
        },
    }


def _row(table_id: str, page: int, row_index: int) -> dict[str, Any]:
    return {
        "block_id": f"{table_id}:row:{row_index}",
        "type": "TABLE_ROW",
        "parent_id": table_id,
        "page": page,
        "order": page * 100 + row_index,
        "region": "body",
        "row_index": row_index,
        "text": "",
        "excluded_from_main_flow": True,
        "source_locator": f"report.pdf#page={page};table={table_id};row={row_index}",
    }


def _cell(
    table_id: str,
    page: int,
    row_index: int,
    column_index: int,
    text_value: str,
    *,
    column_span: int = 1,
    row_span: int = 1,
    left: int | None = None,
    right: int | None = None,
) -> dict[str, Any]:
    column_left = 20 + column_index * 180 if left is None else left
    column_right = column_left + 180 * column_span if right is None else right
    top = 20 + row_index * 45 if page > 1 else 520 + row_index * 45
    return {
        "block_id": f"{table_id}:cell:{row_index}:{column_index}",
        "type": "TABLE_CELL",
        "parent_id": f"{table_id}:row:{row_index}",
        "table_block_id": table_id,
        "page": page,
        "order": page * 100 + row_index * 10 + column_index,
        "region": "body",
        "row_index": row_index,
        "column_index": column_index,
        "row_span": row_span,
        "column_span": column_span,
        "bbox": [column_left, top, column_right, top + 40],
        "text": text_value,
        "source_locator": (
            f"report.pdf#page={page};table={table_id};row={row_index};column={column_index}"
        ),
        "structure_evidence": {
            "coordinate_system": "IMAGE_PIXELS_LOCAL_TO_RENDERED_PAGE",
            "ocr_confidence": 0.99,
        },
    }


def _fragment(
    table_id: str,
    page: int,
    *,
    repeated_header: bool = True,
    header_title: str = "审批矩阵",
    data_prefix: str = "待审核",
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        _table(table_id, page, [20, 500, 560, 790] if page == 1 else [20, 10, 560, 360])
    ]
    row_index = 0
    if repeated_header:
        blocks.append(_row(table_id, page, row_index))
        blocks.append(
            _cell(
                table_id,
                page,
                row_index,
                0,
                header_title,
                column_span=3,
                left=20,
                right=560,
            )
        )
        row_index += 1
        blocks.append(_row(table_id, page, row_index))
        blocks.extend(
            [
                _cell(table_id, page, row_index, 0, "状态"),
                _cell(table_id, page, row_index, 1, "动作"),
                _cell(table_id, page, row_index, 2, "角色"),
            ]
        )
        row_index += 1
    blocks.append(_row(table_id, page, row_index))
    blocks.extend(
        [
            _cell(table_id, page, row_index, 0, data_prefix),
            _cell(table_id, page, row_index, 1, "审核" if page == 1 else "发货"),
            _cell(table_id, page, row_index, 2, "主管" if page == 1 else "仓管"),
        ]
    )
    return blocks


def _document(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "qualibug.document-structure-ir.v1",
        "format": "pdf",
        "filename": "report.pdf",
        "plain_text": "\n".join(
            str(block.get("text") or "")
            for block in blocks
            if block.get("type") == "TABLE_CELL" and block.get("text")
        ),
        "blocks": blocks,
        "pages": [
            {"page": 1, "page_rendering": [{"width_px": 600, "height_px": 800}]},
            {"page": 2, "page_rendering": [{"width_px": 600, "height_px": 800}]},
            {"page": 3, "page_rendering": [{"width_px": 600, "height_px": 800}]},
        ],
        "unsupported_content": [],
        "structure_receipt": {"status": "COMPLETE"},
    }


def test_repeated_two_level_header_links_pages_and_deduplicates_projection() -> None:
    blocks = [*_fragment("table-1", 1), *_fragment("table-2", 2, data_prefix="已审核")]
    projected = apply_visual_table_projection_authority(_document(blocks))

    assert len(projected["table_groups"]) == 1
    group = projected["table_groups"][0]
    assert group["fragment_table_ids"] == ["table-1", "table-2"]
    assert group["header_row_count"] == 2
    assert group["multi_level_header"] is True
    assert projected["structure_receipt"]["logical_visual_table_group_count"] == 1
    assert projected["structure_receipt"]["multi_level_header_group_count"] == 1

    repeated = [
        row
        for row in projected["blocks"]
        if row.get("table_block_id") == "table-2"
        and row.get("table_header_role") == "REPEATED_HEADER"
    ]
    assert len(repeated) == 4
    assert all(row["excluded_from_plain_text_projection"] is True for row in repeated)
    assert projected["plain_text"].count("审批矩阵") == 1
    assert projected["plain_text"].count("状态") == 1

    page_two_action = next(
        row
        for row in projected["blocks"]
        if row.get("table_block_id") == "table-2"
        and row.get("text") == "发货"
    )
    assert page_two_action["column_header_path"] == ["审批矩阵", "动作"]
    assert page_two_action["header_source_table_id"] == "table-1"
    assert group["document_order_is_business_flow"] is False


def test_explicit_continuation_marker_allows_header_inheritance_without_repeat() -> None:
    blocks = [*_fragment("table-1", 1), *_fragment("table-2", 2, repeated_header=False)]
    blocks.append(
        {
            "block_id": "continued-marker",
            "type": "CAPTION",
            "page": 2,
            "order": 1,
            "region": "body",
            "bbox": [20, 0, 100, 9],
            "text": "续表",
            "source_locator": "report.pdf#page=2;caption=continued",
        }
    )
    result = apply_visual_table_continuations(_document(blocks))

    assert len(result["table_groups"]) == 1
    following = next(row for row in result["blocks"] if row.get("block_id") == "table-2")
    assert following["header_source_table_id"] == "table-1"
    assert following["repeated_header_row_count"] == 0
    data_cell = next(
        row
        for row in result["blocks"]
        if row.get("table_block_id") == "table-2" and row.get("text") == "发货"
    )
    assert data_cell["column_header_path"] == ["审批矩阵", "动作"]


def test_similar_edge_tables_without_repeat_or_marker_remain_ambiguous() -> None:
    blocks = [
        *_fragment("table-1", 1, repeated_header=False),
        *_fragment("table-2", 2, repeated_header=False, data_prefix="已完成"),
    ]
    result = apply_visual_table_continuations(_document(blocks))

    assert result["table_groups"] == []
    gap = next(
        row
        for row in result["unsupported_content"]
        if row.get("reason_code") == "VISUAL_TABLE_CONTINUATION_AMBIGUOUS"
    )
    assert gap["blocks_formal_understanding"] is False
    assert result["structure_receipt"]["status"] == "PARTIAL"


def test_continuation_marker_with_conflicting_header_blocks_formal_understanding() -> None:
    blocks = [
        *_fragment("table-1", 1),
        *_fragment("table-2", 2, header_title="出库矩阵"),
        {
            "block_id": "continued-marker",
            "type": "CAPTION",
            "page": 2,
            "order": 1,
            "region": "body",
            "bbox": [20, 0, 100, 9],
            "text": "续表",
            "source_locator": "report.pdf#page=2;caption=continued",
        },
    ]
    result = apply_visual_table_continuations(_document(blocks))

    assert result["table_groups"] == []
    conflict = next(
        row
        for row in result["unsupported_content"]
        if row.get("reason_code") == "VISUAL_TABLE_CONTINUATION_HEADER_CONFLICT"
    )
    assert conflict["blocks_formal_understanding"] is True
    assert result["structure_receipt"]["status"] == "BLOCKED"


def test_non_adjacent_pages_are_never_linked_as_continuation() -> None:
    blocks = [*_fragment("table-1", 1), *_fragment("table-3", 3)]
    result = apply_visual_table_continuations(_document(blocks))
    assert result["table_groups"] == []
    assert result["table_continuations"] == []
