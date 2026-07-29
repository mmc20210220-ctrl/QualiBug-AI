from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    MergedCellRuledGridVisualTableProvider,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.visual_table_projection import (
    apply_visual_table_projection_authority,
)


def _cell(
    row: int,
    column: int,
    *,
    top: float = 1.0,
    bottom: float = 1.0,
    left: float = 1.0,
    right: float = 1.0,
) -> dict:
    return {
        "row_index": row,
        "column_index": column,
        "row_span": 1,
        "column_span": 1,
        "bbox": [column * 100, row * 50, (column + 1) * 100, (row + 1) * 50],
        "border_complete": min(top, bottom, left, right) >= 0.58,
        "border_support": {
            "top": top,
            "bottom": bottom,
            "left": left,
            "right": right,
        },
    }


def test_non_rectangular_component_is_rejected_before_span_promotion() -> None:
    provider = MergedCellRuledGridVisualTableProvider()
    atomic = {
        (0, 0): _cell(0, 0),
        (0, 1): _cell(0, 1),
        (1, 0): _cell(1, 0),
        (1, 1): _cell(1, 1),
    }
    merged, error = provider._merged_cell([(0, 0), (0, 1), (1, 0)], atomic)
    assert merged is None
    assert error == "NON_RECTANGULAR_MERGE_COMPONENT"


def test_projection_receipt_counts_borderless_and_merged_cells() -> None:
    document_ir = {
        "plain_text": "审批矩阵\n待审核\t审核",
        "unsupported_content": [],
        "structure_receipt": {},
        "blocks": [
            {
                "block_id": "table-borderless",
                "type": "TABLE",
                "page": 1,
                "region": "body",
                "bbox": [0, 0, 300, 150],
                "formal_table_structure": True,
                "excluded_from_main_flow": True,
                "source_locator": "matrix.png#table=1",
                "structure_evidence": {
                    "detection_method": "borderless_repeated_word_box_column_alignment"
                },
            },
            {
                "block_id": "cell-header",
                "type": "TABLE_CELL",
                "table_block_id": "table-borderless",
                "parent_id": "row-header",
                "page": 1,
                "region": "body",
                "row_index": 0,
                "column_index": 0,
                "row_span": 1,
                "column_span": 3,
                "bbox": [0, 0, 300, 50],
                "text": "审批矩阵",
                "source_locator": "matrix.png#table=1;row=0;column=0",
            },
            {
                "block_id": "cell-state",
                "type": "TABLE_CELL",
                "table_block_id": "table-borderless",
                "parent_id": "row-1",
                "page": 1,
                "region": "body",
                "row_index": 1,
                "column_index": 0,
                "row_span": 1,
                "column_span": 1,
                "bbox": [0, 50, 100, 100],
                "text": "待审核",
                "source_locator": "matrix.png#table=1;row=1;column=0",
            },
        ],
    }
    projected = apply_visual_table_projection_authority(document_ir)
    receipt = projected["visual_table_text_authority_receipt"]
    assert receipt["borderless_visual_table_count"] == 1
    assert receipt["ruled_visual_table_count"] == 0
    assert receipt["merged_visual_table_count"] == 1
    assert receipt["merged_visual_table_cell_count"] == 1
    assert projected["structure_receipt"]["merged_visual_table_cell_count"] == 1
