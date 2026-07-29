from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.visual_table_projection import (
    apply_visual_table_projection_authority,
)


def _cell(table_id: str, page: int, row: int, column: int, value: str) -> dict:
    top = 520 + row * 45 if page == 1 else 20 + row * 45
    return {
        "block_id": f"{table_id}:cell:{row}:{column}",
        "type": "TABLE_CELL",
        "table_block_id": table_id,
        "parent_id": f"{table_id}:row:{row}",
        "page": page,
        "order": page * 100 + row * 10 + column,
        "region": "body",
        "row_index": row,
        "column_index": column,
        "row_span": 1,
        "column_span": 1,
        "bbox": [20 + column * 180, top, 200 + column * 180, top + 40],
        "text": value,
        "source_locator": f"report.pdf#page={page};table={table_id};row={row};column={column}",
        "structure_evidence": {
            "coordinate_system": "IMAGE_PIXELS_LOCAL_TO_RENDERED_PAGE"
        },
    }


def _table(table_id: str, page: int) -> dict:
    return {
        "block_id": table_id,
        "type": "TABLE",
        "page": page,
        "order": page * 100,
        "region": "body",
        "bbox": [20, 500, 560, 790] if page == 1 else [20, 10, 560, 300],
        "formal_table_structure": True,
        "excluded_from_main_flow": True,
        "source_locator": f"report.pdf#page={page};table={table_id}",
        "structure_evidence": {
            "detection_method": "ruled_grid_pixel_line_intersections",
            "page_rendering": {"page": page, "width_px": 600, "height_px": 800},
        },
    }


def _document() -> dict:
    blocks = []
    for table_id, page, data in (
        ("table-1", 1, "待审核"),
        ("table-2", 2, "已审核"),
    ):
        blocks.append(_table(table_id, page))
        blocks.extend(
            [
                _cell(table_id, page, 0, 0, "状态"),
                _cell(table_id, page, 0, 1, "动作"),
                _cell(table_id, page, 0, 2, "角色"),
                _cell(table_id, page, 1, 0, data),
                _cell(table_id, page, 1, 1, "审核" if page == 1 else "发货"),
                _cell(table_id, page, 1, 2, "主管" if page == 1 else "仓管"),
            ]
        )
    return {
        "schema": "qualibug.document-structure-ir.v1",
        "format": "pdf",
        "filename": "report.pdf",
        "plain_text": "状态\n动作\n角色\n待审核\n状态\n动作\n角色\n已审核",
        "blocks": blocks,
        "tables": [
            {"block_id": "table-1", "type": "TABLE", "page": 1},
            {"block_id": "table-2", "type": "TABLE", "page": 2},
        ],
        "pages": [
            {"page": 1, "page_rendering": [{"width_px": 600, "height_px": 800}]},
            {"page": 2, "page_rendering": [{"width_px": 600, "height_px": 800}]},
        ],
        "unsupported_content": [],
        "structure_receipt": {"status": "COMPLETE"},
    }


def test_projection_is_idempotent_for_table_groups_and_gaps() -> None:
    first = apply_visual_table_projection_authority(_document())
    second = apply_visual_table_projection_authority(first)

    assert len(first["table_groups"]) == 1
    assert second["table_groups"] == first["table_groups"]
    assert second["table_continuations"] == first["table_continuations"]
    assert second["unsupported_content"] == first["unsupported_content"]
    assert second["plain_text"] == first["plain_text"]


def test_top_level_table_summaries_share_logical_table_identity() -> None:
    result = apply_visual_table_projection_authority(_document())
    summaries = {row["block_id"]: row for row in result["tables"]}
    blocks = {
        row["block_id"]: row
        for row in result["blocks"]
        if row.get("type") == "TABLE"
    }

    assert summaries["table-1"]["logical_table_id"] == blocks["table-1"]["logical_table_id"]
    assert summaries["table-2"]["logical_table_id"] == blocks["table-2"]["logical_table_id"]
    assert summaries["table-1"]["continued_to_table_id"] == "table-2"
    assert summaries["table-2"]["continued_from_table_id"] == "table-1"
    assert summaries["table-2"]["header_source_table_id"] == "table-1"
