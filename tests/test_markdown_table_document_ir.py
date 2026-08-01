"""Regression: markdown tables must become Document IR TABLE_CELL blocks.

The generic text adapter previously emitted every ``| ... |`` line as a
PARAGRAPH, so the source-preserving projection dropped table structure
(``table_cell_block_count: 0``). Documents such as USER_ROLES.md then lost
their positive role grants (e.g. buyer -> pay own orders), the permission
matrix contained only narrative deny rows, and the permits derivation could
never pick an actor for documented non-authorization obligations
(``BLOCKED_MISSING_ACTOR`` / ``state_transition_actor_unresolved``).
"""
from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center import _parsing
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    build_document_structure_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.source_ingestion import (
    project_document_ir_for_semantic_extraction,
)


ROLES_MARKDOWN = """# 角色权限说明

| 角色 | 权限 |
|---|---|
| buyer | 浏览商品、支付自己的订单、申请自己的退款 |
| warehouse | 查看库存、发货 |
"""


def test_markdown_table_becomes_table_cell_blocks() -> None:
    ir = build_document_structure_ir(
        ROLES_MARKDOWN.encode("utf-8"),
        source_id="roles-src",
        filename="USER_ROLES.md",
    )
    cells = [
        block
        for block in ir.get("blocks") or []
        if block.get("type") == "TABLE_CELL"
    ]
    assert len(cells) == 6  # header + 2 data rows x 2 columns
    assert ir.get("tables")
    assert len(ir["tables"][0].get("cell_block_ids") or []) == 6
    # Header row starts at row_index 1 (pipeline convention).
    header = [cell for cell in cells if cell.get("row_index") == 1]
    assert {cell.get("column_index") for cell in header} == {1, 2}


def test_projection_preserves_markdown_table() -> None:
    ir = build_document_structure_ir(
        ROLES_MARKDOWN.encode("utf-8"),
        source_id="roles-src",
        filename="USER_ROLES.md",
    )
    projected, receipt = project_document_ir_for_semantic_extraction(
        ir,
        filename="USER_ROLES.md",
    )
    assert receipt["table_cell_block_count"] == 6
    assert receipt["projected_table_count"] == 1
    assert "| 角色 | 权限 |" in projected
    assert "| buyer |" in projected


def test_permission_parse_keeps_table_grants_after_projection() -> None:
    ir = build_document_structure_ir(
        ROLES_MARKDOWN.encode("utf-8"),
        source_id="roles-src",
        filename="USER_ROLES.md",
    )
    projected, _ = project_document_ir_for_semantic_extraction(
        ir,
        filename="USER_ROLES.md",
    )
    rows = _parsing._permission_entries(projected, None, "roles-src")
    buyer_pay = [
        row
        for row in rows
        if row.get("role") == "buyer"
        and row.get("decision") == "allow"
        and row.get("resource") == "payment"
    ]
    assert buyer_pay
    assert any(
        "pay" in (row.get("actions") or [])
        for row in buyer_pay
    )


def test_pipe_delimited_prose_without_separator_is_not_a_table() -> None:
    text = "一行：| 不是表格 | 没有分隔行 |\n普通段落"
    ir = build_document_structure_ir(
        text.encode("utf-8"),
        source_id="prose-src",
        filename="notes.md",
    )
    cells = [
        block
        for block in ir.get("blocks") or []
        if block.get("type") == "TABLE_CELL"
    ]
    assert cells == []
    assert ir.get("tables") == []
