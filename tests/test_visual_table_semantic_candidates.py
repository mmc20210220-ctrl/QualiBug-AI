from __future__ import annotations

from typing import Any

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.visual_table_continuation import (
    TABLE_CONTINUATION_SCHEMA,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.visual_table_projection import (
    apply_visual_table_projection_authority,
)


def _table(
    table_id: str,
    *,
    page: int = 1,
    logical_table_id: str = "",
    fragment_index: int = 0,
    header_row_count: int = 2,
) -> dict[str, Any]:
    row = {
        "block_id": table_id,
        "type": "TABLE",
        "page": page,
        "order": page * 100,
        "region": "body",
        "bbox": [20, 20, 560, 360],
        "formal_table_structure": True,
        "header_row_count": header_row_count,
        "excluded_from_main_flow": True,
        "source_locator": f"matrix.pdf#page={page};table={table_id}",
        "structure_evidence": {
            "detection_method": "ruled_grid_pixel_line_intersections",
            "coordinate_system": "IMAGE_PIXELS_LOCAL_TO_RENDERED_PAGE",
            "page_rendering": {"page": page, "width_px": 600, "height_px": 800},
        },
    }
    if logical_table_id:
        row.update(
            {
                "logical_table_id": logical_table_id,
                "table_fragment_index": fragment_index,
                "table_fragment_count": 2,
                "header_source_table_id": "table-1",
            }
        )
    return row


def _cell(
    table_id: str,
    row: int,
    column: int,
    value: str,
    *,
    page: int = 1,
    column_span: int = 1,
    row_span: int = 1,
    header_role: str = "",
) -> dict[str, Any]:
    left = 20 + column * 180
    top = 20 + row * 55
    cell = {
        "block_id": f"{table_id}:cell:{row}:{column}",
        "type": "TABLE_CELL",
        "parent_id": f"{table_id}:row:{row}",
        "table_block_id": table_id,
        "page": page,
        "order": page * 100 + row * 10 + column,
        "region": "body",
        "row_index": row,
        "column_index": column,
        "row_span": row_span,
        "column_span": column_span,
        "bbox": [left, top, left + 180 * column_span, top + 50],
        "text": value,
        "source_locator": f"matrix.pdf#page={page};table={table_id};row={row};column={column}",
        "structure_evidence": {
            "coordinate_system": "IMAGE_PIXELS_LOCAL_TO_RENDERED_PAGE",
            "ocr_confidence": 0.99,
        },
    }
    if header_role:
        cell["table_header_role"] = header_role
        cell["table_header_level"] = row + 1
    return cell


def _decision_table_blocks(table_id: str = "table-1", *, page: int = 1) -> list[dict[str, Any]]:
    return [
        _table(table_id, page=page),
        _cell(table_id, 0, 0, "判断", page=page, column_span=2, header_role="CANONICAL_HEADER"),
        _cell(table_id, 0, 2, "结果", page=page, header_role="CANONICAL_HEADER"),
        _cell(table_id, 1, 0, "状态", page=page, header_role="CANONICAL_HEADER"),
        _cell(table_id, 1, 1, "输入", page=page, header_role="CANONICAL_HEADER"),
        _cell(table_id, 1, 2, "动作", page=page, header_role="CANONICAL_HEADER"),
        _cell(table_id, 2, 0, "待审核", page=page),
        _cell(table_id, 2, 1, "资料完整", page=page),
        _cell(table_id, 2, 2, "允许提交", page=page),
        _cell(table_id, 3, 0, "已审核", page=page),
        _cell(table_id, 3, 1, "资料完整", page=page),
        _cell(table_id, 3, 2, "允许发货", page=page),
    ]


def _document(blocks: list[dict[str, Any]], *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {
        "schema": "qualibug.document-structure-ir.v1",
        "format": "pdf",
        "filename": "matrix.pdf",
        "plain_text": "\n".join(
            str(row.get("text") or "")
            for row in blocks
            if row.get("type") in {"TABLE_CELL", "NOTE", "CAPTION", "PARAGRAPH"}
            and row.get("text")
        ),
        "blocks": blocks,
        "tables": [
            {
                "block_id": row["block_id"],
                "type": "TABLE",
                "page": row["page"],
            }
            for row in blocks
            if row.get("type") == "TABLE"
        ],
        "pages": [
            {"page": 1, "page_rendering": [{"width_px": 600, "height_px": 800}]},
            {"page": 2, "page_rendering": [{"width_px": 600, "height_px": 800}]},
        ],
        "unsupported_content": [],
        "structure_receipt": {"status": "COMPLETE"},
    }
    result.update(extra or {})
    return result


def test_grouped_headers_form_parent_child_tree_and_decision_candidates() -> None:
    projected = apply_visual_table_projection_authority(_document(_decision_table_blocks()))

    nodes = projected["table_header_nodes"]
    judgment = next(row for row in nodes if row["text"] == "判断")
    state = next(row for row in nodes if row["text"] == "状态")
    input_node = next(row for row in nodes if row["text"] == "输入")
    result_group = next(row for row in nodes if row["text"] == "结果")
    action = next(row for row in nodes if row["text"] == "动作")
    assert judgment["node_kind"] == "HEADER_GROUP"
    assert state["parent_header_node_id"] == judgment["header_node_id"]
    assert input_node["parent_header_node_id"] == judgment["header_node_id"]
    assert action["parent_header_node_id"] == result_group["header_node_id"]

    roles = projected["table_column_role_candidates"]
    condition_columns = {
        row["column_index"]
        for row in roles
        if row["role"] == "CONDITION_COLUMN_CANDIDATE"
    }
    result_columns = {
        row["column_index"]
        for row in roles
        if row["role"] == "RESULT_COLUMN_CANDIDATE"
    }
    assert condition_columns == {0, 1}
    assert result_columns == {2}
    assert len(projected["decision_matrix_candidates"]) == 1
    decision = projected["decision_matrix_candidates"][0]
    assert decision["formal_business_rule"] is False
    assert decision["business_semantics_added"] is False
    assert projected["visual_table_semantic_candidate_receipt"]["formal_business_rules_created"] == 0


def test_leftmost_body_column_becomes_row_header_candidate_only() -> None:
    projected = apply_visual_table_projection_authority(_document(_decision_table_blocks()))
    candidates = projected["table_row_header_candidates"]
    assert len(candidates) == 2
    assert {row["source_cell_block_id"] for row in candidates} == {
        "table-1:cell:2:0",
        "table-1:cell:3:0",
    }
    body_cell = next(
        row for row in projected["blocks"] if row.get("block_id") == "table-1:cell:2:0"
    )
    assert any(
        row.get("role") == "ROW_HEADER_CANDIDATE"
        for row in body_cell["structural_role_candidates"]
    )


def test_explicit_symbol_and_color_legends_are_candidates_not_values() -> None:
    blocks = [
        _table("table-1", header_row_count=1),
        _cell("table-1", 0, 0, "条件", header_role="CANONICAL_HEADER"),
        _cell("table-1", 0, 1, "结果", header_role="CANONICAL_HEADER"),
        _cell("table-1", 1, 0, "资料完整"),
        _cell("table-1", 1, 1, "√"),
        _cell("table-1", 2, 0, "资料缺失"),
        _cell("table-1", 2, 1, "×"),
        {
            "block_id": "legend-symbol",
            "type": "NOTE",
            "page": 1,
            "order": 500,
            "region": "body",
            "bbox": [20, 370, 180, 395],
            "text": "√=允许",
            "source_locator": "matrix.pdf#page=1;note=symbol",
        },
        {
            "block_id": "legend-color",
            "type": "NOTE",
            "page": 1,
            "order": 501,
            "region": "body",
            "bbox": [190, 370, 380, 395],
            "text": "红色表示异常",
            "source_locator": "matrix.pdf#page=1;note=color",
        },
    ]
    projected = apply_visual_table_projection_authority(_document(blocks))
    legends = projected["table_legend_candidates"]
    assert {row["kind"] for row in legends} == {
        "SYMBOL_LEGEND_CANDIDATE",
        "COLOR_LEGEND_CANDIDATE",
    }
    symbol_cell = next(
        row for row in projected["blocks"] if row.get("block_id") == "table-1:cell:1:1"
    )
    assert symbol_cell["text"] == "√"
    assert symbol_cell["legend_meaning_candidates"][0]["meaning_text"] == "允许"
    assert symbol_cell["legend_meaning_candidates"][0]["candidate_only"] is True
    color = next(row for row in legends if row["kind"] == "COLOR_LEGEND_CANDIDATE")
    assert color["visual_sample_verified"] is False


def test_header_matching_both_condition_and_result_remains_ambiguous() -> None:
    blocks = [
        _table("table-1", header_row_count=1),
        _cell("table-1", 0, 0, "条件结果", header_role="CANONICAL_HEADER"),
        _cell("table-1", 0, 1, "备注", header_role="CANONICAL_HEADER"),
        _cell("table-1", 1, 0, "A"),
        _cell("table-1", 1, 1, "B"),
        _cell("table-1", 2, 0, "C"),
        _cell("table-1", 2, 1, "D"),
    ]
    projected = apply_visual_table_projection_authority(_document(blocks))
    gap = next(
        row
        for row in projected["unsupported_content"]
        if row.get("reason_code") == "DECISION_COLUMN_ROLE_AMBIGUOUS"
    )
    assert gap["blocks_formal_understanding"] is False
    assert projected["structure_receipt"]["status"] == "PARTIAL"
    assert projected["structure_receipt"]["decision_column_role_ambiguity_count"] == 1


def test_cross_page_logical_table_owns_one_candidate_set() -> None:
    first = _decision_table_blocks("table-1", page=1)
    second = _decision_table_blocks("table-2", page=2)
    for row in first:
        if row.get("type") == "TABLE":
            row.update(
                {
                    "logical_table_id": "logical-table",
                    "table_fragment_index": 0,
                    "table_fragment_count": 2,
                    "header_source_table_id": "table-1",
                }
            )
    for row in second:
        if row.get("type") == "TABLE":
            row.update(
                {
                    "logical_table_id": "logical-table",
                    "table_fragment_index": 1,
                    "table_fragment_count": 2,
                    "header_source_table_id": "table-1",
                    "repeated_header_row_count": 2,
                }
            )
    projected = apply_visual_table_projection_authority(
        _document(
            [*first, *second],
            extra={
                "table_groups": [
                    {
                        "logical_table_id": "logical-table",
                        "fragment_table_ids": ["table-1", "table-2"],
                        "canonical_header_table_id": "table-1",
                    }
                ],
                "table_continuations": [
                    {"previous_table_id": "table-1", "following_table_id": "table-2"}
                ],
                "visual_table_continuation_receipt": {
                    "schema": TABLE_CONTINUATION_SCHEMA,
                    "logical_table_group_count": 1,
                },
            },
        )
    )
    assert len(projected["decision_matrix_candidates"]) == 1
    assert all(
        row["table_block_id"] == "table-1" for row in projected["table_header_nodes"]
    )
    assert all(
        row["table_block_id"] == "table-1"
        for row in projected["table_column_role_candidates"]
    )
    table_two = next(
        row for row in projected["blocks"] if row.get("block_id") == "table-2"
    )
    assert table_two["semantic_candidate_owner_table_id"] == "table-1"
    assert table_two["decision_matrix_candidate_id"] == projected["decision_matrix_candidates"][0][
        "candidate_id"
    ]
    inherited = next(
        row
        for row in projected["blocks"]
        if row.get("block_id") == "table-2:cell:2:2"
    )
    result_role = next(
        row
        for row in inherited["structural_role_candidates"]
        if row.get("role") == "RESULT_COLUMN_CANDIDATE"
    )
    assert result_role["semantic_candidate_owner_table_id"] == "table-1"
    assert projected["structure_receipt"]["semantic_candidate_inherited_fragment_count"] == 1


def test_plain_table_without_explicit_role_words_does_not_become_decision_matrix() -> None:
    blocks = [
        _table("table-1", header_row_count=1),
        _cell("table-1", 0, 0, "名称", header_role="CANONICAL_HEADER"),
        _cell("table-1", 0, 1, "说明", header_role="CANONICAL_HEADER"),
        _cell("table-1", 1, 0, "A"),
        _cell("table-1", 1, 1, "第一项"),
        _cell("table-1", 2, 0, "B"),
        _cell("table-1", 2, 1, "第二项"),
    ]
    projected = apply_visual_table_projection_authority(_document(blocks))
    assert projected["decision_matrix_candidates"] == []
    assert projected["table_column_role_candidates"] == []
    table = next(row for row in projected["blocks"] if row.get("block_id") == "table-1")
    assert table["decision_matrix_candidate"] is False
